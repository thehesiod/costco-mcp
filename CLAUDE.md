# Costco MCP Server

## Architecture

MCP server that talks directly to Costco's internal GraphQL API using OAuth2 refresh tokens extracted from a browser session. No scraping, no headless browser at runtime — all API calls are pure HTTP via `httpx[http2]`. Runs as stdio transport.

```
src/costco_mcp_server/
  server.py         — MCP tool definitions (FastMCP)
  api.py            — CostcoAPI: GraphQL client, receipt/order/product queries
  auth.py           — CostcoAuth: per-account refresh token persistence + bearer minting
  auth_browser.py   — costco-auth-browser console script (launches Chrome with remote debugging)
  product_cache.py  — SQLite cache for product names + department codes
```

## Key Patterns

### Authentication (Azure AD B2C)

- Costco's SPA uses Azure AD B2C OAuth2. Tenant + policy + client IDs are **public** (ship in Costco's own browser JS) and hardcoded in `auth.py`.
- Refresh tokens live in `~/.costco-mcp/accounts/<name>/auth.json`. Valid ~90 days.
- Bearer tokens (`id_token`) are minted on demand, cached in memory with a 120-second expiry buffer (`_is_token_expired`).
- No ROPC (resource owner password credentials) — initial auth requires a browser login to Costco's MFA flow. After that, it's pure API via the refresh token.
- **`client-identifier` header** (`api.py:CLIENT_IDENTIFIER`) must be the exact value `481b1aec-aa3b-454b-b81b-48187e28f205`. Random UUIDs cause 401. This appears to be a static per-app identifier (not per-user), copied from Costco's browser bundle.

### Multi-account

- Accounts: `~/.costco-mcp/accounts/<name>/auth.json` per account. Config (default account + account list) at `~/.costco-mcp/config.json`.
- Every MCP tool takes an optional `account` parameter. Omitting it uses the default.
- Legacy single-account `~/.costco-mcp/auth.json` is auto-migrated to `default/` on first load (`_migrate_legacy`).
- `_apis` dict in `server.py` caches `CostcoAPI` instances per account name. `save_refresh_token` evicts on token update.

### Refresh token extraction

Two paths:

1. **Manual**: user opens Costco in their browser, reads `msal.*.refreshtoken` from localStorage (DevTools → Application tab), copies the `secret` field, calls `save_refresh_token` or `costco-mcp-server --save-token <name> <token>`.
2. **Semi-automated** (recommended): `costco-auth-browser` launches Chrome with remote debugging on port 9223 and a persistent profile. A separate `chrome-devtools-mcp` instance (pointed at that port) lets Claude read localStorage and call `save_refresh_token` automatically.

The refresh token is stored as Azure MSAL's base64-like secret, not a JWT. `auth.py` doesn't parse it — just POSTs it to the token endpoint.

### GraphQL endpoints

- **Orders**: `https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql` — warehouse receipts, online orders.
- **Products**: `https://ecom-api.costco.com/ebusiness/product/v1/products/graphql` — item number → name lookup.
- Both require the WCS client ID as `client_id` in the bearer token scope.

### Product cache

- SQLite DB at `~/.costco-mcp/products.db`. Caches `short_description` and `department` number per `(item_number, warehouse_number)` tuple.
- Product names come from the products GraphQL endpoint (batched up to 20 per request).
- Department codes come from receipt-detail responses (the products API doesn't return them). So `get_receipt_detail` populates the cache; `lookup_products` reads from it and falls back to the API.
- Department-to-category mapping lives **in callers** (e.g. the `sync-costco-receipts` skill), not in this server — keeps the server generic.

### Response format

All tools return formatted strings (markdown or plain text). No FastMCP `Image`/structured-content returns — receipt data is tabular and fits text well.

## Known Gotchas

### Expired refresh token (every ~90 days)

Symptom: `get_bearer_token` raises with a 400 from the token endpoint. The error message from Azure is HTML, not JSON — check `_refresh_tokens` response body.

Fix: re-run the extraction flow (either manual or `costco-auth-browser` + `chrome-devtools-mcp`). The stored `auth.json` for that account needs its `refresh_token` field replaced.

### `client-identifier` drift

If Costco rotates their app identifier (has not happened as of initial release), API calls will 401 with an opaque error. The fix is to extract the new identifier from the browser's Network tab (look for `client-identifier` request header on a `/orders/graphql` POST) and update `CLIENT_IDENTIFIER` in `api.py`.

### Date formats differ between endpoints

- **Warehouse receipts** (`list_warehouse_receipts`): `M/D/YYYY` (no zero-padding)
- **Online orders** (`list_online_orders`): `YYYY-M-D` (no zero-padding)

Both accept common variants but the above match what the web UI sends.

## Development

```bash
# Run the MCP server
uv run costco-mcp-server

# Launch auth browser
uv run costco-auth-browser

# One-shot token save (useful after manual extraction)
uv run costco-mcp-server --save-token personal <REFRESH_TOKEN>
```

### Adding a tool

1. Add the GraphQL query string to `api.py` (pattern: module-level `QUERY_*` const).
2. Add a method on `CostcoAPI` that posts the query and returns parsed dict/list.
3. Add the `@mcp.tool()` wrapper in `server.py`. Always accept an optional `account: str = ""` parameter.
4. Formatter/stringifier at the tool boundary — don't leak raw GraphQL envelopes to Claude.

### Testing against a live account

Tests are not checked in. For local smoke tests, use the default account and hit a known date range. Rate-limit is modest; Costco doesn't seem to throttle aggressively for read-only receipt/order queries.

## Open Improvement Areas

- **Refresh token auto-rotate**: Azure can issue new refresh tokens alongside new access tokens (when `offline_access` scope is present). Currently we never update the stored refresh token. If it drifts/rotates, our saved copy goes stale before 90 days.
- **Product cache warm-up**: `lookup_products` fires one GraphQL call per cache miss. A bulk-fetch-and-cache flow would help first-time setups with large receipt histories.
- **Error classification**: All non-200 responses currently bubble up as generic httpx errors. Classifying into "expired token" / "rate limited" / "bad query" would let tools return clearer messages.
