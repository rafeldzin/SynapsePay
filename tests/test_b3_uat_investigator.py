"""UAT 7.2 rows INV-01 to INV-12 on Dev A's db.py, events.py and FakePayPal.

Cases are set up as detection (A4) and the gate (A3) will leave them: a payout
created at FakePayPal and settled with advance(), its ledger row, the message
and the case row. The investigation then runs through `investigate(case_id)`,
the function POST /api/cases/{id}/investigate calls. UAT-SEC-05 needs real
detection for the diversion flag and is written once A4 lands.
"""
import json

import pytest

from app import db, seed
from app.contracts import InvestigationReport
from app.investigate import investigator
from tests.conftest import CORRECT_EMAIL, TYPO_EMAIL

PRD_TOOLS = ["paypal_get_payout_item", "paypal_get_payout_batch",
             "ledger_get_milestone_payouts", "case_get_messages_and_events"]
CASE, PAYOUT, NOW = "case_aaaaaaaa", "po_aaaaaaaa", "2026-11-12T15:00:00Z"


def _setup(fake_paypal, *, scenario, payout_state, receiver, settle=True,
           send=True, store_item=True, diversion=False, message=None, trigger="D1"):
    conn = db.connect()
    try:
        db.reset(conn) if db.schema_exists(conn) else db.init_schema(conn)
        batch_id = item_id = None
        if send:
            created = fake_paypal.create_payout(
                sender_batch_id="SP-7f3a9c1e-m1-A1", receiver_email=receiver,
                amount_cents=30000, currency="USD", note="Milestone m1")
            if settle:
                fake_paypal.advance()
            batch_id = created.payout_batch_id
            item_id = fake_paypal.get_batch(batch_id).items[0].payout_item_id if store_item else None
        with db.tx(conn):
            conn.execute("INSERT INTO meta (key, value) VALUES ('run_id', '7f3a9c1e')")
            seed.seed_scenario(conn, scenario, payee_email_correct=CORRECT_EMAIL,
                               payee_email_typo=TYPO_EMAIL)
            conn.execute(
                "INSERT INTO payouts (id, milestone_id, attempt, sender_batch_id, recipient_email, "
                "amount_cents, currency, state, payout_batch_id, payout_item_id, approved_by, "
                "created_at, updated_at) VALUES (?, 'm1', 1, 'SP-7f3a9c1e-m1-A1', ?, 30000, 'USD', "
                "?, ?, ?, 'ops-lead', ?, ?)",
                (PAYOUT, receiver, payout_state, batch_id, item_id, NOW, NOW))
            if message:
                conn.execute("INSERT INTO messages (id, contract_id, from_email, body, created_at) "
                             "VALUES ('msg_aaaaaaaa', 'c1', ?, ?, ?)", (CORRECT_EMAIL, message, NOW))
            conn.execute(
                "INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, "
                "diversion_risk, requested_email, opened_at, updated_at) "
                "VALUES (?, ?, 'm1', ?, 'OPEN', ?, ?, ?, ?)",
                (CASE, PAYOUT, trigger, int(diversion), CORRECT_EMAIL if diversion else None, NOW, NOW))
    finally:
        conn.close()


def scenario_a_case(fake_paypal):
    """Paid to the mistyped email, item UNCLAIMED, contractor wrote from the correct one."""
    _setup(fake_paypal, scenario="A", payout_state="UNCLAIMED", receiver=TYPO_EMAIL,
           diversion=True, message=seed.scenario_a_message(CORRECT_EMAIL))


def scenario_b_case(fake_paypal):
    """Response lost, the retry linked the original batch (D4), item SUCCESS at PayPal."""
    _setup(fake_paypal, scenario="B", payout_state="UNKNOWN", receiver=CORRECT_EMAIL,
           store_item=False, trigger="D4")


def _case_row():
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM cases WHERE id = ?", (CASE,)).fetchone())
    finally:
        conn.close()


def _events(type_):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM events WHERE type = ? ORDER BY id", (type_,))]
    finally:
        conn.close()


def _rank1(report):
    return next(h for h in report.hypotheses if h.rank == 1)


def test_uat_inv_01_scenario_a_case(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)

    report = investigator.investigate(CASE)

    rank1 = _rank1(report)
    assert (rank1.code, rank1.supported) == ("MISTYPED_RECIPIENT", True)
    assert any(h.code == "PROCESSING_DELAY" and h.supported is False for h in report.hypotheses)
    assert report.recommended_action == "CANCEL_UNCLAIMED_AND_REISSUE"
    assert report.do_not_resend is True
    assert report.diversion_risk is True
    assert report.required_verification is not None
    assert any(e.source == "paypal" and e.data.get("transaction_status") == "UNCLAIMED"
               for e in report.evidence)
    assert report.reply_draft.strip()
    assert _case_row()["state"] == "INVESTIGATED"


