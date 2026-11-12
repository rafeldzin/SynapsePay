import { api } from "./client";
import type {
  AppState,
  Approval,
  Fault,
  GateResult,
  PayResponse,
  ResetResponse,
  Scenario,
  VerificationResult,
} from "./types";

/** Kontraktor yang di-seed (sama dengan app/static/app.js), dipakai sebagai submitted_by. */
export const CONTRACTOR = "dewi";

/** Fixture yang di-seed per milestone (fixtures/manifest.json). */
export const FIXTURES: { name: string; milestoneId: string }[] = [
  { name: "m1_pass", milestoneId: "m1" },
  { name: "m2_fail_injected", milestoneId: "m2" },
  { name: "m2_fail_clean", milestoneId: "m2" },
  { name: "m2_good_injected", milestoneId: "m2" },
];

const enc = encodeURIComponent;

/** GET /api/state: satu snapshot berisi kontrak, milestone, payout, kasus, pesan, dan event. */
export const getState = () => api.get<AppState>("/state");

/** POST /api/milestones/{id}/deliverable dengan fixture yang di-seed. */
export const submitFixture = (milestoneId: string, fixture: string) =>
  api.post<VerificationResult>(`/milestones/${enc(milestoneId)}/deliverable`, {
    fixture,
    submitted_by: CONTRACTOR,
  });

/** POST /api/milestones/{id}/deliverable sebagai multipart: CSV + laporan, masing-masing maks 5 MB. */
export function uploadDeliverable(milestoneId: string, csv: File, report: File) {
  const form = new FormData();
  form.append("csv_file", csv);
  form.append("report_file", report);
  form.append("submitted_by", CONTRACTOR);
  return api.post<VerificationResult>(`/milestones/${enc(milestoneId)}/deliverable`, form);
}

/** GET /api/milestones/{id}/gate: pratinjau gate PAY, tanpa event dan tanpa panggilan PayPal. */
export const getGatePreview = (milestoneId: string) => api.get<GateResult>(`/milestones/${enc(milestoneId)}/gate`);

/**
 * POST /api/milestones/{id}/pay. Nominal yang diharapkan dikirim apa adanya dari backend;
 * gate (PAY-x) yang memastikan nominal dan penerima berasal dari database (INV-6).
 */
export const payMilestone = (milestoneId: string, expectedAmountCents: number, approval: Approval) =>
  api.post<PayResponse>(`/milestones/${enc(milestoneId)}/pay`, {
    ...approval,
    expected_amount_cents: expectedAmountCents,
  });

export const resetDemo = (scenario: Scenario) => api.post<ResetResponse>("/demo/reset", { scenario });

/** Memasang fault simulasi untuk create_payout berikutnya (skenario B sudah memasangnya saat reset). */
export const armFault = (fault: Fault) => api.post<{ armed: Fault }>("/demo/arm-fault", { fault });
