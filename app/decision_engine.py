"""
Decision engine: one recommendation from crop health, weather, soil, market
signal and the knowledge base.

Deterministic rules and additive risk scoring. The same inputs give the same
answer, every risk point is attributed to a named factor, and every rule that
changed the advice is listed in `rules_fired`. The LLM only reads the photo;
it never makes the call.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.agriculture import get_soil_info, get_weather
from app.config import get_settings
from app.market.service import sale_window
from app.rag import retrieve as rag_retrieve

logger = logging.getLogger(__name__)

ENGINE_VERSION = "decision-v1"

PROBLEM_CATEGORIES = ("DISEASE", "PEST", "NUTRIENT", "ABIOTIC")
SPRAYABLE = ("DISEASE", "PEST")
PERISHABLE = {
    "onion", "tomato", "potato", "banana", "chilli", "green chilli", "cabbage",
    "cauliflower", "brinjal", "okra", "mango", "grapes", "papaya",
}
SEVERITY_POINTS = {"HIGH": 40, "MEDIUM": 25, "LOW": 10, "NONE": 0}
KNOWLEDGE_MIN_SCORE = 0.62

# Spraying is blocked above this wind speed (OpenWeather metric units, m/s).
SPRAY_MAX_WIND = 8.0


@dataclass
class RiskFactor:
    name: str
    points: int
    max_points: int
    detail: str


@dataclass
class Action:
    kind: str
    priority: int          # 1 do first, 2 soon, 3 when convenient
    title: str
    detail: str
    steps: List[str] = field(default_factory=list)


def _band(score: int) -> str:
    if score < 25:
        return "LOW"
    if score < 50:
        return "MODERATE"
    if score < 75:
        return "HIGH"
    return "SEVERE"


def _crop_health_risk(dx: Optional[Dict[str, Any]]) -> RiskFactor:
    if not dx:
        return RiskFactor("Crop health", 0, 40, "No photo checked.")
    if not dx.get("is_plant") or dx.get("category") not in PROBLEM_CATEGORIES:
        detail = "Crop looks healthy." if dx.get("category") == "HEALTHY" else "Photo was unclear, so crop health is not scored."
        return RiskFactor("Crop health", 0, 40, detail)
    confidence = float(dx.get("confidence") or 0)
    points = round(SEVERITY_POINTS.get(dx.get("severity"), 0) * max(confidence, 0.5))
    return RiskFactor(
        "Crop health", points, 40,
        f"{dx.get('condition')}, {str(dx.get('severity')).lower()} severity, {confidence:.0%} confidence.",
    )


def _weather_risk(w: Dict[str, Any], live: bool) -> RiskFactor:
    if not live:
        return RiskFactor("Weather", 0, 25, "Live weather unavailable, not scored.")
    points = 0
    parts: List[str] = []
    humidity = w.get("humidity") or 0
    rain = w.get("rainfall") or 0
    temp = w.get("temperature") or 0
    wind = w.get("wind_speed") or 0
    if humidity >= 85:
        points += 12
        parts.append(f"humidity {humidity}% favours fungal spread")
    elif humidity >= 75:
        points += 7
        parts.append(f"humidity {humidity}%")
    if rain > 0:
        points += 8
        parts.append(f"rain {rain} mm in the last hour")
    if temp >= 38:
        points += 8
        parts.append(f"heat {temp:.0f}°C")
    elif temp >= 35:
        points += 5
        parts.append(f"warm {temp:.0f}°C")
    if wind >= SPRAY_MAX_WIND:
        points += 4
        parts.append(f"wind {wind:.0f} m/s")
    detail = _sentence("; ".join(parts)) if parts else (
        f"{temp:.0f}°C, {humidity}% humidity, {w.get('description', '')}: no weather stress."
    )
    return RiskFactor("Weather", min(points, 25), 25, detail)


def _sentence(text: str) -> str:
    # str.capitalize() would lowercase market names like "Solapur".
    return text[:1].upper() + text[1:]


def _market_risk(m: Dict[str, Any]) -> RiskFactor:
    if not m.get("available"):
        return RiskFactor("Market", 12, 20, "No reliable price signal for this crop and market.")
    points = 0
    parts: List[str] = []
    quality = m.get("data_quality")
    if quality in ("MOCK", "STALE", "DEGRADED"):
        points += 6
        parts.append("prices are demo data" if quality == "MOCK" else f"prices are {quality.lower()}")
    if m.get("confidence") in ("LOW", "INSUFFICIENT_DATA"):
        points += 4
        parts.append("low confidence in the price trend")
    risks = m.get("risks") or []
    if risks:
        points += min(6, 3 * len(risks))
        first = str(risks[0]).split(". ")[0].rstrip(".")
        # The demo-data warning is already stated above in plainer words.
        if not (quality == "MOCK" and "demonstration" in first.lower()):
            parts.append(first if len(first) <= 90 else first[:87].rstrip() + "...")
    detail = _sentence("; ".join(parts)) + "." if parts else "Price signal is reliable."
    return RiskFactor("Market", min(points, 20), 20, detail)


def _holding_risk(decision: str, commodity: str, storage_days: int, needs_cash: bool, cold_storage: bool) -> RiskFactor:
    points = 0
    parts: List[str] = []
    if needs_cash:
        points += 6
        parts.append("cash is needed now")
    if decision == "WAIT" and storage_days == 0:
        points += 9
        parts.append("waiting is advised but there is no storage")
    if commodity.lower() in PERISHABLE and not cold_storage and storage_days > 7:
        points += 5
        parts.append(f"{commodity.lower()} is perishable without cold storage")
    detail = _sentence("; ".join(parts)) + "." if parts else "No cash or storage pressure."
    return RiskFactor("Storage and cash", min(points, 15), 15, detail)


def _market_title(decision: str, m: Dict[str, Any]) -> str:
    if decision == "SELL_NOW":
        return "Sell now"
    if decision == "WAIT":
        days = m.get("recommended_days") or 0
        return f"Wait about {days} days before selling" if days else "Wait before selling"
    if decision == "SELL_PARTIAL":
        return "Sell part now, hold the rest"
    if decision == "COMPARE_MARKETS":
        alt = (m.get("alternative_market") or {}).get("market")
        return f"Sell at {alt}" if alt else "Sell at a better market"
    return "Check today's mandi rate before selling"


async def decide(
    commodity: str,
    location: str = "",
    quantity_qtl: float = 10.0,
    storage_days: int = 0,
    needs_cash: bool = False,
    cold_storage: bool = False,
    diagnosis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    commodity = commodity.strip()
    location = location.strip() or get_settings().VOICE_DEFAULT_LOCATION

    async def market_signal() -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(
                sale_window, commodity, location, f"{quantity_qtl:g} quintal",
                storage_days, needs_cash, cold_storage,
            )
        except Exception:
            logger.exception("Decision engine: market signal failed for %s at %s", commodity, location)
            return {"available": False, "reason": "Market data could not be read."}

    market, weather = await asyncio.gather(market_signal(), get_weather(location))
    soil = get_soil_info(location)

    weather_live = weather.get("source") != "fallback"
    humidity = weather.get("humidity") or 0
    rain = weather.get("rainfall") or 0
    wind = weather.get("wind_speed") or 0

    dx = diagnosis or {}
    category = dx.get("category")
    condition = dx.get("condition") or ""
    unhealthy = bool(diagnosis) and bool(dx.get("is_plant")) and category in PROBLEM_CATEGORIES
    severe = unhealthy and dx.get("severity") == "HIGH" and float(dx.get("confidence") or 0) >= 0.5
    treat_first = unhealthy and dx.get("severity") in ("MEDIUM", "HIGH")

    market_available = bool(market.get("available"))
    market_decision = market.get("decision") if market_available else "INSUFFICIENT_DATA"
    decision = market_decision
    overrides: List[str] = []
    rules: List[str] = []
    actions: List[Action] = []

    # Rule: a severe crop problem outweighs a price-based reason to wait.
    if severe and decision == "WAIT":
        decision = "SELL_PARTIAL"
        overrides.append(
            f"Prices may rise, but {condition.lower()} is severe. Waiting risks the crop losing grade, "
            "so sell the healthy, graded part now and treat the rest."
        )
        rules.append("SEVERE_PROBLEM_OVERRIDES_WAIT")

    if unhealthy:
        actions.append(Action(
            "TREAT", 1 if treat_first else 2, f"Treat {condition.lower()}",
            dx.get("summary") or "Follow these steps from the photo check.",
            steps=list(dx.get("treatment") or [])[:4],
        ))
        rules.append("PROBLEM_NEEDS_TREATMENT")

    if market_available:
        detail = overrides[0] if overrides else (market.get("spoken") or " ".join(market.get("reasoning") or []))
        actions.append(Action("MARKET", 1, _market_title(decision, market), detail))
    else:
        actions.append(Action(
            "MARKET", 2, _market_title("INSUFFICIENT_DATA", market),
            (market.get("reason") or market.get("message") or "No reliable price data for this crop and market.")
            + " Confirm the day's modal price at your mandi before agreeing to a bid.",
        ))

    if unhealthy and category in SPRAYABLE:
        if not weather_live:
            actions.append(Action(
                "SPRAY_TIMING", 2, "Check the weather before spraying",
                "Live weather is unavailable. Spray only in calm, dry weather, early morning or late afternoon.",
            ))
        elif rain > 0 or wind >= SPRAY_MAX_WIND:
            reason = f"it rained {rain} mm in the last hour" if rain > 0 else f"wind is {wind:.0f} m/s"
            actions.append(Action(
                "SPRAY_TIMING", 1, "Do not spray today",
                f"Right now {reason}, so spray would wash off or drift. Wait for a calm, dry morning.",
            ))
            rules.append("WEATHER_BLOCKS_SPRAYING")
        else:
            actions.append(Action(
                "SPRAY_TIMING", 2, "Spray early morning or late afternoon",
                f"Weather now is {weather.get('temperature', 0):.0f}°C with {humidity}% humidity and no rain. "
                "Avoid the hottest hours.",
            ))

    perishable = commodity.lower() in PERISHABLE
    if decision == "WAIT" and perishable and not cold_storage and weather_live and humidity >= 80:
        actions.append(Action(
            "STORAGE", 2, "Keep stored produce ventilated",
            f"Humidity is {humidity}% and there is no cold storage. Spread the {commodity.lower()} in single layers "
            "and remove soft or mouldy pieces daily while you wait.",
        ))
        rules.append("HUMID_STORAGE_WHILE_WAITING")

    if unhealthy and category == "NUTRIENT":
        actions.append(Action(
            "SOIL_TEST", 2, "Get a soil test",
            f"{soil.get('soil_type', 'Your soil')} (pH {soil.get('ph_range', 'unknown')}). A Soil Health Card test "
            "gives the exact fertiliser dose instead of guessing.",
        ))
        rules.append("DEFICIENCY_NEEDS_SOIL_TEST")

    factors = [
        _crop_health_risk(diagnosis),
        _weather_risk(weather, weather_live),
        _market_risk(market),
        _holding_risk(decision, commodity, storage_days, needs_cash, cold_storage),
    ]
    score = sum(f.points for f in factors)
    band = _band(score)

    if unhealthy and band in ("HIGH", "SEVERE"):
        actions.append(Action(
            "INSURANCE", 2, "Protect your insurance claim",
            "If the damage spreads, report crop loss under PMFBY within 72 hours, with photos. Keep this photo check.",
        ))
        rules.append("HIGH_RISK_INSURANCE_NOTICE")

    if not diagnosis:
        actions.append(Action(
            "PHOTO", 3, "Add a photo to check crop health",
            "A close-up of an affected leaf lets the advice account for disease or pests.",
        ))
    elif category == "HEALTHY":
        actions.append(Action(
            "MONITOR", 3, "Crop looks healthy",
            "Check again in a week, or sooner after rain.", steps=list(dx.get("prevention") or [])[:3],
        ))
    elif not unhealthy:
        actions.append(Action(
            "PHOTO", 2, "Retake the photo",
            dx.get("summary") or "Take a close-up of one affected leaf in daylight, both sides.",
        ))

    actions.sort(key=lambda a: a.priority)
    first = [a.title for a in actions if a.priority == 1][:2]
    headline = (". ".join(first) if first else actions[0].title) + "."

    query_terms = [commodity, condition if unhealthy else "", decision.replace("_", " ").lower(), "selling"]
    try:
        hits = await asyncio.to_thread(rag_retrieve, " ".join(t for t in query_terms if t), 3)
        knowledge = [h.to_dict() for h in hits if h.score >= KNOWLEDGE_MIN_SCORE]
    except Exception as exc:
        logger.warning("Decision engine: knowledge lookup skipped: %s", exc)
        knowledge = []

    logger.info(
        "[decision] %s at %s: %s (market %s), risk %d %s, rules=%s",
        commodity, location, decision, market_decision, score, band, rules,
    )

    return {
        "commodity": commodity,
        "location": location,
        "decision": decision,
        "market_decision": market_decision,
        "treat_first": treat_first,
        "headline": headline,
        "overrides": overrides,
        "risk": {"score": score, "band": band, "factors": [asdict(f) for f in factors]},
        "actions": [asdict(a) for a in actions],
        "inputs": {
            "market": {
                "available": market_available,
                "decision": market_decision,
                "market": market.get("market"),
                "current_price": market.get("current_price"),
                "data_quality": market.get("data_quality"),
                "confidence": market.get("confidence"),
                "reason": None if market_available else (market.get("reason") or market.get("message")),
            },
            "weather": {
                "live": weather_live,
                "temperature": weather.get("temperature"),
                "humidity": weather.get("humidity"),
                "rainfall": weather.get("rainfall"),
                "wind_speed": weather.get("wind_speed"),
                "description": weather.get("description"),
            },
            "soil": {
                "region": soil.get("name"),
                "soil_type": soil.get("soil_type"),
                "ph_range": soil.get("ph_range"),
            },
            "diagnosis": {
                "id": dx.get("id"),
                "condition": condition,
                "category": category,
                "severity": dx.get("severity"),
                "confidence": dx.get("confidence"),
            } if diagnosis else None,
        },
        "knowledge": knowledge,
        "rules_fired": rules,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
