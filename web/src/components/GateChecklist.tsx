import type { GateCheck } from "../api/types";

/**
 * Checklist gate dirender apa adanya dari backend (PRD 10); tidak ada aturan yang dihitung ulang di sini.
 * `ok: null` berarti cek persetujuan manusia yang menunggu klik (hanya di pratinjau).
 */
export default function GateChecklist({ checks, pendingLabel }: { checks: GateCheck[]; pendingLabel?: string }) {
  return (
    <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
      {checks.map((check) => {
        const pending = check.ok === null;
        const icon = pending ? "…" : check.ok ? "✓" : "✕";
        const iconClass = pending
          ? "bg-slate-200 text-slate-600"
          : check.ok
            ? "bg-green-600 text-white"
            : "bg-red-600 text-white";
        return (
          <li key={check.id} className="flex items-start gap-3 px-3 py-2 text-sm">
            <span
              aria-label={pending ? "menunggu" : check.ok ? "lulus" : "gagal"}
              className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${iconClass}`}
            >
              {icon}
            </span>
            <div className="min-w-0">
              <div className="font-medium">
                <span className="font-mono">{check.id}</span>{" "}
                <span className="font-mono text-xs font-normal text-slate-500">{check.name}</span>
              </div>
              <div className="text-xs text-slate-600">{pending ? (pendingLabel ?? check.detail) : check.detail}</div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
