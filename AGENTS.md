# SynapsePay: agent instructions (AGENTS.md, for Codex CLI and Antigravity)

Hackathon MVP: FastAPI + SQLite monolith that verifies contractor work and pays milestones through the PayPal Payouts API (sandbox). Hard deadline Fri 13 Nov 2026, 03:00 WIB. Freeze 01:00, submit by 02:30.
`CLAUDE.md` is the same file for Claude Code. Edit both together, in the same commit.

## 1. Read first, every session
- Before writing or changing any code, read `docs/PRD.md` and `docs/UAT.md`. They win over this file, over code, and over other agents' output.
- If either is ambiguous, wrong, or conflicts with a task: stop and ask the human. Do not guess.
- Do not add anything outside scope. PRD 3.2 lists what is out: authorize/capture/void/escrow, Next.js, Postgres, ORMs, Docker, Redis, Celery, agent frameworks, a second service or any HTTP between parts of this app, login, webhooks, Transaction Search, PayPal Agent Toolkit/MCP, images or code as deliverables, LLM confidence thresholds. No "small extras". New runtime dependencies need a human decision (allowed: fastapi, uvicorn, httpx, pydantic, python-multipart, one LLM SDK).

## 2. Invariants (PRD 2, never weaken, not even to pass a test or hit the deadline)
- **INV-1** Direct Payouts only, sandbox only. Only these PayPal calls exist: OAuth token, create payout, read batch, read item, cancel unclaimed item. Never `/v2/checkout/orders`, authorizations, capture, void, or `intent=AUTHORIZE`. No fund holding.
- **INV-12** The app refuses to start unless the host is `api-m.sandbox.paypal.com`.
- **Two-Key Rule (INV-4)** AI recommends, the deterministic gate checks (re-evaluated server-side at execution time, with fresh PayPal reads where the PRD says so), a human approves in the same request. Only then may a PayPal write run. AI output is an input to the gate, never a trigger.
- **INV-2** PayPal write functions live only in `app/paypal/writes.py`, imported ONLY by `app/gate/executor.py`.
- **INV-3** `app/verify/`, `app/investigate/`, `app/llm.py` never import `paypal.writes`, `paypal.http` or `gate.executor`. Investigator tools are the four read-only tools in PRD 11.2.
- **Import rules** The LLM SDK is imported ONLY by `app/llm.py` (PRD 4.3 table). `paypal.reads` is imported by `investigate/tools.py`, `gate/policy.py` and `paypal/sync.py` only (sync.py does read-only batch polling, PRD 8.5). `paypal/sync.py` may also import `db`, `events` and `cases` to persist sync updates, write events and run detection; it never imports `paypal.writes`, `paypal.http` or `gate.executor`.
- **INV-6** Amount, currency and recipient come from the database. Request bodies use `extra="forbid"`. Money is integer cents.
- **INV-5** At most one live payout per milestone (partial unique index plus gate).
- **INV-7** `events` is append-only (SQLite triggers). Never UPDATE or DELETE it. Every state change and every PayPal call writes an event using the exact types in PRD 5.1.
- **INV-8/9/10** Untrusted content (reports, CSV, messages) goes only inside delimited data blocks in the user turn. The model can never loosen a deterministic verdict. On LLM failure: verifier returns `NEEDS_REVIEW`, investigator returns `ESCALATE_TO_HUMAN`. Never default to a pass or a write.
- **INV-11** Nothing on screen is invented. Seeded and simulated parts are labeled.
- **Secrets** Live only in git-ignored `.env`. Never print, log, commit, or return them from an endpoint. Run `git grep -n -i -E "client_secret|api_key|sk-"` before committing.

