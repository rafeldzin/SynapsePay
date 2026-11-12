"""A3.3: pay, gate preview and retry over HTTP (UAT-PAY-01 to 12, UAT-SEC-06 to 08)."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.contracts import GateResult
from tests.conftest import TYPO_EMAIL
from tests.helpers_a import payout, verify


@pytest.fixture
def conn(seed_a, fake_paypal):
    connection = db.connect()
    yield connection
    connection.close()


@pytest.fixture
def verified(conn):
    verify(conn)  # m1 verified PASS
    return conn


def pay_body(approve, amount=30000, **extra):
    return {**approve, "expected_amount_cents": amount, **extra}


def pay(client, approve, milestone="m1", amount=30000, **extra):
    return client.post(f"/api/milestones/{milestone}/pay", json=pay_body(approve, amount, **extra))


def failing(body):
    return [c["id"] for c in body["checks"] if c["ok"] is False]


def types(conn):
    return [e["type"] for e in events.list_events(conn)]


def test_uat_pay_01_a_verified_milestone_is_paid_with_the_contract_values(client, verified, approve,
                                                                           fake_paypal, seed_a):
    resp = pay(client, approve)
    assert resp.status_code == 200
    body = resp.json()
    assert [c["id"] for c in body["gate"]["checks"]] == [f"PAY-{i}" for i in range(1, 9)]
    assert all(c["ok"] is True for c in body["gate"]["checks"])
    (name, kwargs), = fake_paypal.write_calls
    assert name == "create_payout" and kwargs["amount_cents"] == 30000
    assert kwargs["receiver_email"] == TYPO_EMAIL
    assert kwargs["sender_batch_id"] == f"SP-{seed_a['run_id']}-m1-A1"
    assert body["payout"]["state"] == "SENT"
    assert verified.execute("SELECT state FROM milestones WHERE id='m1'").fetchone()[0] == "PAYOUT_IN_FLIGHT"
    order = types(verified)
    for a, b in [("GATE_EVALUATED", "PAYOUT_CREATING"), ("PAYOUT_CREATING", "PAYPAL_PAYOUT_CREATED"),
                 ("PAYPAL_PAYOUT_CREATED", "PAYOUT_STATE_CHANGED")]:
        assert order.index(a) < order.index(b)


@pytest.mark.parametrize("state", ["AWAITING_DELIVERABLE", "FAIL", "NEEDS_REVIEW"])
def test_uat_pay_02_m2_is_refused_unless_it_passed(client, conn, approve, fake_paypal, state):
    if state != "AWAITING_DELIVERABLE":
        verify(conn, "m2", state=state)
    resp = pay(client, approve, "m2", 20000)
    assert resp.status_code == 409 and "PAY-1" in failing(resp.json())
    assert types(conn).count("GATE_EVALUATED") == 1
    assert fake_paypal.write_calls == []


def test_uat_pay_03_a_fail_with_a_25_character_override_is_paid_and_recorded(client, conn, approve, fake_paypal):
    verify(conn, "m2", state="FAIL")
    reason = "x" * 25
    resp = pay(client, approve, "m2", 20000, override_reason=reason)
    assert resp.status_code == 200 and len(fake_paypal.write_calls) == 1
    recorded = [e for e in events.list_events(conn) if e["type"] == "OVERRIDE_RECORDED"]
    assert len(recorded) == 1
    assert recorded[0]["payload"]["reason"] == reason and recorded[0]["payload"]["approved_by"] == "ops-lead"


def test_uat_pay_04_a_ten_character_override_is_refused(client, conn, approve, fake_paypal):
    verify(conn, "m2", state="FAIL")
    resp = pay(client, approve, "m2", 20000, override_reason="x" * 10)
    assert resp.status_code == 409 and "PAY-1" in failing(resp.json()) and fake_paypal.write_calls == []


def test_uat_pay_05_paying_twice_is_refused_on_pay4_with_one_create(client, verified, approve, fake_paypal):
    assert pay(client, approve).status_code == 200
    second = pay(client, approve)
    assert second.status_code == 409 and "PAY-4" in failing(second.json())
    assert len(fake_paypal.write_calls) == 1


def test_uat_pay_06_ten_parallel_pays_give_one_200_and_nine_409(client, verified, approve, fake_paypal):
    codes, barrier = [], threading.Barrier(10)

    def worker():
        barrier.wait()
        codes.append(pay(client, approve).status_code)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200] + [409] * 9
    assert len(fake_paypal.write_calls) == 1
    assert verified.execute("SELECT COUNT(*) FROM payouts").fetchone()[0] == 1


def test_uat_pay_07_a_newer_unverified_deliverable_blocks_on_pay2(client, verified, approve, fake_paypal):
    verified.execute("INSERT INTO deliverables (milestone_id, sha256, csv_name, csv_bytes, report_text, "
                     "submitted_by, created_at) VALUES ('m1', 'newer', 'x.csv', x'00', 'r', 'dewi', 't')")
    resp = pay(client, approve)
    assert resp.status_code == 409 and failing(resp.json()) == ["PAY-2"]
    assert fake_paypal.write_calls == []


def test_uat_pay_08_a_rejected_create_fails_the_payout_and_the_next_pay_is_attempt_2(
        client, verified, approve, fake_paypal):
    fake_paypal.fail_next("reject_400")
    resp = pay(client, approve)
    assert resp.status_code == 200 and resp.json()["payout"]["state"] == "FAILED"
    assert verified.execute("SELECT state FROM milestones WHERE id='m1'").fetchone()[0] == "PASS"
    rejected = next(e for e in events.list_events(verified) if e["type"] == "PAYPAL_REJECTED")
    assert rejected["payload"]["name"] and rejected["payload"]["debug_id"]
    second = pay(client, approve)
    assert second.status_code == 200 and second.json()["payout"]["sender_batch_id"].endswith("-A2")


def test_uat_pay_09_a_lost_response_is_unknown_and_blocks_a_second_pay(client, verified, approve, fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    resp = pay(client, approve)
    assert resp.status_code == 200 and resp.json()["payout"]["state"] == "UNKNOWN"
    assert "PAYPAL_OUTCOME_UNKNOWN" in types(verified)
    assert verified.execute("SELECT state FROM milestones WHERE id='m1'").fetchone()[0] == "PAYOUT_IN_FLIGHT"
    again = pay(client, approve)
    assert again.status_code == 409 and "PAY-4" in failing(again.json())


def test_uat_pay_10_startup_recovery_turns_a_stale_creating_payout_into_unknown(conn, fake_paypal):
    payout(conn, "CREATING")
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE payouts SET created_at = ?", (old,))
    from app.main import app
    with TestClient(app):  # startup runs the recovery
        pass
    assert conn.execute("SELECT state FROM payouts").fetchone()[0] == "UNKNOWN"


def test_uat_pay_11_the_preview_leaves_the_human_check_pending_and_writes_no_event(client, verified):
    before = len(events.list_events(verified))
    resp = client.get("/api/milestones/m1/gate")
    assert resp.status_code == 200
    gate = GateResult(**resp.json())
    assert gate.preview and gate.allowed
    assert next(c for c in gate.checks if c.id == "PAY-7").ok is None
    assert len(events.list_events(verified)) == before


def test_uat_pay_12_each_reset_gives_a_different_sender_batch_id(client, conn, approve, fake_paypal):
    keys = []
    for _ in range(2):
        db.reset_demo(conn, "A")
        verify(conn)
        keys.append(pay(client, approve).json()["payout"]["sender_batch_id"])
    assert keys[0] != keys[1]


@pytest.mark.parametrize("extra", [{"amount_cents": 1}, {"receiver": "attacker@example.test"}])
def test_uat_sec_06_extra_fields_are_rejected_with_422_and_nothing_happens(client, verified, approve,
                                                                           fake_paypal, extra):
    resp = pay(client, approve, **extra)
    assert resp.status_code == 422
    assert fake_paypal.calls == []
    assert verified.execute("SELECT COUNT(*) FROM payouts").fetchone()[0] == 0


def test_uat_sec_07_an_amount_one_cent_off_is_refused_on_pay3(client, verified, approve, fake_paypal):
    resp = pay(client, approve, amount=30001)
    assert resp.status_code == 409 and failing(resp.json()) == ["PAY-3"]
    assert fake_paypal.write_calls == []


@pytest.mark.parametrize("override", [{"confirm": False}, {"approved_by": ""}])
def test_uat_sec_08_no_confirmation_or_no_approver_is_refused_on_pay7(client, verified, approve,
                                                                       fake_paypal, override):
    resp = pay(client, {**approve, **override})
    assert resp.status_code == 409 and failing(resp.json()) == ["PAY-7"]
    assert fake_paypal.write_calls == []


def test_a3_api_unknown_ids_are_404(client, seed_a):
    assert client.get("/api/milestones/m9/gate").status_code == 404
    assert client.post("/api/milestones/m9/pay", json={"approved_by": "a", "confirm": True,
                                                        "expected_amount_cents": 1}).status_code == 404
    assert client.post("/api/payouts/po_nope/retry", json={"approved_by": "a", "confirm": True}).status_code == 404


def test_a3_api_a_refusal_body_parses_as_a_gate_result(client, verified, approve):
    resp = pay(client, {**approve, "confirm": False})
    gate = GateResult(**resp.json())
    assert not gate.allowed and any(c.ok is False for c in gate.checks)


@pytest.mark.parametrize("bad", [{"confirm": "true"}, {"expected_amount_cents": "30000"}, {"approved_by": None}])
def test_a3_api_wrong_types_are_422(client, verified, approve, bad):
    assert client.post("/api/milestones/m1/pay", json={**pay_body(approve), **bad}).status_code == 422


def test_a3_api_retry_over_http_finds_the_original_and_opens_d4(client, verified, approve, fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    po = pay(client, approve).json()["payout"]
    resp = client.post(f"/api/payouts/{po['id']}/retry", json=approve)
    assert resp.status_code == 200
    body = resp.json()
    assert body["payout"]["state"] == "UNKNOWN" and body["case_id"]
    assert body["payout"]["payout_batch_id"] == "FAKEB0001" and len(fake_paypal.batches) == 1


def test_a3_api_retry_of_a_payout_that_is_not_unknown_is_409_on_rt1(client, verified, approve, fake_paypal):
    po = pay(client, approve).json()["payout"]
    resp = client.post(f"/api/payouts/{po['id']}/retry", json=approve)
    assert resp.status_code == 409 and failing(resp.json()) == ["RT-1"]
    assert len(fake_paypal.write_calls) == 1


def test_a3_api_retry_rejects_extra_fields(client, verified, approve, fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    po = pay(client, approve).json()["payout"]
    resp = client.post(f"/api/payouts/{po['id']}/retry", json={**approve, "amount_cents": 1})
    assert resp.status_code == 422
