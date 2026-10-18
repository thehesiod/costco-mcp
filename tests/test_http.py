import sys

import pytest

from costco_mcp_server import server


def test_http_refuses_non_loopback(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["costco-mcp-server"])
    monkeypatch.setenv("COSTCO_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("COSTCO_MCP_HOST", "0.0.0.0")
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code == 2


def test_unknown_transport_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["costco-mcp-server"])
    monkeypatch.setenv("COSTCO_MCP_TRANSPORT", "sse")
    with pytest.raises(SystemExit) as e:
        server.main()
    assert e.value.code == 2


def test_security_settings_add_proxy_names(monkeypatch):
    monkeypatch.setenv("COSTCO_MCP_ALLOWED_HOSTS", "mcp.example.com, other.example")
    monkeypatch.setenv("COSTCO_MCP_ALLOWED_ORIGINS", "https://mcp.example.com")
    s = server.http_security_settings()
    assert s.enable_dns_rebinding_protection
    assert {"127.0.0.1:*", "mcp.example.com", "other.example"} <= set(s.allowed_hosts)
    assert "https://mcp.example.com" in s.allowed_origins


def test_http_mode_drops_save_refresh_token(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["costco-mcp-server"])
    monkeypatch.setenv("COSTCO_MCP_TRANSPORT", "streamable-http")
    monkeypatch.delenv("COSTCO_MCP_HOST", raising=False)
    seen = {}
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: seen.update(k))
    removed = []
    monkeypatch.setattr(server.mcp, "remove_tool", removed.append)
    server.main()
    assert removed == ["save_refresh_token"]
    assert seen["host"] == "127.0.0.1"
