"""
Sale-window decision engine.

These tests pin the behaviour that matters most: the engine must refuse to
advise when data is weak, must never tell a farmer to hold a crop they cannot
store, and must produce a reason for every decision it makes.
"""

from __future__ import annotations

import pytest

from app.market.provenance import Confidence, DataQuality
from app.market.sale_window import (
    Decision,
    FarmerConstraints,
    recommend_sale_window,
)
from app.market.trends import build_trend_report
from tests.conftest import NOW, make_series


def undervalued_trend():
    """
    A market that has fallen well below its own long-run level and is just
    starting to recover, with arrivals thinning: the textbook case for waiting.
    """
    prices = [3200.0] * 80 + [2200, 2220, 2250, 2280, 2300, 2330, 2350, 2370, 2390, 2400]
    arrivals = [600.0] * 80 + [220.0] * 10
    return build_trend_report("Onion", make_series(prices, arrivals=arrivals), market="Kalaburagi")


def overvalued_falling_trend():
    """Price well above its benchmark, now falling, with arrivals swelling."""
    prices = [2000.0] * 80 + [3400, 3350, 3300, 3200, 3100, 3000, 2950, 2900, 2850, 2800]
    arrivals = [200.0] * 80 + [900.0] * 10
    return build_trend_report("Onion", make_series(prices, arrivals=arrivals), market="Kalaburagi")


class TestWaitPath:
    def test_undervalued_recovering_market_says_wait(self):
        rec = recommend_sale_window(
            undervalued_trend(),
            FarmerConstraints(storage_days_available=25, quantity_kg=3000),
        )
        assert rec.decision is Decision.WAIT
        assert rec.recommended_days > 0

    def test_wait_carries_an_expected_range_and_net_gain(self):
        rec = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=25)
        )
        assert rec.expected_price_low is not None
        assert rec.expected_price_high > rec.expected_price_low
        # Waiting must be worth more than it costs to hold.
        assert rec.net_expected_gain_per_quintal > 0
        assert rec.storage_cost_per_quintal >= 0

    def test_wait_explains_itself(self):
        rec = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=25)
        )
        assert rec.reasoning, "a recommendation with no reasoning is not acceptable"
        assert any("below" in r for r in rec.reasoning)
        assert rec.risks, "waiting always carries a risk that must be stated"

    def test_wait_never_exceeds_available_storage(self):
        rec = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=4)
        )
        assert rec.recommended_days <= 4


class TestHardConstraintsOverrideSignals:
    def test_no_storage_never_says_wait(self):
        # Even in the most bullish case the engine must not advise holding a
        # crop the farmer has nowhere to keep.
        rec = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=0)
        )
        assert rec.decision is not Decision.WAIT
        assert any("no storage" in r.lower() for r in rec.reasoning)

    def test_urgent_cash_need_never_says_wait(self):
        rec = recommend_sale_window(
            undervalued_trend(),
            FarmerConstraints(storage_days_available=30, needs_cash_urgently=True),
        )
        assert rec.decision is not Decision.WAIT
        assert any("cash" in r.lower() for r in rec.reasoning)

    def test_two_farmers_same_market_different_advice(self):
        # The whole point of taking constraints: identical market conditions
        # must be able to yield different advice.
        trend = undervalued_trend()
        patient = recommend_sale_window(trend, FarmerConstraints(storage_days_available=30))
        pressed = recommend_sale_window(
            trend, FarmerConstraints(storage_days_available=30, needs_cash_urgently=True)
        )
        assert patient.decision is not pressed.decision


class TestSellPath:
    def test_overvalued_falling_market_says_sell(self):
        rec = recommend_sale_window(
            overvalued_falling_trend(), FarmerConstraints(storage_days_available=30)
        )
        assert rec.decision in (Decision.SELL_NOW, Decision.SELL_PARTIAL)

    def test_sell_decision_has_zero_wait_days(self):
        rec = recommend_sale_window(
            overvalued_falling_trend(), FarmerConstraints(storage_days_available=0)
        )
        assert rec.decision is Decision.SELL_NOW
        assert rec.recommended_days == 0


class TestRefusalToGuess:
    def test_no_price_yields_insufficient_data(self):
        rec = recommend_sale_window(build_trend_report("Onion", []))
        assert rec.decision is Decision.INSUFFICIENT_DATA
        assert rec.confidence is Confidence.INSUFFICIENT_DATA
        assert rec.reasoning

    def test_insufficient_data_states_that_it_is_not_advising(self):
        rec = recommend_sale_window(build_trend_report("Onion", []))
        assert any("no recommendation" in r.lower() for r in rec.risks)

    def test_mock_data_is_refused_by_default(self):
        trend = build_trend_report(
            "Onion",
            make_series([2000, 2100, 2200])[:],
        )
        # Force the trend to look like fixture data.
        trend.data_quality = DataQuality.MOCK
        rec = recommend_sale_window(trend, FarmerConstraints(storage_days_available=20))
        assert rec.decision is Decision.INSUFFICIENT_DATA
        assert "MOCK" in rec.reasoning[0] or "demo" in rec.reasoning[0].lower()

    def test_mock_data_allowed_only_when_explicitly_requested(self):
        trend = undervalued_trend()
        trend.data_quality = DataQuality.MOCK
        rec = recommend_sale_window(
            trend, FarmerConstraints(storage_days_available=25), allow_mock=True
        )
        assert rec.decision is not Decision.INSUFFICIENT_DATA
        # ...but it must label itself loudly.
        assert any("DEMONSTRATION ONLY" in r for r in rec.risks)
        assert rec.confidence is Confidence.LOW

    def test_stale_data_lowers_confidence_and_warns(self):
        trend = undervalued_trend()
        trend.data_quality = DataQuality.STALE
        rec = recommend_sale_window(trend, FarmerConstraints(storage_days_available=25))
        assert rec.confidence is Confidence.LOW
        assert any("confirm at the mandi" in r.lower() for r in rec.risks)


class TestOutputContract:
    def test_every_decision_carries_reasoning(self):
        cases = [
            (undervalued_trend(), FarmerConstraints(storage_days_available=25)),
            (undervalued_trend(), FarmerConstraints(storage_days_available=0)),
            (overvalued_falling_trend(), FarmerConstraints(storage_days_available=25)),
            (build_trend_report("Onion", []), FarmerConstraints()),
        ]
        for trend, constraints in cases:
            rec = recommend_sale_window(trend, constraints)
            assert rec.reasoning, f"{rec.decision} produced no reasoning"

    def test_signals_are_exposed_with_their_numbers(self):
        rec = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=25)
        )
        assert rec.signals
        for signal in rec.signals:
            assert signal.direction in ("favours_waiting", "favours_selling", "neutral")
            assert signal.detail

    def test_result_is_serialisable_and_versioned(self):
        payload = recommend_sale_window(
            undervalued_trend(), FarmerConstraints(storage_days_available=25)
        ).to_dict()
        assert payload["engine_version"]
        assert payload["generated_at"]
        assert isinstance(payload["decision"], str)
        assert isinstance(payload["reasoning"], list)

    def test_engine_is_deterministic(self):
        trend = undervalued_trend()
        constraints = FarmerConstraints(storage_days_available=25)
        a = recommend_sale_window(trend, constraints).to_dict()
        b = recommend_sale_window(trend, constraints).to_dict()
        a.pop("generated_at"), b.pop("generated_at")
        assert a == b
