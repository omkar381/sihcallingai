"""
Offers, payments, disputes and the audit trail.

These tests guard money. The invariants that matter: a lot cannot be sold
twice, a payment cannot skip escrow, a dispute freezes the funds, and the
audit trail can prove all of it afterwards.
"""

from __future__ import annotations

import pytest

from app.trade import audit, buyers, disputes, lots, offers, payments


@pytest.fixture
def sale(verified_buyer):
    """A published lot with one open offer on it."""
    lot = lots.create_lot(
        "Onion", 3_000, farmer_phone="+91900000001", grade="FAQ",
        pickup_district="Kalaburagi", expected_price=3000, publish=True,
    )
    offer = offers.place_offer(
        lot["id"], verified_buyer["id"], price_per_quintal=3100, payment_terms_days=5
    )
    return {"lot": lot, "offer": offer, "buyer": verified_buyer}


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

class TestOffers:
    def test_offer_moves_lot_to_offer_received(self, sale):
        assert lots.get_lot(sale["lot"]["id"])["status"] == lots.LotStatus.OFFER_RECEIVED

    def test_unverified_buyer_cannot_bid(self, trade_db):
        buyer = buyers.create_buyer("Unverified Co", buyers.BuyerType.WHOLESALER)
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001", publish=True)
        with pytest.raises(offers.OfferError, match="verified"):
            offers.place_offer(lot["id"], buyer["id"], 3000)

    def test_offer_cannot_exceed_lot_size(self, verified_buyer):
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001", publish=True)
        with pytest.raises(offers.OfferError, match="between 0 and the lot size"):
            offers.place_offer(lot["id"], verified_buyer["id"], 3000, quantity_kg=5_000)

    def test_cannot_bid_on_a_draft_lot(self, verified_buyer):
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001")
        with pytest.raises(offers.OfferError, match="not open for offers"):
            offers.place_offer(lot["id"], verified_buyer["id"], 3000)

    def test_gross_value_is_computed_from_price_and_weight(self, sale):
        # 3000 kg = 30 quintal at 3100 = 93,000
        assert sale["offer"]["gross_value"] == pytest.approx(93_000.0)


class TestAcceptance:
    def test_acceptance_commits_the_sale(self, sale):
        result = offers.accept_offer(sale["offer"]["id"], actor="+91900000001")
        assert result["offer"]["status"] == offers.OfferStatus.ACCEPTED
        assert result["lot"]["status"] == lots.LotStatus.ACCEPTED
        assert result["payment"]["status"] == payments.PaymentStatus.PENDING

    def test_payment_amount_matches_the_offer(self, sale):
        result = offers.accept_offer(sale["offer"]["id"], actor="+91900000001")
        assert result["payment"]["amount_paise"] == 9_300_000   # 93,000 rupees
        assert result["payment"]["amount_rupees"] == 93_000.0

    def test_a_lot_cannot_be_sold_twice(self, sale, verified_buyer):
        second = offers.place_offer(sale["lot"]["id"], verified_buyer["id"], 3200)
        offers.accept_offer(sale["offer"]["id"], actor="+91900000001")

        # The competing offer must be dead, not merely losing.
        assert offers.get_offer(second["id"])["status"] == offers.OfferStatus.SUPERSEDED
        with pytest.raises(offers.OfferError):
            offers.accept_offer(second["id"], actor="+91900000001")

    def test_accepting_twice_is_refused(self, sale):
        offers.accept_offer(sale["offer"]["id"], actor="+91900000001")
        with pytest.raises(offers.OfferError, match="cannot be accepted"):
            offers.accept_offer(sale["offer"]["id"], actor="+91900000001")

    def test_acceptance_draws_down_buyer_demand(self, verified_buyer):
        demand = buyers.post_demand(verified_buyer["id"], "Onion", 10_000, price_offered=3100)
        lot = lots.create_lot("Onion", 3_000, farmer_phone="+91900000001", publish=True)
        offer = offers.place_offer(
            lot["id"], verified_buyer["id"], 3100, demand_id=demand["id"]
        )
        offers.accept_offer(offer["id"], actor="+91900000001")
        assert buyers.get_demand(demand["id"])["quantity_remaining_kg"] == 7_000

    def test_expired_offer_cannot_be_accepted(self, verified_buyer):
        lot = lots.create_lot("Onion", 1_000, farmer_phone="+91900000001", publish=True)
        offer = offers.place_offer(
            lot["id"], verified_buyer["id"], 3000, expires_in_hours=0
        )
        with pytest.raises(offers.OfferError, match="expired"):
            offers.accept_offer(offer["id"], actor="+91900000001")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@pytest.fixture
