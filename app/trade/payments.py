"""
Payment lifecycle and settlement.

Money is handled in integer paise throughout. Floating-point rupees
accumulate rounding error across a settlement chain, and an FPO split that
does not reconcile to the paisa is not auditable.

The lifecycle is a guarded state machine. A payment cannot jump from PENDING
to RELEASED: funds must be escrowed, delivery confirmed, and only then
released. Every transition writes both a payment_event and an audit event, so
a settlement can be reconstructed exactly.

Nothing here moves real money. This models and tracks the obligation; a
production deployment would bind RELEASE to a payment service provider's
webhook. The states are named so that integration is a matter of calling
`mark_released` from a verified callback rather than restructuring anything.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class PaymentStatus:
    PENDING = "PENDING"
    INITIATED = "INITIATED"
    ESCROW = "ESCROW"
    DELIVERY_CONFIRMED = "DELIVERY_CONFIRMED"
    RELEASE_PENDING = "RELEASE_PENDING"
    RELEASED = "RELEASED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    DISPUTED = "DISPUTED"


ALLOWED_PAYMENT_TRANSITIONS: Dict[str, Set[str]] = {
    PaymentStatus.PENDING: {PaymentStatus.INITIATED, PaymentStatus.FAILED},
    PaymentStatus.INITIATED: {
        PaymentStatus.ESCROW, PaymentStatus.FAILED, PaymentStatus.REFUNDED,
    },
    PaymentStatus.ESCROW: {
        PaymentStatus.DELIVERY_CONFIRMED, PaymentStatus.DISPUTED, PaymentStatus.REFUNDED,
    },
    PaymentStatus.DELIVERY_CONFIRMED: {
        PaymentStatus.RELEASE_PENDING, PaymentStatus.RELEASED, PaymentStatus.DISPUTED,
    },
    PaymentStatus.RELEASE_PENDING: {PaymentStatus.RELEASED, PaymentStatus.DISPUTED},
    PaymentStatus.RELEASED: set(),                      # terminal
    PaymentStatus.FAILED: {PaymentStatus.INITIATED},    # retryable
    PaymentStatus.REFUNDED: set(),                      # terminal
    PaymentStatus.DISPUTED: {
        PaymentStatus.RELEASED, PaymentStatus.REFUNDED, PaymentStatus.ESCROW,
    },
}

TERMINAL_STATUSES = {PaymentStatus.RELEASED, PaymentStatus.REFUNDED}


class PaymentError(ValueError):
    """Invalid payment operation."""


def rupees_to_paise(rupees: float) -> int:
    return int(round(float(rupees) * 100))


def paise_to_rupees(paise: int) -> float:
    return round(int(paise) / 100.0, 2)


def payment_to_dict(row) -> Dict[str, Any]:
    now = now_ts()
    overdue = (
        row["due_date"] is not None
        and row["due_date"] < now
        and row["status"] not in TERMINAL_STATUSES
    )
    return {
        "id": row["id"],
        "lot_id": row["lot_id"],
        "offer_id": row["offer_id"],
        "buyer_id": row["buyer_id"],
        "payee_phone": row["payee_phone"],
        "payee_fpo_id": row["payee_fpo_id"],
        "amount_paise": row["amount_paise"],
        "amount_rupees": paise_to_rupees(row["amount_paise"]),
        "status": row["status"],
        "method": row["method"],
        "utr_ref": row["utr_ref"],
        "due_date": row["due_date"],
        "is_overdue": overdue,
        "days_overdue": (
            max(0, (now - row["due_date"]) // 86400) if overdue else 0
        ),
        "initiated_at": row["initiated_at"],
        "escrowed_at": row["escrowed_at"],
        "released_at": row["released_at"],
        "failed_reason": row["failed_reason"],
        "created_at": row["created_at"],
        "is_terminal": row["status"] in TERMINAL_STATUSES,
    }


def get_payment(payment_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
            return payment_to_dict(row) if row else None
        finally:
            conn.close()


def list_payments(
    lot_id: Optional[str] = None,
    buyer_id: Optional[str] = None,
    payee_phone: Optional[str] = None,
    status: Optional[str] = None,
    overdue_only: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM payments WHERE 1=1"
    params: List[Any] = []
    if lot_id:
        sql += " AND lot_id = ?"
        params.append(lot_id)
    if buyer_id:
        sql += " AND buyer_id = ?"
        params.append(buyer_id)
    if payee_phone:
        sql += " AND payee_phone = ?"
        params.append(payee_phone)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if overdue_only:
        sql += " AND due_date IS NOT NULL AND due_date < ? AND status NOT IN (?,?)"
        params.extend([now_ts(), PaymentStatus.RELEASED, PaymentStatus.REFUNDED])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            return [payment_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def payment_history(payment_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM payment_events WHERE payment_id = ? ORDER BY created_at ASC",
                (payment_id,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "from_status": r["from_status"], "to_status": r["to_status"],
            "actor": r["actor"], "note": r["note"], "created_at": r["created_at"],
        }
        for r in rows
    ]


def _transition(
    payment_id: str,
    to_status: str,
    *,
    actor: Optional[str],
    actor_role: str = "system",
    note: Optional[str] = None,
    audit_action: Optional[str] = None,
    extra_sql: Optional[str] = None,
    extra_params: tuple = (),
) -> Dict[str, Any]:
    """
    Move a payment to a new status, refusing illegal transitions.

    The status UPDATE is conditional on the current status, so two concurrent
    releases cannot both succeed.
    """
    ts = now_ts()
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
            if row is None:
                raise PaymentError(f"Unknown payment {payment_id}")

            current = row["status"]
            if current == to_status:
                conn.rollback()
                return payment_to_dict(row)

            allowed = ALLOWED_PAYMENT_TRANSITIONS.get(current, set())
            if to_status not in allowed:
                raise PaymentError(
                    f"Cannot move payment from {current} to {to_status}. "
                    f"Allowed: {', '.join(sorted(allowed)) or 'none (terminal)'}"
                )

            cur = conn.execute(
                "UPDATE payments SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (to_status, ts, payment_id, current),
            )
            if cur.rowcount != 1:
                raise PaymentError("Payment was modified concurrently; transition aborted")

            if extra_sql:
                conn.execute(extra_sql, (*extra_params, payment_id))

            conn.execute(
                """
                INSERT INTO payment_events(id, payment_id, from_status, to_status,
                                           actor, note, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (str(uuid.uuid4()), payment_id, current, to_status, actor, note, ts),
            )
            audit.record(
                audit.EntityType.PAYMENT, payment_id,
                audit_action or audit.Action.STATUS_CHANGED,
                actor=actor, actor_role=actor_role,
                old_value={"status": current}, new_value={"status": to_status},
                metadata={"note": note} if note else None,
                conn=conn,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return get_payment(payment_id)


def initiate(
    payment_id: str, method: str = "UPI", actor: Optional[str] = None
) -> Dict[str, Any]:
    """Buyer starts the payment."""
    return _transition(
        payment_id, PaymentStatus.INITIATED,
        actor=actor, actor_role="buyer", note=f"via {method}",
        audit_action=audit.Action.PAYMENT_INITIATED,
        extra_sql="UPDATE payments SET method = ?, initiated_at = ? WHERE id = ?",
        extra_params=(method, now_ts()),
    )


def mark_escrowed(
    payment_id: str, utr_ref: Optional[str] = None, actor: Optional[str] = None
) -> Dict[str, Any]:
    """
    Funds are held. The farmer's produce is now protected.

    This is the state that makes the marketplace safe to use: the buyer has
    committed money before the crop leaves the village.
    """
    return _transition(
        payment_id, PaymentStatus.ESCROW,
        actor=actor, actor_role="system", note=f"UTR {utr_ref}" if utr_ref else None,
        audit_action=audit.Action.PAYMENT_ESCROWED,
        extra_sql="UPDATE payments SET utr_ref = ?, escrowed_at = ? WHERE id = ?",
        extra_params=(utr_ref, now_ts()),
    )


def confirm_delivery(payment_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    """
    Buyer confirms the consignment arrived and matched its grade.

    Also advances the lot to PAYMENT_PENDING. The lot and payment state
    machines are separate but must stay in step: a lot stuck at DELIVERED
    while its payment is RELEASED would make the audit trail incoherent.
    """
    result = _transition(
        payment_id, PaymentStatus.DELIVERY_CONFIRMED,
        actor=actor, actor_role="buyer",
        audit_action=audit.Action.DELIVERY_CONFIRMED,
    )
    _advance_lot(result["lot_id"], "PAYMENT_PENDING", actor)
    return result


def _advance_lot(lot_id: str, to_status: str, actor: Optional[str]) -> None:
    """
    Keep the lot in step with its payment.

    A refusal here means the two state machines have diverged, which is a
    data-integrity problem worth a warning - not something to swallow.
    """
    from app.trade.lots import transition as lot_transition

    try:
        lot_transition(lot_id, to_status, actor=actor, actor_role="system")
    except Exception as exc:
        logger.warning(
            "Lot %s could not advance to %s alongside its payment: %s",
            lot_id, to_status, exc,
        )


def release(payment_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    """
    Release escrowed funds to the seller and close out the transaction.

    Updates the lot to PAID and folds the outcome into the buyer's trust
    record, which is what makes the trust score reflect real behaviour.
    """
    payment = get_payment(payment_id)
    if payment is None:
        raise PaymentError(f"Unknown payment {payment_id}")

    result = _transition(
        payment_id, PaymentStatus.RELEASED,
        actor=actor, actor_role="system",
        audit_action=audit.Action.PAYMENT_RELEASED,
        extra_sql="UPDATE payments SET released_at = ? WHERE id = ?",
        extra_params=(now_ts(),),
    )

    days = None
    if payment.get("created_at"):
        days = max(0.0, (now_ts() - payment["created_at"]) / 86400.0)

    from app.trade.buyers import record_transaction_outcome
    record_transaction_outcome(
        payment["buyer_id"], completed=True, payment_days=days,
        defaulted=bool(payment.get("is_overdue")),
    )

    _advance_lot(payment["lot_id"], "PAID", actor)
    return result


def mark_failed(
    payment_id: str, reason: str, actor: Optional[str] = None
) -> Dict[str, Any]:
    return _transition(
        payment_id, PaymentStatus.FAILED,
        actor=actor, actor_role="system", note=reason,
        audit_action=audit.Action.PAYMENT_FAILED,
        extra_sql="UPDATE payments SET failed_reason = ? WHERE id = ?",
        extra_params=(reason,),
    )


def refund(payment_id: str, reason: str, actor: Optional[str] = None) -> Dict[str, Any]:
    return _transition(
        payment_id, PaymentStatus.REFUNDED,
        actor=actor, actor_role="system", note=reason,
        audit_action=audit.Action.PAYMENT_REFUNDED,
    )


def flag_disputed(payment_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    """Freeze a payment while a dispute is open."""
    return _transition(
        payment_id, PaymentStatus.DISPUTED, actor=actor, actor_role="system",
        note="Frozen pending dispute resolution",
    )


def mark_overdue_defaults() -> Dict[str, Any]:
    """
    Record a default against buyers whose escrow never arrived.

    Run periodically. Defaults are what make the trust score protective
    rather than decorative.
    """
    overdue = list_payments(overdue_only=True, limit=500)
    flagged = []
    from app.trade.buyers import record_transaction_outcome

    for payment in overdue:
        # Only count as a default when the buyer never funded escrow.
        if payment["status"] in (PaymentStatus.PENDING, PaymentStatus.INITIATED):
            if payment["days_overdue"] >= 3:
                record_transaction_outcome(
                    payment["buyer_id"], completed=False, defaulted=True
                )
                audit.record(
                    audit.EntityType.PAYMENT, payment["id"], audit.Action.PAYMENT_FAILED,
                    actor="system", actor_role="system",
                    metadata={"days_overdue": payment["days_overdue"]},
                )
                flagged.append(payment["id"])

    return {"checked": len(overdue), "defaults_recorded": len(flagged), "payment_ids": flagged}


def settlement_summary(lot_id: str) -> Dict[str, Any]:
    """
    Who gets paid what for a lot, including the FPO split.

    For an aggregate lot the total is apportioned across member farmers by
    weight, reconciling exactly to the paisa.
    """
    payments = list_payments(lot_id=lot_id)
    if not payments:
        return {"lot_id": lot_id, "payments": [], "total_paise": 0, "splits": []}

    total_paise = sum(p["amount_paise"] for p in payments)

    from app.trade.lots import get_lot
    lot = get_lot(lot_id)
    splits: List[Dict[str, Any]] = []

    if lot and lot.get("is_aggregate"):
        from app.trade.fpo import split_settlement
        try:
            splits = split_settlement(lot_id, total_paise)
        except Exception as exc:
            logger.warning("Could not split settlement for %s: %s", lot_id, exc)

    return {
        "lot_id": lot_id,
        "is_aggregate": bool(lot and lot.get("is_aggregate")),
        "total_paise": total_paise,
        "total_rupees": paise_to_rupees(total_paise),
        "payments": payments,
        "splits": splits,
        "reconciles": (
            sum(s["amount_paise"] for s in splits) == total_paise if splits else True
        ),
    }
