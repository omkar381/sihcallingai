"""
Farmer lot to buyer demand matching.

Scores every open demand against a lot on seven weighted dimensions and
returns a ranked, explained shortlist. Explanation is not decoration: a
farmer deciding where to send a truckload deserves to see why one buyer
ranked above another, and an evaluator deserves to see that the ranking is
not a black box.

Hard requirements are enforced before scoring, not folded into it. A buyer
who needs Grade A cannot be offered Grade C produce at any score, and a
suspended buyer never appears at all. Scoring only ranks candidates that are
already legitimate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.market.logistics import compute_net_realisation
from app.market.normalization import COMMODITY_BY_CANONICAL
from app.market.repository import resolve_origin, road_distance_km
from app.trade.buyers import VerificationStatus, compute_trust, list_open_demand
from app.trade.schema import now_ts

logger = logging.getLogger(__name__)

# Weights sum to 100. Commodity dominates because a wrong-crop match is
# useless however good the price; buyer reliability is last because a farmer
# can mitigate it with payment terms, but cannot mitigate the wrong crop.
WEIGHTS = {
    "commodity": 25.0,
    "quantity": 15.0,
    "quality": 15.0,
    "price": 15.0,
    "distance": 10.0,
    "window": 10.0,
    "reliability": 10.0,
}

GRADE_RANK = {"A": 3, "FAQ": 3, "B": 2, "C": 1, "UNGRADED": 0}


@dataclass
class MatchFactor:
    name: str
    score: float        # 0..1
    weight: float
    detail: str

    @property
    def points(self) -> float:
        return self.score * self.weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factor": self.name,
            "score_pct": round(self.score * 100, 1),
            "weight": self.weight,
            "points": round(self.points, 2),
            "detail": self.detail,
        }


@dataclass
class Match:
    demand_id: str
    buyer_id: str
    buyer_name: str
    score: int
    commodity: str
    price_offered: Optional[float]
    quantity_needed_kg: float
    distance_km: Optional[float]
    net_price_per_quintal: Optional[float]
    estimated_total_net: Optional[float]
    badge: Dict[str, Any]
    factors: List[MatchFactor] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "demand_id": self.demand_id,
            "buyer_id": self.buyer_id,
            "buyer_name": self.buyer_name,
            "match_score": self.score,
            "commodity": self.commodity,
            "price_offered": self.price_offered,
            "quantity_needed_kg": self.quantity_needed_kg,
            "distance_km": round(self.distance_km, 1) if self.distance_km is not None else None,
            "net_price_per_quintal": (
                round(self.net_price_per_quintal, 2)
                if self.net_price_per_quintal is not None else None
            ),
            "estimated_total_net": (
                round(self.estimated_total_net, 2)
                if self.estimated_total_net is not None else None
            ),
            "badge": self.badge,
            "factors": [f.to_dict() for f in self.factors],
            "reasons": self.reasons,
            "warnings": self.warnings,
        }


def _score_commodity(lot: Dict[str, Any], demand: Dict[str, Any]) -> MatchFactor:
    if lot["commodity"] != demand["commodity"]:
        return MatchFactor("commodity", 0.0, WEIGHTS["commodity"], "Different commodity")

    lot_variety, want_variety = lot.get("variety"), demand.get("variety")
    if want_variety and lot_variety and want_variety.lower() != lot_variety.lower():
        return MatchFactor(
            "commodity", 0.6, WEIGHTS["commodity"],
            f"Commodity matches but buyer wants {want_variety} variety",
        )
    return MatchFactor("commodity", 1.0, WEIGHTS["commodity"],
                       f"Buyer is purchasing {lot['commodity']}")


def _score_quantity(lot: Dict[str, Any], demand: Dict[str, Any]) -> MatchFactor:
    lot_kg = float(lot["quantity_kg"])
    remaining = float(demand["quantity_remaining_kg"])
    min_lot = demand.get("min_lot_kg")

    if min_lot and lot_kg < float(min_lot):
        shortfall = float(min_lot) - lot_kg
        return MatchFactor(
            "quantity", 0.15, WEIGHTS["quantity"],
            f"Below the buyer's {float(min_lot):,.0f} kg minimum by {shortfall:,.0f} kg",
        )

    if remaining <= 0:
        return MatchFactor("quantity", 0.0, WEIGHTS["quantity"], "Buyer's requirement is already filled")

    ratio = lot_kg / remaining
    if 0.8 <= ratio <= 1.2:
        return MatchFactor("quantity", 1.0, WEIGHTS["quantity"],
                           f"Lot size closely matches the {remaining:,.0f} kg still needed")
    if ratio < 0.8:
        # Partial fills are useful, just less so.
        return MatchFactor("quantity", 0.5 + 0.5 * ratio, WEIGHTS["quantity"],
                           f"Covers {ratio * 100:.0f}% of the {remaining:,.0f} kg needed")
    return MatchFactor("quantity", 0.75, WEIGHTS["quantity"],
                       f"Lot is larger than the {remaining:,.0f} kg needed; a partial sale is possible")


def _score_quality(lot: Dict[str, Any], demand: Dict[str, Any]) -> MatchFactor:
    want = (demand.get("min_grade") or "").upper()
    have = (lot.get("grade") or "UNGRADED").upper()

    if not want:
        return MatchFactor("quality", 0.8, WEIGHTS["quality"], "Buyer has not specified a grade")

    want_rank = GRADE_RANK.get(want, 0)
    have_rank = GRADE_RANK.get(have, 0)

    if have == "UNGRADED":
        return MatchFactor("quality", 0.3, WEIGHTS["quality"],
                           f"Buyer wants grade {want} but this lot is ungraded")
    if have_rank >= want_rank:
        bonus = 1.0 if lot.get("grade_is_evidence") else 0.85
        note = "inspected" if lot.get("grade_is_evidence") else "self-declared"
        return MatchFactor("quality", bonus, WEIGHTS["quality"],
                           f"Grade {have} meets the buyer's minimum of {want} ({note})")

    # Zero, not a low score. The buyer stated a minimum grade, so this is a
    # sale that cannot happen - showing it to the farmer would send them to a
    # buyer who will reject the consignment on arrival.
    return MatchFactor("quality", 0.0, WEIGHTS["quality"],
                       f"Grade {have} is below the buyer's minimum of {want}")


def _score_price(lot: Dict[str, Any], demand: Dict[str, Any]) -> MatchFactor:
    offered = demand.get("price_offered")
    expected = lot.get("expected_price")

    if offered is None:
        return MatchFactor("price", 0.5, WEIGHTS["price"], "Buyer has not published a price")
    if not expected:
        return MatchFactor("price", 0.8, WEIGHTS["price"],
                           f"Buyer offers {offered:,.0f} per quintal")

    ratio = float(offered) / float(expected)
    if ratio >= 1.0:
        return MatchFactor("price", 1.0, WEIGHTS["price"],
                           f"Offer of {offered:,.0f} meets or beats your {expected:,.0f} expectation")
    if ratio >= 0.9:
        return MatchFactor("price", 0.7, WEIGHTS["price"],
                           f"Offer of {offered:,.0f} is slightly below your {expected:,.0f} expectation")
    return MatchFactor("price", max(0.1, ratio * 0.5), WEIGHTS["price"],
                       f"Offer of {offered:,.0f} is well below your {expected:,.0f} expectation")


def _score_distance(distance_km: Optional[float], demand: Dict[str, Any]) -> MatchFactor:
    if distance_km is None:
        return MatchFactor("distance", 0.5, WEIGHTS["distance"], "Distance could not be determined")

    limit = demand.get("max_distance_km")
    if limit and distance_km > float(limit):
        return MatchFactor("distance", 0.0, WEIGHTS["distance"],
                           f"{distance_km:.0f} km exceeds the buyer's {float(limit):.0f} km limit")

    if distance_km <= 50:
        return MatchFactor("distance", 1.0, WEIGHTS["distance"], f"Only {distance_km:.0f} km away")
    if distance_km <= 150:
        return MatchFactor("distance", 0.75, WEIGHTS["distance"], f"{distance_km:.0f} km away")
    if distance_km <= 300:
        return MatchFactor("distance", 0.4, WEIGHTS["distance"],
                           f"{distance_km:.0f} km away, so freight will be significant")
    return MatchFactor("distance", 0.15, WEIGHTS["distance"],
                       f"{distance_km:.0f} km away, a long haul")


def _score_window(lot: Dict[str, Any], demand: Dict[str, Any]) -> MatchFactor:
    d_start, d_end = demand.get("window_start"), demand.get("window_end")
    l_start, l_end = lot.get("available_from"), lot.get("available_until")

    if not d_start and not d_end:
        return MatchFactor("window", 0.9, WEIGHTS["window"], "Buyer accepts delivery at any time")

    now = now_ts()
    if d_end and d_end < now:
        return MatchFactor("window", 0.0, WEIGHTS["window"], "The buyer's delivery window has closed")
    if l_end and d_start and l_end < d_start:
        return MatchFactor("window", 0.0, WEIGHTS["window"],
                           "Your lot is no longer available when the buyer needs it")
    if l_start and d_end and l_start > d_end:
        return MatchFactor("window", 0.0, WEIGHTS["window"],
                           "Your lot becomes available after the buyer's window closes")
    return MatchFactor("window", 1.0, WEIGHTS["window"], "Delivery timing works for both sides")


def _score_reliability(buyer: Dict[str, Any]) -> MatchFactor:
    trust = buyer.get("trust", {})
    score = float(trust.get("score", 50)) / 100.0
    band = trust.get("band", "NEW")

    if band == "NEW":
        return MatchFactor("reliability", 0.5, WEIGHTS["reliability"],
                           "Verified, but no completed transactions here yet")
    if band == "STRONG":
        days = trust.get("avg_payment_days")
        detail = f"Strong record, {trust.get('completed_transactions', 0)} deals completed"
        if days is not None:
            detail += f", pays in about {days:.1f} days"
        return MatchFactor("reliability", score, WEIGHTS["reliability"], detail)
    return MatchFactor("reliability", score, WEIGHTS["reliability"],
                       f"Payment record is rated {band.lower()}")


def find_matches(
    lot: Dict[str, Any],
    *,
    limit: int = 10,
    min_score: int = 30,
    include_unverified: bool = False,
) -> List[Match]:
    """
    Rank open buyer demand against a lot.

    Excludes demand that fails a hard requirement outright rather than
    ranking it low, so nothing appears in a farmer's list that they are not
    actually allowed to sell into.
    """
    demands = list_open_demand(
        commodity=lot["commodity"], verified_only=not include_unverified, limit=200
    )
    if not demands:
        return []

    origin = None
    if lot.get("pickup_district"):
        origin = resolve_origin(lot["pickup_district"])

    perishable = COMMODITY_BY_CANONICAL[lot["commodity"]].perishable \
        if lot["commodity"] in COMMODITY_BY_CANONICAL else False

    matches: List[Match] = []

    for demand in demands:
        buyer = demand.get("buyer") or {}

        # Hard exclusions.
        if buyer.get("verification_status") in (
            VerificationStatus.SUSPENDED, VerificationStatus.REJECTED
        ):
            continue
        if float(demand["quantity_remaining_kg"]) <= 0:
            continue

        distance_km = None
        if origin and origin.latitude is not None and demand.get("delivery_district"):
            destination = resolve_origin(demand["delivery_district"])
            if destination and destination.latitude is not None:
                distance_km = road_distance_km(
                    origin.latitude, origin.longitude,
                    destination.latitude, destination.longitude,
                )

        factors = [
            _score_commodity(lot, demand),
            _score_quantity(lot, demand),
            _score_quality(lot, demand),
            _score_price(lot, demand),
            _score_distance(distance_km, demand),
            _score_window(lot, demand),
            _score_reliability(buyer),
        ]

        # A zero on commodity, quality or window is disqualifying, not merely
        # a low score: those represent a sale that cannot legally happen.
        blocking = [f for f in factors if f.name in ("commodity", "quality", "window")
                    and f.score == 0.0]
        if blocking:
            continue

        total = int(round(sum(f.points for f in factors)))
        if total < min_score:
            continue

        net_price = None
        total_net = None
        if demand.get("price_offered") and distance_km is not None:
            try:
                realisation = compute_net_realisation(
                    market=demand.get("delivery_district") or "buyer location",
                    gross_price_per_quintal=float(demand["price_offered"]),
                    distance_km=distance_km,
                    quantity_kg=float(lot["quantity_kg"]),
                    perishable=perishable,
                )
                net_price = realisation.net_price_per_quintal
                total_net = realisation.total_net
            except ValueError:
                pass

        warnings: List[str] = []
        badge = buyer.get("badge", {})
        if badge.get("caution"):
            warnings.append(badge["caution"])
        if not lot.get("grade_is_evidence") and demand.get("min_grade"):
            warnings.append(
                "Your grade is self-declared. The buyer may re-grade on arrival, "
                "which can change the final price."
            )

        matches.append(Match(
            demand_id=demand["id"],
            buyer_id=demand["buyer_id"],
            buyer_name=buyer.get("name", "Unknown buyer"),
            score=total,
            commodity=demand["commodity"],
            price_offered=demand.get("price_offered"),
            quantity_needed_kg=float(demand["quantity_remaining_kg"]),
            distance_km=distance_km,
            net_price_per_quintal=net_price,
            estimated_total_net=total_net,
            badge=badge,
            factors=factors,
            reasons=[f.detail for f in factors if f.score >= 0.7],
            warnings=warnings,
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def explain_match(match: Match) -> str:
    """Plain-language summary, suitable for reading aloud on a call."""
    lines = [f"{match.buyer_name} scores {match.score} out of 100 for this lot."]
    if match.price_offered:
        lines.append(f"They are offering {match.price_offered:,.0f} rupees per quintal.")
    if match.net_price_per_quintal is not None:
        lines.append(
            f"After transport and market charges you would keep about "
            f"{match.net_price_per_quintal:,.0f} rupees per quintal."
        )
    if match.badge.get("label"):
        lines.append(match.badge["label"] + ".")
    for warning in match.warnings:
        lines.append(warning)
    return " ".join(lines)
