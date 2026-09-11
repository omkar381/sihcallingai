"""
Multi-market comparison and ranking.

Answers "where should I take this crop?" by pricing every reachable market on
the same basis: gross modal price, minus the real cost of getting the
consignment there, producing a net realisation the farmer can compare directly.

Markets whose latest observation is too old to be comparable are excluded
rather than ranked, because comparing today's print against a three-week-old
one manufactures a confident, wrong answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.market.logistics import compute_net_realisation, NetRealisation
from app.market.normalization import COMMODITY_BY_CANONICAL, resolve_commodity
from app.market.provenance import Confidence, DataQuality, Provenance
from app.market.repository import (
    Market,
    PriceRecord,
    find_markets_near,
    get_latest_prices_for_markets,
    resolve_origin,
)

logger = logging.getLogger(__name__)

# Observations older than this are not comparable against each other.
MAX_COMPARISON_AGE_DAYS = 10

DEFAULT_RADIUS_KM = 150.0
DEFAULT_QUANTITY_KG = 1_000.0


@dataclass
class MarketOption:
    """One market, priced end-to-end for this specific consignment."""

    market: str
    district: str
    state: str
    distance_km: float
    gross_price_per_quintal: float
    net_price_per_quintal: float
    total_net: float
    arrival_date: str
    data_quality: DataQuality
    arrival_qty: Optional[float]
    net_realisation: NetRealisation
    provenance: Provenance
    is_origin: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "district": self.district,
            "state": self.state,
            "distance_km": round(self.distance_km, 1),
            "gross_price_per_quintal": round(self.gross_price_per_quintal, 2),
            "net_price_per_quintal": round(self.net_price_per_quintal, 2),
            "total_net": round(self.total_net, 2),
            "arrival_date": self.arrival_date,
            "arrival_qty": self.arrival_qty,
            "data_quality": self.data_quality.value,
            "is_origin": self.is_origin,
            "cost_breakdown": self.net_realisation.breakdown,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class MarketComparison:
    commodity: str
    origin: Optional[str]
    quantity_kg: float
    options: List[MarketOption] = field(default_factory=list)
    best: Optional[MarketOption] = None
    origin_option: Optional[MarketOption] = None
    advantage_per_quintal: Optional[float] = None
    advantage_total: Optional[float] = None
    confidence: Confidence = Confidence.INSUFFICIENT_DATA
    data_quality: DataQuality = DataQuality.UNAVAILABLE
    markets_considered: int = 0
    markets_with_usable_prices: int = 0
    markets_excluded_stale: int = 0
    markets_without_data: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(self.options)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "origin": self.origin,
            "quantity_kg": round(self.quantity_kg, 1),
            "options": [o.to_dict() for o in self.options],
            "best_market": self.best.market if self.best else None,
            "advantage_per_quintal": (
                round(self.advantage_per_quintal, 2)
                if self.advantage_per_quintal is not None else None
            ),
            "advantage_total": (
                round(self.advantage_total, 2) if self.advantage_total is not None else None
            ),
            "confidence": self.confidence.value,
            "data_quality": self.data_quality.value,
            "coverage": {
                "markets_considered": self.markets_considered,
                "markets_with_usable_prices": self.markets_with_usable_prices,
                "markets_displayed": len(self.options),
                "markets_excluded_stale": self.markets_excluded_stale,
                "markets_without_data": self.markets_without_data,
            },
            "notes": self.notes,
        }


def compare_markets(
    commodity: str,
    origin_location: str,
    quantity_kg: float = DEFAULT_QUANTITY_KG,
    radius_km: float = DEFAULT_RADIUS_KM,
    max_options: int = 6,
) -> MarketComparison:
    """
    Rank reachable markets by net realisation for this consignment.

    Returns an empty comparison with explanatory notes when data is missing,
    never a fabricated ranking.
    """
    match = resolve_commodity(commodity)
    canonical = match.canonical if match else None

    result = MarketComparison(
        commodity=canonical or commodity,
        origin=origin_location,
        quantity_kg=quantity_kg,
    )

    if canonical is None:
        result.notes.append(
            f"'{commodity}' is not a recognised commodity, so no market comparison could be made."
        )
        return result

    origin = resolve_origin(origin_location)
    if origin is None or origin.latitude is None:
        result.notes.append(
            f"Location '{origin_location}' could not be matched to a known market, "
            "so distances and transport costs cannot be computed."
        )
        return result

    result.origin = origin.name

    nearby: List[Market] = find_markets_near(
        origin.latitude, origin.longitude, radius_km=radius_km, limit=max_options * 3
    )
    result.markets_considered = len(nearby)
    if not nearby:
        result.notes.append(f"No registered markets within {radius_km:.0f} km of {origin.name}.")
        return result

    latest = get_latest_prices_for_markets(
        canonical, [m.name for m in nearby], max_age_days=MAX_COMPARISON_AGE_DAYS
    )

    perishable = COMMODITY_BY_CANONICAL[canonical].perishable
    options: List[MarketOption] = []

    for market in nearby:
        record: Optional[PriceRecord] = latest.get(market.name.lower())
        if record is None:
            result.markets_without_data += 1
            continue

        provenance = record.provenance()
        if provenance.data_quality in (DataQuality.UNAVAILABLE, DataQuality.DEGRADED):
            result.markets_excluded_stale += 1
            continue

        try:
            net = compute_net_realisation(
                market=market.name,
                gross_price_per_quintal=record.modal_price,
                distance_km=market.distance_km or 0.0,
                quantity_kg=quantity_kg,
                perishable=perishable,
            )
        except ValueError as exc:
            logger.warning("Net realisation failed for %s: %s", market.name, exc)
            continue

        options.append(
            MarketOption(
                market=market.name,
                district=market.district,
                state=market.state,
                distance_km=market.distance_km or 0.0,
                gross_price_per_quintal=record.modal_price,
                net_price_per_quintal=net.net_price_per_quintal,
                total_net=net.total_net,
                arrival_date=record.arrival_date,
                data_quality=provenance.data_quality,
                arrival_qty=record.arrival_qty,
                net_realisation=net,
                provenance=provenance,
                is_origin=(market.name.lower() == origin.name.lower()),
            )
        )

    if not options:
        result.notes.append(
            f"No market within {radius_km:.0f} km has a {canonical} price from the last "
            f"{MAX_COMPARISON_AGE_DAYS} days. Price comparison is unavailable, not zero."
        )
        return result

    options.sort(key=lambda o: o.net_price_per_quintal, reverse=True)
    result.markets_with_usable_prices = len(options)

    result.best = options[0]
    result.origin_option = next((o for o in options if o.is_origin), None)

    shortlist = options[:max_options]
    # The farmer's own market is the baseline for every comparison, so it must
    # always be shown even when it ranks below the cut. Being told "Solapur is
    # better" is useless without seeing what the local yard pays.
    if result.origin_option is not None and result.origin_option not in shortlist:
        shortlist = shortlist[: max_options - 1] + [result.origin_option]
        shortlist.sort(key=lambda o: o.net_price_per_quintal, reverse=True)
    result.options = shortlist

    baseline = result.origin_option or (result.options[-1] if len(result.options) > 1 else None)
    if baseline is not None and baseline is not result.best:
        result.advantage_per_quintal = (
            result.best.net_price_per_quintal - baseline.net_price_per_quintal
        )
        result.advantage_total = result.best.total_net - baseline.total_net

    qualities = [o.data_quality for o in result.options]
    result.data_quality = min(qualities, key=lambda q: q.rank)

    if result.markets_with_usable_prices >= 3 and result.data_quality.presentable_as_current:
        result.confidence = Confidence.HIGH
    elif result.markets_with_usable_prices >= 2:
        result.confidence = Confidence.MEDIUM
    else:
        result.confidence = Confidence.LOW
        result.notes.append("Only one market had usable data, so no comparison was possible.")

    if result.markets_without_data:
        result.notes.append(
            f"{result.markets_without_data} nearby market(s) had no {canonical} price on record."
        )
    if result.markets_excluded_stale:
        result.notes.append(
            f"{result.markets_excluded_stale} market(s) were excluded because their data was stale."
        )

    return result
