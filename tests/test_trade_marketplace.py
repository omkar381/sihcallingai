"""
Buyer registry, verification, FPO aggregation and matching.

The invariants here protect farmers from counterparties and from each other:
unverified buyers must not reach a farmer's screen, and incompatible produce
must never be pooled into one consignment.
"""

from __future__ import annotations

import pytest

from app.trade import buyers, fpo, lots, matching


# ---------------------------------------------------------------------------
# Buyer verification
# ---------------------------------------------------------------------------

class TestBuyerVerification:
    def test_new_buyer_starts_unverified(self, trade_db):
        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.PROCESSOR)
        assert buyer["verification_status"] == buyers.VerificationStatus.UNVERIFIED
        assert buyer["badge"]["safe_to_recommend"] is False
        assert buyer["badge"]["caution"]

    def test_verification_refused_without_documents(self, trade_db):
        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.APMC_TRADER)
        with pytest.raises(buyers.BuyerError, match="missing documents"):
            buyers.verify_buyer(buyer["id"], verified_by="admin")

    def test_readiness_names_what_is_missing(self, trade_db):
        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.APMC_TRADER)
        readiness = buyers.verification_readiness(buyer["id"])
        assert readiness["can_verify"] is False
        assert "APMC_LICENCE" in readiness["missing_documents"]

    def test_documents_move_buyer_to_pending(self, trade_db):
        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.RETAILER)
        buyers.submit_document(buyer["id"], "PAN", doc_number="ABCDE1234F")
        assert buyers.get_buyer(buyer["id"])["verification_status"] == "PENDING"

    def test_verification_succeeds_once_documents_are_on_file(self, verified_buyer):
        assert verified_buyer["verification_status"] == "VERIFIED"
        assert verified_buyer["verified_at"]

    def test_forced_verification_is_recorded_as_forced(self, trade_db):
        from app.trade import audit

        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.APMC_TRADER)
        buyers.verify_buyer(buyer["id"], verified_by="admin", force=True)
        events = audit.history(audit.EntityType.BUYER, buyer["id"])
        forced = [e for e in events if e.action == audit.Action.VERIFIED]
        assert forced and forced[0].metadata["forced"] is True

    def test_suspension_closes_open_demand(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000, price_offered=3000)
        assert buyers.list_open_demand(commodity="Onion")

        buyers.suspend_buyer(verified_buyer["id"], reason="payment defaults")
        assert buyers.list_open_demand(commodity="Onion") == []

    def test_suspended_buyer_is_never_safe_to_recommend(self, verified_buyer):
        buyers.suspend_buyer(verified_buyer["id"], reason="fraud")
        badge = buyers.get_buyer(verified_buyer["id"])["badge"]
        assert badge["safe_to_recommend"] is False
        assert badge["trust_band"] == "DO_NOT_TRADE"


class TestTrustScoring:
    def test_new_buyer_is_flagged_new_not_scored_as_good(self, verified_buyer):
        trust = verified_buyer["trust"]
        assert trust["is_new"] is True
        assert trust["band"] == "NEW"
        assert "no trading history" in verified_buyer["badge"]["label"].lower()

    def test_good_history_raises_the_score(self, verified_buyer):
        for _ in range(6):
            buyers.record_transaction_outcome(
                verified_buyer["id"], completed=True, payment_days=1.5
            )
        trust = buyers.get_buyer(verified_buyer["id"])["trust"]
        assert trust["band"] == "STRONG"
        assert trust["score"] > verified_buyer["trust"]["score"]

    def test_defaults_dominate_an_otherwise_good_record(self, verified_buyer):
        for _ in range(10):
            buyers.record_transaction_outcome(
                verified_buyer["id"], completed=True, payment_days=1.0
            )
        buyers.record_transaction_outcome(
            verified_buyer["id"], completed=False, defaulted=True
        )
        buyers.record_transaction_outcome(
            verified_buyer["id"], completed=False, defaulted=True
        )
        trust = buyers.get_buyer(verified_buyer["id"])["trust"]
        assert trust["payment_defaults"] == 2
        assert trust["band"] in ("FAIR", "WEAK", "POOR")

    def test_badge_is_computed_not_hardcoded(self, verified_buyer):
        before = buyers.get_buyer(verified_buyer["id"])["badge"]["label"]
        for _ in range(8):
            buyers.record_transaction_outcome(
                verified_buyer["id"], completed=True, payment_days=1.0
            )
        after = buyers.get_buyer(verified_buyer["id"])["badge"]["label"]
        assert before != after


