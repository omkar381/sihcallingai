"""
Voice agent, call transcripts and portal-facing aggregates.

    /api/system/overview          what the platform holds right now, for the home page
    /api/farmers/{phone}/workspace everything one farmer has in motion
    /api/calls...                 call transcripts and a live stream of turns
    /api/voice/simulate           talk to the agent from a browser, same pipeline as the phone
    /api/demo/...                 demo marketplace data, demo mode only
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.security import rate_limit_ai, rate_limit_queries, require_api_key
from app.voice import call_log
from app.voice.language import normalise_language

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-agent"])


def _normalise_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    return digits


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def _overview() -> Dict[str, Any]:
    from app.market import repository
    from app.market.normalization import COMMODITY_BY_CANONICAL
    from app.services.db import DB_LOCK, get_connection
    from app.trade.schema import init_trade_tables

    settings = get_settings()
    init_trade_tables()
    coverage = repository.get_data_coverage()
    total = sum(c["records"] for c in coverage)
    mock = sum(c["mock_records"] for c in coverage)

    def scalar(conn, sql: str, *params) -> Any:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else 0

    with DB_LOCK:
        conn = get_connection()
        try:
            lots_by_status = {
                r["status"]: r["n"]
                for r in conn.execute("SELECT status, COUNT(*) AS n FROM lots GROUP BY status")
            }
            trade = {
                "buyers_total": scalar(conn, "SELECT COUNT(*) FROM buyers WHERE is_active = 1"),
                "buyers_verified": scalar(
                    conn, "SELECT COUNT(*) FROM buyers WHERE is_active = 1 AND verification_status = 'VERIFIED'"
                ),
                "open_demand": scalar(
                    conn, "SELECT COUNT(*) FROM buyer_demand WHERE status = 'OPEN' "
                          "AND quantity_filled_kg < quantity_needed_kg"
                ),
                "open_demand_kg": scalar(
                    conn, "SELECT SUM(quantity_needed_kg - quantity_filled_kg) FROM buyer_demand "
                          "WHERE status = 'OPEN'"
                ),
                "fpos": scalar(conn, "SELECT COUNT(*) FROM fpos WHERE is_active = 1"),
                "lots_listed": sum(
                    lots_by_status.get(s, 0) for s in ("PUBLISHED", "MATCHED", "OFFER_RECEIVED", "NEGOTIATION")
                ),
                "lots_by_status": lots_by_status,
                "open_offers": scalar(conn, "SELECT COUNT(*) FROM offers WHERE status = 'OPEN'"),
                "escrow_rupees": scalar(
                    conn, "SELECT SUM(amount_paise) FROM payments WHERE status = 'ESCROW'"
                ) / 100.0,
                "released_rupees": scalar(
                    conn, "SELECT SUM(amount_paise) FROM payments WHERE status = 'RELEASED'"
                ) / 100.0,
                "open_disputes": scalar(
                    conn, "SELECT COUNT(*) FROM disputes WHERE status IN "
                          "('OPEN','UNDER_REVIEW','REQUESTED_INFORMATION','ESCALATED')"
                ),
            }
        finally:
            conn.close()

    return {
        "demo_mode": settings.MARKET_DEMO_MODE,
        "voice_number": settings.TWILIO_PHONE_NUMBER or None,
        "market": {
            "markets": len(repository.list_markets()),
            "commodities_supported": len(COMMODITY_BY_CANONICAL),
            "commodities_with_data": len(coverage),
            "price_records": total,
            "mock_records": mock,
            "real_records": total - mock,
            "is_demo_data": bool(total) and mock == total,
        },
        "trade": trade,
        "calls": call_log.call_statistics(),
    }


@router.get("/api/system/overview", dependencies=[Depends(rate_limit_queries)])
async def system_overview():
    return await asyncio.to_thread(_overview)


# ---------------------------------------------------------------------------
# Farmer workspace
# ---------------------------------------------------------------------------

def _workspace(phone: str) -> Dict[str, Any]:
    from app.farmer_store import get_farmer
    from app.trade import buyers, disputes, fpo, lots, matching, offers, payments
    from app.trade.schema import init_trade_tables

    init_trade_tables()
    my_lots = lots.list_lots(farmer_phone=phone, exclude_aggregated=False, limit=200)
    buyer_cache: Dict[str, Dict[str, Any]] = {}

    def buyer_name(buyer_id: str) -> Optional[str]:
        if buyer_id not in buyer_cache:
            buyer_cache[buyer_id] = buyers.get_buyer(buyer_id) or {}
        return buyer_cache[buyer_id].get("name")

    lot_rows: List[Dict[str, Any]] = []
    lot_ids = set()
    for lot in my_lots:
        lot_ids.add(lot["id"])
        lot_offers = offers.list_offers(lot_id=lot["id"])
        match_count = None
        if lot["status"] in ("PUBLISHED", "MATCHED", "OFFER_RECEIVED"):
            try:
                match_count = len(matching.find_matches(lot, limit=20))
            except Exception:
                match_count = None
        lot_rows.append({**lot, "offers": lot_offers, "match_count": match_count})

    pay_rows = []
    for p in payments.list_payments(payee_phone=phone, limit=100):
        lot = next((l for l in my_lots if l["id"] == p["lot_id"]), None)
        pay_rows.append({
            **p, "buyer_name": buyer_name(p["buyer_id"]),
            "commodity": lot["commodity"] if lot else None,
        })

    dispute_rows = []
    for lot_id in lot_ids:
        dispute_rows.extend(disputes.list_disputes(lot_id=lot_id))
    dispute_rows.sort(key=lambda d: d["created_at"], reverse=True)

    fpo_rows = []
    for org in fpo.get_farmer_fpos(phone):
        groups = []
        for group in fpo.find_aggregation_groups(org["id"]):
            item = group.to_dict()
            item["includes_farmer"] = any(l.get("farmer_phone") == phone for l in group.lots)
            groups.append(item)
        fpo_rows.append({**org, "groups": groups})

    calls = [c for c in call_log.recent_calls(limit=100) if c.get("phone") == phone][:10]

    return {
        "phone": phone,
        "farmer": get_farmer(phone),
        "lots": lot_rows,
        "payments": pay_rows,
        "disputes": dispute_rows,
        "fpos": fpo_rows,
        "calls": calls,
        "language": call_log.get_language_preference(phone),
        "summary": {
            "listed": sum(1 for l in my_lots if l["status"] in ("PUBLISHED", "MATCHED", "OFFER_RECEIVED", "NEGOTIATION")),
            "open_offers": sum(1 for l in lot_rows for o in l["offers"] if o["status"] == "OPEN"),
            "in_escrow_rupees": sum(p["amount_rupees"] for p in pay_rows if p["status"] == "ESCROW"),
            "released_rupees": sum(p["amount_rupees"] for p in pay_rows if p["status"] == "RELEASED"),
            "open_disputes": sum(1 for d in dispute_rows if d["is_open"]),
        },
    }


@router.get("/api/farmers/{phone}/workspace", dependencies=[Depends(rate_limit_queries)])
async def farmer_workspace(phone: str):
    normalised = _normalise_phone(phone)
    if len(normalised) < 11:
        raise HTTPException(status_code=400, detail="Enter a 10-digit mobile number")
    return await asyncio.to_thread(_workspace, normalised)


@router.post("/api/farmers/{phone}/language", dependencies=[Depends(require_api_key)])
async def set_farmer_language(phone: str, payload: Dict[str, Any] = Body(...)):
    """Set the language the farmer is greeted in on calls: kn, hi or en."""
    normalised = _normalise_phone(phone)
    language = normalise_language(payload.get("language"), default=None)
    if len(normalised) < 11:
        raise HTTPException(status_code=400, detail="Enter a 10-digit mobile number")
    if language is None:
        raise HTTPException(status_code=400, detail="language must be kn, hi or en")
    await asyncio.to_thread(call_log.set_language_preference, normalised, language)
    return {"phone": normalised, "language": language}


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

@router.get("/api/calls", dependencies=[Depends(rate_limit_queries)])
async def list_calls(limit: int = Query(30, ge=1, le=200)):
    rows = await asyncio.to_thread(call_log.recent_calls, limit)
    return {"count": len(rows), "calls": rows}


@router.get("/api/calls/stats", dependencies=[Depends(rate_limit_queries)])
async def calls_stats():
    return await asyncio.to_thread(call_log.call_statistics)


@router.get("/api/calls/stream")
async def calls_stream(after: int = Query(0, ge=0)):
    """Server-sent events: every new turn and call start or end, as it happens."""

    async def events():
        seq = after or call_log.feed_head()
        idle = 0
        yield f"event: ready\ndata: {json.dumps({'seq': seq})}\n\n"
        while True:
            fresh = call_log.feed_since(seq)
            for event in fresh:
                seq = event["seq"]
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if fresh:
                idle = 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/calls/{call_sid}", dependencies=[Depends(rate_limit_queries)])
async def get_call(call_sid: str):
    call = await asyncio.to_thread(call_log.get_call, call_sid)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


# ---------------------------------------------------------------------------
# Browser simulator
# ---------------------------------------------------------------------------

@router.post(
    "/api/voice/simulate",
    dependencies=[Depends(require_api_key), Depends(rate_limit_ai)],
)
async def simulate_turn(payload: Dict[str, Any] = Body(...)):
    """
    Speak to the agent by typing. Runs the exact phone pipeline - understanding,
    dialogue, Kannada reply and audio - and records the turn like a real call.
    """
    from app.voice.pipeline import run_turn

    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    session = str(payload.get("session_id") or uuid.uuid4().hex[:12])
    phone = _normalise_phone(str(payload.get("phone") or "")) or ""
    call_sid = f"WEB-{session}"

    raw_language = payload.get("language")
    language = normalise_language(raw_language, default=None)
    if raw_language and language is None:
        raise HTTPException(status_code=400, detail="language must be kn, hi or en")

    if payload.get("new_session"):
        from app.voice import dialogue
        dialogue.end_state(call_sid)
        await asyncio.to_thread(call_log.start_call, call_sid, phone, "web", language)

    result = await asyncio.to_thread(run_turn, call_sid, phone, text, channel="web", language=language)
    data = result.to_dict()
    data["session_id"] = session
    data["audio_path"] = f"/audio/{result.audio_file}" if result.audio_file else None
    return data


@router.post("/api/voice/simulate/reset", dependencies=[Depends(require_api_key)])
async def reset_simulation(payload: Dict[str, Any] = Body(...)):
    from app.voice import dialogue

    session = str(payload.get("session_id") or "")
    if session:
        dialogue.end_state(f"WEB-{session}")
        await asyncio.to_thread(call_log.end_call, f"WEB-{session}", "completed")
    return {"reset": bool(session)}


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

@router.get("/api/demo/status", dependencies=[Depends(rate_limit_queries)])
async def demo_status():
    from app.trade import demo_seed

    rows = await asyncio.to_thread(demo_seed.registry)
    anchors = sorted({r["anchor_phone"] for r in rows if r.get("anchor_phone")})
    return {
        "demo_mode": get_settings().MARKET_DEMO_MODE,
        "seeded": bool(rows),
        "anchor_phones": anchors,
        "entity_ids": await asyncio.to_thread(demo_seed.demo_entity_ids),
    }


@router.post("/api/demo/trade-seed", dependencies=[Depends(require_api_key)])
async def seed_trade(payload: Dict[str, Any] = Body(...)):
    from app.trade import demo_seed

    phone = _normalise_phone(str(payload.get("phone") or ""))
    if len(phone) < 11:
        raise HTTPException(status_code=400, detail="A farmer phone number is required")
    try:
        return await asyncio.to_thread(demo_seed.seed_trade_demo, phone)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
