"""
Demo dataset generator - DEVELOPMENT AND DEMONSTRATION ONLY.

Every record produced here is written with data_quality = MOCK. That tag
travels with the data through the repository, the analytics and out to the
API and voice layers, which is what prevents it from ever being narrated as a
live government price.

The generator exists because the live source (data.gov.in) requires a
registered API key the project does not yet have. It is a stand-in for a
missing credential, not a substitute for the integration: the ingestion
pipeline, validation rules and storage schema are identical either way, so
swapping in a real key changes nothing but the source of the rows.

Series are deterministic for a given seed so tests and demos are reproducible.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from app.market.provenance import DataQuality
from app.market.repository import PriceRecord, get_market_by_name, upsert_price_records

logger = logging.getLogger(__name__)

SOURCE_DEMO = "demo:generated-fixture"

DEFAULT_SEED = 20260911


@dataclass(frozen=True)
class CommodityProfile:
    """Shape of a synthetic price series for one commodity."""

    commodity: str
    base_price: float          # INR/quintal
    seasonal_amplitude: float  # fraction of base
    seasonal_peak_day: int     # day-of-year where the season tops out
    daily_volatility: float    # random-walk step as a fraction of base
    base_arrival_tonnes: float


# Levels chosen to sit inside the validation bands and to look like the
# commodity actually behaves: onion volatile, cereals steady.
DEMO_PROFILES: Tuple[CommodityProfile, ...] = (
    CommodityProfile("Onion", 2_400.0, 0.28, 300, 0.035, 450.0),
    CommodityProfile("Tomato", 1_900.0, 0.35, 180, 0.055, 220.0),
    CommodityProfile("Potato", 1_600.0, 0.18, 330, 0.025, 300.0),
    CommodityProfile("Tur", 9_800.0, 0.12, 60, 0.015, 90.0),
    CommodityProfile("Bengal Gram", 6_200.0, 0.10, 90, 0.013, 120.0),
    CommodityProfile("Maize", 2_250.0, 0.14, 150, 0.016, 380.0),
    CommodityProfile("Ragi", 3_650.0, 0.11, 120, 0.014, 110.0),
    CommodityProfile("Jowar", 3_100.0, 0.12, 110, 0.015, 140.0),
    CommodityProfile("Cotton", 7_400.0, 0.09, 45, 0.012, 160.0),
    CommodityProfile("Chilli", 18_500.0, 0.20, 30, 0.030, 70.0),
)

DEMO_PROFILE_BY_NAME: Dict[str, CommodityProfile] = {p.commodity: p for p in DEMO_PROFILES}

# Markets used for the demo, with a structural price premium/discount.
# Terminal and export markets pay more; interior yards pay less. This is what
# makes the net-realisation comparison produce a meaningful answer.
DEMO_MARKETS: Tuple[Tuple[str, float], ...] = (
    ("Kalaburagi", 1.000),
    ("Chittapur", 0.972),
    ("Jevargi", 0.968),
    ("Aland", 0.985),
    ("Sedam", 0.978),
    ("Afzalpur", 0.975),
    ("Humnabad", 1.021),
    ("Basavakalyan", 1.034),
    ("Bidar", 1.045),
    ("Yadgir", 0.990),
    ("Shahapur", 0.981),
    ("Solapur", 1.058),
    ("Lasalgaon", 1.072),
    ("Pimpalgaon Baswant", 1.065),
    ("Nashik", 1.061),
    ("Pune", 1.088),
)


def _seasonal_factor(profile: CommodityProfile, day: datetime) -> float:
    """Smooth annual cycle peaking at the profile's peak day."""
    doy = day.timetuple().tm_yday
    phase = 2 * math.pi * (doy - profile.seasonal_peak_day) / 365.0
    return 1.0 + profile.seasonal_amplitude * math.cos(phase)


