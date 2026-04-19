"""SQLite cache for Costco product names and departments."""

import logging
import sqlite3
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".costco-mcp"
CACHE_DB = CACHE_DIR / "products.db"


class ProductInfo(NamedTuple):
    short_description: str
    department: int | None


def _get_conn() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            item_number TEXT PRIMARY KEY,
            short_description TEXT NOT NULL,
            department INTEGER
        )
    """)
    return conn


def get_cached_names(item_numbers: list[str]) -> dict[str, str]:
    """Look up item numbers in the cache. Returns {item_number: description} for hits."""
    if not item_numbers:
        return {}
    conn = _get_conn()
    placeholders = ",".join("?" for _ in item_numbers)
    rows = conn.execute(
        f"SELECT item_number, short_description FROM products WHERE item_number IN ({placeholders})",
        item_numbers,
    ).fetchall()
    conn.close()
    return dict(rows)


def get_cached_products(item_numbers: list[str]) -> dict[str, ProductInfo]:
    """Look up full product info. Returns {item_number: ProductInfo} for hits."""
    if not item_numbers:
        return {}
    conn = _get_conn()
    placeholders = ",".join("?" for _ in item_numbers)
    rows = conn.execute(
        f"SELECT item_number, short_description, department FROM products WHERE item_number IN ({placeholders})",
        item_numbers,
    ).fetchall()
    conn.close()
    return {row[0]: ProductInfo(short_description=row[1], department=row[2]) for row in rows}


def store_names(products: dict[str, str]) -> None:
    """Store product names in the cache (preserves existing department if set)."""
    if not products:
        return
    conn = _get_conn()
    for item_number, desc in products.items():
        conn.execute(
            """INSERT INTO products (item_number, short_description)
               VALUES (?, ?)
               ON CONFLICT(item_number) DO UPDATE SET short_description = excluded.short_description""",
            (item_number, desc),
        )
    conn.commit()
    conn.close()
    logger.info("Cached %d product names", len(products))


def store_departments(departments: dict[str, int]) -> None:
    """Update department numbers for cached products."""
    if not departments:
        return
    conn = _get_conn()
    for item_number, dept in departments.items():
        conn.execute(
            """INSERT INTO products (item_number, short_description, department)
               VALUES (?, '', ?)
               ON CONFLICT(item_number) DO UPDATE SET department = excluded.department""",
            (item_number, dept),
        )
    conn.commit()
    conn.close()
    logger.info("Cached %d department numbers", len(departments))


def stats() -> dict:
    """Return cache stats."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    with_dept = conn.execute("SELECT COUNT(*) FROM products WHERE department IS NOT NULL").fetchone()[0]
    conn.close()
    return {"cache_file": str(CACHE_DB), "total_products": count, "with_department": with_dept}
