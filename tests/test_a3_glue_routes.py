"""State, reset, arm-fault, deliverable and case routes (PRD 12). Covers UAT-API-01 to 06 and 09, UAT-SEC-09/11."""
import json

import pytest

from app import db, events
from app.contracts import GateResult
from tests.conftest import CORRECT_EMAIL, TYPO_EMAIL

STATE_KEYS = {"run_id", "scenario", "paypal_mode", "llm_mode", "contract", "milestones", "payouts",
              "cases", "messages", "events"}


def fixture_body(name, by="dewi"):
    return {"fixture": name, "submitted_by": by}


def submit(client, milestone, fixture):
    return client.post(f"/api/milestones/{milestone}/deliverable", json=fixture_body(fixture))


def pay(client, approve, milestone="m1", amount=30000, **extra):
    return client.post(f"/api/milestones/{milestone}/pay", json={**approve, "expected_amount_cents": amount, **extra})


def test_uat_api_01_health_has_the_five_fields(client):
    body = client.get("/api/health").json()
    assert set(body) == {"ok", "run_id", "scenario", "paypal_mode", "llm_mode"} and body["ok"] is True


def test_uat_api_02_state_has_the_prd_12_1_shape_and_events_oldest_first(client, seed_a):
    body = client.get("/api/state").json()
    assert set(body) == STATE_KEYS
    assert body["run_id"] == seed_a["run_id"] and body["contract"]["id"] == "c1"
    assert [m["id"] for m in body["milestones"]] == ["m1", "m2"]
    for m in body["milestones"]:
        assert {"criteria", "verification", "gate_preview"} <= set(m)
        GateResult(**m["gate_preview"])
    ids = [e["id"] for e in body["events"]]
    assert ids == sorted(ids) and body["events"][0]["type"] == "RESET"


def test_uat_api_03_reset_gives_a_new_run_and_the_scenario_email(client):
    first = client.post("/api/demo/reset", json={"scenario": "A"}).json()
    state_a = client.get("/api/state").json()
    second = client.post("/api/demo/reset", json={"scenario": "B"}).json()
    state_b = client.get("/api/state").json()
    assert first["run_id"] != second["run_id"] and (first["scenario"], second["scenario"]) == ("A", "B")
    assert state_a["contract"]["payee_email"] == TYPO_EMAIL
    assert state_b["contract"]["payee_email"] == CORRECT_EMAIL
    assert [m["state"] for m in state_b["milestones"]] == ["AWAITING_DELIVERABLE"] * 2
    assert state_b["payouts"] == [] and state_b["cases"] == [] and state_b["messages"] == []
    assert [e["type"] for e in state_b["events"]] == ["RESET"]


def test_uat_api_04_a_missing_database_boots_into_scenario_a(client):
    state = client.get("/api/state").json()
    assert state["scenario"] == "A" and state["contract"]["payee_email"] == TYPO_EMAIL


def test_uat_api_05_unknown_ids_are_404(client, seed_a, approve):
    assert client.get("/api/milestones/m9/gate").status_code == 404
    assert pay(client, approve, "m9").status_code == 404
    assert client.post("/api/payouts/po_nope/retry", json=approve).status_code == 404
    assert client.get("/api/cases/case_nope").status_code == 404
    assert client.post("/api/cases/case_nope/investigate").status_code == 404
    assert submit(client, "m9", "m1_pass").status_code == 404


def test_uat_api_06_the_operator_page_has_both_reset_buttons_and_no_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200 and 'id="reset-a"' in resp.text and 'id="reset-b"' in resp.text


def test_uat_api_09_a_gate_refusal_is_409_with_a_parsable_gate_result(client, seed_a, approve):
    resp = pay(client, approve)  # m1 was never verified
    assert resp.status_code == 409
    gate = GateResult(**resp.json())
    assert not gate.allowed and any(c.ok is False for c in gate.checks)


