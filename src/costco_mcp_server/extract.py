"""Automatic Costco refresh-token extraction from Chromium browser localStorage.

Extracts MSAL.js refresh tokens from local Chrome/Edge/Brave/Chromium profiles,
verifies them against Costco's Azure AD B2C token endpoint, and saves them.
"""

import json
import logging
import os
import platform
import shutil
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import jwt
from chromium_reader.localstorage import LocalStorageReader
from curl_cffi.requests import exceptions as curl_exceptions

from costco_mcp_server.auth import (
    CostcoAuth,
    MSAL_CLIENT_ID,
    _refresh_tokens,
    get_default_account,
)

logger = logging.getLogger(__name__)

DEDICATED_PROFILE_DIR = Path.home() / ".costco-mcp" / "chrome-profile"
COSTCO_HOSTS = {"www.costco.com", "signin.costco.com", "costco.com"}


@dataclass
class TokenCandidate:
    secret: str
    client_id: str
    home_account_id: str
    email: str | None = None
    leveldb_seq_number: int = 0
    id_token: str | None = None
    policy_name: str | None = None
    verified: bool = False
    source_profile: str = ""


@dataclass
class ExtractResult:
    account: str
    email: str | None
    source_profile: str  # filesystem path, not a secret
    verified: bool
    _token: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "email": self.email,
            "source_profile": self.source_profile,
            "verified": self.verified,
        }


def _is_costco_host(host_or_origin: str | None) -> bool:
    """Check if the given host or origin belongs to Costco SPA/auth."""
    if not host_or_origin:
        return False
    h = host_or_origin.lower().strip()
    if h.startswith("http://") or h.startswith("https://"):
        try:
            parsed = urllib.parse.urlsplit(h)
            hostname = parsed.hostname or ""
        except Exception:
            hostname = ""
    else:
        hostname = h.split(":")[0].split("/")[0]
    return hostname in COSTCO_HOSTS


def _copy_leveldb_unlocked(src: Path) -> Path:
    """Copy a LevelDB directory ignoring LOCK so an open browser doesn't block reading."""
    tmp = Path(tempfile.mkdtemp(prefix="costco-leveldb-"))
    dest = tmp / "leveldb"
    shutil.copytree(src, dest, ignore=lambda *_: ["LOCK"])
    return dest


def _is_leveldb_dir(p: Path) -> bool:
    """Verify directory exists and appears to be a LevelDB store."""
    if not p.is_dir():
        return False
    if (p / "CURRENT").is_file():
        return True
    try:
        return any(p.glob("*.ldb")) or any(p.glob("*.log"))
    except OSError:
        return False


def _get_chromium_user_data_dirs(browser: str = "auto") -> dict[str, list[Path]]:
    """Return OS-specific base directories for Chromium browsers."""
    system = platform.system()
    home = Path.home()
    local_app_data = (
        Path(os.environ["LOCALAPPDATA"])
        if "LOCALAPPDATA" in os.environ
        else home / "AppData" / "Local"
    )

    bases: dict[str, list[Path]] = {
        "chrome": [],
        "chromium": [],
        "brave": [],
        "edge": [],
    }

    if system == "Darwin":
        app_sup = home / "Library" / "Application Support"
        bases["chrome"].append(app_sup / "Google" / "Chrome")
        bases["chromium"].append(app_sup / "Chromium")
        bases["brave"].append(app_sup / "BraveSoftware" / "Brave-Browser")
        bases["edge"].append(app_sup / "Microsoft Edge")
    elif system == "Windows":
        bases["chrome"].append(local_app_data / "Google" / "Chrome" / "User Data")
        bases["chromium"].append(local_app_data / "Chromium" / "User Data")
        bases["brave"].append(
            local_app_data / "BraveSoftware" / "Brave-Browser" / "User Data"
        )
        bases["edge"].append(local_app_data / "Microsoft" / "Edge" / "User Data")
    else:  # Linux and other Unix
        config = home / ".config"
        bases["chrome"].append(config / "google-chrome")
        bases["chromium"].append(config / "chromium")
        bases["brave"].append(config / "BraveSoftware" / "Brave-Browser")
        bases["edge"].append(config / "microsoft-edge")

    return bases


