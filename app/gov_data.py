"""
Government data integrations from data.gov.in APIs:
- Live Mandi/Market commodity prices
- Variety-wise daily market prices
- PMFBY crop insurance claims data
"""

import time
import logging
from typing import Dict, List, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ============================================
# API Resource IDs
# ============================================
MANDI_DAILY_PRICES_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
VARIETY_PRICES_RESOURCE = "35985678-0d79-46b4-9ed6-6f13308a1d24"
PMFBY_INSURANCE_RESOURCE = "8bc48fe3-d644-48c3-b459-a01e5cca0669"

DATA_GOV_BASE = "https://api.data.gov.in/resource"

# ============================================
# In-memory cache (15-minute TTL)
# ============================================
_cache: Dict[str, dict] = {}
CACHE_TTL = 900  # 15 minutes


def _cache_key(resource: str, **kwargs) -> str:
    parts = [resource] + [f"{k}={v}" for k, v in sorted(kwargs.items()) if v]
    return "|".join(parts)


def _get_cached(key: str) -> Optional[list]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        logger.debug(f"Cache HIT: {key[:60]}")
        return entry["data"]
    return None


def _set_cache(key: str, data: list):
    _cache[key] = {"data": data, "ts": time.time()}


# ============================================
# Mandi / Market Prices
# ============================================

async def fetch_mandi_prices(
    state: str = "Karnataka",
    district: str = "",
    commodity: str = "",
    limit: int = 20,
) -> List[Dict]:
    """
    Fetch current daily commodity prices from government mandis.
    Returns list of dicts with: state, district, market, commodity,
    variety, min_price, max_price, modal_price, arrival_date
    """
    ck = _cache_key(MANDI_DAILY_PRICES_RESOURCE, state=state, district=district, commodity=commodity)
    cached = _get_cached(ck)
    if cached is not None:
        return cached

    settings = get_settings()
    params = {
        "api-key": settings.DATA_GOV_API_KEY,
        "format": "json",
        "limit": str(limit),
        "offset": "0",
    }
    # Build filters
    filters = {}
    if state:
        filters["filters[state.keyword]"] = state
    if district:
        filters["filters[district]"] = district
    if commodity:
        filters["filters[commodity]"] = commodity
    params.update(filters)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{DATA_GOV_BASE}/{MANDI_DAILY_PRICES_RESOURCE}"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("records", [])
            total = data.get("total", 0)
            logger.info(f"Mandi prices: {len(records)} records (total={total}) for {state}/{district}/{commodity}")
            _set_cache(ck, records)
            return records

    except Exception as e:
        logger.warning(f"Mandi prices API failed: {e} — returning empty")
        return []


# ============================================
# Variety-wise Prices (historical)
# ============================================

async def fetch_variety_prices(
    state: str = "Karnataka",
    district: str = "",
    commodity: str = "",
    limit: int = 20,
) -> List[Dict]:
    """
    Fetch variety-wise daily market prices.
    State and District are mandatory filters for this API.
    """
    if not state or not district:
        logger.warning("Variety prices require both state and district")
        return []

    ck = _cache_key(VARIETY_PRICES_RESOURCE, state=state, district=district, commodity=commodity)
    cached = _get_cached(ck)
    if cached is not None:
        return cached

    settings = get_settings()
    params = {
        "api-key": settings.DATA_GOV_API_KEY,
        "format": "json",
        "limit": str(limit),
        "offset": "0",
        "filters[State]": state,
        "filters[District]": district,
    }
    if commodity:
        params["filters[Commodity]"] = commodity

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{DATA_GOV_BASE}/{VARIETY_PRICES_RESOURCE}"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("records", [])
            logger.info(f"Variety prices: {len(records)} records for {state}/{district}")
            _set_cache(ck, records)
            return records

    except Exception as e:
        logger.warning(f"Variety prices API failed: {e}")
        return []


# ============================================
# PMFBY Insurance Data
# ============================================

async def fetch_pmfby_data(
    district: str = "",
    limit: int = 20,
) -> List[Dict]:
    """
    Fetch PMFBY crop insurance enrollment and claims data for Karnataka.
    """
    ck = _cache_key(PMFBY_INSURANCE_RESOURCE, district=district)
    cached = _get_cached(ck)
    if cached is not None:
        return cached

    settings = get_settings()
    params = {
        "api-key": settings.DATA_GOV_API_KEY,
        "format": "json",
        "limit": str(limit),
        "offset": "0",
    }
    if district:
        params["filters[district_name]"] = district

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{DATA_GOV_BASE}/{PMFBY_INSURANCE_RESOURCE}"
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            records = data.get("records", [])
            logger.info(f"PMFBY data: {len(records)} records for district={district}")
            _set_cache(ck, records)
            return records

    except Exception as e:
        logger.warning(f"PMFBY API failed: {e}")
        return []


# ============================================
# Convenience helpers for voice agent / dashboard
# ============================================

async def get_market_price_summary(
    state: str = "Karnataka",
    district: str = "",
    commodity: str = "",
) -> str:
    """
    Returns a human-readable summary of market prices.
    Used by the voice agent and dashboard.
    """
    records = await fetch_mandi_prices(state=state, district=district, commodity=commodity, limit=10)
    if not records:
        return f"No live market price data available for {commodity or 'crops'} in {district or state}."

    lines = []
    for r in records[:5]:
        name = r.get("commodity", "Unknown")
        market = r.get("market", "")
        modal = r.get("modal_price", 0)
        min_p = r.get("min_price", 0)
        max_p = r.get("max_price", 0)
        date = r.get("arrival_date", "")
        lines.append(
            f"- {name} in {market}: Rs.{modal}/quintal (range Rs.{min_p}-{max_p}) as of {date}"
        )

    header = f"Live market prices for {commodity or 'various commodities'} in {district or state}:"
    return header + "\n" + "\n".join(lines)
