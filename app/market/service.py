"""
Market intelligence facade.

One entry point for every consumer - voice tools, REST API, dashboard - so
that a farmer on the phone and an evaluator reading JSON see identical
numbers computed the same way.

Two rules hold everywhere in this module:

  1. Nothing returns a bare price. Every payload carries data_quality,
     provenance and a spoken-language caveat when the data is not current.
  2. A failure returns a payload that says so. There is no path where a
     missing source becomes a plausible-looking number.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.market.comparison import MarketComparison, compare_markets
from app.market.normalization import (
    COMMODITY_BY_CANONICAL,
    parse_quantity,
    resolve_commodity,
)
from app.market.provenance import Confidence, DataQuality
from app.market.repository import (
    get_latest_price,
    get_price_series,
    list_markets,
    resolve_origin,
)
from app.market.sale_window import (
    Decision,
    FarmerConstraints,
    SaleWindowRecommendation,
    recommend_sale_window,
)
from app.market.trends import TrendReport, build_trend_report

logger = logging.getLogger(__name__)

DEFAULT_LOCATION = "Kalaburagi"
DEFAULT_QUANTITY_KG = 1_000.0


def _demo_mode() -> bool:
    return bool(get_settings().MARKET_DEMO_MODE)


def _unavailable(reason: str, **extra: Any) -> Dict[str, Any]:
    """
    The shape returned whenever data is missing.

    `spoken` is what the voice layer reads out. It says the data is missing,
    which is the entire point: a farmer told "I could not reach the market
    data" can go ask someone else, whereas a farmer told an invented price
    acts on it.
    """
    payload = {
        "available": False,
        "data_quality": DataQuality.UNAVAILABLE.value,
        "reason": reason,
        "spoken": (
            "I could not get reliable market data for that right now. "
            "Please check with your local mandi before making a decision."
        ),
    }
    payload.update(extra)
    return payload


def _freshness_caveat(quality: DataQuality, age_description: str) -> Optional[str]:
    """The sentence the voice layer must append when data is not current."""
    if quality is DataQuality.MOCK:
        return (
            "This is demonstration data, not a real market price, and must not be "
            "used for an actual selling decision."
        )
    if quality is DataQuality.STALE:
        return (
            f"Please note this price is {age_description}, so confirm at the mandi "
            "before you act on it."
        )
    if quality is DataQuality.DEGRADED:
        return "This record had data quality problems, so treat it with caution."
    return None


# ---------------------------------------------------------------------------
# Price lookup
# ---------------------------------------------------------------------------

def get_price(commodity: str, location: str = DEFAULT_LOCATION) -> Dict[str, Any]:
    """Latest price for a commodity at or near a location."""
    match = resolve_commodity(commodity)
    if match is None:
        return _unavailable(
            f"'{commodity}' is not a commodity I have market data for.",
            requested_commodity=commodity,
        )

    canonical = match.canonical
    origin = resolve_origin(location)
    market_name = origin.name if origin else None

    record = get_latest_price(canonical, market=market_name)
    if record is None and market_name:
        # Fall back to the district, then the state, before giving up.
        record = get_latest_price(canonical, district=origin.district)
        if record is None:
            record = get_latest_price(canonical, state=origin.state)

    if record is None:
        return _unavailable(
            f"No {canonical} price is on record for {location}.",
            commodity=canonical, location=location,
        )

    provenance = record.provenance()
    caveat = _freshness_caveat(provenance.data_quality, provenance.describe_age())

    return {
        "available": True,
        "commodity": canonical,
        "market": record.market,
        "district": record.district,
        "state": record.state,
        "modal_price": record.modal_price,
        "min_price": record.min_price,
        "max_price": record.max_price,
        "price_unit": record.price_unit,
        "price_per_kg": round(record.modal_price / 100.0, 2),
        "arrival_qty": record.arrival_qty,
        "arrival_date": record.arrival_date,
        "data_quality": provenance.data_quality.value,
        "is_current": provenance.presentable_as_current,
        "provenance": provenance.to_dict(),
        "caveat": caveat,
        "spoken": _speak_price(canonical, record.market, record.modal_price,
                              provenance, caveat),
    }


def _speak_price(commodity, market, modal, provenance, caveat) -> str:
    when = "today" if provenance.presentable_as_current else f"as of {provenance.data_timestamp}"
    line = (
        f"The modal price for {commodity} at {market} is "
        f"{modal:.0f} rupees per quintal {when}, "
        f"which is about {modal / 100:.0f} rupees per kilo."
    )
    return f"{line} {caveat}" if caveat else line


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

def get_trend(
    commodity: str,
    location: str = DEFAULT_LOCATION,
    days: int = 90,
) -> Dict[str, Any]:
    """Price and arrival trend for a commodity at a market."""
    match = resolve_commodity(commodity)
    if match is None:
        return _unavailable(f"'{commodity}' is not a commodity I have market data for.")

    canonical = match.canonical
    origin = resolve_origin(location)
    market_name = origin.name if origin else None

    series = get_price_series(canonical, market=market_name, days=days)
    if not series and origin:
        series = get_price_series(canonical, district=origin.district, days=days)

    if not series:
        return _unavailable(
            f"No price history is stored for {canonical} near {location}.",
            commodity=canonical,
        )

    report = build_trend_report(canonical, series, market=market_name)
    payload = report.to_dict()
    payload["available"] = True
    payload["spoken"] = _speak_trend(report)
    return payload


def _speak_trend(report: TrendReport) -> str:
    parts: List[str] = []
    week = report.window(7)
    month = report.window(30)

    if report.current_price:
        parts.append(
            f"{report.commodity} is currently {report.current_price:.0f} rupees per quintal"
            + (f" at {report.market}" if report.market else "")
            + "."
        )

    if week and week.sufficient and week.change_pct is not None:
        move = "risen" if week.change_pct > 0 else "fallen"
        parts.append(f"Over the last week the price has {move} {abs(week.change_pct):.1f} percent.")

    if month and month.sufficient and month.average:
        parts.append(f"The thirty day average is {month.average:.0f} rupees.")

    arrivals = report.arrivals.get(30)
    if arrivals and arrivals.change_pct is not None and abs(arrivals.change_pct) > 5:
        direction = "increased" if arrivals.change_pct > 0 else "decreased"
        parts.append(
            f"Arrivals into the market have {direction} by about "
            f"{abs(arrivals.change_pct):.0f} percent."
        )

    caveat = _freshness_caveat(
        report.data_quality,
        report.provenance.describe_age() if report.provenance else "old",
    )
    if caveat:
        parts.append(caveat)

    return " ".join(parts) if parts else "I do not have enough price history to describe a trend."


# ---------------------------------------------------------------------------
# Market comparison
# ---------------------------------------------------------------------------

def compare(
    commodity: str,
    location: str = DEFAULT_LOCATION,
    quantity: Optional[str] = None,
    radius_km: Optional[float] = None,
) -> Dict[str, Any]:
    """Rank nearby markets by net realisation for this consignment."""
    quantity_kg = DEFAULT_QUANTITY_KG
    parsed = parse_quantity(quantity) if quantity else None
    if parsed:
        quantity_kg = parsed.kg

    radius = radius_km or get_settings().MARKET_DEFAULT_RADIUS_KM
    result = compare_markets(commodity, location, quantity_kg=quantity_kg, radius_km=radius)

    payload = result.to_dict()
    payload["available"] = result.has_data
    if not result.has_data:
        payload["spoken"] = (
            "I could not compare markets for that crop right now. "
            + (result.notes[0] if result.notes else "")
        )
        return payload

    payload["spoken"] = _speak_comparison(result)
    return payload


def _speak_comparison(result: MarketComparison) -> str:
    best = result.best
    parts = [
        f"Comparing {len(result.options)} markets near {result.origin} for {result.commodity}."
    ]

    origin_option = result.origin_option
    if origin_option:
        parts.append(
            f"Your local market {origin_option.market} is paying "
            f"{origin_option.gross_price_per_quintal:.0f} rupees per quintal, "
            f"which works out to about {origin_option.net_price_per_quintal:.0f} rupees "
            "after transport and market charges."
        )

    if best and (not origin_option or best.market != origin_option.market):
        parts.append(
            f"{best.market}, about {best.distance_km:.0f} kilometres away, is paying "
            f"{best.gross_price_per_quintal:.0f} rupees. "
            f"Even after transport you would keep about "
            f"{best.net_price_per_quintal:.0f} rupees per quintal there."
        )
        if result.advantage_per_quintal and result.advantage_total:
            parts.append(
                f"That is roughly {result.advantage_per_quintal:.0f} rupees more per quintal, "
                f"or about {result.advantage_total:.0f} rupees more for your whole lot."
            )
    elif best:
        parts.append("Your local market is already the best option after transport costs.")

    caveat = _freshness_caveat(result.data_quality, "not current")
    if caveat:
        parts.append(caveat)

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Sale window
# ---------------------------------------------------------------------------

def sale_window(
    commodity: str,
    location: str = DEFAULT_LOCATION,
    quantity: Optional[str] = None,
    storage_days: int = 0,
    needs_cash_urgently: bool = False,
    has_cold_storage: bool = False,
) -> Dict[str, Any]:
    """
    Sell-or-wait recommendation, with reasoning the farmer can check.

    Demo data reaches the engine only when MARKET_DEMO_MODE is on, and the
    recommendation labels itself when it does.
    """
    match = resolve_commodity(commodity)
    if match is None:
        return _unavailable(f"'{commodity}' is not a commodity I have market data for.")

    canonical = match.canonical
    quantity_kg = DEFAULT_QUANTITY_KG
    parsed = parse_quantity(quantity) if quantity else None
    if parsed:
        quantity_kg = parsed.kg

    trend_payload = get_trend(canonical, location)
    if not trend_payload.get("available"):
        return trend_payload

    origin = resolve_origin(location)
    market_name = origin.name if origin else None
    series = get_price_series(canonical, market=market_name, days=90)
    if not series and origin:
        series = get_price_series(canonical, district=origin.district, days=90)

    report = build_trend_report(canonical, series, market=market_name)

    comparison = compare_markets(
        canonical, location, quantity_kg=quantity_kg,
        radius_km=get_settings().MARKET_DEFAULT_RADIUS_KM,
    )

    constraints = FarmerConstraints(
        storage_days_available=storage_days,
        needs_cash_urgently=needs_cash_urgently,
        has_cold_storage=has_cold_storage,
        quantity_kg=quantity_kg,
    )

    recommendation = recommend_sale_window(
        report, constraints, comparison, allow_mock=_demo_mode()
    )

    payload = recommendation.to_dict()
    payload["available"] = recommendation.decision is not Decision.INSUFFICIENT_DATA
    payload["constraints"] = constraints.to_dict()
    payload["spoken"] = _speak_recommendation(recommendation)
    return payload


def _humanise(text: str) -> str:
    """
    Turn an internal reasoning string into something that sounds right aloud.

    The reasoning strings are written for the audit record ("price rose 16.0%
    over the last 7 days"). Spoken by a TTS voice, the symbols and the
    lower-case opening make it sound like a machine reading a log line.
    """
    spoken = text.strip()
    spoken = spoken.replace("%", " percent")
    spoken = spoken.replace("/quintal", " rupees per quintal")
    spoken = spoken.replace("INR ", "")
    spoken = re.sub(r"\s{2,}", " ", spoken)
    spoken = re.sub(r"\b(\d+)-day\b", r"\1 day", spoken)
    if spoken:
        spoken = spoken[0].upper() + spoken[1:]
    return spoken


def _speak_recommendation(rec: SaleWindowRecommendation) -> str:
    if rec.decision is Decision.INSUFFICIENT_DATA:
        return (
            "I do not have enough reliable market data to advise you on whether to sell "
            "or wait. Please check with your local mandi."
        )

    opening = {
        Decision.SELL_NOW: f"My recommendation is to sell your {rec.commodity} now.",
        Decision.WAIT: (
            f"My recommendation is to wait about {rec.recommended_days} days "
            f"before selling your {rec.commodity}."
        ),
        Decision.SELL_PARTIAL: (
            f"My recommendation is to sell part of your {rec.commodity} now "
            "and hold the rest."
        ),
        Decision.COMPARE_MARKETS: (
            f"My recommendation is to sell your {rec.commodity}, but not at your local market."
        ),
    }[rec.decision]

    parts = [opening]

    if rec.reasoning:
        # Two reasons is what a person can hold in mind on a phone call.
        reasons = [_humanise(r) for r in rec.reasoning[:2]]
        parts.append("Here is why. " + " ".join(r.rstrip(".") + "." for r in reasons))

    if rec.decision is Decision.COMPARE_MARKETS and rec.alternative_market:
        alt = rec.alternative_market
        parts.append(
            f"{alt['market']} is about {alt['distance_km']:.0f} kilometres away and would "
            f"leave you roughly {alt['advantage_per_quintal']:.0f} rupees more per quintal "
            "after transport."
        )

    if rec.decision is Decision.WAIT and rec.expected_price_low:
        parts.append(
            f"If prices move as expected, you might get between "
            f"{rec.expected_price_low:.0f} and {rec.expected_price_high:.0f} rupees per quintal."
        )

    confidence_word = {
        Confidence.HIGH: "high", Confidence.MEDIUM: "medium",
        Confidence.LOW: "low", Confidence.INSUFFICIENT_DATA: "very low",
    }[rec.confidence]
    parts.append(f"My confidence in this is {confidence_word}.")

    if rec.risks:
        parts.append(rec.risks[0])

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def available_markets(state: Optional[str] = None) -> Dict[str, Any]:
    markets = list_markets(state=state)
    return {
        "available": bool(markets),
        "count": len(markets),
        "markets": [m.to_dict() for m in markets],
    }


def supported_commodities() -> Dict[str, Any]:
    return {
        "available": True,
        "count": len(COMMODITY_BY_CANONICAL),
        "commodities": [
            {
                "name": c.canonical,
                "group": c.group,
                "perishable": c.perishable,
                "typical_storage_days": c.typical_storage_days,
            }
            for c in COMMODITY_BY_CANONICAL.values()
        ],
    }
