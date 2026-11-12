import type { GateCheck } from "../api/types";

/** Label ramah untuk nama cek dari backend. Nama asli tetap ditampilkan. */
const LABELS: Record<string, string> = {
  verdict_pass: "Verdict PASS",
  no_injection: "Tidak ada prompt injection",
  amount_matches: "Nominal sama dengan milestone",
  recipient_matches_contract: "Penerima sesuai kontrak",
  no_active_payout: "Belum ada payout aktif",
  human_approved: "Disetujui manusia",
  idempotency_key: "Kunci idempotensi",
};

interface Props {
  checks: GateCheck[];
  /**
   * Untuk pratinjau sebelum klik: cek human_approved ditampilkan sebagai
   * "menunggu klik", karena klik tombol itulah persetujuannya.
   */
  humanApprovalPending?: boolean;
}

/** Checklist gate dirender apa adanya dari backend; tidak ada aturan yang dihitung ulang di sini. */
export default function GateChecklist({ checks, humanApprovalPending = false }: Props) {
  return (
    <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
      {checks.map((check) => {
        const pending = humanApprovalPending && check.name === "human_approved";
        const icon = pending ? "…" : check.passed ? "✓" : "✕";
        const iconClass = pending
          ? "bg-slate-200 text-slate-600"
          : check.passed
            ? "bg-green-600 text-white"
            : "bg-red-600 text-white";
        return (
          <li key={check.name} className="flex items-start gap-3 px-3 py-2 text-sm">
            <span
              aria-label={pending ? "menunggu" : check.passed ? "lulus" : "gagal"}
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${iconClass}`}
            >
              {icon}
            </span>
            <div className="min-w-0">
              <div className="font-medium">
                {LABELS[check.name] ?? check.name}{" "}
                <span className="font-mono text-xs font-normal text-slate-400">{check.name}</span>
              </div>
              <div className="text-xs text-slate-600">
                {pending ? "Menunggu klik Approve & pay." : check.detail}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