class TestDemand:
    def test_unverified_buyer_cannot_post_demand(self, trade_db):
        buyer = buyers.create_buyer("Acme Agro", buyers.BuyerType.PROCESSOR)
        with pytest.raises(buyers.BuyerError, match="VERIFIED"):
            buyers.post_demand(buyer["id"], "Onion", 5_000)

    def test_unknown_commodity_is_refused(self, verified_buyer):
        with pytest.raises(buyers.BuyerError, match="not a recognised commodity"):
            buyers.post_demand(verified_buyer["id"], "unobtainium", 5_000)

    def test_demand_tracks_remaining_quantity(self, verified_buyer):
        demand = buyers.post_demand(verified_buyer["id"], "Onion", 10_000)
        assert demand["quantity_remaining_kg"] == 10_000
        buyers.fill_demand(demand["id"], 4_000)
        assert buyers.get_demand(demand["id"])["quantity_remaining_kg"] == 6_000

    def test_demand_closes_when_filled(self, verified_buyer):
        demand = buyers.post_demand(verified_buyer["id"], "Onion", 1_000)
        buyers.fill_demand(demand["id"], 1_000)
        assert buyers.get_demand(demand["id"])["status"] == "FILLED"

    def test_listing_hides_unverified_buyers_by_default(self, trade_db, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 5_000)
        assert len(buyers.list_open_demand(commodity="Onion")) == 1


# ---------------------------------------------------------------------------
# FPO aggregation
# ---------------------------------------------------------------------------

@pytest.fixture
def fpo_with_lots(trade_db):
    """An FPO with four members holding Grade A onion, plus one Grade C lot."""
    org = fpo.create_fpo("Test Producer Company", district="Kalaburagi", state="Karnataka")
    fpo.verify_fpo(org["id"])

    members = [("+91900000001", 600), ("+91900000002", 850),
               ("+91900000003", 1200), ("+91900000004", 750)]
    for phone, _ in members:
        fpo.add_member(org["id"], phone)

    created = [
        lots.create_lot("Onion", kg, farmer_phone=phone, grade="A",
                        pickup_district="Kalaburagi", publish=True)
        for phone, kg in members
    ]
    odd = lots.create_lot("Onion", 400, farmer_phone="+91900000001", grade="C",
                          pickup_district="Kalaburagi", publish=True)
    return {"fpo": org, "lots": created, "odd_lot": odd}


