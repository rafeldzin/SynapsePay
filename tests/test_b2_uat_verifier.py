"""UAT 5.1 rows VER-04 to VER-12 on Dev A's db.py and events.py.

"Submit" is `verifier.submit_fixture()`, the function the deliverable route
(POST /api/milestones/{id}/deliverable, package A3) calls. These rows assert the
verdict, the stored milestone and the events, none of which depend on HTTP.
UAT-VER-13 (HTTP 409 while in flight or paid) and SEC-11 (upload size) need the
route itself and are written with it.
"""
import json

import pytest

from app import db, seed
from app.verify import verifier
from tests.conftest import CORRECT_EMAIL, TYPO_EMAIL

FACT_IDS = {"sha256", "row_count", "schema_ok", "missing_columns",
            "null_email_count", "duplicate_email_count"}


@pytest.fixture
def scenario_a():
    """Empty schema seeded with scenario A (the reset flow of A6 does the same)."""
    conn = db.connect()
    try:
        db.reset(conn) if db.schema_exists(conn) else db.init_schema(conn)
        with db.tx(conn):
            conn.execute("INSERT INTO meta (key, value) VALUES ('run_id', '7f3a9c1e')")
            seed.seed_scenario(conn, "A", payee_email_correct=CORRECT_EMAIL,
                               payee_email_typo=TYPO_EMAIL)
    finally:
        conn.close()


def _submit(milestone_id, fixture):
    return verifier.submit_fixture(milestone_id, fixture, submitted_by="dewi")


def _milestone(milestone_id):
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone())
    finally:
        conn.close()


def _event_types(milestone_id):
    conn = db.connect()
    try:
        return [r["type"] for r in conn.execute(
            "SELECT type FROM events WHERE milestone_id = ? ORDER BY id", (milestone_id,))]
    finally:
        conn.close()


def _by_id(result):
    return {c.criterion_id: c for c in result.criteria}


def test_uat_ver_04_submit_m1_pass_honest(scenario_a, fake_llm):
    result = _submit("m1", "m1_pass")

    assert result.overall == "PASS"
    assert len(result.criteria) == 3
    for criterion in result.criteria:
        assert criterion.verdict == "PASS"
        assert criterion.cited_fact_ids and set(criterion.cited_fact_ids) <= FACT_IDS
    assert _milestone("m1")["state"] == "PASS"
    types = _event_types("m1")
    wanted = ["DELIVERABLE_SUBMITTED", "FACTS_COMPUTED", "VERDICT_RECORDED"]
    assert [t for t in types if t in wanted] == wanted


def test_uat_ver_05_submit_m2_fail_clean_honest(scenario_a, fake_llm):
    result = _submit("m2", "m2_fail_clean")
    c2 = _by_id(result)["m2.c2"]

    assert result.overall == "FAIL"
    assert c2.verdict == "FAIL" and "duplicate_email_count" in c2.cited_fact_ids
    assert any(c.fact_id == "duplicate_email_count" and c.fact_value == 37
               for c in result.contradicted_claims)
    assert result.injection_detected is False
    assert result.note_to_contractor.strip()


def test_uat_ver_06_all_pass_cited_cannot_loosen_hard_check(scenario_a, fake_llm):
    fake_llm.use("all_pass_cited")

    result = _submit("m2", "m2_fail_clean")

    assert result.overall == "FAIL"
    assert _by_id(result)["m2.c2"].downgraded_by == "hard_check"


def test_uat_ver_07_all_pass_no_citations_coverage_rule(scenario_a, fake_llm):
    fake_llm.use("all_pass_no_citations")

    result = _submit("m1", "m1_pass")

    assert result.overall == "NEEDS_REVIEW"
    assert [c.downgraded_by for c in result.criteria] == ["coverage_rule"] * 3


def test_uat_ver_08_unknown_fact_ids_coverage_rule(scenario_a, fake_llm):
    fake_llm.use("unknown_fact_ids")

    result = _submit("m1", "m1_pass")

    assert result.overall == "NEEDS_REVIEW"
    assert {c.downgraded_by for c in result.criteria} == {"coverage_rule"}


def test_uat_ver_09_missing_criterion(scenario_a, fake_llm):
    fake_llm.use("missing_criterion")

    result = _submit("m1", "m1_pass")

    assert "NEEDS_REVIEW" in [c.verdict for c in result.criteria]
    assert result.overall == "NEEDS_REVIEW"


