"""A5 (scenario B part): demo fault hook, gated RECONCILE and the UAT-FB tests (PRD 8.3, 10, 12.2, 13.4)."""
import pytest

from app import db, events
from app.contracts import GateResult
from app.gate import executor, policy
from tests.conftest import CORRECT_EMAIL

OK = {"approved_by": "ops-lead", "confirm": True}


def submit(client, milestone="m1", fixture="m1_pass"):
    assert client.post(f"/api/milestones/{milestone}/deliverable",
                       json={"fixture": fixture, "submitted_by": "dewi"}).status_code == 200


def pay(client, milestone="m1", amount=30000):
    return client.post(f"/api/milestones/{milestone}/pay", json={**OK, "expected_amount_cents": amount})


def retry(client, payout_id):
    return client.post(f"/api/payouts/{payout_id}/retry", json=OK)


def reconcile(client, case_id, **override):
    return client.post(f"/api/cases/{case_id}/actions/reconcile", json={**OK, **override})


def failing(body):
    return [c["id"] for c in body["checks"] if c["ok"] is False]


def event_types(client):
    return [e["type"] for e in client.get("/api/state").json()["events"]]


@pytest.fixture
def scenario_b(client, seed_b, fake_paypal):
    """Scenario B: m1 verified, then paid with the seeded timeout_after_send fault."""
    submit(client)
    po = pay(client).json()["payout"]
    return client, fake_paypal, po


def d4_case(client, fake, payout):
    resp = retry(client, payout["id"])
    assert resp.status_code == 200
    return resp.json()["case_id"]


# ---- UAT-FB --------------------------------------------------------------------

def test_uat_fb_01_the_armed_fault_fires_and_the_payout_is_unknown(scenario_b):
    client, fake, po = scenario_b
    assert po["state"] == "UNKNOWN" and po["payout_batch_id"] is None
    assert len(fake.batches) == 1  # the request really reached "PayPal"
    assert "PAYPAL_OUTCOME_UNKNOWN" in event_types(client)
    unknown = next(e for e in client.get("/api/state").json()["events"] if e["type"] == "PAYPAL_OUTCOME_UNKNOWN")
    assert unknown["payload"]["simulated_fault"] == "timeout_after_send"  # labeled simulated (INV-11)


def test_uat_fb_02_retry_with_reject_mode_finds_the_original_and_opens_d4(scenario_b):
    client, fake, po = scenario_b
    resp = retry(client, po["id"]).json()
    first, second = [kw for _, kw in fake.write_calls]
    assert second["sender_batch_id"] == first["sender_batch_id"]
    assert resp["payout"]["state"] == "UNKNOWN" and resp["payout"]["payout_batch_id"] == "FAKEB0001"
    assert "PAYPAL_ORIGINAL_FOUND" in event_types(client)
    case = client.get(f"/api/cases/{resp['case_id']}").json()
    assert case["trigger_rule"] == "D4" and len(fake.batches) == 1


def test_uat_fb_03_retry_with_replay_mode_is_created_with_the_original_id(client, seed_b, fake_paypal):
    fake_paypal.duplicate_mode = "replay"
    submit(client)
    po = pay(client).json()["payout"]
    resp = retry(client, po["id"]).json()
    assert resp["payout"]["state"] == "UNKNOWN" and resp["payout"]["payout_batch_id"] == "FAKEB0001"
    assert resp["case_id"] and len(fake_paypal.batches) == 1


def test_uat_fb_04_retry_of_a_payout_that_is_not_unknown_is_409_on_rt1(client, seed_a, fake_paypal):
    submit(client)
    po = pay(client).json()["payout"]
    resp = retry(client, po["id"])
    assert resp.status_code == 409 and failing(resp.json()) == ["RT-1"]


