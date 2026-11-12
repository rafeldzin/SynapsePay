"""A4: status sync and exception detection (UAT-SYN-01 to 07, UAT-DET-01 to 09; DET-07 needs the gated cancel, A5)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import db, events, seed
from app.cases import detect
from app.gate import executor
from app.paypal import sync
from tests.conftest import CORRECT_EMAIL, TYPO_EMAIL
from tests.helpers_a import payout, verify

PAY = dict(approved_by="ops-lead", confirm=True, expected_amount_cents=30000)
NOT_PAID = f"Hi, the milestone 1 payment never arrived. My PayPal email is {CORRECT_EMAIL}. Can you resend it there?"


@pytest.fixture
def conn(seed_a, fake_paypal):
    connection = db.connect()
    verify(connection)
    yield connection
    connection.close()


def sent(conn, fake, *, registered=False):
    """Pays m1 through the executor on the fake. Returns the SENT payout row."""
    if registered:
        fake.registered_emails.add(TYPO_EMAIL.lower())  # scenario A's payee, made registered for this test
    result = executor.pay("m1", **PAY)
    assert result.payout["state"] == "SENT"
    return result.payout


def row(conn, table, row_id):
    return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def types(conn):
    return [e["type"] for e in events.list_events(conn)]


def post(conn, body=NOT_PAID, from_email=CORRECT_EMAIL):
    with db.tx(conn):
        return detect.on_message(conn, "c1", from_email, body)


# ---- UAT-SYN -----------------------------------------------------------------------

def test_uat_syn_01_a_registered_receiver_ends_paid_and_the_milestone_paid(conn, fake_paypal):
    po = sent(conn, fake_paypal, registered=True)
    fake_paypal.advance()
    result = sync.sync_payout(po["id"])
    assert result["state"] == "PAID" and result["payout_item_id"] and result["item_status"] == "SUCCESS"
    assert row(conn, "milestones", "m1")["state"] == "PAID"
    after_pay = types(conn)[types(conn).index("PAYPAL_PAYOUT_CREATED"):]  # only what the sync wrote
    assert after_pay[after_pay.index("PAYOUT_STATE_CHANGED") + 1:] == [
        "PAYPAL_STATUS_READ", "PAYOUT_STATE_CHANGED", "MILESTONE_STATE_CHANGED"]


def test_uat_syn_02_an_unregistered_receiver_is_unclaimed_inside_a_success_batch(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.advance()
    result = sync.sync_payout(po["id"])
    assert (result["state"], result["batch_status"], result["item_status"]) == ("UNCLAIMED", "SUCCESS", "UNCLAIMED")
    assert row(conn, "milestones", "m1")["state"] == "PAYOUT_IN_FLIGHT"


@pytest.mark.parametrize("item_status, expected", [
    ("SUCCESS", "PAID"), ("UNCLAIMED", "UNCLAIMED"), ("RETURNED", "RETURNED"), ("REFUNDED", "RETURNED"),
    ("REVERSED", "RETURNED"), ("FAILED", "FAILED"), ("BLOCKED", "FAILED"), ("PENDING", "SENT"), ("ONHOLD", "SENT")])
def test_uat_syn_03_every_item_status_maps_as_in_prd_8_4(conn, fake_paypal, item_status, expected):
    po = sent(conn, fake_paypal)
    fake_paypal.set_item_status(fake_paypal.batches[po["payout_batch_id"]]["items"][0]["payout_item_id"], item_status)
    assert sync.sync_payout(po["id"])["state"] == expected


@pytest.mark.parametrize("batch_status", ["DENIED", "CANCELED"])
def test_uat_syn_03_a_denied_or_canceled_batch_fails_the_payout(conn, fake_paypal, batch_status):
    po = sent(conn, fake_paypal)
    fake_paypal.batches[po["payout_batch_id"]]["batch_status"] = batch_status
    result = sync.sync_payout(po["id"])
    assert result["state"] == "FAILED"
    assert row(conn, "milestones", "m1")["state"] == "PASS"  # a dead payout frees the milestone


def test_uat_syn_04_a_second_sync_with_no_change_writes_no_events(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.advance()
    sync.sync_payout(po["id"])  # -> UNCLAIMED
    before = len(events.list_events(conn))
    again = sync.sync_payout(po["id"])
    assert again["state"] == "UNCLAIMED" and len(events.list_events(conn)) == before


def test_uat_syn_05_a_failed_read_logs_sync_error_and_changes_nothing(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.advance()
    fake_paypal.fail_next("read_error")
    result = sync.sync_payout(po["id"])  # must not raise
    assert result["state"] == "SENT"
    assert types(conn)[-1] == "SYNC_ERROR"
    assert "PAYPAL_STATUS_READ" not in types(conn)


def test_uat_syn_06_an_unknown_payout_is_never_read(conn, fake_paypal):
    fake_paypal.fail_next("unknown_after_send")
    po = executor.pay("m1", **PAY).payout
    assert po["state"] == "UNKNOWN"
    reads_before = [n for n, _ in fake_paypal.calls if n == "get_batch"]
    result = sync.sync_payout(po["id"])
    assert result["state"] == "UNKNOWN"
    assert [n for n, _ in fake_paypal.calls if n == "get_batch"] == reads_before


def test_uat_syn_07_a_claimed_payout_is_paid_and_resolves_its_open_case(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.advance()
    sync.sync_payout(po["id"])  # UNCLAIMED
    case_id = post(conn)["case_id"]
    fake_paypal.claim(fake_paypal.batches[po["payout_batch_id"]]["items"][0]["payout_item_id"])
    result = sync.sync_payout(po["id"])
    assert result["state"] == "PAID" and row(conn, "milestones", "m1")["state"] == "PAID"
    case = row(conn, "cases", case_id)
    assert (case["state"], case["resolution"]) == ("RESOLVED", "PAID_AFTER_WAIT")


def test_a4_sync_of_an_unknown_payout_id_raises_lookup_error(conn):
    with pytest.raises(LookupError):
        sync.sync_payout("po_nope")


def test_a4_sync_all_syncs_only_eligible_payouts_created_within_24_hours(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.advance()
    assert sync.sync_all() == 1
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE payouts SET created_at = ? WHERE id = ?", (old, po["id"]))
    assert sync.sync_all() == 0


def test_a4_the_background_loop_moves_a_sent_payout_without_a_manual_sync(conn, fake_paypal, monkeypatch):
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "1")
    po = sent(conn, fake_paypal, registered=True)
    fake_paypal.advance()
    sync.start_loop()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and row(conn, "payouts", po["id"])["state"] != "PAID":
            time.sleep(0.2)
    finally:
        sync.stop_loop()
    assert row(conn, "payouts", po["id"])["state"] == "PAID"


# ---- UAT-DET -----------------------------------------------------------------------

def unclaimed(conn, fake):
    po = sent(conn, fake)
    fake.advance()
    return sync.sync_payout(po["id"])


def test_uat_det_01_the_seeded_not_paid_message_opens_a_d1_case_on_the_payout(conn, fake_paypal):
    po = unclaimed(conn, fake_paypal)
    result = post(conn, seed.scenario_a_message(CORRECT_EMAIL))
    case = row(conn, "cases", result["case_id"])
    assert (case["trigger_rule"], case["payout_id"], case["milestone_id"], case["state"]) == \
        ("D1", po["id"], "m1", "OPEN")
    order = types(conn)
    assert order.index("MESSAGE_RECEIVED") < order.index("CASE_OPENED")


def test_uat_det_02_a_second_not_paid_message_adds_no_second_case(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    first = post(conn)["case_id"]
    second = post(conn, "Hello? still nothing, payment never received")["case_id"]
    assert second == first and conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert types(conn).count("MESSAGE_RECEIVED") == 2


def test_uat_det_03_a_friendly_message_opens_no_case(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    result = post(conn, "Thanks, looks great!")
    assert result["case_id"] is None and conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
    assert result["message"]["body"] == "Thanks, looks great!"


def test_uat_det_04_an_indonesian_not_paid_message_opens_a_case(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    assert post(conn, "pembayaran milestone 1 belum masuk")["case_id"]


def test_uat_det_05_unclaimed_longer_than_the_grace_period_opens_a_d2_case(conn, fake_paypal):
    po = unclaimed(conn, fake_paypal)
    since = detect._parse(row(conn, "payouts", po["id"])["updated_at"])
    assert detect.run_rules(conn, now=since + timedelta(seconds=599)) == []
    assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
    opened = detect.run_rules(conn, now=since + timedelta(seconds=601))
    assert len(opened) == 1 and row(conn, "cases", opened[0])["trigger_rule"] == "D2"
    assert detect.run_rules(conn, now=since + timedelta(seconds=700)) == []  # no second case


def test_uat_det_06_a_sent_payout_that_pay_pal_fails_opens_a_d3_case(conn, fake_paypal):
    po = sent(conn, fake_paypal)
    fake_paypal.set_item_status(fake_paypal.batches[po["payout_batch_id"]]["items"][0]["payout_item_id"], "FAILED")
    assert sync.sync_payout(po["id"])["state"] == "FAILED"
    case = conn.execute("SELECT * FROM cases").fetchone()
    assert case["trigger_rule"] == "D3" and case["payout_id"] == po["id"]


def test_uat_det_08_an_email_that_differs_from_the_payee_flags_diversion(conn, fake_paypal):
    po = unclaimed(conn, fake_paypal)  # payee on file is the typo address
    result = post(conn, "My payment never arrived, please send it to attacker@example.test", CORRECT_EMAIL)
    assert result["diversion_risk"] is True
    case = row(conn, "cases", result["case_id"])
    assert case["diversion_risk"] == 1 and case["requested_email"] == "attacker@example.test"
    flagged = next(e for e in events.list_events(conn) if e["type"] == "DIVERSION_RISK_FLAGGED")
    assert flagged["payload"]["requested_email"] == "attacker@example.test"
    assert flagged["payload"]["current_email"] == TYPO_EMAIL
    assert flagged["payload"]["edit_distance"] == detect.edit_distance("attacker@example.test", TYPO_EMAIL)
    assert flagged["payout_id"] == po["id"]


def test_uat_det_09_the_same_email_in_a_different_case_is_not_diversion(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    result = post(conn, f"Payment never arrived, my email is {TYPO_EMAIL.upper()}", TYPO_EMAIL.title())
    assert result["diversion_risk"] is False
    assert row(conn, "cases", result["case_id"])["diversion_risk"] == 0
    assert "DIVERSION_RISK_FLAGGED" not in types(conn)


def test_a4_the_seeded_scenario_a_message_flags_the_correct_email_as_a_change_request(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    result = post(conn, seed.scenario_a_message(CORRECT_EMAIL))
    case = row(conn, "cases", result["case_id"])
    assert case["diversion_risk"] == 1 and case["requested_email"] == CORRECT_EMAIL.lower()
    assert detect.edit_distance(CORRECT_EMAIL, TYPO_EMAIL) == 1  # the typo is one letter


def test_a4_the_message_attaches_to_the_milestone_it_names_else_the_newest_live_payout(conn):
    verify(conn, "m2", state="PASS")
    payout(conn, "SENT", milestone_id="m1", pid="po_00000001")
    payout(conn, "SENT", milestone_id="m2", pid="po_00000002")
    conn.execute("UPDATE payouts SET created_at = '2026-11-12T10:00:00Z' WHERE id = 'po_00000001'")
    conn.execute("UPDATE payouts SET created_at = '2026-11-12T11:00:00Z' WHERE id = 'po_00000002'")
    named = post(conn, "milestone 1 payment never arrived")
    assert row(conn, "cases", named["case_id"])["payout_id"] == "po_00000001"
    conn.execute("UPDATE cases SET state = 'RESOLVED'")
    unnamed = post(conn, "my payment never arrived")
    assert row(conn, "cases", unnamed["case_id"])["payout_id"] == "po_00000002"  # the newest live one


def test_a4_a_not_paid_message_with_no_live_payout_is_stored_but_opens_no_case(conn):
    result = post(conn, "my payment never arrived")
    assert result["case_id"] is None and conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1


def test_a4_a_message_for_an_unknown_contract_raises_lookup_error(conn):
    with pytest.raises(LookupError):
        with db.tx(conn):
            detect.on_message(conn, "c9", CORRECT_EMAIL, "never arrived")


def test_a4_a_second_message_on_an_open_case_can_still_flag_diversion(conn, fake_paypal):
    unclaimed(conn, fake_paypal)
    first = post(conn, "my payment never arrived", TYPO_EMAIL)
    assert row(conn, "cases", first["case_id"])["diversion_risk"] == 0
    second = post(conn, "still not paid, send it to evil@example.test instead", TYPO_EMAIL)
    assert second["case_id"] == first["case_id"]
    assert row(conn, "cases", first["case_id"])["requested_email"] == "evil@example.test"


@pytest.mark.parametrize("a, b, distance", [("abc", "abc", 0), ("abc", "abd", 1), ("kitten", "sitting", 3), ("", "ab", 2)])
def test_a4_edit_distance_is_levenshtein(a, b, distance):
    assert detect.edit_distance(a, b) == distance


# ---- routes ------------------------------------------------------------------------

def _verify_and_pay(client, approve):
    assert client.post("/api/milestones/m1/deliverable",
                       json={"fixture": "m1_pass", "submitted_by": "dewi"}).status_code == 200
    return client.post("/api/milestones/m1/pay", json={**approve, "expected_amount_cents": 30000}).json()["payout"]


def test_a4_route_messages_opens_a_case_and_returns_the_prd_shape(client, seed_a, approve, fake_paypal):
    po = _verify_and_pay(client, approve)
    fake_paypal.advance()
    client.post(f"/api/payouts/{po['id']}/sync")  # UNCLAIMED
    resp = client.post("/api/messages", json={"contract_id": "c1", "from_email": CORRECT_EMAIL, "body": NOT_PAID})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"message", "case_id", "diversion_risk"} and body["case_id"] and body["diversion_risk"] is True
    state = client.get("/api/state").json()
    assert state["cases"][0]["trigger_rule"] == "D1" and len(state["messages"]) == 1


def test_a4_route_messages_unknown_contract_is_404_and_extra_fields_are_422(client, seed_a):
    ok = {"contract_id": "c9", "from_email": "a@b.test", "body": "hi"}
    assert client.post("/api/messages", json=ok).status_code == 404
    assert client.post("/api/messages", json={**ok, "contract_id": "c1", "x": 1}).status_code == 422


def test_a4_route_sync_and_recheck_resolve_a_claimed_payout(client, seed_a, approve, fake_paypal):
    po = _verify_and_pay(client, approve)
    fake_paypal.advance()
    assert client.post("/api/sync").json() == {"synced": 1}
    case_id = client.post("/api/messages", json={"contract_id": "c1", "from_email": CORRECT_EMAIL,
                                                 "body": NOT_PAID}).json()["case_id"]
    still = client.post(f"/api/cases/{case_id}/actions/recheck").json()
    assert still["payout"]["state"] == "UNCLAIMED" and still["case"]["state"] == "OPEN"
    fake_paypal.claim(fake_paypal.batches[po["payout_batch_id"]]["items"][0]["payout_item_id"])
    done = client.post(f"/api/cases/{case_id}/actions/recheck").json()
    assert done["payout"]["state"] == "PAID"
    assert (done["case"]["state"], done["case"]["resolution"]) == ("RESOLVED", "PAID_AFTER_WAIT")
    assert client.get("/api/state").json()["milestones"][0]["state"] == "PAID"


def test_a4_route_unknown_ids_are_404(client, seed_a):
    assert client.post("/api/payouts/po_nope/sync").status_code == 404
    assert client.post("/api/cases/case_nope/actions/recheck").status_code == 404