def test_uat_sec_09_no_secret_appears_in_state_health_or_event_payloads(client, seed_a, approve, monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "s3cret-client-value")
    monkeypatch.setenv("LLM_API_KEY", "s3cret-llm-value")
    submit(client, "m1", "m1_pass")
    pay(client, approve)
    text = client.get("/api/state").text + client.get("/api/health").text
    assert "s3cret" not in text


def test_a3_reset_rejects_an_unknown_scenario_and_extra_fields(client):
    assert client.post("/api/demo/reset", json={"scenario": "C"}).status_code == 422
    assert client.post("/api/demo/reset", json={"scenario": "A", "x": 1}).status_code == 422


def test_a3_reset_in_fake_mode_also_clears_the_fake_paypal(client, seed_a, fake_paypal, approve):
    submit(client, "m1", "m1_pass")
    pay(client, approve)
    from app.paypal.fake import active_fake
    assert len(active_fake().batches) == 1
    client.post("/api/demo/reset", json={"scenario": "A"})
    assert len(active_fake().batches) == 0


def test_a3_arm_fault_stores_it_logs_it_and_replaces_the_seeded_one(client, seed_b):
    resp = client.post("/api/demo/arm-fault", json={"fault": "timeout_before_send"})
    assert resp.json() == {"armed": "timeout_before_send"}
    conn = db.connect()
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        logged = [e for e in events.list_events(conn) if e["type"] == "FAULT_ARMED"]
    finally:
        conn.close()
    assert meta["armed_fault"] == "timeout_before_send"
    assert logged[-1]["payload"] == {"fault": "timeout_before_send"}
    assert client.post("/api/demo/arm-fault", json={"fault": "explode"}).status_code == 422


def test_a3_deliverable_fixture_is_verified_and_stored(client, seed_a):
    resp = submit(client, "m1", "m1_pass")
    assert resp.status_code == 200 and resp.json()["overall"] == "PASS"
    state = client.get("/api/state").json()
    m1 = state["milestones"][0]
    assert m1["state"] == "PASS" and m1["verification"]["overall"] == "PASS"
    assert m1["gate_preview"]["allowed"] is True
    order = [e["type"] for e in state["events"]]
    assert order.index("DELIVERABLE_SUBMITTED") < order.index("FACTS_COMPUTED") < order.index("VERDICT_RECORDED")