def accepted(sale):
    result = offers.accept_offer(sale["offer"]["id"], actor="+91900000001")
    return {**sale, "payment": result["payment"]}


class TestPaymentLifecycle:
    def test_money_is_stored_in_integer_paise(self, accepted):
        assert isinstance(accepted["payment"]["amount_paise"], int)

    def test_payment_cannot_skip_escrow(self, accepted):
        with pytest.raises(payments.PaymentError, match="Cannot move payment"):
            payments.release(accepted["payment"]["id"], actor="system")

    def test_full_happy_path(self, accepted):
        pid = accepted["payment"]["id"]
        lot_id = accepted["lot"]["id"]

        payments.initiate(pid, "UPI", actor=accepted["buyer"]["id"])
        payments.mark_escrowed(pid, utr_ref="UTR123", actor="psp")
        assert payments.get_payment(pid)["status"] == payments.PaymentStatus.ESCROW

        for status in ("LOGISTICS_PENDING", "IN_TRANSIT", "DELIVERED"):
            lots.transition(lot_id, status, actor="system")

        payments.confirm_delivery(pid, actor=accepted["buyer"]["id"])
        payments.release(pid, actor="system")

        assert payments.get_payment(pid)["status"] == payments.PaymentStatus.RELEASED
        assert lots.get_lot(lot_id)["status"] == lots.LotStatus.PAID

    def test_lot_and_payment_stay_in_step(self, accepted):
        pid = accepted["payment"]["id"]
        lot_id = accepted["lot"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")
        for status in ("LOGISTICS_PENDING", "IN_TRANSIT", "DELIVERED"):
            lots.transition(lot_id, status, actor="system")

        payments.confirm_delivery(pid, actor="b")
        # Confirming delivery must advance the lot too, or the two state
        # machines diverge and the audit trail stops making sense.
        assert lots.get_lot(lot_id)["status"] == lots.LotStatus.PAYMENT_PENDING

    def test_release_is_idempotent(self, accepted):
        pid = accepted["payment"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")
        payments.confirm_delivery(pid, actor="b")
        payments.release(pid, actor="system")
        # A retried payment-provider webhook must not explode.
        again = payments.release(pid, actor="system")
        assert again["status"] == payments.PaymentStatus.RELEASED

    def test_every_transition_is_recorded(self, accepted):
        pid = accepted["payment"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")
        history = payments.payment_history(pid)
        assert [h["to_status"] for h in history] == ["INITIATED", "ESCROW"]

    def test_release_updates_buyer_trust(self, accepted):
        pid = accepted["payment"]["id"]
        before = buyers.get_buyer(accepted["buyer"]["id"])["trust"]["total_transactions"]

        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")
        payments.confirm_delivery(pid, actor="b")
        payments.release(pid, actor="system")

        after = buyers.get_buyer(accepted["buyer"]["id"])["trust"]["total_transactions"]
        assert after == before + 1

    def test_failed_payment_can_be_retried(self, accepted):
        pid = accepted["payment"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_failed(pid, "insufficient funds", actor="psp")
        assert payments.get_payment(pid)["status"] == payments.PaymentStatus.FAILED
        payments.initiate(pid, "NEFT", actor="b")
        assert payments.get_payment(pid)["status"] == payments.PaymentStatus.INITIATED


class TestSettlement:
    def test_settlement_summary_reconciles(self, accepted):
        summary = payments.settlement_summary(accepted["lot"]["id"])
        assert summary["total_paise"] == accepted["payment"]["amount_paise"]
        assert summary["reconciles"] is True

    def test_rupee_conversion_round_trips(self):
        assert payments.paise_to_rupees(payments.rupees_to_paise(1234.56)) == 1234.56


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

class TestDisputes:
    def test_opening_a_dispute_freezes_the_payment(self, accepted):
        pid = accepted["payment"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")

        disputes.open_dispute(
            accepted["lot"]["id"], raised_by="+91900000001", raised_by_role="farmer",
            category=disputes.DisputeCategory.PAYMENT,
            description="Buyer has not released payment after delivery",
            payment_id=pid, against_party=accepted["buyer"]["id"],
        )
        assert payments.get_payment(pid)["status"] == payments.PaymentStatus.DISPUTED

    def test_dispute_requires_a_description(self, accepted):
        with pytest.raises(disputes.DisputeError, match="needs a description"):
            disputes.open_dispute(
                accepted["lot"]["id"], "+91900000001", "farmer",
                disputes.DisputeCategory.QUALITY, "   ",
            )

    def test_unknown_category_is_refused(self, accepted):
        with pytest.raises(disputes.DisputeError, match="Unknown dispute category"):
            disputes.open_dispute(
                accepted["lot"]["id"], "+91900000001", "farmer", "NONSENSE", "text",
            )

    def test_resolution_cannot_both_release_and_refund(self, accepted):
        dispute = disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.QUALITY, "Grade was disputed",
        )
        with pytest.raises(disputes.DisputeError, match="both release and refund"):
            disputes.resolve(
                dispute["id"], "resolved", release_payment=True, refund_payment=True
            )

    def test_resolution_needs_an_explanation(self, accepted):
        dispute = disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.QUALITY, "Grade was disputed",
        )
        with pytest.raises(disputes.DisputeError, match="needs an explanation"):
            disputes.resolve(dispute["id"], "  ")

    def test_dispute_workflow_is_tracked(self, accepted):
        dispute = disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.QUALITY, "Moisture was higher than declared",
        )
        disputes.start_review(dispute["id"], actor="admin")
        disputes.request_information(dispute["id"], "Please send photos", actor="admin")
        resolved = disputes.resolve(
            dispute["id"], "Price adjusted by 3% by mutual agreement", actor="admin"
        )
        assert resolved["status"] == disputes.DisputeStatus.RESOLVED
        assert resolved["resolution"]
        assert [h["to_status"] for h in disputes.dispute_history(dispute["id"])] == [
            "OPEN", "UNDER_REVIEW", "REQUESTED_INFORMATION", "RESOLVED",
        ]

    def test_resolved_dispute_cannot_be_reopened(self, accepted):
        dispute = disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.QUALITY, "Grade dispute",
        )
        disputes.resolve(dispute["id"], "settled")
        with pytest.raises(disputes.DisputeError, match="Cannot move dispute"):
            disputes.start_review(dispute["id"])

    def test_dispute_counts_against_the_buyer(self, accepted):
        dispute = disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.PAYMENT, "Late payment",
            against_party=accepted["buyer"]["id"],
        )
        disputes.resolve(dispute["id"], "Buyer paid late; warning issued")
        assert buyers.get_buyer(accepted["buyer"]["id"])["trust"]["disputes"] == 1

    def test_statistics_summarise_health(self, accepted):
        disputes.open_dispute(
            accepted["lot"]["id"], "+91900000001", "farmer",
            disputes.DisputeCategory.QUALITY, "Grade dispute",
        )
        stats = disputes.dispute_statistics()
        assert stats["total"] >= 1
        assert stats["open"] >= 1
        assert "QUALITY" in stats["by_category"]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_the_whole_sale_is_reconstructable(self, accepted):
        pid = accepted["payment"]["id"]
        payments.initiate(pid, "UPI", actor="b")
        payments.mark_escrowed(pid, actor="psp")

        lot_events = audit.history(audit.EntityType.LOT, accepted["lot"]["id"])
        actions = [e.action for e in lot_events]
        assert audit.Action.CREATED in actions
        assert audit.Action.STATUS_CHANGED in actions

        pay_events = audit.history(audit.EntityType.PAYMENT, pid)
        assert len(pay_events) >= 3

    def test_events_carry_before_and_after_values(self, accepted):
        events = audit.history(audit.EntityType.LOT, accepted["lot"]["id"])
        changes = [e for e in events if e.action == audit.Action.STATUS_CHANGED]
        assert changes
        assert changes[0].old_value["status"]
        assert changes[0].new_value["status"]

    def test_sequence_is_monotonic(self, accepted):
        events = audit.recent(limit=100)
        sequences = [e.sequence for e in events]
        assert sequences == sorted(sequences, reverse=True)

    def test_integrity_check_passes_on_a_clean_trail(self, accepted):
        integrity = audit.verify_integrity()
        assert integrity["intact"] is True
        assert integrity["missing_sequences"] == 0
        assert integrity["duplicate_sequences"] == 0

    def test_integrity_check_detects_a_deleted_event(self, accepted):
        from app.services.db import DB_LOCK, get_connection

        # Simulate tampering: remove an event from the middle of the trail.
        with DB_LOCK:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT id FROM audit_events ORDER BY sequence LIMIT 1 OFFSET 1"
                ).fetchone()
                conn.execute("DELETE FROM audit_events WHERE id = ?", (row["id"],))
                conn.commit()
            finally:
                conn.close()

        integrity = audit.verify_integrity()
        assert integrity["intact"] is False
        assert integrity["missing_sequences"] >= 1
