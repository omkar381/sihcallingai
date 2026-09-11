"""
Transaction infrastructure schema.

Covers the farm-to-payment lifecycle: verified buyers, their structured
demand, FPOs and aggregated lots, offers, logistics, payments and disputes,
plus an append-only audit trail over all of it.

Two conventions hold throughout:

  * Money is stored in paise (integer), never as a float. Floating-point
    rupees accumulate rounding error across a settlement chain, and a
    payment system that cannot reconcile to the paisa is not a payment
    system.
  * Nothing economically meaningful is ever hard-deleted. State moves
    forward through a status column and every transition writes an audit
    event, so a government evaluator can reconstruct what happened.
"""

from __future__ import annotations

import logging
import time
from typing import Tuple

from app.services.db import DB_LOCK, get_connection

logger = logging.getLogger(__name__)


TRADE_DDL: Tuple[str, ...] = (
    # ---------------------------------------------------------------- audit
    # Append-only. No UPDATE or DELETE is ever issued against this table.
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id             TEXT PRIMARY KEY,
        sequence       INTEGER,
        entity_type    TEXT NOT NULL,
        entity_id      TEXT NOT NULL,
        action         TEXT NOT NULL,
        actor          TEXT,
        actor_role     TEXT,
        old_value      TEXT,
        new_value      TEXT,
        metadata       TEXT,
        request_id     TEXT,
        created_at     INTEGER NOT NULL
    )
    """,

    # --------------------------------------------------------------- buyers
    """
    CREATE TABLE IF NOT EXISTS buyers (
        id                    TEXT PRIMARY KEY,
        name                  TEXT NOT NULL,
        buyer_type            TEXT NOT NULL,
        phone                 TEXT,
        email                 TEXT,
        gstin                 TEXT,
        pan                   TEXT,
        apmc_licence_no       TEXT,
        address               TEXT,
        district              TEXT,
        state                 TEXT,
        service_districts     TEXT,
        verification_status   TEXT NOT NULL DEFAULT 'UNVERIFIED',
        verified_at           INTEGER,
        verified_by           TEXT,
        rejection_reason      TEXT,
        total_transactions    INTEGER NOT NULL DEFAULT 0,
        completed_transactions INTEGER NOT NULL DEFAULT 0,
        payment_default_count INTEGER NOT NULL DEFAULT 0,
        dispute_count         INTEGER NOT NULL DEFAULT 0,
        total_payment_days    REAL NOT NULL DEFAULT 0,
        payment_samples       INTEGER NOT NULL DEFAULT 0,
        is_active             INTEGER NOT NULL DEFAULT 1,
        created_at            INTEGER NOT NULL,
        updated_at            INTEGER NOT NULL
    )
    """,

    # Evidence supporting a verification decision. Kept separately so a
    # verification can be re-reviewed without losing what it was based on.
    """
    CREATE TABLE IF NOT EXISTS buyer_documents (
        id             TEXT PRIMARY KEY,
        buyer_id       TEXT NOT NULL,
        doc_type       TEXT NOT NULL,
        doc_number     TEXT,
        file_ref       TEXT,
        status         TEXT NOT NULL DEFAULT 'SUBMITTED',
        reviewed_by    TEXT,
        reviewed_at    INTEGER,
        notes          TEXT,
        created_at     INTEGER NOT NULL,
        FOREIGN KEY(buyer_id) REFERENCES buyers(id) ON DELETE CASCADE
    )
    """,

    # What buyers actually want to buy, with the quality they require.
    """
    CREATE TABLE IF NOT EXISTS buyer_demand (
        id                 TEXT PRIMARY KEY,
        buyer_id           TEXT NOT NULL,
        commodity          TEXT NOT NULL,
        variety            TEXT,
        min_grade          TEXT,
        quantity_needed_kg REAL NOT NULL,
        quantity_filled_kg REAL NOT NULL DEFAULT 0,
        min_lot_kg         REAL,
        price_offered      REAL,
        price_unit         TEXT NOT NULL DEFAULT 'INR/quintal',
        delivery_district  TEXT,
        delivery_state     TEXT,
        max_distance_km    REAL,
        window_start       INTEGER,
        window_end         INTEGER,
        max_moisture_pct   REAL,
        quality_spec       TEXT,
        payment_terms_days INTEGER NOT NULL DEFAULT 7,
        status             TEXT NOT NULL DEFAULT 'OPEN',
        created_at         INTEGER NOT NULL,
        updated_at         INTEGER NOT NULL,
        FOREIGN KEY(buyer_id) REFERENCES buyers(id) ON DELETE CASCADE
    )
    """,

    # ----------------------------------------------------------------- FPOs
    """
    CREATE TABLE IF NOT EXISTS fpos (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        registration_no     TEXT,
        district            TEXT,
        state               TEXT,
        contact_phone       TEXT,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        verified_at         INTEGER,
        is_active           INTEGER NOT NULL DEFAULT 1,
        created_at          INTEGER NOT NULL,
        updated_at          INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fpo_members (
        fpo_id        TEXT NOT NULL,
        farmer_phone  TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'MEMBER',
        joined_at     INTEGER NOT NULL,
        is_active     INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(fpo_id, farmer_phone),
        FOREIGN KEY(fpo_id) REFERENCES fpos(id) ON DELETE CASCADE
    )
    """,

    # ----------------------------------------------------------------- lots
    # A lot is one sellable consignment. It may belong to a single farmer or
    # be an aggregate assembled by an FPO from several member lots.
    """
    CREATE TABLE IF NOT EXISTS lots (
        id                  TEXT PRIMARY KEY,
        farmer_phone        TEXT,
        fpo_id              TEXT,
        parent_lot_id       TEXT,
        is_aggregate        INTEGER NOT NULL DEFAULT 0,
        commodity           TEXT NOT NULL,
        variety             TEXT,
        grade               TEXT,
        grade_basis         TEXT NOT NULL DEFAULT 'SELF_DECLARED',
        moisture_pct        REAL,
        damage_pct          REAL,
        foreign_matter_pct  REAL,
        quality_notes       TEXT,
        photo_refs          TEXT,
        quantity_kg         REAL NOT NULL,
        expected_price      REAL,
        price_unit          TEXT NOT NULL DEFAULT 'INR/quintal',
        harvest_date        TEXT,
        available_from      INTEGER,
        available_until     INTEGER,
        pickup_village      TEXT,
        pickup_district     TEXT,
        pickup_state        TEXT,
        storage_type        TEXT,
        status              TEXT NOT NULL DEFAULT 'DRAFT',
        accepted_offer_id   TEXT,
        created_at          INTEGER NOT NULL,
        updated_at          INTEGER NOT NULL,
        FOREIGN KEY(fpo_id) REFERENCES fpos(id),
        FOREIGN KEY(parent_lot_id) REFERENCES lots(id)
    )
    """,

    # --------------------------------------------------------------- offers
    """
    CREATE TABLE IF NOT EXISTS offers (
        id                 TEXT PRIMARY KEY,
        lot_id             TEXT NOT NULL,
        buyer_id           TEXT NOT NULL,
        demand_id          TEXT,
        price_per_quintal  REAL NOT NULL,
        quantity_kg        REAL NOT NULL,
        payment_terms_days INTEGER NOT NULL DEFAULT 7,
        pickup_by          INTEGER,
        conditions         TEXT,
        status             TEXT NOT NULL DEFAULT 'OPEN',
        created_at         INTEGER NOT NULL,
        responded_at       INTEGER,
        expires_at         INTEGER,
        FOREIGN KEY(lot_id) REFERENCES lots(id) ON DELETE CASCADE,
        FOREIGN KEY(buyer_id) REFERENCES buyers(id)
    )
    """,

    # ------------------------------------------------------------ logistics
    """
    CREATE TABLE IF NOT EXISTS transport_providers (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        phone               TEXT,
        vehicle_type        TEXT NOT NULL,
        capacity_kg         REAL NOT NULL,
        rate_per_km         REAL NOT NULL,
        minimum_fare        REAL NOT NULL DEFAULT 0,
        service_districts   TEXT,
        base_district       TEXT,
        base_state          TEXT,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        rating              REAL,
        completed_trips     INTEGER NOT NULL DEFAULT 0,
        is_active           INTEGER NOT NULL DEFAULT 1,
        created_at          INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transport_bookings (
        id             TEXT PRIMARY KEY,
        lot_id         TEXT NOT NULL,
        provider_id    TEXT,
        offer_id       TEXT,
        pickup_place   TEXT,
        drop_place     TEXT,
        distance_km    REAL,
        quantity_kg    REAL,
        quoted_cost    REAL,
        status         TEXT NOT NULL DEFAULT 'REQUESTED',
        scheduled_for  INTEGER,
        completed_at   INTEGER,
        created_at     INTEGER NOT NULL,
        FOREIGN KEY(lot_id) REFERENCES lots(id),
        FOREIGN KEY(provider_id) REFERENCES transport_providers(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS storage_providers (
        id                  TEXT PRIMARY KEY,
        name                TEXT NOT NULL,
        phone               TEXT,
        storage_type        TEXT NOT NULL,
        capacity_kg         REAL NOT NULL,
        available_kg        REAL NOT NULL,
        commodities         TEXT,
        daily_cost_per_qtl  REAL NOT NULL,
        district            TEXT,
        state               TEXT,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
        is_active           INTEGER NOT NULL DEFAULT 1,
        created_at          INTEGER NOT NULL
    )
    """,

    # ------------------------------------------------------------- payments
    # amount_paise is an INTEGER. See the module docstring.
    """
    CREATE TABLE IF NOT EXISTS payments (
        id              TEXT PRIMARY KEY,
        lot_id          TEXT NOT NULL,
        offer_id        TEXT,
        buyer_id        TEXT NOT NULL,
        payee_phone     TEXT,
        payee_fpo_id    TEXT,
        amount_paise    INTEGER NOT NULL,
        status          TEXT NOT NULL DEFAULT 'PENDING',
        method          TEXT,
        utr_ref         TEXT,
        due_date        INTEGER,
        initiated_at    INTEGER,
        escrowed_at     INTEGER,
        released_at     INTEGER,
        failed_reason   TEXT,
        created_at      INTEGER NOT NULL,
        updated_at      INTEGER NOT NULL,
        FOREIGN KEY(lot_id) REFERENCES lots(id),
        FOREIGN KEY(buyer_id) REFERENCES buyers(id)
    )
    """,
    # Every status change, so a settlement can be reconstructed exactly.
    """
    CREATE TABLE IF NOT EXISTS payment_events (
        id           TEXT PRIMARY KEY,
        payment_id   TEXT NOT NULL,
        from_status  TEXT,
        to_status    TEXT NOT NULL,
        actor        TEXT,
        note         TEXT,
        created_at   INTEGER NOT NULL,
        FOREIGN KEY(payment_id) REFERENCES payments(id) ON DELETE CASCADE
    )
    """,

    # ------------------------------------------------------------- disputes
    """
    CREATE TABLE IF NOT EXISTS disputes (
        id              TEXT PRIMARY KEY,
        lot_id          TEXT NOT NULL,
        offer_id        TEXT,
        payment_id      TEXT,
        raised_by       TEXT NOT NULL,
        raised_by_role  TEXT NOT NULL,
        against_party   TEXT,
        category        TEXT NOT NULL,
        description     TEXT NOT NULL,
        evidence_refs   TEXT,
        claim_paise     INTEGER,
        status          TEXT NOT NULL DEFAULT 'OPEN',
        resolution      TEXT,
        resolved_by     TEXT,
        resolved_at     INTEGER,
        created_at      INTEGER NOT NULL,
        updated_at      INTEGER NOT NULL,
        FOREIGN KEY(lot_id) REFERENCES lots(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispute_events (
        id           TEXT PRIMARY KEY,
        dispute_id   TEXT NOT NULL,
        from_status  TEXT,
        to_status    TEXT NOT NULL,
        actor        TEXT,
        note         TEXT,
        created_at   INTEGER NOT NULL,
        FOREIGN KEY(dispute_id) REFERENCES disputes(id) ON DELETE CASCADE
    )
    """,
)


TRADE_INDEXES: Tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_buyers_status ON buyers(verification_status, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_buyer_docs ON buyer_documents(buyer_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_demand_lookup ON buyer_demand(commodity, status, delivery_district)",
    "CREATE INDEX IF NOT EXISTS idx_demand_buyer ON buyer_demand(buyer_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_fpo_members_farmer ON fpo_members(farmer_phone, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_lots_status ON lots(status, commodity)",
    "CREATE INDEX IF NOT EXISTS idx_lots_farmer ON lots(farmer_phone, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lots_fpo ON lots(fpo_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_lots_parent ON lots(parent_lot_id)",
    "CREATE INDEX IF NOT EXISTS idx_offers_lot ON offers(lot_id, price_per_quintal DESC)",
    "CREATE INDEX IF NOT EXISTS idx_offers_buyer ON offers(buyer_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_payments_lot ON payments(lot_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status, due_date)",
    "CREATE INDEX IF NOT EXISTS idx_payment_events ON payment_events(payment_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_disputes_lot ON disputes(lot_id)",
    "CREATE INDEX IF NOT EXISTS idx_transport_service ON transport_providers(base_district, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_storage_district ON storage_providers(district, is_active)",
)


def init_trade_tables() -> None:
    """Create all transaction tables and indexes. Idempotent."""
    with DB_LOCK:
        conn = get_connection()
        try:
            for statement in TRADE_DDL:
                conn.execute(statement)
            for statement in TRADE_INDEXES:
                conn.execute(statement)
            conn.commit()
            logger.info("Transaction infrastructure tables ready")
        finally:
            conn.close()


def now_ts() -> int:
    return int(time.time())
