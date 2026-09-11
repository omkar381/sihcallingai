"""
Buyer registry, verification and structured demand.

Replaces the hardcoded `"status": "Verified Partner"` string with a
verification state machine backed by submitted documents, plus a trust score
computed from actual transaction behaviour.

The distinction that matters: `verification_status` records what a human
reviewer confirmed about the buyer's identity and licences. The trust score
describes how they have behaved since. A buyer can be VERIFIED and still be a
poor counterparty, and a farmer deserves to hear both.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.market.normalization import normalize_market_name, resolve_commodity
from app.services.db import DB_LOCK, get_connection
from app.trade import audit
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)


class BuyerType:
    APMC_TRADER = "APMC_TRADER"
    PROCESSOR = "PROCESSOR"
    EXPORTER = "EXPORTER"
    INSTITUTIONAL = "INSTITUTIONAL"
    WHOLESALER = "WHOLESALER"
    RETAILER = "RETAILER"
    FPO = "FPO"
    OTHER = "OTHER"

    ALL = {
        APMC_TRADER, PROCESSOR, EXPORTER, INSTITUTIONAL,
        WHOLESALER, RETAILER, FPO, OTHER,
    }


class VerificationStatus:
    UNVERIFIED = "UNVERIFIED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"

    ALL = {UNVERIFIED, PENDING, VERIFIED, SUSPENDED, REJECTED}


class DocumentType:
    GSTIN = "GSTIN"
    PAN = "PAN"
    APMC_LICENCE = "APMC_LICENCE"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    BUSINESS_REG = "BUSINESS_REG"
    PHONE = "PHONE"


# Documents a buyer must have on file before verification can be granted.
# APMC traders are legally licensed, so their licence is non-negotiable.
REQUIRED_DOCUMENTS: Dict[str, set] = {
    BuyerType.APMC_TRADER: {DocumentType.APMC_LICENCE, DocumentType.PAN, DocumentType.PHONE},
    BuyerType.PROCESSOR: {DocumentType.GSTIN, DocumentType.PAN, DocumentType.PHONE},
    BuyerType.EXPORTER: {DocumentType.GSTIN, DocumentType.PAN, DocumentType.PHONE},
    BuyerType.INSTITUTIONAL: {DocumentType.GSTIN, DocumentType.BUSINESS_REG, DocumentType.PHONE},
    BuyerType.WHOLESALER: {DocumentType.GSTIN, DocumentType.PAN, DocumentType.PHONE},
    BuyerType.RETAILER: {DocumentType.PAN, DocumentType.PHONE},
    BuyerType.FPO: {DocumentType.BUSINESS_REG, DocumentType.PAN, DocumentType.PHONE},
    BuyerType.OTHER: {DocumentType.PAN, DocumentType.PHONE},
}


class BuyerError(ValueError):
    """Invalid buyer operation."""


# ---------------------------------------------------------------------------
# Trust scoring
# ---------------------------------------------------------------------------

@dataclass
class TrustProfile:
    """
    How a buyer has actually behaved, computed from their transaction record.

    Never hardcoded, and never conflated with verification status.
    """

    score: int
    band: str
    completed_transactions: int
    total_transactions: int
    completion_rate: Optional[float]
    avg_payment_days: Optional[float]
    payment_defaults: int
    disputes: int
    is_new: bool
    factors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "completed_transactions": self.completed_transactions,
            "total_transactions": self.total_transactions,
            "completion_rate": (
                round(self.completion_rate, 3) if self.completion_rate is not None else None
            ),
            "avg_payment_days": (
                round(self.avg_payment_days, 1) if self.avg_payment_days is not None else None
            ),
            "payment_defaults": self.payment_defaults,
            "disputes": self.disputes,
            "is_new": self.is_new,
            "factors": self.factors,
        }


def compute_trust(row) -> TrustProfile:
    """
    Score a buyer 0-100 from their record.

    A buyer with no history scores 50 and is explicitly flagged `is_new`
    rather than being scored as if it were good or bad. Pretending a brand
    new buyer is trustworthy is how farmers get defrauded; pretending they
    are untrustworthy would keep legitimate new buyers out of the market.
    """
    total = int(row["total_transactions"] or 0)
    completed = int(row["completed_transactions"] or 0)
    defaults = int(row["payment_default_count"] or 0)
    disputes = int(row["dispute_count"] or 0)
    samples = int(row["payment_samples"] or 0)
    total_days = float(row["total_payment_days"] or 0.0)

    avg_payment_days = (total_days / samples) if samples else None
    completion_rate = (completed / total) if total else None
    is_new = total == 0

    factors: List[str] = []
    score = 50.0

    if is_new:
        factors.append("No completed transactions yet on this platform")
    else:
        score += (completion_rate - 0.5) * 40.0
        factors.append(
            f"{completed} of {total} transactions completed "
            f"({completion_rate * 100:.0f}%)"
        )

    if avg_payment_days is not None:
        if avg_payment_days <= 2:
            score += 15
            factors.append(f"Pays quickly, averaging {avg_payment_days:.1f} days")
        elif avg_payment_days <= 7:
            score += 5
            factors.append(f"Pays within terms, averaging {avg_payment_days:.1f} days")
        else:
            score -= 10
            factors.append(f"Slow to pay, averaging {avg_payment_days:.1f} days")

    if defaults:
        # Defaults are weighted heavily: this is the risk a farmer most needs
        # protecting from, and it should be hard to outscore.
        score -= defaults * 20
        factors.append(f"{defaults} payment default(s) on record")

    if disputes:
        score -= disputes * 5
        factors.append(f"{disputes} dispute(s) raised against them")

    if row["verification_status"] == VerificationStatus.SUSPENDED:
        score = min(score, 10)
        factors.append("Currently suspended")
    elif row["verification_status"] == VerificationStatus.VERIFIED:
        score += 10
        factors.append("Identity and licences verified")

    score = max(0, min(100, int(round(score))))

    if row["verification_status"] in (VerificationStatus.SUSPENDED, VerificationStatus.REJECTED):
        band = "DO_NOT_TRADE"
    elif is_new:
        band = "NEW"
    elif score >= 75:
        band = "STRONG"
    elif score >= 55:
        band = "FAIR"
    elif score >= 35:
        band = "WEAK"
    else:
        band = "POOR"

    return TrustProfile(
        score=score,
        band=band,
        completed_transactions=completed,
        total_transactions=total,
        completion_rate=completion_rate,
        avg_payment_days=avg_payment_days,
        payment_defaults=defaults,
        disputes=disputes,
        is_new=is_new,
        factors=factors,
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _split_list(text: Optional[str]) -> List[str]:
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return [p.strip() for p in text.split(",") if p.strip()]


def buyer_to_dict(row, include_trust: bool = True) -> Dict[str, Any]:
    payload = {
        "id": row["id"],
        "name": row["name"],
        "buyer_type": row["buyer_type"],
        "phone": row["phone"],
        "district": row["district"],
        "state": row["state"],
        "service_districts": _split_list(row["service_districts"]),
        "verification_status": row["verification_status"],
        "verified_at": row["verified_at"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }
    if include_trust:
        trust = compute_trust(row)
        payload["trust"] = trust.to_dict()
        payload["badge"] = describe_badge(row["verification_status"], trust)
    return payload


def describe_badge(verification_status: str, trust: TrustProfile) -> Dict[str, Any]:
    """
    The farmer-facing trust summary.

    Computed from state, never a literal string. `label` is safe to speak on
    a call; `caution` is present whenever the farmer should be careful.
    """
    if verification_status == VerificationStatus.VERIFIED:
        if trust.band == "STRONG":
            label, caution = "Verified buyer, strong payment record", None
        elif trust.band == "NEW":
            label, caution = (
                "Verified buyer, no trading history yet",
                "This buyer is verified but has not completed a transaction here before.",
            )
        elif trust.band in ("WEAK", "POOR"):
            label, caution = (
                "Verified buyer, weak payment record",
                "This buyer is verified but has a poor payment history. Consider asking for advance payment.",
            )
        else:
            label, caution = "Verified buyer", None
    elif verification_status == VerificationStatus.PENDING:
        label, caution = (
            "Verification in progress",
            "This buyer's documents are still being checked. Take care until verification completes.",
        )
    elif verification_status == VerificationStatus.SUSPENDED:
        label, caution = (
            "Suspended buyer",
            "This buyer is suspended. Do not trade with them.",
        )
    elif verification_status == VerificationStatus.REJECTED:
        label, caution = (
            "Rejected buyer",
            "This buyer failed verification. Do not trade with them.",
        )
    else:
        label, caution = (
            "Unverified buyer",
            "This buyer has not been verified. Do not share produce before payment terms are agreed.",
        )

    return {
        "label": label,
        "verification_status": verification_status,
        "trust_band": trust.band,
        "trust_score": trust.score,
        "caution": caution,
        "safe_to_recommend": (
            verification_status == VerificationStatus.VERIFIED
            and trust.band not in ("POOR", "DO_NOT_TRADE")
        ),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def create_buyer(
    name: str,
    buyer_type: str,
    *,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    gstin: Optional[str] = None,
    pan: Optional[str] = None,
    apmc_licence_no: Optional[str] = None,
    district: Optional[str] = None,
    state: Optional[str] = None,
    service_districts: Optional[List[str]] = None,
    address: Optional[str] = None,
    actor: str = "system",
) -> Dict[str, Any]:
    """Register a buyer. Always starts UNVERIFIED, whatever was supplied."""
    name = (name or "").strip()
    if not name:
        raise BuyerError("Buyer name is required")
    if buyer_type not in BuyerType.ALL:
        raise BuyerError(f"Unknown buyer_type '{buyer_type}'")

    buyer_id = str(uuid.uuid4())
    ts = now_ts()
    districts = [normalize_market_name(d) or d for d in (service_districts or [])]

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO buyers(
                    id, name, buyer_type, phone, email, gstin, pan, apmc_licence_no,
                    address, district, state, service_districts,
                    verification_status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    buyer_id, name, buyer_type, phone, email, gstin, pan, apmc_licence_no,
                    address, normalize_market_name(district) if district else None,
                    state, json.dumps(districts),
                    VerificationStatus.UNVERIFIED, ts, ts,
                ),
            )
            audit.record(
                audit.EntityType.BUYER, buyer_id, audit.Action.CREATED,
                actor=actor, actor_role="admin",
                new_value={"name": name, "buyer_type": buyer_type},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info("Buyer registered: %s (%s)", name, buyer_id)
    return get_buyer(buyer_id)


def get_buyer(buyer_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute("SELECT * FROM buyers WHERE id = ?", (buyer_id,)).fetchone()
            return buyer_to_dict(row) if row else None
        finally:
            conn.close()


def list_buyers(
    verification_status: Optional[str] = None,
    commodity: Optional[str] = None,
    district: Optional[str] = None,
    active_only: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM buyers WHERE 1=1"
    params: List[Any] = []
    if active_only:
        sql += " AND is_active = 1"
    if verification_status:
        sql += " AND verification_status = ?"
        params.append(verification_status)
    if district:
        sql += " AND (LOWER(district) = LOWER(?) OR service_districts LIKE ?)"
        params.extend([district, f"%{district}%"])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    buyers = [buyer_to_dict(r) for r in rows]

    if commodity:
        canonical = resolve_commodity(commodity)
        if canonical:
            wanted = {d["buyer_id"] for d in list_open_demand(commodity=canonical.canonical)}
            buyers = [b for b in buyers if b["id"] in wanted]

    return buyers


# ---------------------------------------------------------------------------
# Documents and verification
# ---------------------------------------------------------------------------

def submit_document(
    buyer_id: str,
    doc_type: str,
    doc_number: Optional[str] = None,
    file_ref: Optional[str] = None,
    actor: str = "buyer",
) -> Dict[str, Any]:
    """Attach supporting evidence and move the buyer to PENDING."""
    if not get_buyer(buyer_id):
        raise BuyerError(f"Unknown buyer {buyer_id}")

    doc_id = str(uuid.uuid4())
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO buyer_documents(id, buyer_id, doc_type, doc_number, file_ref,
                                            status, created_at)
                VALUES (?,?,?,?,?, 'SUBMITTED', ?)
                """,
                (doc_id, buyer_id, doc_type, doc_number, file_ref, now_ts()),
            )
            conn.execute(
                """
                UPDATE buyers SET verification_status = ?, updated_at = ?
                WHERE id = ? AND verification_status = ?
                """,
                (VerificationStatus.PENDING, now_ts(), buyer_id, VerificationStatus.UNVERIFIED),
            )
            audit.record(
                audit.EntityType.BUYER, buyer_id, audit.Action.UPDATED,
                actor=actor, actor_role="buyer",
                new_value={"document_submitted": doc_type},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    return {"document_id": doc_id, "buyer_id": buyer_id, "doc_type": doc_type, "status": "SUBMITTED"}


def get_documents(buyer_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM buyer_documents WHERE buyer_id = ? ORDER BY created_at",
                (buyer_id,),
            ).fetchall()
        finally:
            conn.close()
    return [
        {
            "id": r["id"], "doc_type": r["doc_type"], "doc_number": r["doc_number"],
            "status": r["status"], "reviewed_by": r["reviewed_by"],
            "reviewed_at": r["reviewed_at"], "notes": r["notes"],
        }
        for r in rows
    ]


def verification_readiness(buyer_id: str) -> Dict[str, Any]:
    """
    Whether a buyer can be verified, and precisely what is missing.

    Returning the gap rather than a bare "no" is what lets an admin screen be
    actionable instead of merely obstructive.
    """
    buyer = get_buyer(buyer_id)
    if not buyer:
        raise BuyerError(f"Unknown buyer {buyer_id}")

    required = REQUIRED_DOCUMENTS.get(buyer["buyer_type"], REQUIRED_DOCUMENTS[BuyerType.OTHER])
    approved = {
        d["doc_type"] for d in get_documents(buyer_id)
        if d["status"] in ("SUBMITTED", "APPROVED")
    }
    missing = sorted(required - approved)

    return {
        "buyer_id": buyer_id,
        "buyer_type": buyer["buyer_type"],
        "required_documents": sorted(required),
        "documents_on_file": sorted(approved),
        "missing_documents": missing,
        "can_verify": not missing,
    }


def verify_buyer(buyer_id: str, verified_by: str, force: bool = False) -> Dict[str, Any]:
    """
    Grant verification.

    Refuses when required documents are missing unless `force` is passed, so
    verification cannot quietly become a rubber stamp. A forced verification
    is recorded as such in the audit trail.
    """
    readiness = verification_readiness(buyer_id)
    if not readiness["can_verify"] and not force:
        raise BuyerError(
            "Cannot verify: missing documents "
            + ", ".join(readiness["missing_documents"])
        )

    ts = now_ts()
    with DB_LOCK:
        conn = get_connection()
        try:
            old = conn.execute(
                "SELECT verification_status FROM buyers WHERE id = ?", (buyer_id,)
            ).fetchone()
            conn.execute(
                """
                UPDATE buyers
                SET verification_status = ?, verified_at = ?, verified_by = ?,
                    rejection_reason = NULL, updated_at = ?
                WHERE id = ?
                """,
                (VerificationStatus.VERIFIED, ts, verified_by, ts, buyer_id),
            )
            conn.execute(
                "UPDATE buyer_documents SET status = 'APPROVED', reviewed_by = ?, reviewed_at = ? "
                "WHERE buyer_id = ? AND status = 'SUBMITTED'",
                (verified_by, ts, buyer_id),
            )
            audit.record(
                audit.EntityType.BUYER, buyer_id, audit.Action.VERIFIED,
                actor=verified_by, actor_role="admin",
                old_value={"verification_status": old["verification_status"] if old else None},
                new_value={"verification_status": VerificationStatus.VERIFIED},
                metadata={"forced": force, "missing_documents": readiness["missing_documents"]},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    logger.info("Buyer %s verified by %s (forced=%s)", buyer_id, verified_by, force)
    return get_buyer(buyer_id)


def _set_status(
    buyer_id: str, status: str, actor: str, action: str, reason: Optional[str] = None
) -> Dict[str, Any]:
    with DB_LOCK:
        conn = get_connection()
        try:
            old = conn.execute(
                "SELECT verification_status FROM buyers WHERE id = ?", (buyer_id,)
            ).fetchone()
            if not old:
                raise BuyerError(f"Unknown buyer {buyer_id}")
            conn.execute(
                "UPDATE buyers SET verification_status = ?, rejection_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (status, reason, now_ts(), buyer_id),
            )
            audit.record(
                audit.EntityType.BUYER, buyer_id, action,
                actor=actor, actor_role="admin",
                old_value={"verification_status": old["verification_status"]},
                new_value={"verification_status": status, "reason": reason},
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()
    return get_buyer(buyer_id)


def suspend_buyer(buyer_id: str, reason: str, actor: str = "admin") -> Dict[str, Any]:
    """Suspend a buyer. Their open demand is closed so no new matches form."""
    result = _set_status(buyer_id, VerificationStatus.SUSPENDED, actor,
                         audit.Action.SUSPENDED, reason)
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE buyer_demand SET status = 'WITHDRAWN', updated_at = ? "
                "WHERE buyer_id = ? AND status = 'OPEN'",
                (now_ts(), buyer_id),
            )
            conn.commit()
        finally:
            conn.close()
    return result


def reject_buyer(buyer_id: str, reason: str, actor: str = "admin") -> Dict[str, Any]:
    return _set_status(buyer_id, VerificationStatus.REJECTED, actor,
                       audit.Action.REJECTED, reason)


def record_transaction_outcome(
    buyer_id: str,
    *,
    completed: bool,
    payment_days: Optional[float] = None,
    defaulted: bool = False,
) -> None:
    """
    Fold a finished transaction into the buyer's trust record.

    Called by the payment layer on settlement. This is what makes the trust
    score reflect reality rather than an administrator's opinion.
    """
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE buyers SET
                    total_transactions = total_transactions + 1,
                    completed_transactions = completed_transactions + ?,
                    payment_default_count = payment_default_count + ?,
                    total_payment_days = total_payment_days + ?,
                    payment_samples = payment_samples + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if completed else 0,
                    1 if defaulted else 0,
                    float(payment_days or 0.0),
                    1 if payment_days is not None else 0,
                    now_ts(), buyer_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------

def post_demand(
    buyer_id: str,
    commodity: str,
    quantity_needed_kg: float,
    *,
    variety: Optional[str] = None,
    min_grade: Optional[str] = None,
    min_lot_kg: Optional[float] = None,
    price_offered: Optional[float] = None,
    delivery_district: Optional[str] = None,
    delivery_state: Optional[str] = None,
    max_distance_km: Optional[float] = None,
    max_moisture_pct: Optional[float] = None,
    quality_spec: Optional[Dict[str, Any]] = None,
    payment_terms_days: int = 7,
    window_start: Optional[int] = None,
    window_end: Optional[int] = None,
    actor: str = "buyer",
) -> Dict[str, Any]:
    """
    Post what a buyer wants to purchase.

    Only VERIFIED buyers may post demand. An unverified buyer's demand would
    otherwise surface in farmer-facing matching with nothing behind it.
    """
    buyer = get_buyer(buyer_id)
    if not buyer:
        raise BuyerError(f"Unknown buyer {buyer_id}")
    if buyer["verification_status"] != VerificationStatus.VERIFIED:
        raise BuyerError(
            f"Buyer must be VERIFIED to post demand (currently {buyer['verification_status']})"
        )

    match = resolve_commodity(commodity)
    if match is None:
        raise BuyerError(f"'{commodity}' is not a recognised commodity")
    if quantity_needed_kg <= 0:
        raise BuyerError("quantity_needed_kg must be positive")

    demand_id = str(uuid.uuid4())
    ts = now_ts()

    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO buyer_demand(
                    id, buyer_id, commodity, variety, min_grade, quantity_needed_kg,
                    min_lot_kg, price_offered, delivery_district, delivery_state,
                    max_distance_km, window_start, window_end, max_moisture_pct,
                    quality_spec, payment_terms_days, status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN', ?,?)
                """,
                (
                    demand_id, buyer_id, match.canonical, variety, min_grade,
                    float(quantity_needed_kg), min_lot_kg, price_offered,
                    normalize_market_name(delivery_district) if delivery_district else None,
                    delivery_state, max_distance_km, window_start, window_end,
                    max_moisture_pct, json.dumps(quality_spec or {}),
                    int(payment_terms_days), ts, ts,
                ),
            )
            audit.record(
                audit.EntityType.BUYER_DEMAND, demand_id, audit.Action.CREATED,
                actor=actor, actor_role="buyer",
                new_value={
                    "buyer_id": buyer_id, "commodity": match.canonical,
                    "quantity_needed_kg": quantity_needed_kg,
                },
                conn=conn,
            )
            conn.commit()
        finally:
            conn.close()

    return get_demand(demand_id)


def demand_to_dict(row, buyer_row=None) -> Dict[str, Any]:
    remaining = max(0.0, float(row["quantity_needed_kg"]) - float(row["quantity_filled_kg"] or 0))
    payload = {
        "id": row["id"],
        "buyer_id": row["buyer_id"],
        "commodity": row["commodity"],
        "variety": row["variety"],
        "min_grade": row["min_grade"],
        "quantity_needed_kg": row["quantity_needed_kg"],
        "quantity_filled_kg": row["quantity_filled_kg"],
        "quantity_remaining_kg": remaining,
        "min_lot_kg": row["min_lot_kg"],
        "price_offered": row["price_offered"],
        "price_unit": row["price_unit"],
        "delivery_district": row["delivery_district"],
        "delivery_state": row["delivery_state"],
        "max_distance_km": row["max_distance_km"],
        "max_moisture_pct": row["max_moisture_pct"],
        "payment_terms_days": row["payment_terms_days"],
        "window_start": row["window_start"],
        "window_end": row["window_end"],
        "status": row["status"],
        "created_at": row["created_at"],
    }
    if buyer_row is not None:
        payload["buyer"] = buyer_to_dict(buyer_row)
    return payload


def get_demand(demand_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM buyer_demand WHERE id = ?", (demand_id,)
            ).fetchone()
            if not row:
                return None
            buyer_row = conn.execute(
                "SELECT * FROM buyers WHERE id = ?", (row["buyer_id"],)
            ).fetchone()
            return demand_to_dict(row, buyer_row)
        finally:
            conn.close()


def list_open_demand(
    commodity: Optional[str] = None,
    district: Optional[str] = None,
    verified_only: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Open demand, joined to the buyer so callers see who is behind it.

    Defaults to verified buyers only: surfacing unverified demand to a farmer
    is how they end up trucking produce to someone who will not pay.
    """
    sql = """
        SELECT d.*, b.id AS b_id FROM buyer_demand d
        JOIN buyers b ON b.id = d.buyer_id
        WHERE d.status = 'OPEN' AND b.is_active = 1
          AND d.quantity_filled_kg < d.quantity_needed_kg
    """
    params: List[Any] = []
    if verified_only:
        sql += " AND b.verification_status = ?"
        params.append(VerificationStatus.VERIFIED)
    if commodity:
        match = resolve_commodity(commodity)
        sql += " AND LOWER(d.commodity) = LOWER(?)"
        params.append(match.canonical if match else commodity)
    if district:
        sql += " AND (d.delivery_district IS NULL OR LOWER(d.delivery_district) = LOWER(?))"
        params.append(district)
    sql += " ORDER BY d.price_offered DESC NULLS LAST, d.created_at DESC LIMIT ?"
    params.append(limit)

    with DB_LOCK:
        conn = get_connection()
        try:
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception:
                # Older SQLite builds lack NULLS LAST.
                rows = conn.execute(
                    sql.replace(" NULLS LAST", ""), params
                ).fetchall()
            out = []
            for r in rows:
                buyer_row = conn.execute(
                    "SELECT * FROM buyers WHERE id = ?", (r["buyer_id"],)
                ).fetchone()
                out.append(demand_to_dict(r, buyer_row))
            return out
        finally:
            conn.close()


def fill_demand(demand_id: str, quantity_kg: float, conn=None) -> None:
    """Record quantity committed against a demand, closing it when filled."""
    sql_update = """
        UPDATE buyer_demand
        SET quantity_filled_kg = MIN(quantity_needed_kg, quantity_filled_kg + ?),
            updated_at = ?
        WHERE id = ?
    """
    sql_close = """
        UPDATE buyer_demand SET status = 'FILLED', updated_at = ?
        WHERE id = ? AND quantity_filled_kg >= quantity_needed_kg
    """
    ts = now_ts()

    def _run(c):
        c.execute(sql_update, (float(quantity_kg), ts, demand_id))
        c.execute(sql_close, (ts, demand_id))

    if conn is not None:
        _run(conn)
        return

    with DB_LOCK:
        own = get_connection()
        try:
            _run(own)
            own.commit()
        finally:
            own.close()
