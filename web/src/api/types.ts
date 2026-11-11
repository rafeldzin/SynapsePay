/**
 * Bentuk data dari backend FastAPI.
 *
 * Diselaraskan dengan branch backend `feat/rafel-paypal-engine` (commit 7942aa7):
 * app/main.py, app/core/database.py, app/core/policy_gate.py. Kalau backend
 * berubah, jalankan `npm run gen:api` dan cocokkan dengan file ini.
 */

export type VerdictValue = "PASS" | "FAIL" | "NEEDS_REVIEW";

export interface Contract {
  id: string;
  buyer_name: string | null;
  contractor_name: string | null;
  currency: string;
}

/** Baris tabel milestones. GET /milestones mengembalikan array ini (tanpa status). */
export interface Milestone {
  id: string;
  contract_id: string | null;
  title: string;
  amount: number;
  /** String JSON, mis. {"criteria": ["...", "..."]}. */
  acceptance_json: string | null;
  contractor_email: string;
}

/** GET /milestones/{id}: milestone plus kontraknya. */
export interface MilestoneDetail extends Milestone {
  contract: Contract | null;
}

export interface CriterionVerdict {
  criterion: string;
  verdict: VerdictValue;
  /** Backend mengirim satu string; UI menampilkannya sebagai satu chip. */
  evidence: string | null;
}

export interface Verdict {
  milestone_id: string;
  verdict: VerdictValue;
  reasons: string[];
  injection_flag: boolean;
  criteria: CriterionVerdict[];
}

export interface GateCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface PolicyGateResult {
  allowed: boolean;
  checks: GateCheck[];
  sender_batch_id: string | null;
  reasons?: string[];
}

/** Baris tabel payouts. Status adalah status ITEM PayPal (atau RESERVED/FAILED lokal). */
export interface Payout {
  id: number;
  milestone_id: string;
  attempt: number;
  sender_batch_id: string;
  payout_batch_id: string | null;
  payout_item_id: string | null;
  receiver_email: string;
  amount: number;
  status: string;
  created_at: string;
  updated_at: string;
}

/** Baris tabel events (append-only). `timestamp` dalam UTC tanpa zona waktu. */
export interface EventRecord {
  id: number;
  milestone_id: string | null;
  payout_id: number | null;
  type: string;
  payload_json: string | null;
  payload: unknown;
  timestamp: string;
}

/**
 * POST /milestones/{id}/pay.
 * - Ditolak gate: status "REJECTED_OR_IN_PROGRESS", success false, gate_result berisi checks.
 * - Berhasil: success true, status = status item PayPal, plus ID PayPal.
 * - Simulasi respons hilang / timeout: success false, status "RESERVED", reconcile_required true.
 * - Error PayPal: backend saat ini membalas HTTP 500 teks polos (lihat event PAYOUT_ERROR).
 */
export interface PayResponse {
  status: string;
  success: boolean;
  allowed?: boolean;
  payout_id?: number;
  sender_batch_id?: string;
  payout_batch_id?: string | null;
  payout_item_id?: string | null;
  gate_result?: PolicyGateResult;
  error?: string;
  detail?: string;
  reconcile_required?: boolean;
  simulated_lost_response?: boolean;
}

/** POST /payouts/{id}/sync */
export interface SyncResponse {
  payout_id: number;
  old_status: string;
  new_status: string;
  flagged: boolean;
  flags: string[];
  payout: Payout;
}

export type DemoScenario = "timeout" | "unclaimed";

/** POST /demo/reset */
export interface ResetResponse {
  scenario: DemoScenario;
  contract: Contract;
  milestones: Milestone[];
}
