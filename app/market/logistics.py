"""
Transport cost estimation and net realisable price.

The headline mandi price is not what the farmer receives. A market paying
150/quintal more but sitting 90 km further away can easily be the worse
choice once a hired vehicle, loading labour, APMC cess and commission are
paid. This module makes that arithmetic explicit, so a recommendation can be
defended line by line rather than asserted.

Every rate here is a documented assumption with a named source field, not a
scraped fact. They are presented as estimates and must never be narrated as
quoted prices from a specific transporter.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.market.normalization import KG_PER_QUINTAL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VehicleClass:
    """A hireable vehicle tier with its indicative freight rate."""

    name: str
    capacity_kg: float
    rate_per_km: float
    minimum_fare: float
    # Loading plus unloading labour, per quintal, for this tier.
    handling_per_quintal: float


# Indicative Karnataka/Maharashtra spot-hire rates. Wide tiers rather than
# precise quotes: the goal is a defensible comparison between markets, and
# the same model is applied to every market so the ranking stays fair.
VEHICLE_CLASSES: Tuple[VehicleClass, ...] = (
    VehicleClass("Tempo (Chhota Hathi)", 1_000, 22.0, 900.0, 12.0),
    VehicleClass("Pickup (1.5 ton)", 1_500, 26.0, 1_200.0, 12.0),
    VehicleClass("Mini truck (3 ton)", 3_000, 34.0, 2_000.0, 10.0),
    VehicleClass("Truck (7 ton)", 7_000, 48.0, 3_500.0, 9.0),
    VehicleClass("Truck (9 ton)", 9_000, 55.0, 4_500.0, 9.0),
    VehicleClass("Multi-axle (16 ton)", 16_000, 72.0, 7_000.0, 8.0),
)

# APMC statutory charges. Market fee and commission vary by state and
# commodity; these are typical mid-range values and are reported as such.
DEFAULT_MARKET_FEE_PCT = 1.0
DEFAULT_COMMISSION_PCT = 2.0

# Share of a consignment typically lost to grading rejection at the yard.
# Perishables are penalised harder, and a longer haul raises the risk.
BASE_REJECTION_PCT = 1.0
PERISHABLE_REJECTION_PCT = 2.5
REJECTION_PER_100KM_PCT = 0.75

COST_MODEL_VERSION = "logistics-v1"
COST_MODEL_SOURCE = "indicative spot-hire rates; estimate, not a transporter quote"


@dataclass
class TransportQuote:
    vehicle: str
    distance_km: float
    quantity_kg: float
    trips: int
    freight_cost: float
    handling_cost: float
    total_cost: float
    cost_per_quintal: float
    cost_per_kg: float
    is_estimate: bool = True
    source: str = COST_MODEL_SOURCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vehicle": self.vehicle,
            "distance_km": round(self.distance_km, 1),
            "quantity_kg": round(self.quantity_kg, 1),
            "trips": self.trips,
            "freight_cost": round(self.freight_cost, 2),
            "handling_cost": round(self.handling_cost, 2),
            "total_cost": round(self.total_cost, 2),
            "cost_per_quintal": round(self.cost_per_quintal, 2),
            "cost_per_kg": round(self.cost_per_kg, 2),
            "is_estimate": self.is_estimate,
            "source": self.source,
            "model_version": COST_MODEL_VERSION,
        }


@dataclass
class NetRealisation:
    """Full cost breakdown from gross mandi price to what the farmer keeps."""

    market: str
    gross_price_per_quintal: float
    transport_per_quintal: float
    handling_per_quintal: float
    market_fee_per_quintal: float
    commission_per_quintal: float
    rejection_loss_per_quintal: float
    net_price_per_quintal: float
    quantity_kg: float
    total_gross: float
    total_net: float
    distance_km: float
    vehicle: str
    deductions_pct: float
    breakdown: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "gross_price_per_quintal": round(self.gross_price_per_quintal, 2),
            "net_price_per_quintal": round(self.net_price_per_quintal, 2),
            "deductions_per_quintal": {
                "transport": round(self.transport_per_quintal, 2),
                "handling": round(self.handling_per_quintal, 2),
                "market_fee": round(self.market_fee_per_quintal, 2),
                "commission": round(self.commission_per_quintal, 2),
                "expected_rejection_loss": round(self.rejection_loss_per_quintal, 2),
            },
            "deductions_pct": round(self.deductions_pct, 2),
            "quantity_kg": round(self.quantity_kg, 1),
            "total_gross": round(self.total_gross, 2),
            "total_net": round(self.total_net, 2),
            "distance_km": round(self.distance_km, 1),
            "vehicle": self.vehicle,
            "breakdown": self.breakdown,
            "model_version": COST_MODEL_VERSION,
        }


def select_vehicle(quantity_kg: float) -> VehicleClass:
    """
    Smallest vehicle that carries the load in one trip.

    Falls back to the largest tier for consignments beyond it; the trip count
    in estimate_transport then handles the repetition.
    """
    for vehicle in VEHICLE_CLASSES:
        if quantity_kg <= vehicle.capacity_kg:
            return vehicle
    return VEHICLE_CLASSES[-1]


def estimate_transport(
    distance_km: float,
    quantity_kg: float,
    vehicle: Optional[VehicleClass] = None,
) -> TransportQuote:
    """
    Estimate the cost of moving a consignment to a market.

    Freight is charged on the loaded leg only; the minimum fare absorbs the
    operator's return trip, which is how spot hire is actually quoted locally.
    """
    if quantity_kg <= 0:
        raise ValueError("quantity_kg must be positive")
    distance_km = max(float(distance_km), 0.0)

    vehicle = vehicle or select_vehicle(quantity_kg)
    trips = max(1, math.ceil(quantity_kg / vehicle.capacity_kg))

    freight = max(vehicle.rate_per_km * distance_km, vehicle.minimum_fare) * trips
    quintals = quantity_kg / KG_PER_QUINTAL
    handling = vehicle.handling_per_quintal * quintals
    total = freight + handling

    return TransportQuote(
        vehicle=vehicle.name,
        distance_km=distance_km,
        quantity_kg=quantity_kg,
        trips=trips,
        freight_cost=freight,
        handling_cost=handling,
        total_cost=total,
        cost_per_quintal=total / quintals if quintals else 0.0,
        cost_per_kg=total / quantity_kg,
    )


def expected_rejection_pct(distance_km: float, perishable: bool) -> float:
    """Grading-rejection allowance, rising with haul length for perishables."""
    base = PERISHABLE_REJECTION_PCT if perishable else BASE_REJECTION_PCT
    return base + (distance_km / 100.0) * REJECTION_PER_100KM_PCT * (1.5 if perishable else 1.0)


def compute_net_realisation(
    market: str,
    gross_price_per_quintal: float,
    distance_km: float,
    quantity_kg: float,
    *,
    perishable: bool = False,
    market_fee_pct: float = DEFAULT_MARKET_FEE_PCT,
    commission_pct: float = DEFAULT_COMMISSION_PCT,
    vehicle: Optional[VehicleClass] = None,
) -> NetRealisation:
    """
    Reduce a gross mandi price to what the farmer actually keeps per quintal.

    Every deduction appears in `breakdown` with its basis, so the voice layer
    can answer "why is that market worse?" with real numbers.
    """
    if gross_price_per_quintal <= 0:
        raise ValueError("gross_price_per_quintal must be positive")

    quote = estimate_transport(distance_km, quantity_kg, vehicle=vehicle)
    quintals = quantity_kg / KG_PER_QUINTAL

    transport_pq = quote.freight_cost / quintals if quintals else 0.0
    handling_pq = quote.handling_cost / quintals if quintals else 0.0
    fee_pq = gross_price_per_quintal * (market_fee_pct / 100.0)
    commission_pq = gross_price_per_quintal * (commission_pct / 100.0)

    rejection_pct = expected_rejection_pct(distance_km, perishable)
    rejection_pq = gross_price_per_quintal * (rejection_pct / 100.0)

    net_pq = gross_price_per_quintal - transport_pq - handling_pq - fee_pq - commission_pq - rejection_pq
    total_gross = gross_price_per_quintal * quintals
    total_net = net_pq * quintals
    deductions_pct = (
        ((gross_price_per_quintal - net_pq) / gross_price_per_quintal) * 100.0
        if gross_price_per_quintal else 0.0
    )

    breakdown = [
        {"item": "Gross mandi price", "per_quintal": round(gross_price_per_quintal, 2), "basis": "modal price"},
        {"item": "Freight", "per_quintal": -round(transport_pq, 2),
         "basis": f"{quote.vehicle}, {quote.distance_km:.0f} km, {quote.trips} trip(s)"},
        {"item": "Loading and unloading", "per_quintal": -round(handling_pq, 2), "basis": "labour, both ends"},
        {"item": "Market fee", "per_quintal": -round(fee_pq, 2), "basis": f"{market_fee_pct}% APMC cess"},
        {"item": "Commission", "per_quintal": -round(commission_pq, 2), "basis": f"{commission_pct}% agent commission"},
        {"item": "Expected rejection loss", "per_quintal": -round(rejection_pq, 2),
         "basis": f"{rejection_pct:.1f}% grading allowance"},
        {"item": "Net realisation", "per_quintal": round(net_pq, 2), "basis": "what the farmer keeps"},
    ]

    return NetRealisation(
        market=market,
        gross_price_per_quintal=gross_price_per_quintal,
        transport_per_quintal=transport_pq,
        handling_per_quintal=handling_pq,
        market_fee_per_quintal=fee_pq,
        commission_per_quintal=commission_pq,
        rejection_loss_per_quintal=rejection_pq,
        net_price_per_quintal=net_pq,
        quantity_kg=quantity_kg,
        total_gross=total_gross,
        total_net=total_net,
        distance_km=distance_km,
        vehicle=quote.vehicle,
        deductions_pct=deductions_pct,
        breakdown=breakdown,
    )


def storage_cost_per_quintal(days: int, cold_storage: bool = False) -> float:
    """
    Indicative holding cost, used by the sale-window engine.

    Advising a farmer to wait is only sound if the carry cost is subtracted
    from the expected gain.
    """
    if days <= 0:
        return 0.0
    daily = 2.5 if cold_storage else 0.6
    return daily * days
