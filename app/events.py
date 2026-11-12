"""Append-only event ledger (PRD 5.1, INV-7). The caller owns the transaction."""
import json
import sqlite3
from datetime import datetime, timezone

EVENT_TYPES = frozenset({
    "RESET", "DELIVERABLE_SUBMITTED", "FACTS_COMPUTED", "INJECTION_FLAGGED",
    "VERDICT_RECORDED", "OVERRIDE_RECORDED", "GATE_EVALUATED", "PAYOUT_CREATING",
    "PAYPAL_PAYOUT_CREATED", "PAYPAL_OUTCOME_UNKNOWN", "PAYPAL_REJECTED",
    "PAYPAL_ORIGINAL_FOUND", "PAYPAL_STATUS_READ", "PAYPAL_ITEM_CANCELLED",
    "PAYOUT_STATE_CHANGED", "MILESTONE_STATE_CHANGED", "MESSAGE_RECEIVED",
    "CASE_OPENED", "DIVERSION_RISK_FLAGGED", "INVESTIGATION_TOOL_CALL",
    "INVESTIGATION_REPORTED", "PAYEE_EMAIL_CHANGED", "CASE_STATE_CHANGED",
    "SYNC_ERROR", "FAULT_ARMED",
})


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(conn: sqlite3.Connection, type: str, actor: str, *,
           contract_id: str | None = None, milestone_id: str | None = None,
           payout_id: str | None = None, case_id: str | None = None,
           paypal_batch_id: str | None = None, paypal_item_id: str | None = None,
           payload: dict | None = None) -> int:
    """Inserts one event and returns its id. Rejects a type that is not in PRD 5.1."""
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")
    cur = conn.execute(
        "INSERT INTO events (ts, type, actor, contract_id, milestone_id, payout_id, case_id, "
        "paypal_batch_id, paypal_item_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), type, actor, contract_id, milestone_id, payout_id, case_id,
         paypal_batch_id, paypal_item_id, json.dumps(payload or {}, default=str)),
    )
    return cur.lastrowid


def list_events(conn: sqlite3.Connection, *, milestone_id: str | None = None,
                payout_id: str | None = None, case_id: str | None = None,
                limit: int = 300) -> list[dict]:
    """The newest `limit` events matching the filters, oldest first. payload_json is parsed."""
    where, args = [], []
    for column, value in (("milestone_id", milestone_id), ("payout_id", payout_id),
                          ("case_id", case_id)):
        if value is not None:
            where.append(f"{column} = ?")
            args.append(value)
    sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    out = []
    for row in reversed(rows):
        event = dict(row)
        event["payload"] = json.loads(event.pop("payload_json"))
        out.append(event)
    return out
