"""
Data provenance and quality classification.

Every value that leaves the market intelligence engine carries a Provenance
record saying where it came from and how much it can be trusted. This is the
mechanism that makes the "no fake data disguised as real data" rule enforceable
rather than aspirational: a caller cannot obtain a price without also obtaining
its data_quality, and the voice layer refuses to narrate anything below FRESH
as "today's price".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class DataQuality(str, Enum):
    """
    How much a record can be trusted, ordered from best to worst.

    LIVE        Observed within the last day. Safe to call "today's price".
    FRESH       Recent enough to act on, but not today's print.
    STALE       Real observed data, but too old to present as current.
    DEGRADED    Real data with integrity problems (failed validation rules).
    UNAVAILABLE No data could be obtained. Never carries a price.
    MOCK        Demo fixture. Must never reach a production voice call.
    """

    LIVE = "LIVE"
    FRESH = "FRESH"
    STALE = "STALE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    MOCK = "MOCK"

    @property
    def rank(self) -> int:
        return _QUALITY_RANK[self]

    def is_at_least(self, floor: "DataQuality") -> bool:
        return self.rank >= floor.rank

    @property
    def presentable_as_current(self) -> bool:
        """True only for data that may be spoken as 'today's market price'."""
        return self in (DataQuality.LIVE, DataQuality.FRESH)

    @property
    def carries_price(self) -> bool:
        """False for qualities that must never be attached to a number."""
        return self not in (DataQuality.UNAVAILABLE,)


_QUALITY_RANK: Dict[DataQuality, int] = {
    DataQuality.UNAVAILABLE: 0,
    DataQuality.MOCK: 1,
    DataQuality.DEGRADED: 2,
    DataQuality.STALE: 3,
    DataQuality.FRESH: 4,
    DataQuality.LIVE: 5,
}


class Confidence(str, Enum):
    """Confidence in a derived result (a trend, a forecast, a recommendation)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# Freshness thresholds in hours. A mandi publishes at most once per day, so
# "today or yesterday" is LIVE and anything inside a working week is FRESH.
LIVE_MAX_HOURS = 36.0
FRESH_MAX_HOURS = 24.0 * 7
STALE_MAX_HOURS = 24.0 * 45


def _utc_now() -> float:
    return time.time()


def parse_data_date(value: Any) -> Optional[datetime]:
    """
    Parse the many date shapes Indian agri feeds emit into an aware datetime.

    Accepts dd/mm/YYYY (data.gov.in and most scraped mandi tables), ISO dates,
    and epoch seconds. Returns None rather than guessing when the shape is
    unrecognised - an unparseable date must degrade the record, not silently
    become "now".
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hours_since(data_date: Optional[datetime], now: Optional[float] = None) -> Optional[float]:
    """Age of an observation in hours. None when the date is unknown."""
    if data_date is None:
        return None
    now = now if now is not None else _utc_now()
    return (now - data_date.timestamp()) / 3600.0


def classify_freshness(
    data_date: Optional[datetime],
    now: Optional[float] = None,
) -> DataQuality:
    """
    Map an observation date onto a quality band.

    A future-dated record is DEGRADED, not LIVE: agri feeds do occasionally
    emit bad dates, and treating them as fresh would be the exact failure this
    module exists to prevent.
    """
    age = hours_since(data_date, now=now)
    if age is None:
        return DataQuality.UNAVAILABLE
    if age < -12.0:
        return DataQuality.DEGRADED
    if age <= LIVE_MAX_HOURS:
        return DataQuality.LIVE
    if age <= FRESH_MAX_HOURS:
        return DataQuality.FRESH
    if age <= STALE_MAX_HOURS:
        return DataQuality.STALE
    return DataQuality.STALE


@dataclass
class Provenance:
    """Where a value came from and how far it can be trusted."""

    source: str
    source_type: str = "api"
    data_quality: DataQuality = DataQuality.UNAVAILABLE
    data_timestamp: Optional[str] = None
    source_timestamp: Optional[str] = None
    freshness_hours: Optional[float] = None
    confidence: Confidence = Confidence.MEDIUM
    record_count: int = 0
    notes: list = field(default_factory=list)

    @classmethod
    def from_observation(
        cls,
        source: str,
        data_date: Optional[datetime],
        *,
        source_type: str = "api",
        fetched_at: Optional[float] = None,
        record_count: int = 1,
        confidence: Confidence = Confidence.MEDIUM,
        quality_override: Optional[DataQuality] = None,
    ) -> "Provenance":
        fetched_at = fetched_at if fetched_at is not None else _utc_now()
        quality = quality_override or classify_freshness(data_date)
        age = hours_since(data_date)
        return cls(
            source=source,
            source_type=source_type,
            data_quality=quality,
            data_timestamp=data_date.date().isoformat() if data_date else None,
            source_timestamp=datetime.fromtimestamp(fetched_at, tz=timezone.utc).isoformat(),
            freshness_hours=round(age, 2) if age is not None else None,
            confidence=confidence,
            record_count=record_count,
        )

    @classmethod
    def unavailable(cls, source: str, reason: str) -> "Provenance":
        """A failure carries a Provenance too, so callers cannot mistake it for data."""
        return cls(
            source=source,
            source_type="api",
            data_quality=DataQuality.UNAVAILABLE,
            confidence=Confidence.INSUFFICIENT_DATA,
            record_count=0,
            notes=[reason],
        )

    @classmethod
    def mock(cls, source: str, reason: str = "demo fixture") -> "Provenance":
        return cls(
            source=source,
            source_type="fixture",
            data_quality=DataQuality.MOCK,
            confidence=Confidence.LOW,
            notes=[reason],
        )

    def degrade(self, reason: str) -> "Provenance":
        """Mark a record as failing validation while preserving its origin."""
        self.data_quality = DataQuality.DEGRADED
        self.notes.append(reason)
        return self

    @property
    def presentable_as_current(self) -> bool:
        return self.data_quality.presentable_as_current

    def describe_age(self) -> str:
        """Human phrasing of the age, for both logs and the voice layer."""
        if self.data_timestamp is None:
            return "date unknown"
        age = self.freshness_hours
        if age is None:
            return f"dated {self.data_timestamp}"
        if age <= 36:
            return "today"
        days = int(age // 24)
        if days <= 1:
            return "yesterday"
        if days < 30:
            return f"{days} days old"
        months = days // 30
        return f"about {months} month{'s' if months > 1 else ''} old"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["data_quality"] = self.data_quality.value
        payload["confidence"] = self.confidence.value
        payload["age_description"] = self.describe_age()
        return payload


def worst(*provenances: Provenance) -> DataQuality:
    """
    Quality of a derived result is the quality of its weakest input.

    A trend computed from one LIVE and forty STALE observations is STALE.
    """
    qualities = [p.data_quality for p in provenances if p is not None]
    if not qualities:
        return DataQuality.UNAVAILABLE
    return min(qualities, key=lambda q: q.rank)
