"""
Gemini tool functions for market intelligence.

These are the only route by which the voice agent may obtain a market number.
Each returns a plain dict that the model reads and then narrates; the numbers
themselves are computed by the deterministic engine underneath.

Every payload carries `data_quality` and, when the data is not current, a
`caveat` the model is instructed to repeat. If a tool cannot answer it returns
`available: False` with an `agent_instruction` telling the model to say so
rather than improvise - which is the failure mode these tools exist to close.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.market import service

logger = logging.getLogger(__name__)


def _log(tool: str, **kwargs: Any) -> None:
    detail = ", ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
    logger.info("MARKET TOOL: %s(%s)", tool, detail)


def _guard(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Attach explicit narration instructions for the model.

    Left to itself an LLM will smooth over a missing value with a plausible
    one. Saying the required behaviour in the payload, every time, is what
    keeps the spoken answer tied to the data.
    """
    if not payload.get("available"):
        payload["agent_instruction"] = (
            "This data is NOT available. Tell the farmer plainly that you could not get "
            "the market information and suggest they check with their local mandi. "
            "Do NOT state any price, trend or recommendation of your own."
        )
        return payload

    quality = payload.get("data_quality")
    if quality == "MOCK":
        payload["agent_instruction"] = (
            "This is DEMONSTRATION data. You must say so when you give the number. "
            "Never describe it as today's live market price."
        )
    elif quality in ("STALE", "DEGRADED"):
        payload["agent_instruction"] = (
            f"This data is {quality}. State the date of the price and tell the farmer to "
            "confirm at the mandi. Never call it today's price."
        )
    else:
        payload["agent_instruction"] = (
            "Report these figures exactly as given. Do not round them differently, "
            "do not add prices of your own, and do not invent markets."
        )
    return payload


# ---------------------------------------------------------------------------
# Tools exposed to Gemini
# ---------------------------------------------------------------------------

def get_market_price(commodity: str, location: str = "Kalaburagi") -> Dict[str, Any]:
    """
    Get the latest mandi price for a crop at or near the farmer's location.

    Use this whenever the farmer asks what their crop is worth, what today's
    rate is, or what price a mandi is paying.

    Args:
        commodity: The crop, in any language (e.g. "Onion", "eerulli", "kanda").
        location: The farmer's town or district (e.g. "Kalaburagi", "Bidar").

    Returns:
        Latest modal, minimum and maximum price per quintal with the arrival
        date, data quality and provenance.
    """
    _log("get_market_price", commodity=commodity, location=location)
    return _guard(service.get_price(commodity, location))


def compare_nearby_markets(
    commodity: str,
    location: str = "Kalaburagi",
    quantity: str = "10 quintal",
) -> Dict[str, Any]:
    """
    Compare nearby mandis and find which one pays the most AFTER transport costs.

    Use this when the farmer asks where to sell, which mandi is best, whether
    another market pays more, or how much they would actually keep.

    This accounts for freight, loading, market fees, commission and expected
    rejection, so the ranking reflects what the farmer really receives rather
    than the headline price.

    Args:
        commodity: The crop, in any language.
        location: The farmer's town or district.
        quantity: How much they have, e.g. "30 quintal", "500 kg", "2 tonne".

    Returns:
        Ranked markets with gross price, net realisation, distance and a full
        cost breakdown for each.
    """
    _log("compare_nearby_markets", commodity=commodity, location=location, quantity=quantity)
    return _guard(service.compare(commodity, location, quantity=quantity))


def get_price_trend(commodity: str, location: str = "Kalaburagi") -> Dict[str, Any]:
    """
    Get how a crop's price has moved over the last 7, 30 and 90 days.

    Use this when the farmer asks whether prices are rising or falling, what
    the average has been, or how today compares with recent weeks.

    Args:
        commodity: The crop, in any language.
        location: The farmer's town or district.

    Returns:
        Averages, highs, lows, percentage change, volatility and arrival
        volume trends for each window.
    """
    _log("get_price_trend", commodity=commodity, location=location)
    return _guard(service.get_trend(commodity, location))


def get_sale_recommendation(
    commodity: str,
    location: str = "Kalaburagi",
    quantity: str = "10 quintal",
    storage_days: int = 0,
    needs_cash_urgently: bool = False,
) -> Dict[str, Any]:
    """
    Advise whether the farmer should sell now or wait, and explain why.

    Use this when the farmer asks "should I sell now or wait?", "is this a
    good time to sell?", or "will the price go up?".

    IMPORTANT: ask the farmer how many days they can store the crop and
    whether they need money urgently before calling this. Both change the
    answer completely - a farmer with no storage must never be told to wait.

    Args:
        commodity: The crop, in any language.
        location: The farmer's town or district.
        quantity: How much they have, e.g. "30 quintal".
        storage_days: Days they can hold the crop. 0 means no storage.
        needs_cash_urgently: True if they need money immediately.

    Returns:
        A decision (SELL_NOW, WAIT, SELL_PARTIAL, COMPARE_MARKETS or
        INSUFFICIENT_DATA) with reasoning, risks, confidence and an expected
        price range when waiting is advised.
    """
    _log(
        "get_sale_recommendation", commodity=commodity, location=location,
        quantity=quantity, storage_days=storage_days, urgent=needs_cash_urgently,
    )
    return _guard(
        service.sale_window(
            commodity, location,
            quantity=quantity,
            storage_days=storage_days,
            needs_cash_urgently=needs_cash_urgently,
        )
    )


def list_supported_commodities() -> Dict[str, Any]:
    """
    List the crops this system holds market data for.

    Use this when the farmer names a crop you could not find, so you can tell
    them which crops you can actually help with.
    """
    _log("list_supported_commodities")
    payload = service.supported_commodities()
    payload["agent_instruction"] = (
        "Read out only a few relevant crop names, not the whole list."
    )
    return payload


MARKET_TOOLS = [
    get_market_price,
    compare_nearby_markets,
    get_price_trend,
    get_sale_recommendation,
    list_supported_commodities,
]
