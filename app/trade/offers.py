"""
Offers and acceptance.

Acceptance is the moment a conversation becomes a contract: the lot is
committed, competing offers die, the buyer's demand is drawn down and a
payment obligation comes into existence. All of that happens inside one
database transaction under the global write lock, so two buyers racing to
have their offer accepted cannot both win.

The guard is a conditional UPDATE on the offer's own status rather than a
read-then-write. If a competing transaction already accepted the lot, the
UPDATE matches zero rows and this acceptance aborts instead of silently
double-selling the consignment.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.buyers import VerificationStatus, fill_demand
from app.trade.lots import ALLOWED_TRANSITIONS, LotError, LotStatus
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class OfferStatus:
    OPEN = "OPEN"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"   # another offer on the same lot was accepted


class OfferError(ValueError):
    """Invalid offer operation."""


def offer_to_dict(row, buyer_row=None) -> Dict[str, Any]:
    quintals = float(row["quantity_kg"]) / 100.0
    payload = {
        "id": row["id"],
        "lot_id": row["lot_id"],
        "buyer_id": row["buyer_id"],
        "demand_id": row["demand_id"],
        "price_per_quintal": row["price_per_quintal"],
        "quantity_kg": row["quantity_kg"],
        "gross_value": round(float(row["price_per_quintal"]) * quintals, 2),
        "payment_terms_days": row["payment_terms_days"],
        "pickup_by": row["pickup_by"],
        "conditions": json.loads(row["conditions"]) if row["conditions"] else {},
        "status": row["status"],
        "created_at": row["created_at"],
        "responded_at": row["responded_at"],
        "expires_at": row["expires_at"],
    }
    if buyer_row is not None:
        from app.trade.buyers import buyer_to_dict
        payload["buyer"] = buyer_to_dict(buyer_row)
    return payload


def place_offer(
    lot_id: str,
    buyer_id: str,
    price_per_quintal: float,
    *,
    quantity_kg: Optional[float] = None,
    demand_id: Optional[str] = None,
    payment_terms_days: int = 7,
    pickup_by: Optional[int] = None,
    conditions: Optional[Dict[str, Any]] = None,
    expires_in_hours: int = 48,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Place a bid on a lot.

    Only verified buyers may bid, and only on lots that are open to bidding.
    Placing the first offer moves the lot to OFFER_RECEIVED.
    """
    if price_per_quintal <= 0:
        raise OfferError("price_per_quintal must be positive")

    ts = now_ts()
    offer_id = str(uuid.uuid4())

    with DB_LOCK:
        conn = get_connection()
        try:
            lot = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
            if lot is None:
                raise OfferError(f"Unknown lot {lot_id}")

            biddable = {
                LotStatus.PUBLISHED, LotStatus.MATCHED,
                LotStatus.OFFER_RECEIVED, LotStatus.NEGOTIATION,
            }
            if lot["status"] not in biddable:
                raise OfferError(
                    f"Lot is {lot['status']} and is not open for offers"
                )

            buyer = conn.execute("SELECT * FROM buyers WHERE id = ?", (buyer_id,)).fetchone()
            if buyer is None:
                raise OfferError(f"Unknown buyer {buyer_id}")
            if buyer["verification_status"] != VerificationStatus.VERIFIED:
                raise OfferError(
                    f"Only verified buyers may place offers "
                    f"(this buyer is {buyer['verification_status']})"
                )

            qty = float(quantity_kg if quantity_kg is not None else lot["quantity_kg"])
            if qty <= 0 or qty > float(lot["quantity_kg"]):
                raise OfferError(
                    f"Offer quantity must be between 0 and the lot size "
                    f"({float(lot['quantity_kg']):,.0f} kg)"
                )

            conn.execute(
                """
                INSERT INTO offers(
                    id, lot_id, buyer_id, demand_id, price_per_quintal, quantity_kg,
                    payment_terms_days, pickup_by, conditions, status, created_at, expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?, 'OPEN', ?,?)
                """,
                (
                    offer_id, lot_id, buyer_id, demand_id, float(price_per_quintal), qty,
                    int(payment_terms_days), pickup_by, json.dumps(conditions or {}),
                    ts, ts + expires_in_hours * 3600,
                ),
            )

            if lot["status"] in (LotStatus.PUBLISHED, LotStatus.MATCHED):
                conn.execute(
                    "UPDATE lots SET status = ?, updated_at = ? WHERE id = ?",
                    (LotStatus.OFFER_RECEIVED, ts, lot_id),
                )
                audit.record(
                    audit.EntityType.LOT, lot_id, audit.Action.STATUS_CHANGED,
                    actor=actor or buyer_id, actor_role="buyer",
                    old_value={"status": lot["status"]},
                    new_value={"status": LotStatus.OFFER_RECEIVED},
                    conn=conn,
                )

            audit.record(
                audit.EntityType.OFFER, offer_id, audit.Action.OFFER_PLACED,
                actor=actor or buyer_id, actor_role="buyer",
                new_value={
                    "lot_id": lot_id, "buyer_id": buyer_id,
                    "price_per_quintal": price_per_quintal, "quantity_kg": qty,
                },
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info("Offer %s placed on lot %s at %.2f/quintal", offer_id, lot_id, price_per_quintal)
    return get_offer(offer_id)


def get_offer(offer_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if row is None:
                return None
            buyer = conn.execute(
                "SELECT * FROM buyers WHERE id = ?", (row["buyer_id"],)
            ).fetchone()
            return offer_to_dict(row, buyer)
        finally:
            conn.close()


def list_offers(
    lot_id: Optional[str] = None,
    buyer_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM offers WHERE 1=1"
    params: List[Any] = []
    if lot_id:
        sql += " AND lot_id = ?"
        params.append(lot_id)
    if buyer_id:
        sql += " AND buyer_id = ?"
        params.append(buyer_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY price_per_quintal DESC, created_at ASC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
            out = []
            for r in rows:
                buyer = conn.execute(
                    "SELECT * FROM buyers WHERE id = ?", (r["buyer_id"],)
                ).fetchone()
                out.append(offer_to_dict(r, buyer))
            return out
        finally:
            conn.close()


def expire_stale_offers() -> int:
    """Close offers past their expiry. Safe to call repeatedly."""
    ts = now_ts()
    with DB_LOCK:
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE offers SET status = ?, responded_at = ? "
                "WHERE status = ? AND expires_at IS NOT NULL AND expires_at < ?",
                (OfferStatus.EXPIRED, ts, OfferStatus.OPEN, ts),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def accept_offer(
    offer_id: str,
    *,
    actor: Optional[str] = None,
    actor_role: str = "farmer",
) -> Dict[str, Any]:
    """
    Accept an offer and commit the sale.

    In one transaction: the offer is accepted, competing offers are
    superseded, the lot moves to ACCEPTED, the buyer's demand is drawn down
    and a PENDING payment is created.

    Returns the lot, the accepted offer and the new payment.
    """
    from app.trade.payments import PaymentStatus  # local import avoids a cycle

    ts = now_ts()
    payment_id = str(uuid.uuid4())

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")

            offer = conn.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
            if offer is None:
                raise OfferError(f"Unknown offer {offer_id}")
            if offer["status"] != OfferStatus.OPEN:
                raise OfferError(f"Offer is {offer['status']} and cannot be accepted")
            # `<=`, not `<`: an offer whose deadline is exactly now has closed.
            # For a contract deadline the conservative reading is the correct one.
            if offer["expires_at"] and offer["expires_at"] <= ts:
                raise OfferError("Offer has expired")

            lot = conn.execute(
                "SELECT * FROM lots WHERE id = ?", (offer["lot_id"],)
            ).fetchone()
            if lot is None:
                raise OfferError(f"Unknown lot {offer['lot_id']}")
            if lot["accepted_offer_id"]:
                raise OfferError("This lot already has an accepted offer")
            if LotStatus.ACCEPTED not in ALLOWED_TRANSITIONS.get(lot["status"], set()):
                raise OfferError(
                    f"Lot is {lot['status']} and cannot move to ACCEPTED"
                )

            # Conditional UPDATE is the race guard: if a competing transaction
            # accepted first, this matches zero rows and we abort.
            cur = conn.execute(
                "UPDATE offers SET status = ?, responded_at = ? WHERE id = ? AND status = ?",
                (OfferStatus.ACCEPTED, ts, offer_id, OfferStatus.OPEN),
            )
            if cur.rowcount != 1:
                raise OfferError("Offer was modified concurrently; acceptance aborted")

            conn.execute(
                "UPDATE offers SET status = ?, responded_at = ? "
                "WHERE lot_id = ? AND id != ? AND status = ?",
                (OfferStatus.SUPERSEDED, ts, offer["lot_id"], offer_id, OfferStatus.OPEN),
            )

            conn.execute(
                "UPDATE lots SET status = ?, accepted_offer_id = ?, updated_at = ? WHERE id = ?",
                (LotStatus.ACCEPTED, offer_id, ts, offer["lot_id"]),
            )

            if offer["demand_id"]:
                fill_demand(offer["demand_id"], float(offer["quantity_kg"]), conn=conn)

            quintals = float(offer["quantity_kg"]) / 100.0
            amount_paise = int(round(float(offer["price_per_quintal"]) * quintals * 100))
            due_date = ts + int(offer["payment_terms_days"]) * 86400

            conn.execute(
                """
                INSERT INTO payments(
                    id, lot_id, offer_id, buyer_id, payee_phone, payee_fpo_id,
                    amount_paise, status, due_date, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payment_id, offer["lot_id"], offer_id, offer["buyer_id"],
                    lot["farmer_phone"], lot["fpo_id"], amount_paise,
                    PaymentStatus.PENDING, due_date, ts, ts,
                ),
            )

            audit.record(
                audit.EntityType.OFFER, offer_id, audit.Action.OFFER_ACCEPTED,
                actor=actor, actor_role=actor_role,
                old_value={"status": OfferStatus.OPEN},
                new_value={"status": OfferStatus.ACCEPTED},
                metadata={"lot_id": offer["lot_id"], "payment_id": payment_id},
                conn=conn,
            )
            audit.record(
                audit.EntityType.LOT, offer["lot_id"], audit.Action.STATUS_CHANGED,
                actor=actor, actor_role=actor_role,
                old_value={"status": lot["status"]},
                new_value={"status": LotStatus.ACCEPTED, "accepted_offer_id": offer_id},
                conn=conn,
            )
            audit.record(
                audit.EntityType.PAYMENT, payment_id, audit.Action.CREATED,
                actor=actor, actor_role=actor_role,
                new_value={
                    "amount_paise": amount_paise, "status": PaymentStatus.PENDING,
                    "due_date": due_date,
                },
                conn=conn,
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    from app.trade.lots import get_lot
    from app.trade.payments import get_payment

    logger.info("Offer %s accepted; payment %s created", offer_id, payment_id)
    return {
        "offer": get_offer(offer_id),
        "lot": get_lot(offer["lot_id"]),
        "payment": get_payment(payment_id),
    }


def reject_offer(
    offer_id: str, reason: str = "", actor: Optional[str] = None
) -> Dict[str, Any]:
    with DB_LOCK:
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE offers SET status = ?, responded_at = ? WHERE id = ? AND status = ?",
                (OfferStatus.REJECTED, now_ts(), offer_id, OfferStatus.OPEN),
            )
            if cur.rowcount != 1:
                raise OfferError("Offer is not open and cannot be rejected")
            audit.record(
                audit.EntityType.OFFER, offer_id, audit.Action.OFFER_REJECTED,
                actor=actor, actor_role="farmer",
                new_value={"status": OfferStatus.REJECTED, "reason": reason},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()
    return get_offer(offer_id)
