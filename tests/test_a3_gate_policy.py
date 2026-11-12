"""A3.1: policy gate checks for PAY and RETRY (PRD 10). Pure functions over the database."""
import pytest

from app import db, events
from app.gate import policy
from tests.helpers_a import payout, verify

APPROVAL = dict(approved_by="ops-lead", confirm=True, expected_amount_cents=30000)


@pytest.fixture
def conn(seed_a):
    connection = db.connect()
    yield connection
    connection.close()


def failing(result):
    return [c.id for c in result.checks if c.ok is False]


def test_a3_reset_demo_seeds_run_id_and_one_reset_event(conn, seed_a):
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    assert meta["run_id"] == seed_a["run_id"] and len(meta["run_id"]) == 8
    assert meta["scenario"] == "A" and "armed_fault" not in meta
    listed = events.list_events(conn)
    assert [e["type"] for e in listed] == ["RESET"]
    assert listed[0]["payload"] == {"run_id": seed_a["run_id"], "scenario": "A"}


def test_a3_reset_demo_scenario_b_arms_the_timeout_fault(seed_b):
    conn = db.connect()
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    finally:
        conn.close()
    assert meta["armed_fault"] == "timeout_after_send"


def test_a3_pay_all_eight_checks_pass_for_a_verified_milestone(conn):
    verify(conn)
    result = policy.evaluate_pay(conn, "m1", **APPROVAL)
    assert [c.id for c in result.checks] == [f"PAY-{i}" for i in range(1, 9)]
    assert result.allowed and result.preview is False and failing(result) == []
    assert all(c.ok is True for c in result.checks)


def test_a3_pay_check_names_are_the_prd_names(conn):
    verify(conn)
    names = [c.name for c in policy.evaluate_pay(conn, "m1", **APPROVAL).checks]
    assert names == ["verdict_ok", "verdict_current", "amount_matches", "no_live_payout",
                     "no_open_case", "idempotency_key", "human_approval", "sandbox_only"]


@pytest.mark.parametrize("state", ["AWAITING_DELIVERABLE", "FAIL", "NEEDS_REVIEW"])
def test_a3_pay1_refuses_anything_but_pass(conn, state):
    if state != "AWAITING_DELIVERABLE":  # that one is the seeded state, nothing submitted yet
        verify(conn, state=state)
    assert "PAY-1" in failing(policy.evaluate_pay(conn, "m1", **APPROVAL))


def test_a3_pay1_accepts_fail_with_a_long_enough_override(conn):
    verify(conn, state="FAIL")
    ok = policy.evaluate_pay(conn, "m1", override_reason="x" * 25, **APPROVAL)
    assert ok.allowed
    short = policy.evaluate_pay(conn, "m1", override_reason="x" * 10, **APPROVAL)
    assert failing(short) == ["PAY-1"]
    padded = policy.evaluate_pay(conn, "m1", override_reason=" " * 30, **APPROVAL)  # whitespace is not a reason
    assert failing(padded) == ["PAY-1"]


def test_a3_pay1_refuses_a_milestone_already_in_flight_or_paid(conn):
    verify(conn)
    conn.execute("UPDATE milestones SET state = 'PAYOUT_IN_FLIGHT' WHERE id = 'm1'")
    assert "PAY-1" in failing(policy.evaluate_pay(conn, "m1", **APPROVAL))


def test_a3_pay2_refuses_a_verdict_older_than_the_newest_deliverable(conn):
    verify(conn, sha="old")
    conn.execute("INSERT INTO deliverables (milestone_id, sha256, csv_name, csv_bytes, report_text, "
                 "submitted_by, created_at) VALUES ('m1', 'new', 'x.csv', x'00', 'r', 'dewi', 't')")
    assert failing(policy.evaluate_pay(conn, "m1", **APPROVAL)) == ["PAY-2"]


def test_a3_pay2_refuses_when_nothing_was_verified(conn):
    assert "PAY-2" in failing(policy.evaluate_pay(conn, "m1", **APPROVAL))


@pytest.mark.parametrize("expected", [29999, 30001, None])
def test_a3_pay3_amount_must_match_the_milestone_to_the_cent(conn, expected):
    verify(conn)
    result = policy.evaluate_pay(conn, "m1", approved_by="ops-lead", confirm=True,
                                 expected_amount_cents=expected)
    assert failing(result) == ["PAY-3"]


@pytest.mark.parametrize("state", ["CREATING", "UNKNOWN", "SENT", "UNCLAIMED", "PAID"])
def test_a3_pay4_any_live_payout_blocks_and_the_detail_names_it(conn, state):
    verify(conn)
    payout(conn, state)
    result = policy.evaluate_pay(conn, "m1", **APPROVAL)
    assert "PAY-4" in failing(result)
    assert "po_00000001" in result.checks[3].detail


@pytest.mark.parametrize("state", ["RETURNED", "FAILED"])
def test_a3_pay4_a_dead_payout_does_not_block_and_the_next_attempt_is_2(conn, state):
    verify(conn)
    payout(conn, state)
    result = policy.evaluate_pay(conn, "m1", **APPROVAL)
    assert result.allowed
    assert policy.next_attempt(conn, "m1") == 2
    assert "-m1-A2" in result.checks[5].detail


