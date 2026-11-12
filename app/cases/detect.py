"""Case detection (PRD 11.1). Deterministic rules D1 to D4 and the diversion flag. No AI is spent here."""
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app import events
from app.config import get_settings
from app.events import now_iso

LIVE_STATES = ("CREATING", "UNKNOWN", "SENT", "UNCLAIMED", "PAID")
NOT_PAID_PATTERNS = re.compile(
    r"not paid|never arrived|never received|haven't received|didn't receive|did not receive"
    r"|where is my payment|belum (di)?terima|belum masuk", re.IGNORECASE)
MILESTONE_REF = re.compile(r"milestone\s*(\d+)", re.IGNORECASE)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")


def _contract_id(conn: sqlite3.Connection, milestone_id: str) -> str:
    return conn.execute("SELECT contract_id FROM milestones WHERE id = ?", (milestone_id,)).fetchone()["contract_id"]


def open_case(conn: sqlite3.Connection, payout_id: str, trigger_rule: str, *, actor: str = "system") -> str:
    """Opens a case for the payout, or only logs an event if one is already open (PRD 11.1).

    Runs inside the caller's transaction. Returns the case id.
    """
    payout = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    contract_id = _contract_id(conn, payout["milestone_id"])
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


def _has_open_case(conn: sqlite3.Connection, payout_id: str) -> bool:
    return conn.execute("SELECT 1 FROM cases WHERE payout_id = ? AND state != 'RESOLVED'",
                        (payout_id,)).fetchone() is not None


# ---- diversion flag (code, not the model) ----------------------------------------

def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def diversion_risk(body: str, from_email: str, payee_email: str) -> tuple[bool, str | None, int | None]:
    """(flagged, requested_email, edit_distance). Every address in the body and the sender that
    differs from the payee on file, case-insensitively, is a diversion risk. Body addresses come first."""
    payee = payee_email.strip().lower()
    for address in [*EMAIL.findall(body), from_email]:
        address = address.strip().lower()
        if address and address != payee:
            return True, address, edit_distance(address, payee)
    return False, None, None


def _flag_diversion(conn: sqlite3.Connection, case_id: str, payout: sqlite3.Row, payee: str,
                    requested: str, distance: int) -> None:
    conn.execute("UPDATE cases SET diversion_risk = 1, requested_email = ?, updated_at = ? WHERE id = ?",
                 (requested, now_iso(), case_id))
    events.append(conn, "DIVERSION_RISK_FLAGGED", "system", contract_id=_contract_id(conn, payout["milestone_id"]),
                  milestone_id=payout["milestone_id"], payout_id=payout["id"], case_id=case_id,
                  payload={"requested_email": requested, "current_email": payee, "edit_distance": distance})


# ---- D1: an inbound "not paid" message ---------------------------------------------

def on_message(conn: sqlite3.Connection, contract_id: str, from_email: str, body: str) -> dict:
    """Stores an inbound contractor message and applies rule D1. Runs inside the caller's transaction.

    Returns {message, case_id, diversion_risk}. Raises LookupError for an unknown contract.
    """
    contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    if contract is None:
        raise LookupError(f"unknown contract {contract_id!r}")
    message_id, ts = f"msg_{secrets.token_hex(4)}", now_iso()
    conn.execute("INSERT INTO messages (id, contract_id, from_email, body, created_at) VALUES (?, ?, ?, ?, ?)",
                 (message_id, contract_id, from_email, body, ts))
    events.append(conn, "MESSAGE_RECEIVED", "system", contract_id=contract_id,
                  payload={"message_id": message_id, "from_email": from_email})
    message = {"id": message_id, "contract_id": contract_id, "from_email": from_email, "body": body,
               "created_at": ts}

    flagged, requested, distance = diversion_risk(body, from_email, contract["payee_email"])
    result = {"message": message, "case_id": None, "diversion_risk": flagged}
    if not NOT_PAID_PATTERNS.search(body):
        return result

    live = conn.execute(
        f"SELECT p.*, m.seq FROM payouts p JOIN milestones m ON m.id = p.milestone_id "
        f"WHERE m.contract_id = ? AND p.state IN ({','.join('?' * len(LIVE_STATES))}) "
        f"ORDER BY p.created_at DESC, p.attempt DESC", (contract_id, *LIVE_STATES)).fetchall()
    if not live:
        return result
    named = MILESTONE_REF.search(body)
    payout = next((p for p in live if named and p["seq"] == int(named.group(1))), live[0])

    case_id = open_case(conn, payout["id"], "D1")
    if flagged:  # also on an already open case: a payee change request is a safety signal
        _flag_diversion(conn, case_id, payout, contract["payee_email"], requested, distance)
    result["case_id"] = case_id
    return result


# ---- D2 and D3: status based --------------------------------------------------------

def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check_unclaimed(conn: sqlite3.Connection, payout_id: str, *, now: datetime | None = None) -> str | None:
    """D2: a payout UNCLAIMED for longer than UNCLAIMED_GRACE_SECONDS opens a case by itself."""
    payout = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    if payout is None or payout["state"] != "UNCLAIMED" or _has_open_case(conn, payout_id):
        return None
    grace = timedelta(seconds=get_settings().unclaimed_grace_seconds)
    if (now or datetime.now(timezone.utc)) - _parse(payout["updated_at"]) <= grace:
        return None
    return open_case(conn, payout_id, "D2")


def run_rules(conn: sqlite3.Connection, *, now: datetime | None = None) -> list[str]:
    """Runs the time based rule (D2) over every UNCLAIMED payout. Returns the ids of cases opened."""
    opened = []
    for row in conn.execute("SELECT id FROM payouts WHERE state = 'UNCLAIMED'").fetchall():
        case_id = check_unclaimed(conn, row["id"], now=now)
        if case_id:
            opened.append(case_id)
    return opened


def after_sync(conn: sqlite3.Connection, payout_id: str, previous_state: str,
               *, now: datetime | None = None) -> None:
    """Rules that run after a status sync (PRD 8.5): resolution by payment, D3 and D2."""
    payout = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    state = payout["state"]
    contract_id = _contract_id(conn, payout["milestone_id"])
    if state == "PAID":  # the money arrived: any open case on it is resolved
        for case in conn.execute("SELECT id, state FROM cases WHERE payout_id = ? AND state != 'RESOLVED'",
                                 (payout_id,)).fetchall():
            conn.execute("UPDATE cases SET state = 'RESOLVED', resolution = 'PAID_AFTER_WAIT', updated_at = ? "
                         "WHERE id = ?", (now_iso(), case["id"]))
            events.append(conn, "CASE_STATE_CHANGED", "system", contract_id=contract_id,
                          milestone_id=payout["milestone_id"], payout_id=payout_id, case_id=case["id"],
                          payload={"from": case["state"], "to": "RESOLVED", "resolution": "PAID_AFTER_WAIT"})
    elif state in ("RETURNED", "FAILED") and previous_state != state:
        if not _has_open_case(conn, payout_id):  # D3: PayPal moved it and the app did not cause it
            open_case(conn, payout_id, "D3")
    elif state == "UNCLAIMED":
        check_unclaimed(conn, payout_id, now=now)
