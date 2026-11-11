# SynapsePay

Autonomous milestone verification & deterministic direct PayPal Payouts payment rail.

---

## Architecture & Pivot: Zero-Escrow Direct Payouts

SynapsePay enforces a fundamental design principle: **the platform NEVER holds, pools, or escrows funds in advance.**

1. **Direct Payouts Rail**: Each verified milestone deliverable triggers a separate, direct PayPal Payout from the buyer's sandbox business account directly to the contractor's email address.
2. **Deterministic Policy Gate**: The AI agent only recommends; the deterministic policy gate (`app/core/policy_gate.py`) is the **ONLY code path allowed to call a PayPal write endpoint**.
3. **No Escrow / No Pre-authorization**: Legacy `AUTHORIZE` orders, authorization captures, voids, and cumulative capture limit checks have been removed.
4. **Append-Only Audit Trail**: All state changes, policy evaluations, and payment events are stored in an append-only `events` table protected by database triggers that explicitly reject `UPDATE` and `DELETE` operations.
5. **Race Condition Guard**: A SQLite partial unique index (`UNIQUE(milestone_id) WHERE status IN ('RESERVED','PENDING','PROCESSING','UNCLAIMED','SUCCESS','ONHOLD','BLOCKED')`) guarantees that concurrent requests cannot trigger duplicate payments.

---

## Real vs. Mocked

| Component | Mode | Description |
| :--- | :--- | :--- |
| **Test Suite (`pytest`)** | **Mocked** | Uses mocked `httpx` AsyncClient responses. No network requests are made, guaranteeing hermetic, fast, and deterministic test execution. |
| **API Server (`app/main.py`)** | **Real / Configurable** | When configured with `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, sends real REST API calls to PayPal Sandbox (`POST /v1/payments/payouts`, etc.). |
| **Spike Script (`scripts/gate0_spike.py`)** | **Real (with `--mock` option)** | Connects to the real PayPal Sandbox REST API to execute payouts and cancellations. If credentials are not configured in `.env`, run with `--mock` to simulate. |
| **Simulated "Lost Response" (Timeout Demo)** | **Mixed — Real PayPal, Simulated App Behaviour** | When `POST /milestones/{id}/pay` is called with `"simulate_lost_response": true`, a **real** `POST /v1/payments/payouts` call is made to PayPal Sandbox. The response (batch ID, item ID) is then deliberately discarded at the application layer to simulate the app crashing or timing out after the network call. The payout row stays `RESERVED`. Two events are logged: `PAYOUT_ERROR` (`reason: "simulated_lost_response"`) and `PAYPAL_RESPONSE_DISCARDED` (containing the **real** PayPal batch ID). This is the only intentional deception in the system; both events explicitly flag `simulated: true`. |
| **Contractor Inbox Message** | **Simulated** | `POST /milestones/{id}/messages` stores a message body supplied by the user/demo script — the text content is simulated. It is used by the deterministic investigator to detect diversion risk and trigger case opening. |
| **Phone-Verification Checkbox** | **Simulated** | The `new_email_verified: true` flag in cancel-reissue requests represents a human operator confirming the new email out-of-band (e.g. via phone). The flag itself is not network-verified; policy enforcement treats it as the human's attestation. |
| **PayPal Cancel Endpoint** | **Best-Effort (Sandbox 500)** | Real sandbox finding: `POST /v1/payments/payouts-item/{id}/cancel` returns HTTP 500 `INTERNAL_SERVICE_ERROR` even for UNCLAIMED items in a SUCCESS batch (confirmed debug_ids: `f494683063fd6`, `f245215b1bd6e`). The client retries up to 3 times on 5xx with 1s / 3s backoff. If cancel still fails, the platform returns `cancel_unavailable: true`, logs `ITEM_CANCEL_FAILED`, and does **not** reissue. No result is ever faked or simulated as success. |

---

## Setup & Installation

### 1. Requirements
- Python 3.13+
- Virtual environment recommended

```bash
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your PayPal Sandbox Developer Credentials:
```env
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
DATABASE_URL=sqlite:///./db/synapsepay.db
SPIKE_RECEIVER_EMAIL=your_sandbox_registered_contractor@business.example.com
```

---

## Running the Tests

Run the full pytest suite (no live PayPal network required):
```bash
pytest -v
```
or
```bash
python -m pytest -v
```

Included tests:
- **`tests/test_phase1.py`**:
  - OAuth Bearer token in-memory caching
  - Payout creation request payload & `PayPal-Request-Id` idempotency header
  - `DuplicateBatchError` surfaced with original batch ID/link
  - Typed `PayPalError` exception handling
  - Append-only `events` table rejecting `UPDATE` and `DELETE` via triggers
  - Partial unique index blocking duplicate active milestone payouts across active statuses (`RESERVED`, `PENDING`, `PROCESSING`, `UNCLAIMED`, `SUCCESS`, `ONHOLD`, `BLOCKED`)
  - Coverage rule downgrading criteria lacking evidence
  - Stub verifier detecting prompt injections (`INJECTION` / `OVERRIDE`)
