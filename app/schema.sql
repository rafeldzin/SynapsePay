PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);  -- keys: run_id, scenario, armed_fault

CREATE TABLE contracts (
  id                       TEXT PRIMARY KEY,
  buyer_name               TEXT NOT NULL,
  contractor_name          TEXT NOT NULL,
  payee_email              TEXT NOT NULL,
  payee_email_verified_at  TEXT,
  payee_email_verified_by  TEXT,
  currency                 TEXT NOT NULL DEFAULT 'USD',
  created_at               TEXT NOT NULL
);

CREATE TABLE milestones (
  id                 TEXT PRIMARY KEY,
  contract_id        TEXT NOT NULL REFERENCES contracts(id),
  seq                INTEGER NOT NULL,
  title              TEXT NOT NULL,
  amount_cents       INTEGER NOT NULL CHECK (amount_cents > 0),
  criteria_json      TEXT NOT NULL,
  state              TEXT NOT NULL CHECK (state IN
                       ('AWAITING_DELIVERABLE','PASS','FAIL','NEEDS_REVIEW','PAYOUT_IN_FLIGHT','PAID')),
  verdict            TEXT CHECK (verdict IN ('PASS','FAIL','NEEDS_REVIEW')),
  verification_json  TEXT,
  override_reason    TEXT,
  override_by        TEXT,
  updated_at         TEXT NOT NULL,
  UNIQUE (contract_id, seq)
);

CREATE TABLE deliverables (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  milestone_id  TEXT NOT NULL REFERENCES milestones(id),
  sha256        TEXT NOT NULL,
  csv_name      TEXT NOT NULL,
  csv_bytes     BLOB NOT NULL,
  report_text   TEXT NOT NULL,
  submitted_by  TEXT NOT NULL,
  created_at    TEXT NOT NULL
);

CREATE TABLE payouts (
  id                  TEXT PRIMARY KEY,
  milestone_id        TEXT NOT NULL REFERENCES milestones(id),
  attempt             INTEGER NOT NULL,
  sender_batch_id     TEXT NOT NULL UNIQUE,
  recipient_email     TEXT NOT NULL,
  amount_cents        INTEGER NOT NULL,
  currency            TEXT NOT NULL,
  state               TEXT NOT NULL CHECK (state IN
                        ('CREATING','UNKNOWN','SENT','UNCLAIMED','PAID','RETURNED','FAILED')),
  payout_batch_id     TEXT,
  payout_item_id      TEXT,
  batch_status        TEXT,
  item_status         TEXT,
  replaces_payout_id  TEXT REFERENCES payouts(id),
  approved_by         TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL,
  last_synced_at      TEXT,
  UNIQUE (milestone_id, attempt)
);

CREATE UNIQUE INDEX ux_payouts_one_live
  ON payouts(milestone_id)
  WHERE state IN ('CREATING','UNKNOWN','SENT','UNCLAIMED','PAID');

CREATE TABLE messages (
  id           TEXT PRIMARY KEY,
  contract_id  TEXT NOT NULL REFERENCES contracts(id),
  from_email   TEXT NOT NULL,
  body         TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE cases (
  id                  TEXT PRIMARY KEY,
  payout_id           TEXT NOT NULL REFERENCES payouts(id),
  milestone_id        TEXT NOT NULL REFERENCES milestones(id),
  trigger_rule        TEXT NOT NULL CHECK (trigger_rule IN ('D1','D2','D3','D4')),
  state               TEXT NOT NULL CHECK (state IN
                        ('OPEN','INVESTIGATED','NEEDS_HUMAN','AWAITING_REISSUE','RESOLVED')),
  diversion_risk      INTEGER NOT NULL DEFAULT 0,
  requested_email     TEXT,
  recommended_action  TEXT,
  report_json         TEXT,
  resolution          TEXT,
  opened_at           TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

CREATE UNIQUE INDEX ux_cases_one_open
  ON cases(payout_id)
  WHERE state != 'RESOLVED';

CREATE TABLE events (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts               TEXT NOT NULL,
  type             TEXT NOT NULL,
  actor            TEXT NOT NULL,   -- system | human:<name> | ai:verifier | ai:investigator | paypal
  contract_id      TEXT,
  milestone_id     TEXT,
  payout_id        TEXT,
  case_id          TEXT,
  paypal_batch_id  TEXT,
  paypal_item_id   TEXT,
  payload_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
