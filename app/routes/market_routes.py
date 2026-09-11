"""
Market intelligence REST API.

Thin HTTP layer over `app.market.service`: route handlers validate input and
delegate, so the voice agent and the API can never drift apart in what they
report. Business logic belongs in the service, not here.

Read endpoints are open but rate limited. Anything that triggers an external
fetch or mutates stored data requires an API key.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.market import repository as repo
from app.market import service
from app.market.demo_data import clear_demo_prices, seed_demo_prices
from app.market.ingestion import check_source, ingest_datagov, ingest_many
from app.market.schema import init_market_tables
from app.security import rate_limit_queries, require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market", tags=["market-intelligence"])


def _require_available(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Surface a missing-data condition as HTTP 404, not as an empty 200.

    A 200 carrying nulls invites a client to render zeros, which is the same
    failure as fabricating a price - just one layer further out.
    """
    if not payload.get("available", False):
        raise HTTPException(
            status_code=404,
            detail={
                "message": payload.get("reason", "Market data is not available."),
                "data_quality": payload.get("data_quality", "UNAVAILABLE"),
            },
        )
    return payload


# ---------------------------------------------------------------------------
# Price discovery
# ---------------------------------------------------------------------------

@router.get("/price", dependencies=[Depends(rate_limit_queries)])
async def get_price(
    commodity: str = Query(..., description="Crop name in any supported language"),
    location: str = Query("Kalaburagi", description="Farmer's town or district"),
):
    """Latest mandi price for a commodity, with provenance and data quality."""
    return _require_available(service.get_price(commodity, location))


@router.get("/compare", dependencies=[Depends(rate_limit_queries)])
async def compare(
    commodity: str = Query(...),
    location: str = Query("Kalaburagi"),
    quantity: str = Query("10 quintal", description='e.g. "30 quintal", "500 kg"'),
    radius_km: Optional[float] = Query(None, ge=5, le=500),
):
    """
    Rank nearby markets by NET realisation after transport, fees and losses.

    The ranking is by what the farmer keeps, not by the headline price.
    """
    return _require_available(
        service.compare(commodity, location, quantity=quantity, radius_km=radius_km)
    )


@router.get("/trend", dependencies=[Depends(rate_limit_queries)])
async def trend(
    commodity: str = Query(...),
    location: str = Query("Kalaburagi"),
    days: int = Query(90, ge=7, le=365),
):
    """Price and arrival trends over 7/30/90-day windows."""
    return _require_available(service.get_trend(commodity, location, days=days))


@router.get("/sale-window", dependencies=[Depends(rate_limit_queries)])
async def sale_window(
    commodity: str = Query(...),
    location: str = Query("Kalaburagi"),
    quantity: str = Query("10 quintal"),
    storage_days: int = Query(0, ge=0, le=365, description="0 means no storage available"),
    needs_cash_urgently: bool = Query(False),
    has_cold_storage: bool = Query(False),
):
    """
    Sell-or-wait recommendation with reasoning, risks and confidence.

    `storage_days` and `needs_cash_urgently` materially change the answer:
    a farmer with no storage is never advised to wait.
    """
    payload = service.sale_window(
        commodity, location,
        quantity=quantity,
        storage_days=storage_days,
        needs_cash_urgently=needs_cash_urgently,
        has_cold_storage=has_cold_storage,
    )
    # A refusal to advise is a legitimate answer here, so it is returned as
    # 200 with decision=INSUFFICIENT_DATA rather than raised as an error.
    return payload


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@router.get("/markets", dependencies=[Depends(rate_limit_queries)])
async def markets(state: Optional[str] = Query(None)):
    """Registered markets, optionally filtered by state."""
    return service.available_markets(state=state)


@router.get("/markets/nearby", dependencies=[Depends(rate_limit_queries)])
async def nearby_markets(
    location: str = Query(...),
    radius_km: float = Query(150.0, ge=5, le=500),
):
    """Markets within a road-distance radius of a location."""
    origin = repo.resolve_origin(location)
    if origin is None or origin.latitude is None:
        raise HTTPException(
            status_code=404,
            detail=f"Location '{location}' could not be matched to a known market.",
        )
    found = repo.find_markets_near(origin.latitude, origin.longitude, radius_km=radius_km)
    return {
        "available": bool(found),
        "origin": origin.to_dict(),
        "radius_km": radius_km,
        "count": len(found),
        "markets": [m.to_dict() for m in found],
    }


@router.get("/commodities", dependencies=[Depends(rate_limit_queries)])
async def commodities():
    """Commodities this system holds market data for."""
    return service.supported_commodities()


# ---------------------------------------------------------------------------
# Operations and data quality
# ---------------------------------------------------------------------------

@router.get("/coverage", dependencies=[Depends(rate_limit_queries)])
async def coverage():
    """
    What data exists and how trustworthy it is.

    The honest answer to "is this system running on real data?" - it reports
    record counts, date ranges and how much of it is demo fixture data.
    """
    rows = repo.get_data_coverage()
    total = sum(r["records"] for r in rows)
    mock = sum(r["mock_records"] for r in rows)
    return {
        "available": bool(rows),
        "total_records": total,
        "mock_records": mock,
        "real_records": total - mock,
        "is_demo_data": mock == total and total > 0,
        "by_commodity": rows,
    }


@router.get("/ingestion/runs", dependencies=[Depends(rate_limit_queries)])
async def ingestion_runs(limit: int = Query(20, ge=1, le=100)):
    """Recent ingestion runs, including failures. Failures are never hidden."""
    runs = repo.get_recent_ingestion_runs(limit=limit)
    return {"count": len(runs), "runs": runs}


@router.get("/ingestion/source", dependencies=[Depends(require_api_key)])
async def source_status(state: str = Query("Karnataka")):
    """Live probe of the upstream data source. Slow by nature - it makes a real call."""
    return check_source(state=state).to_dict()


@router.post("/ingestion/run", dependencies=[Depends(require_api_key)])
async def run_ingestion(
    state: Optional[str] = Query("Karnataka"),
    commodity: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Trigger one ingestion pass. Returns the run record, success or failure."""
    init_market_tables()
    return ingest_datagov(state=state, commodity=commodity, limit=limit).to_dict()


@router.post("/ingestion/run-all", dependencies=[Depends(require_api_key)])
async def run_ingestion_all(limit: int = Query(1000, ge=1, le=10000)):
    """Ingest the main commodities across Karnataka and Maharashtra."""
    init_market_tables()
    commodities = ["Onion", "Tomato", "Potato", "Tur", "Maize", "Cotton"]
    results = ingest_many(commodities, limit=limit)
    return {
        "runs": [r.to_dict() for r in results],
        "total_accepted": sum(r.records_accepted for r in results),
        "failed_runs": sum(1 for r in results if r.status == "FAILED"),
    }


# ---------------------------------------------------------------------------
# Demo data (explicitly separated, always tagged MOCK)
# ---------------------------------------------------------------------------

@router.post("/demo/seed", dependencies=[Depends(require_api_key)])
async def seed_demo(days: int = Query(120, ge=7, le=365)):
    """
    Seed demonstration price history.

    Every row is written with data_quality=MOCK and is refused by the
    recommendation engine unless MARKET_DEMO_MODE is explicitly enabled.
    """
    init_market_tables()
    result = seed_demo_prices(days=days)
    result["warning"] = (
        "All seeded rows are tagged MOCK and must never be presented as live "
        "government market data."
    )
    return result


@router.delete("/demo/seed", dependencies=[Depends(require_api_key)])
async def clear_demo():
    """Remove demo rows. Real observations are left untouched."""
    return {"deleted": clear_demo_prices()}
