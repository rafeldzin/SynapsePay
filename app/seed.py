"""Seeds the demo contract, its two milestones and the scenario (PRD 13.2, 13.4).

Everything here is seeded demo data and is labeled as such in the UI and README
(INV-11). This module writes rows only. It does not reset the database, create
the run_id, write the RESET event or commit: the reset flow owns those, and the
caller owns the transaction.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.contracts import Criterion, HardCheck

CONTRACT_ID = "c1"
BUYER_NAME = "Lumen Studio"
CONTRACTOR_NAME = "Dewi"
CURRENCY = "USD"

SCENARIOS = ("A", "B")
# PRD 13.4: scenario B loses the response of the first create call.
ARMED_FAULT = {"A": None, "B": "timeout_after_send"}


def _eq(fact_id: str, value: int | bool) -> HardCheck:
    return HardCheck(fact_id=fact_id, op="==", value=value)


# (id, seq, title, amount_cents, criteria) from the two tables in PRD 13.2.
MILESTONES = (
    ("m1", 1, "Clean the active customer list", 30000, (
        Criterion(id="m1.c1",
                  text="The file has the required columns and exactly 5,000 rows",
                  hard_checks=[_eq("schema_ok", True), _eq("row_count", 5000)]),
        Criterion(id="m1.c2",
                  text="No row has an empty or duplicate email",
                  hard_checks=[_eq("null_email_count", 0), _eq("duplicate_email_count", 0)]),
        Criterion(id="m1.c3",
                  text="The method note's stated counts agree with the delivered data"),
    )),
    ("m2", 2, "Deduplicate the legacy leads list", 20000, (
        Criterion(id="m2.c1",
                  text="The file has the required columns",
                  hard_checks=[_eq("schema_ok", True)]),
        Criterion(id="m2.c2",
                  text="No duplicate emails remain",
                  hard_checks=[_eq("duplicate_email_count", 0)]),
        Criterion(id="m2.c3",
                  text="The method report's stated counts agree with the delivered data"),
    )),
)


def scenario_a_message(payee_email_correct: str) -> str:
    """The contractor's not-paid message for scenario A (PRD 13.4).

    It is never inserted by the seed. The operator types it into the inbox,
    which the UI labels as simulated.
    """
    return (
        "Hi, the milestone 1 payment never arrived. "
        f"My PayPal email is {payee_email_correct}. Can you resend it there?"
    )


def seed_scenario(conn: sqlite3.Connection, scenario: str, *,
                  payee_email_correct: str, payee_email_typo: str,
                  now: str | None = None) -> None:
    """Inserts contract c1, milestones m1 and m2, and the scenario's meta keys.

    Expects the empty schema of PRD 5. `meta.armed_fault` is written only when
    the scenario arms a fault, so no row means no fault.
    """
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}, got {scenario!r}")
    correct = payee_email_correct.strip()
    typo = payee_email_typo.strip()
    if not correct or not typo:
        raise ValueError("DEMO_PAYEE_EMAIL_CORRECT and DEMO_PAYEE_EMAIL_TYPO must both be set")
    if correct.lower() == typo.lower():
        raise ValueError("DEMO_PAYEE_EMAIL_TYPO must differ from DEMO_PAYEE_EMAIL_CORRECT")

    ts = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payee_email = typo if scenario == "A" else correct

    conn.execute(
        "INSERT INTO contracts (id, buyer_name, contractor_name, payee_email, currency, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (CONTRACT_ID, BUYER_NAME, CONTRACTOR_NAME, payee_email, CURRENCY, ts),
    )
    for milestone_id, seq, title, amount_cents, criteria in MILESTONES:
        conn.execute(
            "INSERT INTO milestones (id, contract_id, seq, title, amount_cents, criteria_json, "
            "state, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'AWAITING_DELIVERABLE', ?)",
            (milestone_id, CONTRACT_ID, seq, title, amount_cents,
             json.dumps([c.model_dump() for c in criteria]), ts),
        )
    conn.execute("INSERT INTO meta (key, value) VALUES ('scenario', ?)", (scenario,))
    if ARMED_FAULT[scenario] is not None:
        conn.execute("INSERT INTO meta (key, value) VALUES ('armed_fault', ?)",
                     (ARMED_FAULT[scenario],))