def generate_series(
    profile: CommodityProfile,
    market: str,
    market_factor: float,
    days: int,
    end: datetime,
    rng: random.Random,
) -> List[Tuple[datetime, float, float, float, float]]:
    """
    Build one market's series.

    Returns (date, min, max, modal, arrival_tonnes) per day. Arrivals move
    inversely to price so the arrival-pressure signal has something real to
    read, which is the behaviour the sale-window engine expects.
    """
    out = []
    # Mean-reverting random walk: drift wanders but is pulled back, so the
    # series stays inside plausible bounds over a long window.
    drift = 0.0
    for offset in range(days, -1, -1):
        day = end - timedelta(days=offset)
        drift = drift * 0.92 + rng.gauss(0.0, profile.daily_volatility)
        level = profile.base_price * market_factor * _seasonal_factor(profile, day) * (1.0 + drift)
        level = max(level, profile.base_price * 0.35)

        spread = level * rng.uniform(0.06, 0.16)
        modal = round(level, -1)
        low = round(max(level - spread, level * 0.5), -1)
        high = round(level + spread, -1)

        # Higher prices accompany thinner arrivals.
        price_ratio = level / (profile.base_price * market_factor)
        arrivals = profile.base_arrival_tonnes * max(0.25, 2.0 - price_ratio) * rng.uniform(0.75, 1.25)

        out.append((day, low, high, modal, round(arrivals, 1)))
    return out


def seed_demo_prices(
    commodities: Optional[Sequence[str]] = None,
    markets: Optional[Sequence[Tuple[str, float]]] = None,
    days: int = 120,
    end: Optional[datetime] = None,
    seed: int = DEFAULT_SEED,
) -> Dict[str, int]:
    """
    Populate price history with clearly-tagged demo data.

    Returns counts. Every row carries data_quality=MOCK and
    source='demo:generated-fixture'; nothing here can masquerade as real.
    """
    end = end or datetime.now(timezone.utc)
    profiles = [
        DEMO_PROFILE_BY_NAME[c] for c in (commodities or DEMO_PROFILE_BY_NAME.keys())
        if c in DEMO_PROFILE_BY_NAME
    ]
    market_list = list(markets or DEMO_MARKETS)

    rng = random.Random(seed)
    records: List[PriceRecord] = []

    for profile in profiles:
        for market_name, factor in market_list:
            market_row = get_market_by_name(market_name)
            if market_row is None:
                logger.debug("Demo seed: market %s not in registry, skipping", market_name)
                continue

            series = generate_series(profile, market_name, factor, days, end, rng)
            for day, low, high, modal, arrivals in series:
                records.append(
                    PriceRecord(
                        state=market_row.state,
                        district=market_row.district,
                        market=market_row.name,
                        market_id=market_row.id,
                        commodity=profile.commodity,
                        variety=None,
                        grade="FAQ",
                        min_price=low,
                        max_price=high,
                        modal_price=modal,
                        price_unit="INR/quintal",
                        arrival_qty=arrivals,
                        arrival_unit="tonne",
                        arrival_date=day.date().isoformat(),
                        source=SOURCE_DEMO,
                        data_quality=DataQuality.MOCK,
                    )
                )

    inserted, duplicates = upsert_price_records(records)
    logger.warning(
        "Seeded DEMO market data: %d inserted, %d updated, across %d commodities and %d markets. "
        "All rows are tagged data_quality=MOCK.",
        inserted, duplicates, len(profiles), len(market_list),
    )
    return {
        "inserted": inserted,
        "updated": duplicates,
        "commodities": len(profiles),
        "markets": len(market_list),
        "days": days,
        "data_quality": DataQuality.MOCK.value,
    }


def clear_demo_prices() -> int:
    """Remove every demo row, leaving real observations untouched."""
    from app.services.db import DB_LOCK, get_connection

    with DB_LOCK:
        conn = get_connection()
        try:
            cur = conn.execute("DELETE FROM mandi_price_history WHERE source = ?", (SOURCE_DEMO,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
