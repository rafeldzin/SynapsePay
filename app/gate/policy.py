"""Policy gate (PRD 10): one pure check function per action.

Reads only the database (and, for later actions, a fresh PayPal read). It writes nothing,
not even events: gate/executor.py records GATE_EVALUATED and everything else. It never imports
paypal.writes or llm.
"""
import json
import sqlite3
from urllib.parse import urlparse

from app.config import SANDBOX_HOST, get_settings
from app.contracts import BatchView, GateCheck, GateResult
from app.events import now_iso
from app.paypal import reads

LIVE_STATES = ("CREATING", "UNKNOWN", "SENT", "UNCLAIMED", "PAID")
OVERRIDE_MIN_CHARS = 20


def _milestone(conn: sqlite3.Connection, milestone_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if row is None:
        raise LookupError(f"unknown milestone {milestone_id!r}")
    return row


def _run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'run_id'").fetchone()
    return row["value"] if row else None


def next_attempt(conn: sqlite3.Connection, milestone_id: str) -> int:
    """Attempt grows only when a new payout row is created (PRD 6.2)."""
    row = conn.execute("SELECT MAX(attempt) AS a FROM payouts WHERE milestone_id = ?",
                       (milestone_id,)).fetchone()
    return (row["a"] or 0) + 1


def sender_batch_id(run_id: str, milestone_id: str, attempt: int) -> str:
    """PRD 8.3: SP-{run_id}-{milestone_id}-A{attempt}."""
    return f"SP-{run_id}-{milestone_id}-A{attempt}"


def _result(action: str, checks: list[GateCheck], preview: bool) -> GateResult:
    # In a preview the human check is None (not yet clicked) and is ignored.
    allowed = all(c.ok for c in checks if not (preview and c.ok is None))
    return GateResult(action=action, allowed=allowed, preview=preview, checks=checks,
                      evaluated_at=now_iso())


def _human_approval(approved_by: str | None, confirm: bool | None, preview: bool,
                    check_id: str) -> GateCheck:
    if preview:
        return GateCheck(id=check_id, name="human_approval", ok=None, detail="pending the human click")
    ok = bool((approved_by or "").strip()) and confirm is True
    return GateCheck(id=check_id, name="human_approval", ok=ok,
                     detail=f"approved_by={approved_by!r}, confirm={confirm!r}")


def _sandbox_only(check_id: str) -> GateCheck:
    host = urlparse(get_settings().paypal_base_url).hostname
    return GateCheck(id=check_id, name="sandbox_only", ok=host == SANDBOX_HOST,
                     detail=f"PayPal host is {host}")


def evaluate_pay(conn: sqlite3.Connection, milestone_id: str, *, approved_by: str | None = None,
                 confirm: bool | None = None, expected_amount_cents: int | None = None,
                 override_reason: str | None = None, preview: bool = False) -> GateResult:
    """PAY-1 to PAY-8. Recipient and amount come from the database (INV-6), never the request."""
    m = _milestone(conn, milestone_id)
    reason = (override_reason or "").strip()

    if m["state"] == "PASS":
        verdict = GateCheck(id="PAY-1", name="verdict_ok", ok=True, detail="milestone verdict is PASS")
    elif m["state"] in ("FAIL", "NEEDS_REVIEW") and len(reason) >= OVERRIDE_MIN_CHARS:
        verdict = GateCheck(id="PAY-1", name="verdict_ok", ok=True,
                            detail=f"milestone is {m['state']}, override reason given ({len(reason)} chars)")
    elif m["state"] in ("FAIL", "NEEDS_REVIEW"):
        verdict = GateCheck(id="PAY-1", name="verdict_ok", ok=False,
                            detail=f"milestone is {m['state']} and no override reason of at least "
                                   f"{OVERRIDE_MIN_CHARS} characters was given")
    else:
        verdict = GateCheck(id="PAY-1", name="verdict_ok", ok=False,
                            detail=f"milestone state is {m['state']}, not PASS")

    verification = json.loads(m["verification_json"]) if m["verification_json"] else None
    newest = conn.execute("SELECT sha256 FROM deliverables WHERE milestone_id = ? ORDER BY id DESC LIMIT 1",
                          (milestone_id,)).fetchone()
    verified_sha = verification.get("deliverable_sha256") if verification else None
    current = GateCheck(
        id="PAY-2", name="verdict_current",
        ok=bool(newest) and verified_sha == newest["sha256"],
        detail=("no deliverable has been verified" if not newest or not verification
                else "verdict covers the newest deliverable" if verified_sha == newest["sha256"]
                else "a newer deliverable has not been verified"))

    amount = GateCheck(
        id="PAY-3", name="amount_matches", ok=expected_amount_cents == m["amount_cents"],
        detail=f"expected {expected_amount_cents!r}, milestone is {m['amount_cents']} cents")

    live = conn.execute(
        f"SELECT id, state FROM payouts WHERE milestone_id = ? AND state IN ({','.join('?' * len(LIVE_STATES))})",
        (milestone_id, *LIVE_STATES)).fetchone()
    no_live = GateCheck(id="PAY-4", name="no_live_payout", ok=live is None,
                        detail="no live payout" if live is None
                        else f"payout {live['id']} is live ({live['state']})")

    open_case = conn.execute("SELECT id, state FROM cases WHERE milestone_id = ? AND state != 'RESOLVED'",
                             (milestone_id,)).fetchone()
    no_case = GateCheck(id="PAY-5", name="no_open_case", ok=open_case is None,
                        detail="no open case" if open_case is None
                        else f"case {open_case['id']} is {open_case['state']}")

    run_id = _run_id(conn)
    if run_id is None:
        key = GateCheck(id="PAY-6", name="idempotency_key", ok=False, detail="no run_id: the demo was never reset")
    else:
        batch_id = sender_batch_id(run_id, milestone_id, next_attempt(conn, milestone_id))
        used = conn.execute("SELECT 1 FROM payouts WHERE sender_batch_id = ?", (batch_id,)).fetchone()
        key = GateCheck(id="PAY-6", name="idempotency_key", ok=used is None,
                        detail=f"{batch_id} is {'already used' if used else 'unused'}")

    checks = [verdict, current, amount, no_live, no_case, key,
              _human_approval(approved_by, confirm, preview, "PAY-7"), _sandbox_only("PAY-8")]
    return _result("PAY", checks, preview)


def read_fresh_batch(payout) -> BatchView | Exception | None:
    """A PayPal read made during this gate evaluation, never a stored status (PRD 10).

    Returns the BatchView, the exception if the read failed (the gate then fails the check with
    that detail), or None when the payout has no batch id yet. Taken before the write transaction
    so no network call runs under the write lock.
    """
    if not payout["payout_batch_id"]:
        return None
    try:
        return reads.get_batch(payout["payout_batch_id"])
    except Exception as exc:  # any failed read is "no fresh evidence"
        return exc


def evaluate_reconcile(conn: sqlite3.Connection, payout_id: str, *, fresh: BatchView | Exception | None,
                       approved_by: str | None = None, confirm: bool | None = None,
                       preview: bool = False) -> GateResult:
    """RC-1 to RC-5. RECONCILE makes no PayPal write: it only moves the ledger to PAID (PRD 10)."""
    p = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    if p is None:
        raise LookupError(f"unknown payout {payout_id!r}")
    ok1 = p["state"] == "UNKNOWN" and bool(p["payout_batch_id"])
    checks = [GateCheck(id="RC-1", name="state_unknown_with_batch", ok=ok1,
                        detail=f"payout state is {p['state']}, batch id "
                               f"{p['payout_batch_id'] or 'not stored'}")]

    view = fresh if isinstance(fresh, BatchView) else None
    no_read = ("no batch id to read" if fresh is None
               else f"the fresh PayPal read failed: {type(fresh).__name__}: {fresh}")
    item = view.items[0] if view and len(view.items) == 1 else None
    if view is None:
        checks.append(GateCheck(id="RC-2", name="same_batch", ok=False, detail=no_read))
    else:
        checks.append(GateCheck(
            id="RC-2", name="same_batch",
            ok=view.sender_batch_id == p["sender_batch_id"] and len(view.items) == 1,
            detail=f"PayPal batch {view.payout_batch_id} has sender_batch_id {view.sender_batch_id!r} and "
                   f"{len(view.items)} item(s); the ledger expects {p['sender_batch_id']!r} and one item"))
    checks.append(GateCheck(
        id="RC-3", name="item_success", ok=item is not None and item.transaction_status == "SUCCESS",
        detail=no_read if view is None else
        f"item status is {item.transaction_status}" if item else "the batch does not hold exactly one item"))
    if item is None:
        checks.append(GateCheck(id="RC-4", name="matches_ledger", ok=False,
                                detail=no_read if view is None else "no single item to compare"))
    else:
        same = (item.amount_cents == p["amount_cents"] and item.currency == p["currency"]
                and item.receiver.lower() == p["recipient_email"].lower())
        checks.append(GateCheck(
            id="RC-4", name="matches_ledger", ok=same,
            detail=f"PayPal item: {item.amount_cents} {item.currency} to {item.receiver}; "
                   f"ledger: {p['amount_cents']} {p['currency']} to {p['recipient_email']}"))
    checks.append(_human_approval(approved_by, confirm, preview, "RC-5"))
    return _result("RECONCILE", checks, preview)


def evaluate_retry(conn: sqlite3.Connection, payout_id: str, *, approved_by: str | None = None,
                   confirm: bool | None = None, preview: bool = False) -> GateResult:
    """RT-1 to RT-4. The request is rebuilt from the payout row, so key, amount and recipient stay."""
    p = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
    if p is None:
        raise LookupError(f"unknown payout {payout_id!r}")
    checks = [
        GateCheck(id="RT-1", name="state_unknown", ok=p["state"] == "UNKNOWN",
                  detail=f"payout state is {p['state']}"),
        GateCheck(id="RT-2", name="same_key", ok=True,
                  detail=f"resend rebuilt from the row: {p['sender_batch_id']}, "
                         f"{p['amount_cents']} {p['currency']} to {p['recipient_email']}"),
        _human_approval(approved_by, confirm, preview, "RT-3"),
        _sandbox_only("RT-4"),
    ]
    return _result("RETRY", checks, preview)
