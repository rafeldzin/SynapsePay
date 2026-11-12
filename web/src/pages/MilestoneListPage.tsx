import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMilestone, getMilestones, resetDemo } from "../api/milestones";
import { getPayouts, latestPayoutFor } from "../api/payouts";
import type { DemoScenario, Milestone, Payout } from "../api/types";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatMoney } from "../lib/format";

/** Mengelompokkan milestone per contract_id, urutan sesuai respons backend. */
function groupByContract(milestones: Milestone[]) {
  const groups = new Map<string, Milestone[]>();
  for (const m of milestones) {
    const key = m.contract_id ?? "__tanpa_kontrak__";
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  return [...groups.entries()];
}

/**
 * GET /milestones tidak menyertakan nama kontrak, jadi header mengambilnya
 * dari GET /milestones/{id} milestone pertama di grup.
 */
function ContractSection({ milestones, payouts }: { milestones: Milestone[]; payouts: Payout[] | undefined }) {
  const first = milestones[0];
  const detail = useQuery({ queryKey: ["milestone", first.id], queryFn: () => getMilestone(first.id) });
  const contract = detail.data?.contract ?? null;

  return (
    <section className="overflow-hidden rounded border border-slate-200 bg-white">
      <div className="flex flex-wrap items-baseline gap-x-3 border-b border-slate-200 px-4 py-3">
        <h2 className="font-semibold">
          {contract ? (
            <>
              {contract.buyer_name ?? "—"} <span className="font-normal text-slate-400">membayar</span>{" "}
              {contract.contractor_name ?? "—"}
            </>
          ) : (
            "Kontrak"
          )}
        </h2>
        {first.contract_id && <span className="font-mono text-xs text-slate-500">{first.contract_id}</span>}
      </div>
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th className="px-4 py-2 font-medium">Milestone</th>
            <th className="px-4 py-2 font-medium">Penerima</th>
            <th className="px-4 py-2 text-right font-medium">Nominal</th>
            <th className="px-4 py-2 font-medium">Status payout PayPal</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {milestones.map((m) => {
            const payout = latestPayoutFor(payouts, m.id);
            return (
              <tr key={m.id} className="hover:bg-slate-50">
                <td className="px-4 py-3">
                  <div className="font-medium">{m.title}</div>
                  <div className="font-mono text-xs text-slate-400">{m.id}</div>
                </td>
                <td className="px-4 py-3 font-mono text-xs">{m.contractor_email}</td>
                <td className="px-4 py-3 text-right tabular-nums">{formatMoney(m.amount, contract?.currency)}</td>
                <td className="px-4 py-3">
                  {payout ? (
                    <StatusBadge status={payout.status} />
                  ) : (
                    <span className="text-xs text-slate-400">belum ada payout</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    to={`/milestones/${encodeURIComponent(m.id)}`}
                    className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-white"
                  >
                    Buka
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

const SCENARIOS: { value: DemoScenario; label: string }[] = [
  { value: "timeout", label: "timeout (email M1 benar)" },
  { value: "unclaimed", label: "unclaimed (email M1 salah ketik)" },
];

function ResetDemoControl() {
  const queryClient = useQueryClient();
  const [scenario, setScenario] = useState<DemoScenario>("timeout");
  const reset = useMutation({
    mutationFn: () => resetDemo(scenario),
    // Semua data di layar lain ikut kedaluwarsa setelah reset.
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const onClick = () => {
    if (reset.isPending) return;
    const ok = window.confirm(
      `Reset demo dengan skenario "${scenario}"?\n\nSemua milestone, payout, event, dan kasus di backend akan dihapus lalu diisi ulang.`,
    );
    if (ok) reset.mutate();
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <select
          value={scenario}
          onChange={(e) => setScenario(e.target.value as DemoScenario)}
          disabled={reset.isPending}
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          aria-label="Skenario demo"
        >
          {SCENARIOS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onClick}
          disabled={reset.isPending}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {reset.isPending ? "Mereset…" : "Reset demo"}
        </button>
      </div>
      {reset.isSuccess && (
        <span className="text-xs text-green-700">
          Demo direset dengan skenario <span className="font-mono">{reset.data.scenario}</span>.
        </span>
      )}
      {reset.isError && (
        <div className="w-96 max-w-full">
          <ErrorBox error={reset.error} title="Reset demo gagal" />
        </div>
      )}
    </div>
  );
}

export default function MilestoneListPage() {
  const milestones = useQuery({ queryKey: ["milestones"], queryFn: getMilestones });
  const payouts = useQuery({ queryKey: ["payouts"], queryFn: getPayouts });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Milestone</h1>
          <p className="text-sm text-slate-500">
            Pekerjaan diverifikasi dulu, lalu dibayar langsung dari akun pembeli setelah disetujui. Tidak ada dana
            yang ditahan.
          </p>
        </div>
        <ResetDemoControl />
      </div>

      {milestones.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat milestone…</div>
      )}

      {milestones.isError && (
        <ErrorBox error={milestones.error} title="Gagal memuat milestone" onRetry={() => milestones.refetch()} />
      )}

      {payouts.isError && (
        <ErrorBox error={payouts.error} title="Gagal memuat status payout" onRetry={() => payouts.refetch()} />
      )}

      {milestones.isSuccess && milestones.data.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Belum ada milestone. Tekan "Reset demo" untuk mengisi data demo.
        </div>
      )}

      {milestones.isSuccess &&
        groupByContract(milestones.data).map(([key, items]) => (
          <ContractSection key={key} milestones={items} payouts={payouts.data} />
        ))}
    </div>
  );
}
