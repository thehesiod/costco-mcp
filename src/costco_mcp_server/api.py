"""Costco GraphQL API client."""

import logging
import uuid
from typing import Any

from curl_cffi import requests as curl_requests

from costco_mcp_server.auth import CostcoAuth, WCS_CLIENT_ID
from costco_mcp_server.product_cache import get_cached_names, store_names

# Must match the TLS fingerprint used in auth.py — Akamai Bot Manager
# rejects requests whose JA3/JA4 doesn't match a recognized browser build.
_IMPERSONATE = "chrome131"

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = "https://ecom-api.costco.com/ebusiness/order/v1/orders/graphql"
PRODUCT_GRAPHQL_ENDPOINT = "https://ecom-api.costco.com/ebusiness/product/v1/products/graphql"

# GraphQL query: list warehouse receipts for a date range
QUERY_LIST_RECEIPTS = """
query receiptsWithCounts($startDate: String!, $endDate: String!, $documentType: String!, $documentSubType: String!) {
    receiptsWithCounts(startDate: $startDate, endDate: $endDate, documentType: $documentType, documentSubType: $documentSubType) {
        inWarehouse
        gasStation
        carWash
        gasAndCarWash
        receipts {
            warehouseName
            receiptType
            documentType
            transactionDateTime
            transactionBarcode
            transactionType
            total
            totalItemCount
            itemArray {
                itemNumber
            }
            tenderArray {
                tenderTypeCode
                tenderDescription
                amountTender
            }
            couponArray {
                upcnumberCoupon
            }
        }
    }
}
"""

# GraphQL query: get full receipt detail by barcode
QUERY_RECEIPT_DETAIL = """
query receiptsWithCounts($barcode: String!, $documentType: String!) {
    receiptsWithCounts(barcode: $barcode, documentType: $documentType) {
        receipts {
            warehouseName
            receiptType
            documentType
            transactionDateTime
            transactionDate
            companyNumber
            warehouseNumber
            operatorNumber
            warehouseShortName
            registerNumber
            transactionNumber
            transactionType
            transactionBarcode
            total
            warehouseAddress1
            warehouseAddress2
            warehouseCity
            warehouseState
            warehouseCountry
            warehousePostalCode
            totalItemCount
            subTotal
            taxes
            invoiceNumber
            sequenceNumber
            membershipNumber
            instantSavings
            itemArray {
                itemNumber
                itemDescription01
                itemDescription02
                itemIdentifier
                itemDepartmentNumber
                unit
                amount
                taxFlag
                transDepartmentNumber
                itemUnitPriceAmount
            }
            tenderArray {
                tenderTypeCode
                tenderSubTypeCode
                tenderDescription
                amountTender
                displayAccountNumber
                sequenceNumber
                approvalNumber
                tenderTypeName
                walletType
            }
            subTaxes {
                tax1
                tax2
                tax3
                tax4
                aTaxPercent
                aTaxLegend
                aTaxAmount
                aTaxPrintCode
                bTaxPercent
                bTaxLegend
                bTaxAmount
                bTaxPrintCode
            }
        }
    }
}
"""

# GraphQL query: list online orders
QUERY_ONLINE_ORDERS = """
query getOnlineOrders($startDate: String!, $endDate: String!, $pageNumber: Int, $pageSize: Int, $warehouseNumber: String!) {
    getOnlineOrders(startDate: $startDate, endDate: $endDate, pageNumber: $pageNumber, pageSize: $pageSize, warehouseNumber: $warehouseNumber) {
        pageNumber
        pageSize
        totalNumberOfRecords
        bcOrders {
            orderHeaderId
            orderPlacedDate: orderedDate
            orderNumber: sourceOrderNumber
            orderTotal
            warehouseNumber
            status
            emailAddress
            orderCancelAllowed
            orderReturnAllowed
            orderLineItems {
                orderLineItemId
                itemId
                itemNumber
                itemDescription
                lineNumber
                status
                orderStatus
                isBuyAgainEligible
                shippingType
                shippingTimeFrame
                shipment {
                    shipmentId
                    shippedDate
                    trackingNumber
                    carrierName
                    estimatedArrivalDate
                    deliveredDate
                    status
                }
            }
        }
    }
}
"""


