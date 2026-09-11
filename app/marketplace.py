"""Marketplace module — CRUD for farmer crop listings with SQLite storage."""

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_db_initialized = False
_DB_PATH = os.path.join("data", "krishi.sqlite3")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_marketplace_db() -> None:
    global _db_initialized
    if _db_initialized:
        return
    with _lock:
        if _db_initialized:
            return
        conn = _connect()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS marketplace_listings (
                    id TEXT PRIMARY KEY,
                    seller_phone TEXT NOT NULL,
                    seller_name TEXT NOT NULL DEFAULT 'Farmer',
                    crop TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    unit TEXT NOT NULL DEFAULT 'quintal',
                    price_per_unit REAL NOT NULL,
                    description TEXT DEFAULT '',
                    location TEXT NOT NULL DEFAULT 'Kalaburgi',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS marketplace_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id TEXT NOT NULL,
                    buyer_phone TEXT NOT NULL,
                    buyer_name TEXT DEFAULT 'Buyer',
                    message TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(listing_id) REFERENCES marketplace_listings(id)
                )
            """)
            conn.commit()
            _db_initialized = True
        finally:
            conn.close()


def _now() -> int:
    return int(time.time())


def _row_to_dict(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row)


def create_listing(
    seller_phone: str,
    seller_name: str,
    crop: str,
    quantity: float,
    unit: str,
    price_per_unit: float,
    description: str = "",
    location: str = "Kalaburgi",
) -> Dict[str, Any]:
    _init_marketplace_db()
    listing_id = f"LST-{uuid.uuid4().hex[:8].upper()}"
    now = _now()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO marketplace_listings
                   (id, seller_phone, seller_name, crop, quantity, unit, price_per_unit, description, location, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (listing_id, seller_phone, seller_name or "Farmer", crop, quantity, unit, price_per_unit, description, location, now, now),
            )
            conn.commit()
            return _row_to_dict(conn.execute("SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,)).fetchone())
        finally:
            conn.close()


def get_listings(
    crop_filter: str = "",
    location_filter: str = "",
    status: str = "active",
    page: int = 1,
    limit: int = 20,
) -> Dict[str, Any]:
    _init_marketplace_db()
    offset = (max(1, page) - 1) * limit
    conditions = []
    params: list = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if crop_filter:
        conditions.append("LOWER(crop) LIKE ?")
        params.append(f"%{crop_filter.lower()}%")
    if location_filter:
        conditions.append("LOWER(location) LIKE ?")
        params.append(f"%{location_filter.lower()}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = _connect()
    try:
        total = conn.execute(f"SELECT COUNT(*) as cnt FROM marketplace_listings {where}", params).fetchone()["cnt"]
        rows = conn.execute(
            f"SELECT * FROM marketplace_listings {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "page": page, "limit": limit, "listings": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


def get_listing(listing_id: str) -> Optional[Dict[str, Any]]:
    _init_marketplace_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_listing(listing_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    _init_marketplace_db()
    allowed = {"crop", "quantity", "unit", "price_per_unit", "description", "location", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_listing(listing_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [_now(), listing_id]

    with _lock:
        conn = _connect()
        try:
            conn.execute(f"UPDATE marketplace_listings SET {set_clause}, updated_at = ? WHERE id = ?", values)
            conn.commit()
            return _row_to_dict(conn.execute("SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,)).fetchone())
        finally:
            conn.close()


def delete_listing(listing_id: str) -> bool:
    _init_marketplace_db()
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute("DELETE FROM marketplace_listings WHERE id = ?", (listing_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def contact_seller(listing_id: str, buyer_phone: str, buyer_name: str = "Buyer", message: str = "") -> Dict[str, Any]:
    _init_marketplace_db()
    with _lock:
        conn = _connect()
        try:
            listing = conn.execute("SELECT * FROM marketplace_listings WHERE id = ?", (listing_id,)).fetchone()
            if not listing:
                return {"success": False, "reason": "listing_not_found"}
            conn.execute(
                "INSERT INTO marketplace_contacts (listing_id, buyer_phone, buyer_name, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (listing_id, buyer_phone, buyer_name, message, _now()),
            )
            conn.commit()
            return {
                "success": True,
                "listing_id": listing_id,
                "seller_phone": listing["seller_phone"],
                "seller_name": listing["seller_name"],
                "message": "Contact request sent. Seller will be notified.",
            }
        finally:
            conn.close()


def get_marketplace_stats() -> Dict[str, Any]:
    """Get marketplace summary stats for admin dashboard."""
    _init_marketplace_db()
    conn = _connect()
    try:
        active = conn.execute("SELECT COUNT(*) as c FROM marketplace_listings WHERE status = 'active'").fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) as c FROM marketplace_listings").fetchone()["c"]
        contacts = conn.execute("SELECT COUNT(*) as c FROM marketplace_contacts").fetchone()["c"]
        return {"active_listings": active, "total_listings": total, "total_contacts": contacts}
    finally:
        conn.close()
