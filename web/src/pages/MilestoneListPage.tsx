import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { contractInfo, getMilestones, resetDemo } from "../api/milestones";
import type { Milestone } from "../api/types";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatMoney } from "../lib/format";

/** Mengelompokkan milestone per kontrak, urutan sesuai respons backend. */
function groupByContract(milestones: Milestone[]) {
  const groups = new Map<string, Milestone[]>();
  for (const m of milestones) {
    const key = contractInfo(m).id ?? "__tanpa_kontrak__";
    const list = groups.get(key) ?? [];
    list.push(m);
    groups.set(key, list);
  }
  return [...groups.entries()];
}

function ContractHeader({ sample }: { sample: Milestone }) {
  const info = contractInfo(sample);
  const hasNames = info.buyer_name || info.contractor_name;
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 border-b border-slate-200 px-4 py-3">
      <h2 className="font-semibold">
        {hasNames ? (
          <>
            {info.buyer_name ?? "—"} <span className="font-normal text-slate-400">membayar</span>{" "}
            {info.contractor_name ?? "—"}
          </>
        ) : (
          "Kontrak"
        )}
      </h2>
      {info.id && <span className="font-mono text-xs text-slate-500">{info.id}</span>}
    </div>
  );
}

function MilestoneTable({ milestones }: { milestones: Milestone[] }) {
  return (
    <table className="w-full text-sm">
      <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
        <tr>
          <th className="px-4 py-2 font-medium">Milestone</th>
          <th className="px-4 py-2 font-medium">Penerima</th>
          <th className="px-4 py-2 text-right font-medium">Nominal</th>
          <th className="px-4 py-2 font-medium">Status</th>
          <th className="px-4 py-2" />
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {milestones.map((m) => (
          <tr key={m.id} className="hover:bg-slate-50">
            <td className="px-4 py-3">
              <div className="font-medium">{m.title}</div>
              <div className="font-mono text-xs text-slate-400">{m.id}</div>
            </td>
            <td className="px-4 py-3 font-mono text-xs">{m.contractor_email}</td>
            <td className="px-4 py-3 text-right tabular-nums">
              {formatMoney(m.amount, contractInfo(m).currency)}
            </td>
            <td className="px-4 py-3">
              <StatusBadge status={m.status} />
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
        ))}
      </tbody>
    </table>
  );
}

function ResetDemoButton() {
  const queryClient = useQueryClient();
  const reset = useMutation({
    mutationFn: resetDemo,
    // Semua data di layar lain ikut kedaluwarsa setelah reset.
    onSuccess: () => queryClient.invalidateQueries(),
  });

  const onClick = () => {
    if (reset.isPending) return;
    const ok = window.confirm(
      "Reset demo akan mengembalikan semua data demo ke kondisi awal di backend. Lanjutkan?",
    );
    if (ok) reset.mutate();
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={reset.isPending}
        className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {reset.isPending ? "Mereset…" : "Reset demo"}
      </button>
      {reset.isSuccess && <span className="text-xs text-green-700">Demo sudah direset.</span>}
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

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Milestone</h1>
          <p className="text-sm text-slate-500">
            Pekerjaan diverifikasi dulu, lalu dibayar langsung dari akun pembeli setelah disetujui. Tidak ada dana
            yang ditahan.
          </p>
        </div>
        <ResetDemoButton />
      </div>

      {milestones.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat milestone…</div>
      )}

      {milestones.isError && (
        <ErrorBox error={milestones.error} title="Gagal memuat milestone" onRetry={() => milestones.refetch()} />
      )}

      {milestones.isSuccess && milestones.data.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Belum ada milestone. Coba tekan "Reset demo" untuk mengisi data demo.
        </div>
      )}

      {milestones.isSuccess &&
        groupByContract(milestones.data).map(([key, items]) => (
          <section key={key} className="overflow-hidden rounded border border-slate-200 bg-white">
            <ContractHeader sample={items[0]} />
            <MilestoneTable milestones={items} />
          </section>
        ))}

      {milestones.isFetching && !milestones.isPending && (
        <div className="text-xs text-slate-400">Memperbarui…</div>
      )}
    </div>
  );
}
