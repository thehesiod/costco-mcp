import pytest

from costco_mcp_server import auth


@pytest.fixture
def env_token(home, monkeypatch):
    monkeypatch.setenv("COSTCO_REFRESH_TOKEN", "seed-token")
    monkeypatch.delenv("COSTCO_ACCOUNT", raising=False)
    monkeypatch.setattr(auth, "_is_token_expired", lambda *a, **k: True)
    calls = []

    def fake_refresh(token):
        calls.append(token)
        return {"id_token": "id", "refresh_token": f"rotated-{len(calls)}"}

    monkeypatch.setattr(auth, "_refresh_tokens", fake_refresh)
    return calls


def test_env_token_is_default_account(env_token):
    assert auth.get_default_account() == "default"
    assert auth.list_accounts() == ["default"]
    assert auth.CostcoAuth().check_status()["token_source"] == "$COSTCO_REFRESH_TOKEN"


def test_env_token_never_written_to_disk(home, env_token):
    a = auth.CostcoAuth()
    a.get_bearer_token()
    a.get_bearer_token()
    assert env_token == ["seed-token", "rotated-1"]  # rotation kept in memory
    assert not (home / "accounts").exists()


def test_env_account_name(home, env_token, monkeypatch):
    monkeypatch.setenv("COSTCO_ACCOUNT", "personal")
    assert auth.CostcoAuth().account == "personal"
    monkeypatch.setenv("COSTCO_ACCOUNT", "../x")
    with pytest.raises(ValueError):
        auth.get_default_account()


def test_save_refresh_token_refused_for_env_account(env_token):
    with pytest.raises(RuntimeError, match="COSTCO_REFRESH_TOKEN"):
        auth.CostcoAuth().save_refresh_token("other")


def test_file_accounts_still_work_alongside_env(home, env_token):
    auth.CostcoAuth("spouse", use_env=False).save_refresh_token("file-token")
    assert auth.list_accounts() == ["default", "spouse"]
    assert auth.CostcoAuth("spouse")._refresh_token == "file-token"


def test_refresh_failure_points_at_env(home, monkeypatch):
    monkeypatch.setenv("COSTCO_REFRESH_TOKEN", "dead")
    monkeypatch.setattr(auth, "_refresh_tokens", lambda t: (_ for _ in ()).throw(OSError("400")))
    with pytest.raises(RuntimeError, match=r"\$COSTCO_REFRESH_TOKEN"):
        auth.CostcoAuth().get_bearer_token()
