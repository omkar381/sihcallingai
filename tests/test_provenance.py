"""Freshness classification and provenance rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.market.provenance import (
    Confidence,
    DataQuality,
    Provenance,
    classify_freshness,
    hours_since,
    parse_data_date,
    worst,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)



@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch):
    """
    Freshness is relative to "now". These tests build observations relative to
    the fixed NOW, so the classifier must use the same clock - otherwise they
    pass on the day they were written and fail a few days later.
    """
    import app.market.provenance as provenance_module

    monkeypatch.setattr(provenance_module, "_utc_now", lambda: NOW.timestamp())


class TestParseDataDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("03/01/2024", (2024, 1, 3)),      # dd/mm/yyyy - the data.gov.in shape
            ("2026-09-11", (2026, 9, 11)),
            ("11-09-2026", (2026, 9, 11)),
            ("2026/09/11", (2026, 9, 11)),
        ],
    )
    def test_parses_known_formats(self, raw, expected):
        parsed = parse_data_date(raw)
        assert parsed is not None
        assert (parsed.year, parsed.month, parsed.day) == expected

    def test_day_month_order_is_not_ambiguous(self):
        # 03/01/2024 must be 3 January, not 1 March. Getting this backwards
        # would silently shift every Indian feed by up to eleven months.
        parsed = parse_data_date("03/01/2024")
        assert (parsed.month, parsed.day) == (1, 3)

    @pytest.mark.parametrize("raw", [None, "", "   ", "not-a-date", "99/99/9999"])
    def test_returns_none_rather_than_guessing(self, raw):
        assert parse_data_date(raw) is None


class TestClassifyFreshness:
    def test_today_is_live(self):
        assert classify_freshness(NOW - timedelta(hours=2), now=NOW.timestamp()) is DataQuality.LIVE

    def test_yesterday_is_live(self):
        assert classify_freshness(NOW - timedelta(days=1), now=NOW.timestamp()) is DataQuality.LIVE

    def test_four_days_is_fresh(self):
        assert classify_freshness(NOW - timedelta(days=4), now=NOW.timestamp()) is DataQuality.FRESH

    def test_two_months_is_stale(self):
        assert classify_freshness(NOW - timedelta(days=60), now=NOW.timestamp()) is DataQuality.STALE

    def test_the_january_2024_case_is_stale_not_live(self):
        # The defect this whole module exists to prevent: the scraper returned
        # 03/01/2024 and the agent narrated it as a "Live update".
        quality = classify_freshness(parse_data_date("03/01/2024"), now=NOW.timestamp())
        assert quality is DataQuality.STALE
        assert not quality.presentable_as_current

    def test_missing_date_is_unavailable(self):
        assert classify_freshness(None, now=NOW.timestamp()) is DataQuality.UNAVAILABLE

    def test_future_date_is_degraded_not_live(self):
        future = NOW + timedelta(days=3)
        assert classify_freshness(future, now=NOW.timestamp()) is DataQuality.DEGRADED


class TestQualityOrdering:
    def test_rank_order(self):
        assert DataQuality.LIVE.rank > DataQuality.FRESH.rank > DataQuality.STALE.rank
        assert DataQuality.STALE.rank > DataQuality.DEGRADED.rank > DataQuality.MOCK.rank
        assert DataQuality.MOCK.rank > DataQuality.UNAVAILABLE.rank

    @pytest.mark.parametrize(
        "quality,ok",
        [
            (DataQuality.LIVE, True),
            (DataQuality.FRESH, True),
            (DataQuality.STALE, False),
            (DataQuality.DEGRADED, False),
            (DataQuality.MOCK, False),
            (DataQuality.UNAVAILABLE, False),
        ],
    )
    def test_only_live_and_fresh_may_be_called_current(self, quality, ok):
        assert quality.presentable_as_current is ok

    def test_worst_of_mixed_inputs_wins(self):
        live = Provenance.from_observation("a", NOW - timedelta(hours=1))
        stale = Provenance.from_observation("b", NOW - timedelta(days=50))
        # A derived figure is only as trustworthy as its weakest input.
        assert worst(live, stale) is DataQuality.STALE

    def test_worst_of_nothing_is_unavailable(self):
        assert worst() is DataQuality.UNAVAILABLE


class TestProvenance:
    def test_unavailable_carries_no_confidence(self):
        p = Provenance.unavailable("data.gov.in", "timeout")
        assert p.data_quality is DataQuality.UNAVAILABLE
        assert p.confidence is Confidence.INSUFFICIENT_DATA
        assert not p.presentable_as_current
        assert "timeout" in p.notes[0]

    def test_mock_is_never_presentable(self):
        p = Provenance.mock("demo")
        assert p.data_quality is DataQuality.MOCK
        assert not p.presentable_as_current

    def test_degrade_preserves_source(self):
        p = Provenance.from_observation("data.gov.in", NOW - timedelta(hours=1))
        assert p.data_quality is DataQuality.LIVE
        p.degrade("modal outside min/max")
        assert p.data_quality is DataQuality.DEGRADED
        assert p.source == "data.gov.in"

    def test_age_description_is_human(self):
        assert Provenance.from_observation("s", NOW - timedelta(hours=3)).describe_age() == "today"
        old = Provenance.from_observation("s", parse_data_date("03/01/2024"))
        assert "month" in old.describe_age()

    def test_to_dict_serialises_enums(self):
        payload = Provenance.from_observation("s", NOW - timedelta(hours=1)).to_dict()
        assert payload["data_quality"] == "LIVE"
        assert isinstance(payload["confidence"], str)
        assert "age_description" in payload


def test_hours_since_handles_missing_date():
    assert hours_since(None) is None
