"""Test helpers for Dev A packages: build milestone and payout state directly in SQL."""
import json

from app.gate import policy


def verify(conn, milestone_id="m1", state="PASS", sha="abc123", newest_sha=None):
    """Stores a deliverable and a verification for it, and sets the milestone state."""
    conn.execute("INSERT INTO deliverables (milestone_id, sha256, csv_name, csv_bytes, report_text, "
                 "submitted_by, created_at) VALUES (?, ?, 'x.csv', x'00', 'r', 'dewi', 't')",
                 (milestone_id, newest_sha or sha))
    conn.execute("UPDATE milestones SET state = ?, verdict = ?, verification_json = ? WHERE id = ?",
                 (state, state, json.dumps({"deliverable_sha256": sha}), milestone_id))


def payout(conn, state, attempt=1, milestone_id="m1", pid="po_00000001"):
    run_id = policy._run_id(conn)
    conn.execute(
        "INSERT INTO payouts (id, milestone_id, attempt, sender_batch_id, recipient_email, amount_cents, "
        "currency, state, approved_by, created_at, updated_at) VALUES (?, ?, ?, ?, 'x@example.test', "
        "30000, 'USD', ?, 'ops-lead', 't', 't')",
        (pid, milestone_id, attempt, policy.sender_batch_id(run_id, milestone_id, attempt), state))
    return pid
