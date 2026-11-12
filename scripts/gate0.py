#!/usr/bin/env python3
"""Gate 0: PayPal sandbox smoke test (UAT section 2, PRD 8 and 8.7).

Runs UAT-G0-01 to UAT-G0-07 against the real sandbox, prints one PASS or FAIL
line per step and writes docs/gate0_result.json. Exits 0 only if G0-01 to
G0-03 pass. Nothing is invented: every id, status and body comes from a
PayPal response. Each payout is $1.00.

Usage: py scripts/gate0.py
Needs .env: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, DEMO_PAYEE_EMAIL_CORRECT,
DEMO_PAYEE_EMAIL_TYPO (PAYPAL_BASE_URL optional, must be the sandbox host).
"""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
RESULT_PATH = ROOT / "docs" / "gate0_result.json"
SANDBOX_HOST = "api-m.sandbox.paypal.com"
POLL_EVERY_S = 3
POLL_MAX_S = 120
TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def load_env(path: Path) -> None:
    """Tiny .env reader (stdlib only). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def find_original_batch_id(body) -> str | None:
    """Scan every string in the body for a /v1/payments/payouts/{id} link (PRD 8.3)."""
    pattern = re.compile(r"/v1/payments/payouts/([A-Za-z0-9]+)")
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            m = pattern.search(node)
            if m:
                return m.group(1)
    return None


def payout_body(sender_batch_id: str, receiver: str, note: str) -> dict:
    return {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "SynapsePay Gate 0",
            "email_message": "Gate 0 sandbox smoke test.",
        },
        "items": [{
            "recipient_type": "EMAIL",
            "amount": {"value": "1.00", "currency": "USD"},
            "receiver": receiver,
            "note": note,
            "sender_item_id": f"{sender_batch_id}-I1",
        }],
    }


def safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"raw_text": resp.text[:2000]}


class Gate0:
    def __init__(self, client: httpx.Client, base_url: str, client_id: str, client_secret: str):
        self.http = client
        self.base = base_url.rstrip("/")
        self.cid, self.secret = client_id, client_secret
        self.token: str | None = None
        self.steps: list[dict] = []

    def record(self, step_id: str, name: str, ok: bool, detail: str, **ids) -> bool:
        self.steps.append({"id": step_id, "name": name, "result": "PASS" if ok else "FAIL",
                           "detail": detail, **ids})
        print(f"{'PASS' if ok else 'FAIL'}  {step_id}  {name}: {detail}", flush=True)
        return ok

    def call(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        headers.update(kw.pop("headers", {}))
        return self.http.request(method, self.base + path, headers=headers, **kw)

    def get_batch(self, batch_id: str) -> dict:
        return safe_json(self.call("GET", f"/v1/payments/payouts/{batch_id}"))

    def wait_item(self, batch_id: str, want: str) -> tuple[bool, float, dict]:
        """Poll the batch until its first item reaches `want`. Returns (ok, seconds, last batch)."""
        start, last = time.monotonic(), {}
        while True:
            last = self.get_batch(batch_id)
            items = last.get("items") or []
            if items and items[0].get("transaction_status") == want:
                return True, round(time.monotonic() - start, 1), last
            if time.monotonic() - start >= POLL_MAX_S:
                return False, round(time.monotonic() - start, 1), last
            time.sleep(POLL_EVERY_S)

    def wait_batch_success(self, batch_id: str) -> tuple[bool, str | None]:
        """Cancel is only valid once the batch itself is SUCCESS (else BATCH_NOT_COMPLETED)."""
        start, status = time.monotonic(), None
        while True:
            status = (self.get_batch(batch_id).get("batch_header") or {}).get("batch_status")
            if status == "SUCCESS" or time.monotonic() - start >= POLL_MAX_S:
                return status == "SUCCESS", status
            time.sleep(POLL_EVERY_S)

    def create(self, sender_batch_id: str, receiver: str, note: str) -> httpx.Response:
        return self.call("POST", "/v1/payments/payouts",
                         json=payout_body(sender_batch_id, receiver, note),
                         headers={"PayPal-Request-Id": sender_batch_id,
                                  "Content-Type": "application/json"})

    def run(self, correct: str, typo: str) -> dict:
        res: dict = {"ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        stamp = int(time.time())

        # G0-01 token
        basic = base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()
        r = self.http.post(self.base + "/v1/oauth2/token",
                           headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
                           data={"grant_type": "client_credentials"})
        self.token = safe_json(r).get("access_token") if r.status_code == 200 else None
        res["token_ok"] = self.record("UAT-G0-01", "OAuth token", bool(self.token),
                                      f"HTTP {r.status_code}, host {urlparse(self.base).hostname}")
        if not self.token:
            return res

        # G0-02 create to the correct email
        sbid1 = f"G0-{stamp}-1"
        r = self.create(sbid1, correct, "Gate 0 success path")
        body = safe_json(r)
        batch1 = (body.get("batch_header") or {}).get("payout_batch_id")
        ok2 = r.status_code == 201 and bool(batch1)
        self.record("UAT-G0-02", "Create payout (correct email)", ok2,
                    f"HTTP {r.status_code}, payout_batch_id={batch1}", sender_batch_id=sbid1,
                    payout_batch_id=batch1, debug_id=body.get("debug_id"))
        if not ok2:
            res["success_seconds"] = None
            return res

        # G0-03 poll to SUCCESS
        ok3, secs, last = self.wait_item(batch1, "SUCCESS")
        item1 = (last.get("items") or [{}])[0]
        res["success_seconds"] = secs if ok3 else None
        self.record("UAT-G0-03", "Item reaches SUCCESS", ok3,
                    f"item {item1.get('transaction_status')} after {secs}s, batch "
                    f"{(last.get('batch_header') or {}).get('batch_status')}",
                    payout_batch_id=batch1, payout_item_id=item1.get("payout_item_id"))
        item1_id = item1.get("payout_item_id")

        # G0-04 create to the typo email, poll to UNCLAIMED
        sbid2 = f"G0-{stamp}-2"
        r = self.create(sbid2, typo, "Gate 0 unclaimed path")
        body = safe_json(r)
        batch2 = (body.get("batch_header") or {}).get("payout_batch_id")
        item2_id = None
        unclaimed = False
        if r.status_code == 201 and batch2:
            unclaimed, secs4, last2 = self.wait_item(batch2, "UNCLAIMED")
            item2_id = ((last2.get("items") or [{}])[0]).get("payout_item_id")
            detail = (f"item {((last2.get('items') or [{}])[0]).get('transaction_status')} after {secs4}s, "
                      f"batch {(last2.get('batch_header') or {}).get('batch_status')}")
        else:
            detail = f"create failed: HTTP {r.status_code}, name={body.get('name')}, debug_id={body.get('debug_id')}"
        res["unclaimed_reproduced"] = unclaimed
        self.record("UAT-G0-04", "Typo email reaches UNCLAIMED", unclaimed, detail,
                    sender_batch_id=sbid2, payout_batch_id=batch2, payout_item_id=item2_id)

        # G0-05 cancel the unclaimed item (one attempt, raw result recorded)
        if item2_id:
            batch_done, batch_status = self.wait_batch_success(batch2)
            r = self.call("POST", f"/v1/payments/payouts-item/{item2_id}/cancel", json={})
            body = safe_json(r)
            returned = r.status_code == 200 and body.get("transaction_status") == "RETURNED"
            res.update(cancel_status_code=r.status_code, cancel_debug_id=body.get("debug_id"),
                       cancel_response_body=body, cancel_batch_status_at_call=batch_status)
            self.record("UAT-G0-05", "Cancel unclaimed item", returned,
                        f"batch {batch_status} at call, HTTP {r.status_code}, name={body.get('name')}, "
                        f"debug_id={body.get('debug_id')}, status={body.get('transaction_status')}",
                        payout_item_id=item2_id)
        else:
            returned = False
            res.update(cancel_status_code=None, cancel_debug_id=None)
            self.record("UAT-G0-05", "Cancel unclaimed item", False,
                        "BLOCKED: no UNCLAIMED item id from G0-04")
        res["cancel_returned"] = returned

        # G0-06 duplicate sender_batch_id with the same PayPal-Request-Id
        r = self.create(sbid1, correct, "Gate 0 success path")
        body = safe_json(r)
        original = find_original_batch_id(body)
        returned_batch = (body.get("batch_header") or {}).get("payout_batch_id")
        if 400 <= r.status_code < 500 and original:
            behavior = "rejected_with_link"
        elif 200 <= r.status_code < 300 and returned_batch == batch1:
            behavior = "replayed"
        else:
            behavior = "other"
        res.update(duplicate_behavior=behavior, duplicate_status_code=r.status_code,
                   duplicate_response_body=body, duplicate_original_batch_id=original or returned_batch,
                   duplicate_matches_original=(original or returned_batch) == batch1)
        self.record("UAT-G0-06", "Duplicate sender_batch_id", behavior != "other",
                    f"HTTP {r.status_code}, behavior={behavior}, original batch="
                    f"{original or returned_batch}, matches G0-02={(original or returned_batch) == batch1}",
                    payout_batch_id=batch1)

        # G0-07 cancel the SUCCESS item
        if item1_id:
            r = self.call("POST", f"/v1/payments/payouts-item/{item1_id}/cancel", json={})
            body = safe_json(r)
            res.update(cancel_on_success_error_name=body.get("name"),
                       cancel_on_success_status_code=r.status_code,
                       cancel_on_success_debug_id=body.get("debug_id"))
            self.record("UAT-G0-07", "Cancel a SUCCESS item is rejected",
                        400 <= r.status_code < 500,
                        f"HTTP {r.status_code}, name={body.get('name')}, debug_id={body.get('debug_id')}",
                        payout_item_id=item1_id)
        else:
            res.update(cancel_on_success_error_name=None)
            self.record("UAT-G0-07", "Cancel a SUCCESS item is rejected", False,
                        "BLOCKED: no item id from G0-03")
        return res


def main() -> int:
    load_env(ROOT / ".env")
    base = os.environ.get("PAYPAL_BASE_URL") or f"https://{SANDBOX_HOST}"
    if urlparse(base).hostname != SANDBOX_HOST:
        print(f"REFUSED: PAYPAL_BASE_URL host must be {SANDBOX_HOST} (INV-12)")
        return 2
    need = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "DEMO_PAYEE_EMAIL_CORRECT", "DEMO_PAYEE_EMAIL_TYPO"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        print(f"Missing in .env: {', '.join(missing)}")
        return 2
    correct, typo = os.environ["DEMO_PAYEE_EMAIL_CORRECT"], os.environ["DEMO_PAYEE_EMAIL_TYPO"]
    if correct.lower() == typo.lower():
        print("DEMO_PAYEE_EMAIL_CORRECT and DEMO_PAYEE_EMAIL_TYPO must differ")
        return 2

    with httpx.Client(timeout=TIMEOUT) as client:
        g = Gate0(client, base, os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])
        result = g.run(correct, typo)
    result["steps"] = g.steps
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {RESULT_PATH.relative_to(ROOT)}")
    core = {s["id"]: s["result"] for s in g.steps}
    return 0 if all(core.get(i) == "PASS" for i in ("UAT-G0-01", "UAT-G0-02", "UAT-G0-03")) else 1


if __name__ == "__main__":
    sys.exit(main())
