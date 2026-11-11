#!/usr/bin/env python3
"""
scripts/duplicate_probe.py

Probe script to inspect PayPal Sandbox's exact HTTP response when the SAME payout
is submitted twice with identical sender_batch_id and PayPal-Request-Id header.

Usage:
  python scripts/duplicate_probe.py
"""

import asyncio
import os
import re
import sys
import time
import httpx
from dotenv import load_dotenv

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.paypal import PayPalClient, PayPalError

load_dotenv()


async def main():
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    base_url = (os.getenv("PAYPAL_BASE_URL") or "https://api-m.sandbox.paypal.com").rstrip("/")
    receiver_email = os.getenv("SPIKE_RECEIVER_EMAIL", "").strip()

    if not client_id or not client_secret:
        print("[ERROR] PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    if not receiver_email:
        print("[ERROR] SPIKE_RECEIVER_EMAIL must be set in .env")
        sys.exit(1)

    client = PayPalClient(client_id=client_id, client_secret=client_secret, base_url=base_url)

    print("[AUTH] Obtaining PayPal access token...")
    token = await client.get_access_token()
    print("[AUTH] Token obtained successfully.")

    # Generate unique batch ID
    sender_batch_id = f"dup-probe-{int(time.time())}"
    print(f"\n[PROBE] Using sender_batch_id: {sender_batch_id}")
    print(f"[PROBE] Receiver: {receiver_email} | Amount: $1.00 USD")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "PayPal-Request-Id": sender_batch_id,
    }

    payload = {
        "sender_batch_header": {
            "sender_batch_id": sender_batch_id,
            "email_subject": "SynapsePay Duplicate Probe",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "receiver": receiver_email,
                "amount": {
                    "value": "1.00",
                    "currency": "USD",
                },
                "note": "Duplicate Probe First Call",
                "sender_item_id": f"{sender_batch_id}-item1",
            }
        ],
    }

    url = f"{base_url}/v1/payments/payouts"

    print("\n--- CALL 1: First submission ---")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        resp1 = await http_client.post(url, json=payload, headers=headers)
        print(f"Call 1 HTTP Status: {resp1.status_code}")
        try:
            body1 = resp1.json()
            batch_id1 = body1.get("batch_header", {}).get("payout_batch_id")
            batch_status1 = body1.get("batch_header", {}).get("batch_status")
            print(f"Call 1 Batch ID:     {batch_id1}")
            print(f"Call 1 Batch Status: {batch_status1}")
        except Exception:
            print(f"Call 1 Raw Body: {resp1.text}")

    print("\nWaiting 2 seconds before Call 2...")
    await asyncio.sleep(2.0)

    print("\n--- CALL 2: Duplicate submission (IDENTICAL sender_batch_id & PayPal-Request-Id) ---")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        resp2 = await http_client.post(url, json=payload, headers=headers)

        print(f"\n[CALL 2 RESPONSE DETAILS]")
        print(f"HTTP Status Code: {resp2.status_code}")
        print("\nResponse Headers:")
        for k, v in resp2.headers.items():
            print(f"  {k}: {v}")

        print("\nRaw JSON Body:")
        raw_body = resp2.text
        print(raw_body)

        # Inspect if original batch ID or link is present
        print("\n--- ANALYSIS OF CALL 2 RESPONSE ---")
        extracted_batch_id = None
        extracted_batch_link = None

        try:
            data2 = resp2.json()
            if isinstance(data2, dict):
                # Check links
                for link in data2.get("links", []):
                    if isinstance(link, dict):
                        href = link.get("href", "")
                        rel = link.get("rel", "")
                        print(f"Found link: rel='{rel}', href='{href}'")
                        if "/payouts/" in href or rel in ("original_batch", "batch"):
                            extracted_batch_link = href
                            match = re.search(r"/payouts/([A-Za-z0-9_-]+)", href)
                            if match:
                                extracted_batch_id = match.group(1)

                # Check details
                for d in data2.get("details", []):
                    if isinstance(d, dict):
                        print(f"Found detail: {d}")
                        d_text = f"{d.get('issue', '')} {d.get('description', '')}"
                        match = re.search(r"/payouts/([A-Za-z0-9_-]+)", d_text)
                        if match and not extracted_batch_id:
                            extracted_batch_id = match.group(1)

                # Check top-level keys
                for k in ("original_batch_id", "payout_batch_id", "batch_id"):
                    if data2.get(k):
                        print(f"Found top-level key {k}: {data2[k]}")
                        extracted_batch_id = extracted_batch_id or str(data2[k])
        except Exception as e:
            print(f"Could not parse Call 2 JSON: {e}")

        # Regex fallback on raw_body
        if not extracted_batch_id and raw_body:
            match = re.search(r"/payouts/([A-Za-z0-9_-]+)", raw_body)
            if match:
                extracted_batch_id = match.group(1)

        print("\nExtraction Result:")
        print(f"-> Includes Original Batch ID?:   {'YES (' + extracted_batch_id + ')' if extracted_batch_id else 'NO'}")
        print(f"-> Includes Original Batch Link?: {'YES (' + extracted_batch_link + ')' if extracted_batch_link else 'NO'}")


if __name__ == "__main__":
    asyncio.run(main())
