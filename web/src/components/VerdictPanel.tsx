import type { Criterion, Fact, VerificationResult } from "../api/types";
import StatusBadge from "./StatusBadge";

function factValue(value: Fact["value"] | undefined): string {
  if (value === undefined) return "?";
  return Array.isArray(value) ? value.join(", ") : String(value);
}

/** Hasil verifier (PRD 7, 9): verdict per kriteria dengan fakta yang dikutip. */
export default function VerdictPanel({
  result,
  criteria,
}: {
  result: VerificationResult;
  criteria: Criterion[] | null;
}) {
  const facts = new Map(result.facts.map((f) => [f.id, f.value]));
  const criterionText = new Map((criteria ?? []).map((c) => [c.id, c.text]));

  return (
    <div className="space-y-3">
      {result.injection_detected && (
        <div role="alert" className="rounded border-2 border-red-500 bg-red-50 p-3 text-sm text-red-900">
          <div className="font-semibold">Upaya prompt injection terdeteksi</div>
          <div>
            Deliverable berisi instruksi yang mencoba mengubah keputusan. Instruksi itu diperlakukan sebagai data dan
            diabaikan. Verifier tidak punya akses untuk membayar.
          </div>
          <ul className="mt-1.5 space-y-0.5 text-xs">
            {result.injection_findings.map((f, i) => (
              <li key={i}>
                <span className="font-mono">{f.pattern_id}</span> ({f.source}):{" "}
                <span className="font-mono">{f.excerpt}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-500">Verdict keseluruhan</span>
        <StatusBadge status={result.overall} />
        {result.overall_capped_by && (
          <span className="rounded bg-red-100 px-1.5 py-0.5 font-mono text-xs text-red-800">
            dibatasi: {result.overall_capped_by}
          </span>
        )}
        {!result.llm_used && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">tanpa model AI</span>
        )}
      </div>

      {result.contradicted_claims.length > 0 && (
        <div className="rounded border border-red-200 bg-red-50 p-2 text-sm text-red-900">
          <div className="text-xs font-medium">Klaim laporan yang dibantah data</div>
          <ul className="mt-1 list-disc pl-5">
            {result.contradicted_claims.map((c, i) => (
              <li key={i}>
                "{c.claim}" · fakta <span className="font-mono">{c.fact_id}</span> ={" "}
                <span className="font-mono">{String(c.fact_value)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="divide-y divide-slate-100 rounded border border-slate-200 bg-white">
        {result.criteria.map((c) => (
          <li key={c.criterion_id} className="space-y-1.5 px-3 py-2.5">
            <div className="flex items-start justify-between gap-3 text-sm">
              <span>
                <span className="font-mono text-xs text-slate-500">{c.criterion_id}</span>{" "}
                <span className="font-medium">{criterionText.get(c.criterion_id) ?? ""}</span>
              </span>
              <StatusBadge status={c.verdict} />
            </div>
            <p className="text-xs text-slate-700">{c.rationale}</p>
            {c.downgraded_by && (
              <div className="text-xs text-amber-800">
                Diturunkan oleh <span className="font-mono">{c.downgraded_by}</span>
              </div>
            )}
            <div className="flex flex-wrap gap-1.5">
              {c.cited_fact_ids.length === 0 && (
                <span className="text-xs italic text-amber-700">Tidak ada fakta yang dikutip</span>
              )}
              {c.cited_fact_ids.map((id) => (
                <span
                  key={id}
                  className="rounded-full border border-blue-200 bg-blue-50 px-2.5 py-0.5 font-mono text-xs text-blue-900"
                >
                  {id} = {factValue(facts.get(id))}
                </span>
              ))}
            </div>
          </li>
        ))}
        {result.criteria.length === 0 && (
          <li className="px-3 py-2.5 text-sm text-slate-500">Backend tidak mengirim kriteria.</li>
        )}
      </ul>

      {result.note_to_contractor && (
        <div className="text-sm">
          <div className="text-xs font-medium text-slate-500">Catatan untuk kontraktor</div>
          <p className="mt-0.5 whitespace-pre-wrap text-slate-700">{result.note_to_contractor}</p>
        </div>
      )}
    </div>
  );
}
