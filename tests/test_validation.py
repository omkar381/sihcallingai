"""
Ingestion validation rules.

Real feeds emit blanks, transposed columns, unit errors and impossible dates.
These tests encode which of those are repairable and which must be refused,
because a silently "fixed" record is indistinguishable from invented data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.market.provenance import DataQuality
from app.market.validation import (
    RejectionReason,
    Verdict,
    detect_price_anomaly,
    summarise_rejections,
    validate_raw_record,
)

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


def good_row(**overrides):
    row = {
        "state": "Karnataka",
        "district": "Kalaburagi",
        "market": "Kalaburagi",
        "commodity": "Onion",
        "variety": "Red",
        "grade": "FAQ",
        "min_price": "2200",
        "max_price": "2800",
        "modal_price": "2500",
        "arrival_date": "10/09/2026",
        "arrival_qty": "430",
    }
    row.update(overrides)
    return row


def validate(**overrides):
    return validate_raw_record(good_row(**overrides), source="test", now=NOW)


class TestHappyPath:
    def test_valid_row_is_accepted_and_normalised(self):
        result = validate()
        assert result.verdict is Verdict.ACCEPTED
        assert result.normalized["commodity"] == "Onion"
        assert result.normalized["modal_price"] == 2500.0
        assert result.normalized["arrival_date"] == "2026-09-10"
        assert result.normalized["price_unit"] == "INR/quintal"

    def test_recent_row_is_marked_live(self):
        assert validate().normalized["data_quality"] is DataQuality.LIVE

    def test_old_row_is_marked_stale_not_rejected(self):
        # Stale data is still real data; it must be kept and labelled.
        result = validate(arrival_date="03/01/2024")
        assert result.accepted
        assert result.normalized["data_quality"] is DataQuality.STALE

    def test_aliases_are_canonicalised(self):
        result = validate(commodity="ONION (RED)", market="Gulbarga APMC")
        assert result.normalized["commodity"] == "Onion"
        assert result.normalized["market"] == "Kalaburagi"


class TestRejections:
    @pytest.mark.parametrize(
        "overrides,reason",
        [
            ({"commodity": ""}, RejectionReason.MISSING_COMMODITY),
            ({"commodity": "unobtainium"}, RejectionReason.UNKNOWN_COMMODITY),
            ({"market": ""}, RejectionReason.MISSING_MARKET),
            ({"arrival_date": ""}, RejectionReason.MISSING_DATE),
            ({"arrival_date": "not-a-date"}, RejectionReason.UNPARSEABLE_DATE),
            ({"modal_price": "", "min_price": "", "max_price": ""}, RejectionReason.MISSING_PRICE),
            ({"modal_price": "-500"}, RejectionReason.NEGATIVE_PRICE),
        ],
    )
    def test_rejected_with_reason(self, overrides, reason):
        result = validate(**overrides)
        assert result.verdict is Verdict.REJECTED
        assert reason in result.reasons

    def test_future_dated_row_is_rejected(self):
        future = (NOW + timedelta(days=5)).strftime("%d/%m/%Y")
        result = validate(arrival_date=future)
        assert result.verdict is Verdict.REJECTED
        assert RejectionReason.FUTURE_DATE in result.reasons

    def test_unit_error_is_rejected_not_stored(self):
        # An onion price of 25 is a per-kg figure in a per-quintal column.
        # Storing it would drag every average down.
        result = validate(modal_price="25", min_price="22", max_price="28")
        assert result.verdict is Verdict.REJECTED
        assert RejectionReason.PRICE_OUT_OF_RANGE in result.reasons

    def test_absurdly_high_price_is_rejected(self):
        result = validate(modal_price="2500000", min_price="", max_price="")
        assert result.verdict is Verdict.REJECTED
        assert RejectionReason.PRICE_OUT_OF_RANGE in result.reasons

    def test_rejection_never_returns_normalised_data(self):
        assert validate(commodity="unobtainium").normalized is None


class TestRepairs:
    def test_transposed_min_max_are_swapped_and_reported(self):
        result = validate(min_price="2800", max_price="2200")
        assert result.accepted
        assert result.normalized["min_price"] == 2200
        assert result.normalized["max_price"] == 2800
        assert any("inverted" in w for w in result.warnings)

    def test_missing_modal_is_derived_from_the_range(self):
        result = validate(modal_price="")
        assert result.accepted
        assert result.normalized["modal_price"] == 2500  # midpoint of 2200-2800
        assert any("derived" in w for w in result.warnings)

    def test_modal_outside_range_is_degraded_not_silently_fixed(self):
        result = validate(modal_price="3500")
        assert result.verdict is Verdict.ACCEPTED_DEGRADED
        assert result.normalized["data_quality"] is DataQuality.DEGRADED

    def test_negative_arrival_is_discarded_but_row_survives(self):
        result = validate(arrival_qty="-50")
        assert result.accepted
        assert result.normalized["arrival_qty"] is None

    @pytest.mark.parametrize("blank", ["", "-", "NA", "N/A", "NR", "null"])
    def test_blank_sentinels_are_absent_not_zero(self, blank):
        result = validate(arrival_qty=blank)
        assert result.accepted
        assert result.normalized["arrival_qty"] is None

    def test_thousands_separators_are_parsed(self):
        result = validate(modal_price="12,500", min_price="", max_price="",
                          commodity="Tur")
        assert result.accepted
        assert result.normalized["modal_price"] == 12500


class TestAnomalyDetection:
    def test_spike_is_flagged(self):
        assert detect_price_anomaly(9000, [2000, 2100, 2050, 1950]) is not None

    def test_collapse_is_flagged(self):
        assert detect_price_anomaly(400, [2000, 2100, 2050, 1950]) is not None

    def test_normal_move_is_not_flagged(self):
        assert detect_price_anomaly(2300, [2000, 2100, 2050, 1950]) is None

    def test_too_little_history_yields_no_opinion(self):
        assert detect_price_anomaly(9000, [2000]) is None


def test_rejection_summary_counts_by_reason():
    results = [
        validate(commodity="unobtainium"),
        validate(commodity="unobtainium"),
        validate(arrival_date="not-a-date"),
        validate(),
    ]
    summary = summarise_rejections(results)
    assert summary["UNKNOWN_COMMODITY"] == 2
    assert summary["UNPARSEABLE_DATE"] == 1
    assert "ACCEPTED" not in summary
