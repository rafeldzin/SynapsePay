"""A2: FakePayPal behavior (UAT 1.4) and the seam contract (UAT-PP-10)."""
import pytest

from app.contracts import BatchView, CreateResult, ItemView
from app.paypal import http, reads, writes
from app.paypal.http import PayPalOutcomeUnknown, PayPalRejected

CORRECT, TYPO = "dewi.lestari@example.test", "dewi.lestary@example.test"


def _create(sender="SP-run-m1-A1", receiver=CORRECT, amount=30000):
    return writes.create_payout(sender_batch_id=sender, receiver_email=receiver, amount_cents=amount,
                                currency="USD", note="Milestone m1")


def _item_id(fake, batch_id):
    return fake.batches[batch_id]["items"][0]["payout_item_id"]


def test_uat_pp_10_seam_returns_the_same_types_through_reads_and_writes(fake_paypal):
    created = _create()
    assert isinstance(created, CreateResult) and created.outcome == "CREATED"
    batch = reads.get_batch(created.payout_batch_id)
    assert isinstance(batch, BatchView) and isinstance(batch.items[0], ItemView)
    assert isinstance(reads.get_item(batch.items[0].payout_item_id), ItemView)
    fake_paypal.advance()
    unclaimed = _create("SP-run-m1-A2", TYPO)
    fake_paypal.advance()
    assert isinstance(writes.cancel_item(_item_id(fake_paypal, unclaimed.payout_batch_id)), ItemView)


def test_uat_pp_10_seam_raises_the_same_exceptions_as_the_live_client(fake_paypal):
    fake_paypal.fail_next("unknown_before_send")
    with pytest.raises(PayPalOutcomeUnknown):
        _create()
    fake_paypal.fail_next("reject_400")
    with pytest.raises(PayPalRejected) as err:
        _create()
    assert err.value.name and err.value.debug_id and err.value.status_code == 400
    created = _create("SP-run-m1-A9")
    fake_paypal.advance()
    with pytest.raises(PayPalRejected) as err:  # item is SUCCESS, not UNCLAIMED
        writes.cancel_item(_item_id(fake_paypal, created.payout_batch_id))
    assert err.value.name == "ITEM_INCORRECT_STATUS"
    fake_paypal.fail_next("read_error")
    with pytest.raises(PayPalOutcomeUnknown):
        reads.get_batch(created.payout_batch_id)


def test_uat_pp_10_fake_payloads_parse_unchanged_with_the_live_parser(fake_paypal):
    created = _create()
    fake_paypal.advance()
    batch = reads.get_batch(created.payout_batch_id)
    assert http.parse_batch(batch.raw) == batch
    assert http.parse_item(batch.items[0].raw) == batch.items[0]


def test_a2_new_batch_is_pending_with_one_item_and_no_final_status(fake_paypal):
    created = _create()
    batch = reads.get_batch(created.payout_batch_id)
    assert (created.batch_status, batch.batch_status) == ("PENDING", "PENDING")
    assert len(batch.items) == 1 and batch.items[0].transaction_status == "PENDING"
    assert (batch.items[0].amount_cents, batch.items[0].receiver) == (30000, CORRECT)
    assert batch.sender_batch_id == "SP-run-m1-A1"


def test_a2_advance_settles_a_registered_receiver_to_success_and_others_to_unclaimed(fake_paypal):
    ok, bad = _create("S1", CORRECT), _create("S2", TYPO)
    fake_paypal.advance()
    assert reads.get_batch(ok.payout_batch_id).items[0].transaction_status == "SUCCESS"
    bad_batch = reads.get_batch(bad.payout_batch_id)
    assert bad_batch.batch_status == "SUCCESS"  # a SUCCESS batch can hold an UNCLAIMED item (PRD 8.4)
    assert bad_batch.items[0].transaction_status == "UNCLAIMED"
    assert bad_batch.items[0].errors == {"name": "RECEIVER_UNREGISTERED"}


def test_a2_duplicate_reject_mode_returns_original_found_and_never_a_second_batch(fake_paypal):
    first = _create()
    again = _create()
    assert (again.outcome, again.payout_batch_id) == ("ORIGINAL_FOUND", first.payout_batch_id)
    assert len(fake_paypal.batches) == 1