def test_a3_deliverable_unknown_fixture_is_404_and_bad_bodies_are_422(client, seed_a):
    assert submit(client, "m1", "nope").status_code == 404
    assert client.post("/api/milestones/m1/deliverable", json={"fixture": "m1_pass"}).status_code == 422
    assert client.post("/api/milestones/m1/deliverable",
                       json={**fixture_body("m1_pass"), "csv": "x"}).status_code == 422
    assert client.post("/api/milestones/m1/deliverable", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 422


def test_a3_deliverable_multipart_upload_is_accepted(client, seed_a):
    from app.verify import verifier
    csv_name, csv_bytes, report = verifier.load_fixture("m1_pass")
    resp = client.post("/api/milestones/m1/deliverable",
                       files={"csv_file": (csv_name, csv_bytes, "text/csv"),
                              "report_file": ("report.md", report.encode(), "text/markdown")},
                       data={"submitted_by": "dewi"})
    assert resp.status_code == 200 and resp.json()["overall"] == "PASS"


def test_uat_sec_11_a_6_mb_upload_is_413_and_nothing_is_stored(client, seed_a):
    resp = client.post("/api/milestones/m1/deliverable",
                       files={"csv_file": ("big.csv", b"x" * (6 * 1024 * 1024), "text/csv"),
                              "report_file": ("r.md", b"report", "text/markdown")},
                       data={"submitted_by": "dewi"})
    assert resp.status_code == 413
    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM deliverables").fetchone()[0] == 0
    finally:
        conn.close()


def test_a3_multipart_missing_a_part_is_422(client, seed_a):
    resp = client.post("/api/milestones/m1/deliverable", files={"csv_file": ("a.csv", b"x", "text/csv")},
                       data={"submitted_by": "dewi"})
    assert resp.status_code == 422


def test_a3_a_deliverable_is_409_while_the_milestone_is_in_flight_or_paid(client, seed_a, approve, fake_paypal):
    submit(client, "m1", "m1_pass")
    pay(client, approve)
    locked = submit(client, "m1", "m1_pass")
    assert locked.status_code == 409
    state = client.get("/api/state").json()
    assert state["milestones"][0]["verification"]["overall"] == "PASS"  # the stored verdict is unchanged


def test_a3_verify_then_pay_over_http_end_to_end_in_scenario_a(client, seed_a, approve, fake_paypal):
    assert submit(client, "m1", "m1_pass").status_code == 200
    paid = pay(client, approve)
    assert paid.status_code == 200 and paid.json()["payout"]["state"] == "SENT"
    state = client.get("/api/state").json()
    assert state["milestones"][0]["state"] == "PAYOUT_IN_FLIGHT" and len(state["payouts"]) == 1
    assert state["milestones"][1]["gate_preview"]["allowed"] is False  # m2 is still awaiting a deliverable


def test_a3_get_case_returns_the_case_with_its_report_and_events(client, seed_a, approve, fake_paypal):
    submit(client, "m1", "m1_pass")
    fake_paypal.fail_next("unknown_after_send")
    po = pay(client, approve).json()["payout"]
    case_id = client.post(f"/api/payouts/{po['id']}/retry", json=approve).json()["case_id"]
    case = client.get(f"/api/cases/{case_id}").json()
    assert case["id"] == case_id and case["trigger_rule"] == "D4" and case["report"] is None
    assert [e["type"] for e in case["events"]] == ["CASE_OPENED"]


def test_a3_investigate_a_d4_case_recommends_reconcile_once_paypal_shows_success(
        client, seed_a, approve, fake_paypal, fake_llm):
    fake_paypal.registered_emails.add(TYPO_EMAIL.lower())  # scenario A's payee is registered in this test
    submit(client, "m1", "m1_pass")
    fake_paypal.fail_next("unknown_after_send")
    po = client.post("/api/milestones/m1/pay", json={**approve, "expected_amount_cents": 30000}).json()["payout"]
    case_id = client.post(f"/api/payouts/{po['id']}/retry", json=approve).json()["case_id"]
    fake_paypal.advance()
    resp = client.post(f"/api/cases/{case_id}/investigate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["recommended_action"] == "RECONCILE_MARK_PAID"
    state = client.get("/api/state").json()
    assert state["cases"][0]["state"] == "INVESTIGATED" and state["cases"][0]["report"]["recommended_action"]
    assert len(fake_paypal.write_calls) == 2  # create + retry: the investigation wrote nothing


def test_a3_polling_state_and_gate_preview_never_writes_an_event(client, seed_a, approve, fake_paypal):
    submit(client, "m1", "m1_pass")
    pay(client, approve)
    before = client.get("/api/state").json()["events"]
    for _ in range(3):
        client.get("/api/state")
        client.get("/api/milestones/m1/gate")
        client.get("/api/milestones/m2/gate")
    after = client.get("/api/state").json()["events"]
    assert [e["id"] for e in after] == [e["id"] for e in before]
    assert "GATE_EVALUATED" in [e["type"] for e in before]  # only the real pay wrote one
    assert sum(e["type"] == "GATE_EVALUATED" for e in after) == 1


def test_a3_state_returns_parsed_objects_not_raw_json_strings(client, seed_a):
    submit(client, "m1", "m1_pass")
    state = client.get("/api/state").json()
    m1 = state["milestones"][0]
    assert isinstance(m1["criteria"], list) and isinstance(m1["verification"], dict)
    assert all(isinstance(e["payload"], dict) for e in state["events"])
    assert not any(k.endswith("_json") for m in state["milestones"] for k in m)
    assert not any(k.endswith("_json") for e in state["events"] for k in e)