def test_uat_fb_05_after_the_retry_the_investigator_recommends_reconcile(scenario_b, fake_llm):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    report = client.post(f"/api/cases/{case_id}/investigate").json()
    assert report["recommended_action"] == "RECONCILE_MARK_PAID" and report["do_not_resend"] is True
    assert report["hypotheses"][0]["code"] == "ORIGINAL_SUCCEEDED_LEDGER_STALE"


def test_uat_fb_06_reconcile_with_approval_marks_everything_paid_without_a_write(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    writes_before, calls_before = len(fake.write_calls), len(fake.calls)
    resp = reconcile(client, case_id)
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body["gate"]["checks"]] == ["RC-1", "RC-2", "RC-3", "RC-4", "RC-5"]
    assert body["payout"]["state"] == "PAID" and body["payout"]["payout_item_id"]
    assert body["case"]["state"] == "RESOLVED" and body["case"]["resolution"] == "RECONCILED_PAID"
    assert len(fake.write_calls) == writes_before  # no PayPal write
    assert [n for n, _ in fake.calls[calls_before:]] == ["get_batch"]  # but one fresh batch read
    state = client.get("/api/state").json()
    assert state["milestones"][0]["state"] == "PAID"
    types = [e["type"] for e in state["events"]]
    last_gate = len(types) - 1 - types[::-1].index("GATE_EVALUATED")  # the reconcile's own evaluation
    assert types[last_gate:] == ["GATE_EVALUATED", "PAYPAL_STATUS_READ", "PAYOUT_STATE_CHANGED",
                                 "MILESTONE_STATE_CHANGED", "CASE_STATE_CHANGED"]
    changed = [e for e in state["events"] if e["type"] == "CASE_STATE_CHANGED"][-1]
    assert changed["payload"]["resolution"] == "RECONCILED_PAID"


def test_uat_fb_07_reconcile_while_the_item_is_still_pending_is_409_on_rc3(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)  # no advance(): PayPal still shows PENDING
    resp = reconcile(client, case_id)
    assert resp.status_code == 409 and failing(resp.json()) == ["RC-3"]
    assert client.get("/api/state").json()["payouts"][0]["state"] == "UNKNOWN"