def find_chromium_profiles(
    browser: str = "auto", profile: str | None = None
) -> list[Path]:
    """Find all Chromium-based localStorage LevelDB paths.

    Order:
      1. Dedicated profile (~/.costco-mcp/chrome-profile) first
      2. Regular profiles sorted by LevelDB directory mtime (newest first)
    """
    valid_browsers = {"auto", "chrome", "edge", "brave", "chromium"}
    browser_lower = browser.lower().strip()
    if browser_lower not in valid_browsers:
        raise ValueError(
            f"Unsupported browser: '{browser}'. Must be one of: {', '.join(sorted(valid_browsers))}"
        )

    dedicated_paths: list[Path] = []
    # Dedicated profile is searched when browser is auto, chrome, or chromium
    if browser_lower in {"auto", "chrome", "chromium"}:
        for sub in (
            DEDICATED_PROFILE_DIR / "Default" / "Local Storage" / "leveldb",
            DEDICATED_PROFILE_DIR / "Local Storage" / "leveldb",
        ):
            if _is_leveldb_dir(sub) and sub not in dedicated_paths:
                dedicated_paths.append(sub)

    bases_by_browser = _get_chromium_user_data_dirs(browser_lower)
    candidate_bases: list[Path] = []
    if browser_lower == "auto":
        for b_list in bases_by_browser.values():
            candidate_bases.extend(b_list)
    else:
        candidate_bases.extend(bases_by_browser.get(browser_lower, []))

    other_paths: list[Path] = []
    for base in candidate_bases:
        if not base.is_dir():
            continue

        target_folders: list[str] = []
        if profile:
            target_folders.append(profile)
            local_state = base / "Local State"
            if local_state.is_file():
                try:
                    state_data = json.loads(local_state.read_text(encoding="utf-8"))
                    info_cache = state_data.get("profile", {}).get("info_cache", {})
                    for folder, pinfo in info_cache.items():
                        if (
                            pinfo.get("name", "").lower() == profile.lower()
                            or pinfo.get("gaia_name", "").lower() == profile.lower()
                            or folder.lower() == profile.lower()
                        ):
                            target_folders.append(folder)
                except Exception:
                    pass

            for folder in target_folders:
                ldb = base / folder / "Local Storage" / "leveldb"
                if _is_leveldb_dir(ldb) and ldb not in other_paths:
                    other_paths.append(ldb)
        else:
            # Check if base itself is a profile directory
            base_ldb = base / "Local Storage" / "leveldb"
            if _is_leveldb_dir(base_ldb) and base_ldb not in other_paths:
                other_paths.append(base_ldb)

            # Check direct children (e.g. Default, Profile 1, Profile 2)
            try:
                for child in base.iterdir():
                    if child.is_dir():
                        ldb = child / "Local Storage" / "leveldb"
                        if _is_leveldb_dir(ldb) and ldb not in other_paths:
                            other_paths.append(ldb)
            except OSError:
                pass

    # Exclude dedicated paths from other_paths
    dedicated_set = set(dedicated_paths)
    other_paths = [p for p in other_paths if p not in dedicated_set]

    # Sort other_paths by mtime descending (newest first)
    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    other_paths.sort(key=_safe_mtime, reverse=True)

    result = dedicated_paths + other_paths
    # Deduplicate while preserving order
    seen: set[Path] = set()
    deduped: list[Path] = []
    for p in result:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return deduped


