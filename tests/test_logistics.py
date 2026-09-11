"""
Transport cost and net realisable price.

This arithmetic decides which market a farmer drives to, so the invariants
here are treated as money-critical.
"""

from __future__ import annotations

import pytest

from app.market.logistics import (
    VEHICLE_CLASSES,
    compute_net_realisation,
    estimate_transport,
    expected_rejection_pct,
    select_vehicle,
    storage_cost_per_quintal,
)
from app.market.normalization import KG_PER_QUINTAL


class TestVehicleSelection:
    @pytest.mark.parametrize(
        "quantity_kg,expected_capacity",
        [(500, 1_000), (1_000, 1_000), (1_200, 1_500), (2_500, 3_000), (6_000, 7_000)],
    )
    def test_picks_smallest_sufficient_vehicle(self, quantity_kg, expected_capacity):
        assert select_vehicle(quantity_kg).capacity_kg == expected_capacity

    def test_oversized_load_falls_back_to_largest(self):
        assert select_vehicle(50_000) is VEHICLE_CLASSES[-1]

    def test_oversized_load_is_split_into_trips(self):
        quote = estimate_transport(distance_km=50, quantity_kg=40_000)
        assert quote.trips > 1
        assert quote.total_cost > 0


class TestTransportEstimate:
    def test_longer_haul_costs_more(self):
        near = estimate_transport(distance_km=20, quantity_kg=3_000)
        far = estimate_transport(distance_km=200, quantity_kg=3_000)
        assert far.total_cost > near.total_cost

    def test_minimum_fare_applies_on_very_short_trips(self):
        vehicle = select_vehicle(1_000)
        quote = estimate_transport(distance_km=1, quantity_kg=1_000)
        assert quote.freight_cost >= vehicle.minimum_fare

    def test_zero_distance_still_costs_handling(self):
        quote = estimate_transport(distance_km=0, quantity_kg=1_000)
        assert quote.handling_cost > 0

    def test_rejects_non_positive_quantity(self):
        with pytest.raises(ValueError):
            estimate_transport(distance_km=10, quantity_kg=0)

    def test_is_labelled_an_estimate(self):
        # It must never be mistaken for a quoted transporter rate.
        quote = estimate_transport(distance_km=50, quantity_kg=1_000)
        assert quote.is_estimate is True
        assert "estimate" in quote.to_dict()["source"].lower()


class TestNetRealisation:
    def test_net_is_always_below_gross(self):
        net = compute_net_realisation("Solapur", 3_000, distance_km=100, quantity_kg=3_000)
        assert net.net_price_per_quintal < net.gross_price_per_quintal

    def test_breakdown_arithmetic_reconciles(self):
        net = compute_net_realisation("Solapur", 3_000, distance_km=100, quantity_kg=3_000)
        deductions = (
            net.transport_per_quintal
            + net.handling_per_quintal
            + net.market_fee_per_quintal
            + net.commission_per_quintal
            + net.rejection_loss_per_quintal
        )
        assert net.gross_price_per_quintal - deductions == pytest.approx(
            net.net_price_per_quintal, rel=1e-9
        )

    def test_breakdown_is_itemised_for_explanation(self):
        net = compute_net_realisation("Solapur", 3_000, distance_km=100, quantity_kg=3_000)
        items = [line["item"] for line in net.breakdown]
        assert "Freight" in items
        assert "Market fee" in items
        assert "Net realisation" in items
        for line in net.breakdown:
            assert line["basis"], "every deduction must state its basis"

    def test_higher_gross_can_lose_to_a_nearer_market(self):
        # The signature scenario from the spec: the headline price is not the
        # answer once freight is paid.
        far = compute_net_realisation("Far", 2_800, distance_km=400, quantity_kg=1_000)
        near = compute_net_realisation("Near", 2_650, distance_km=10, quantity_kg=1_000)
        assert far.gross_price_per_quintal > near.gross_price_per_quintal
        assert near.net_price_per_quintal > far.net_price_per_quintal

    def test_freight_per_quintal_falls_as_load_grows(self):
        # The economic basis of FPO aggregation: bigger lots move cheaper.
        small = compute_net_realisation("M", 3_000, distance_km=100, quantity_kg=500)
        large = compute_net_realisation("M", 3_000, distance_km=100, quantity_kg=7_000)
        assert large.transport_per_quintal < small.transport_per_quintal
        assert large.net_price_per_quintal > small.net_price_per_quintal

    def test_totals_scale_with_quantity(self):
        net = compute_net_realisation("M", 3_000, distance_km=50, quantity_kg=2_000)
        quintals = 2_000 / KG_PER_QUINTAL
        assert net.total_net == pytest.approx(net.net_price_per_quintal * quintals)
        assert net.total_gross == pytest.approx(3_000 * quintals)

    def test_rejects_non_positive_price(self):
        with pytest.raises(ValueError):
            compute_net_realisation("M", 0, distance_km=10, quantity_kg=1_000)

    def test_perishable_carries_a_larger_rejection_allowance(self):
        fresh = compute_net_realisation("M", 3_000, 100, 1_000, perishable=True)
        dry = compute_net_realisation("M", 3_000, 100, 1_000, perishable=False)
        assert fresh.rejection_loss_per_quintal > dry.rejection_loss_per_quintal

    def test_rejection_risk_grows_with_distance(self):
        assert expected_rejection_pct(300, True) > expected_rejection_pct(20, True)


class TestStorageCost:
    def test_no_cost_for_zero_days(self):
        assert storage_cost_per_quintal(0) == 0

    def test_cost_accumulates_with_days(self):
        assert storage_cost_per_quintal(20) > storage_cost_per_quintal(5)

    def test_cold_storage_costs_more(self):
        assert storage_cost_per_quintal(10, cold_storage=True) > storage_cost_per_quintal(10)
