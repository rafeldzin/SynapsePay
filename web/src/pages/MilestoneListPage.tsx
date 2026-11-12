import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { resetDemo } from "../api/milestones";
import { latestPayoutFor } from "../api/payouts";
import type { Scenario } from "../api/types";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatCents } from "../lib/format";
import { useAppState } from "../lib/state";

/** Teks sama dengan banner skenario di app/static/app.js. */
const SCENARIO_TEXT: Record<Scenario, string> = {
  A: "Skenario A: m1 dibayar ke email yang salah ketik dan berstatus UNCLAIMED. Kontraktor mengirim pesan, investigator menyarankan cancel lalu terbitkan ulang setelah email baru dikonfirmasi.",
  B: "Skenario B: respons PayPal pertama sengaja dibuang (fault simulasi), jadi ledger UNKNOWN. Retry dengan kunci yang sama menemukan batch asli, investigator menyarankan rekonsiliasi, manusia menyetujuinya setelah gate membaca PayPal lagi, dan tidak ada yang dibayar dua kali.",
};

function ResetDemoControl() {
  const queryClient = useQueryClient();
  const reset = useMutation({
    mutationFn: (scenario: Scenario) => resetDemo(scenario),
    // Semua data di layar lain ikut kedaluwarsa setelah reset.
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const onClick = (scenario: Scenario) => {
    if (reset.isPending) return;
    const ok = window.confirm(
      `Reset demo ke skenario ${scenario}?\n\nDatabase lokal dikosongkan (termasuk timeline event) lalu diisi ulang dengan run ID baru. Payout sandbox yang sudah terkirim tetap tercatat di PayPal.`,
    );
    if (ok) reset.mutate(scenario);
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onClick("B")}
          disabled={reset.isPending}
          className="rounded bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-50"
        >
          Reset demo (B)
        </button>
        <button
          type="button"
          onClick={() => onClick("A")}
          disabled={reset.isPending}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          Reset demo (A)
        </button>
      </div>
      {reset.isPending && <span className="text-xs text-slate-500">Mereset…</span>}
      {reset.isSuccess && (
        <span className="text-xs text-green-700">
          Direset ke skenario <span className="font-mono">{reset.data.scenario}</span>, run{" "}
          <span className="font-mono">{reset.data.run_id}</span>.
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
  const state = useAppState();
  const s = state.data;
  const currency = s?.contract?.currency;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Milestone</h1>
          <p className="text-sm text-slate-500">
            Pekerjaan diverifikasi dulu, lalu dibayar langsung dari akun pembeli setelah gate lolos dan manusia
            menyetujui. Tidak ada dana yang ditahan.
          </p>
        </div>
        <ResetDemoControl />
      </div>

      {s?.scenario && (
        <div className="rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">{SCENARIO_TEXT[s.scenario]}</div>
      )}

      {state.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat state…</div>
      )}
      {state.isError && <ErrorBox error={state.error} title="Gagal memuat /api/state" onRetry={() => state.refetch()} />}

      {s && (
        <section className="overflow-hidden rounded border border-slate-200 bg-white">
          <div className="flex flex-wrap items-baseline gap-x-3 border-b border-slate-200 px-4 py-3">
            <h2 className="font-semibold">
              {s.contract ? (
                <>
                  {s.contract.buyer_name} <span className="font-normal text-slate-400">membayar</span>{" "}
                  {s.contract.contractor_name}
                </>
              ) : (
                "Kontrak"
              )}
            </h2>
            {s.contract && (
              <span className="font-mono text-xs text-slate-500">
                {s.contract.id} · ke {s.contract.payee_email}
              </span>
            )}
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-600">data seed</span>
          </div>
          {s.milestones.length === 0 ? (
            <p className="p-4 text-sm text-slate-500">Belum ada milestone. Tekan "Reset demo (B)".</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Milestone</th>
                  <th className="px-4 py-2 text-right font-medium">Nominal</th>
                  <th className="px-4 py-2 font-medium">Status milestone</th>
                  <th className="px-4 py-2 font-medium">Payout terakhir</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {s.milestones.map((m) => {
                  const payout = latestPayoutFor(s.payouts, m.id);
                  return (
                    <tr key={m.id} className="hover:bg-slate-50">
                      <td className="px-4 py-3">
                        <div className="font-medium">{m.title}</div>
                        <div className="font-mono text-xs text-slate-400">{m.id}</div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">{formatCents(m.amount_cents, currency)}</td>
                      <td className="px-4 py-3">
                        <StatusBadge status={m.state} />
                      </td>
                      <td className="px-4 py-3">
                        {payout ? (
                          <StatusBadge status={payout.state} />
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
          )}
        </section>
      )}
    </div>
  );
}
