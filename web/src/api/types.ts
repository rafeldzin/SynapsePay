/**
 * Bentuk data dari backend FastAPI di `app/main.py` (PRD 7, 12).
 *
 * Disalin dari `app/contracts.py` (kontrak beku) dan dari kolom tabel di `app/schema.sql`.
 * Semua nominal dalam sen (integer). Semua timestamp UTC ISO 8601, mis. "2026-11-12T15:48:09Z".
 * Kalau backend berubah, jalankan `npm run gen:api` dan cocokkan dengan file ini.
 */

export type VerdictValue = "PASS" | "FAIL" | "NEEDS_REVIEW";
export type GateAction = "PAY" | "RETRY" | "CANCEL" | "REISSUE" | "RECONCILE";
export type ActionCode =
  | "WAIT_AND_RECHECK"
  | "CANCEL_UNCLAIMED_AND_REISSUE"
  | "RECONCILE_MARK_PAID"
  | "RETRY_SAME_BATCH_ID"
  | "ESCALATE_TO_HUMAN";
export type HypothesisCode =
  | "MISTYPED_RECIPIENT"
  | "PROCESSING_DELAY"
  | "ORIGINAL_SUCCEEDED_LEDGER_STALE"
  | "PAYOUT_NEVER_CREATED"
  | "RECIPIENT_CANNOT_RECEIVE"
  | "OTHER";
export type Scenario = "A" | "B";
export type Fault = "timeout_after_send" | "timeout_before_send";

export type MilestoneState = "AWAITING_DELIVERABLE" | "PASS" | "FAIL" | "NEEDS_REVIEW" | "PAYOUT_IN_FLIGHT" | "PAID";
export type PayoutState = "CREATING" | "UNKNOWN" | "SENT" | "UNCLAIMED" | "PAID" | "RETURNED" | "FAILED";
export type CaseState = "OPEN" | "INVESTIGATED" | "NEEDS_HUMAN" | "AWAITING_REISSUE" | "RESOLVED";
export type TriggerRule = "D1" | "D2" | "D3" | "D4";

// ---- verifikasi (PRD 7) ------------------------------------------------------

export interface HardCheck {
  fact_id: string;
  op: "==" | "<=" | ">=";
  value: number | string | boolean;
}

export interface Criterion {
  id: string;
  text: string;
  hard_checks: HardCheck[];
}

export interface Fact {
  id: string;
  value: number | string | boolean | string[];
}

export interface InjectionFinding {
  source: "report" | "csv" | "model";
  pattern_id: string;
  excerpt: string;
}

export interface ContradictedClaim {
  claim: string;
  fact_id: string;
  fact_value: number | string | boolean;
}

export interface CriterionVerdict {
  criterion_id: string;
  verdict: VerdictValue;
  rationale: string;
  cited_fact_ids: string[];
  downgraded_by: "hard_check" | "coverage_rule" | "llm_error" | null;
}

export interface VerificationResult {
  milestone_id: string;
  deliverable_sha256: string;
  facts: Fact[];
  criteria: CriterionVerdict[];
  overall: VerdictValue;
  overall_capped_by: "injection_cap" | null;
  injection_detected: boolean;
  injection_findings: InjectionFinding[];
  contradicted_claims: ContradictedClaim[];
  note_to_contractor: string;
  llm_used: boolean;
  created_at: string;
}

// ---- gate (PRD 7, 10) --------------------------------------------------------

export interface GateCheck {
  /** mis. "PAY-4" */
  id: string;
  name: string;
  /** null = menunggu klik manusia (hanya di pratinjau). */
  ok: boolean | null;
  detail: string;
}

export interface GateResult {
  action: GateAction;
  allowed: boolean;
  preview: boolean;
  checks: GateCheck[];
  evaluated_at: string;
}

// ---- investigasi (PRD 7, 11) -------------------------------------------------

export interface Evidence {
  id: string;
  tool: string;
  source: "paypal" | "local";
  summary: string;
  data: Record<string, unknown>;
  fetched_at: string;
}

export interface Hypothesis {
  code: HypothesisCode;
  rank: number;
  supported: boolean;
  rationale: string;
  evidence_ids: string[];
}

