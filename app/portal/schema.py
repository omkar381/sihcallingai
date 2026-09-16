"""Tables behind the farmer portal."""

from __future__ import annotations

import threading

from app.services.db import DB_LOCK, get_connection

_initialised_paths: set = set()
_init_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_farmers (
    farmer_id            TEXT PRIMARY KEY,
    login_id             TEXT NOT NULL UNIQUE,
    mobile               TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    failed_logins        INTEGER NOT NULL DEFAULT 0,
    locked_until         INTEGER,
    full_name            TEXT NOT NULL,
    guardian_name        TEXT,
    date_of_birth        TEXT,
    gender               TEXT,
    alternate_mobile     TEXT,
    email                TEXT,
    preferred_language   TEXT NOT NULL DEFAULT 'kn',
    address_line         TEXT,
    village              TEXT NOT NULL,
    taluk                TEXT,
    district             TEXT NOT NULL,
    state                TEXT NOT NULL,
    pincode              TEXT,
    declared_land_acres  REAL,
    photo_path           TEXT,
    photo_mime           TEXT,
    verification_status  TEXT NOT NULL DEFAULT 'PENDING',
    verified_at          INTEGER,
    verified_by          TEXT,
    card_serial          TEXT NOT NULL UNIQUE,
    registered_at        INTEGER NOT NULL,
    valid_until          INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    last_login_at        INTEGER
);

CREATE TABLE IF NOT EXISTS portal_sequences (
    scope TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_sessions (
    token_hash TEXT PRIMARY KEY,
    farmer_id  TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS portal_plots (
    id              TEXT PRIMARY KEY,
    farmer_id       TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    survey_number   TEXT,
    area_acres      REAL NOT NULL,
    ownership       TEXT NOT NULL DEFAULT 'OWNED',
    village         TEXT,
    taluk           TEXT,
    district        TEXT,
    latitude        REAL,
    longitude       REAL,
    soil_type       TEXT,
    irrigation_type TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_crops (
    id                    TEXT PRIMARY KEY,
    farmer_id             TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    plot_id               TEXT REFERENCES portal_plots(id) ON DELETE SET NULL,
    crop_name             TEXT NOT NULL,
    variety               TEXT,
    season                TEXT NOT NULL,
    sowing_date           TEXT NOT NULL,
    expected_harvest_date TEXT,
    area_acres            REAL NOT NULL,
    seed_requirement      TEXT,
    fertilizer_plan       TEXT,
    pesticide_plan        TEXT,
    irrigation_plan       TEXT,
    status                TEXT NOT NULL,
    notes                 TEXT,
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_harvests (
    id            TEXT PRIMARY KEY,
    crop_id       TEXT NOT NULL REFERENCES portal_crops(id) ON DELETE CASCADE,
    farmer_id     TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    harvest_date  TEXT NOT NULL,
    quantity      REAL NOT NULL,
    unit          TEXT NOT NULL,
    quality_grade TEXT,
    notes         TEXT,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_documents (
    id               TEXT PRIMARY KEY,
    farmer_id        TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    category         TEXT NOT NULL,
    title            TEXT NOT NULL,
    reference_number TEXT,
    original_name    TEXT NOT NULL,
    stored_path      TEXT NOT NULL,
    mime             TEXT NOT NULL,
    size_bytes       INTEGER NOT NULL,
    uploaded_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS portal_notifications (
    id         TEXT PRIMARY KEY,
    farmer_id  TEXT REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    category   TEXT NOT NULL,
    severity   TEXT NOT NULL DEFAULT 'INFO',
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    source     TEXT,
    is_sample  INTEGER NOT NULL DEFAULT 0,
    dedupe_key TEXT UNIQUE,
    link       TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER
);

CREATE TABLE IF NOT EXISTS portal_notification_reads (
    notification_id TEXT NOT NULL REFERENCES portal_notifications(id) ON DELETE CASCADE,
    farmer_id       TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    read_at         INTEGER NOT NULL,
    PRIMARY KEY (notification_id, farmer_id)
);

CREATE TABLE IF NOT EXISTS portal_activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_id  TEXT NOT NULL REFERENCES portal_farmers(farmer_id) ON DELETE CASCADE,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portal_sessions_farmer ON portal_sessions(farmer_id);
CREATE INDEX IF NOT EXISTS idx_portal_plots_farmer ON portal_plots(farmer_id);
CREATE INDEX IF NOT EXISTS idx_portal_crops_farmer ON portal_crops(farmer_id, status);
CREATE INDEX IF NOT EXISTS idx_portal_harvests_crop ON portal_harvests(crop_id);
CREATE INDEX IF NOT EXISTS idx_portal_documents_farmer ON portal_documents(farmer_id);
CREATE INDEX IF NOT EXISTS idx_portal_notifications_farmer ON portal_notifications(farmer_id, created_at);
CREATE INDEX IF NOT EXISTS idx_portal_activity_farmer ON portal_activity(farmer_id, created_at);
"""


def init_portal_tables() -> None:
    from app.services.db import DB_PATH

    with _init_lock:
        if DB_PATH in _initialised_paths:
            return
        with DB_LOCK:
            conn = get_connection()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
        _initialised_paths.add(DB_PATH)
