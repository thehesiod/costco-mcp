import pytest

from costco_mcp_server import auth


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every auth path at a throwaway ~/.costco-mcp."""
    base = tmp_path / ".costco-mcp"
    monkeypatch.setattr(auth, "BASE_DIR", base)
    monkeypatch.setattr(auth, "ACCOUNTS_DIR", base / "accounts")
    monkeypatch.setattr(auth, "CONFIG_FILE", base / "config.json")
    monkeypatch.setattr(auth, "LEGACY_AUTH_FILE", base / "auth.json")
    return base
