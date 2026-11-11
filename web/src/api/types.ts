/**
 * Bentuk data dari backend FastAPI.
 *
 * ASUMSI: file ini ditulis dari app/models/schema.py (commit bc0cecb) plus instruksi
 * tim, karena /docs backend terbaru belum bisa diakses. Begitu backend jalan, jalankan
 * `npm run gen:api` lalu cocokkan file ini dengan src/api/openapi.d.ts.
 *
 * Field bertanda "ASUMSI" belum ada di schema.py dan diminta ke tim backend.
 * Semua field asumsi dibuat opsional supaya UI tidak rusak kalau belum dikirim;
 * UI menampilkan "—" alih-alih mengarang nilai.
 */

export type VerdictValue = "PASS" | "FAIL" | "NEEDS_REVIEW";

export interface Contract {
  id: string;
  buyer_name?: string | null;
  contractor_name?: string | null;
  currency: string;
}

export interface Milestone {
  id: string;
  title: string;
  amount: number;
  contractor_email: string;
  contract_id?: string | null;
  acceptance?: string | null;
  acceptance_json?: string | null;
  /** ASUMSI: status milestone dari backend, mis. DRAFT / VERIFIED / PAID. */
  status?: string | null;
  /** ASUMSI: mata uang; kalau tidak ada, diambil dari contract. */
  currency?: string | null;
  /** ASUMSI: info kontrak ikut di respons milestone, nested atau datar. */
  contract?: Contract | null;
  buyer_name?: string | null;
  contractor_name?: string | null;
}

export interface CriterionVerdict {
  criterion: string;
  verdict: VerdictValue;
  /** Di schema.py masih satu string; UI menampilkannya sebagai satu chip. */
  evidence?: string | string[] | null;
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
  sender_batch_id?: string | null;
  reasons: string[];
}
