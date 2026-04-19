# costco-mcp

Model Context Protocol server for Costco warehouse receipts and online orders, with multi-account support.

Talks directly to Costco's internal GraphQL API using an OAuth2 refresh token extracted from a browser session. No scraping, no headless browser at runtime — the authenticated call pattern is pure HTTP via [httpx](https://www.python-httpx.org/).

## Tools

| Tool | Purpose |
|-|-|
| `check_auth_status` | Report token freshness and account list |
| `save_refresh_token` | Register/update a refresh token for an account (manual path) |
| `list_warehouse_receipts` | Warehouse transactions for a date range |
| `get_receipt_detail` | Line items for a specific receipt barcode |
| `list_online_orders` | Online orders for a date range |
| `get_all_receipt_details` | Bulk fetch of receipts with detail |
| `lookup_products` | Product info by item number (for a warehouse) |

Refresh tokens live in `~/.costco-mcp/accounts/<name>/auth.json` and are valid for ~90 days. Bearer tokens are minted transparently on each API call.

## Install

Requires Python 3.12+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/thehesiod/costco-mcp ~/costco-mcp-server
```

Register with Claude Code:

```bash
claude mcp add costco -- \
    uv run --python 3.13 \
    --with-editable ~/costco-mcp-server \
    python -u -m costco_mcp_server.server
```

Or, if you've `uv pip install ~/costco-mcp-server` globally, `claude mcp add costco -- costco-mcp-server` works.

## Authentication

Costco doesn't expose a public OAuth app, so refresh tokens are extracted from your browser's Azure AD B2C session storage. Two flows — semi-automated is recommended.

### Semi-automated (recommended)

Uses a dedicated Chrome profile + [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) so Claude can extract and register the token for you. After first setup, each re-auth is "launch the browser, ask Claude to refresh."

1. **Launch the auth browser** (cross-platform console script):

   ```bash
   costco-auth-browser
   ```

   This starts Chrome on port `9223` with a persistent profile at `~/.costco-mcp/chrome-profile/`.

2. **Register a chrome-devtools-mcp instance** pointed at that port (one time):

   ```bash
   claude mcp add costco-browser -- npx -y chrome-devtools-mcp --browserUrl http://127.0.0.1:9223
   ```

3. **Log into costco.com** in the launched browser window (first time only — the profile persists).

4. **Ask Claude** to refresh:

   > Extract my Costco refresh token and save it for account "personal".

   Claude will read the `msal.*.refreshtoken` entry from localStorage via `chrome-devtools-mcp`, then call `save_refresh_token`.

### Manual fallback

If you don't want to wire up a second MCP:

1. Log into [costco.com](https://www.costco.com) in any browser.
2. Open DevTools → Application → Local Storage → `https://www.costco.com`.
3. Find the key containing `refreshtoken` (format: `msal.<tenant>.<client>-<scope>-refreshtoken`).
4. Copy the `secret` field from its JSON value.
5. Call the `save_refresh_token` MCP tool with that string, or run the CLI:

   ```bash
   costco-mcp-server --save-token personal <REFRESH_TOKEN>
   ```

## Multi-account

Every tool takes an optional `account` argument (e.g. `"personal"`, `"spouse"`). Omitting it uses the default account (first registered, or whichever `set_default_account` most recently set).

Account data is isolated per name under `~/.costco-mcp/accounts/<name>/`.

## Platform notes

Fully cross-platform (macOS, Linux, Windows). No shell scripts — all entry points are Python console scripts.

The semi-automated flow requires Chrome or Chromium installed at a standard location. `costco-auth-browser` searches:

- **macOS**: `/Applications/Google Chrome.app/...`
- **Windows**: `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LocalAppData%` under `Google\Chrome\Application\chrome.exe`
- **Linux / fallback**: `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser` on `PATH`

Override the debugger port with `COSTCO_AUTH_PORT`.

## Security notes

- Refresh tokens are stored plaintext in `~/.costco-mcp/`. File permissions are not hardened — rely on OS-level home directory permissions.
- The Azure AD B2C client IDs baked into `auth.py` (tenant, policy, MSAL/WCS client IDs) are public SPA identifiers that ship in Costco's own browser bundle. They are **not** secrets.
- No credentials (username/password) are handled by this server — authentication happens entirely in your browser's Costco login flow.

## License

MIT — see `LICENSE`.
