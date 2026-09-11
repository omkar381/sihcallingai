"""
Lot lifecycle and quality grading.

A lot is one sellable consignment. It carries the quality information a buyer
needs to bid without inspecting it in person, and moves through an explicit
state machine so that "what happened to my crop?" always has an answer.

Grading is deliberately layered. A grade the farmer described over the phone
is not the same claim as one an inspector confirmed, and the difference is
recorded in `grade_basis` rather than flattened away:

    SELF_DECLARED  -> the farmer said so
    AI_ESTIMATED   -> derived from their description; still their claim
    BUYER_ACCEPTED -> a buyer agreed to it when offering
    INSPECTED      -> a third party physically checked it
    VERIFIED       -> inspected and reconciled at delivery

Only INSPECTED and VERIFIED are evidence. Everything above them is a claim,
and the API says so.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set

from app.market.normalization import normalize_grade, normalize_market_name, resolve_commodity
from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class LotStatus:
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    MATCHED = "MATCHED"
    OFFER_RECEIVED = "OFFER_RECEIVED"
    NEGOTIATION = "NEGOTIATION"
    ACCEPTED = "ACCEPTED"
    LOGISTICS_PENDING = "LOGISTICS_PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"
    AGGREGATED = "AGGREGATED"   # absorbed into an FPO parent lot


# Legal transitions. An explicit map beats scattered `if status ==` checks:
# it makes the lifecycle reviewable in one place and impossible to bypass.
ALLOWED_TRANSITIONS: Dict[str, Set[str]] = {
    LotStatus.DRAFT: {LotStatus.PUBLISHED, LotStatus.CANCELLED},
    LotStatus.PUBLISHED: {
        LotStatus.MATCHED, LotStatus.OFFER_RECEIVED, LotStatus.AGGREGATED,
        LotStatus.CANCELLED,
    },
    LotStatus.MATCHED: {LotStatus.OFFER_RECEIVED, LotStatus.PUBLISHED, LotStatus.CANCELLED},
    LotStatus.OFFER_RECEIVED: {
        LotStatus.NEGOTIATION, LotStatus.ACCEPTED, LotStatus.PUBLISHED, LotStatus.CANCELLED,
    },
    LotStatus.NEGOTIATION: {LotStatus.ACCEPTED, LotStatus.PUBLISHED, LotStatus.CANCELLED},
    LotStatus.ACCEPTED: {LotStatus.LOGISTICS_PENDING, LotStatus.DISPUTED, LotStatus.CANCELLED},
    LotStatus.LOGISTICS_PENDING: {LotStatus.IN_TRANSIT, LotStatus.DISPUTED, LotStatus.CANCELLED},
    LotStatus.IN_TRANSIT: {LotStatus.DELIVERED, LotStatus.DISPUTED},
    LotStatus.DELIVERED: {LotStatus.PAYMENT_PENDING, LotStatus.DISPUTED},
    LotStatus.PAYMENT_PENDING: {LotStatus.PAID, LotStatus.DISPUTED},
    LotStatus.PAID: {LotStatus.COMPLETED, LotStatus.DISPUTED},
    LotStatus.COMPLETED: set(),
    # A dispute can land the lot back in the flow once resolved.
    LotStatus.DISPUTED: {
        LotStatus.DELIVERED, LotStatus.PAYMENT_PENDING, LotStatus.PAID,
        LotStatus.COMPLETED, LotStatus.CANCELLED,
    },
    LotStatus.CANCELLED: set(),
    LotStatus.AGGREGATED: {LotStatus.PUBLISHED, LotStatus.CANCELLED},
}

GRADE_BASIS_ORDER = [
    "SELF_DECLARED", "AI_ESTIMATED", "BUYER_ACCEPTED", "INSPECTED", "VERIFIED",
]
EVIDENCE_BASES = {"INSPECTED", "VERIFIED"}


class LotError(ValueError):
    """Invalid lot operation."""


# ---------------------------------------------------------------------------
# Quality
# ---------------------------------------------------------------------------

@dataclass
class QualityAssessment:
    grade: str
    basis: str
    confidence: str
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "grade": self.grade,
            "basis": self.basis,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "is_evidence": self.basis in EVIDENCE_BASES,
        }


def assess_quality(
    *,
    moisture_pct: Optional[float] = None,
    damage_pct: Optional[float] = None,
    foreign_matter_pct: Optional[float] = None,
    description: Optional[str] = None,
) -> QualityAssessment:
    """
    Derive a grade from measurable attributes, or from a farmer's description.

    Measured values drive the grade when present. A text description alone
    yields a weaker claim, and the result always says which it was, so a
    buyer can see whether a grade rests on numbers or on a phone call.
    """
    reasons: List[str] = []
    measured = [v for v in (moisture_pct, damage_pct, foreign_matter_pct) if v is not None]

    if measured:
        grade = "A"
        if moisture_pct is not None:
            if moisture_pct > 16:
                grade = "C"
                reasons.append(f"Moisture {moisture_pct:.1f}% is high")
            elif moisture_pct > 12:
                grade = min(grade, "B", key=lambda g: "ABC".index(g)) if grade == "A" else grade
                grade = "B" if grade == "A" else grade
                reasons.append(f"Moisture {moisture_pct:.1f}% is moderate")
            else:
                reasons.append(f"Moisture {moisture_pct:.1f}% is within norms")

        if damage_pct is not None:
            if damage_pct > 10:
                grade = "C"
                reasons.append(f"Damage {damage_pct:.1f}% is high")
            elif damage_pct > 4:
                grade = "C" if grade == "C" else "B"
                reasons.append(f"Damage {damage_pct:.1f}% is moderate")
            else:
                reasons.append(f"Damage {damage_pct:.1f}% is low")

        if foreign_matter_pct is not None:
            if foreign_matter_pct > 3:
                grade = "C"
                reasons.append(f"Foreign matter {foreign_matter_pct:.1f}% is high")
            else:
                reasons.append(f"Foreign matter {foreign_matter_pct:.1f}% is acceptable")

        return QualityAssessment(grade, "SELF_DECLARED", "MEDIUM", reasons)

    if description:
        text = description.lower()
        negative = any(w in text for w in
                       ("damage", "rotten", "spoil", "wet", "black", "small", "poor", "broken"))
        positive = any(w in text for w in
                       ("good", "dry", "clean", "big", "large", "fresh", "best", "export"))

        if negative and not positive:
            grade, reasons = "C", ["Farmer's description mentions quality problems"]
        elif positive and not negative:
            grade, reasons = "A", ["Farmer describes the produce as good quality"]
        else:
            grade, reasons = "B", ["Farmer's description is mixed or unclear"]

        reasons.append("Grade estimated from a spoken description; not measured or inspected")
        return QualityAssessment(grade, "AI_ESTIMATED", "LOW", reasons)

    return QualityAssessment(
        "UNGRADED", "SELF_DECLARED", "LOW",
        ["No quality information was provided"],
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def lot_to_dict(row, include_children: bool = False) -> Dict[str, Any]:
    payload = {
        "id": row["id"],
        "farmer_phone": row["farmer_phone"],
        "fpo_id": row["fpo_id"],
        "parent_lot_id": row["parent_lot_id"],
        "is_aggregate": bool(row["is_aggregate"]),
        "commodity": row["commodity"],
        "variety": row["variety"],
        "grade": row["grade"],
        "grade_basis": row["grade_basis"],
        "grade_is_evidence": row["grade_basis"] in EVIDENCE_BASES,
        "moisture_pct": row["moisture_pct"],
        "damage_pct": row["damage_pct"],
        "foreign_matter_pct": row["foreign_matter_pct"],
        "quality_notes": row["quality_notes"],
        "photo_refs": json.loads(row["photo_refs"]) if row["photo_refs"] else [],
        "quantity_kg": row["quantity_kg"],
        "quantity_quintal": round(float(row["quantity_kg"]) / 100.0, 2),
        "expected_price": row["expected_price"],
        "price_unit": row["price_unit"],
        "harvest_date": row["harvest_date"],
        "available_from": row["available_from"],
        "available_until": row["available_until"],
        "pickup_village": row["pickup_village"],
        "pickup_district": row["pickup_district"],
        "pickup_state": row["pickup_state"],
        "storage_type": row["storage_type"],
        "status": row["status"],
        "accepted_offer_id": row["accepted_offer_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_children and row["is_aggregate"]:
        payload["member_lots"] = get_child_lots(row["id"])
    return payload


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def create_lot(
    commodity: str,
    quantity_kg: float,
    *,
    farmer_phone: Optional[str] = None,
    fpo_id: Optional[str] = None,
    variety: Optional[str] = None,
    grade: Optional[str] = None,
    moisture_pct: Optional[float] = None,
    damage_pct: Optional[float] = None,
    foreign_matter_pct: Optional[float] = None,
    quality_description: Optional[str] = None,
    quality_notes: Optional[str] = None,
    photo_refs: Optional[List[str]] = None,
    expected_price: Optional[float] = None,
    harvest_date: Optional[str] = None,
    available_from: Optional[int] = None,
    available_until: Optional[int] = None,
    pickup_village: Optional[str] = None,
    pickup_district: Optional[str] = None,
    pickup_state: Optional[str] = None,
    storage_type: Optional[str] = None,
    publish: bool = False,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a lot. Starts in DRAFT unless `publish` is set."""
    match = resolve_commodity(commodity)
    if match is None:
        raise LotError(f"'{commodity}' is not a recognised commodity")
    if quantity_kg <= 0:
        raise LotError("quantity_kg must be positive")
    if not farmer_phone and not fpo_id:
        raise LotError("A lot needs either a farmer_phone or an fpo_id")

    assessment = assess_quality(
        moisture_pct=moisture_pct,
        damage_pct=damage_pct,
        foreign_matter_pct=foreign_matter_pct,
        description=quality_description,
    )
    final_grade = normalize_grade(grade) if grade else assessment.grade
    basis = "SELF_DECLARED" if grade else assessment.basis

    lot_id = str(uuid.uuid4())
    ts = now_ts()
    status = LotStatus.PUBLISHED if publish else LotStatus.DRAFT

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO lots(
                    id, farmer_phone, fpo_id, is_aggregate, commodity, variety, grade,
                    grade_basis, moisture_pct, damage_pct, foreign_matter_pct,
                    quality_notes, photo_refs, quantity_kg, expected_price,
                    harvest_date, available_from, available_until,
                    pickup_village, pickup_district, pickup_state, storage_type,
                    status, created_at, updated_at
                ) VALUES (?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    lot_id, farmer_phone, fpo_id, match.canonical, variety, final_grade,
                    basis, moisture_pct, damage_pct, foreign_matter_pct,
                    quality_notes, json.dumps(photo_refs or []), float(quantity_kg),
                    expected_price, harvest_date, available_from, available_until,
                    pickup_village,
                    normalize_market_name(pickup_district) if pickup_district else None,
                    pickup_state, storage_type, status, ts, ts,
                ),
            )
            audit.record(
                audit.EntityType.LOT, lot_id, audit.Action.CREATED,
                actor=actor or farmer_phone, actor_role="farmer",
                new_value={
                    "commodity": match.canonical, "quantity_kg": quantity_kg,
                    "grade": final_grade, "grade_basis": basis, "status": status,
                },
                metadata={"quality_assessment": assessment.to_dict()},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    result = get_lot(lot_id)
    result["quality_assessment"] = assessment.to_dict()
    return result


def get_lot(lot_id: str, include_children: bool = False) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
            return lot_to_dict(row, include_children=include_children) if row else None
        finally:
            conn.close()


def get_child_lots(parent_lot_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM lots WHERE parent_lot_id = ? ORDER BY created_at",
                (parent_lot_id,),
            ).fetchall()
        finally:
            conn.close()
    return [lot_to_dict(r) for r in rows]


def list_lots(
    status: Optional[str] = None,
    commodity: Optional[str] = None,
    farmer_phone: Optional[str] = None,
    fpo_id: Optional[str] = None,
    district: Optional[str] = None,
    exclude_aggregated: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM lots WHERE 1=1"
    params: List[Any] = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    elif exclude_aggregated:
        # Member lots absorbed into an FPO lot would otherwise be double
        # counted alongside their parent.
        sql += " AND status != ?"
        params.append(LotStatus.AGGREGATED)
    if commodity:
        match = resolve_commodity(commodity)
        sql += " AND LOWER(commodity) = LOWER(?)"
        params.append(match.canonical if match else commodity)
    if farmer_phone:
        sql += " AND farmer_phone = ?"
        params.append(farmer_phone)
    if fpo_id:
        sql += " AND fpo_id = ?"
        params.append(fpo_id)
    if district:
        sql += " AND LOWER(pickup_district) = LOWER(?)"
        params.append(district)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    return [lot_to_dict(r) for r in rows]


def transition(
    lot_id: str,
    to_status: str,
    *,
    actor: Optional[str] = None,
    actor_role: str = "system",
    note: Optional[str] = None,
    conn=None,
) -> Dict[str, Any]:
    """
    Move a lot to a new status, refusing illegal transitions.

    Every move writes an audit event in the same transaction as the update,
    so the status and its explanation can never disagree.
    """
    def _apply(c):
        row = c.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
        if row is None:
            raise LotError(f"Unknown lot {lot_id}")

        current = row["status"]
        if current == to_status:
            return row

        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if to_status not in allowed:
            raise LotError(
                f"Cannot move lot from {current} to {to_status}. "
                f"Allowed: {', '.join(sorted(allowed)) or 'none (terminal state)'}"
            )

        c.execute(
            "UPDATE lots SET status = ?, updated_at = ? WHERE id = ?",
            (to_status, now_ts(), lot_id),
        )
        audit.record(
            audit.EntityType.LOT, lot_id, audit.Action.STATUS_CHANGED,
            actor=actor, actor_role=actor_role,
            old_value={"status": current}, new_value={"status": to_status},
            metadata={"note": note} if note else None,
            conn=c,
        )
        return row

    if conn is not None:
        _apply(conn)
    else:
        with DB_LOCK:
            own = get_connection()
            try:
                _apply(own)
                own.commit()
            finally:
                own.close()

    return get_lot(lot_id)


def publish_lot(lot_id: str, actor: Optional[str] = None) -> Dict[str, Any]:
    return transition(lot_id, LotStatus.PUBLISHED, actor=actor, actor_role="farmer")


def cancel_lot(lot_id: str, reason: str, actor: Optional[str] = None) -> Dict[str, Any]:
    return transition(lot_id, LotStatus.CANCELLED, actor=actor,
                      actor_role="farmer", note=reason)


def update_quality(
    lot_id: str,
    *,
    grade: Optional[str] = None,
    basis: str = "INSPECTED",
    moisture_pct: Optional[float] = None,
    damage_pct: Optional[float] = None,
    foreign_matter_pct: Optional[float] = None,
    notes: Optional[str] = None,
    actor: str = "inspector",
) -> Dict[str, Any]:
    """
    Record an inspection result.

    Grade basis may only move up the evidence ladder. Downgrading an
    INSPECTED grade back to a self-declared claim would let a party erase an
    inconvenient inspection.
    """
    if basis not in GRADE_BASIS_ORDER:
        raise LotError(f"Unknown grade basis '{basis}'")

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
            if row is None:
                raise LotError(f"Unknown lot {lot_id}")

            current_rank = GRADE_BASIS_ORDER.index(row["grade_basis"])
            new_rank = GRADE_BASIS_ORDER.index(basis)
            if new_rank < current_rank:
                raise LotError(
                    f"Cannot downgrade grade basis from {row['grade_basis']} to {basis}"
                )

            new_grade = normalize_grade(grade) if grade else row["grade"]
            conn.execute(
                """
                UPDATE lots SET grade = ?, grade_basis = ?, moisture_pct = ?,
                    damage_pct = ?, foreign_matter_pct = ?, quality_notes = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    new_grade, basis,
                    moisture_pct if moisture_pct is not None else row["moisture_pct"],
                    damage_pct if damage_pct is not None else row["damage_pct"],
                    foreign_matter_pct if foreign_matter_pct is not None
                    else row["foreign_matter_pct"],
                    notes or row["quality_notes"], now_ts(), lot_id,
                ),
            )
            audit.record(
                audit.EntityType.LOT, lot_id, audit.Action.UPDATED,
                actor=actor, actor_role="inspector",
                old_value={"grade": row["grade"], "grade_basis": row["grade_basis"]},
                new_value={"grade": new_grade, "grade_basis": basis},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    return get_lot(lot_id)
