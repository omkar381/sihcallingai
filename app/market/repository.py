"""
Data access for market intelligence.

All SQL lives here. Services above this layer deal in dataclasses and never
see a cursor, which is what makes the eventual PostgreSQL migration a
connection-factory swap rather than a rewrite.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.db import DB_LOCK, get_connection
from app.market.provenance import DataQuality, Provenance, classify_freshness, parse_data_date

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0

# Straight-line distance understates road distance. 1.3 is a conservative
# multiplier for Indian state highways and is applied consistently so market
# comparisons stay fair even where it is imprecise.
ROAD_DISTANCE_FACTOR = 1.3


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PriceRecord:
    """One market x commodity x day observation."""

    state: str
    district: str
    market: str
    commodity: str
    modal_price: float
    arrival_date: str
    source: str
    variety: Optional[str] = None
    grade: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    price_unit: str = "INR/quintal"
    arrival_qty: Optional[float] = None
    arrival_unit: Optional[str] = None
    data_quality: DataQuality = DataQuality.LIVE
    market_id: Optional[str] = None
    fetched_at: int = field(default_factory=lambda: int(time.time()))
    ingestion_run_id: Optional[str] = None

    @property
    def arrival_datetime(self) -> Optional[datetime]:
        return parse_data_date(self.arrival_date)

    def provenance(self) -> Provenance:
        return Provenance.from_observation(
            source=self.source,
            data_date=self.arrival_datetime,
            fetched_at=float(self.fetched_at),
            quality_override=(
                self.data_quality
                if self.data_quality in (DataQuality.MOCK, DataQuality.DEGRADED)
                else None
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "district": self.district,
            "market": self.market,
            "commodity": self.commodity,
            "variety": self.variety,
            "grade": self.grade,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "modal_price": self.modal_price,
            "price_unit": self.price_unit,
            "arrival_qty": self.arrival_qty,
            "arrival_unit": self.arrival_unit,
            "arrival_date": self.arrival_date,
            "provenance": self.provenance().to_dict(),
        }


@dataclass
class Market:
    id: str
    name: str
    district: str
    state: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    market_type: str = "APMC"
    distance_km: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "district": self.district,
            "state": self.state,
            "market_type": self.market_type,
        }
        if self.distance_km is not None:
            payload["distance_km"] = round(self.distance_km, 1)
        return payload


@dataclass
class IngestionResult:
    run_id: str
    source: str
    status: str
    records_fetched: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    records_duplicate: int = 0
    error: Optional[str] = None
    rejections: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "source": self.source,
            "status": self.status,
            "records_fetched": self.records_fetched,
            "records_accepted": self.records_accepted,
            "records_rejected": self.records_rejected,
            "records_duplicate": self.records_duplicate,
            "error": self.error,
            "rejections": self.rejections,
        }


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def road_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Estimated road distance. Explicitly an estimate, never presented as routed."""
    return haversine_km(lat1, lon1, lat2, lon2) * ROAD_DISTANCE_FACTOR


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

