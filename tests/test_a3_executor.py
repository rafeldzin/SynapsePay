"""A3.2: executor on FakePayPal. PAY and RETRY outcomes, write-ahead, events, recovery (PRD 10, 6.2)."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import db, events
from app.gate import executor
from app.paypal.fake import active_fake
from tests.conftest import TYPO_EMAIL
from tests.helpers_a import payout, verify

PAY = dict(approved_by="ops-lead", confirm=True, expected_amount_cents=30000)


@pytest.fixture
def conn(seed_a, fake_paypal):
    connection = db.connect()
    verify(connection)  # m1 verified PASS
    yield connection
    connection.close()


def types(conn, **filters):
    return [e["type"] for e in events.list_events(conn, **filters)]


def state_of(conn, table, row_id):
    return conn.execute(f"SELECT state FROM {table} WHERE id = ?", (row_id,)).fetchone()["state"]


def test_a3_pay_sends_one_create_from_database_values_and_ends_sent(conn, seed_a, fake_paypal):
    result = executor.pay("m1", **PAY)
    assert result.allowed and result.payout["state"] == "SENT"
    (name, kwargs), = fake_paypal.write_calls
    assert name == "create_payout"
    assert kwargs["amount_cents"] == 30000 and kwargs["currency"] == "USD"
    assert kwargs["receiver_email"] == TYPO_EMAIL  # scenario A: the contract's payee email
    assert kwargs["sender_batch_id"] == f"SP-{seed_a['run_id']}-m1-A1"
    assert result.payout["payout_batch_id"] == next(iter(fake_paypal.batches))
    assert state_of(conn, "milestones", "m1") == "PAYOUT_IN_FLIGHT"


def test_a3_pay_events_are_in_the_prd_order_with_paypal_ids(conn, fake_paypal):
    result = executor.pay("m1", **PAY)
    log = events.list_events(conn)
    order = [e["type"] for e in log]
    for earlier, later in [("GATE_EVALUATED", "PAYOUT_CREATING"), ("PAYOUT_CREATING", "PAYPAL_PAYOUT_CREATED"),
                           ("PAYPAL_PAYOUT_CREATED", "PAYOUT_STATE_CHANGED")]:
        assert order.index(earlier) < order.index(later)
    created = next(e for e in log if e["type"] == "PAYPAL_PAYOUT_CREATED")
    assert created["paypal_batch_id"] == result.payout["payout_batch_id"]
    assert created["payload"]["on_retry"] is False
    assert next(e for e in log if e["type"] == "GATE_EVALUATED")["actor"] == "human:ops-lead"
    assert {"MILESTONE_STATE_CHANGED"} <= set(order)


def test_a3_a_refusal_is_logged_and_writes_nothing_to_paypal(conn, fake_paypal):
    result = executor.pay("m1", approved_by="ops-lead", confirm=False, expected_amount_cents=30000)
    assert not result.allowed and result.payout is None
    assert fake_paypal.calls == []
    assert types(conn).count("GATE_EVALUATED") == 1
    assert conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0] == 0
    assert state_of(conn, "milestones", "m1") == "PASS"


def test_a3_override_is_recorded_and_then_pays(conn, fake_paypal):
    conn.execute("UPDATE milestones SET state = 'FAIL', verdict = 'FAIL' WHERE id = 'm1'")
    reason = "Client accepted the partial delivery by phone"
    result = executor.pay("m1", override_reason=reason, **PAY)
    assert result.allowed and len(fake_paypal.write_calls) == 1
    recorded = next(e for e in events.list_events(conn) if e["type"] == "OVERRIDE_RECORDED")
    assert recorded["payload"]["reason"] == reason and recorded["payload"]["approved_by"] == "ops-lead"
    row = conn.execute("SELECT override_reason, override_by FROM milestones WHERE id = 'm1'").fetchone()
    assert (row["override_reason"], row["override_by"]) == (reason, "ops-lead")


def test_a3_second_pay_is_refused_on_pay4_and_creates_nothing(conn, fake_paypal):
    executor.pay("m1", **PAY)
    again = executor.pay("m1", **PAY)
    assert not again.allowed
    assert [c.id for c in again.gate.checks if c.ok is False] == ["PAY-1", "PAY-4"]  # in flight and live
    assert len(fake_paypal.write_calls) == 1


def test_a3_ten_parallel_pays_create_exactly_one_payout(conn, fake_paypal):
    results, barrier = [], threading.Barrier(10)

    def worker():
        barrier.wait()
        results.append(executor.pay("m1", **PAY))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r.allowed for r in results) == 1 and len(results) == 10
    assert len(fake_paypal.write_calls) == 1 and len(fake_paypal.batches) == 1
    assert conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0] == 1


def test_a3_a_definite_rejection_fails_the_payout_and_frees_the_milestone(conn, fake_paypal):
    fake_paypal.fail_next("reject_400")
    result = executor.pay("m1", **PAY)
    assert result.allowed and result.payout["state"] == "FAILED"
    assert state_of(conn, "milestones", "m1") == "PASS"
    rejected = next(e for e in events.list_events(conn) if e["type"] == "PAYPAL_REJECTED")
    assert rejected["payload"]["name"] == "VALIDATION_ERROR" and rejected["payload"]["debug_id"]
    second = executor.pay("m1", **PAY)  # allowed again, attempt 2, new key
    assert second.allowed and second.payout["attempt"] == 2
    assert second.payout["sender_batch_id"].endswith("-A2")
    assert second.payout["state"] == "SENT"


@pytest.mark.parametrize("fault, batches", [("unknown_after_send", 1), ("unknown_before_send", 0)])
def test_a3_a_lost_response_leaves_the_payout_unknown_and_blocks_a_second_pay(conn, fake_paypal, fault, batches):
    fake_paypal.fail_next(fault)
    result = executor.pay("m1", **PAY)
    assert result.allowed and result.payout["state"] == "UNKNOWN"
    assert result.payout["payout_batch_id"] is None
    assert "PAYPAL_OUTCOME_UNKNOWN" in types(conn)
    assert state_of(conn, "milestones", "m1") == "PAYOUT_IN_FLIGHT"
    assert len(fake_paypal.batches) == batches
    again = executor.pay("m1", **PAY)
    assert not again.allowed and any(c.id == "PAY-4" and c.ok is False for c in again.gate.checks)


def test_a3_the_payout_row_is_committed_before_paypal_is_called(conn, fake_paypal, monkeypatch):
    seen = {}
    real = fake_paypal.create_payout

    def spy(**kwargs):
        other = db.connect()  # a separate connection sees only committed rows
        try:
            row = other.execute("SELECT state, sender_batch_id FROM payouts").fetchone()
            seen["row"] = dict(row) if row else None
        finally:
            other.close()
        return real(**kwargs)

    monkeypatch.setattr(fake_paypal, "create_payout", spy)
    executor.pay("m1", **PAY)
    assert seen["row"]["state"] == "CREATING"
    assert seen["row"]["sender_batch_id"].endswith("-m1-A1")


def test_a3_an_unexpected_duplicate_on_a_first_send_is_unknown_with_a_d4_case(conn, fake_paypal):
    run_id = conn.execute("SELECT value FROM meta WHERE key = 'run_id'").fetchone()["value"]
    fake_paypal.create_payout(sender_batch_id=f"SP-{run_id}-m1-A1", receiver_email="x@example.test",
                              amount_cents=30000, currency="USD", note="pre-existing")
    result = executor.pay("m1", **PAY)
    assert result.payout["state"] == "UNKNOWN" and result.payout["payout_batch_id"] == "FAKEB0001"
    case = conn.execute("SELECT trigger_rule, state FROM cases").fetchone()
    assert (case["trigger_rule"], case["state"]) == ("D4", "OPEN")
    assert "PAYPAL_ORIGINAL_FOUND" in types(conn)


def _unknown_payout(conn, fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    return executor.pay("m1", **PAY).payout


def test_a3_retry_reject_mode_finds_the_original_stays_unknown_and_opens_d4(conn, fake_paypal):
    po = _unknown_payout(conn, fake_paypal)
    result = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    (_, first), (_, second) = [c for c in fake_paypal.write_calls]
    assert second["sender_batch_id"] == first["sender_batch_id"]  # the same key
    assert result.payout["state"] == "UNKNOWN" and result.payout["payout_batch_id"] == "FAKEB0001"
    assert len(fake_paypal.batches) == 1
    assert result.case_id and conn.execute("SELECT trigger_rule FROM cases").fetchone()[0] == "D4"
    found = next(e for e in events.list_events(conn) if e["type"] == "PAYPAL_ORIGINAL_FOUND")
    assert found["payload"]["on_retry"] is True and found["paypal_batch_id"] == "FAKEB0001"


def test_a3_retry_replay_mode_returns_created_but_the_ledger_stays_unknown(conn, fake_paypal):
    fake_paypal.duplicate_mode = "replay"
    po = _unknown_payout(conn, fake_paypal)
    result = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    assert result.payout["state"] == "UNKNOWN" and result.payout["payout_batch_id"] == "FAKEB0001"
    assert len(fake_paypal.batches) == 1 and result.case_id
    created = [e for e in events.list_events(conn) if e["type"] == "PAYPAL_PAYOUT_CREATED"]
    assert created[-1]["payload"]["on_retry"] is True


def test_a3_retry_that_is_lost_again_has_no_case_and_stays_unknown(conn, fake_paypal):
    po = _unknown_payout(conn, fake_paypal)
    fake_paypal.fail_next("unknown_before_send")
    result = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    assert result.payout["state"] == "UNKNOWN" and result.case_id is None
    assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0


def test_a3_retry_with_a_definite_rejection_fails_the_payout_and_frees_the_milestone(conn, fake_paypal):
    fake_paypal.fail_next("unknown_before_send")
    po = executor.pay("m1", **PAY).payout
    fake_paypal.fail_next("reject_400")
    result = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    assert result.payout["state"] == "FAILED" and result.case_id is None
    assert state_of(conn, "milestones", "m1") == "PASS"


def test_a3_retry_is_refused_unless_the_payout_is_unknown(conn, fake_paypal):
    po = executor.pay("m1", **PAY).payout  # SENT
    result = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    assert not result.allowed and [c.id for c in result.gate.checks if c.ok is False] == ["RT-1"]
    assert len(fake_paypal.write_calls) == 1


def test_a3_a_second_retry_reuses_the_open_case(conn, fake_paypal):
    po = _unknown_payout(conn, fake_paypal)
    first = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    second = executor.retry(po["id"], approved_by="ops-lead", confirm=True)
    assert second.case_id == first.case_id
    assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1


def test_a3_two_resets_give_two_different_sender_batch_ids(seed_a, fake_paypal):
    keys = []
    for _ in range(2):
        conn = db.connect()
        db.reset_demo(conn, "A")
        verify(conn)
        keys.append(executor.pay("m1", **PAY).payout["sender_batch_id"])
        conn.close()
    assert keys[0] != keys[1] and all(k.endswith("-m1-A1") for k in keys)


def test_a3_recovery_turns_a_stale_creating_row_into_unknown(conn):
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payout(conn, "CREATING")
    conn.execute("UPDATE payouts SET created_at = ?", (old,))
    assert executor.recover_creating(conn) == 1
    assert state_of(conn, "payouts", "po_00000001") == "UNKNOWN"
    changed = [e for e in events.list_events(conn) if e["type"] == "PAYOUT_STATE_CHANGED"][-1]
    assert changed["payload"] == {"from": "CREATING", "to": "UNKNOWN", "cause": "startup_recovery"}


def test_a3_recovery_leaves_a_fresh_creating_row_alone(conn):
    payout(conn, "CREATING")
    conn.execute("UPDATE payouts SET created_at = ?",
                 (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    assert executor.recover_creating(conn) == 0
    assert state_of(conn, "payouts", "po_00000001") == "CREATING"
