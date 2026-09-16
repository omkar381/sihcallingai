"""
Dispute and grievance handling.

When a buyer claims the grade was worse than declared, or a farmer claims
payment never arrived, the transaction needs somewhere to go that is not
"phone calls and hope". Opening a dispute freezes the associated payment, so
escrowed funds are not released while the question is open, and every step is
recorded for review.

Disputes deliberately do not auto-resolve. A resolution records who decided,
what was decided, and what happened to the money - that trail is the point.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class DisputeCategory:
    QUALITY = "QUALITY"
    PAYMENT = "PAYMENT"
    QUANTITY = "QUANTITY"
    LOGISTICS = "LOGISTICS"
    DAMAGE = "DAMAGE"
    WEIGHT = "WEIGHT"
    OTHER = "OTHER"

    ALL = {QUALITY, PAYMENT, QUANTITY, LOGISTICS, DAMAGE, WEIGHT, OTHER}


class DisputeStatus:
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    REQUESTED_INFORMATION = "REQUESTED_INFORMATION"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


ALLOWED_DISPUTE_TRANSITIONS: Dict[str, Set[str]] = {
    DisputeStatus.OPEN: {
        DisputeStatus.UNDER_REVIEW, DisputeStatus.REQUESTED_INFORMATION,
        DisputeStatus.RESOLVED, DisputeStatus.REJECTED,
    },
    DisputeStatus.UNDER_REVIEW: {
        DisputeStatus.REQUESTED_INFORMATION, DisputeStatus.ESCALATED,
        DisputeStatus.RESOLVED, DisputeStatus.REJECTED,
    },
    DisputeStatus.REQUESTED_INFORMATION: {
        DisputeStatus.UNDER_REVIEW, DisputeStatus.ESCALATED,
        DisputeStatus.RESOLVED, DisputeStatus.REJECTED,
    },
    DisputeStatus.ESCALATED: {DisputeStatus.RESOLVED, DisputeStatus.REJECTED},
    DisputeStatus.RESOLVED: set(),
    DisputeStatus.REJECTED: set(),
}

OPEN_STATUSES = {
    DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW,
    DisputeStatus.REQUESTED_INFORMATION, DisputeStatus.ESCALATED,
}


class DisputeError(ValueError):
    """Invalid dispute operation."""


def dispute_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "lot_id": row["lot_id"],
        "offer_id": row["offer_id"],
        "payment_id": row["payment_id"],
        "raised_by": row["raised_by"],
        "raised_by_role": row["raised_by_role"],
        "against_party": row["against_party"],
        "category": row["category"],
        "description": row["description"],
        "evidence_refs": json.loads(row["evidence_refs"]) if row["evidence_refs"] else [],
        "claim_paise": row["claim_paise"],
        "claim_rupees": (
            round(row["claim_paise"] / 100.0, 2) if row["claim_paise"] else None
        ),
        "status": row["status"],
        "is_open": row["status"] in OPEN_STATUSES,
        "resolution": row["resolution"],
        "resolved_by": row["resolved_by"],
        "resolved_at": row["resolved_at"],
        "created_at": row["created_at"],
        "age_days": max(0, (now_ts() - row["created_at"]) // 86400),
    }


def open_dispute(
    lot_id: str,
    raised_by: str,
    raised_by_role: str,
    category: str,
    description: str,
    *,
    offer_id: Optional[str] = None,
    payment_id: Optional[str] = None,
    against_party: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
    claim_rupees: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Raise a dispute and freeze any associated payment.

    Freezing is the substantive protection: without it, escrow could be
    released while the complaint is still being investigated.
    """
    if category not in DisputeCategory.ALL:
        raise DisputeError(f"Unknown dispute category '{category}'")
    if not (description or "").strip():
        raise DisputeError("A dispute needs a description")

    dispute_id = str(uuid.uuid4())
    ts = now_ts()
    claim_paise = int(round(claim_rupees * 100)) if claim_rupees else None

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            lot = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
            if lot is None:
                raise DisputeError(f"Unknown lot {lot_id}")

            conn.execute(
                """
                INSERT INTO disputes(
                    id, lot_id, offer_id, payment_id, raised_by, raised_by_role,
                    against_party, category, description, evidence_refs, claim_paise,
                    status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'OPEN', ?,?)
                """,
                (
                    dispute_id, lot_id, offer_id, payment_id, raised_by, raised_by_role,
                    against_party, category, description.strip(),
                    json.dumps(evidence_refs or []), claim_paise, ts, ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO dispute_events(id, dispute_id, from_status, to_status,
                                           actor, note, created_at)
                VALUES (?,?,NULL,'OPEN',?,?,?)
                """,
                (str(uuid.uuid4()), dispute_id, raised_by, "Dispute opened", ts),
            )
            audit.record(
                audit.EntityType.DISPUTE, dispute_id, audit.Action.DISPUTE_OPENED,
                actor=raised_by, actor_role=raised_by_role,
                new_value={
                    "lot_id": lot_id, "category": category, "claim_paise": claim_paise,
                },
                conn=conn,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Freeze money and lot outside the insert transaction: each of these has
    # its own guarded state machine and may legitimately refuse.
    if payment_id:
        from app.trade.payments import flag_disputed
        try:
            flag_disputed(payment_id, actor=raised_by)
        except Exception as exc:
            logger.warning("Could not freeze payment %s: %s", payment_id, exc)

    from app.trade.lots import LotStatus, transition
    try:
        transition(lot_id, LotStatus.DISPUTED, actor=raised_by, actor_role=raised_by_role,
                   note=f"{category} dispute opened")
    except Exception as exc:
        logger.info("Lot %s not moved to DISPUTED: %s", lot_id, exc)

    logger.info("Dispute %s opened on lot %s (%s)", dispute_id, lot_id, category)
    return get_dispute(dispute_id)


def get_dispute(dispute_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
            return dispute_to_dict(row) if row else None
        finally:
            conn.close()


def list_disputes(
    status: Optional[str] = None,
    lot_id: Optional[str] = None,
    open_only: bool = False,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM disputes WHERE 1=1"
    params: List[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    elif open_only:
        placeholders = ",".join("?" for _ in OPEN_STATUSES)
        sql += f" AND status IN ({placeholders})"
        params.extend(sorted(OPEN_STATUSES))
    if lot_id:
        sql += " AND lot_id = ?"
        params.append(lot_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            return [dispute_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def dispute_history(dispute_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dispute_events WHERE dispute_id = ? ORDER BY created_at ASC",
                (dispute_id,),
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
    dispute_id: str,
    to_status: str,
    actor: str,
    note: Optional[str] = None,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    ts = now_ts()
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM disputes WHERE id = ?", (dispute_id,)).fetchone()
            if row is None:
                raise DisputeError(f"Unknown dispute {dispute_id}")

            current = row["status"]
            allowed = ALLOWED_DISPUTE_TRANSITIONS.get(current, set())
            if to_status not in allowed:
                raise DisputeError(
                    f"Cannot move dispute from {current} to {to_status}. "
                    f"Allowed: {', '.join(sorted(allowed)) or 'none (closed)'}"
                )

            if resolution is not None:
                conn.execute(
                    """
                    UPDATE disputes SET status = ?, resolution = ?, resolved_by = ?,
                        resolved_at = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (to_status, resolution, actor, ts, ts, dispute_id, current),
                )
            else:
                conn.execute(
                    "UPDATE disputes SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                    (to_status, ts, dispute_id, current),
                )

            conn.execute(
                """
                INSERT INTO dispute_events(id, dispute_id, from_status, to_status,
                                           actor, note, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (str(uuid.uuid4()), dispute_id, current, to_status, actor, note, ts),
            )
            audit.record(
                audit.EntityType.DISPUTE, dispute_id,
                audit.Action.DISPUTE_RESOLVED if to_status == DisputeStatus.RESOLVED
                else audit.Action.STATUS_CHANGED,
                actor=actor, actor_role="admin",
                old_value={"status": current},
                new_value={"status": to_status, "resolution": resolution},
                conn=conn,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return get_dispute(dispute_id)


def start_review(dispute_id: str, actor: str = "admin") -> Dict[str, Any]:
    return _transition(dispute_id, DisputeStatus.UNDER_REVIEW, actor, "Review started")


def request_information(dispute_id: str, what: str, actor: str = "admin") -> Dict[str, Any]:
    return _transition(dispute_id, DisputeStatus.REQUESTED_INFORMATION, actor, what)


def escalate(dispute_id: str, reason: str, actor: str = "admin") -> Dict[str, Any]:
    return _transition(dispute_id, DisputeStatus.ESCALATED, actor, reason)


def resolve(
    dispute_id: str,
    resolution: str,
    actor: str = "admin",
    *,
    release_payment: bool = False,
    refund_payment: bool = False,
) -> Dict[str, Any]:
    """
    Close a dispute and say what happens to the money.

    `release_payment` and `refund_payment` are mutually exclusive: a
    resolution that both pays the farmer and refunds the buyer is incoherent,
    and silently picking one would hide the contradiction.
    """
    if release_payment and refund_payment:
        raise DisputeError("A resolution cannot both release and refund the payment")
    if not (resolution or "").strip():
        raise DisputeError("A resolution needs an explanation")

    dispute = get_dispute(dispute_id)
    if dispute is None:
        raise DisputeError(f"Unknown dispute {dispute_id}")

    result = _transition(
        dispute_id, DisputeStatus.RESOLVED, actor,
        note="Resolved", resolution=resolution.strip(),
    )

    payment_id = dispute.get("payment_id")
    if payment_id:
        from app.trade.payments import refund as refund_payment_fn, release as release_payment_fn
        try:
            if release_payment:
                release_payment_fn(payment_id, actor=actor)
            elif refund_payment:
                refund_payment_fn(payment_id, f"Dispute {dispute_id}: {resolution}", actor=actor)
        except Exception as exc:
            logger.warning("Payment action after dispute resolution failed: %s", exc)

    # Record the complaint against the buyer's trust profile regardless of
    # outcome: a pattern of disputes is itself a signal.
    if dispute.get("raised_by_role") == "farmer" and dispute.get("against_party"):
        with DB_LOCK:
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE buyers SET dispute_count = dispute_count + 1, updated_at = ? "
                    "WHERE id = ?",
                    (now_ts(), dispute["against_party"]),
                )
                conn.commit()
            finally:
                conn.close()

    return result


def reject(dispute_id: str, reason: str, actor: str = "admin") -> Dict[str, Any]:
    return _transition(dispute_id, DisputeStatus.REJECTED, actor,
                       note=reason, resolution=reason)


def dispute_statistics() -> Dict[str, Any]:
    """Aggregate dispute health, for the admin view."""
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT status, category, COUNT(*) AS n FROM disputes GROUP BY status, category"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM disputes").fetchone()["n"]
            resolved = conn.execute(
                "SELECT COUNT(*) AS n, AVG(resolved_at - created_at) AS avg_secs "
                "FROM disputes WHERE status = 'RESOLVED'"
            ).fetchone()
        finally:
            conn.close()

    by_status: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
        by_category[r["category"]] = by_category.get(r["category"], 0) + r["n"]

    avg_days = None
    if resolved["avg_secs"]:
        avg_days = round(resolved["avg_secs"] / 86400.0, 2)

    return {
        "total": total,
        "open": sum(by_status.get(s, 0) for s in OPEN_STATUSES),
        "resolved": resolved["n"],
        "by_status": by_status,
        "by_category": by_category,
        "avg_resolution_days": avg_days,
    }
