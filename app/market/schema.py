"""
Market intelligence schema and market registry seed.

Tables live in the same SQLite database as the existing auction/farmer tables
so a lot, its price context and its buyer share one transactional store. All
DDL is idempotent; migration to PostgreSQL means swapping the connection
factory, not rewriting these statements.
"""

from __future__ import annotations

import logging
from typing import Tuple

from app.services.db import DB_LOCK, get_connection

logger = logging.getLogger(__name__)


MARKET_DDL: Tuple[str, ...] = (
    # ---- Market registry -------------------------------------------------
    # One row per physical APMC / market yard. Coordinates drive the
    # nearby-market search and the transport cost model.
    """
    CREATE TABLE IF NOT EXISTS markets (
        id                TEXT PRIMARY KEY,
        name              TEXT NOT NULL,
        district          TEXT NOT NULL,
        state             TEXT NOT NULL,
        latitude          REAL,
        longitude         REAL,
        market_type       TEXT NOT NULL DEFAULT 'APMC',
        is_active         INTEGER NOT NULL DEFAULT 1,
        created_at        INTEGER NOT NULL,
        UNIQUE(name, district, state)
    )
    """,

    # ---- Price history ---------------------------------------------------
    # The UNIQUE constraint is the idempotency guarantee for ingestion: the
    # same arrival day can be re-fetched any number of times without
    # duplicating the series.
    """
    CREATE TABLE IF NOT EXISTS mandi_price_history (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id         TEXT,
        state             TEXT NOT NULL,
        district          TEXT NOT NULL,
        market            TEXT NOT NULL,
        commodity         TEXT NOT NULL,
        variety           TEXT,
        grade             TEXT,
        min_price         REAL,
        max_price         REAL,
        modal_price       REAL NOT NULL,
        price_unit        TEXT NOT NULL DEFAULT 'INR/quintal',
        arrival_qty       REAL,
        arrival_unit      TEXT,
        arrival_date      TEXT NOT NULL,
        fetched_at        INTEGER NOT NULL,
        source            TEXT NOT NULL,
        data_quality      TEXT NOT NULL,
        ingestion_run_id  TEXT,
        UNIQUE(market, commodity, variety, grade, arrival_date),
        FOREIGN KEY(market_id) REFERENCES markets(id)
    )
    """,

    # ---- Ingestion audit -------------------------------------------------
    # Every fetch leaves a record whether it succeeded or not. A source that
    # is failing must be visible, never papered over with fixture data.
    """
    CREATE TABLE IF NOT EXISTS market_ingestion_runs (
        id                TEXT PRIMARY KEY,
        source            TEXT NOT NULL,
        scope             TEXT,
        status            TEXT NOT NULL,
        started_at        INTEGER NOT NULL,
        completed_at      INTEGER,
        records_fetched   INTEGER NOT NULL DEFAULT 0,
        records_accepted  INTEGER NOT NULL DEFAULT 0,
        records_rejected  INTEGER NOT NULL DEFAULT 0,
        records_duplicate INTEGER NOT NULL DEFAULT 0,
        error             TEXT,
        rejection_summary TEXT
    )
    """,
)

