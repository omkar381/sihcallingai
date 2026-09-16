"""
Marketplace REST API.

Thin HTTP layer over the trade services. Route handlers validate and
delegate; business rules live in `app.trade.*` so the voice agent, the API and
the dashboard cannot drift apart.

Domain errors (`BuyerError`, `LotError`, `OfferError`, `PaymentError`,
`DisputeError`) map to 400 with the service's own message. Those messages are
written to be read by a person - "Cannot move lot from PUBLISHED to PAID.
Allowed: ..." - so surfacing them directly is better than a generic failure.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.security import rate_limit_queries, require_api_key
from app.trade import audit, buyers, disputes, fpo, lots, matching, offers, payments
from app.trade.schema import init_trade_tables

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trade", tags=["marketplace"])

DOMAIN_ERRORS = (
    buyers.BuyerError, fpo.FpoError, lots.LotError,
    offers.OfferError, payments.PaymentError, disputes.DisputeError,
)


def _handle(fn, *args, **kwargs):
    """Run a service call, turning domain errors into readable 400s."""
    try:
        return fn(*args, **kwargs)
    except DOMAIN_ERRORS as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _found(value, what: str):
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


# ---------------------------------------------------------------------------
# Buyers
# ---------------------------------------------------------------------------

@router.get("/buyers", dependencies=[Depends(rate_limit_queries)])
async def list_buyers(
    verification_status: Optional[str] = Query(None),
    commodity: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Registered buyers with their computed trust badge."""
    rows = buyers.list_buyers(
        verification_status=verification_status, commodity=commodity,
        district=district, limit=limit,
    )
    return {"count": len(rows), "buyers": rows}


@router.get("/buyers/{buyer_id}", dependencies=[Depends(rate_limit_queries)])
async def get_buyer(buyer_id: str):
    buyer = _found(buyers.get_buyer(buyer_id), "Buyer")
    buyer["documents"] = buyers.get_documents(buyer_id)
    buyer["verification_readiness"] = buyers.verification_readiness(buyer_id)
    return buyer


@router.post("/buyers", dependencies=[Depends(require_api_key)])
async def create_buyer(payload: Dict[str, Any] = Body(...)):
    """Register a buyer. Always starts UNVERIFIED."""
    init_trade_tables()
    return _handle(
        buyers.create_buyer,
        payload.get("name", ""), payload.get("buyer_type", buyers.BuyerType.OTHER),
        phone=payload.get("phone"), email=payload.get("email"),
        gstin=payload.get("gstin"), pan=payload.get("pan"),
        apmc_licence_no=payload.get("apmc_licence_no"),
        district=payload.get("district"), state=payload.get("state"),
        service_districts=payload.get("service_districts"),
        address=payload.get("address"),
    )


@router.post("/buyers/{buyer_id}/documents", dependencies=[Depends(require_api_key)])
async def submit_document(buyer_id: str, payload: Dict[str, Any] = Body(...)):
    return _handle(
        buyers.submit_document, buyer_id,
        payload.get("doc_type", ""), payload.get("doc_number"), payload.get("file_ref"),
    )


@router.post("/buyers/{buyer_id}/verify", dependencies=[Depends(require_api_key)])
async def verify_buyer(
    buyer_id: str,
    verified_by: str = Query("admin"),
    force: bool = Query(False, description="Verify despite missing documents; audited as forced"),
):
    return _handle(buyers.verify_buyer, buyer_id, verified_by=verified_by, force=force)


@router.post("/buyers/{buyer_id}/suspend", dependencies=[Depends(require_api_key)])
async def suspend_buyer(buyer_id: str, reason: str = Query(...)):
    return _handle(buyers.suspend_buyer, buyer_id, reason)


# ---------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------

@router.get("/demand", dependencies=[Depends(rate_limit_queries)])
async def list_demand(
    commodity: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    verified_only: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
):
    """Open buyer demand. Defaults to verified buyers only."""
    rows = buyers.list_open_demand(
        commodity=commodity, district=district,
        verified_only=verified_only, limit=limit,
    )
    return {"count": len(rows), "demand": rows}


@router.post("/demand", dependencies=[Depends(require_api_key)])
async def post_demand(payload: Dict[str, Any] = Body(...)):
    """Post what a buyer wants to purchase. Verified buyers only."""
    return _handle(
        buyers.post_demand,
        payload.get("buyer_id", ""), payload.get("commodity", ""),
        float(payload.get("quantity_needed_kg", 0)),
        variety=payload.get("variety"), min_grade=payload.get("min_grade"),
        min_lot_kg=payload.get("min_lot_kg"), price_offered=payload.get("price_offered"),
        delivery_district=payload.get("delivery_district"),
        delivery_state=payload.get("delivery_state"),
        max_distance_km=payload.get("max_distance_km"),
        max_moisture_pct=payload.get("max_moisture_pct"),
        quality_spec=payload.get("quality_spec"),
        payment_terms_days=int(payload.get("payment_terms_days", 7)),
    )