def test_uat_fb_08_a_receiver_that_differs_from_the_ledger_is_409_on_rc4(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    fake.batches["FAKEB0001"]["items"][0]["receiver"] = "someone.else@example.test"
    resp = reconcile(client, case_id)
    assert resp.status_code == 409 and failing(resp.json()) == ["RC-4"]


def test_uat_fb_09_a_fault_before_send_then_retry_creates_the_one_batch(client, seed_b, fake_paypal, fake_llm):
    assert client.post("/api/demo/arm-fault", json={"fault": "timeout_before_send"}).status_code == 200
    submit(client)
    po = pay(client).json()["payout"]
    assert po["state"] == "UNKNOWN" and len(fake_paypal.batches) == 0
    resp = retry(client, po["id"]).json()
    assert resp["payout"]["state"] == "UNKNOWN" and resp["payout"]["payout_batch_id"] == "FAKEB0001"
    assert len(fake_paypal.batches) == 1
    assert client.get(f"/api/cases/{resp['case_id']}").json()["trigger_rule"] == "D4"
    fake_paypal.advance()
    assert client.post(f"/api/cases/{resp['case_id']}/investigate").json()["recommended_action"] == \
        "RECONCILE_MARK_PAID"
    assert reconcile(client, resp["case_id"]).json()["payout"]["state"] == "PAID"


def test_uat_fb_10_the_whole_scenario_pays_the_milestone_exactly_once(scenario_b, fake_llm):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    client.post(f"/api/cases/{case_id}/investigate")
    assert reconcile(client, case_id).status_code == 200
    assert len(fake.batches) == 1
    assert [kw["amount_cents"] for _, kw in fake.write_calls] == [30000, 30000]  # both are the one batch's key
    again = pay(client)
    assert again.status_code == 409  # PAID: nothing can pay it a second time
    assert len(fake.batches) == 1
    state = client.get("/api/state").json()
    assert state["milestones"][0]["state"] == "PAID" and sum(p["state"] == "PAID" for p in state["payouts"]) == 1


# ---- fault hook ----------------------------------------------------------------

def test_a5_a_fault_is_consumed_by_exactly_one_create(client, seed_b, fake_paypal):
    submit(client)
    po = pay(client).json()["payout"]
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE key = 'armed_fault'").fetchone()[0] == 0
    finally:
        conn.close()
    resp = retry(client, po["id"]).json()  # the retry is not faulted again
    assert resp["payout"]["payout_batch_id"] == "FAKEB0001"


def test_a5_scenario_a_has_no_fault_and_pays_normally(client, seed_a, fake_paypal):
    submit(client)
    assert pay(client).json()["payout"]["state"] == "SENT"


def test_a5_arming_a_fault_in_scenario_a_makes_the_next_pay_unknown(client, seed_a, fake_paypal):
    client.post("/api/demo/arm-fault", json={"fault": "timeout_after_send"})
    submit(client)
    assert pay(client).json()["payout"]["state"] == "UNKNOWN"
    assert len(fake_paypal.batches) == 1


# ---- RECONCILE gate ------------------------------------------------------------

def test_a5_reconcile_is_refused_without_approval_and_leaves_the_ledger_alone(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    resp = reconcile(client, case_id, confirm=False)
    assert resp.status_code == 409 and failing(resp.json()) == ["RC-5"]
    assert client.get("/api/state").json()["payouts"][0]["state"] == "UNKNOWN"


def test_a5_reconcile_twice_the_second_is_refused_on_rc1(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    assert reconcile(client, case_id).status_code == 200
    second = reconcile(client, case_id)
    assert second.status_code == 409 and "RC-1" in failing(second.json())


def test_a5_reconcile_of_a_payout_that_is_not_unknown_is_refused_on_rc1(client, seed_a, fake_paypal):
    submit(client)
    po = pay(client).json()["payout"]  # SENT
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, opened_at, "
                         "updated_at) VALUES ('case_00000001', ?, 'm1', 'D1', 'OPEN', 't', 't')", (po["id"],))
    finally:
        conn.close()
    resp = reconcile(client, "case_00000001")
    assert resp.status_code == 409 and "RC-1" in failing(resp.json())


def test_a5_a_failed_fresh_read_fails_the_gate_with_the_reason_and_changes_nothing(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    fake.fail_next("read_error")
    resp = reconcile(client, case_id)
    gate = GateResult(**resp.json())
    assert resp.status_code == 409 and not gate.allowed
    assert {c.id for c in gate.checks if c.ok is False} >= {"RC-2", "RC-3", "RC-4"}
    assert "fresh PayPal read failed" in next(c for c in gate.checks if c.id == "RC-2").detail
    assert client.get("/api/state").json()["payouts"][0]["state"] == "UNKNOWN"


def test_a5_reconcile_works_on_a_case_that_was_never_investigated(scenario_b):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    resp = reconcile(client, case_id)  # the case is still OPEN
    assert resp.status_code == 200 and resp.json()["case"]["state"] == "RESOLVED"


def test_a5_reconcile_unknown_case_is_404_and_extra_fields_are_422(client, seed_b):
    assert reconcile(client, "case_nope").status_code == 404
    assert client.post("/api/cases/case_nope/actions/reconcile",
                       json={**OK, "amount_cents": 1}).status_code == 422


def test_a5_reconcile_response_case_has_a_parsed_report_field(scenario_b, fake_llm):
    client, fake, po = scenario_b
    case_id = d4_case(client, fake, po)
    fake.advance()
    client.post(f"/api/cases/{case_id}/investigate")
    case = reconcile(client, case_id).json()["case"]
    assert isinstance(case["report"], dict) and "report_json" not in case
