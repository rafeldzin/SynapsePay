import { api } from "./client";
import type { CaseActionRequest, CaseActionResponse, CaseEvidence, CaseRecord, InboundMessage } from "./types";

export const getCases = () => api.get<CaseRecord[]>("/cases");

/** Memanggil PayPal live di backend, jadi bisa beberapa detik. Juga memicu aturan pembukaan kasus. */
export const getCaseEvidence = (milestoneId: string) =>
  api.get<CaseEvidence>(`/milestones/${encodeURIComponent(milestoneId)}/case-evidence`);

/** Pesan kontraktor (simulasi). Backend mencatat MESSAGE_RECEIVED dan mengecek pembukaan kasus. */
export const postMessage = (milestoneId: string, sender: string, body: string) =>
  api.post<InboundMessage>(`/milestones/${encodeURIComponent(milestoneId)}/messages`, {
    sender: sender.trim() || null,
    body,
  });

export const runCaseAction = (caseId: number, req: CaseActionRequest) =>
  api.post<CaseActionResponse>(`/cases/${caseId}/actions`, req);

/**
 * Katalog semua aksi yang dikenal backend (lihat execute_case_action di policy_gate.py).
 * Backend hanya mengirim aksi yang DIIZINKAN di allowed_actions; aksi lain ditampilkan abu-abu.
 * `condition` adalah ringkasan kebijakan backend untuk ditampilkan, BUKAN dipakai untuk memutuskan.
 * Sengaja tidak ada aksi "resend" biasa.
 */
export const ACTION_CATALOG: { action: string; label: string; condition: string; movesMoney: boolean }[] = [
  {
    action: "mark_paid_no_resend",
    label: "Tandai sudah dibayar (tanpa kirim ulang)",
    condition: "Status item live di PayPal SUCCESS.",
    movesMoney: false,
  },
  {
    action: "cancel_and_reissue",
    label: "Batalkan item dan terbitkan ulang ke email terverifikasi",
    condition: "Item UNCLAIMED, batch SUCCESS, dan belum pernah gagal cancel.",
    movesMoney: true,
  },
  {
    action: "request_out_of_band_verification",
    label: "Minta verifikasi di luar chat",
    condition: "Ada risiko pengalihan: pesan menyebut email yang beda dengan kontrak.",
    movesMoney: false,
  },
  {
    action: "wait_and_resync",
    label: "Tunggu lalu sinkron ulang",
    condition: "Payout masih RESERVED, PENDING, atau PROCESSING.",
    movesMoney: false,
  },
  {
    action: "escalate_manual",
    label: "Eskalasi ke petugas manusia",
    condition: "Risiko pengalihan, cancel pernah gagal, atau item BLOCKED/ONHOLD.",
    movesMoney: false,
  },
];