# ---------------------------------------------------------------------------
# FPOs
# ---------------------------------------------------------------------------

@router.get("/fpos", dependencies=[Depends(rate_limit_queries)])
async def list_fpos(district: Optional[str] = Query(None)):
    rows = fpo.list_fpos(district=district)
    return {"count": len(rows), "fpos": rows}


@router.get("/fpos/{fpo_id}", dependencies=[Depends(rate_limit_queries)])
async def get_fpo(fpo_id: str):
    org = _found(fpo.get_fpo(fpo_id), "FPO")
    org["members"] = fpo.list_members(fpo_id)
    return org


@router.post("/fpos", dependencies=[Depends(require_api_key)])
async def create_fpo(payload: Dict[str, Any] = Body(...)):
    init_trade_tables()
    return _handle(
        fpo.create_fpo, payload.get("name", ""),
        registration_no=payload.get("registration_no"),
        district=payload.get("district"), state=payload.get("state"),
        contact_phone=payload.get("contact_phone"),
    )


@router.post("/fpos/{fpo_id}/members", dependencies=[Depends(require_api_key)])
async def add_member(fpo_id: str, payload: Dict[str, Any] = Body(...)):
    return _handle(
        fpo.add_member, fpo_id, payload.get("farmer_phone", ""),
        payload.get("role", "MEMBER"),
    )


@router.get("/fpos/{fpo_id}/aggregation-groups", dependencies=[Depends(rate_limit_queries)])
async def aggregation_groups(
    fpo_id: str,
    commodity: Optional[str] = Query(None),
    price_per_quintal: Optional[float] = Query(None, description="To estimate the benefit"),
    distance_km: float = Query(100.0, ge=0, le=1000),
    location: Optional[str] = Query(
        None, description="Price each pool at the best market near here (preferred)"
    ),
):
    """
    Compatible pools of member lots, with the rupee benefit of pooling each.

    Groups never mix commodity, grade or variety.
    """
    groups = _handle(fpo.find_aggregation_groups, fpo_id, commodity)
    out: List[Dict[str, Any]] = []
    for group in groups:
        payload = group.to_dict()
        if location and group.lots:
            try:
                benefit = fpo.estimate_pool_benefit_at_market(group, location)
                if benefit:
                    payload["benefit"] = benefit
            except Exception as exc:
                logger.warning("Benefit estimate failed: %s", exc)
        elif price_per_quintal and group.lots:
            try:
                payload["benefit"] = fpo.estimate_aggregation_benefit(
                    group, price_per_quintal, distance_km
                )
            except Exception as exc:
                logger.warning("Benefit estimate failed: %s", exc)
        out.append(payload)
    return {"fpo_id": fpo_id, "count": len(out), "groups": out}


@router.post("/fpos/{fpo_id}/aggregate", dependencies=[Depends(require_api_key)])
async def aggregate(fpo_id: str, payload: Dict[str, Any] = Body(...)):
    """Pool compatible member lots into one buyer-ready consignment."""
    return _handle(
        fpo.create_aggregate_lot, fpo_id, payload.get("lot_ids", []),
        expected_price=payload.get("expected_price"),
        pickup_village=payload.get("pickup_village"),
        pickup_district=payload.get("pickup_district"),
    )


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------