- **`tests/test_phase2.py`**:
  - Policy gate: each of the 7 checks individually failing blocks payment; all passing allows
  - Concurrency guard: two concurrent `execute_payout` calls make exactly ONE PayPal call
  - Network timeout leaves payout in unknown/`RESERVED` state, never marks `FAILED`, and does not retry blindly
  - Cancel & reissue: refuses cancellation if item is not `UNCLAIMED`
  - Cancel & reissue: gate check `batch_processed` returns "retry shortly" if batch is still processing without calling cancel
  - Cancel & reissue: succeeds when `UNCLAIMED` and batch `SUCCESS`, sets old payout to `RETURNED`, updates contractor email, and reissues attempt `N+1`
  - Status synchronization: stores ITEM status (`transaction_status`), not batch status
  - Status synchronization: exception detection rule (mismatch, `UNCLAIMED`, payee dispute)
  - Full FastAPI endpoint integration workflow

---

## Running the Gate 0 Spike Script

The standalone spike script validates live sandbox flows:
- **Part (a)**: Sends $1.00 payout to a registered sandbox contractor account (read from `SPIKE_RECEIVER_EMAIL`), polls until payout **ITEM status == SUCCESS**.
- **Part (b)**: Sends $1.00 payout to an unregistered email, polls until item reaches `UNCLAIMED`, polls batch until **BATCH status == SUCCESS**, cancels item via `POST /v1/payments/payouts-item/{id}/cancel`, and confirms status is `RETURNED`.

### With Live PayPal Sandbox Credentials:
```bash
python scripts/gate0_spike.py
```
Optional custom receiver arguments:
```bash
python scripts/gate0_spike.py --success-email contractor@example.com --unclaimed-email nobody@example.com
```

### Simulated Mode (No Credentials Required):
```bash
python scripts/gate0_spike.py --mock
```

---

## Running the FastAPI Backend

Start the API server:
```bash
uvicorn app.main:app --reload --port 8000
```

### Endpoints
- `GET /health`: Healthcheck.
- `GET /milestones`: List all milestones with status and details.
- `GET /milestones/{id}`: Single milestone details with contract info.
- `POST /milestones/{id}/verify`: Verify milestone deliverable (runs stub verifier & logs `VERDICT_RECORDED`).
- `POST /milestones/{id}/pay`: Evaluates policy gate and triggers direct PayPal payout. Body: `{"human_approved": true, "simulate_lost_response": false}`. Set `simulate_lost_response: true` to run the **primary demo path** — a real PayPal call is made but the response is intentionally discarded, leaving the row `RESERVED`.
- `GET /milestones/{id}/events`: Audit timeline of events for a milestone.
- `GET /payouts`: List all payout attempts and their PayPal IDs.
- `POST /payouts/{id}/sync`: Polls PayPal REST API, syncs **item** status (not batch), and runs exception detection rule (flags mismatch, UNCLAIMED, payee dispute messages).
- `POST /payouts/{id}/retry`: Re-sends `create_payout` using the **exact same** `sender_batch_id` as a `RESERVED` row (`human_approved` required). Expected PayPal behaviour: duplicate rejection → reconcile from original batch. Never generates a new batch ID.
- `POST /payouts/{id}/cancel-reissue`: Cancels unclaimed payout once batch is `SUCCESS`, updates recipient email, and reissues payment (`{"new_email": "...", "new_email_verified": true, "human_approved": true}`). Returns `cancel_unavailable: true` if PayPal cancel returns 5xx after 3 retries.

### Investigator / Case Endpoints
- `POST /milestones/{id}/messages`: Store a simulated contractor inbox message (`{sender, body}`). Triggers deterministic case-opening check and diversion-risk analysis.
- `GET /milestones/{id}/case-evidence`: Full investigator evidence bundle — contract info, live PayPal statuses, audit timeline, inbound messages, Levenshtein email distances, `diversion_risk` flag, and deterministically computed `allowed_actions` (never includes plain resend).
- `GET /cases`: List all opened investigator cases across contracts.
- `POST /cases/{id}/actions`: Execute a deterministically-allowed action for a case (`{action, human_approved, ...}`). Rejects any action not present in the current `allowed_actions` list.

### Demo
- `POST /demo/reset`: Reseeds Lumen Studio / Dewi sample contract & 2 milestones. Accepts optional `{"scenario": "timeout" | "unclaimed"}` body to pre-configure contractor emails and amounts from env vars (`DEMO_CONTRACTOR_EMAIL`, `DEMO_TYPO_EMAIL`, `DEMO_AMOUNT_M1`, `DEMO_AMOUNT_M2`).