def _parse_msal_refresh_tokens(records: Iterable[Any]) -> list[TokenCandidate]:
    """Parse MSAL refresh and id tokens from records.

    Rules:
      - Filter to Costco hosts only
      - Collect id_token email by homeAccountId
      - Collect refresh tokens; prefer client_id == MSAL_CLIENT_ID
      - Deduplicate and sort by leveldb_seq_number descending
    """
    home_account_emails: dict[str, str] = {}
    home_account_idtokens: dict[str, str] = {}
    raw_candidates: list[TokenCandidate] = []

    for r in records:
        host = getattr(r, "host", None) or getattr(r, "storage_key", None) or ""
        if not _is_costco_host(host):
            continue

        val_str = getattr(r, "value", None)
        if not val_str or not isinstance(val_str, str):
            continue

        try:
            data = json.loads(val_str)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        script_key = (getattr(r, "script_key", "") or "").lower()
        cred_type = (data.get("credentialType") or "").lower()
        home_account_id = str(data.get("homeAccountId") or "")
        seq = int(getattr(r, "leveldb_seq_number", 0) or 0)

        # Check for id token
        if "idtoken" in script_key or cred_type == "idtoken":
            id_secret = data.get("secret")
            if id_secret and isinstance(id_secret, str):
                try:
                    payload = jwt.decode(id_secret, options={"verify_signature": False})
                    email = payload.get("email")
                    if email and home_account_id:
                        home_account_emails[home_account_id] = email
                        home_account_idtokens[home_account_id] = id_secret
                except Exception:
                    pass

        # Check for refresh token
        if "refreshtoken" in script_key or cred_type == "refreshtoken":
            secret = data.get("secret")
            client_id = str(data.get("clientId") or "")
            if secret and isinstance(secret, str):
                policy_name = None
                if home_account_id:
                    left = home_account_id.split(".")[0]
                    idx = left.lower().find("b2c_")
                    if idx != -1:
                        policy_name = left[idx:].lower()

                raw_candidates.append(
                    TokenCandidate(
                        secret=secret,
                        client_id=client_id,
                        home_account_id=home_account_id,
                        leveldb_seq_number=seq,
                        policy_name=policy_name,
                    )
                )

    if not raw_candidates:
        return []

    # Prefer MSAL_CLIENT_ID; ignore other client IDs if MSAL_CLIENT_ID is present
    has_spa_client = any(c.client_id == MSAL_CLIENT_ID for c in raw_candidates)
    if has_spa_client:
        filtered = [c for c in raw_candidates if c.client_id == MSAL_CLIENT_ID]
    else:
        filtered = raw_candidates

    # Pair with sibling id token email and id_token secret
    for c in filtered:
        c.email = home_account_emails.get(c.home_account_id)
        c.id_token = home_account_idtokens.get(c.home_account_id)

    # Deduplicate: if multiple records share the same secret or home_account_id,
    # keep the one with the highest sequence number
    best_by_key: dict[tuple[str, str], TokenCandidate] = {}
    for c in sorted(filtered, key=lambda x: x.leveldb_seq_number):
        key = (c.home_account_id, c.secret)
        best_by_key[key] = c

    # Sort candidates by leveldb_seq_number descending (newest first)
    candidates = list(best_by_key.values())
    candidates.sort(key=lambda x: x.leveldb_seq_number, reverse=True)
    return candidates


def verify_candidate(
    secret: str, policy_name: str | None = None
) -> tuple[bool | None, dict | None]:
    """Verify refresh token against Costco's Azure AD B2C token endpoint.

    Returns:
        (True, resp_dict) if token is active and minted an id_token
        (False, None) if token is expired/invalid (HTTP 400/401/403)
        (None, None) if verification is inconclusive (network/DNS/timeout error)
    """
    try:
        resp = _refresh_tokens(secret, policy_name=policy_name)
        if isinstance(resp, dict) and "id_token" in resp:
            return True, resp
        return False, None
    except (
        curl_exceptions.ConnectionError,
        curl_exceptions.DNSError,
        curl_exceptions.ConnectTimeout,
        curl_exceptions.ReadTimeout,
        curl_exceptions.Timeout,
    ):
        return None, None
    except curl_exceptions.HTTPError as exc:
        status_code = (
            getattr(exc.response, "status_code", None)
            if hasattr(exc, "response")
            else None
        )
        if status_code in (400, 401, 403):
            return False, None
        return None, None
    except Exception:
        return False, None