# GraphQL query: look up product details by item numbers
QUERY_PRODUCTS = """
query products($clientId: String!, $itemNumbers: [String], $locale: [String], $warehouseNumber: String!) {
    products(clientId: $clientId, itemNumbers: $itemNumbers, locale: $locale, warehouseNumber: $warehouseNumber) {
        catalogData {
            itemNumber
            description { shortDescription }
        }
    }
}
"""

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class CostcoAPI:
    """Client for Costco's ecom GraphQL API."""

    # Stable client identifier — must remain consistent across requests
    CLIENT_IDENTIFIER = "481b1aec-aa3b-454b-b81b-48187e28f205"

    def __init__(self, auth: CostcoAuth) -> None:
        self._auth = auth

    def _headers(self) -> dict[str, str]:
        token = self._auth.get_bearer_token()
        return {
            "Content-Type": "application/json-patch+json",
            "Accept": "*/*",
            "Origin": "https://www.costco.com",
            "Referer": "https://www.costco.com/",
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "costco-x-authorization": f"Bearer {token}",
            "costco-x-wcs-clientid": WCS_CLIENT_ID,
            "client-identifier": self.CLIENT_IDENTIFIER,
            "costco.env": "ecom",
            "costco.service": "restOrders",
        }

    def _post(self, query: str, variables: dict[str, Any]) -> dict:
        """Execute a GraphQL query, retrying once on auth failure."""
        for attempt in range(2):
            resp = curl_requests.post(
                GRAPHQL_ENDPOINT,
                json={"query": query, "variables": variables},
                headers=self._headers(),
                impersonate=_IMPERSONATE,
                timeout=30,
            )
            if resp.status_code == 401 and attempt == 0:
                logger.info("Got 401, forcing token refresh...")
                self._auth._id_token = None  # force refresh
                continue
            resp.raise_for_status()
            return resp.json()

        raise RuntimeError("Authentication failed after retry")

    def list_receipts(
        self,
        start_date: str,
        end_date: str,
        document_type: str = "all",
        document_sub_type: str = "all",
    ) -> dict:
        """List warehouse receipts for a date range.

        Args:
            start_date: Start date as M/DD/YYYY (e.g. "1/01/2026")
            end_date: End date as M/DD/YYYY (e.g. "3/31/2026")
            document_type: Filter type ("all", "warehouse", "gas", "carwash")
            document_sub_type: Sub-filter ("all")
        """
        return self._post(QUERY_LIST_RECEIPTS, {
            "startDate": start_date,
            "endDate": end_date,
            "documentType": document_type,
            "documentSubType": document_sub_type,
        })

    def get_receipt_detail(self, barcode: str) -> dict:
        """Get full itemized receipt by transaction barcode.

        Also caches department numbers for each item in the local product DB.

        Args:
            barcode: Transaction barcode from list_receipts (e.g. "21126700703332602262040")
        """
        result = self._post(QUERY_RECEIPT_DETAIL, {
            "barcode": barcode,
            "documentType": "warehouse",
        })

        # Cache department numbers from receipt items
        from costco_mcp_server.product_cache import store_departments
        receipts = result.get("data", {}).get("receiptsWithCounts", {}).get("receipts", [])
        if receipts:
            depts = {}
            for item in receipts[0].get("itemArray", []):
                num = item.get("itemNumber", "")
                dept = item.get("itemDepartmentNumber")
                if num and dept is not None and dept > 0:
                    depts[num] = dept
            if depts:
                store_departments(depts)

        return result

    def list_online_orders(
        self,
        start_date: str,
        end_date: str,
        warehouse_number: str = "847",
        page_number: int = 1,
        page_size: int = 25,
    ) -> dict:
        """List online orders for a date range.

        Args:
            start_date: Start date as YYYY-M-DD (e.g. "2026-1-01")
            end_date: End date as YYYY-M-DD (e.g. "2026-3-31")
            warehouse_number: Warehouse number (default "847")
            page_number: Page number (default 1)
            page_size: Results per page (default 25)
        """
        return self._post(QUERY_ONLINE_ORDERS, {
            "startDate": start_date,
            "endDate": end_date,
            "warehouseNumber": warehouse_number,
            "pageNumber": page_number,
            "pageSize": page_size,
        })

    def lookup_products(
        self,
        item_numbers: list[str],
        warehouse_number: str = "847",
    ) -> dict[str, str]:
        """Look up full product names by Costco item numbers.

        Uses a local SQLite cache (~/.costco-mcp/products.db) to avoid
        repeated API calls for the same items.

        Args:
            item_numbers: List of item numbers (e.g. ["70476", "5887"])
            warehouse_number: Warehouse number (default "847")

        Returns:
            Dict mapping item number -> short description
        """
        # Check cache first (skip empty cached values)
        cached = {k: v for k, v in get_cached_names(item_numbers).items() if v}
        uncached = [n for n in item_numbers if n not in cached]

        if not uncached:
            logger.info("All %d products found in cache", len(cached))
            return cached

        logger.info("%d cached, %d to look up via API", len(cached), len(uncached))

        # Fetch uncached from API in batches of 20
        api_results: dict[str, str] = {}
        for i in range(0, len(uncached), 20):
            batch = uncached[i:i + 20]
            variables = {
                "itemNumbers": batch,
                "clientId": WCS_CLIENT_ID,
                "locale": ["en-US"],
                "warehouseNumber": warehouse_number,
            }
            for attempt in range(2):
                resp = curl_requests.post(
                    PRODUCT_GRAPHQL_ENDPOINT,
                    json={"query": QUERY_PRODUCTS, "variables": variables},
                    headers=self._headers(),
                    impersonate=_IMPERSONATE,
                    timeout=30,
                )
                if resp.status_code == 401 and attempt == 0:
                    self._auth._id_token = None
                    continue
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("data", {}).get("products", {}).get("catalogData", []):
                    desc = item.get("description", {}).get("shortDescription", "")
                    if desc:  # don't cache empty descriptions
                        api_results[item["itemNumber"]] = desc
                break

        # Store new results in cache
        if api_results:
            store_names(api_results)

        # Merge cached + API results
        return {**cached, **api_results}
