import io
import sys

import pytest

from costco_mcp_server import server


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["costco-mcp-server", *argv])
    server.main()


def test_save_token_from_env(home, monkeypatch, capsys):
    monkeypatch.setenv("COSTCO_REFRESH_TOKEN", "env-token")
    _run(monkeypatch, ["--save-token", "personal"])
    assert '"env-token"' in (home / "accounts" / "personal" / "auth.json").read_text()
    assert "env-token" not in capsys.readouterr().out


def test_save_token_from_stdin(home, monkeypatch):
    monkeypatch.delenv("COSTCO_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("piped-token\n"))
    _run(monkeypatch, ["--save-token", "personal"])
    assert '"piped-token"' in (home / "accounts" / "personal" / "auth.json").read_text()


def test_positional_token_still_works_but_warns(home, monkeypatch, capsys):
    _run(monkeypatch, ["--save-token", "personal", "argv-token"])
    assert '"argv-token"' in (home / "accounts" / "personal" / "auth.json").read_text()
    assert "shell history" in capsys.readouterr().err


def test_bad_account_name_exits_cleanly(home, monkeypatch):
    monkeypatch.setenv("COSTCO_REFRESH_TOKEN", "t")
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["--save-token", "../x"])
    assert e.value.code == 1