def _row_to_market(row) -> Market:
    return Market(
        id=row["id"],
        name=row["name"],
        district=row["district"],
        state=row["state"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        market_type=row["market_type"],
    )


def get_market_by_name(name: str, state: Optional[str] = None) -> Optional[Market]:
    with DB_LOCK:
        conn = get_connection()
        try:
            if state:
                row = conn.execute(
                    "SELECT * FROM markets WHERE LOWER(name) = LOWER(?) AND LOWER(state) = LOWER(?) LIMIT 1",
                    (name, state),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM markets WHERE LOWER(name) = LOWER(?) LIMIT 1", (name,)
                ).fetchone()
            return _row_to_market(row) if row else None
        finally:
            conn.close()


def list_markets(state: Optional[str] = None, district: Optional[str] = None) -> List[Market]:
    sql = "SELECT * FROM markets WHERE is_active = 1"
    params: List[Any] = []
    if state:
        sql += " AND LOWER(state) = LOWER(?)"
        params.append(state)
    if district:
        sql += " AND LOWER(district) = LOWER(?)"
        params.append(district)
    sql += " ORDER BY state, district, name"

    with DB_LOCK:
        conn = get_connection()
        try:
            return [_row_to_market(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def find_markets_near(
    latitude: float,
    longitude: float,
    radius_km: float = 150.0,
    limit: int = 12,
) -> List[Market]:
    """
    Markets within a road-distance radius, nearest first.

    Filters to a bounding box in SQL before computing distances, so this stays
    cheap as the registry grows.
    """
    deg = (radius_km / ROAD_DISTANCE_FACTOR) / 111.0
    lat_min, lat_max = latitude - deg, latitude + deg
    lon_span = deg / max(math.cos(math.radians(latitude)), 0.1)
    lon_min, lon_max = longitude - lon_span, longitude + lon_span

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT * FROM markets
                WHERE is_active = 1
                  AND latitude BETWEEN ? AND ?
                  AND longitude BETWEEN ? AND ?
                """,
                (lat_min, lat_max, lon_min, lon_max),
            ).fetchall()
        finally:
            conn.close()

    out: List[Market] = []
    for row in rows:
        market = _row_to_market(row)
        if market.latitude is None or market.longitude is None:
            continue
        market.distance_km = road_distance_km(latitude, longitude, market.latitude, market.longitude)
        if market.distance_km <= radius_km:
            out.append(market)

    # Explicit None check: `m.distance_km or inf` would sort the farmer's own
    # market (0.0 km, falsy) to the very end.
    out.sort(key=lambda m: float("inf") if m.distance_km is None else m.distance_km)
    return out[:limit]


def resolve_origin(location: str) -> Optional[Market]:
    """
    Resolve a farmer's stated location to a registry market (their origin point).

    Matches the market name first, then falls back to any market in a district
    of that name, which is how callers usually phrase it ("I am in Bidar").
    """
    from app.market.normalization import normalize_market_name

    canonical = normalize_market_name(location)
    if not canonical:
        return None

    direct = get_market_by_name(canonical)
    if direct:
        return direct

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM markets WHERE LOWER(district) = LOWER(?) AND is_active = 1 LIMIT 1",
                (canonical,),
            ).fetchone()
            return _row_to_market(row) if row else None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def _row_to_price(row) -> PriceRecord:
    try:
        quality = DataQuality(row["data_quality"])
    except ValueError:
        quality = DataQuality.DEGRADED
    return PriceRecord(
        state=row["state"],
        district=row["district"],
        market=row["market"],
        commodity=row["commodity"],
        variety=row["variety"],
        grade=row["grade"],
        min_price=row["min_price"],
        max_price=row["max_price"],
        modal_price=row["modal_price"],
        price_unit=row["price_unit"],
        arrival_qty=row["arrival_qty"],
        arrival_unit=row["arrival_unit"],
        arrival_date=row["arrival_date"],
        source=row["source"],
        data_quality=quality,
        market_id=row["market_id"],
        fetched_at=row["fetched_at"],
    )


def upsert_price_records(records: Sequence[PriceRecord]) -> Tuple[int, int]:
    """
    Insert observations idempotently.

    Returns (inserted, duplicates). Re-running an ingestion over the same
    arrival day updates the row in place instead of growing the series, so a
    scheduler that overlaps itself cannot corrupt the history.
    """
    if not records:
        return (0, 0)

    inserted = 0
    duplicates = 0

    with DB_LOCK:
        conn = get_connection()
        try:
            for rec in records:
                existing = conn.execute(
                    """
                    SELECT id FROM mandi_price_history
                    WHERE market = ? AND commodity = ?
                      AND IFNULL(variety,'') = IFNULL(?,'')
                      AND IFNULL(grade,'') = IFNULL(?,'')
                      AND arrival_date = ?
                    """,
                    (rec.market, rec.commodity, rec.variety, rec.grade, rec.arrival_date),
                ).fetchone()

                if existing:
                    conn.execute(
                        """
                        UPDATE mandi_price_history
                        SET min_price = ?, max_price = ?, modal_price = ?,
                            arrival_qty = ?, arrival_unit = ?, fetched_at = ?,
                            source = ?, data_quality = ?, ingestion_run_id = ?
                        WHERE id = ?
                        """,
                        (
                            rec.min_price, rec.max_price, rec.modal_price,
                            rec.arrival_qty, rec.arrival_unit, rec.fetched_at,
                            rec.source, rec.data_quality.value, rec.ingestion_run_id,
                            existing["id"],
                        ),
                    )
                    duplicates += 1
                    continue

                conn.execute(
                    """
                    INSERT INTO mandi_price_history(
                        market_id, state, district, market, commodity, variety, grade,
                        min_price, max_price, modal_price, price_unit,
                        arrival_qty, arrival_unit, arrival_date,
                        fetched_at, source, data_quality, ingestion_run_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec.market_id, rec.state, rec.district, rec.market, rec.commodity,
                        rec.variety, rec.grade, rec.min_price, rec.max_price,
                        rec.modal_price, rec.price_unit, rec.arrival_qty, rec.arrival_unit,
                        rec.arrival_date, rec.fetched_at, rec.source,
                        rec.data_quality.value, rec.ingestion_run_id,
                    ),
                )
                inserted += 1

            conn.commit()
        finally:
            conn.close()

    return (inserted, duplicates)


def get_latest_price(
    commodity: str,
    market: Optional[str] = None,
    district: Optional[str] = None,
    state: Optional[str] = None,
) -> Optional[PriceRecord]:
    """Most recent observation for a commodity, optionally scoped to a market."""
    sql = "SELECT * FROM mandi_price_history WHERE LOWER(commodity) = LOWER(?)"
    params: List[Any] = [commodity]
    if market:
        sql += " AND LOWER(market) = LOWER(?)"
        params.append(market)
    if district:
        sql += " AND LOWER(district) = LOWER(?)"
        params.append(district)
    if state:
        sql += " AND LOWER(state) = LOWER(?)"
        params.append(state)
    sql += " ORDER BY arrival_date DESC, fetched_at DESC LIMIT 1"

    with DB_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(sql, params).fetchone()
            return _row_to_price(row) if row else None
        finally:
            conn.close()


def get_price_series(
    commodity: str,
    market: Optional[str] = None,
    district: Optional[str] = None,
    state: Optional[str] = None,
    days: int = 90,
    end_date: Optional[datetime] = None,
) -> List[PriceRecord]:
    """
    Observations for a commodity over a trailing window, oldest first.

    Ordering ascending matters: every analytic downstream assumes chronological
    order and reversing it would silently invert momentum calculations.
    """
    end = end_date or datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    sql = """
        SELECT * FROM mandi_price_history
        WHERE LOWER(commodity) = LOWER(?)
          AND arrival_date >= ? AND arrival_date <= ?
    """
    params: List[Any] = [commodity, start.date().isoformat(), end.date().isoformat()]
    if market:
        sql += " AND LOWER(market) = LOWER(?)"
        params.append(market)
    if district:
        sql += " AND LOWER(district) = LOWER(?)"
        params.append(district)
    if state:
        sql += " AND LOWER(state) = LOWER(?)"
        params.append(state)
    sql += " ORDER BY arrival_date ASC"

    with DB_LOCK:
        conn = get_connection()
        try:
            return [_row_to_price(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()


def get_latest_prices_for_markets(
    commodity: str,
    market_names: Iterable[str],
    max_age_days: int = 10,
) -> Dict[str, PriceRecord]:
    """
    Latest observation per market, for the multi-market comparison.

    Observations older than max_age_days are excluded outright: comparing
    today's price in one mandi against a three-week-old print in another
    would produce a confidently wrong recommendation.
    """
    names = [n for n in market_names if n]
    if not names:
        return {}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date().isoformat()
    placeholders = ",".join("?" for _ in names)

    sql = f"""
        SELECT * FROM mandi_price_history
        WHERE LOWER(commodity) = LOWER(?)
          AND arrival_date >= ?
          AND market COLLATE NOCASE IN ({placeholders})
        ORDER BY arrival_date ASC
    """

    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(sql, [commodity, cutoff, *names]).fetchall()
        finally:
            conn.close()

    latest: Dict[str, PriceRecord] = {}
    for row in rows:
        rec = _row_to_price(row)
        latest[rec.market.lower()] = rec  # ascending order leaves the newest last
    return latest


def count_price_records(commodity: Optional[str] = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM mandi_price_history"
    params: List[Any] = []
    if commodity:
        sql += " WHERE LOWER(commodity) = LOWER(?)"
        params.append(commodity)
    with DB_LOCK:
        conn = get_connection()
        try:
            return int(conn.execute(sql, params).fetchone()["n"])
        finally:
            conn.close()


def get_data_coverage() -> List[Dict[str, Any]]:
    """Per-commodity coverage, for the admin freshness view."""
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT commodity,
                       COUNT(*)                AS records,
                       COUNT(DISTINCT market)  AS markets,
                       COUNT(DISTINCT source)  AS sources,
                       MIN(arrival_date)       AS first_date,
                       MAX(arrival_date)       AS last_date,
                       SUM(CASE WHEN data_quality = 'MOCK' THEN 1 ELSE 0 END) AS mock_records
                FROM mandi_price_history
                GROUP BY commodity
                ORDER BY records DESC
                """
            ).fetchall()
        finally:
            conn.close()

    out = []
    for r in rows:
        freshness = classify_freshness(parse_data_date(r["last_date"]))
        mock_records = int(r["mock_records"] or 0)
        # Report the stored quality, not just how recent the date looks.
        # A fixture row dated today is still MOCK, and calling it LIVE here
        # would reintroduce exactly the mislabelling this module prevents.
        effective = DataQuality.MOCK if mock_records == r["records"] else freshness
        out.append({
            "commodity": r["commodity"],
            "records": r["records"],
            "markets": r["markets"],
            "sources": r["sources"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
            "date_freshness": freshness.value,
            "data_quality": effective.value,
            "mock_records": mock_records,
            "contains_mock": mock_records > 0,
        })
    return out


# ---------------------------------------------------------------------------
# Ingestion audit
# ---------------------------------------------------------------------------

def start_ingestion_run(source: str, scope: str = "") -> str:
    run_id = str(uuid.uuid4())
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO market_ingestion_runs(id, source, scope, status, started_at)
                VALUES (?, ?, ?, 'RUNNING', ?)
                """,
                (run_id, source, scope, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
    return run_id


def finish_ingestion_run(result: IngestionResult) -> None:
    with DB_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE market_ingestion_runs
                SET status = ?, completed_at = ?, records_fetched = ?,
                    records_accepted = ?, records_rejected = ?, records_duplicate = ?,
                    error = ?, rejection_summary = ?
                WHERE id = ?
                """,
                (
                    result.status, int(time.time()), result.records_fetched,
                    result.records_accepted, result.records_rejected,
                    result.records_duplicate, result.error,
                    json.dumps(result.rejections) if result.rejections else None,
                    result.run_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_recent_ingestion_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with DB_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM market_ingestion_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

    out = []
    for r in rows:
        out.append({
            "run_id": r["id"],
            "source": r["source"],
            "scope": r["scope"],
            "status": r["status"],
            "started_at": datetime.fromtimestamp(r["started_at"], tz=timezone.utc).isoformat(),
            "completed_at": (
                datetime.fromtimestamp(r["completed_at"], tz=timezone.utc).isoformat()
                if r["completed_at"] else None
            ),
            "records_fetched": r["records_fetched"],
            "records_accepted": r["records_accepted"],
            "records_rejected": r["records_rejected"],
            "records_duplicate": r["records_duplicate"],
            "error": r["error"],
            "rejections": json.loads(r["rejection_summary"]) if r["rejection_summary"] else {},
        })
    return out