def test_uat_inv_02_scenario_b_case(fake_paypal, fake_llm):
    scenario_b_case(fake_paypal)

    report = investigator.investigate(CASE)

    assert _rank1(report).code == "ORIGINAL_SUCCEEDED_LEDGER_STALE"
    assert report.recommended_action == "RECONCILE_MARK_PAID"
    assert report.do_not_resend is True


def test_uat_inv_03_pending_item_waits(fake_paypal, fake_llm):
    _setup(fake_paypal, scenario="B", payout_state="SENT", receiver=CORRECT_EMAIL, settle=False)

    report = investigator.investigate(CASE)

    assert report.recommended_action == "WAIT_AND_RECHECK"


def test_uat_inv_04_unknown_without_batch_retries_same_batch_id(fake_paypal, fake_llm):
    _setup(fake_paypal, scenario="B", payout_state="UNKNOWN", receiver=CORRECT_EMAIL, send=False)

    report = investigator.investigate(CASE)

    assert report.recommended_action == "RETRY_SAME_BATCH_ID"
    paypal = [e for e in report.evidence if e.source == "paypal"]
    assert paypal and all(e.data["available"] is False for e in paypal)


def test_uat_inv_05_step_cap(fake_paypal, fake_llm, monkeypatch):
    monkeypatch.setenv("INVESTIGATOR_MAX_STEPS", "6")
    scenario_a_case(fake_paypal)
    fake_llm.use("infinite_tools")

    report = investigator.investigate(CASE)

    assert report.steps_used == 6
    assert (report.recommended_action, report.downgraded_by) == ("ESCALATE_TO_HUMAN", "step_cap")
    assert set(PRD_TOOLS) <= {e.tool for e in report.evidence}
    assert _case_row()["state"] == "NEEDS_HUMAN"


def test_uat_inv_06_action_outside_list(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    fake_llm.use("action_outside_list")

    report = investigator.investigate(CASE)

    assert (report.recommended_action, report.downgraded_by) == (
        "ESCALATE_TO_HUMAN", "action_not_in_list")


def test_uat_inv_07_no_citations(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    fake_llm.use("no_citations")

    report = investigator.investigate(CASE)

    assert (report.recommended_action, report.downgraded_by) == (
        "ESCALATE_TO_HUMAN", "no_paypal_citation")


def test_uat_inv_08_cancel_when_success_is_status_mismatch(fake_paypal, fake_llm):
    _setup(fake_paypal, scenario="B", payout_state="PAID", receiver=CORRECT_EMAIL)
    fake_llm.use("cancel_when_success")

    report = investigator.investigate(CASE)

    assert (report.recommended_action, report.downgraded_by) == (
        "ESCALATE_TO_HUMAN", "status_mismatch")


def test_uat_inv_09_llm_timeout(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    fake_llm.use("timeout")

    report = investigator.investigate(CASE)

    assert (report.recommended_action, report.downgraded_by) == ("ESCALATE_TO_HUMAN", "llm_error")
    assert [e.tool for e in report.evidence] == PRD_TOOLS
    assert report.mode == "tool_loop"


def test_uat_inv_10_investigation_is_read_only_and_logged(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    writes_before = len(fake_paypal.write_calls)

    report = investigator.investigate(CASE)

    assert len(fake_paypal.write_calls) == writes_before
    tool_events = _events("INVESTIGATION_TOOL_CALL")
    assert len(tool_events) == len(report.evidence) == 4
    assert len(_events("INVESTIGATION_REPORTED")) == 1
    item_event = tool_events[0]
    assert item_event["paypal_item_id"] and item_event["paypal_batch_id"]
    assert item_event["actor"] == "ai:investigator" and item_event["case_id"] == CASE


def test_uat_inv_11_second_investigation_replaces_the_first(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    investigator.investigate(CASE)
    fake_llm.use("timeout")

    second = investigator.investigate(CASE)

    conn = db.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1
    finally:
        conn.close()
    stored = InvestigationReport.model_validate_json(_case_row()["report_json"])
    assert stored == second
    assert len(_events("INVESTIGATION_REPORTED")) == 2


def test_uat_inv_12_code_value_of_diversion_wins(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    fake_llm.use("honest", diversion_risk=False)

    investigator.investigate(CASE)

    stored = json.loads(_case_row()["report_json"])
    assert stored["diversion_risk"] is True


# ---- lane tests for the wiring -------------------------------------------

def test_b3_investigate_writes_case_state_event(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)

    investigator.investigate(CASE)

    [changed] = _events("CASE_STATE_CHANGED")
    assert json.loads(changed["payload_json"]) == {"from": "OPEN", "to": "INVESTIGATED",
                                                   "resolution": None}
    assert changed["milestone_id"] == "m1" and changed["payout_id"] == PAYOUT


def test_b3_investigate_unknown_case_raises_lookup_error(fake_paypal, fake_llm):
    scenario_a_case(fake_paypal)
    with pytest.raises(LookupError):
        investigator.investigate("case_ffffffff")
