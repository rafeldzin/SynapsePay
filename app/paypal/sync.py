"""Status sync by polling (PRD 8.4, 8.5).

Syncs payouts in SENT or UNCLAIMED that have a batch id. It never touches UNKNOWN payouts: those
change only through a gated RETRY or RECONCILE (INV-4). A failed read writes SYNC_ERROR and
changes nothing else.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from app import db, events
from app.cases import detect
from app.config import get_settings
from app.events import now_iso
from app.paypal import reads

SYNCABLE = ("SENT", "UNCLAIMED")
ITEM_TO_STATE = {"SUCCESS": "PAID", "UNCLAIMED": "UNCLAIMED", "RETURNED": "RETURNED", "REFUNDED": "RETURNED",
                 "REVERSED": "RETURNED", "FAILED": "FAILED", "BLOCKED": "FAILED"}
BATCH_TO_STATE = {"DENIED": "FAILED", "CANCELED": "FAILED"}
UNCLAIMED_INTERVAL_S = 30
MAX_AGE = timedelta(hours=24)


def map_status(item_status: str | None, batch_status: str | None) -> str | None:
    """PRD 8.4. None means the local state stays as it is (PENDING, ONHOLD, no item yet)."""
    return ITEM_TO_STATE.get(item_status or "") or BATCH_TO_STATE.get(batch_status or "")


def sync_payout(payout_id: str) -> dict:
    """One status sync. Raises LookupError for an unknown payout, never for a PayPal failure."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
        if row is None:
            raise LookupError(f"unknown payout {payout_id!r}")
        if row["state"] not in SYNCABLE or not row["payout_batch_id"]:
            return dict(row)  # UNKNOWN, dead and batch-less payouts are never read

        try:
            batch = reads.get_batch(row["payout_batch_id"])
        except Exception as exc:  # the read failed: log it, change nothing else
            with db.tx(conn):
                events.append(conn, "SYNC_ERROR", "paypal", milestone_id=row["milestone_id"],
                              payout_id=payout_id, paypal_batch_id=row["payout_batch_id"],
                              payload={"error": f"{type(exc).__name__}: {exc}"})
            return dict(row)

        with db.tx(conn):
            row = conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone()
            if row["state"] not in SYNCABLE:  # moved by someone else while we were reading
                return dict(row)
            item = batch.items[0] if batch.items else None
            item_status = item.transaction_status if item else None
            contract_id = conn.execute("SELECT contract_id FROM milestones WHERE id = ?",
                                       (row["milestone_id"],)).fetchone()["contract_id"]
            ids = dict(contract_id=contract_id, milestone_id=row["milestone_id"], payout_id=payout_id,
                       paypal_batch_id=row["payout_batch_id"], paypal_item_id=item.payout_item_id if item else None)
            columns = {"last_synced_at": now_iso()}
            if item and not row["payout_item_id"]:
                columns["payout_item_id"] = item.payout_item_id  # stored the first time it appears
            if (batch.batch_status, item_status) != (row["batch_status"], row["item_status"]):
                events.append(conn, "PAYPAL_STATUS_READ", "paypal", **ids,
                              payload={"batch_status": batch.batch_status, "item_status": item_status,
                                       "previous_batch_status": row["batch_status"],
                                       "previous_item_status": row["item_status"]})
                columns.update(batch_status=batch.batch_status, item_status=item_status)

            target = map_status(item_status, batch.batch_status)
            if target and target != row["state"]:
                columns.update(state=target, updated_at=now_iso())
            conn.execute(f"UPDATE payouts SET {', '.join(f'{k} = ?' for k in columns)} WHERE id = ?",
                         (*columns.values(), payout_id))
            if target and target != row["state"]:
                events.append(conn, "PAYOUT_STATE_CHANGED", "system", **ids,
                              payload={"from": row["state"], "to": target, "cause": "status_sync"})
                _move_milestone(conn, row["milestone_id"], contract_id, target)
            detect.after_sync(conn, payout_id, previous_state=row["state"])
            return dict(conn.execute("SELECT * FROM payouts WHERE id = ?", (payout_id,)).fetchone())
    finally:
        conn.close()


def _move_milestone(conn, milestone_id: str, contract_id: str, payout_state: str) -> None:
    """PAID pays the milestone. A dead payout (RETURNED, FAILED) returns it to its verdict (PRD 6.1)."""
    m = conn.execute("SELECT state, verdict FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if m["state"] != "PAYOUT_IN_FLIGHT":
        return
    new = "PAID" if payout_state == "PAID" else m["verdict"] if payout_state in ("RETURNED", "FAILED") else None
    if new:
        conn.execute("UPDATE milestones SET state = ?, updated_at = ? WHERE id = ?", (new, now_iso(), milestone_id))
        events.append(conn, "MILESTONE_STATE_CHANGED", "system", contract_id=contract_id,
                      milestone_id=milestone_id, payload={"from": "PAYOUT_IN_FLIGHT", "to": new})


def _eligible(conn, now: datetime) -> list[dict]:
    cutoff = (now - MAX_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [dict(r) for r in conn.execute(
        "SELECT * FROM payouts WHERE state IN ('SENT', 'UNCLAIMED') AND payout_batch_id IS NOT NULL "
        "AND created_at >= ?", (cutoff,)).fetchall()]


def sync_all(*, now: datetime | None = None) -> int:
    """Syncs every eligible payout (created within 24 h). Returns how many were synced."""
    conn = db.connect()
    try:
        payouts = _eligible(conn, now or datetime.now(timezone.utc))
    finally:
        conn.close()
    for p in payouts:
        sync_payout(p["id"])
    return len(payouts)


# ---- background loop (SYNC_LOOP_ENABLED) ----------------------------------------------

_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    last_sync: dict[str, float] = {}
    while not _stop.wait(1.0):
        try:
            interval = get_settings().sync_interval_seconds
            conn = db.connect()
            try:
                payouts = _eligible(conn, datetime.now(timezone.utc))
            finally:
                conn.close()
            for p in payouts:
                due = interval if p["state"] == "SENT" else UNCLAIMED_INTERVAL_S
                if time.monotonic() - last_sync.get(p["id"], 0.0) >= due:
                    last_sync[p["id"]] = time.monotonic()
                    sync_payout(p["id"])
        except Exception:  # the loop must outlive a bad tick (e.g. a reset dropped the tables)
            pass


def start_loop() -> None:
    """Starts the daemon sync thread once. Called at startup when SYNC_LOOP_ENABLED=true."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="paypal-sync", daemon=True)
    _thread.start()


def stop_loop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=3)
