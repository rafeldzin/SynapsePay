import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMilestone } from "../api/milestones";
import { getPayouts, syncPayout } from "../api/payouts";
import type { MilestoneDetail, Payout } from "../api/types";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatMoney, formatTimestamp } from "../lib/format";

function LedgerRow({ payout, milestone }: { payout: Payout; milestone: MilestoneDetail | undefined }) {
  const queryClient = useQueryClient();
  const canSync = Boolean(payout.payout_item_id || payout.payout_batch_id);

  const sync = useMutation({
    mutationFn: () => syncPayout(payout.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["payouts"] });
      queryClient.invalidateQueries({ queryKey: ["events", payout.milestone_id] });
    },
  });

  return (
    <>
      <tr className="align-top hover:bg-slate-50">
        <td className="px-3 py-2.5">
          <Link to={`/milestones/${encodeURIComponent(payout.milestone_id)}`} className="text-blue-700 hover:underline">
            {milestone?.title ?? payout.milestone_id}
          </Link>
          <div className="font-mono text-xs text-slate-400">
            {payout.milestone_id} · percobaan #{payout.attempt} · ID {payout.id}
          </div>
        </td>
        <td className="px-3 py-2.5 font-mono text-xs">{payout.receiver_email}</td>
        <td className="px-3 py-2.5 text-right tabular-nums">
          {formatMoney(payout.amount, milestone?.contract?.currency)}
        </td>
        <td className="px-3 py-2.5">
          <StatusBadge status={payout.status} />
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
            title={canSync ? "Ambil status item terbaru dari PayPal" : "Belum ada ID PayPal untuk disinkronkan"}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sync.isPending ? "Sync…" : "Sync"}
          </button>
        </td>
      </tr>
      {(sync.isSuccess || sync.isError) && (
        <tr>
          <td colSpan={7} className="px-3 pb-3">
            {sync.isError && <ErrorBox error={sync.error} title={`Sync payout ${payout.id} gagal`} />}
            {sync.isSuccess && (
              <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
                Sync payout {payout.id}: <span className="font-mono">{sync.data.old_status}</span> →{" "}
                <span className="font-mono">{sync.data.new_status}</span>
                {sync.data.flags.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-amber-900">
                    {sync.data.flags.map((flag, i) => (
                      <li key={i}>{flag}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

export default function LedgerPage() {
  const payouts = useQuery({ queryKey: ["payouts"], queryFn: getPayouts });

  // GET /payouts tidak menyertakan judul milestone dan mata uang, jadi diambil per milestone (di-cache).
  const milestoneIds = [...new Set((payouts.data ?? []).map((p) => p.milestone_id))];
  const milestoneQueries = useQueries({
    queries: milestoneIds.map((id) => ({ queryKey: ["milestone", id], queryFn: () => getMilestone(id) })),
  });
  const milestoneById = new Map<string, MilestoneDetail>();
  milestoneQueries.forEach((q) => {
    if (q.data) milestoneById.set(q.data.id, q.data);
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Ledger payout</h1>
          <p className="text-sm text-slate-500">
            Setiap percobaan payout ke PayPal, terbaru di atas. Status adalah status item dari PayPal; tekan Sync untuk
            membaca ulang langsung dari PayPal.
          </p>
        </div>
        <button
          type="button"
          onClick={() => payouts.refetch()}
          disabled={payouts.isFetching}
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50"
        >
          {payouts.isFetching ? "Memuat…" : "Muat ulang"}
        </button>
      </div>

      {payouts.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat payout…</div>
      )}
      {payouts.isError && (
        <ErrorBox error={payouts.error} title="Gagal memuat payout" onRetry={() => payouts.refetch()} />
      )}

      {payouts.isSuccess && payouts.data.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Belum ada payout. Payout muncul di sini setelah Approve & pay di halaman milestone.
        </div>
      )}

      {payouts.isSuccess && payouts.data.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Milestone</th>
                <th className="px-3 py-2 font-medium">Penerima</th>
                <th className="px-3 py-2 text-right font-medium">Nominal</th>
                <th className="px-3 py-2 font-medium">Status item PayPal</th>
                <th className="px-3 py-2 font-medium">Batch ID</th>
                <th className="px-3 py-2 font-medium">Item ID</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {payouts.data.map((p) => (
                <LedgerRow key={p.id} payout={p} milestone={milestoneById.get(p.milestone_id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
