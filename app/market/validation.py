"""
Record validation and anomaly detection for ingested market data.

Government and scraped agri feeds routinely emit blank prices, transposed
min/max values, prices in the wrong unit, and future-dated rows. Every record
passes through here before it can reach the price history.

A record is either ACCEPTED, ACCEPTED_DEGRADED (usable but flagged) or
REJECTED with a machine-readable reason. Nothing is silently repaired: a
guessed correction is indistinguishable from fabricated data once stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.market.normalization import (
    KG_PER_QUINTAL,
    COMMODITY_BY_CANONICAL,
    normalize_grade,
    normalize_market_name,
    normalize_state_name,
    resolve_commodity,
)
from app.market.provenance import DataQuality, classify_freshness, parse_data_date

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    ACCEPTED_DEGRADED = "ACCEPTED_DEGRADED"
    REJECTED = "REJECTED"


class RejectionReason(str, Enum):
    MISSING_COMMODITY = "MISSING_COMMODITY"
    UNKNOWN_COMMODITY = "UNKNOWN_COMMODITY"
    MISSING_MARKET = "MISSING_MARKET"
    MISSING_PRICE = "MISSING_PRICE"
    NON_NUMERIC_PRICE = "NON_NUMERIC_PRICE"
    NEGATIVE_PRICE = "NEGATIVE_PRICE"
    PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"
    MISSING_DATE = "MISSING_DATE"
    UNPARSEABLE_DATE = "UNPARSEABLE_DATE"
    FUTURE_DATE = "FUTURE_DATE"
    INVERTED_MIN_MAX = "INVERTED_MIN_MAX"
    MODAL_OUTSIDE_RANGE = "MODAL_OUTSIDE_RANGE"
    NEGATIVE_ARRIVAL = "NEGATIVE_ARRIVAL"


# Plausible modal price bands in INR per quintal, by commodity group.
# Deliberately wide: the purpose is to catch unit errors and decimal slips
# (an onion price of 12 or 1,250,000), not to second-guess the market.
PRICE_BANDS_PER_QUINTAL: Dict[str, Tuple[float, float]] = {
    "vegetable": (100.0, 30_000.0),
    "fruit": (200.0, 60_000.0),
    "cereal": (800.0, 20_000.0),
    "pulse": (2_000.0, 40_000.0),
    "oilseed": (1_500.0, 40_000.0),
    "spice": (1_000.0, 150_000.0),
    "fibre": (2_000.0, 40_000.0),
    "cash": (100.0, 10_000.0),
}
DEFAULT_PRICE_BAND = (50.0, 200_000.0)

# A single day's move larger than this against the recent local average is
# flagged, not rejected: onion genuinely does move violently.
ANOMALY_JUMP_RATIO = 3.0


@dataclass
class ValidationResult:
    verdict: Verdict
    reasons: List[RejectionReason]
    warnings: List[str]
    normalized: Optional[Dict[str, Any]] = None

    @property
    def accepted(self) -> bool:
        return self.verdict in (Verdict.ACCEPTED, Verdict.ACCEPTED_DEGRADED)

    @property
    def primary_reason(self) -> Optional[str]:
        return self.reasons[0].value if self.reasons else None


def _to_float(value: Any) -> Optional[float]:
    """Parse a feed price. Blank sentinels are absent values, not zeros."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "NA", "N/A", "null", "NULL", "NR"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def validate_raw_record(
    raw: Dict[str, Any],
    *,
    source: str,
    now: Optional[datetime] = None,
) -> ValidationResult:
    """
    Validate and normalise one raw feed row into canonical fields.

    Expects the loosely-named keys real feeds use; the adapter maps its own
    column names onto these before calling.
    """
    now = now or datetime.now(timezone.utc)
    reasons: List[RejectionReason] = []
    warnings: List[str] = []

    # --- commodity ---
    raw_commodity = raw.get("commodity")
    if not raw_commodity:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.MISSING_COMMODITY], warnings)

    match = resolve_commodity(raw_commodity)
    if match is None:
        # Unknown crops are rejected rather than stored under their raw name:
        # an unmapped alias would fragment the series for that commodity.
        return ValidationResult(Verdict.REJECTED, [RejectionReason.UNKNOWN_COMMODITY], warnings)
    commodity = match.canonical

    # --- market ---
    market = normalize_market_name(raw.get("market"))
    if not market:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.MISSING_MARKET], warnings)

    district = normalize_market_name(raw.get("district")) or market
    state = normalize_state_name(raw.get("state")) or "Unknown"

    # --- date ---
    raw_date = raw.get("arrival_date")
    if not raw_date:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.MISSING_DATE], warnings)

    arrival_dt = parse_data_date(raw_date)
    if arrival_dt is None:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.UNPARSEABLE_DATE], warnings)

    # Half a day of tolerance absorbs timezone skew in the feed.
    if (arrival_dt - now).total_seconds() > 12 * 3600:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.FUTURE_DATE], warnings)

    # --- prices ---
    modal = _to_float(raw.get("modal_price"))
    min_price = _to_float(raw.get("min_price"))
    max_price = _to_float(raw.get("max_price"))

    if modal is None:
        # Some feeds omit modal but carry the range; the midpoint is a
        # legitimate derivation, and we record that we derived it.
        if min_price is not None and max_price is not None:
            modal = (min_price + max_price) / 2.0
            warnings.append("modal_price derived from min/max midpoint")
        else:
            return ValidationResult(Verdict.REJECTED, [RejectionReason.MISSING_PRICE], warnings)

    if modal <= 0:
        return ValidationResult(Verdict.REJECTED, [RejectionReason.NEGATIVE_PRICE], warnings)

    if min_price is not None and max_price is not None and min_price > max_price:
        # Transposed columns are common enough to be worth flagging loudly.
        min_price, max_price = max_price, min_price
        warnings.append("min/max were inverted in the source and have been swapped")

    degraded = False
    if min_price is not None and max_price is not None:
        if not (min_price <= modal <= max_price):
            reasons.append(RejectionReason.MODAL_OUTSIDE_RANGE)
            warnings.append(f"modal {modal} outside [{min_price}, {max_price}]")
            degraded = True

    group = COMMODITY_BY_CANONICAL[commodity].group
    low, high = PRICE_BANDS_PER_QUINTAL.get(group, DEFAULT_PRICE_BAND)
    if not (low <= modal <= high):
        # Out-of-band almost always means a unit error (per-kg quoted as
        # per-quintal). Reject: storing it would poison every average.
        return ValidationResult(
            Verdict.REJECTED,
            [RejectionReason.PRICE_OUT_OF_RANGE],
            warnings + [f"{modal} outside plausible {group} band [{low}, {high}] INR/quintal"],
        )

    # --- arrivals ---
    arrival_qty = _to_float(raw.get("arrival_qty"))
    if arrival_qty is not None and arrival_qty < 0:
        arrival_qty = None
        warnings.append("negative arrival quantity discarded")

    normalized = {
        "state": state,
        "district": district,
        "market": market,
        "commodity": commodity,
        "variety": (str(raw["variety"]).strip() if raw.get("variety") else None),
        "grade": normalize_grade(raw.get("grade")),
        "min_price": min_price,
        "max_price": max_price,
        "modal_price": round(modal, 2),
        "price_unit": "INR/quintal",
        "arrival_qty": arrival_qty,
        "arrival_unit": raw.get("arrival_unit") or ("tonne" if arrival_qty is not None else None),
        "arrival_date": arrival_dt.date().isoformat(),
        "source": source,
        "data_quality": (
            DataQuality.DEGRADED if degraded else classify_freshness(arrival_dt, now=now.timestamp())
        ),
    }

    verdict = Verdict.ACCEPTED_DEGRADED if degraded else Verdict.ACCEPTED
    return ValidationResult(verdict, reasons, warnings, normalized)


def detect_price_anomaly(
    modal_price: float,
    recent_prices: List[float],
) -> Optional[str]:
    """
    Flag a price that moved implausibly against its own recent history.

    Returns a description, or None. Callers flag rather than drop: a genuine
    supply shock looks exactly like a data error, and discarding real shocks
    would hide precisely the events a farmer most needs to hear about.
    """
    usable = [p for p in recent_prices if p and p > 0]
    if len(usable) < 3:
        return None

    average = sum(usable) / len(usable)
    if average <= 0:
        return None

    ratio = modal_price / average
    if ratio >= ANOMALY_JUMP_RATIO:
        return f"price {modal_price:.0f} is {ratio:.1f}x the recent average {average:.0f}"
    if ratio <= (1.0 / ANOMALY_JUMP_RATIO):
        return f"price {modal_price:.0f} is {1 / ratio:.1f}x below the recent average {average:.0f}"
    return None


def summarise_rejections(results: List[ValidationResult]) -> Dict[str, int]:
    """Counts by reason, for the ingestion run record."""
    counts: Dict[str, int] = {}
    for result in results:
        if result.verdict is Verdict.REJECTED and result.reasons:
            key = result.reasons[0].value
            counts[key] = counts.get(key, 0) + 1
    return counts
