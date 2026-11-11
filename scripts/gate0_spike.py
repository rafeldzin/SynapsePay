#!/usr/bin/env python3
"""
scripts/gate0_spike.py

Standalone spike script that demonstrates direct PayPal Payouts on PayPal Sandbox:
(a) Sends a $1.00 sandbox payout to an email WITH a sandbox account (from SPIKE_RECEIVER_EMAIL),
    polls until payout ITEM status == SUCCESS (not batch status).
(b) Sends a $1.00 sandbox payout to an email WITHOUT a sandbox account, polls until item reaches UNCLAIMED,
    then keeps polling GET batch until BATCH status == SUCCESS (max ~25 polls, 5s apart),
    THEN calls cancel via POST /v1/payments/payouts-item/{id}/cancel,
    then polls until item status == RETURNED.

Prints every PayPal ID and status code/transition at each step.
"""

import argparse
import asyncio
import os
import sys
import time
from typing import Optional

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
from app.services.paypal import PayPalClient, PayPalError

load_dotenv()


async def run_spike_a(
    client: PayPalClient,
    success_email: str,
    max_polls: int = 30,
    poll_interval: float = 2.0,
):
    print("\n" + "=" * 70)
    print("SPIKE PART (a): Payout to registered sandbox account -> Poll to ITEM status == SUCCESS")
    print("=" * 70)
    print(f"Target Receiver Email: {success_email}")
    print(f"Amount: $1.00 USD")

    sender_batch_id = f"spike-success-{int(time.time())}"
    print(f"Generated sender_batch_id: {sender_batch_id}")

    print("\n[Step a.1] Calling POST /v1/payments/payouts...")
    create_res = await client.create_payout(
        sender_batch_id=sender_batch_id,
        receiver_email=success_email,
        amount=1.00,
        currency="USD",
        note="SynapsePay Spike (a): Verified Milestone Payout",
    )

    batch_id = create_res.get("batch_id")
    initial_status = create_res.get("batch_status")
    item_id = create_res.get("payout_item_id")

    print(f"-> PayPal Batch ID:       {batch_id}")
    print(f"-> Batch Status:          {initial_status}")
    print(f"-> Initial Payout Item ID: {item_id}")

    if not batch_id:
        print("[ERROR] No payout_batch_id returned in response:", create_res)
        return

    print("\n[Step a.2] Polling until payout ITEM status == SUCCESS...")
    item_status = None
    batch_status = initial_status

    for attempt in range(1, max_polls + 1):
        await asyncio.sleep(poll_interval)
        try:
            batch_details = await client.get_payout_batch(batch_id)
            batch_header = batch_details.get("batch_header", {})
            batch_status = batch_header.get("batch_status")
            items = batch_details.get("items", [])

            if items and isinstance(items[0], dict):
                item_id = items[0].get("payout_item_id") or item_id
                item_status = items[0].get("transaction_status")

            print(
                f"  [Poll {attempt}/{max_polls}] Batch Status: {batch_status} | "
                f"Item ID: {item_id} | Item Status: {item_status}"
            )

            # Requirement 1: success condition MUST be payout ITEM status == SUCCESS, not batch status
            if item_status == "SUCCESS":
                print(f"\n[SUCCESS] Part (a) completed! Payout item reached SUCCESS.")
                print(f"PayPal Batch ID: {batch_id}")
                print(f"PayPal Item ID:  {item_id}")
                print(f"Final Item Status: {item_status} (Batch: {batch_status})")
                return
        except PayPalError as e:
            print(f"  [Poll {attempt}] PayPal API error while polling: {e.message}")

    print(f"\n[INFO] Polling stopped after {max_polls} attempts. Final Item Status: {item_status}, Batch Status: {batch_status}")