## 3. Lanes and frozen contracts (PRD 0, 4.2)
- **Dev A** (PayPal, backend, state machine): `main.py`, `config.py`, `db.py`, `schema.sql`, `events.py`, `paypal/*` (incl. `fake.py`), `gate/*`, `cases/detect.py`, `scripts/gate0.py`.
- **Dev B** (AI, fixtures, UI): `seed.py`, `llm.py`, `verify/*`, `investigate/*`, `static/*`, `fixtures/*`, `scripts/e2e.py`, `README.md`.
- **Shared**: `contracts.py` (A+B). Each lane tests its own modules. `tests/conftest.py` and `tests/fakes.py` are shared.
- Do not edit a file outside your lane. Need a change there? Put the request in your commit message and tell the human.
- `app/contracts.py` (frozen since T+0:30) and `app/schema.sql` are frozen contracts. Change them only with both developers agreeing, and edit the PRD in the same commit.
- Use exact names from the PRD: states (milestone 6.1, payout 6.2, case 6.3), event types (5.1), routes (12), gate check IDs like `PAY-4` (10), action and hypothesis codes (7). Never rename, pluralize or re-case.
- Stack rules: Python 3.11+, route handlers are plain `def`, one uvicorn worker, stdlib `sqlite3` (WAL, `BEGIN IMMEDIATE` for writes), UTC ISO 8601 timestamps.

## 4. Testing and UAT (UAT 0, 1)
- Each UAT row is one pytest function named `test_uat_<area>_<nn>_<slug>`. Never rename, merge or delete them.
- **Never edit, skip, xfail or weaken a test to make it pass.** If a test looks wrong, check it against the PRD: PRD agrees with the test means fix the code; PRD disagrees means stop and tell the human. ARCH, LED, SEC and gate tests are release blockers.
- After every change run Tier 1 (offline, fake PayPal, fake LLM, under 60 s): `pytest -m "not sandbox and not llm" -q`
- Run other tiers only at explicit gates (UAT 12.1) or when the human asks:
  - Tier 0 `python scripts/gate0.py` (first 45 minutes)
  - Tier 2 `pytest -m llm -q` (3 runs; needs LLM key)
  - Tier 3 `pytest -m sandbox -q` (real sandbox)
  - Tier 4 `python scripts/e2e.py --scenario A|B --base-url <url>`
- No test may reach a host other than the PayPal sandbox. Never retry a PayPal write automatically.
- Report honestly: command, pass/fail counts, every failing ID. A run with failures, skips or unrun tests is not "passing".
- Definition of done for a task: its Work Package UAT IDs (PRD 15) pass in Tier 1 and nothing that passed before now fails.

## 5. Sprint execution (PRD 14, 15)
- Work one Work Package at a time, by ID, in your lane:
  - **Dev A**: A0 gate0 script, A1 config/schema/events/startup, A2 PayPal client + fake, A3 gate PAY/RETRY + executor + routes, A4 sync + detection, A5 CANCEL/REISSUE/RECONCILE + faults, A6 reset/state/deploy.
  - **Dev B**: B0 contracts + fixtures, B1 facts + injection scan, B2 LLM adapter + verifier, B3 investigator, B4 operator page, B5 e2e runner + README + video.
- Start your reply by naming the package ID you are on. Touch only that package's files.
- **Cut #1 is ACTIVE.** Do not build: reset-time cancel of UNCLAIMED items, the `LLM_DAILY_CALL_CAP`, or an override control in the UI. The `override_reason` gate check (PAY-1) and API stay. Record UAT-API-07 and UAT-API-08 as "cut 1", not failed. Sandbox balance is funded high instead.
- Further cuts (PRD 14.1, in order): 2 scenario B in UI, 3 reissue step, 4 investigator tool loop to `prefetch`, 5 injection scene, 6 polish. Apply one only on its trigger and only after the human agrees.
- Never cut (PRD 14.2): real sandbox payout for m1, verifier PASS/FAIL with cited facts and gate refusing m2, one investigated exception that avoids a duplicate payment, hosted URL with no login plus reset button, any invariant.
- Gate 0 decides the on-camera scenario (A or B). Do not improvise a different payment rail if Gate 0 fails: stop and ask.
- Commit small, one package per commit. Commit only when asked.
