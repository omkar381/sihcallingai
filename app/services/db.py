"""SQLite helpers for AI Krishi extension modules."""

import os
import sqlite3
import threading

DB_PATH = os.path.join("data", "krishi.sqlite3")
DB_LOCK = threading.RLock()


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_extension_tables() -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY,
                    farmer_phone TEXT NOT NULL,
                    crop_name TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    base_price_per_unit REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('OPEN', 'CLOSED', 'SOLD')),
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    accepted_bid_id TEXT,
                    buyer_id TEXT,
                    sold_price_per_unit REAL,
                    sold_quantity REAL,
                    order_status TEXT,
                    FOREIGN KEY(farmer_phone) REFERENCES farmers(phone)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bids (
                    id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    bid_price_per_unit REAL NOT NULL,
                    quantity REAL NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(listing_id) REFERENCES listings(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS farmer_twin (
                    farmer_phone TEXT PRIMARY KEY,
                    crops_grown TEXT NOT NULL,
                    land_size REAL,
                    soil_type TEXT,
                    water_source TEXT,
                    avg_yield REAL,
                    preferred_crops TEXT,
                    risk_score REAL,
                    last_updated INTEGER NOT NULL,
                    FOREIGN KEY(farmer_phone) REFERENCES farmers(phone)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_status_expires ON listings(status, expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_farmer_created ON listings(farmer_phone, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bids_listing_price ON bids(listing_id, bid_price_per_unit DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bids_listing_created ON bids(listing_id, created_at DESC)")
            conn.commit()
        finally:
            conn.close()
