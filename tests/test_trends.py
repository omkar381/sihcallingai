"""Price and arrival analytics. Pure arithmetic over known series."""

from __future__ import annotations

import pytest

from app.market.trends import (
    Direction,
    Volatility,
    build_trend_report,
    compare_to_benchmark,
    compute_arrival_stats,
    compute_window_stats,
)
from app.market.provenance import Confidence, DataQuality
from tests.conftest import NOW, make_series


class TestWindowStats:
    def test_known_series_statistics(self):
        records = make_series([100, 200, 300, 400, 500])
        stats = compute_window_stats(records, days=30, end=NOW)
        assert stats.sample_count == 5
        assert stats.average == 300
        assert stats.median == 300
        assert stats.minimum == 100
        assert stats.maximum == 500
        assert stats.first == 100
        assert stats.last == 500

    def test_change_pct_is_first_to_last(self):
        stats = compute_window_stats(make_series([1000, 1100, 1200]), days=30, end=NOW)
        assert stats.change_pct == pytest.approx(20.0)
        assert stats.direction is Direction.RISING

    def test_falling_series(self):
        stats = compute_window_stats(make_series([2000, 1800, 1600]), days=30, end=NOW)
        assert stats.change_pct == pytest.approx(-20.0)
        assert stats.direction is Direction.FALLING

    def test_small_move_is_flat_not_a_trend(self):
        # A 1% drift must not be reported as a rising market.
        stats = compute_window_stats(make_series([2000, 2005, 2010]), days=30, end=NOW)
        assert stats.direction is Direction.FLAT

    def test_window_excludes_older_observations(self):
        records = make_series([100] * 40)   # 40 days of history
        assert compute_window_stats(records, days=7, end=NOW).sample_count == 8  # incl. today
        assert compute_window_stats(records, days=30, end=NOW).sample_count == 31

    def test_empty_window_is_insufficient(self):
        stats = compute_window_stats([], days=30, end=NOW)
        assert stats.sample_count == 0
        assert not stats.sufficient
        assert stats.average is None
        assert stats.direction is Direction.UNKNOWN

    def test_stable_series_is_low_volatility(self):
        stats = compute_window_stats(make_series([2000, 2010, 1990, 2005, 1995]), days=30, end=NOW)
        assert stats.volatility is Volatility.LOW

    def test_wild_series_is_high_volatility(self):
        stats = compute_window_stats(make_series([1000, 3000, 1200, 2800, 1100]), days=30, end=NOW)
        assert stats.volatility is Volatility.HIGH

    def test_volatility_is_scale_free(self):
        # Same relative dispersion at very different price levels must band
        # identically, which is why CV is used rather than stdev.
        cheap = compute_window_stats(make_series([100, 110, 90, 105, 95]), days=30, end=NOW)
        dear = compute_window_stats(make_series([10000, 11000, 9000, 10500, 9500]), days=30, end=NOW)
        assert cheap.volatility is dear.volatility


class TestArrivalStats:
    def test_falling_arrivals_detected(self):
        records = make_series(
            [2000] * 30,
            arrivals=[500] * 23 + [200] * 7,   # recent week much thinner
        )
        stats = compute_arrival_stats(records, days=30, end=NOW)
        assert stats.direction is Direction.FALLING
        assert stats.change_pct < 0

    def test_rising_arrivals_detected(self):
        records = make_series([2000] * 30, arrivals=[200] * 23 + [600] * 7)
        stats = compute_arrival_stats(records, days=30, end=NOW)
        assert stats.direction is Direction.RISING

    def test_absent_arrivals_are_unknown_not_zero(self):
        stats = compute_arrival_stats(make_series([2000] * 10), days=30, end=NOW)
        assert stats.sample_count == 0
        assert stats.average_qty is None
        assert stats.direction is Direction.UNKNOWN


class TestTrendReport:
    def test_report_uses_latest_observation_as_current(self):
        report = build_trend_report("Onion", make_series([2000, 2100, 2500]), market="Kalaburagi")
        assert report.current_price == 2500
        assert report.current_date == NOW.date().isoformat()

    def test_report_with_no_records_is_explicit(self):
        report = build_trend_report("Onion", [], market="Kalaburagi")
        assert report.current_price is None
        assert report.confidence is Confidence.INSUFFICIENT_DATA
        assert report.data_quality is DataQuality.UNAVAILABLE
        assert report.notes  # must say why

    def test_thin_history_lowers_confidence(self):
        thin = build_trend_report("Onion", make_series([2000, 2100]))
        deep = build_trend_report("Onion", make_series([2000 + i for i in range(40)]))
        assert thin.confidence.value != deep.confidence.value
        assert deep.confidence is Confidence.HIGH

    def test_thin_windows_are_flagged_in_notes(self):
        report = build_trend_report("Onion", make_series([2000, 2100]))
        assert any("too few observations" in n for n in report.notes)

    def test_missing_arrivals_are_flagged(self):
        report = build_trend_report("Onion", make_series([2000] * 10))
        assert any("Arrival volumes" in n for n in report.notes)

    def test_report_is_deterministic(self):
        # Same inputs must always yield the same report, or a stored
        # recommendation could never be reproduced for audit.
        records = make_series([2000, 2200, 2100, 2400])
        a = build_trend_report("Onion", records, market="Kalaburagi").to_dict()
        b = build_trend_report("Onion", records, market="Kalaburagi").to_dict()
        a.pop("provenance"), b.pop("provenance")
        assert a == b

    def test_unordered_input_is_sorted_before_analysis(self):
        records = make_series([1000, 2000, 3000])
        shuffled = [records[2], records[0], records[1]]
        assert build_trend_report("Onion", shuffled).current_price == 3000


class TestBenchmarkComparison:
    def test_above_benchmark(self):
        result = compare_to_benchmark(2400, 2000)
        assert result["position"] == "above"
        assert result["difference_pct"] == pytest.approx(20.0)

    def test_below_benchmark(self):
        result = compare_to_benchmark(1600, 2000)
        assert result["position"] == "below"
        assert result["difference_pct"] == pytest.approx(-20.0)

    def test_no_benchmark_returns_none(self):
        assert compare_to_benchmark(2000, None) is None
        assert compare_to_benchmark(2000, 0) is None
