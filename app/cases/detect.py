"""Case detection (PRD 11.1). A4 adds the rules D1 to D3 and the diversion flag; open_case is shared."""
import secrets
import sqlite3

from app import events
from app.events import now_iso


def open_case(conn: sqlite3.Connection, payout_id: str, trigger_rule: str, *, actor: str = "system") -> str:
    """Opens a case for the payout, or only logs an event if one is already open (PRD 11.1).

    Runs inside the caller's transaction. Returns the case id.
    """
    payout = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    contract_id = conn.execute("SELECT contract_id FROM milestones WHERE id = ?",
                               (payout["milestone_id"],)).fetchone()["contract_id"]
    existing = conn.execute("SELECT id FROM cases WHERE payout_id = ? AND state != 'RESOLVED'",
                            (payout_id,)).fetchone()
    if existing:
        events.append(conn, "CASE_OPENED", actor, contract_id=contract_id,
                      milestone_id=payout["milestone_id"], payout_id=payout_id, case_id=existing["id"],
                      payload={"trigger_rule": trigger_rule, "already_open": True})
        return existing["id"]
    case_id = f"case_{secrets.token_hex(4)}"
    ts = now_iso()
    conn.execute(
        "INSERT INTO cases (id, payout_id, milestone_id, trigger_rule, state, opened_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'OPEN', ?, ?)",
        (case_id, payout_id, payout["milestone_id"], trigger_rule, ts, ts))
    events.append(conn, "CASE_OPENED", actor, contract_id=contract_id, milestone_id=payout["milestone_id"],
                  payout_id=payout_id, case_id=case_id, payload={"trigger_rule": trigger_rule})
    return case_id
