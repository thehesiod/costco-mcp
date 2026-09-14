"""Unit tests for server module, MCP extract_refresh_token tool, and CLI."""

import json
from unittest.mock import MagicMock, patch

from costco_mcp_server.extract import ExtractResult
from costco_mcp_server.server import _apis, extract_refresh_token, main


def test_extract_refresh_token_mcp_tool():
    secret_token = "very-secret-refresh-token-value-xyz"
    fake_res = ExtractResult(
        account="shopper",
        email="shopper@domain.com",
        source_profile="/path/to/profile/Default/Local Storage/leveldb",
        verified=True,
        _token=secret_token,
    )

    _apis["shopper"] = MagicMock()
    _apis["other"] = MagicMock()

    with patch("costco_mcp_server.server.extract_and_save", return_value=fake_res), \
         patch("costco_mcp_server.server.list_accounts", return_value=["shopper", "other"]):
        res_str = extract_refresh_token(account="shopper", browser="chrome", profile="")

        # Must be valid JSON
        data = json.loads(res_str)
        assert data["account"] == "shopper"
        assert data["email"] == "shopper@domain.com"
        assert data["source_profile"] == "/path/to/profile/Default/Local Storage/leveldb"
        assert data["verified"] is True
        assert data["all_accounts"] == ["shopper", "other"]

        # Secret MUST NOT leak into the output
        assert secret_token not in res_str

        # _apis["shopper"] must be evicted
        assert "shopper" not in _apis
        assert "other" in _apis


def test_extract_refresh_token_mcp_tool_failure():
    with patch("costco_mcp_server.server.extract_and_save", side_effect=RuntimeError("No active tokens found")):
        res_str = extract_refresh_token(account="", browser="auto", profile="")
        assert "Extraction failed: No active tokens found" in res_str


def test_cli_extract_token(capsys):
    secret_token = "cli-secret-token"
    fake_res = ExtractResult(
        account="personal",
        email="test@costco.com",
        source_profile="/fake/profile",
        verified=True,
        _token=secret_token,
    )

    with patch("sys.argv", ["costco-mcp-server", "--extract-token", "--account", "personal"]), \
         patch("costco_mcp_server.server.extract_and_save", return_value=fake_res), \
         patch("costco_mcp_server.server.list_accounts", return_value=["personal"]):
        main()
        captured = capsys.readouterr()
        assert "Extraction successful:" in captured.out
        assert "Account: personal" in captured.out
        assert "Email: test@costco.com" in captured.out
        # Without --print-token, secret token must not be printed
        assert secret_token not in captured.out
        assert secret_token not in captured.err


def test_cli_extract_token_print_token(capsys):
    secret_token = "cli-secret-token"
    fake_res = ExtractResult(
        account="personal",
        email="test@costco.com",
        source_profile="/fake/profile",
        verified=True,
        _token=secret_token,
    )

    with patch("sys.argv", ["costco-mcp-server", "--extract-token", "--print-token"]), \
         patch("costco_mcp_server.server.extract_and_save", return_value=fake_res), \
         patch("costco_mcp_server.server.list_accounts", return_value=["personal"]):
        main()
        captured = capsys.readouterr()
        assert secret_token in captured.out
        assert "WARNING: Printing refresh token" in captured.err
