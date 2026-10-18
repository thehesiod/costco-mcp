import json
import stat

import pytest

from costco_mcp_server import auth


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_saved_token_is_owner_only(home):
    auth.CostcoAuth("personal").save_refresh_token("secret-token")

    token_file = home / "accounts" / "personal" / "auth.json"
    assert json.loads(token_file.read_text()) == {"refresh_token": "secret-token"}
    assert _mode(token_file) == 0o600
    assert _mode(token_file.parent) == 0o700
    assert _mode(home) == 0o700
    assert _mode(home / "config.json") == 0o600


def test_existing_world_readable_token_is_tightened(home):
    token_file = home / "accounts" / "personal" / "auth.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("{}")
    token_file.chmod(0o644)

    auth.CostcoAuth("personal").save_refresh_token("secret-token")

    assert _mode(token_file) == 0o600


def test_no_temp_files_left_behind(home):
    auth.CostcoAuth("personal").save_refresh_token("secret-token")

    leftovers = [p.name for p in (home / "accounts" / "personal").iterdir()]
    assert leftovers == ["auth.json"]



@pytest.mark.parametrize("name", ["../escape", "..", "/etc", "a/b", ".hidden", "", "x" * 65, "a\\b"])
def test_unsafe_account_names_rejected(home, name):
    with pytest.raises(ValueError):
        auth.CostcoAuth(name).save_refresh_token("secret-token")
    assert not (home.parent / "escape").exists()


@pytest.mark.parametrize("name", ["personal", "spouse", "john.doe", "acct_2", "a-b"])
def test_normal_account_names_allowed(home, name):
    auth.CostcoAuth(name).save_refresh_token("secret-token")
    assert (home / "accounts" / name / "auth.json").exists()


def test_legacy_migration_sanitizes_email_name(home, monkeypatch):
    home.mkdir()
    (home / "auth.json").write_text(json.dumps({"id_token": "x", "refresh_token": "r"}))
    monkeypatch.setattr(auth.jwt, "decode", lambda *a, **k: {"email": "../evil@example.com"})

    assert auth.list_accounts() == ["default"]
    assert (home / "accounts" / "default" / "auth.json").exists()