def test_a2_duplicate_replay_mode_returns_created_with_the_original_id(fake_paypal):
    fake_paypal.duplicate_mode = "replay"
    first = _create()
    again = _create()
    assert (again.outcome, again.payout_batch_id) == ("CREATED", first.payout_batch_id)
    assert len(fake_paypal.batches) == 1


def test_a2_the_duplicate_rejection_body_is_parsed_by_the_live_duplicate_rule(fake_paypal):
    first = _create()
    body = _create().raw
    assert http.duplicate_original_batch_id(body) == first.payout_batch_id


def test_a2_cancel_returns_an_unclaimed_item_and_rejects_everything_else(fake_paypal):
    created = _create("S2", TYPO)
    item_id = _item_id(fake_paypal, created.payout_batch_id)
    with pytest.raises(PayPalRejected) as err:  # batch still PENDING, as at the real sandbox
        writes.cancel_item(item_id)
    assert err.value.name == "BATCH_NOT_COMPLETED"
    fake_paypal.advance()
    assert writes.cancel_item(item_id).transaction_status == "RETURNED"
    with pytest.raises(PayPalRejected) as err:
        writes.cancel_item(item_id)
    assert err.value.name == "ITEM_INCORRECT_STATUS"


def test_a2_claim_turns_an_unclaimed_item_into_success(fake_paypal):
    created = _create("S2", TYPO)
    fake_paypal.advance()
    item_id = _item_id(fake_paypal, created.payout_batch_id)
    fake_paypal.claim(item_id)
    assert reads.get_item(item_id).transaction_status == "SUCCESS"
    with pytest.raises(PayPalRejected):
        writes.cancel_item(item_id)


def test_a2_set_item_status_drives_any_status(fake_paypal):
    created = _create()
    item_id = _item_id(fake_paypal, created.payout_batch_id)
    fake_paypal.set_item_status(item_id, "ONHOLD")
    assert reads.get_item(item_id).transaction_status == "ONHOLD"


def test_a2_unknown_after_send_creates_the_payout_but_loses_the_response(fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    with pytest.raises(PayPalOutcomeUnknown):
        _create()
    assert len(fake_paypal.batches) == 1
    assert _create().outcome == "ORIGINAL_FOUND" and len(fake_paypal.batches) == 1


def test_a2_unknown_before_send_creates_nothing(fake_paypal):
    fake_paypal.fail_next("unknown_before_send")
    with pytest.raises(PayPalOutcomeUnknown):
        _create()
    assert fake_paypal.batches == {}
    assert _create().outcome == "CREATED"  # the next attempt is a normal first send


def test_a2_cancel_can_lose_its_response_after_the_item_was_returned(fake_paypal):
    created = _create("S2", TYPO)
    fake_paypal.advance()
    item_id = _item_id(fake_paypal, created.payout_batch_id)
    fake_paypal.fail_next("unknown_after_send", on="cancel_item")
    with pytest.raises(PayPalOutcomeUnknown):
        writes.cancel_item(item_id)
    assert reads.get_item(item_id).transaction_status == "RETURNED"


def test_a2_a_fault_is_consumed_by_exactly_one_call(fake_paypal):
    fake_paypal.fail_next("read_error")
    with pytest.raises(PayPalOutcomeUnknown):
        reads.get_batch("x")
    with pytest.raises(PayPalRejected):  # a normal not-found now, not a read error
        reads.get_batch("x")


def test_a2_calls_log_every_call_and_write_calls_only_the_writes(fake_paypal):
    created = _create()
    reads.get_batch(created.payout_batch_id)
    fake_paypal.advance()
    assert [name for name, _ in fake_paypal.calls] == ["create_payout", "get_batch"]
    assert [name for name, _ in fake_paypal.write_calls] == ["create_payout"]
    assert fake_paypal.write_calls[0][1]["amount_cents"] == 30000


def test_a2_unknown_fault_kind_is_rejected(fake_paypal):
    with pytest.raises(ValueError):
        fake_paypal.fail_next("explode")