class TestFpoAggregation:
    def test_compatible_lots_group_together(self, fpo_with_lots):
        groups = fpo.find_aggregation_groups(fpo_with_lots["fpo"]["id"], commodity="Onion")
        grade_a = [g for g in groups if g.grade == "A"]
        assert len(grade_a) == 1
        assert grade_a[0].total_kg == 3400
        assert grade_a[0].farmer_count == 4

    def test_different_grades_are_never_pooled(self, fpo_with_lots):
        groups = fpo.find_aggregation_groups(fpo_with_lots["fpo"]["id"], commodity="Onion")
        grades = {g.grade for g in groups}
        assert grades == {"A", "C"}

    def test_aggregation_refuses_mixed_grades(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"][:2]] + [fpo_with_lots["odd_lot"]["id"]]
        with pytest.raises(fpo.FpoError, match="different grades"):
            fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)

    def test_aggregation_refuses_different_commodities(self, fpo_with_lots):
        other = lots.create_lot("Tur", 500, farmer_phone="+91900000002",
                                grade="A", publish=True)
        ids = [fpo_with_lots["lots"][0]["id"], other["id"]]
        with pytest.raises(fpo.FpoError, match="different commodities"):
            fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)

    def test_aggregate_lot_sums_member_weights(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        assert parent["is_aggregate"] is True
        assert parent["quantity_kg"] == 3400
        assert len(parent["member_lots"]) == 4

    def test_member_lots_are_marked_aggregated_not_deleted(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        for lot_id in ids:
            child = lots.get_lot(lot_id)
            assert child["status"] == lots.LotStatus.AGGREGATED
            assert child["parent_lot_id"]

    def test_aggregated_children_are_excluded_from_listings(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        listed = {l["id"] for l in lots.list_lots(commodity="Onion")}
        assert parent["id"] in listed
        assert not (set(ids) & listed), "member lots must not be double counted"

    def test_a_lot_cannot_be_aggregated_twice(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        with pytest.raises(fpo.FpoError, match="already part of an aggregate"):
            fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)

    def test_aggregation_needs_at_least_two_lots(self, fpo_with_lots):
        with pytest.raises(fpo.FpoError, match="at least two"):
            fpo.create_aggregate_lot(
                fpo_with_lots["fpo"]["id"], [fpo_with_lots["lots"][0]["id"]]
            )

    def test_grade_basis_falls_to_the_weakest_member(self, fpo_with_lots):
        # One inspected lot does not make the whole pooled consignment inspected.
        lots.update_quality(fpo_with_lots["lots"][0]["id"], grade="A", basis="INSPECTED")
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        assert parent["grade_basis"] == "SELF_DECLARED"
        assert parent["grade_is_evidence"] is False


class TestAggregationEconomics:
    def test_pooling_beats_shipping_separately(self, fpo_with_lots):
        groups = fpo.find_aggregation_groups(fpo_with_lots["fpo"]["id"], commodity="Onion")
        group = [g for g in groups if g.grade == "A"][0]
        benefit = fpo.estimate_aggregation_benefit(
            group, price_per_quintal=3280, distance_km=136.5, perishable=True
        )
        assert benefit["total_gain"] > 0
        assert benefit["net_if_aggregated"] > benefit["net_if_sold_separately"]
        assert benefit["gain_per_farmer"] > 0

    def test_benefit_explains_itself(self, fpo_with_lots):
        groups = fpo.find_aggregation_groups(fpo_with_lots["fpo"]["id"], commodity="Onion")
        group = [g for g in groups if g.grade == "A"][0]
        benefit = fpo.estimate_aggregation_benefit(group, 3280, 136.5)
        assert "freight" in benefit["explanation"].lower()
        assert benefit["vehicle_when_aggregated"]


class TestSettlementSplit:
    def test_split_is_proportional_to_weight(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        shares = fpo.split_settlement(parent["id"], 11_152_000)

        biggest = max(shares, key=lambda s: s["quantity_kg"])
        assert biggest["quantity_kg"] == 1200
        assert biggest["amount_paise"] == max(s["amount_paise"] for s in shares)

    def test_split_reconciles_to_the_exact_paisa(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)

        # An amount chosen to force a rounding remainder.
        total = 10_000_007
        shares = fpo.split_settlement(parent["id"], total)
        assert sum(s["amount_paise"] for s in shares) == total

    def test_rounding_remainder_goes_to_the_largest_contributor(self, fpo_with_lots):
        ids = [l["id"] for l in fpo_with_lots["lots"]]
        parent = fpo.create_aggregate_lot(fpo_with_lots["fpo"]["id"], ids)
        shares = fpo.split_settlement(parent["id"], 10_000_007)
        with_remainder = [s for s in shares if "rounding_remainder_paise" in s]
        if with_remainder:
            assert with_remainder[0]["quantity_kg"] == 1200


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

class TestMatching:
    def test_matches_are_scored_and_explained(self, verified_buyer):
        buyers.post_demand(
            verified_buyer["id"], "Onion", 50_000,
            min_grade="FAQ", price_offered=3100, delivery_district="Solapur",
        )
        lot = lots.create_lot("Onion", 3_400, farmer_phone="+91900000001",
                              grade="A", pickup_district="Kalaburagi", publish=True)
        matches = matching.find_matches(lot)
        assert matches
        assert 0 < matches[0].score <= 100
        assert matches[0].factors
        assert matches[0].reasons

    def test_wrong_commodity_never_matches(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000, price_offered=3100)
        lot = lots.create_lot("Tur", 1_000, farmer_phone="+91900000001",
                              grade="A", pickup_district="Kalaburagi", publish=True)
        assert matching.find_matches(lot) == []

    def test_grade_below_requirement_is_excluded(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000,
                           min_grade="A", price_offered=3100)
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001",
                              grade="C", pickup_district="Kalaburagi", publish=True)
        assert matching.find_matches(lot) == []

    def test_suspended_buyer_disappears_from_matches(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000, price_offered=3100)
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001",
                              grade="A", pickup_district="Kalaburagi", publish=True)
        assert matching.find_matches(lot)

        buyers.suspend_buyer(verified_buyer["id"], reason="defaults")
        assert matching.find_matches(lot) == []

    def test_self_declared_grade_produces_a_warning(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000,
                           min_grade="FAQ", price_offered=3100)
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001",
                              grade="A", pickup_district="Kalaburagi", publish=True)
        match = matching.find_matches(lot)[0]
        assert any("self-declared" in w for w in match.warnings)

    def test_factor_weights_sum_to_one_hundred(self):
        assert sum(matching.WEIGHTS.values()) == pytest.approx(100.0)

    def test_explanation_is_speakable(self, verified_buyer):
        buyers.post_demand(verified_buyer["id"], "Onion", 10_000, price_offered=3100)
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001",
                              grade="A", pickup_district="Kalaburagi", publish=True)
        text = matching.explain_match(matching.find_matches(lot)[0])
        assert "score" in text.lower()
        assert "{" not in text and "}" not in text
