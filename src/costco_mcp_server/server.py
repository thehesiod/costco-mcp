"""Costco MCP Server - warehouse receipts and online orders, multi-account."""

import json
import logging
import sys
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

from costco_mcp_server.auth import CostcoAuth, list_accounts, get_default_account
from costco_mcp_server.api import CostcoAPI

# Must log to stderr to avoid corrupting MCP stdio transport
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP("Costco MCP Server")

# Cache of API instances per account
_apis: dict[str, CostcoAPI] = {}


def _get_api(account: str = "") -> CostcoAPI:
    """Get or create a CostcoAPI instance for the given account."""
    key = account or get_default_account() or "default"
    if key not in _apis:
        _apis[key] = CostcoAPI(CostcoAuth(key if account else None))
    return _apis[key]


def _default_date_range() -> tuple[str, str]:
    now = datetime.now()
    start = now - timedelta(days=90)
    return f"{start.month}/{start.day:02d}/{start.year}", f"{now.month}/{now.day:02d}/{now.year}"


def _default_date_range_iso() -> tuple[str, str]:
    now = datetime.now()
    start = now - timedelta(days=90)
    return f"{start.year}-{start.month}-{start.day:02d}", f"{now.year}-{now.month}-{now.day:02d}"


@mcp.tool()
def check_auth_status(account: str = "") -> str:
    """Check if authenticated with Costco. Shows token status and expiry.

    Args:
        account: Account name (optional, uses default if empty). Use list_accounts to see all.
    """
    api = _get_api(account)
    info = api._auth.check_status()
    info["all_accounts"] = list_accounts()
    info["default_account"] = get_default_account()
    return json.dumps(info, indent=2, default=str)


@mcp.tool()
def save_refresh_token(refresh_token: str, account: str = "") -> str:
    """Save a refresh token obtained from a browser login session.

    Use this to add a new account or update an existing one.
    The refresh token is valid for 90 days.

    Args:
        refresh_token: The OAuth2 refresh token from Costco's Azure AD B2C
        account: Account name (e.g. "personal", "spouse"). Creates the account if new.
                 If empty, uses default account.
    """
    name = account or get_default_account() or "default"
    auth = CostcoAuth(name)
    auth.save_refresh_token(refresh_token)
    # Clear cached API so it picks up new token
    _apis.pop(name, None)
    return f"Refresh token saved for account '{name}'. Accounts: {list_accounts()}"


@mcp.tool()
def list_warehouse_receipts(
    start_date: str = "",
    end_date: str = "",
    document_type: str = "all",
    account: str = "",
) -> str:
    """List in-warehouse purchase receipts for a date range.

    Returns receipt summaries with date, warehouse name, total, item count,
    and transaction barcode (use barcode with get_receipt_detail for full items).

    Args:
        start_date: Start date as M/DD/YYYY (e.g. "1/01/2026"). Defaults to 90 days ago.
        end_date: End date as M/DD/YYYY (e.g. "3/31/2026"). Defaults to today.
        document_type: Filter: "all", "warehouse", "gas", "carwash". Defaults to "all".
        account: Account name (optional, uses default).
    """
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    result = _get_api(account).list_receipts(start_date, end_date, document_type, "all")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_receipt_detail(barcode: str, account: str = "") -> str:
    """Get full itemized receipt detail for a warehouse purchase.

    Returns all line items with descriptions, prices, quantities, tax flags,
    plus payment tender details and tax breakdown.

    Args:
        barcode: Transaction barcode from list_warehouse_receipts
        account: Account name (optional, uses default).
    """
    result = _get_api(account).get_receipt_detail(barcode)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_online_orders(
    start_date: str = "",
    end_date: str = "",
    warehouse_number: str = "847",
    page_number: int = 1,
    page_size: int = 25,
    account: str = "",
) -> str:
    """List online Costco.com orders for a date range.

    Returns order summaries with order number, date, total, status,
    and line items with descriptions and shipping info.

    Args:
        start_date: Start date as YYYY-M-DD (e.g. "2026-1-01"). Defaults to 90 days ago.
        end_date: End date as YYYY-M-DD (e.g. "2026-3-31"). Defaults to today.
        warehouse_number: Warehouse number (default "847").
        page_number: Page number for pagination (default 1).
        page_size: Results per page (default 25).
        account: Account name (optional, uses default).
    """
    if not start_date or not end_date:
        start_date, end_date = _default_date_range_iso()

    result = _get_api(account).list_online_orders(
        start_date, end_date, warehouse_number, page_number, page_size
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def get_all_receipt_details(
    start_date: str = "",
    end_date: str = "",
    account: str = "",
) -> str:
    """Get full itemized details for ALL warehouse receipts in a date range.

    Fetches the receipt list then retrieves full details for each one.

    Args:
        start_date: Start date as M/DD/YYYY. Defaults to 90 days ago.
        end_date: End date as M/DD/YYYY. Defaults to today.
        account: Account name (optional, uses default).
    """
    if not start_date or not end_date:
        start_date, end_date = _default_date_range()

    api = _get_api(account)
    list_result = api.list_receipts(start_date, end_date, "all", "all")
    receipts = (
        list_result.get("data", {})
        .get("receiptsWithCounts", {})
        .get("receipts", [])
    )

    details = []
    for receipt in receipts:
        barcode = receipt.get("transactionBarcode")
        if not barcode:
            continue
        detail = api.get_receipt_detail(barcode)
        detail_receipts = (
            detail.get("data", {})
            .get("receiptsWithCounts", {})
            .get("receipts", [])
        )
        if detail_receipts:
            details.append(detail_receipts[0])

    return json.dumps({"count": len(details), "receipts": details}, indent=2)


@mcp.tool()
def lookup_products(item_numbers: str, warehouse_number: str = "847", account: str = "") -> str:
    """Look up full Costco product names by item numbers.

    Uses a shared local cache — only calls the API for uncached items.

    Args:
        item_numbers: Comma-separated item numbers (e.g. "70476,5887,1532925")
        warehouse_number: Warehouse number (default "847")
        account: Account name (optional, uses default).
    """
    nums = [n.strip() for n in item_numbers.split(",") if n.strip()]
    result = _get_api(account).lookup_products(nums, warehouse_number)
    return json.dumps(result, indent=2)


def main():
    if "--setup" in sys.argv:
        print("Costco MCP Server - Multi-Account Setup")
        print(f"Accounts: {list_accounts() or '(none)'}")
        print(f"Default: {get_default_account() or '(none)'}")
        print()
        print("To add an account:")
        print("  costco-mcp-server --save-token <ACCOUNT_NAME> <REFRESH_TOKEN>")
        print()
        print("To get a refresh token:")
        print("  1. Log into costco.com in a browser")
        print("  2. DevTools > Application > Local Storage > signin.costco.com")
        print("  3. Find the key containing 'refreshtoken', copy the 'secret' value")
        return

    if "--save-token" in sys.argv:
        idx = sys.argv.index("--save-token")
        if idx + 2 >= len(sys.argv):
            print("Usage: costco-mcp-server --save-token <ACCOUNT_NAME> <REFRESH_TOKEN>", file=sys.stderr)
            sys.exit(1)
        name = sys.argv[idx + 1]
        token = sys.argv[idx + 2]
        auth = CostcoAuth(name)
        auth.save_refresh_token(token)
        print(f"Token saved for account '{name}'")
        print(f"Accounts: {list_accounts()}")
        return

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
