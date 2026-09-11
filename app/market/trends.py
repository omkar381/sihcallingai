"""
Deterministic price and arrival analytics.

Everything here is arithmetic over stored observations. No language model
touches these numbers - the LLM's only job downstream is to read the result
aloud in Kannada. That separation is what makes a quoted figure defensible.

Each statistic carries the sample size it was computed from, and confidence
degrades automatically when the window is thin. A "30-day average" over four
observations is reported as such rather than presented as settled fact.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from app.market.provenance import Confidence, DataQuality, Provenance, parse_data_date
from app.market.repository import PriceRecord

logger = logging.getLogger(__name__)

# Below this many observations a window cannot support a claim.
MIN_SAMPLES_FOR_TREND = 3
MIN_SAMPLES_FOR_CONFIDENT_TREND = 8

# Coefficient-of-variation cutoffs for volatility banding, chosen so that
# staples land LOW and onion/tomato land MEDIUM-HIGH in normal conditions.
VOLATILITY_LOW = 0.08
VOLATILITY_MEDIUM = 0.18

# A move smaller than this is noise, not a trend.
FLAT_THRESHOLD_PCT = 2.0


class Direction(str, Enum):
    RISING = "RISING"
    FALLING = "FALLING"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


class Volatility(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass
class WindowStats:
    """Descriptive statistics for one trailing window."""

    days: int
    sample_count: int
    average: Optional[float] = None
    median: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    first: Optional[float] = None
    last: Optional[float] = None
    change_pct: Optional[float] = None
    volatility_cv: Optional[float] = None
    volatility: Volatility = Volatility.UNKNOWN
    direction: Direction = Direction.UNKNOWN

    @property
    def sufficient(self) -> bool:
        return self.sample_count >= MIN_SAMPLES_FOR_TREND

    def to_dict(self) -> Dict[str, Any]:
        return {
            "days": self.days,
            "sample_count": self.sample_count,
            "average": _round(self.average),
            "median": _round(self.median),
            "min": _round(self.minimum),
            "max": _round(self.maximum),
            "first": _round(self.first),
            "last": _round(self.last),
            "change_pct": _round(self.change_pct),
            "volatility": self.volatility.value,
            "volatility_cv": _round(self.volatility_cv, 4),
            "direction": self.direction.value,
            "sufficient_data": self.sufficient,
        }


@dataclass
class ArrivalStats:
    days: int
    sample_count: int
    average_qty: Optional[float] = None
    recent_qty: Optional[float] = None
    change_pct: Optional[float] = None
    direction: Direction = Direction.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "days": self.days,
            "sample_count": self.sample_count,
            "average_qty": _round(self.average_qty),
            "recent_qty": _round(self.recent_qty),
            "change_pct": _round(self.change_pct),
            "direction": self.direction.value,
        }


@dataclass
class TrendReport:
    commodity: str
    market: Optional[str]
    current_price: Optional[float]
    current_date: Optional[str]
    price_unit: str = "INR/quintal"
    windows: Dict[int, WindowStats] = field(default_factory=dict)
    arrivals: Dict[int, ArrivalStats] = field(default_factory=dict)
    confidence: Confidence = Confidence.INSUFFICIENT_DATA
    data_quality: DataQuality = DataQuality.UNAVAILABLE
    provenance: Optional[Provenance] = None
    notes: List[str] = field(default_factory=list)

    def window(self, days: int) -> Optional[WindowStats]:
        return self.windows.get(days)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "commodity": self.commodity,
            "market": self.market,
            "current_price": _round(self.current_price),
            "current_date": self.current_date,
            "price_unit": self.price_unit,
            "windows": {str(k): v.to_dict() for k, v in sorted(self.windows.items())},
            "arrivals": {str(k): v.to_dict() for k, v in sorted(self.arrivals.items())},
            "confidence": self.confidence.value,
            "data_quality": self.data_quality.value,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "notes": self.notes,
        }


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), places)


def _pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or old == 0:
        return None
    return ((new - old) / old) * 100.0


def _classify_volatility(cv: Optional[float]) -> Volatility:
    if cv is None:
        return Volatility.UNKNOWN
    if cv < VOLATILITY_LOW:
        return Volatility.LOW
    if cv < VOLATILITY_MEDIUM:
        return Volatility.MEDIUM
    return Volatility.HIGH


def _classify_direction(change_pct: Optional[float]) -> Direction:
    if change_pct is None:
        return Direction.UNKNOWN
    if change_pct > FLAT_THRESHOLD_PCT:
        return Direction.RISING
    if change_pct < -FLAT_THRESHOLD_PCT:
        return Direction.FALLING
    return Direction.FLAT


def _filter_window(
    records: Sequence[PriceRecord],
    days: int,
    end: datetime,
) -> List[PriceRecord]:
    start = end - timedelta(days=days)
    out = []
    for rec in records:
        dt = parse_data_date(rec.arrival_date)
        if dt is not None and start <= dt <= end:
            out.append(rec)
    return out


def compute_window_stats(
    records: Sequence[PriceRecord],
    days: int,
    end: Optional[datetime] = None,
) -> WindowStats:
    """Descriptive statistics over the trailing `days` window."""
    end = end or datetime.now(timezone.utc)
    window = _filter_window(records, days, end)
    prices = [r.modal_price for r in window if r.modal_price and r.modal_price > 0]

    stats = WindowStats(days=days, sample_count=len(prices))
    if not prices:
        return stats

    stats.average = statistics.fmean(prices)
    stats.median = statistics.median(prices)
    stats.minimum = min(prices)
    stats.maximum = max(prices)
    stats.first = prices[0]
    stats.last = prices[-1]

    if len(prices) >= 2:
        stats.change_pct = _pct_change(prices[0], prices[-1])
        stats.direction = _classify_direction(stats.change_pct)

    if len(prices) >= MIN_SAMPLES_FOR_TREND and stats.average:
        # Coefficient of variation: comparable across commodities with very
        # different price levels, unlike raw standard deviation.
        stats.volatility_cv = statistics.pstdev(prices) / stats.average
        stats.volatility = _classify_volatility(stats.volatility_cv)

    return stats


def compute_arrival_stats(
    records: Sequence[PriceRecord],
    days: int,
    end: Optional[datetime] = None,
    recent_days: int = 7,
) -> ArrivalStats:
    """
    Arrival-volume behaviour over a window.

    Compares the most recent `recent_days` against the window average, because
    the signal that matters is "are arrivals tightening right now", not the
    long-run level.
    """
    end = end or datetime.now(timezone.utc)
    window = _filter_window(records, days, end)
    quantities = [r.arrival_qty for r in window if r.arrival_qty and r.arrival_qty > 0]

    stats = ArrivalStats(days=days, sample_count=len(quantities))
    if not quantities:
        return stats

    stats.average_qty = statistics.fmean(quantities)

    recent_window = _filter_window(records, recent_days, end)
    recent = [r.arrival_qty for r in recent_window if r.arrival_qty and r.arrival_qty > 0]
    if recent:
        stats.recent_qty = statistics.fmean(recent)
        stats.change_pct = _pct_change(stats.average_qty, stats.recent_qty)
        stats.direction = _classify_direction(stats.change_pct)

    return stats


def _overall_confidence(windows: Dict[int, WindowStats]) -> Confidence:
    """Confidence is driven by the deepest window that has enough samples."""
    best = max((w.sample_count for w in windows.values()), default=0)
    if best >= MIN_SAMPLES_FOR_CONFIDENT_TREND:
        return Confidence.HIGH
    if best >= MIN_SAMPLES_FOR_TREND:
        return Confidence.MEDIUM
    if best > 0:
        return Confidence.LOW
    return Confidence.INSUFFICIENT_DATA


def build_trend_report(
    commodity: str,
    records: Sequence[PriceRecord],
    market: Optional[str] = None,
    windows: Sequence[int] = (7, 30, 90),
    end: Optional[datetime] = None,
) -> TrendReport:
    """
    Assemble the full trend picture from a price series.

    Pure function over records: the same series always produces the same
    report, which is what makes a recommendation reproducible months later.
    """
    end = end or datetime.now(timezone.utc)
    ordered = sorted(
        [r for r in records if r.modal_price and r.modal_price > 0],
        key=lambda r: r.arrival_date,
    )

    report = TrendReport(commodity=commodity, market=market, current_price=None, current_date=None)

    if not ordered:
        report.notes.append("No price observations available for this commodity and market.")
        return report

    latest = ordered[-1]
    report.current_price = latest.modal_price
    report.current_date = latest.arrival_date
    report.price_unit = latest.price_unit
    report.provenance = latest.provenance()
    report.data_quality = report.provenance.data_quality

    for days in windows:
        report.windows[days] = compute_window_stats(ordered, days, end=end)
        report.arrivals[days] = compute_arrival_stats(ordered, days, end=end)

    report.confidence = _overall_confidence(report.windows)

    thin = [d for d, w in report.windows.items() if 0 < w.sample_count < MIN_SAMPLES_FOR_TREND]
    if thin:
        report.notes.append(
            "Windows with too few observations for a reliable trend: "
            + ", ".join(f"{d}-day" for d in sorted(thin))
        )
    if not any(r.arrival_qty for r in ordered):
        report.notes.append("Arrival volumes were not reported by the source for this series.")

    return report


def compare_to_benchmark(
    current_price: float,
    benchmark: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Position today's price against a longer-run benchmark average."""
    if benchmark is None or benchmark <= 0:
        return None
    diff = current_price - benchmark
    return {
        "benchmark": _round(benchmark),
        "difference": _round(diff),
        "difference_pct": _round((diff / benchmark) * 100.0),
        "position": "above" if diff > 0 else ("below" if diff < 0 else "at"),
    }
