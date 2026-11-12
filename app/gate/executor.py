"""Executor (PRD 10): the only caller of paypal.writes (INV-2).

Flow for every write: (1) in one BEGIN IMMEDIATE transaction evaluate the gate, record
GATE_EVALUATED, and if allowed insert the write-ahead rows; (2) call PayPal exactly once;
(3) in a second transaction record the outcome. Nothing here retries a write on its own.
"""
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app import db, events
from app.cases import detect
from app.contracts import GateResult
from app.events import now_iso
from app.gate import policy
from app.paypal import writes
from app.paypal.http import PayPalAuthError, PayPalOutcomeUnknown, PayPalRejected

CREATING_MAX_AGE_S = 60


@dataclass
class ExecResult:
    gate: GateResult
    payout: dict | None = None
    case_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.gate.allowed


def _actor(approved_by: str | None) -> str:
    return f"human:{approved_by.strip()}" if (approved_by or "").strip() else "system"


def _payout_row(conn: sqlite3.Connection, payout_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone())


def _set_payout_state(conn: sqlite3.Connection, payout: sqlite3.Row | dict, to: str, cause: str,
                      **columns) -> None:
    """Updates the payout and writes PAYOUT_STATE_CHANGED. `columns` are extra columns to set."""
    sets = {"state": to, "updated_at": now_iso(), **columns}
    conn.execute(f"UPDATE payouts SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                 (*sets.values(), payout["id"]))
    events.append(conn, "PAYOUT_STATE_CHANGED", "system", milestone_id=payout["milestone_id"],
                  payout_id=payout["id"], paypal_batch_id=columns.get("payout_batch_id") or payout["payout_batch_id"],
                  payload={"from": payout["state"], "to": to, "cause": cause})


def _milestone_back_to_verdict(conn: sqlite3.Connection, milestone_id: str) -> None:
    """The payout died (RETURNED or FAILED): the milestone returns to its verdict (PRD 6.1)."""
    m = conn.execute("SELECT state, verdict FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if m["state"] == "PAYOUT_IN_FLIGHT" and m["verdict"]:
        conn.execute("UPDATE milestones SET state = ?, updated_at = ? WHERE id = ?",
                     (m["verdict"], now_iso(), milestone_id))
        events.append(conn, "MILESTONE_STATE_CHANGED", "system", milestone_id=milestone_id,
                      payload={"from": "PAYOUT_IN_FLIGHT", "to": m["verdict"]})


def _record_rejected(conn: sqlite3.Connection, payout: sqlite3.Row | dict, exc: PayPalRejected,
                     cause: str) -> None:
    events.append(conn, "PAYPAL_REJECTED", "paypal", milestone_id=payout["milestone_id"],
                  payout_id=payout["id"],
                  payload={"name": exc.name, "debug_id": exc.debug_id, "status_code": exc.status_code})
    _set_payout_state(conn, payout, "FAILED", cause)
    _milestone_back_to_verdict(conn, payout["milestone_id"])


def _call_create(payout: dict):
    """The one PayPal create call. Recipient, amount and key come from the payout row (INV-6)."""
    return writes.create_payout(
        sender_batch_id=payout["sender_batch_id"], receiver_email=payout["recipient_email"],
        amount_cents=payout["amount_cents"], currency=payout["currency"],
        note=f"Milestone {payout['milestone_id']}")


# ---- PAY ----------------------------------------------------------------------

def pay(milestone_id: str, *, approved_by: str | None, confirm: bool | None,
        expected_amount_cents: int | None, override_reason: str | None = None) -> ExecResult:
    """Gated PAY. Raises LookupError for an unknown milestone. A refusal returns allowed=False."""
    conn = db.connect()
    try:
        with db.tx(conn):
            gate = policy.evaluate_pay(conn, milestone_id, approved_by=approved_by, confirm=confirm,
                                       expected_amount_cents=expected_amount_cents,
                                       override_reason=override_reason)
            m = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
            events.append(conn, "GATE_EVALUATED", _actor(approved_by), contract_id=m["contract_id"],
                          milestone_id=milestone_id, payload=gate.model_dump())
            if not gate.allowed:
                return ExecResult(gate=gate)

            contract = conn.execute("SELECT * FROM contracts WHERE id = ?", (m["contract_id"],)).fetchone()
            attempt = policy.next_attempt(conn, milestone_id)
            batch_key = policy.sender_batch_id(policy._run_id(conn), milestone_id, attempt)
            payout_id, ts = f"po_{secrets.token_hex(4)}", now_iso()

            if m["state"] in ("FAIL", "NEEDS_REVIEW"):
                conn.execute("UPDATE milestones SET override_reason = ?, override_by = ? WHERE id = ?",
                             (override_reason.strip(), approved_by.strip(), milestone_id))
                events.append(conn, "OVERRIDE_RECORDED", _actor(approved_by), contract_id=m["contract_id"],
                              milestone_id=milestone_id,
                              payload={"reason": override_reason.strip(), "approved_by": approved_by.strip(),
                                       "verdict": m["state"]})
            conn.execute(
                "INSERT INTO payouts (id, milestone_id, attempt, sender_batch_id, recipient_email, "
                "amount_cents, currency, state, approved_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'CREATING', ?, ?, ?)",
                (payout_id, milestone_id, attempt, batch_key, contract["payee_email"], m["amount_cents"],
                 contract["currency"], approved_by.strip(), ts, ts))
            events.append(conn, "PAYOUT_CREATING", _actor(approved_by), contract_id=m["contract_id"],
                          milestone_id=milestone_id, payout_id=payout_id,
                          payload={"attempt": attempt, "sender_batch_id": batch_key,
                                   "amount_cents": m["amount_cents"], "recipient_email": contract["payee_email"]})
            conn.execute("UPDATE milestones SET state = 'PAYOUT_IN_FLIGHT', updated_at = ? WHERE id = ?",
                         (ts, milestone_id))
            events.append(conn, "MILESTONE_STATE_CHANGED", "system", contract_id=m["contract_id"],
                          milestone_id=milestone_id, payload={"from": m["state"], "to": "PAYOUT_IN_FLIGHT"})
            payout = _payout_row(conn, payout_id)

        # PayPal is called outside any transaction. The CREATING row is already committed.
        outcome, error = None, None
        try:
            outcome = _call_create(payout)
        except PayPalOutcomeUnknown as exc:
            error = exc
        except PayPalRejected as exc:
            error = exc
        except PayPalAuthError as exc:  # no request reached PayPal
            error = PayPalRejected("PAYPAL_AUTH_ERROR", None, {"message": str(exc)})

        with db.tx(conn):
            row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
            contract_id = m["contract_id"]
            if outcome is not None and outcome.outcome == "CREATED":
                events.append(conn, "PAYPAL_PAYOUT_CREATED", "paypal", contract_id=contract_id,
                              milestone_id=milestone_id, payout_id=payout_id,
                              paypal_batch_id=outcome.payout_batch_id,
                              payload={"on_retry": False, "batch_status": outcome.batch_status,
                                       "sender_batch_id": row["sender_batch_id"]})
                _set_payout_state(conn, row, "SENT", "paypal_accepted",
                                  payout_batch_id=outcome.payout_batch_id, batch_status=outcome.batch_status)
            elif outcome is not None:  # ORIGINAL_FOUND on a first send should not happen (PRD 8.3)
                events.append(conn, "PAYPAL_ORIGINAL_FOUND", "paypal", contract_id=contract_id,
                              milestone_id=milestone_id, payout_id=payout_id,
                              paypal_batch_id=outcome.payout_batch_id,
                              payload={"sender_batch_id": row["sender_batch_id"], "on_first_send": True})
                _set_payout_state(conn, row, "UNKNOWN", "unexpected_duplicate",
                                  payout_batch_id=outcome.payout_batch_id)
                detect.open_case(conn, payout_id, "D4")
            elif isinstance(error, PayPalOutcomeUnknown):
                events.append(conn, "PAYPAL_OUTCOME_UNKNOWN", "paypal", contract_id=contract_id,
                              milestone_id=milestone_id, payout_id=payout_id, payload={"detail": str(error)})
                _set_payout_state(conn, row, "UNKNOWN", "outcome_unknown")
            else:
                _record_rejected(conn, row, error, "paypal_rejected")
            final = _payout_row(conn, payout_id)
        return ExecResult(gate=gate, payout=final)
    finally:
        conn.close()


# ---- RETRY --------------------------------------------------------------------

def retry(payout_id: str, *, approved_by: str | None, confirm: bool | None) -> ExecResult:
    """Gated RETRY of an UNKNOWN payout with the same sender_batch_id (PRD 6.2, 8.3).

    A batch id coming back, by a 2xx or by a duplicate rejection, keeps the payout UNKNOWN and
    opens a D4 case: the ledger moves only through a gated RECONCILE.
    """
    conn = db.connect()
    try:
        with db.tx(conn):
            gate = policy.evaluate_retry(conn, payout_id, approved_by=approved_by, confirm=confirm)
            payout = _payout_row(conn, payout_id)
            contract_id = conn.execute("SELECT contract_id FROM milestones WHERE id = ?",
                                       (payout["milestone_id"],)).fetchone()["contract_id"]
            events.append(conn, "GATE_EVALUATED", _actor(approved_by), contract_id=contract_id,
                          milestone_id=payout["milestone_id"], payout_id=payout_id, payload=gate.model_dump())
            if not gate.allowed:
                return ExecResult(gate=gate, payout=payout)

        outcome, error = None, None
        try:
            outcome = _call_create(payout)
        except (PayPalOutcomeUnknown, PayPalRejected) as exc:
            error = exc
        except PayPalAuthError as exc:
            error = PayPalRejected("PAYPAL_AUTH_ERROR", None, {"message": str(exc)})

        case_id = None
        with db.tx(conn):
            row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
            if outcome is not None:
                found = outcome.outcome == "ORIGINAL_FOUND"
                events.append(conn, "PAYPAL_ORIGINAL_FOUND" if found else "PAYPAL_PAYOUT_CREATED", "paypal",
                              contract_id=contract_id, milestone_id=row["milestone_id"], payout_id=payout_id,
                              paypal_batch_id=outcome.payout_batch_id,
                              payload={"on_retry": True, "sender_batch_id": row["sender_batch_id"],
                                       "batch_status": outcome.batch_status})
                conn.execute("UPDATE payouts SET payout_batch_id = ?, batch_status = ?, updated_at = ? "
                             "WHERE id = ?", (outcome.payout_batch_id, outcome.batch_status, now_iso(), payout_id))
                case_id = detect.open_case(conn, payout_id, "D4")
            elif isinstance(error, PayPalOutcomeUnknown):
                events.append(conn, "PAYPAL_OUTCOME_UNKNOWN", "paypal", contract_id=contract_id,
                              milestone_id=row["milestone_id"], payout_id=payout_id,
                              payload={"detail": str(error), "on_retry": True})
            else:
                _record_rejected(conn, row, error, "retry_rejected")
            final = _payout_row(conn, payout_id)
        return ExecResult(gate=gate, payout=final, case_id=case_id)
    finally:
        conn.close()


# ---- startup recovery ---------------------------------------------------------

def recover_creating(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """A CREATING row older than 60 s means the process died before PayPal answered: it becomes
    UNKNOWN (PRD 6.2). Returns how many rows were moved."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(seconds=CREATING_MAX_AGE_S)
              ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx(conn):
        stale = conn.execute("SELECT * FROM payouts WHERE state = 'CREATING' AND created_at < ?",
                             (cutoff,)).fetchall()
        for row in stale:
            _set_payout_state(conn, row, "UNKNOWN", "startup_recovery")
    return len(stale)
