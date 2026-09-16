"""Shared fixtures and builders for the market intelligence test suite."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

import pytest

# Tests import the app package directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.market.provenance import DataQuality  # noqa: E402
from app.market.repository import PriceRecord  # noqa: E402


NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def make_record(
    *,
    modal: float,
    days_ago: int,
    market: str = "Kalaburagi",
    commodity: str = "Onion",
    arrival_qty: Optional[float] = None,
    quality: DataQuality = DataQuality.LIVE,
    now: datetime = NOW,
) -> PriceRecord:
    """Build a single price observation `days_ago` before `now`."""
    day = now - timedelta(days=days_ago)
    return PriceRecord(
        state="Karnataka",
        district="Kalaburagi",
        market=market,
        commodity=commodity,
        modal_price=modal,
        min_price=modal * 0.92,
        max_price=modal * 1.08,
        arrival_qty=arrival_qty,
        arrival_unit="tonne" if arrival_qty is not None else None,
        arrival_date=day.date().isoformat(),
        source="test",
        data_quality=quality,
        fetched_at=int(now.timestamp()),
    )


def make_series(
    prices: Sequence[float],
    *,
    market: str = "Kalaburagi",
    commodity: str = "Onion",
    arrivals: Optional[Sequence[float]] = None,
    now: datetime = NOW,
) -> List[PriceRecord]:
    """
    Build a daily series ending today.

    `prices[-1]` is today's price; earlier entries walk backwards one day at a
    time, which matches how the analytics expect a series to be ordered.
    """
    n = len(prices)
    out = []
    for i, price in enumerate(prices):
        days_ago = n - 1 - i
        qty = arrivals[i] if arrivals is not None and i < len(arrivals) else None
        out.append(
            make_record(
                modal=price, days_ago=days_ago, market=market,
                commodity=commodity, arrival_qty=qty, now=now,
            )
        )
    return out


@pytest.fixture
def now() -> datetime:
    return NOW


# ---------------------------------------------------------------------------
# Isolated database
# ---------------------------------------------------------------------------

@pytest.fixture
def trade_db(tmp_path, monkeypatch):
    """
    Point the whole data layer at a throwaway SQLite file.

    Trade tests create buyers, accept offers and release payments. Running
    those against the real development database would pollute it with test
    fixtures that later look like genuine records - exactly the kind of
    contamination the provenance rules exist to prevent.
    """
    import app.services.db as db

    db_file = tmp_path / "test_krishi.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))

    from app.market.schema import init_market_tables
    from app.trade.schema import init_trade_tables

    init_market_tables(seed=True)
    init_trade_tables()

    # farmers table is created by farmer_store; lots reference it loosely.
    try:
        from app.farmer_store import _init_db
        _init_db()
    except Exception:
        pass

    return str(db_file)


@pytest.fixture
def verified_buyer(trade_db):
    """A buyer that has cleared verification, ready to trade."""
    from app.trade import buyers

    buyer = buyers.create_buyer(
        "Test Agro Traders", buyers.BuyerType.APMC_TRADER,
        phone="+919000000001", district="Solapur", state="Maharashtra",
    )
    for doc in ("APMC_LICENCE", "PAN", "PHONE"):
        buyers.submit_document(buyer["id"], doc, doc_number=f"DOC-{doc}")
    return buyers.verify_buyer(buyer["id"], verified_by="test-admin")
