"""UAT rows that need no new behavior: ARCH-01, ARCH-02, VER-13 and RES-01."""
import re
from pathlib import Path

from app import db, events
from app.paypal import sync

APP = Path(__file__).resolve().parent.parent / "app"
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".sql", ".md", ".json", ".txt"}
OK = {"approved_by": "ops-lead", "confirm": True}


def app_files():
    return [p for p in APP.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts]


def rel(path: Path) -> str:
    return path.relative_to(APP.parent).as_posix()


def test_uat_arch_01_no_checkout_authorize_capture_or_void_anywhere_under_app():
    forbidden = re.compile(r"checkout/orders|/authorizations|AUTHORIZE|/capture|/void")
    hits = [rel(p) for p in app_files() if forbidden.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []  # INV-1: Direct Payouts only


def test_uat_arch_02_paypal_dot_com_appears_only_in_config_and_the_paypal_package():
    hits = [rel(p) for p in app_files() if "paypal.com" in p.read_text(encoding="utf-8", errors="ignore")]
    allowed = [h for h in hits if h == "app/config.py" or h.startswith("app/paypal/")]
    assert sorted(hits) == sorted(allowed), f"paypal.com found outside config.py and app/paypal/: {set(hits) - set(allowed)}"


def _submit(client, milestone="m1", fixture="m1_pass"):
    return client.post(f"/api/milestones/{milestone}/deliverable", json={"fixture": fixture, "submitted_by": "dewi"})


def _pay(client, milestone="m1", amount=30000):
    return client.post(f"/api/milestones/{milestone}/pay", json={**OK, "expected_amount_cents": amount})


def test_uat_ver_13_a_deliverable_is_409_while_in_flight_and_while_paid_and_the_verdict_stays(
        client, seed_a, fake_paypal):
    assert _submit(client).status_code == 200
    po = _pay(client).json()["payout"]
    before = client.get("/api/state").json()["milestones"][0]["verification"]

    in_flight = _submit(client)
    assert in_flight.status_code == 409

    fake_paypal.registered_emails.add(client.get("/api/state").json()["contract"]["payee_email"].lower())
    fake_paypal.advance()
    assert sync.sync_payout(po["id"])["state"] == "PAID"
    paid = _submit(client)
    assert paid.status_code == 409

    state = client.get("/api/state").json()["milestones"][0]
    assert state["state"] == "PAID" and state["verification"] == before  # the stored verdict is unchanged


def test_uat_res_01_resend_while_the_payout_is_unclaimed_is_blocked_on_pay4_and_logged(
        client, seed_a, fake_paypal):
    assert _submit(client).status_code == 200
    po = _pay(client).json()["payout"]
    fake_paypal.advance()  # scenario A's payee has no PayPal account, so the item is UNCLAIMED
    assert sync.sync_payout(po["id"])["state"] == "UNCLAIMED"
    writes_before = len(fake_paypal.write_calls)

    resend = _pay(client)  # the tempting Resend button
    assert resend.status_code == 409
    pay4 = next(c for c in resend.json()["checks"] if c["id"] == "PAY-4")
    assert pay4["ok"] is False and po["id"] in pay4["detail"]  # the detail names the live payout
    assert len(fake_paypal.write_calls) == writes_before  # no write call

    conn = db.connect()
    try:
        refusals = [e for e in events.list_events(conn) if e["type"] == "GATE_EVALUATED"
                    and not e["payload"]["allowed"]]
    finally:
        conn.close()
    assert refusals  # the refusal is on the timeline