def test_a3_pay4_a_live_payout_on_the_other_milestone_does_not_block(conn):
    verify(conn)
    payout(conn, "SENT", milestone_id="m2")
    assert policy.evaluate_pay(conn, "m1", **APPROVAL).allowed


@pytest.mark.parametrize("state", ["OPEN", "INVESTIGATED", "NEEDS_HUMAN", "AWAITING_REISSUE"])
def test_a3_pay5_an_unresolved_case_blocks(conn, state):
    verify(conn)
    payout(conn, "RETURNED")
    conn.execute("INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, opened_at, updated_at) "
                 "VALUES ('case_00000001', 'po_00000001', 'm1', 'D1', ?, 't', 't')", (state,))
    assert failing(policy.evaluate_pay(conn, "m1", **APPROVAL)) == ["PAY-5"]


def test_a3_pay5_a_resolved_case_does_not_block(conn):
    verify(conn)
    payout(conn, "RETURNED")
    conn.execute("INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, opened_at, updated_at) "
                 "VALUES ('case_00000001', 'po_00000001', 'm1', 'D1', 'RESOLVED', 't', 't')")
    assert policy.evaluate_pay(conn, "m1", **APPROVAL).allowed


def test_a3_pay6_refuses_a_key_that_is_already_in_payouts(conn):
    verify(conn)
    payout(conn, "RETURNED")
    # force the next computed key to collide with an existing row
    conn.execute("UPDATE payouts SET sender_batch_id = ?", (policy.sender_batch_id(policy._run_id(conn), "m1", 2),))
    assert failing(policy.evaluate_pay(conn, "m1", **APPROVAL)) == ["PAY-6"]


def test_a3_pay6_the_key_carries_run_id_milestone_and_attempt(conn, seed_a):
    verify(conn)
    detail = policy.evaluate_pay(conn, "m1", **APPROVAL).checks[5].detail
    assert f"SP-{seed_a['run_id']}-m1-A1" in detail


@pytest.mark.parametrize("approved_by, confirm", [("ops-lead", False), ("", True), ("   ", True), (None, True),
                                                  ("ops-lead", None)])
def test_a3_pay7_needs_a_named_approver_and_confirm_true(conn, approved_by, confirm):
    verify(conn)
    result = policy.evaluate_pay(conn, "m1", approved_by=approved_by, confirm=confirm,
                                 expected_amount_cents=30000)
    assert failing(result) == ["PAY-7"] and not result.allowed


def test_a3_pay8_refuses_any_host_but_the_sandbox(conn, monkeypatch):
    verify(conn)
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    assert failing(policy.evaluate_pay(conn, "m1", **APPROVAL)) == ["PAY-8"]


def test_a3_pay_preview_leaves_the_human_check_pending_and_allows(conn):
    verify(conn)
    result = policy.evaluate_pay(conn, "m1", expected_amount_cents=30000, preview=True)
    assert result.preview and result.allowed
    assert result.checks[6].id == "PAY-7" and result.checks[6].ok is None


def test_a3_pay_preview_still_blocks_on_a_failing_check(conn):
    result = policy.evaluate_pay(conn, "m1", expected_amount_cents=30000, preview=True)  # nothing verified
    assert not result.allowed and "PAY-1" in failing(result)


def test_a3_pay_does_not_write_anything(conn):
    verify(conn)
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    policy.evaluate_pay(conn, "m1", **APPROVAL)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_a3_unknown_ids_raise_lookup_error(conn):
    with pytest.raises(LookupError):
        policy.evaluate_pay(conn, "m9", **APPROVAL)
    with pytest.raises(LookupError):
        policy.evaluate_retry(conn, "po_nope", approved_by="a", confirm=True)


def test_a3_retry_passes_only_for_an_unknown_payout_with_approval(conn):
    payout(conn, "UNKNOWN")
    result = policy.evaluate_retry(conn, "po_00000001", approved_by="ops-lead", confirm=True)
    assert [c.id for c in result.checks] == ["RT-1", "RT-2", "RT-3", "RT-4"]
    assert [c.name for c in result.checks] == ["state_unknown", "same_key", "human_approval", "sandbox_only"]
    assert result.allowed and result.action == "RETRY"


@pytest.mark.parametrize("state", ["CREATING", "SENT", "UNCLAIMED", "PAID", "RETURNED", "FAILED"])
def test_a3_retry_rt1_refuses_any_state_but_unknown(conn, state):
    payout(conn, state)
    result = policy.evaluate_retry(conn, "po_00000001", approved_by="ops-lead", confirm=True)
    assert failing(result) == ["RT-1"]


def test_a3_retry_rt3_needs_approval_and_preview_leaves_it_pending(conn):
    payout(conn, "UNKNOWN")
    assert failing(policy.evaluate_retry(conn, "po_00000001", approved_by="", confirm=True)) == ["RT-3"]
    preview = policy.evaluate_retry(conn, "po_00000001", preview=True)
    assert preview.allowed and preview.checks[2].ok is None
