"""
Sale-window recommendation engine.

Decides whether a farmer should sell now, wait, sell part of the lot, or take
the crop to a different market - and explains why in terms the farmer can
check. This is deterministic: the same inputs always yield the same decision,
and every recommendation is stored with the snapshot it was computed from so
it can be audited later.

The engine refuses to recommend rather than guess. If the price history is too
thin to support a claim, the decision is INSUFFICIENT_DATA and the farmer is
told what is missing.

Design rule: waiting is only ever recommended when the expected gain survives
the carry cost, the spoilage risk and the farmer's own cash needs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from app.market.comparison import MarketComparison
from app.market.logistics import storage_cost_per_quintal
from app.market.normalization import COMMODITY_BY_CANONICAL
from app.market.provenance import Confidence, DataQuality
from app.market.trends import Direction, TrendReport, Volatility

logger = logging.getLogger(__name__)

ENGINE_VERSION = "sale-window-v1"

# A market advantage worth the trouble of a different yard, per quintal.
MATERIAL_MARKET_ADVANTAGE = 75.0

# How far below its own benchmark a price must sit before waiting is worth it.
UNDERVALUED_PCT = 5.0
OVERVALUED_PCT = 6.0

# Default holding horizon considered when waiting looks attractive.
DEFAULT_WAIT_DAYS = 10
MAX_WAIT_DAYS = 21


class Decision(str, Enum):
    SELL_NOW = "SELL_NOW"
    WAIT = "WAIT"
    SELL_PARTIAL = "SELL_PARTIAL"
    COMPARE_MARKETS = "COMPARE_MARKETS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass
class FarmerConstraints:
    """
    What the farmer can actually do, which governs what may be advised.

    Two farmers with identical crops get different advice when one needs cash
    this week and the other can hold for a month. Ignoring this is how
    market advice becomes useless.
    """

    storage_days_available: int = 0
    needs_cash_urgently: bool = False
    has_cold_storage: bool = False
    quantity_kg: float = 1_000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "storage_days_available": self.storage_days_available,
            "needs_cash_urgently": self.needs_cash_urgently,
            "has_cold_storage": self.has_cold_storage,
            "quantity_kg": self.quantity_kg,
        }


@dataclass
class Signal:
    """One contributing factor, with the number behind it."""

    name: str
    direction: str          # "favours_waiting" | "favours_selling" | "neutral"
    weight: float
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.name,
            "direction": self.direction,
            "weight": round(self.weight, 2),
            "detail": self.detail,
        }


@dataclass
class SaleWindowRecommendation:
    commodity: str
    market: Optional[str]
    decision: Decision
    confidence: Confidence
    data_quality: DataQuality
    current_price: Optional[float] = None
    recommended_days: int = 0
    expected_price_low: Optional[float] = None
    expected_price_high: Optional[float] = None
    expected_gain_per_quintal: Optional[float] = None
    storage_cost_per_quintal: Optional[float] = None
    net_expected_gain_per_quintal: Optional[float] = None
    alternative_market: Optional[Dict[str, Any]] = None
    signals: List[Signal] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "market": self.market,
            "decision": self.decision.value,
            "confidence": self.confidence.value,
            "data_quality": self.data_quality.value,
            "current_price": self.current_price,
            "recommended_days": self.recommended_days,
            "expected_price_range": (
                {"low": self.expected_price_low, "high": self.expected_price_high}
                if self.expected_price_low is not None else None
            ),
            "expected_gain_per_quintal": self.expected_gain_per_quintal,
            "storage_cost_per_quintal": self.storage_cost_per_quintal,
            "net_expected_gain_per_quintal": self.net_expected_gain_per_quintal,
            "alternative_market": self.alternative_market,
            "signals": [s.to_dict() for s in self.signals],
            "reasoning": self.reasoning,
            "risks": self.risks,
            "engine_version": self.engine_version,
            "generated_at": self.generated_at,
        }


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _insufficient(commodity: str, market: Optional[str], reason: str) -> SaleWindowRecommendation:
    rec = SaleWindowRecommendation(
        commodity=commodity,
        market=market,
        decision=Decision.INSUFFICIENT_DATA,
        confidence=Confidence.INSUFFICIENT_DATA,
        data_quality=DataQuality.UNAVAILABLE,
    )
    rec.reasoning.append(reason)
    rec.risks.append(
        "No recommendation is being made. Acting on an unsupported guess would be worse "
        "than having no advice."
    )
    return rec


def recommend_sale_window(
    trend: TrendReport,
    constraints: Optional[FarmerConstraints] = None,
    comparison: Optional[MarketComparison] = None,
    allow_mock: bool = False,
) -> SaleWindowRecommendation:
    """
    Produce an explainable sell/wait recommendation.

    Gathers weighted signals, then applies hard constraints that can override
    the signal balance - a farmer who needs cash this week is told to sell
    regardless of how bullish the series looks.

    `allow_mock` must be set explicitly by a demo caller to let MOCK-tagged
    data reach the engine. It defaults to False so a production voice call can
    never receive a recommendation built on fixture data by accident; when it
    is set, the recommendation is stamped so the output stays self-labelling.
    """
    constraints = constraints or FarmerConstraints()
    commodity = trend.commodity
    market = trend.market

    if trend.current_price is None:
        return _insufficient(commodity, market, "No current price is available for this commodity.")

    if trend.data_quality is DataQuality.UNAVAILABLE:
        return _insufficient(
            commodity, market,
            "No usable price data is available, so no sell-or-wait decision can be made.",
        )

    if trend.data_quality is DataQuality.MOCK and not allow_mock:
        return _insufficient(
            commodity, market,
            "The only available price data is demo fixture data (MOCK), which cannot "
            "support a real sell-or-wait decision.",
        )

    rec = SaleWindowRecommendation(
        commodity=commodity,
        market=market,
        decision=Decision.SELL_NOW,
        confidence=trend.confidence,
        data_quality=trend.data_quality,
        current_price=_round(trend.current_price),
    )

    price = trend.current_price
    spec = COMMODITY_BY_CANONICAL.get(commodity)
    perishable = spec.perishable if spec else False
    max_hold = spec.typical_storage_days if spec else 30

    signals: List[Signal] = []

    # --- Signal 1: price position against its own benchmark ---------------
    benchmark_window = trend.window(90) or trend.window(30)
    benchmark = benchmark_window.average if benchmark_window and benchmark_window.sufficient else None
    position_pct: Optional[float] = None

    if benchmark:
        position_pct = ((price - benchmark) / benchmark) * 100.0
        if position_pct <= -UNDERVALUED_PCT:
            signals.append(Signal(
                "price_position", "favours_waiting", 2.0,
                f"current price {price:.0f} is {abs(position_pct):.1f}% below the "
                f"{benchmark_window.days}-day average of {benchmark:.0f}",
            ))
        elif position_pct >= OVERVALUED_PCT:
            signals.append(Signal(
                "price_position", "favours_selling", 2.0,
                f"current price {price:.0f} is {position_pct:.1f}% above the "
                f"{benchmark_window.days}-day average of {benchmark:.0f}",
            ))
        else:
            signals.append(Signal(
                "price_position", "neutral", 0.5,
                f"current price is close to the {benchmark_window.days}-day average",
            ))
    else:
        rec.risks.append("No reliable long-run benchmark; the price position could not be assessed.")

    # --- Signal 2: short-term momentum ------------------------------------
    short = trend.window(7)
    if short and short.sufficient and short.change_pct is not None:
        if short.direction is Direction.RISING:
            signals.append(Signal(
                "momentum", "favours_waiting", 1.5,
                f"price rose {short.change_pct:.1f}% over the last 7 days",
            ))
        elif short.direction is Direction.FALLING:
            signals.append(Signal(
                "momentum", "favours_selling", 1.5,
                f"price fell {abs(short.change_pct):.1f}% over the last 7 days",
            ))
        else:
            signals.append(Signal("momentum", "neutral", 0.3, "price has been broadly flat for 7 days"))

    # --- Signal 3: arrival pressure ---------------------------------------
    arrivals = trend.arrivals.get(30) or trend.arrivals.get(7)
    if arrivals and arrivals.change_pct is not None and arrivals.sample_count >= 3:
        if arrivals.direction is Direction.FALLING:
            signals.append(Signal(
                "arrival_pressure", "favours_waiting", 1.5,
                f"arrivals are down {abs(arrivals.change_pct):.1f}%, which historically "
                "accompanies firmer prices",
            ))
        elif arrivals.direction is Direction.RISING:
            signals.append(Signal(
                "arrival_pressure", "favours_selling", 1.5,
                f"arrivals are up {arrivals.change_pct:.1f}%, which adds supply pressure",
            ))

    # --- Signal 4: volatility ---------------------------------------------
    vol_window = trend.window(30) or trend.window(7)
    if vol_window and vol_window.volatility is Volatility.HIGH:
        signals.append(Signal(
            "volatility", "favours_selling", 1.0,
            "prices have been highly volatile, so waiting carries real downside",
        ))
        rec.risks.append("This commodity has been volatile recently; the expected range is wide.")
    elif vol_window and vol_window.volatility is Volatility.LOW:
        signals.append(Signal("volatility", "neutral", 0.3, "prices have been stable"))

    # --- Signal 5: storage feasibility ------------------------------------
    effective_storage = min(constraints.storage_days_available, max_hold)
    if constraints.storage_days_available <= 0:
        signals.append(Signal(
            "storage", "favours_selling", 2.5,
            "no storage is available, so the crop cannot be held",
        ))
    elif perishable and constraints.storage_days_available > max_hold:
        signals.append(Signal(
            "storage", "favours_selling", 1.5,
            f"{commodity} typically keeps about {max_hold} days without cold storage",
        ))
        rec.risks.append(
            f"Holding {commodity} beyond roughly {max_hold} days risks spoilage losses."
        )
    elif effective_storage >= 7:
        signals.append(Signal(
            "storage", "favours_waiting", 1.0,
            f"storage is available for about {effective_storage} days",
        ))

    # --- Signal 6: cash urgency (a hard constraint, weighted to dominate) --
    if constraints.needs_cash_urgently:
        signals.append(Signal(
            "cash_need", "favours_selling", 3.0,
            "immediate cash requirement outweighs a possible future price gain",
        ))

    rec.signals = signals

    # --- Weigh the signals ------------------------------------------------
    wait_score = sum(s.weight for s in signals if s.direction == "favours_waiting")
    sell_score = sum(s.weight for s in signals if s.direction == "favours_selling")

    # --- Alternative market ----------------------------------------------
    advantage = None
    if comparison and comparison.has_data and comparison.best:
        advantage = comparison.advantage_per_quintal
        if advantage and advantage >= MATERIAL_MARKET_ADVANTAGE:
            rec.alternative_market = {
                "market": comparison.best.market,
                "distance_km": round(comparison.best.distance_km, 1),
                "net_price_per_quintal": round(comparison.best.net_price_per_quintal, 2),
                "advantage_per_quintal": round(advantage, 2),
                "advantage_total": (
                    round(comparison.advantage_total, 2)
                    if comparison.advantage_total is not None else None
                ),
            }

    # --- Expected range if waiting ---------------------------------------
    hold_days = min(max(DEFAULT_WAIT_DAYS, 0), MAX_WAIT_DAYS)
    if effective_storage > 0:
        hold_days = min(hold_days, effective_storage)

    if benchmark and vol_window and vol_window.volatility_cv is not None:
        # Expected level reverts part-way toward the benchmark; the band is
        # the series' own observed dispersion. No forecasting model is claimed.
        reversion = (benchmark - price) * 0.5
        centre = price + reversion
        spread = centre * min(vol_window.volatility_cv, 0.25)
        rec.expected_price_low = _round(max(centre - spread, 0))
        rec.expected_price_high = _round(centre + spread)
        rec.expected_gain_per_quintal = _round(centre - price)
        rec.storage_cost_per_quintal = _round(
            storage_cost_per_quintal(hold_days, constraints.has_cold_storage)
        )
        rec.net_expected_gain_per_quintal = _round(
            (rec.expected_gain_per_quintal or 0.0) - (rec.storage_cost_per_quintal or 0.0)
        )

    # --- Decide -----------------------------------------------------------
    decision = _decide(
        wait_score=wait_score,
        sell_score=sell_score,
        constraints=constraints,
        net_gain=rec.net_expected_gain_per_quintal,
        alternative=rec.alternative_market,
    )
    rec.decision = decision
    rec.recommended_days = hold_days if decision in (Decision.WAIT, Decision.SELL_PARTIAL) else 0

    # --- Explain ----------------------------------------------------------
    rec.reasoning = [s.detail for s in signals if s.direction != "neutral"]

    if decision is Decision.WAIT:
        if rec.net_expected_gain_per_quintal is not None:
            rec.reasoning.append(
                f"after an estimated {rec.storage_cost_per_quintal:.0f}/quintal holding cost, "
                f"waiting is worth about {rec.net_expected_gain_per_quintal:.0f}/quintal"
            )
        rec.risks.append(
            "If arrivals rise sharply, the expected price improvement may not materialise."
        )
    elif decision is Decision.COMPARE_MARKETS and rec.alternative_market:
        alt = rec.alternative_market
        rec.reasoning.append(
            f"{alt['market']} nets about {alt['advantage_per_quintal']:.0f}/quintal more "
            f"after transport, {alt['distance_km']:.0f} km away"
        )
    elif decision is Decision.SELL_PARTIAL:
        rec.reasoning.append(
            "signals are mixed, so selling part of the lot now captures the current price "
            "while keeping some exposure to a recovery"
        )

    if trend.data_quality is DataQuality.STALE:
        rec.confidence = Confidence.LOW
        rec.risks.append(
            f"The latest price is {trend.provenance.describe_age() if trend.provenance else 'old'}; "
            "confirm at the mandi before acting."
        )

    if not rec.reasoning:
        rec.reasoning.append("No strong signal in either direction from the available data.")

    if trend.data_quality is DataQuality.MOCK:
        # Stamped on the recommendation itself, so the label survives even if
        # only this object is passed onward.
        rec.confidence = Confidence.LOW
        rec.risks.insert(
            0,
            "DEMONSTRATION ONLY: this recommendation was computed from generated fixture "
            "data, not live mandi prices. It must not be acted on.",
        )

    return rec


def _decide(
    *,
    wait_score: float,
    sell_score: float,
    constraints: FarmerConstraints,
    net_gain: Optional[float],
    alternative: Optional[Dict[str, Any]],
) -> Decision:
    """
    Turn weighted signals into one decision.

    Hard constraints are applied before the score comparison: no amount of
    bullish signal justifies telling a farmer with no storage to wait.
    """
    # Hard constraints first.
    if constraints.needs_cash_urgently or constraints.storage_days_available <= 0:
        return Decision.COMPARE_MARKETS if alternative else Decision.SELL_NOW

    # Waiting must pay for itself after carry cost.
    if net_gain is not None and net_gain <= 0 and wait_score > sell_score:
        return Decision.COMPARE_MARKETS if alternative else Decision.SELL_NOW

    margin = wait_score - sell_score
    if margin >= 2.0:
        return Decision.WAIT
    if margin <= -2.0:
        return Decision.COMPARE_MARKETS if alternative else Decision.SELL_NOW
    if alternative:
        return Decision.COMPARE_MARKETS
    return Decision.SELL_PARTIAL
