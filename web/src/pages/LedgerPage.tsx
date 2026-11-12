import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { syncAll, syncPayout } from "../api/payouts";
import type { Milestone, Payout } from "../api/types";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatCents, formatTimestamp } from "../lib/format";
import { useAppState, useRefreshState } from "../lib/state";

function LedgerRow({ payout, milestone }: { payout: Payout; milestone: Milestone | undefined }) {
  const refresh = useRefreshState();
  const canSync = Boolean(payout.payout_batch_id && payout.state !== "UNKNOWN");
  const sync = useMutation({ mutationFn: () => syncPayout(payout.id), onSettled: refresh });

  return (
    <>
      <tr className="align-top hover:bg-slate-50">
        <td className="px-3 py-2.5">
          <Link to={`/milestones/${encodeURIComponent(payout.milestone_id)}`} className="text-blue-700 hover:underline">
            {milestone?.title ?? payout.milestone_id}
          </Link>
          <div className="font-mono text-xs text-slate-400">
            {payout.milestone_id} · percobaan #{payout.attempt} · {payout.id}
          </div>
        </td>
        <td className="px-3 py-2.5 font-mono text-xs">{payout.recipient_email}</td>
        <td className="px-3 py-2.5 text-right tabular-nums">{formatCents(payout.amount_cents, payout.currency)}</td>
        <td className="px-3 py-2.5">
          <StatusBadge status={payout.state} />
          <div className="mt-1 flex gap-1">
            <StatusBadge status={payout.batch_status} />
            <StatusBadge status={payout.item_status} />
          </div>
          <div className="mt-1 text-xs text-slate-400">{formatTimestamp(payout.updated_at)}</div>
        </td>
        <td className="px-3 py-2.5">
          <CopyableId value={payout.payout_batch_id} />
          <div className="mt-1 text-xs text-slate-400">
            sender: <span className="font-mono">{payout.sender_batch_id}</span>
          </div>
        </td>
        <td className="px-3 py-2.5">
          <CopyableId value={payout.payout_item_id} />
        </td>
        <td className="px-3 py-2.5 text-right">
          <button
            type="button"
            onClick={() => sync.mutate()}
            disabled={!canSync || sync.isPending}
            title={canSync ? "Baca status terbaru dari PayPal (read-only)" : "Payout UNKNOWN tidak disinkronkan; pakai Retry"}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sync.isPending ? "Sync…" : "Sync"}
          </button>
        </td>
      </tr>
      {sync.isError && (
        <tr>
          <td colSpan={7} className="px-3 pb-3">
            <ErrorBox error={sync.error} title={`Sync payout ${payout.id} gagal`} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function LedgerPage() {
  const state = useAppState();
  const refresh = useRefreshState();
  const all = useMutation({ mutationFn: syncAll, onSettled: refresh });
  const s = state.data;
  const milestoneById = new Map((s?.milestones ?? []).map((m) => [m.id, m]));
  const payouts = [...(s?.payouts ?? [])].reverse();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Ledger payout</h1>
          <p className="text-sm text-slate-500">
            Setiap percobaan payout, terbaru di atas. Status ledger hanya berubah lewat sync read-only atau aksi yang
            disetujui manusia. Payout UNKNOWN tidak pernah disinkronkan otomatis.
          </p>
        </div>
        <button
          type="button"
          onClick={() => all.mutate()}
          disabled={all.isPending}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          {all.isPending ? "Sinkronisasi…" : "Sync semua"}
        </button>
      </div>

      {state.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat payout…</div>
      )}
      {state.isError && <ErrorBox error={state.error} title="Gagal memuat /api/state" onRetry={() => state.refetch()} />}
      {all.isError && <ErrorBox error={all.error} title="Sync semua gagal" />}

      {s && payouts.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Belum ada payout. Payout muncul di sini setelah Approve & pay di halaman milestone.
        </div>
      )}

      {payouts.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Milestone</th>
                <th className="px-3 py-2 font-medium">Penerima</th>
                <th className="px-3 py-2 text-right font-medium">Nominal</th>
                <th className="px-3 py-2 font-medium">Ledger / batch / item</th>
                <th className="px-3 py-2 font-medium">Batch ID</th>
                <th className="px-3 py-2 font-medium">Item ID</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {payouts.map((p) => (
                <LedgerRow key={p.id} payout={p} milestone={milestoneById.get(p.milestone_id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
