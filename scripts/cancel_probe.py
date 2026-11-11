import os, sys, httpx
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

BASE = os.environ.get("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
auth = (os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])
ITEMS = sys.argv[1:] or ["9EYL8U34F9ML4", "28QB2A8U2BNRA"]

with httpx.Client(timeout=30) as c:
    tok = c.post(f"{BASE}/v1/oauth2/token", auth=auth,
                 data={"grant_type": "client_credentials"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    for item in ITEMS:
        g = c.get(f"{BASE}/v1/payments/payouts-item/{item}", headers=h)
        print(f"\n[{item}] GET -> {g.status_code} item status:",
              g.json().get("transaction_status"))
        r = c.post(f"{BASE}/v1/payments/payouts-item/{item}/cancel", headers=h)
        print(f"[{item}] CANCEL -> HTTP {r.status_code}")
        print("  body:", r.text[:500])
        print("  PayPal-Debug-Id:", r.headers.get("paypal-debug-id"))