def test_uat_ver_10_llm_failure_abstains(scenario_a, fake_llm):
    for script in ("invalid_json", "timeout"):
        fake_llm.use(script)

        result = _submit("m1", "m1_pass")

        assert result.overall == "NEEDS_REVIEW" and result.overall != "PASS"
        assert result.llm_used is False
        assert {c.downgraded_by for c in result.criteria} == {"llm_error"}
        assert _milestone("m1")["state"] == "NEEDS_REVIEW"


def test_uat_ver_11_hard_checks_run_without_the_model(scenario_a, fake_llm):
    fake_llm.use("timeout")

    result = _submit("m2", "m2_fail_clean")

    assert result.overall == "FAIL"
    assert _by_id(result)["m2.c2"].verdict == "FAIL"


def test_uat_ver_12_new_submission_replaces_the_verdict(scenario_a, fake_llm):
    first = _submit("m2", "m2_fail_clean")
    second = _submit("m2", "m2_good_injected")

    stored = _milestone("m2")
    verification = json.loads(stored["verification_json"])
    conn = db.connect()
    try:
        newest = conn.execute("SELECT sha256 FROM deliverables WHERE milestone_id = 'm2' "
                              "ORDER BY id DESC LIMIT 1").fetchone()["sha256"]
    finally:
        conn.close()
    assert first.overall == "FAIL" and second.overall == "NEEDS_REVIEW"
    assert stored["verdict"] == stored["state"] == "NEEDS_REVIEW"
    assert verification["deliverable_sha256"] == newest == second.deliverable_sha256


# ---- lane tests for the wiring -------------------------------------------

def test_b2_submit_stores_deliverable_and_verdict_events(scenario_a, fake_llm):
    _submit("m2", "m2_fail_injected")

    conn = db.connect()
    try:
        deliverable = conn.execute("SELECT * FROM deliverables").fetchone()
        events = [dict(r) for r in conn.execute(
            "SELECT type, actor, contract_id, payload_json FROM events ORDER BY id")]
    finally:
        conn.close()
    assert deliverable["csv_name"] == "m2_fail.csv" and deliverable["submitted_by"] == "dewi"
    assert [e["type"] for e in events] == ["DELIVERABLE_SUBMITTED", "FACTS_COMPUTED",
                                           "INJECTION_FLAGGED", "VERDICT_RECORDED",
                                           "MILESTONE_STATE_CHANGED"]
    assert events[0]["actor"] == "human:dewi" and events[0]["contract_id"] == "c1"
    assert json.loads(events[0]["payload_json"])["sha256"] == deliverable["sha256"]
    verdict = json.loads(events[3]["payload_json"])
    assert verdict["overall"] == "FAIL"
    assert json.loads(events[4]["payload_json"]) == {"from": "AWAITING_DELIVERABLE", "to": "FAIL"}


def test_b2_new_submission_clears_an_override(scenario_a, fake_llm):
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("UPDATE milestones SET override_reason = 'approved by phone call', "
                         "override_by = 'ops-lead' WHERE id = 'm2'")
    finally:
        conn.close()

    _submit("m2", "m2_fail_clean")

    stored = _milestone("m2")
    assert stored["override_reason"] is None and stored["override_by"] is None


@pytest.mark.parametrize("state", ["PAYOUT_IN_FLIGHT", "PAID"])
def test_b2_submit_while_in_flight_or_paid_is_refused_and_keeps_the_verdict(
        scenario_a, fake_llm, state):
    _submit("m1", "m1_pass")
    conn = db.connect()
    try:
        with db.tx(conn):
            conn.execute("UPDATE milestones SET state = ? WHERE id = 'm1'", (state,))
    finally:
        conn.close()
    before = _milestone("m1")

    with pytest.raises(verifier.MilestoneLocked):
        _submit("m1", "m1_pass")

    assert _milestone("m1") == before
    assert len(fake_llm.calls) == 1


def test_b2_unknown_milestone_or_fixture_raises_lookup_error(scenario_a, fake_llm):
    with pytest.raises(LookupError):
        _submit("m9", "m1_pass")
    with pytest.raises(LookupError):
        _submit("m1", "no_such_fixture")


def test_b2_submit_raw_files(scenario_a, fake_llm):
    result = verifier.submit("m1", csv_name="upload.csv",
                             csv_bytes=b"customer_id,name,email,country,signup_date\n",
                             report_text="Rows delivered: 0", submitted_by="dewi")
    assert result.overall == "FAIL"  # m1.c1 needs 5,000 rows
