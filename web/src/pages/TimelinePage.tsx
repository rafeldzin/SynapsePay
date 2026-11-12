import { useQueries, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getMilestone, getMilestoneEvents, getMilestones } from "../api/milestones";
import type { EventRecord } from "../api/types";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import { isSimulated, paypalIds, summarizeEvent, type Tone } from "../lib/eventSummary";
import { formatTimestamp } from "../lib/format";

const REFRESH_MS = 5000;

const DOT: Record<Tone, string> = {
  green: "bg-green-600",
  red: "bg-red-600",
  amber: "bg-amber-500",
  blue: "bg-blue-600",
  slate: "bg-slate-400",
};

function TimelineItem({
  event,
  showMilestone,
  currency,
}: {
  event: EventRecord;
  showMilestone: boolean;
  currency?: string;
}) {
  const s = summarizeEvent(event, currency);
  const ids = paypalIds(event);
  const simulated = isSimulated(event);

  return (
    <li className="relative pb-5 pl-8 last:pb-0">
      {/* garis vertikal dan titik */}
      <span className="absolute left-[7px] top-4 bottom-0 w-px bg-slate-200" aria-hidden />
      <span
        className={`absolute left-0 top-1.5 h-[15px] w-[15px] rounded-full border-2 border-white ring-1 ring-slate-200 ${DOT[s.tone]}`}
        aria-hidden
      />
      <div className="rounded border border-slate-200 bg-white px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span>{formatTimestamp(event.timestamp)}</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-700">{event.type}</span>
          {simulated && (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800">simulasi</span>
          )}
          {showMilestone && event.milestone_id && (
            <Link
              to={`/milestones/${encodeURIComponent(event.milestone_id)}`}
              className="font-mono text-blue-700 hover:underline"
            >
              {event.milestone_id}
            </Link>
          )}
          {event.payout_id !== null && <span className="font-mono">payout {event.payout_id}</span>}
          <span className="ml-auto font-mono text-slate-400">#{event.id}</span>
        </div>
        <div className="mt-1 text-sm">
          <span className="font-medium">{s.label}</span>
          <span className="text-slate-400"> · </span>
          <span className="text-slate-700">{s.summary}</span>
        </div>
        {ids.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
            {ids.map(({ key, value }) => (
              <span key={key} className="inline-flex items-center gap-1.5 text-xs">
                <span className="text-slate-500">{key}</span>
                <CopyableId value={value} />
              </span>
            ))}
          </div>
        )}
        <details className="mt-1.5">
          <summary className="cursor-pointer text-xs text-slate-400 hover:text-slate-600">Payload mentah</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-[11px] leading-snug text-slate-700">
            {event.payload === null ? "null" : JSON.stringify(event.payload, null, 2)}
          </pre>
        </details>
      </div>
    </li>
  );
}

export default function TimelinePage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("milestone") ?? "all";

  const milestones = useQuery({ queryKey: ["milestones"], queryFn: getMilestones });
  const ids = (milestones.data ?? []).map((m) => m.id);
  const visibleIds = selected === "all" ? ids : ids.filter((id) => id === selected);

  // Backend hanya punya GET /milestones/{id}/events, jadi event per milestone digabung di sini.
  const eventQueries = useQueries({
    queries: visibleIds.map((id) => ({
      queryKey: ["events", id],
      queryFn: () => getMilestoneEvents(id),
      refetchInterval: REFRESH_MS,
    })),
  });
  // Mata uang dari kontrak, untuk ringkasan nominal (di-cache bersama halaman lain).
  const firstId = ids[0];
  const contractQuery = useQuery({
    queryKey: ["milestone", firstId],
    queryFn: () => getMilestone(firstId),
    enabled: Boolean(firstId),
  });
  const currency = contractQuery.data?.contract?.currency;

  const events = eventQueries
    .flatMap((q) => q.data ?? [])
    .sort((a, b) => a.id - b.id);
  const loadingEvents = eventQueries.some((q) => q.isPending);
  const eventError = eventQueries.find((q) => q.isError)?.error;
  const simulatedCount = events.filter(isSimulated).length;

  const onSelect = (value: string) => {
    if (value === "all") setParams({});
    else setParams({ milestone: value });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Timeline event</h1>
          <p className="text-sm text-slate-500">
            Jejak audit dari tabel event append-only di backend, urut dari yang paling lama. Diperbarui otomatis setiap
            {" "}
            {REFRESH_MS / 1000} detik.
          </p>
        </div>
        <select
          value={selected}
          onChange={(e) => onSelect(e.target.value)}
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          aria-label="Filter milestone"
        >
          <option value="all">Semua milestone</option>
          {(milestones.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} · {m.title}
            </option>
          ))}
        </select>
      </div>

      {milestones.isError && (
        <ErrorBox error={milestones.error} title="Gagal memuat milestone" onRetry={() => milestones.refetch()} />
      )}
      {eventError && <ErrorBox error={eventError} title="Gagal memuat event" />}

      {(milestones.isPending || (loadingEvents && events.length === 0)) && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat event…</div>
      )}

      {milestones.isSuccess && !loadingEvents && events.length === 0 && !eventError && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Belum ada event. Event muncul setelah verifikasi, pembayaran, sync, atau pesan kontraktor.
        </div>
      )}

      {events.length > 0 && (
        <>
          <div className="text-xs text-slate-500">
            {events.length} event
            {simulatedCount > 0 && (
              <>
                {" "}
                · <span className="text-amber-800">{simulatedCount} berlabel simulasi</span>
              </>
            )}
          </div>
          <ol className="max-w-4xl">
            {events.map((e) => (
              <TimelineItem key={e.id} event={e} showMilestone={selected === "all"} currency={currency} />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