export interface InvestigationReport {
  case_id: string;
  payout_id: string;
  hypotheses: Hypothesis[];
  recommended_action: ActionCode;
  do_not_resend: boolean;
  resend_risk: string;
  diversion_risk: boolean;
  requested_email: string | null;
  required_verification: string | null;
  evidence: Evidence[];
  reply_draft: string;
  steps_used: number;
  mode: "tool_loop" | "prefetch";
  downgraded_by: "action_not_in_list" | "no_paypal_citation" | "status_mismatch" | "llm_error" | "step_cap" | null;
  created_at: string;
}

// ---- baris tabel seperti dikirim GET /api/state ------------------------------

export interface Contract {
  id: string;
  buyer_name: string;
  contractor_name: string;
  payee_email: string;
  payee_email_verified_at: string | null;
  payee_email_verified_by: string | null;
  currency: string;
}

export interface Milestone {
  id: string;
  seq: number;
  title: string;
  amount_cents: number;
  state: MilestoneState;
  verdict: VerdictValue | null;
  override_reason: string | null;
  override_by: string | null;
  criteria: Criterion[] | null;
  verification: VerificationResult | null;
  /** Pratinjau gate PAY: cek manusia bernilai null, tidak ada event yang ditulis. */
  gate_preview: GateResult;
}

export interface Payout {
  id: string;
  milestone_id: string;
  attempt: number;
  sender_batch_id: string;
  recipient_email: string;
  amount_cents: number;
  currency: string;
  state: PayoutState;
  payout_batch_id: string | null;
  payout_item_id: string | null;
  batch_status: string | null;
  item_status: string | null;
  replaces_payout_id: string | null;
  approved_by: string;
  created_at: string;
  updated_at: string;
  last_synced_at: string | null;
}

export interface CaseRecord {
  id: string;
  payout_id: string;
  milestone_id: string;
  trigger_rule: TriggerRule;
  state: CaseState;
  /** 0 atau 1 dari SQLite. */
  diversion_risk: number;
  requested_email: string | null;
  recommended_action: ActionCode | null;
  report: InvestigationReport | null;
  resolution: string | null;
  opened_at: string;
  updated_at: string;
}

export interface CaseDetail extends CaseRecord {
  events: EventRecord[];
}

export interface Message {
  id: string;
  contract_id: string;
  from_email: string;
  body: string;
  created_at: string;
}

/** Baris tabel events (append-only, PRD 5.1). */
export interface EventRecord {
  id: number;
  ts: string;
  type: string;
  actor: string;
  contract_id: string | null;
  milestone_id: string | null;
  payout_id: string | null;
  case_id: string | null;
  paypal_batch_id: string | null;
  paypal_item_id: string | null;
  payload: Record<string, unknown>;
}

/** GET /api/state (PRD 12.1). */
export interface AppState {
  run_id: string | null;
  scenario: Scenario | null;
  paypal_mode: string;
  llm_mode: string;
  contract: Contract | null;
  milestones: Milestone[];
  payouts: Payout[];
  cases: CaseRecord[];
  messages: Message[];
  events: EventRecord[];
}

// ---- body dan respons aksi ---------------------------------------------------

/** Persetujuan manusia, dikirim di request yang sama dengan aksinya (INV-4). */
export interface Approval {
  approved_by: string;
  confirm: boolean;
}

export interface ResetResponse {
  run_id: string;
  scenario: Scenario;
}

export interface PayResponse {
  gate: GateResult;
  payout: Payout;
}

export interface RetryResponse {
  gate: GateResult;
  payout: Payout;
  /** Kasus D4 yang dibuka saat retry menemukan batch asli. */
  case_id: string | null;
}

export interface ReconcileResponse {
  gate: GateResult;
  payout: Payout;
  case: CaseRecord;
}

export interface RecheckResponse {
  payout: Payout;
  case: CaseRecord;
}

export interface SyncResponse {
  payout: Payout;
}

export interface MessageResponse {
  message: Message | string;
  case_id: string | null;
  diversion_risk: boolean;
}
