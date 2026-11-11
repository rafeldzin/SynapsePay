import type { Verdict } from "../api/types";
import StatusBadge from "./StatusBadge";

function EvidenceChips({ evidence }: { evidence: string | string[] | null | undefined }) {
  const items = (Array.isArray(evidence) ? evidence : evidence ? [evidence] : []).filter((e) => e.trim());
  if (items.length === 0) {
    return <span className="text-xs italic text-amber-700">Tidak ada bukti yang dikutip</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item, i) => (
        <span key={i} className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 text-xs text-blue-900">
          {item}
        </span>
      ))}
    </div>
  );
}

export default function VerdictPanel({ verdict }: { verdict: Verdict }) {
  return (
    <div className="space-y-3">
      {verdict.injection_flag && (
        <div role="alert" className="rounded border-2 border-red-500 bg-red-50 p-3 text-sm text-red-900">
          <div className="font-semibold">Upaya prompt injection terdeteksi</div>
          <div>
            Deliverable berisi instruksi yang mencoba mengubah keputusan verifikasi. Instruksi itu diperlakukan sebagai
            data dan diabaikan. AI verifier tidak punya akses untuk membayar.
          </div>
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="text-sm text-slate-500">Verdict keseluruhan</span>
        <StatusBadge status={verdict.verdict} />
      </div>

      {verdict.reasons.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-slate-700">
          {verdict.reasons.map((reason, i) => (
            <li key={i}>{reason}</li>
          ))}
        </ul>
      )}

      <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
        {verdict.criteria.map((c, i) => (
          <li key={i} className="space-y-1.5 px-3 py-2.5">
            <div className="flex items-start justify-between gap-3 text-sm">
              <span className="font-medium">{c.criterion}</span>
              <StatusBadge status={c.verdict} />
            </div>
            <EvidenceChips evidence={c.evidence} />
          </li>
        ))}
        {verdict.criteria.length === 0 && (
          <li className="px-3 py-2.5 text-sm text-slate-500">Backend tidak mengirim kriteria.</li>
        )}
      </ul>
    </div>
  );
}