def extract_and_save(
    account: str = "",
    browser: str = "auto",
    profile: str | None = None,
    verify: bool = True,
    save: bool = True,
) -> ExtractResult:
    """Extract a Costco refresh token from Chromium browser profiles and save it.

    Parameters:
        account: Account name to save as (defaults to email prefix or default account).
        browser: 'auto', 'chrome', 'edge', 'brave', or 'chromium'.
        profile: Specific browser profile name or folder (e.g. 'Default', 'Personal').
        verify: Verify candidate against Azure AD B2C endpoint before saving.
        save: Save verified token to ~/.costco-mcp/accounts/<name>/auth.json.

    Returns:
        ExtractResult: Details of extracted token (does NOT expose the secret).
    """
    profile_paths = find_chromium_profiles(browser=browser, profile=profile)
    if not profile_paths:
        raise RuntimeError(
            f"No Chromium profiles found for browser='{browser}'"
            + (f" and profile='{profile}'" if profile else "")
            + "."
        )

    errors: list[str] = []
    fallback_candidate: tuple[TokenCandidate, Path] | None = None
    selected_candidate: TokenCandidate | None = None
    selected_profile: Path | None = None

    for profile_path in profile_paths:
        tmp_dir: Path | None = None
        try:
            copied_leveldb = _copy_leveldb_unlocked(profile_path)
            tmp_dir = copied_leveldb.parent

            with LocalStorageReader(str(copied_leveldb)) as reader:
                candidates = _parse_msal_refresh_tokens(reader.records())

            if not candidates:
                errors.append(f"Profile {profile_path}: no Costco refresh tokens found")
                continue

            # Prioritize candidate matching requested account name if specified
            if account:
                candidates.sort(
                    key=lambda c: bool(
                        c.email and c.email.split("@")[0].lower() == account.lower()
                    ),
                    reverse=True,
                )

            found_match = False
            for cand in candidates:
                if verify:
                    status, token_resp = verify_candidate(
                        cand.secret, policy_name=cand.policy_name
                    )
                    if status is True:
                        cand.verified = True
                        if token_resp and "id_token" in token_resp:
                            cand.id_token = token_resp["id_token"]
                        if token_resp and "_policy_name" in token_resp:
                            cand.policy_name = token_resp["_policy_name"]
                        selected_candidate = cand
                        selected_profile = profile_path
                        found_match = True
                        break
                    elif status is None:
                        if fallback_candidate is None:
                            cand.verified = False
                            fallback_candidate = (cand, profile_path)
                        errors.append(
                            f"Profile {profile_path}: token verification inconclusive (network/timeout)"
                        )
                    else:
                        errors.append(f"Profile {profile_path}: token expired (HTTP 400)")
                else:
                    cand.verified = False
                    selected_candidate = cand
                    selected_profile = profile_path
                    found_match = True
                    break

            if found_match:
                break
        except Exception as exc:
            errors.append(f"Profile {profile_path}: {exc}")
        finally:
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    if selected_candidate is None and fallback_candidate is not None:
        selected_candidate, selected_profile = fallback_candidate

    if selected_candidate is None or selected_profile is None:
        raise RuntimeError(
            "Failed to extract active Costco refresh token. Make sure you are logged into "
            "https://www.costco.com in your browser and that the session has not expired.\n"
            + "\n".join(errors)
        )

    # Determine target account name
    target_account = account.strip() if account else ""
    if not target_account:
        if selected_candidate.email:
            target_account = selected_candidate.email.split("@")[0]
        else:
            target_account = get_default_account() or "default"

    if save:
        auth = CostcoAuth(target_account)
        auth.save_refresh_token(
            selected_candidate.secret,
            id_token=selected_candidate.id_token,
            policy_name=selected_candidate.policy_name,
        )
        if selected_candidate.verified:
            auth.get_bearer_token()

    return ExtractResult(
        account=target_account,
        email=selected_candidate.email,
        source_profile=str(selected_profile),
        verified=selected_candidate.verified,
        _token=selected_candidate.secret,
    )