MARKET_INDEXES: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_mph_lookup ON mandi_price_history(commodity, district, arrival_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_mph_market_commodity ON mandi_price_history(market, commodity, arrival_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_mph_state_commodity ON mandi_price_history(state, commodity, arrival_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_mph_arrival_date ON mandi_price_history(arrival_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_markets_district ON markets(state, district)",
    "CREATE INDEX IF NOT EXISTS idx_ingestion_source ON market_ingestion_runs(source, started_at DESC)",
)


# ---------------------------------------------------------------------------
# Seed market registry
# ---------------------------------------------------------------------------
# (name, district, state, latitude, longitude)
# Coordinates are the market town centroid, which is accurate enough for
# road-distance estimation at the 10-200 km range that matters here.
# Karnataka is seeded around Kalaburagi (the existing farmer base) and
# Maharashtra around the Nashik/Solapur onion belt.

SEED_MARKETS: Tuple[Tuple[str, str, str, float, float], ...] = (
    # --- Karnataka: Kalyana-Karnataka region ---
    ("Kalaburagi", "Kalaburagi", "Karnataka", 17.3297, 76.8343),
    ("Sedam", "Kalaburagi", "Karnataka", 17.1833, 77.2833),
    ("Chittapur", "Kalaburagi", "Karnataka", 17.1200, 77.0800),
    ("Aland", "Kalaburagi", "Karnataka", 17.5637, 76.5679),
    ("Afzalpur", "Kalaburagi", "Karnataka", 17.1989, 76.3609),
    ("Jevargi", "Kalaburagi", "Karnataka", 17.0150, 76.7736),
    ("Yadgir", "Yadgir", "Karnataka", 16.7700, 77.1376),
    ("Shahapur", "Yadgir", "Karnataka", 16.6976, 76.8400),
    ("Surapura", "Yadgir", "Karnataka", 16.5150, 76.7550),
    ("Bidar", "Bidar", "Karnataka", 17.9104, 77.5199),
    ("Basavakalyan", "Bidar", "Karnataka", 17.8725, 76.9497),
    ("Humnabad", "Bidar", "Karnataka", 17.7700, 77.1300),
    ("Bhalki", "Bidar", "Karnataka", 18.0433, 77.2064),
    ("Raichur", "Raichur", "Karnataka", 16.2076, 77.3463),
    ("Sindhanur", "Raichur", "Karnataka", 15.7690, 76.7570),
    ("Manvi", "Raichur", "Karnataka", 15.9930, 77.0490),
    ("Koppal", "Koppal", "Karnataka", 15.3547, 76.1546),
    ("Gangavathi", "Koppal", "Karnataka", 15.4310, 76.5290),
    ("Ballari", "Ballari", "Karnataka", 15.1394, 76.9214),
    ("Vijayapura", "Vijayapura", "Karnataka", 16.8302, 75.7100),
    ("Bagalkot", "Bagalkot", "Karnataka", 16.1725, 75.6560),
    # --- Karnataka: south and coastal-adjacent ---
    ("Bengaluru", "Bengaluru Urban", "Karnataka", 12.9716, 77.5946),
    ("Mysuru", "Mysuru", "Karnataka", 12.2958, 76.6394),
    ("Hubballi", "Dharwad", "Karnataka", 15.3647, 75.1240),
    ("Belagavi", "Belagavi", "Karnataka", 15.8497, 74.4977),
    ("Davangere", "Davangere", "Karnataka", 14.4644, 75.9218),
    ("Shivamogga", "Shivamogga", "Karnataka", 13.9299, 75.5681),
    ("Tumakuru", "Tumakuru", "Karnataka", 13.3379, 77.1173),
    ("Hassan", "Hassan", "Karnataka", 13.0072, 76.0962),
    ("Mandya", "Mandya", "Karnataka", 12.5218, 76.8951),
    ("Chitradurga", "Chitradurga", "Karnataka", 14.2251, 76.3980),
    ("Gadag", "Gadag", "Karnataka", 15.4315, 75.6355),
    ("Haveri", "Haveri", "Karnataka", 14.7951, 75.4040),

    # --- Maharashtra: onion belt and major yards ---
    ("Lasalgaon", "Nashik", "Maharashtra", 20.1436, 74.2381),
    ("Pimpalgaon Baswant", "Nashik", "Maharashtra", 20.1731, 73.9903),
    ("Nashik", "Nashik", "Maharashtra", 19.9975, 73.7898),
    ("Yeola", "Nashik", "Maharashtra", 20.0423, 74.4894),
    ("Pune", "Pune", "Maharashtra", 18.5204, 73.8567),
    ("Solapur", "Solapur", "Maharashtra", 17.6599, 75.9064),
    ("Ahmednagar", "Ahmednagar", "Maharashtra", 19.0948, 74.7480),
    ("Chhatrapati Sambhajinagar", "Chhatrapati Sambhajinagar", "Maharashtra", 19.8762, 75.3433),
    ("Latur", "Latur", "Maharashtra", 18.4088, 76.5604),
    ("Nagpur", "Nagpur", "Maharashtra", 21.1458, 79.0882),
    ("Amravati", "Amravati", "Maharashtra", 20.9320, 77.7523),
    ("Akola", "Akola", "Maharashtra", 20.7002, 77.0082),
    ("Jalgaon", "Jalgaon", "Maharashtra", 21.0077, 75.5626),
    ("Kolhapur", "Kolhapur", "Maharashtra", 16.7050, 74.2433),
    ("Sangli", "Sangli", "Maharashtra", 16.8524, 74.5815),
    ("Satara", "Satara", "Maharashtra", 17.6805, 74.0183),
    ("Beed", "Beed", "Maharashtra", 18.9891, 75.7601),
    ("Nanded", "Nanded", "Maharashtra", 19.1383, 77.3210),
    ("Dharashiv", "Dharashiv", "Maharashtra", 18.1860, 76.0419),
    ("Parbhani", "Parbhani", "Maharashtra", 19.2704, 76.7601),
    ("Jalna", "Jalna", "Maharashtra", 19.8410, 75.8864),
    ("Yavatmal", "Yavatmal", "Maharashtra", 20.3888, 78.1204),
    ("Dhule", "Dhule", "Maharashtra", 20.9042, 74.7749),
    ("Mumbai", "Mumbai", "Maharashtra", 19.0760, 72.8777),
)


def _market_id(name: str, district: str, state: str) -> str:
    parts = [p.strip().lower().replace(" ", "-") for p in (state, district, name)]
    return ":".join(parts)


def init_market_tables(seed: bool = True) -> None:
    """Create market tables and indexes, and seed the market registry."""
    import time

    with DB_LOCK:
        conn = get_connection()
        try:
            for statement in MARKET_DDL:
                conn.execute(statement)
            for statement in MARKET_INDEXES:
                conn.execute(statement)

            if seed:
                now = int(time.time())
                rows = [
                    (_market_id(n, d, s), n, d, s, lat, lon, "APMC", 1, now)
                    for (n, d, s, lat, lon) in SEED_MARKETS
                ]
                conn.executemany(
                    """
                    INSERT INTO markets(id, name, district, state, latitude, longitude,
                                        market_type, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name, district, state) DO UPDATE SET
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        is_active = excluded.is_active
                    """,
                    rows,
                )

            conn.commit()
            logger.info("Market intelligence tables ready (%d markets seeded)", len(SEED_MARKETS))
        finally:
            conn.close()
