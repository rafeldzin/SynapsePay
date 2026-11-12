import { Link, useSearchParams } from "react-router-dom";
import type { EventRecord } from "../api/types";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import { isSimulated, paypalIds, summarizeEvent, type Tone } from "../lib/eventSummary";
import { formatTimestamp } from "../lib/format";
import { POLL_MS, useAppState } from "../lib/state";

const DOT: Record<Tone, string> = {
  green: "bg-green-600",
  red: "bg-red-600",
  amber: "bg-amber-500",
  blue: "bg-blue-600",
  slate: "bg-slate-400",
};

function TimelineItem({ event, showMilestone }: { event: EventRecord; showMilestone: boolean }) {
  const s = summarizeEvent(event);
  const ids = paypalIds(event);
  const simulated = isSimulated(event);

  return (
    <li className="relative pb-5 pl-8 last:pb-0">
      <span className="absolute left-[7px] top-4 bottom-0 w-px bg-slate-200" aria-hidden />
      <span
        className={`absolute left-0 top-1.5 h-[15px] w-[15px] rounded-full border-2 border-white ring-1 ring-slate-200 ${DOT[s.tone]}`}
        aria-hidden
      />
      <div className="rounded border border-slate-200 bg-white px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
          <span>{formatTimestamp(event.ts)}</span>
          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-slate-700">{event.type}</span>
          <span className="font-mono">{event.actor}</span>
          {simulated && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800">simulasi</span>}
          {showMilestone && event.milestone_id && (
            <Link to={`/milestones/${encodeURIComponent(event.milestone_id)}`} className="font-mono text-blue-700 hover:underline">
              {event.milestone_id}
            </Link>
          )}
          {event.payout_id && <span className="font-mono">payout {event.payout_id}</span>}
          {event.case_id && (
            <Link to={`/cases?case=${encodeURIComponent(event.case_id)}`} className="font-mono text-blue-700 hover:underline">
              kasus {event.case_id}
            </Link>
          )}
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
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </details>
      </div>
    </li>
  );
}

export default function TimelinePage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("milestone") ?? "all";
  const state = useAppState();
  const s = state.data;

  const events = (s?.events ?? []).filter((e) => selected === "all" || e.milestone_id === selected);
  const simulatedCount = events.filter(isSimulated).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Timeline event</h1>
          <p className="text-sm text-slate-500">
            Jejak audit dari tabel event append-only (300 terbaru), urut dari yang paling lama. Diperbarui tiap{" "}
            {POLL_MS / 1000} detik.
          </p>
        </div>
        <select
          value={selected}
          onChange={(e) => (e.target.value === "all" ? setParams({}) : setParams({ milestone: e.target.value }))}
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          aria-label="Filter milestone"
        >
          <option value="all">Semua event</option>
          {(s?.milestones ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} · {m.title}
            </option>
          ))}
        </select>
      </div>

      {state.isError && <ErrorBox error={state.error} title="Gagal memuat /api/state" onRetry={() => state.refetch()} />}
      {state.isPending && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Memuat event…</div>
      )}
      {s && events.length === 0 && (
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">Belum ada event.</div>
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
              <TimelineItem key={e.id} event={e} showMilestone={selected === "all"} />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