@router.get("/lots", dependencies=[Depends(rate_limit_queries)])
async def list_lots(
    status: Optional[str] = Query(None),
    commodity: Optional[str] = Query(None),
    farmer_phone: Optional[str] = Query(None),
    fpo_id: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    rows = lots.list_lots(
        status=status, commodity=commodity, farmer_phone=farmer_phone,
        fpo_id=fpo_id, district=district, limit=limit,
    )
    return {"count": len(rows), "lots": rows}


@router.get("/lots/{lot_id}", dependencies=[Depends(rate_limit_queries)])
async def get_lot(lot_id: str):
    """A lot with its offers, payments, disputes and full audit history."""
    lot = _found(lots.get_lot(lot_id, include_children=True), "Lot")
    lot["offers"] = offers.list_offers(lot_id=lot_id)
    lot["payments"] = payments.list_payments(lot_id=lot_id)
    lot["disputes"] = disputes.list_disputes(lot_id=lot_id)
    lot["audit_trail"] = [e.to_dict() for e in audit.history(audit.EntityType.LOT, lot_id)]
    return lot


@router.post("/lots", dependencies=[Depends(require_api_key)])
async def create_lot(payload: Dict[str, Any] = Body(...)):
    init_trade_tables()
    return _handle(
        lots.create_lot,
        payload.get("commodity", ""), float(payload.get("quantity_kg", 0)),
        farmer_phone=payload.get("farmer_phone"), fpo_id=payload.get("fpo_id"),
        variety=payload.get("variety"), grade=payload.get("grade"),
        moisture_pct=payload.get("moisture_pct"), damage_pct=payload.get("damage_pct"),
        foreign_matter_pct=payload.get("foreign_matter_pct"),
        quality_description=payload.get("quality_description"),
        quality_notes=payload.get("quality_notes"),
        expected_price=payload.get("expected_price"),
        harvest_date=payload.get("harvest_date"),
        pickup_village=payload.get("pickup_village"),
        pickup_district=payload.get("pickup_district"),
        pickup_state=payload.get("pickup_state"),
        storage_type=payload.get("storage_type"),
        publish=bool(payload.get("publish", False)),
    )


@router.post("/lots/{lot_id}/publish", dependencies=[Depends(require_api_key)])
async def publish_lot(lot_id: str, actor: Optional[str] = Query(None)):
    return _handle(lots.publish_lot, lot_id, actor)


@router.post("/lots/{lot_id}/quality", dependencies=[Depends(require_api_key)])
async def update_quality(lot_id: str, payload: Dict[str, Any] = Body(...)):
    """Record an inspection. Grade basis may only move up the evidence ladder."""
    return _handle(
        lots.update_quality, lot_id,
        grade=payload.get("grade"), basis=payload.get("basis", "INSPECTED"),
        moisture_pct=payload.get("moisture_pct"), damage_pct=payload.get("damage_pct"),
        foreign_matter_pct=payload.get("foreign_matter_pct"),
        notes=payload.get("notes"), actor=payload.get("actor", "inspector"),
    )


@router.post("/lots/{lot_id}/status", dependencies=[Depends(require_api_key)])
async def change_status(lot_id: str, to_status: str = Query(...), actor: Optional[str] = Query(None)):
    return _handle(lots.transition, lot_id, to_status, actor=actor, actor_role="admin")


@router.get("/lots/{lot_id}/matches", dependencies=[Depends(rate_limit_queries)])
async def lot_matches(lot_id: str, limit: int = Query(10, ge=1, le=50)):
    """Ranked, explained buyer matches for a lot."""
    lot = _found(lots.get_lot(lot_id), "Lot")
    found = matching.find_matches(lot, limit=limit)
    return {
        "lot_id": lot_id,
        "count": len(found),
        "matches": [m.to_dict() for m in found],
        "note": (
            "No buyer demand currently matches this lot."
            if not found else None
        ),
    }


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

@router.get("/offers", dependencies=[Depends(rate_limit_queries)])
async def list_offers(
    lot_id: Optional[str] = Query(None),
    buyer_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    rows = offers.list_offers(lot_id=lot_id, buyer_id=buyer_id, status=status)
    return {"count": len(rows), "offers": rows}


@router.post("/offers", dependencies=[Depends(require_api_key)])
async def place_offer(payload: Dict[str, Any] = Body(...)):
    return _handle(
        offers.place_offer,
        payload.get("lot_id", ""), payload.get("buyer_id", ""),
        float(payload.get("price_per_quintal", 0)),
        quantity_kg=payload.get("quantity_kg"), demand_id=payload.get("demand_id"),
        payment_terms_days=int(payload.get("payment_terms_days", 7)),
        conditions=payload.get("conditions"),
        expires_in_hours=int(payload.get("expires_in_hours", 48)),
    )


@router.post("/offers/{offer_id}/accept", dependencies=[Depends(require_api_key)])
async def accept_offer(offer_id: str, actor: Optional[str] = Query(None)):
    """Accept an offer: commits the lot, supersedes rivals, creates the payment."""
    return _handle(offers.accept_offer, offer_id, actor=actor, actor_role="farmer")


@router.post("/offers/{offer_id}/reject", dependencies=[Depends(require_api_key)])
async def reject_offer(offer_id: str, reason: str = Query(""), actor: Optional[str] = Query(None)):
    return _handle(offers.reject_offer, offer_id, reason, actor)


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@router.get("/payments", dependencies=[Depends(rate_limit_queries)])
async def list_payments(
    lot_id: Optional[str] = Query(None),
    buyer_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    overdue_only: bool = Query(False),
):
    rows = payments.list_payments(
        lot_id=lot_id, buyer_id=buyer_id, status=status, overdue_only=overdue_only
    )
    return {"count": len(rows), "payments": rows}


@router.get("/payments/{payment_id}", dependencies=[Depends(rate_limit_queries)])
async def get_payment(payment_id: str):
    payment = _found(payments.get_payment(payment_id), "Payment")
    payment["history"] = payments.payment_history(payment_id)
    return payment


@router.post("/payments/{payment_id}/{action}", dependencies=[Depends(require_api_key)])
async def payment_action(
    payment_id: str,
    action: str,
    actor: Optional[str] = Query(None),
    method: str = Query("UPI"),
    utr_ref: Optional[str] = Query(None),
    reason: str = Query(""),
):
    """Advance a payment: initiate, escrow, confirm-delivery, release, fail or refund."""
    handlers = {
        "initiate": lambda: payments.initiate(payment_id, method, actor),
        "escrow": lambda: payments.mark_escrowed(payment_id, utr_ref, actor),
        "confirm-delivery": lambda: payments.confirm_delivery(payment_id, actor),
        "release": lambda: payments.release(payment_id, actor),
        "fail": lambda: payments.mark_failed(payment_id, reason or "unspecified", actor),
        "refund": lambda: payments.refund(payment_id, reason or "unspecified", actor),
    }
    if action not in handlers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action '{action}'. Valid: {', '.join(handlers)}",
        )
    return _handle(handlers[action])


@router.get("/lots/{lot_id}/settlement", dependencies=[Depends(rate_limit_queries)])
async def settlement(lot_id: str):
    """Who gets paid what, including the FPO split by weight."""
    return payments.settlement_summary(lot_id)


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

@router.get("/disputes", dependencies=[Depends(rate_limit_queries)])
async def list_disputes(
    status: Optional[str] = Query(None),
    lot_id: Optional[str] = Query(None),
    open_only: bool = Query(False),
):
    rows = disputes.list_disputes(status=status, lot_id=lot_id, open_only=open_only)
    return {"count": len(rows), "disputes": rows}


@router.get("/disputes/statistics", dependencies=[Depends(rate_limit_queries)])
async def dispute_statistics():
    return disputes.dispute_statistics()


@router.get("/disputes/{dispute_id}", dependencies=[Depends(rate_limit_queries)])
async def get_dispute(dispute_id: str):
    dispute = _found(disputes.get_dispute(dispute_id), "Dispute")
    dispute["history"] = disputes.dispute_history(dispute_id)
    return dispute


@router.post("/disputes", dependencies=[Depends(require_api_key)])
async def open_dispute(payload: Dict[str, Any] = Body(...)):
    """Raise a dispute. Freezes any associated payment."""
    return _handle(
        disputes.open_dispute,
        payload.get("lot_id", ""), payload.get("raised_by", ""),
        payload.get("raised_by_role", "farmer"), payload.get("category", "OTHER"),
        payload.get("description", ""),
        offer_id=payload.get("offer_id"), payment_id=payload.get("payment_id"),
        against_party=payload.get("against_party"),
        evidence_refs=payload.get("evidence_refs"),
        claim_rupees=payload.get("claim_rupees"),
    )


@router.post("/disputes/{dispute_id}/resolve", dependencies=[Depends(require_api_key)])
async def resolve_dispute(dispute_id: str, payload: Dict[str, Any] = Body(...)):
    return _handle(
        disputes.resolve, dispute_id, payload.get("resolution", ""),
        payload.get("actor", "admin"),
        release_payment=bool(payload.get("release_payment", False)),
        refund_payment=bool(payload.get("refund_payment", False)),
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@router.get("/audit", dependencies=[Depends(rate_limit_queries)])
async def recent_audit(
    entity_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    events = audit.recent(limit=limit, entity_type=entity_type)
    return {"count": len(events), "events": [e.to_dict() for e in events]}


@router.get("/audit/integrity", dependencies=[Depends(rate_limit_queries)])
async def audit_integrity():
    """Check the append-only trail for gaps or duplicates."""
    return audit.verify_integrity()


@router.get("/audit/{entity_type}/{entity_id}", dependencies=[Depends(rate_limit_queries)])
async def entity_audit(entity_type: str, entity_id: str):
    events = audit.history(entity_type.upper(), entity_id)
    return {"count": len(events), "events": [e.to_dict() for e in events]}
