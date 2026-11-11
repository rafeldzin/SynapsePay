import type { EventRecord } from "../api/types";
import { formatMoney } from "./format";

export type Tone = "green" | "red" | "amber" | "slate" | "blue";

export interface EventSummary {
  /** Label singkat berbahasa Indonesia untuk tipe event. */
  label: string;
  /** Ringkasan satu baris, disusun hanya dari isi payload. */
  summary: string;
  tone: Tone;
}

type P = Record<string, unknown>;

const str = (v: unknown, fallback = "—") =>
  v === null || v === undefined || v === "" ? fallback : typeof v === "string" ? v : JSON.stringify(v);

const truncate = (text: string, max = 160) => (text.length > max ? `${text.slice(0, max)}…` : text);

function payloadOf(event: EventRecord): P {
  const p = event.payload;
  return p && typeof p === "object" && !Array.isArray(p) ? (p as P) : {};
}

/**
 * Aturan tim: event yang payload-nya mengandung kata "simulated" diberi label "simulasi".
 * Dicek pada payload_json mentah supaya kunci maupun nilai ikut terdeteksi.
 */
export function isSimulated(event: EventRecord): boolean {
  const raw = event.payload_json ?? (event.payload ? JSON.stringify(event.payload) : "");
  return /simulated/i.test(raw);
}

/** Kunci payload yang berisi ID dari PayPal (atau kunci idempotensi yang dikirim ke PayPal). */
const PAYPAL_ID_KEYS = [
  "payout_batch_id",
  "payout_item_id",
  "sender_batch_id",
  "original_batch_id",
  "cancelled_item_id",
  "new_sender_batch_id",
  "debug_id",
];

export function paypalIds(event: EventRecord): { key: string; value: string }[] {
  const p = payloadOf(event);
  return PAYPAL_ID_KEYS.flatMap((key) => {
    const v = p[key];
    return typeof v === "string" && v ? [{ key, value: v }] : [];
  });
}

export function summarizeEvent(event: EventRecord, currency?: string): EventSummary {
  const p = payloadOf(event);

  switch (event.type) {
    case "VERDICT_RECORDED": {
      const verdict = str(p.verdict);
      const tone: Tone = verdict === "PASS" ? "green" : verdict === "FAIL" ? "red" : "amber";
      return {
        label: "Verdict dicatat",
        summary: `Verdict ${verdict}${p.injection_flag ? " · upaya prompt injection terdeteksi" : ""}`,
        tone,
      };
    }
    case "GATE_EVALUATED": {
      const checks = Array.isArray(p.checks) ? (p.checks as P[]) : [];
      const failed = checks.filter((c) => c.passed === false).map((c) => str(c.name));
      return p.allowed
        ? { label: "Gate dievaluasi", summary: `Gate lolos (${checks.length}/${checks.length} cek)`, tone: "green" }
        : { label: "Gate dievaluasi", summary: `Gate menolak: ${failed.join(", ") || "—"}`, tone: "red" };
    }
    case "PAYOUT_RESERVED":
      return {
        label: "Payout dicadangkan",
        summary: `${formatMoney(Number(p.amount), currency)} ke ${str(p.receiver_email)} · status ${str(p.status)}`,
        tone: "slate",
      };
    case "PAYOUT_SENT":
      return { label: "Payout dikirim ke PayPal", summary: `Status item ${str(p.status)}`, tone: "blue" };
    case "PAYOUT_ERROR": {
      const what = str(p.name ?? p.error_type ?? p.reason);
      return {
        label: "Error payout",
        summary: truncate(`${what}${p.detail ? `: ${str(p.detail)}` : ""}`),
        tone: "red",
      };
    }
    case "PAYPAL_RESPONSE_DISCARDED":
      return {
        label: "Respons PayPal dibuang",
        summary: "Payout sudah dikirim ke PayPal, tetapi responsnya tidak dipakai aplikasi",
        tone: "amber",
      };
    case "PAYOUT_STATUS_SYNCED":
      return {
        label: "Status disinkronkan",
        summary:
          p.action === "RECONCILE"
            ? `Rekonsiliasi · status ${str(p.status)}`
            : `${str(p.old_status)} → ${str(p.new_status)}`,
        tone: "slate",
      };
    case "PAYOUT_RECONCILED":
      return {
        label: "Payout direkonsiliasi",
        summary: `Status ${str(p.status)} · lewat ${str(p.recovery_method)}`,
        tone: "green",
      };
    case "MESSAGE_RECEIVED":
      return {
        label: "Pesan masuk",
        summary: truncate(`${str(p.sender, "pengirim tidak diketahui")}: "${str(p.body, "")}"`),
        tone: "blue",
      };
    case "ITEM_CANCEL_FAILED":
      return {
        label: "Cancel item gagal",
        summary: truncate(`${str(p.paypal_name)} (HTTP ${str(p.status_code)}): ${str(p.error)}`),
        tone: "red",
      };
    case "ITEM_CANCELLED":
      return { label: "Item dibatalkan", summary: `Status ${str(p.status)}`, tone: "amber" };
    case "PAYOUT_REISSUE_FAILED":
      return {
        label: "Penerbitan ulang gagal",
        summary: truncate(`Ke ${str(p.new_email)} · ${str(p.error ?? p.detail)}`),
        tone: "red",
      };
    case "PAYOUT_REISSUED":
      return {
        label: "Payout diterbitkan ulang",
        summary: `Ke ${str(p.new_email)} · percobaan #${str(p.attempt)} · menggantikan payout ${str(p.cancelled_payout_id)}`,
        tone: "green",
      };
    case "PAYOUT_MARKED_PAID":
      return {
        label: "Ditandai sudah dibayar",
        summary: `Tanpa kirim ulang · ${str(p.reason)}`,
        tone: "green",
      };
    case "OUT_OF_BAND_VERIFICATION_REQUESTED":
      return {
        label: "Verifikasi di luar chat diminta",
        summary: `Kasus #${str(p.case_id)} · ${str(p.reason)}`,
        tone: "amber",
      };
    case "CASE_ESCALATED":
      return { label: "Kasus dieskalasi", summary: `Kasus #${str(p.case_id)} · ${str(p.reason)}`, tone: "amber" };
    default:
      return {
        label: event.type,
        summary: truncate(event.payload_json ?? "(tanpa payload)"),
        tone: "slate",
      };
  }
}
