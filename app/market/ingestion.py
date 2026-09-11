"""
Market data ingestion.

Contract: an ingestion run either stores validated real observations, or it
records a failure. It never substitutes fixture data for a failed source.
A source that is down must be visible as down - that visibility is what lets
the voice layer say "I could not reach today's market data" instead of
inventing a number.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from app.config import get_settings
from app.market.provenance import DataQuality
from app.market.repository import (
    IngestionResult,
    PriceRecord,
    finish_ingestion_run,
    get_market_by_name,
    start_ingestion_run,
    upsert_price_records,
)
from app.market.validation import (
    ValidationResult,
    Verdict,
    summarise_rejections,
    validate_raw_record,
)

logger = logging.getLogger(__name__)

SOURCE_DATAGOV = "data.gov.in:variety-wise-daily-market-prices"

DATAGOV_BASE = "https://api.data.gov.in/resource"
MANDI_DAILY_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"

# data.gov.in is frequently slow under load. A generous timeout with retries
# beats a fast failure that drops the day's data entirely.
REQUEST_TIMEOUT_SECONDS = 45.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3.0

# The published sample key is shared by every tutorial on the internet and is
# throttled accordingly. Detect it so the operator is told the real reason
# ingestion is failing.
SAMPLE_KEY_PREFIX = "579b464db66ec23bdd"


class IngestionError(RuntimeError):
    """Raised when a source cannot be read. Carries no data by design."""


@dataclass
class SourceStatus:
    source: str
    reachable: bool
    detail: str
    using_sample_key: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "reachable": self.reachable,
            "detail": self.detail,
            "using_sample_key": self.using_sample_key,
        }


# ---------------------------------------------------------------------------
# data.gov.in adapter
# ---------------------------------------------------------------------------

# The resource exposes these column names; map them onto our canonical keys.
_DATAGOV_FIELD_MAP = {
    "state": ("state", "State"),
    "district": ("district", "District"),
    "market": ("market", "Market"),
    "commodity": ("commodity", "Commodity"),
    "variety": ("variety", "Variety"),
    "grade": ("grade", "Grade"),
    "min_price": ("min_price", "Min_Price", "min_x0020_price"),
    "max_price": ("max_price", "Max_Price", "max_x0020_price"),
    "modal_price": ("modal_price", "Modal_Price", "modal_x0020_price"),
    "arrival_date": ("arrival_date", "Arrival_Date", "arrival_x0020_date"),
    "arrival_qty": ("arrivals", "Arrivals", "arrival_quantity"),
}


def _pick(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _map_datagov_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {canonical: _pick(row, keys) for canonical, keys in _DATAGOV_FIELD_MAP.items()}


def _api_key() -> str:
    return get_settings().DATA_GOV_API_KEY or ""


def fetch_datagov_rows(
    state: Optional[str] = None,
    district: Optional[str] = None,
    commodity: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Fetch raw rows from data.gov.in.

    Raises IngestionError on failure rather than returning [], so a caller
    cannot confuse "the source is down" with "there were no arrivals today".
    """
    key = _api_key()
    if not key:
        raise IngestionError("DATA_GOV_API_KEY is not configured")

    params: Dict[str, str] = {
        "api-key": key,
        "format": "json",
        "limit": str(limit),
        "offset": str(offset),
    }
    if state:
        params["filters[state.keyword]"] = state
    if district:
        params["filters[district]"] = district
    if commodity:
        params["filters[commodity]"] = commodity

    url = f"{DATAGOV_BASE}/{MANDI_DAILY_RESOURCE}"
    last_error: Optional[str] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(url, params=params)
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning("data.gov.in attempt %d/%d -> %s", attempt, MAX_ATTEMPTS, last_error)
            else:
                payload = response.json()
                records = payload.get("records", [])
                logger.info(
                    "data.gov.in returned %d rows (total=%s) for state=%s commodity=%s",
                    len(records), payload.get("total"), state, commodity,
                )
                return records
        except Exception as exc:  # network, JSON, timeout
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("data.gov.in attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, last_error)

        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    hint = ""
    if key.startswith(SAMPLE_KEY_PREFIX):
        hint = (
            " (the configured key is data.gov.in's shared public sample key, which is heavily "
            "rate limited - register for a personal key and set DATA_GOV_API_KEY)"
        )
    raise IngestionError(f"data.gov.in unreachable after {MAX_ATTEMPTS} attempts: {last_error}{hint}")


def check_source(state: str = "Karnataka") -> SourceStatus:
    """Cheap liveness probe used by the admin freshness view."""
    key = _api_key()
    using_sample = key.startswith(SAMPLE_KEY_PREFIX)
    try:
        rows = fetch_datagov_rows(state=state, limit=1)
        return SourceStatus(
            source=SOURCE_DATAGOV,
            reachable=True,
            detail=f"returned {len(rows)} row(s)",
            using_sample_key=using_sample,
        )
    except IngestionError as exc:
        return SourceStatus(
            source=SOURCE_DATAGOV,
            reachable=False,
            detail=str(exc),
            using_sample_key=using_sample,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _to_price_records(
    validations: Iterable[ValidationResult],
    run_id: str,
) -> List[PriceRecord]:
    records: List[PriceRecord] = []
    for result in validations:
        if not result.accepted or not result.normalized:
            continue
        data = dict(result.normalized)
        quality = data.pop("data_quality")
        market_row = get_market_by_name(data["market"], data.get("state"))
        records.append(
            PriceRecord(
                **data,
                data_quality=quality if isinstance(quality, DataQuality) else DataQuality(quality),
                market_id=market_row.id if market_row else None,
                ingestion_run_id=run_id,
            )
        )
    return records


def ingest_datagov(
    state: Optional[str] = "Karnataka",
    district: Optional[str] = None,
    commodity: Optional[str] = None,
    limit: int = 1000,
) -> IngestionResult:
    """
    Run one ingestion pass and record the outcome.

    Always returns an IngestionResult - failures are recorded with
    status FAILED and zero accepted records, never masked.
    """
    scope = f"state={state} district={district} commodity={commodity}"
    run_id = start_ingestion_run(SOURCE_DATAGOV, scope)
    result = IngestionResult(run_id=run_id, source=SOURCE_DATAGOV, status="RUNNING")

    try:
        rows = fetch_datagov_rows(state=state, district=district, commodity=commodity, limit=limit)
    except IngestionError as exc:
        result.status = "FAILED"
        result.error = str(exc)
        finish_ingestion_run(result)
        logger.error("Ingestion run %s failed: %s", run_id, exc)
        return result

    result.records_fetched = len(rows)
    now = datetime.now(timezone.utc)

    validations = [
        validate_raw_record(_map_datagov_row(row), source=SOURCE_DATAGOV, now=now)
        for row in rows
    ]

    accepted = [v for v in validations if v.accepted]
    result.records_rejected = len(validations) - len(accepted)
    result.rejections = summarise_rejections(validations)

    records = _to_price_records(accepted, run_id)
    inserted, duplicates = upsert_price_records(records)

    result.records_accepted = inserted
    result.records_duplicate = duplicates
    result.status = "SUCCESS" if inserted or duplicates else "EMPTY"
    finish_ingestion_run(result)

    logger.info(
        "Ingestion %s: fetched=%d accepted=%d duplicate=%d rejected=%d",
        run_id, result.records_fetched, inserted, duplicates, result.records_rejected,
    )
    return result


def ingest_many(
    commodities: Sequence[str],
    states: Sequence[str] = ("Karnataka", "Maharashtra"),
    limit: int = 1000,
) -> List[IngestionResult]:
    """Ingest a set of commodity/state combinations, continuing past failures."""
    results: List[IngestionResult] = []
    for state in states:
        for commodity in commodities:
            results.append(ingest_datagov(state=state, commodity=commodity, limit=limit))
    return results
