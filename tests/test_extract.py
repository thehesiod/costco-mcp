"""Unit tests for extract module."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
import pytest
from curl_cffi.requests import exceptions as curl_exceptions

from costco_mcp_server.auth import MSAL_CLIENT_ID
from costco_mcp_server.extract import (
    ExtractResult,
    TokenCandidate,
    _copy_leveldb_unlocked,
    _is_costco_host,
    _parse_msal_refresh_tokens,
    extract_and_save,
    find_chromium_profiles,
    verify_candidate,
)


class DummyRecord:
    def __init__(
        self,
        storage_key: str,
        script_key: str,
        value: str | None,
        leveldb_seq_number: int = 1,
        is_live: bool = True,
    ):
        self.storage_key = storage_key
        self.host = storage_key
        self.script_key = script_key
        self.value = value
        self.leveldb_seq_number = leveldb_seq_number
        self.is_live = is_live


def test_is_costco_host():
    assert _is_costco_host("https://www.costco.com")
    assert _is_costco_host("http://www.costco.com")
    assert _is_costco_host("https://www.costco.com:443")
    assert _is_costco_host("www.costco.com")
    assert _is_costco_host("https://signin.costco.com")
    assert _is_costco_host("signin.costco.com")
    assert _is_costco_host("costco.com")
    assert _is_costco_host("https://costco.com")

    assert not _is_costco_host("https://sameday.costco.com")
    assert not _is_costco_host("https://logistics.costco.com")
    assert not _is_costco_host("https://google.com")
    assert not _is_costco_host("https://amazon.com")
    assert not _is_costco_host("")
    assert not _is_costco_host(None)


def test_parse_msal_refresh_tokens():
    home_id = "user123.tenant456"
    test_email = "member@example.com"
    dummy_jwt = jwt.encode(
        {"email": test_email, "exp": 9999999999},
        "dummy-secret-with-at-least-32-bytes-long!",
        algorithm="HS256",
    )

    id_token_val = json.dumps({
        "credentialType": "IdToken",
        "homeAccountId": home_id,
        "clientId": MSAL_CLIENT_ID,
        "secret": dummy_jwt,
    })

    spa_refresh_val = json.dumps({
        "credentialType": "RefreshToken",
        "homeAccountId": home_id,
        "clientId": MSAL_CLIENT_ID,
        "secret": "spa-refresh-token-secret",
    })

    other_refresh_val = json.dumps({
        "credentialType": "RefreshToken",
        "homeAccountId": "other-user",
        "clientId": "other-client-id",
        "secret": "other-secret",
    })

    records = [
        # Irrelevant origin
        DummyRecord("https://example.com", "refreshtoken", spa_refresh_val),
        # Costco ID token
        DummyRecord("https://www.costco.com", f"{home_id}-idtoken-{MSAL_CLIENT_ID}", id_token_val, leveldb_seq_number=5),
        # Costco Refresh token (SPA)
        DummyRecord("https://www.costco.com", f"{home_id}-refreshtoken-{MSAL_CLIENT_ID}", spa_refresh_val, leveldb_seq_number=10),
        # Costco Refresh token with other client ID
        DummyRecord("https://signin.costco.com", "other-refreshtoken", other_refresh_val, leveldb_seq_number=2),
        # Non-JSON record
        DummyRecord("https://www.costco.com", "some_key", "not-json"),
        # Record with missing secret
        DummyRecord("https://www.costco.com", "refreshtoken", json.dumps({"credentialType": "RefreshToken"})),
    ]

    candidates = _parse_msal_refresh_tokens(records)
    # SPA client ID should be preferred, other client ID dropped
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.secret == "spa-refresh-token-secret"
    assert cand.client_id == MSAL_CLIENT_ID
    assert cand.home_account_id == home_id
    assert cand.email == test_email
    assert cand.id_token == dummy_jwt
    assert cand.leveldb_seq_number == 10


def test_parse_msal_deduplication_and_sequence():
    home_id = "user123.tenant456"
    old_val = json.dumps({
        "credentialType": "RefreshToken",
        "homeAccountId": home_id,
        "clientId": MSAL_CLIENT_ID,
        "secret": "old-token",
    })
    new_val = json.dumps({
        "credentialType": "RefreshToken",
        "homeAccountId": home_id,
        "clientId": MSAL_CLIENT_ID,
        "secret": "new-token",
    })

    records = [
        DummyRecord("https://www.costco.com", "refreshtoken", old_val, leveldb_seq_number=5),
        DummyRecord("https://www.costco.com", "refreshtoken", new_val, leveldb_seq_number=20),
    ]

    candidates = _parse_msal_refresh_tokens(records)
    assert len(candidates) == 2
    # Should be sorted newest sequence first
    assert candidates[0].secret == "new-token"
    assert candidates[0].leveldb_seq_number == 20
    assert candidates[1].secret == "old-token"
    assert candidates[1].leveldb_seq_number == 5


def test_find_chromium_profiles(tmp_path):
    dedicated_dir = tmp_path / "dedicated"
    dedicated_leveldb = dedicated_dir / "Default" / "Local Storage" / "leveldb"
    dedicated_leveldb.mkdir(parents=True)
    (dedicated_leveldb / "CURRENT").write_text("dummy")

    chrome_base = tmp_path / "chrome"
    profile1_leveldb = chrome_base / "Default" / "Local Storage" / "leveldb"
    profile1_leveldb.mkdir(parents=True)
    (profile1_leveldb / "CURRENT").write_text("dummy")

    profile2_leveldb = chrome_base / "Profile 2" / "Local Storage" / "leveldb"
    profile2_leveldb.mkdir(parents=True)
    (profile2_leveldb / "CURRENT").write_text("dummy")

    # Set profile2 to be newer than profile1
    os.utime(profile1_leveldb, (1000, 1000))
    os.utime(profile2_leveldb, (2000, 2000))
    # Even older dedicated profile
    os.utime(dedicated_leveldb, (500, 500))

    # Add Local State
    local_state = chrome_base / "Local State"
    local_state.write_text(json.dumps({
        "profile": {
            "info_cache": {
                "Profile 2": {"name": "Personal Work", "gaia_name": "My Name"}
            }
        }
    }))

    with patch("costco_mcp_server.extract.DEDICATED_PROFILE_DIR", dedicated_dir), \
         patch("costco_mcp_server.extract._get_chromium_user_data_dirs", return_value={"chrome": [chrome_base]}):

        # 1. Search all: dedicated should be first, then profile 2 (newer), then profile 1
        profiles = find_chromium_profiles("chrome")
        assert len(profiles) == 3
        assert profiles[0] == dedicated_leveldb
        assert profiles[1] == profile2_leveldb
        assert profiles[2] == profile1_leveldb

        # 2. Filter by profile name matching gaia_name
        filtered = find_chromium_profiles("chrome", profile="Personal Work")
        assert profile2_leveldb in filtered
        assert profile1_leveldb not in filtered

        # 3. Invalid browser
        with pytest.raises(ValueError, match="Unsupported browser"):
            find_chromium_profiles("firefox")


def test_verify_candidate():
    # 1. Successful verification
    with patch("costco_mcp_server.extract._refresh_tokens", return_value={"id_token": "valid_token"}):
        status, resp = verify_candidate("token123")
        assert status is True
        assert resp == {"id_token": "valid_token"}

    # 2. Expired token (HTTP 400)
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_err_400 = curl_exceptions.HTTPError("HTTP Error 400", response=mock_resp)
    with patch("costco_mcp_server.extract._refresh_tokens", side_effect=mock_err_400):
        status, resp = verify_candidate("token123")
        assert status is False
        assert resp is None

    # 3. Network / timeout error (inconclusive)
    with patch("costco_mcp_server.extract._refresh_tokens", side_effect=curl_exceptions.ConnectTimeout("timeout")):
        status, resp = verify_candidate("token123")
        assert status is None
        assert resp is None

    with patch("costco_mcp_server.extract._refresh_tokens", side_effect=curl_exceptions.ConnectionError("conn refused")):
        status, resp = verify_candidate("token123")
        assert status is None
        assert resp is None


def test_extract_and_save(tmp_path):
    fake_profile = tmp_path / "chrome-profile" / "Default" / "Local Storage" / "leveldb"
    fake_profile.mkdir(parents=True)
    (fake_profile / "CURRENT").write_text("dummy")

    fake_cand = TokenCandidate(
        secret="extracted-secret-token",
        client_id=MSAL_CLIENT_ID,
        home_account_id="home123",
        email="shopper@domain.com",
    )

    mock_auth = MagicMock()
    mock_auth.account = "shopper"

    with patch("costco_mcp_server.extract.find_chromium_profiles", return_value=[fake_profile]), \
         patch("costco_mcp_server.extract._copy_leveldb_unlocked", return_value=fake_profile), \
         patch("costco_mcp_server.extract.LocalStorageReader") as mock_reader_cls, \
         patch("costco_mcp_server.extract._parse_msal_refresh_tokens", return_value=[fake_cand]), \
         patch("costco_mcp_server.extract.verify_candidate", return_value=(True, {"id_token": "new-id-token"})), \
         patch("costco_mcp_server.extract.CostcoAuth", return_value=mock_auth):

        mock_reader = MagicMock()
        mock_reader.__enter__.return_value = mock_reader
        mock_reader.records.return_value = []
        mock_reader_cls.return_value = mock_reader

        result = extract_and_save(account="", browser="chrome", verify=True, save=True)

        assert isinstance(result, ExtractResult)
        assert result.account == "shopper"
        assert result.email == "shopper@domain.com"
        assert result.verified is True
        assert result.source_profile == str(fake_profile)

        # Confirm secret is not in repr or to_dict
        assert "extracted-secret-token" not in repr(result)
        dict_rep = result.to_dict()
        assert "extracted-secret-token" not in str(dict_rep)
        assert dict_rep == {
            "account": "shopper",
            "email": "shopper@domain.com",
            "source_profile": str(fake_profile),
            "verified": True,
        }

        # Verify CostcoAuth methods called
        mock_auth.save_refresh_token.assert_called_once_with(
            "extracted-secret-token",
            id_token="new-id-token",
            policy_name=None,
        )
        mock_auth.get_bearer_token.assert_called_once()


def test_copy_leveldb_unlocked(tmp_path):
    src = tmp_path / "leveldb"
    src.mkdir()
    (src / "CURRENT").write_text("manifest-001")
    (src / "000001.log").write_text("log data")
    (src / "LOCK").write_text("lock data")

    dest = _copy_leveldb_unlocked(src)
    try:
        assert dest.is_dir()
        assert (dest / "CURRENT").is_file()
        assert (dest / "000001.log").is_file()
        assert not (dest / "LOCK").exists()
    finally:
        import shutil
        shutil.rmtree(dest.parent, ignore_errors=True)
