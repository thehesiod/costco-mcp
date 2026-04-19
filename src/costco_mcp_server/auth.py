"""Costco authentication via Azure AD B2C token refresh — multi-account support."""

import json
import logging
import time
from pathlib import Path

import jwt
from curl_cffi import requests as curl_requests

# Chrome TLS impersonation is required because Costco's Akamai Bot Manager
# drops connections with non-browser JA3/JA4 fingerprints. Python's stdlib
# ssl (used by httpx and requests) gets silently timed out at the edge.
_IMPERSONATE = "chrome131"

logger = logging.getLogger(__name__)

# Azure AD B2C static configuration for Costco
TENANT_ID = "e0714dd4-784d-46d6-a278-3e29553483eb"
POLICY_NAME = "b2c_1a_sso_wcs_signup_signin_201"
TOKEN_ENDPOINT = f"https://signin.costco.com/{TENANT_ID}/{POLICY_NAME}/oauth2/v2.0/token"

# Client IDs
MSAL_CLIENT_ID = "a3a5186b-7c89-4b4c-93a8-dd604e930757"  # SPA / my-account app
WCS_CLIENT_ID = "4900eb1f-0c10-4bd9-99c3-c59e6c1ecebf"  # WebSphere Commerce

# Base directory
BASE_DIR = Path.home() / ".costco-mcp"
ACCOUNTS_DIR = BASE_DIR / "accounts"
CONFIG_FILE = BASE_DIR / "config.json"

# Legacy auth file (single-account)
LEGACY_AUTH_FILE = BASE_DIR / "auth.json"

_BROWSER_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.costco.com",
    "Referer": "https://www.costco.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(config: dict) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def _account_auth_file(account: str) -> Path:
    return ACCOUNTS_DIR / account / "auth.json"


def _load_account_auth(account: str) -> dict | None:
    path = _account_auth_file(account)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_account_auth(account: str, data: dict) -> None:
    path = _account_auth_file(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _is_token_expired(id_token: str, buffer_seconds: int = 120) -> bool:
    try:
        payload = jwt.decode(id_token, options={"verify_signature": False})
        exp = payload.get("exp", 0)
        return time.time() >= (exp - buffer_seconds)
    except jwt.DecodeError:
        return True


def _refresh_tokens(refresh_token: str) -> dict:
    data = {
        "client_id": MSAL_CLIENT_ID,
        "scope": "openid profile offline_access",
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = curl_requests.post(
        TOKEN_ENDPOINT,
        data=data,
        headers=_BROWSER_HEADERS,
        impersonate=_IMPERSONATE,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _migrate_legacy() -> None:
    """Migrate single-account auth.json to the multi-account structure."""
    if not LEGACY_AUTH_FILE.exists():
        return
    config = _load_config()
    if config.get("accounts"):
        return  # already migrated

    try:
        legacy = json.loads(LEGACY_AUTH_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return

    # Try to get the email from the token to name the account
    account_name = "default"
    id_token = legacy.get("id_token")
    if id_token:
        try:
            payload = jwt.decode(id_token, options={"verify_signature": False})
            email = payload.get("email", "")
            if email:
                account_name = email.split("@")[0]
        except jwt.DecodeError:
            pass

    _save_account_auth(account_name, legacy)
    config["accounts"] = [account_name]
    config["default"] = account_name
    _save_config(config)
    LEGACY_AUTH_FILE.rename(LEGACY_AUTH_FILE.with_suffix(".json.migrated"))
    logger.info("Migrated legacy auth to account '%s'", account_name)


def list_accounts() -> list[str]:
    """Return list of configured account names."""
    _migrate_legacy()
    config = _load_config()
    return config.get("accounts", [])


def get_default_account() -> str | None:
    """Return the default account name."""
    _migrate_legacy()
    config = _load_config()
    return config.get("default")


class CostcoAuth:
    """Manages Costco authentication tokens for a specific account."""

    def __init__(self, account: str | None = None) -> None:
        _migrate_legacy()
        if account is None:
            account = get_default_account()
        if account is None:
            # No accounts configured at all
            self._account = "default"
        else:
            self._account = account
        self._id_token: str | None = None
        self._refresh_token: str | None = None
        self._load_from_disk()

    @property
    def account(self) -> str:
        return self._account

    def _load_from_disk(self) -> None:
        auth = _load_account_auth(self._account)
        if auth:
            self._id_token = auth.get("id_token")
            self._refresh_token = auth.get("refresh_token")
            logger.info("Loaded auth for account '%s'", self._account)

    @property
    def is_authenticated(self) -> bool:
        return self._refresh_token is not None

    def get_bearer_token(self) -> str:
        if not self._refresh_token:
            raise RuntimeError(
                f"Account '{self._account}' not authenticated. "
                f"Use save_refresh_token to add credentials."
            )

        if self._id_token and not _is_token_expired(self._id_token):
            return self._id_token

        logger.info("Token expired for '%s', refreshing...", self._account)
        result = _refresh_tokens(self._refresh_token)

        self._id_token = result["id_token"]
        if "refresh_token" in result:
            self._refresh_token = result["refresh_token"]

        _save_account_auth(self._account, {
            "id_token": self._id_token,
            "refresh_token": self._refresh_token,
        })
        logger.info("Tokens refreshed for '%s'", self._account)
        return self._id_token

    def save_refresh_token(self, refresh_token: str) -> None:
        self._refresh_token = refresh_token
        self._id_token = None
        _save_account_auth(self._account, {"refresh_token": refresh_token})

        # Register account in config
        config = _load_config()
        accounts = config.get("accounts", [])
        if self._account not in accounts:
            accounts.append(self._account)
            config["accounts"] = accounts
        if not config.get("default"):
            config["default"] = self._account
        _save_config(config)
        logger.info("Refresh token saved for '%s'", self._account)

    def check_status(self) -> dict:
        info: dict = {
            "account": self._account,
            "auth_file": str(_account_auth_file(self._account)),
            "has_refresh_token": self.is_authenticated,
        }
        if self._id_token:
            try:
                payload = jwt.decode(self._id_token, options={"verify_signature": False})
                info["email"] = payload.get("email")
                info["token_expires"] = payload.get("exp")
                info["token_expired"] = _is_token_expired(self._id_token, buffer_seconds=0)
            except jwt.DecodeError:
                info["token_valid"] = False
        return info