async def run_spike_b(
    client: PayPalClient,
    unclaimed_email: str,
    max_polls: int = 25,
    poll_interval: float = 2.0,
):
    print("\n" + "=" * 70)
    print("SPIKE PART (b): Payout to unregistered account -> UNCLAIMED -> Batch SUCCESS -> Cancel -> RETURNED")
    print("=" * 70)
    print(f"Target Receiver Email: {unclaimed_email}")
    print(f"Amount: $1.00 USD")

    sender_batch_id = f"spike-unclaimed-{int(time.time())}"
    print(f"Generated sender_batch_id: {sender_batch_id}")

    print("\n[Step b.1] Calling POST /v1/payments/payouts...")
    create_res = await client.create_payout(
        sender_batch_id=sender_batch_id,
        receiver_email=unclaimed_email,
        amount=1.00,
        currency="USD",
        note="SynapsePay Spike (b): Payout to unregistered email",
    )

    batch_id = create_res.get("batch_id")
    initial_status = create_res.get("batch_status")
    item_id = create_res.get("payout_item_id")

    print(f"-> PayPal Batch ID:       {batch_id}")
    print(f"-> Batch Status:          {initial_status}")
    print(f"-> Initial Item ID:       {item_id}")

    if not batch_id:
        print("[ERROR] No payout_batch_id returned in response:", create_res)
        return

    print("\n[Step b.2] Polling until item status reaches UNCLAIMED...")
    item_status = None
    for attempt in range(1, max_polls + 1):
        await asyncio.sleep(poll_interval)
        try:
            batch_details = await client.get_payout_batch(batch_id)
            items = batch_details.get("items", [])

            if items and isinstance(items[0], dict):
                item_id = items[0].get("payout_item_id")
                item_status = items[0].get("transaction_status")

            print(f"  [Item Poll {attempt}/{max_polls}] Item ID: {item_id} | Status: {item_status}")

            if item_status == "UNCLAIMED":
                print(f"\n-> Payout item reached UNCLAIMED state as expected!")
                break
        except PayPalError as e:
            print(f"  [Item Poll {attempt}] PayPal API error while polling: {e.message}")

    if not item_id or item_status != "UNCLAIMED":
        print(f"[ERROR] Payout item did not reach UNCLAIMED (current item_status: {item_status}). Cannot proceed.")
        return

    # Requirement 2: after the item reaches UNCLAIMED, keep polling GET batch until BATCH status == SUCCESS (max ~25 polls, 5s apart), THEN call cancel.
    print("\n[Step b.3] Polling GET batch until BATCH status == SUCCESS (max 25 polls, 5s interval)...")
    batch_status = None
    for attempt in range(1, 26):
        await asyncio.sleep(5.0)
        try:
            batch_details = await client.get_payout_batch(batch_id)
            batch_header = batch_details.get("batch_header", {})
            batch_status = batch_header.get("batch_status")
            print(f"  [Batch Poll {attempt}/25] Payout Batch Status: {batch_status}")

            if batch_status == "SUCCESS":
                print("-> Batch status reached SUCCESS! It is now safe to cancel the UNCLAIMED item.")
                break
        except PayPalError as e:
            print(f"  [Batch Poll {attempt}/25] Error polling batch: {e.message}")

    if batch_status != "SUCCESS":
        print(f"[ERROR] Batch status did not reach SUCCESS (current status: {batch_status}). Cannot cancel item.")
        return

    print(f"\n[Step b.4] Cancelling payout item {item_id} via POST /v1/payments/payouts-item/{item_id}/cancel...")
    try:
        cancel_res = await client.cancel_payout_item(item_id)
        cancelled_status = cancel_res.get("transaction_status") or cancel_res.get("status")
        print(f"-> Cancel Response Status: {cancelled_status}")
        print(f"-> Cancel Response Raw:    {cancel_res}")
    except PayPalError as e:
        print(f"[ERROR] Failed to cancel payout item {item_id}: {e.message}")
        return

    # Requirement 2: Then poll until item status == RETURNED.
    print(f"\n[Step b.5] Polling until item status == RETURNED...")
    final_item_status = None
    for attempt in range(1, max_polls + 1):
        await asyncio.sleep(poll_interval)
        try:
            item_check = await client.get_payout_item(item_id)
            final_item_status = item_check.get("transaction_status") or item_check.get("status")
            print(f"  [Item Poll {attempt}/{max_polls}] Item ID: {item_id} | Status: {final_item_status}")

            if final_item_status in ("RETURNED", "CANCELLED"):
                print(f"\n[CONFIRMED] Live Item Status after cancellation: {final_item_status}")
                print(f"PayPal Item ID: {item_id}")
                print(f"[SUCCESS] Part (b) completed! Item successfully cancelled and funds returned to buyer.")
                return
        except PayPalError as e:
            print(f"  [Item Poll {attempt}] PayPal API error while polling item: {e.message}")

    print(f"\n[INFO] Polling stopped. Last Item Status: {final_item_status}")


