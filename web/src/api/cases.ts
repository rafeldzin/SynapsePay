import { api } from "./client";
import type {
  ActionCode,
  Approval,
  CaseDetail,
  InvestigationReport,
  MessageResponse,
  ReconcileResponse,
  RecheckResponse,
} from "./types";

const enc = encodeURIComponent;

/** GET /api/cases/{id}: kasus + laporan investigasi + event kasus. */
export const getCase = (caseId: string) => api.get<CaseDetail>(`/cases/${enc(caseId)}`);

/** POST /api/cases/{id}/investigate: AI investigator read-only, bisa sampai 45 detik. */
export const investigateCase = (caseId: string) => api.post<InvestigationReport>(`/cases/${enc(caseId)}/investigate`);

/** POST /api/cases/{id}/actions/reconcile: gate RECONCILE membaca PayPal lagi, lalu ledger jadi PAID. */
export const reconcileCase = (caseId: string, approval: Approval) =>
  api.post<ReconcileResponse>(`/cases/${enc(caseId)}/actions/reconcile`, approval);

/** POST /api/cases/{id}/actions/recheck: sync ulang payout kasus (read-only). */
export const recheckCase = (caseId: string) => api.post<RecheckResponse>(`/cases/${enc(caseId)}/actions/recheck`);

/** POST /api/messages: pesan kontraktor (simulasi). Aturan D1 bisa membuka kasus. */
export const postMessage = (contractId: string, fromEmail: string, body: string) =>
  api.post<MessageResponse>("/messages", { contract_id: contractId, from_email: fromEmail, body });

/** Label aksi (kode PRD 7). Kode aslinya selalu ikut ditampilkan. */
export const ACTION_LABELS: Record<ActionCode, string> = {
  WAIT_AND_RECHECK: "Tunggu lalu cek ulang",
  CANCEL_UNCLAIMED_AND_REISSUE: "Batalkan item unclaimed lalu terbitkan ulang",
  RECONCILE_MARK_PAID: "Rekonsiliasi: tandai sudah dibayar",
  RETRY_SAME_BATCH_ID: "Retry dengan sender_batch_id yang sama",
  ESCALATE_TO_HUMAN: "Eskalasi ke manusia",
};
