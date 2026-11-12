import type { EventRecord } from "../api/types";

export type Tone = "green" | "red" | "amber" | "slate" | "blue";

export interface EventSummary {
  /** Label singkat berbahasa Indonesia untuk tipe event (PRD 5.1). */
  label: string;
  /** Ringkasan satu baris, disusun hanya dari isi payload. */
  summary: string;
  tone: Tone;
}

type P = Record<string, unknown>;

const str = (v: unknown, fallback = "—") =>
  v === null || v === undefined || v === "" ? fallback : typeof v === "string" ? v : JSON.stringify(v);

const truncate = (text: string, max = 160) => (text.length > max ? `${text.slice(0, max)}…` : text);

/** Event yang berasal dari fault simulasi diberi label "simulasi" (INV-11). */
export function isSimulated(event: EventRecord): boolean {
  return /simulated/i.test(JSON.stringify(event.payload ?? {})) || event.type === "FAULT_ARMED";
}

/** ID PayPal (kolom event) dan kunci idempotensi / debug_id di payload. */
export function paypalIds(event: EventRecord): { key: string; value: string }[] {
  const out: { key: string; value: string }[] = [];
  if (event.paypal_batch_id) out.push({ key: "payout_batch_id", value: event.paypal_batch_id });
  if (event.paypal_item_id) out.push({ key: "payout_item_id", value: event.paypal_item_id });
  for (const key of ["sender_batch_id", "debug_id"]) {
    const v = event.payload?.[key];
    if (typeof v === "string" && v) out.push({ key, value: v });
  }
  return out;
}

const LABELS: Record<string, [string, Tone]> = {
  RESET: ["Demo direset", "slate"],
  DELIVERABLE_SUBMITTED: ["Deliverable masuk", "blue"],
  FACTS_COMPUTED: ["Fakta dihitung", "slate"],
  INJECTION_FLAGGED: ["Prompt injection terdeteksi", "red"],
  VERDICT_RECORDED: ["Verdict dicatat", "blue"],
  OVERRIDE_RECORDED: ["Override dicatat", "amber"],
  GATE_EVALUATED: ["Gate dievaluasi", "slate"],
  PAYOUT_CREATING: ["Payout dibuat (CREATING)", "slate"],
  PAYPAL_PAYOUT_CREATED: ["PayPal membuat payout", "green"],
  PAYPAL_OUTCOME_UNKNOWN: ["Hasil PayPal tidak diketahui", "amber"],
  PAYPAL_REJECTED: ["PayPal menolak", "red"],
  PAYPAL_ORIGINAL_FOUND: ["Batch asli ditemukan", "green"],
  PAYPAL_STATUS_READ: ["Status PayPal dibaca", "slate"],
  PAYPAL_ITEM_CANCELLED: ["Item PayPal dibatalkan", "amber"],
  PAYOUT_STATE_CHANGED: ["Status payout berubah", "blue"],
  MILESTONE_STATE_CHANGED: ["Status milestone berubah", "blue"],
  MESSAGE_RECEIVED: ["Pesan kontraktor masuk", "blue"],
  CASE_OPENED: ["Kasus dibuka", "amber"],
  DIVERSION_RISK_FLAGGED: ["Risiko pengalihan pembayaran", "red"],
  INVESTIGATION_TOOL_CALL: ["Investigator memanggil tool", "slate"],
  INVESTIGATION_REPORTED: ["Laporan investigasi", "blue"],
  PAYEE_EMAIL_CHANGED: ["Email penerima diubah", "amber"],
  CASE_STATE_CHANGED: ["Status kasus berubah", "blue"],
  SYNC_ERROR: ["Sync gagal", "red"],
  FAULT_ARMED: ["Fault simulasi dipasang", "amber"],
};

export function summarizeEvent(event: EventRecord): EventSummary {
  const p: P = event.payload ?? {};
  const [label, baseTone] = LABELS[event.type] ?? [event.type, "slate" as Tone];

  switch (event.type) {
    case "VERDICT_RECORDED": {
      const verdict = str(p.overall ?? p.verdict);
      return {
        label,
        summary: `Verdict ${verdict}`,
        tone: verdict === "PASS" ? "green" : verdict === "FAIL" ? "red" : "amber",
      };
    }
    case "GATE_EVALUATED": {
      const checks = Array.isArray(p.checks) ? (p.checks as P[]) : [];
      const failed = checks.filter((c) => c.ok === false).map((c) => str(c.id));
      return p.allowed
        ? { label, summary: `Gate ${str(p.action)} lolos (${checks.length} cek)`, tone: "green" }
        : { label, summary: `Gate ${str(p.action)} menolak: ${failed.join(", ") || "—"}`, tone: "red" };
    }
    case "PAYOUT_STATE_CHANGED":
    case "MILESTONE_STATE_CHANGED":
    case "CASE_STATE_CHANGED":
      if ("from" in p || "to" in p) {
        return { label, summary: `${str(p.from)} → ${str(p.to)}`, tone: baseTone };
      }
      break;
    case "INVESTIGATION_TOOL_CALL":
      return { label, summary: `${str(p.tool ?? p.name)}`, tone: baseTone };
    case "INVESTIGATION_REPORTED":
      return { label, summary: `Rekomendasi ${str(p.recommended_action)}`, tone: baseTone };
    case "CASE_OPENED":
      return { label, summary: `Aturan ${str(p.trigger_rule ?? p.rule)}`, tone: baseTone };
    case "FAULT_ARMED":
      return { label, summary: str(p.fault), tone: baseTone };
  }
  const text = Object.keys(p).length ? JSON.stringify(p) : "(tanpa payload)";
  return { label, summary: truncate(text), tone: baseTone };
}