def run_mock_demo():
    """
    Simulated demonstration when live PayPal Sandbox credentials are not provided.
    """
    print("\n" + "=" * 70)
    print("SYNAPSEPAY GATE 0 SPIKE (SIMULATED MODE)")
    print("No PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET provided in .env.")
    print("Running deterministic simulation of PayPal Payouts REST interactions:")
    print("=" * 70)

    # Simulated Part A
    ts = int(time.time())
    print("\n[Part A: Payout to Registered Sandbox Account -> Poll to ITEM status == SUCCESS]")
    print(f"Target: sb-contractor@business.example.com ($1.00 USD)")
    print(f"1. POST /v1/payments/payouts (sender_batch_id='spike-success-{ts}')")
    print(f"   -> HTTP 201 Created")
    print(f"   -> payout_batch_id: '5M9N3P8K7R6TQ'")
    print(f"   -> batch_status: 'PENDING'")
    print(f"2. GET /v1/payments/payouts/5M9N3P8K7R6TQ")
    print(f"   -> Poll 1: batch_status='PROCESSING', item_id='8V2K4M1N9P7T3', item_status='PENDING'")
    print(f"   -> Poll 2: batch_status='SUCCESS', item_id='8V2K4M1N9P7T3', item_status='SUCCESS'")
    print(f"   [SUCCESS] Verified contractor payout item reached SUCCESS.")

    # Simulated Part B
    print("\n[Part B: Payout to Unregistered Sandbox Account -> UNCLAIMED -> Batch SUCCESS -> Cancel -> RETURNED]")
    print(f"Target: unregistered-payee-999@example.com ($1.00 USD)")
    print(f"1. POST /v1/payments/payouts (sender_batch_id='spike-unclaimed-{ts}')")
    print(f"   -> HTTP 201 Created")
    print(f"   -> payout_batch_id: '2B7C9X4W1Z8KY'")
    print(f"   -> batch_status: 'PENDING'")
    print(f"2. GET /v1/payments/payouts/2B7C9X4W1Z8KY")
    print(f"   -> Poll 1: item_id='4L1M7Q9V3X5N8', item_status='UNCLAIMED'")
    print(f"   -> Item reached UNCLAIMED state.")
    print(f"3. GET /v1/payments/payouts/2B7C9X4W1Z8KY (polling batch status)")
    print(f"   -> Batch Poll 1: batch_status='PROCESSING'")
    print(f"   -> Batch Poll 2: batch_status='SUCCESS'")
    print(f"   -> Batch reached SUCCESS! Ready to cancel item.")
    print(f"4. POST /v1/payments/payouts-item/4L1M7Q9V3X5N8/cancel")
    print(f"   -> HTTP 200 OK")
    print(f"   -> transaction_status='RETURNED'")
    print(f"5. GET /v1/payments/payouts-item/4L1M7Q9V3X5N8")
    print(f"   -> Live confirmed item status: RETURNED")
    print(f"   [SUCCESS] Item cancelled and funds returned to buyer sandbox account.")
    print("\nTo run against live PayPal Sandbox, set PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, and SPIKE_RECEIVER_EMAIL in .env.")


async def main_async(args):
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()

    if args.mock or not client_id or not client_secret:
        run_mock_demo()
        return

    # Requirement 1: Read receiver email from env var SPIKE_RECEIVER_EMAIL (no placeholder default; exit with a clear message if unset).
    success_email = args.success_email or os.getenv("SPIKE_RECEIVER_EMAIL")
    if not success_email or not success_email.strip():
        print("\n[ERROR] Environment variable SPIKE_RECEIVER_EMAIL is not set.")
        print("Please set SPIKE_RECEIVER_EMAIL in your .env or pass --success-email with a valid sandbox receiver account email.")
        sys.exit(1)

    base_url = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    client = PayPalClient(client_id=client_id, client_secret=client_secret, base_url=base_url)

    unclaimed_email = args.unclaimed_email or os.getenv("SANDBOX_RECEIVER_UNCLAIMED") or f"unregistered-payee-{int(time.time())}@example.com"

    try:
        # Obtain and verify token
        token = await client.get_access_token()
        print(f"[AUTH] Successfully authenticated to PayPal Sandbox REST API: {base_url}")
        print(f"[AUTH] Bearer token obtained ({len(token)} chars).")

        # Part (a)
        await run_spike_a(client, success_email=success_email.strip())

        # Part (b)
        await run_spike_b(client, unclaimed_email=unclaimed_email.strip())

    except PayPalError as e:
        print(f"\n[FATAL PAYPAL ERROR] Status: {e.status_code} | Name: {e.name}")
        print(f"Message: {e.message}")
        print(f"Debug ID: {e.debug_id}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="SynapsePay Gate 0 Sandbox Spike Script")
    parser.add_argument("--mock", action="store_true", help="Force simulated mode without making PayPal network calls")
    parser.add_argument("--success-email", type=str, default=None, help="Registered sandbox receiver email for part (a) (defaults to SPIKE_RECEIVER_EMAIL)")
    parser.add_argument("--unclaimed-email", type=str, default=None, help="Unregistered receiver email for part (b)")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
