# Costco MCP Server

[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io) [![PyPI](https://img.shields.io/pypi/v/costco-mcp-server)](https://pypi.org/project/costco-mcp-server/)

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that gives Claude access to [Costco](https://www.costco.com) warehouse receipts and online orders. Talks directly to Costco's internal GraphQL API using OAuth2 refresh tokens extracted from a browser session — no scraping, no runtime headless browser. **Multi-account** (e.g. `personal`, `spouse`) with per-account token storage.

Available on the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.thehesiod/costco`.

## Disclaimer

> **This project is not affiliated with, endorsed by, or sponsored by Costco Wholesale Corporation.** "Costco" and all related names, logos, and trademarks are the property of their respective owners.
>
> This server communicates with Costco's **undocumented internal APIs** — endpoints that are not published, not guaranteed to be stable, and may change or be blocked at any time without notice. Use of those APIs may violate Costco's Terms of Service; you are responsible for reviewing the ToS and deciding whether your use is acceptable.
>
> **Use at your own risk.** The authors and contributors accept no responsibility for any consequences of using this software, including but not limited to: account suspension or termination, data loss or corruption, incorrect receipt/order information, failed purchases, financial discrepancies, API rate-limit strikes, IP blocks, or any other direct or indirect damages. No warranty is provided — see [LICENSE](LICENSE) for the full MIT no-warranty clause.
>
> If Costco publishes an official API, this project should be considered deprecated in favor of that.

## Features

### Warehouse
- **`list_warehouse_receipts`** — In-store receipts for a date range (barcode, warehouse, total)
- **`get_receipt_detail`** — Full itemized receipt by barcode (products, quantities, prices, coupons, department codes)
- **`get_all_receipt_details`** — Bulk fetch every receipt's full detail for a date range

### Online Orders
- **`list_online_orders`** — Costco.com orders for a date range (order number, status, items, totals)

### Products
- **`lookup_products`** — Resolve item numbers to full product names + departments. Uses a local SQLite cache (`~/.costco-mcp/products.db`) so repeated lookups are free.

### Authentication
- **`check_auth_status`** — Report token freshness and list configured accounts
- **`save_refresh_token`** — Register or update an account's refresh token

## Setup

### Install in Claude Code

```bash
claude mcp add --transport stdio costco -- \
    uvx --from "costco-mcp-server @ git+https://github.com/thehesiod/costco-mcp" costco-mcp-server
```

Once [published to PyPI](https://pypi.org/project/costco-mcp-server/), the above simplifies to:

```bash
claude mcp add --transport stdio costco -- uvx costco-mcp-server
```

### Authentication

Costco doesn't expose a public OAuth app, so refresh tokens are extracted from your browser's Azure AD B2C session storage. Refresh tokens are valid for ~90 days; bearer tokens are minted transparently on each API call.

**Semi-automated flow (recommended).** Uses a dedicated Chrome profile + [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) so Claude extracts and registers the token for you. After first setup, each re-auth is "launch the browser, ask Claude to refresh."

1. **Launch the auth browser** (cross-platform console script):

   ```bash
   uvx --from "costco-mcp-server @ git+https://github.com/thehesiod/costco-mcp" costco-auth-browser
   ```

   Chrome starts on port 9223 with a persistent profile at `~/.costco-mcp/chrome-profile/`.

2. **Register a chrome-devtools-mcp instance** pointed at that port (one time):

   ```bash
   claude mcp add costco-browser -- npx -y chrome-devtools-mcp --browserUrl http://127.0.0.1:9223
   ```

3. **Log into costco.com** in the launched browser window (first time only — the profile persists).

4. **Ask Claude** to refresh:

   > Extract my Costco refresh token and save it for account "personal".

   Claude reads the `msal.*.refreshtoken` entry from localStorage via `chrome-devtools-mcp` and calls `save_refresh_token`.

**Manual fallback.** If you'd rather not wire up a second MCP: log into costco.com, open DevTools → Application → Local Storage → `https://www.costco.com`, find the key containing `refreshtoken`, copy the `secret` field from its JSON value, then:

```bash
uvx --from "costco-mcp-server @ git+https://github.com/thehesiod/costco-mcp" \
    costco-mcp-server --save-token personal <REFRESH_TOKEN>
```

## Multi-Account

Every tool takes an optional `account` argument (e.g. `"personal"`, `"spouse"`). Omitting it uses the default account (first registered, or most recently set). Account data is isolated per name under `~/.costco-mcp/accounts/<name>/`.

## How It Works

The server uses `httpx[http2]` to hit Costco's internal GraphQL endpoints directly:

- **`ecom-api.costco.com/ebusiness/order/v1/orders/graphql`** — warehouse receipts + online orders
- **`ecom-api.costco.com/ebusiness/product/v1/products/graphql`** — product lookups

Authentication uses Azure AD B2C (`signin.costco.com`). The public client IDs baked into `auth.py` (tenant, policy, MSAL/WCS client IDs) ship in Costco's own browser bundle — they are not secrets. No credentials (username/password) are handled by this server.

See [CLAUDE.md](CLAUDE.md) for architecture details and development notes.

## Platform Notes

Fully cross-platform (macOS, Linux, Windows). No shell scripts — all entry points are Python console scripts.

The semi-automated auth flow requires Chrome or Chromium installed at a standard location. `costco-auth-browser` searches:

- **macOS**: `/Applications/Google Chrome.app/...`
- **Windows**: `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LocalAppData%` under `Google\Chrome\Application\chrome.exe`
- **Linux / fallback**: `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser` on `PATH`

Override the debugger port with `COSTCO_AUTH_PORT`.

## Security Notes

- Refresh tokens are stored plaintext in `~/.costco-mcp/`. Rely on OS-level home directory permissions.
- No credentials handled by this server. Authentication happens entirely in your browser's Costco login flow.
- The Azure AD B2C client IDs in `auth.py` are public SPA identifiers that ship in Costco's browser bundle — not secrets.

## License

MIT — see [LICENSE](LICENSE).

mcp-name: io.github.thehesiod/costco
