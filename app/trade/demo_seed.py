"""
Demonstration marketplace data.

A fresh install has no buyers, FPOs or offers, so the market-linkage half of
the voice agent has nothing to say: "who will buy my onion?" can only answer
"nobody". This seeds a small, coherent marketplace around one farmer's phone
number so every voice flow and every portal screen can be exercised end to end.

Rules, same as the price fixtures:
- Only runs with MARKET_DEMO_MODE on.
- Every entity it creates is written to `demo_seed_registry`, so the UI can
  label it and `clear` can find it. Nothing here is presented as a real business.
- Every state change goes through the normal services, so the audit trail,
  trust scores and state machines are exercised exactly as in real use.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.services.db import DB_LOCK, get_connection

logger = logging.getLogger(__name__)

DEMO_ACTOR = "demo-seed"

# Other farmers in the same FPO. Reserved-looking numbers that cannot be dialled.
_NEIGHBOURS = ["+919000000101", "+919000000102", "+919000000103", "+919000000104"]


def _init_registry() -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS demo_seed_registry (
                    entity_type  TEXT NOT NULL,
                    entity_id    TEXT NOT NULL,
                    anchor_phone TEXT,
                    label        TEXT,
                    created_at   INTEGER NOT NULL,
                    PRIMARY KEY (entity_type, entity_id)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def _register(entity_type: str, entity_id: str, phone: str, label: str) -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO demo_seed_registry VALUES (?,?,?,?,?)",
                (entity_type, entity_id, phone, label, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()


def registry(phone: Optional[str] = None) -> List[Dict[str, Any]]:
    _init_registry()
    with DB_LOCK:
        conn = get_connection()
        try:
            if phone:
                rows = conn.execute(
                    "SELECT * FROM demo_seed_registry WHERE anchor_phone = ? ORDER BY created_at",
                    (phone,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM demo_seed_registry ORDER BY created_at"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def demo_entity_ids() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for row in registry():
        out.setdefault(row["entity_type"], []).append(row["entity_id"])
    return out


def _verified_buyer(name: str, buyer_type: str, district: str, state: str,
                    phone: str, documents: List[str], anchor: str) -> Dict[str, Any]:
    from app.trade import buyers

    buyer = buyers.create_buyer(
        name, buyer_type, phone=phone, district=district, state=state,
        service_districts=[district, "Kalaburagi"], actor=DEMO_ACTOR,
    )
    for doc in documents:
        buyers.submit_document(buyer["id"], doc, doc_number=f"DEMO-{doc}", actor=DEMO_ACTOR)
    buyer = buyers.verify_buyer(buyer["id"], verified_by=DEMO_ACTOR)
    _register("BUYER", buyer["id"], anchor, name)
    return buyer


def seed_trade_demo(farmer_phone: str) -> Dict[str, Any]:
    """Build the demo marketplace around `farmer_phone`. Idempotent per phone."""
    from app.config import get_settings
    from app.farmer_store import get_or_create_farmer
    from app.market import repository
    from app.market.demo_data import seed_demo_prices
    from app.market.schema import init_market_tables
    from app.trade import buyers, fpo, lots, offers, payments
    from app.trade.schema import init_trade_tables

    if not get_settings().MARKET_DEMO_MODE:
        raise RuntimeError("Demo marketplace data can only be seeded with MARKET_DEMO_MODE=true")

    init_market_tables()
    init_trade_tables()
    _init_registry()

    existing = registry(farmer_phone)
    if existing:
        return {"created": False, "farmer_phone": farmer_phone, "entities": existing}

    if not repository.count_price_records():
        seed_demo_prices(days=120)

    get_or_create_farmer(name="Demo Farmer", phone=farmer_phone, city="Kalaburagi")

    # --- Buyers: three verified with different track records, one unverified ---
    strong = _verified_buyer(
        "Basava Agri Traders", buyers.BuyerType.APMC_TRADER, "Kalaburagi", "Karnataka",
        "+919000000201", ["APMC_LICENCE", "PAN", "PHONE"], farmer_phone,
    )
    for _ in range(6):
        buyers.record_transaction_outcome(strong["id"], completed=True, payment_days=1.5)

    processor = _verified_buyer(
        "Solapur Fresh Produce Co", buyers.BuyerType.PROCESSOR, "Solapur", "Maharashtra",
        "+919000000202", ["GSTIN", "PAN", "PHONE"], farmer_phone,
    )

    pulses = _verified_buyer(
        "Deccan Dal Mills", buyers.BuyerType.PROCESSOR, "Kalaburagi", "Karnataka",
        "+919000000203", ["GSTIN", "PAN", "PHONE"], farmer_phone,
    )
    for _ in range(3):
        buyers.record_transaction_outcome(pulses["id"], completed=True, payment_days=3)

    unverified = buyers.create_buyer(
        "Quick Mandi Agents", buyers.BuyerType.OTHER, phone="+919000000204",
        district="Kalaburagi", state="Karnataka", actor=DEMO_ACTOR,
    )
    _register("BUYER", unverified["id"], farmer_phone, "Quick Mandi Agents (unverified)")

    # --- Demand ---
    onion_a = buyers.post_demand(
        strong["id"], "Onion", 5_000, min_grade="A", price_offered=2_950,
        delivery_district="Kalaburagi", delivery_state="Karnataka",
        payment_terms_days=3, actor=DEMO_ACTOR,
    )
    onion_b = buyers.post_demand(
        processor["id"], "Onion", 20_000, min_grade="B", price_offered=2_800,
        delivery_district="Solapur", delivery_state="Maharashtra",
        payment_terms_days=7, actor=DEMO_ACTOR,
    )
    tur = buyers.post_demand(
        pulses["id"], "Tur", 3_000, min_grade="A", price_offered=7_200,
        delivery_district="Kalaburagi", delivery_state="Karnataka",
        payment_terms_days=5, actor=DEMO_ACTOR,
    )
    for demand, label in ((onion_a, "Onion grade A demand"), (onion_b, "Onion grade B demand"),
                          (tur, "Tur demand")):
        _register("DEMAND", demand["id"], farmer_phone, label)

    # --- FPO with the farmer and four neighbours ---
    org = fpo.create_fpo(
        "Kalyana Raitha Producer Company", registration_no="DEMO-FPO-001",
        district="Kalaburagi", state="Karnataka", contact_phone="+919000000301",
        actor=DEMO_ACTOR,
    )
    fpo.verify_fpo(org["id"], verified_by=DEMO_ACTOR)
    _register("FPO", org["id"], farmer_phone, org["name"])
    fpo.add_member(org["id"], farmer_phone, "MEMBER", actor=DEMO_ACTOR)
    for phone in _NEIGHBOURS:
        fpo.add_member(org["id"], phone, "MEMBER", actor=DEMO_ACTOR)

    def lot(commodity: str, kg: float, grade: str, phone: str, label: str) -> Dict[str, Any]:
        created = lots.create_lot(
            commodity, kg, farmer_phone=phone, grade=grade,
            quality_notes="Demo lot: grade declared by the farmer",
            pickup_district="Kalaburagi", pickup_state="Karnataka",
            publish=True, actor=DEMO_ACTOR,
        )
        _register("LOT", created["id"], farmer_phone, label)
        return created

    # The farmer's main onion lot, with two competing offers waiting.
    main_onion = lot("Onion", 3_000, "A", farmer_phone, "Farmer: 30 qtl onion, grade A")
    best = offers.place_offer(
        main_onion["id"], strong["id"], 2_950, demand_id=onion_a["id"],
        quantity_kg=3_000, payment_terms_days=3, expires_in_hours=24 * 14, actor=DEMO_ACTOR,
    )
    second = offers.place_offer(
        main_onion["id"], processor["id"], 2_780, demand_id=onion_b["id"],
        quantity_kg=3_000, payment_terms_days=7, expires_in_hours=24 * 14, actor=DEMO_ACTOR,
    )
    for o in (best, second):
        _register("OFFER", o["id"], farmer_phone, "Open offer on farmer's onion")

    # A tur sale already agreed, with the money held in escrow.
    tur_lot = lot("Tur", 1_200, "A", farmer_phone, "Farmer: 12 qtl tur, grade A (sold)")
    tur_offer = offers.place_offer(
        tur_lot["id"], pulses["id"], 7_150, demand_id=tur["id"],
        quantity_kg=1_200, payment_terms_days=5, actor=DEMO_ACTOR,
    )
    _register("OFFER", tur_offer["id"], farmer_phone, "Accepted tur offer")
    accepted = offers.accept_offer(tur_offer["id"], actor=farmer_phone, actor_role="farmer")
    payment = accepted["payment"]
    payments.initiate(payment["id"], "UPI", actor=DEMO_ACTOR)
    payments.mark_escrowed(payment["id"], utr_ref="DEMO-UTR-0001", actor=DEMO_ACTOR)
    _register("PAYMENT", payment["id"], farmer_phone, "Tur payment in escrow")

    # Small grade B onion lots across the FPO: individually uneconomic to ship,
    # worth pooling together. The farmer has one of them.
    lot("Onion", 400, "B", farmer_phone, "Farmer: 4 qtl onion, grade B")
    for phone, kg in zip(_NEIGHBOURS, (600, 800, 500, 900)):
        lot("Onion", kg, "B", phone, f"Neighbour {phone[-3:]}: onion grade B")

    summary = {
        "created": True,
        "farmer_phone": farmer_phone,
        "buyers": 4,
        "verified_buyers": 3,
        "open_demand": 3,
        "fpo": org["name"],
        "fpo_members": 1 + len(_NEIGHBOURS),
        "open_offers_for_farmer": 2,
        "payment_in_escrow_rupees": payment["amount_rupees"],
        "entities": registry(farmer_phone),
    }
    logger.info("Seeded demo marketplace around %s", farmer_phone)
    return summary
