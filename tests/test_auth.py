import json
import stat

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
