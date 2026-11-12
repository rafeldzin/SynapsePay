import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ACTION_LABELS, investigateCase, postMessage, recheckCase, reconcileCase } from "../api/cases";
import { retryPayout } from "../api/payouts";
import type { ActionCode, AppState, CaseRecord, InvestigationReport, Payout, TriggerRule } from "../api/types";
import Card from "../components/Card";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatCents, formatTimestamp } from "../lib/format";
import { useAppState, useApproval, useRefreshState } from "../lib/state";

/** Aturan pembukaan kasus (PRD 11.1). Deterministik, tanpa AI. */
const TRIGGER_TEXT: Record<TriggerRule, string> = {
  D1: "Pesan kontraktor mengatakan pembayaran belum masuk.",
  D2: "Payout UNCLAIMED lebih lama dari batas waktu.",
  D3: "Status di PayPal berubah tanpa tindakan dari aplikasi.",
  D4: "Retry dengan kunci yang sama menemukan batch asli di PayPal.",
};

function pickDefaultCase(cases: CaseRecord[]): CaseRecord | undefined {
  const open = cases.filter((c) => c.state !== "RESOLVED");
  return (open.length ? open : cases)[(open.length ? open : cases).length - 1];
}

// ---------------------------------------------------------------------------
// Daftar kasus
// ---------------------------------------------------------------------------

function AllCasesTable({ cases, selected, onSelect }: { cases: CaseRecord[]; selected?: string; onSelect: (id: string) => void }) {
  if (cases.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        Belum ada kasus. Di skenario B, kasus <span className="font-mono">D4</span> dibuka saat Retry (kunci sama)
        menemukan batch asli.
      </p>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
        <tr>
          <th className="py-1.5 pr-3 font-medium">Kasus</th>
          <th className="py-1.5 pr-3 font-medium">Milestone</th>
          <th className="py-1.5 pr-3 font-medium">Aturan</th>
          <th className="py-1.5 pr-3 font-medium">Status</th>
          <th className="py-1.5 font-medium">Dibuka</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {cases.map((c) => (
          <tr key={c.id} className={c.id === selected ? "bg-blue-50" : ""}>
            <td className="py-1.5 pr-3">
              <button type="button" onClick={() => onSelect(c.id)} className="font-mono text-blue-700 hover:underline">
                {c.id}
              </button>
            </td>
            <td className="py-1.5 pr-3 font-mono">{c.milestone_id}</td>
            <td className="py-1.5 pr-3 font-mono">{c.trigger_rule}</td>
            <td className="py-1.5 pr-3">
              <StatusBadge status={c.state} />
            </td>
            <td className="py-1.5 text-xs text-slate-500">{formatTimestamp(c.opened_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Ringkasan kasus
// ---------------------------------------------------------------------------

function CaseSummaryCard({ c, payout }: { c: CaseRecord; payout: Payout | undefined }) {
  return (
    <Card title={`Kasus ${c.id}`} aside={<StatusBadge status={c.state} />}>
      <div className="space-y-3 text-sm">
        <div>
          <span className="font-mono">{c.trigger_rule}</span> · {TRIGGER_TEXT[c.trigger_rule]}
        </div>
        {c.diversion_risk ? (
          <div role="alert" className="rounded border-2 border-red-500 bg-red-50 p-3 text-red-900">
            <div className="font-semibold">Risiko pengalihan pembayaran</div>
            <div className="mt-0.5">
              Pesan meminta pembayaran ke email lain
              {c.requested_email ? (
                <>
                  {" "}
                  (<span className="font-mono">{c.requested_email}</span>)
                </>
              ) : null}
              . Perubahan penerima lewat chat harus dikonfirmasi lewat kanal asli sebelum uang bergerak.
            </div>
          </div>
        ) : null}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-slate-500">Milestone</dt>
          <dd>
            <Link to={`/milestones/${encodeURIComponent(c.milestone_id)}`} className="font-mono text-blue-700 hover:underline">
              {c.milestone_id}
            </Link>
          </dd>
          <dt className="text-slate-500">Payout</dt>
          <dd className="flex items-center gap-2">
            <span className="font-mono text-xs">{c.payout_id}</span>
            {payout && <StatusBadge status={payout.state} />}
            {payout && <span className="tabular-nums">{formatCents(payout.amount_cents, payout.currency)}</span>}
          </dd>
          {payout && (
            <>
              <dt className="text-slate-500">payout_batch_id</dt>
              <dd>
                <CopyableId value={payout.payout_batch_id} />
              </dd>
            </>
          )}
          {c.resolution && (
            <>
              <dt className="text-slate-500">Penyelesaian</dt>
              <dd className="font-mono">{c.resolution}</dd>
            </>
          )}
          <dt className="text-slate-500">Diperbarui</dt>
          <dd>{formatTimestamp(c.updated_at)}</dd>
        </dl>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// AI investigator
// ---------------------------------------------------------------------------

function ReportView({ report }: { report: InvestigationReport }) {
  const hypotheses = [...report.hypotheses].sort((a, b) => a.rank - b.rank);
  return (
    <div className="space-y-4 text-sm">
      <div>
        <div className="text-xs font-medium text-slate-500">Aksi yang disarankan</div>
        <div className="mt-0.5 flex flex-wrap items-center gap-2">
          <span className="rounded bg-fuchsia-100 px-2 py-0.5 font-mono text-fuchsia-900">{report.recommended_action}</span>
          <span>{ACTION_LABELS[report.recommended_action]}</span>
        </div>
        {report.downgraded_by && (
          <div className="mt-1 text-xs text-amber-800">
            Diturunkan oleh kode: <span className="font-mono">{report.downgraded_by}</span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-2 text-xs">
        {report.do_not_resend && (
          <span className="rounded bg-red-100 px-2 py-0.5 font-medium text-red-800">Jangan kirim ulang</span>
        )}
        {report.diversion_risk && (
          <span className="rounded bg-red-100 px-2 py-0.5 font-medium text-red-800">Risiko pengalihan</span>
        )}
        {report.required_verification && (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-amber-800">
            Verifikasi wajib: <span className="font-mono">{report.required_verification}</span>
          </span>
        )}
        <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">
          mode <span className="font-mono">{report.mode}</span> · {report.steps_used} langkah
        </span>
      </div>
      {report.resend_risk && <p className="text-slate-700">{report.resend_risk}</p>}

      <div>
        <div className="text-xs font-medium text-slate-500">Hipotesis</div>
        <ol className="mt-1 space-y-2">
          {hypotheses.map((h) => (
            <li
              key={h.code}
              className={`rounded border px-3 py-2 ${h.supported ? "border-slate-300" : "border-slate-200 bg-slate-50 text-slate-500"}`}
            >
              <div className="flex flex-wrap items-center gap-2 font-medium">
                {h.rank}. <span className="font-mono">{h.code}</span>
                <span className={`rounded px-1.5 py-0.5 text-[11px] ${h.supported ? "bg-green-100 text-green-800" : "bg-slate-200 text-slate-600"}`}>
                  {h.supported ? "didukung" : "ditolak"}
                </span>
                {h.evidence_ids.map((id) => (
                  <span key={id} className="rounded-full border border-blue-200 bg-blue-50 px-2 font-mono text-[11px] text-blue-900">
                    {id}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-xs">{h.rationale}</p>
            </li>
          ))}
        </ol>
      </div>

      <div>
        <div className="text-xs font-medium text-slate-500">Bukti (dibaca lewat tool read-only)</div>
        <ul className="mt-1 space-y-1.5">
          {report.evidence.map((e) => (
            <li key={e.id} className="rounded border border-slate-200 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span className="font-mono font-semibold text-slate-800">{e.id}</span>
                <span className="font-mono">{e.tool}</span>
                <span
                  className={`rounded px-1.5 py-0.5 ${e.source === "paypal" ? "bg-green-100 text-green-800" : "bg-slate-100 text-slate-600"}`}
                >
                  {e.source === "paypal" ? "PayPal live" : "lokal"}
                </span>
                <span>{formatTimestamp(e.fetched_at)}</span>
              </div>
              <div className="mt-0.5 text-sm">{e.summary}</div>
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-slate-400">Data mentah</summary>
                <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-50 p-2 text-[11px]">{JSON.stringify(e.data, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="text-xs font-medium text-slate-500">Draf balasan ke kontraktor (belum dikirim)</div>
        <p className="mt-1 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-slate-700">
          {report.reply_draft}
        </p>
      </div>
      <p className="text-xs text-slate-500">
        AI hanya memberi saran ({formatTimestamp(report.created_at)}). Aksi dijalankan lewat tombol di panel aksi, dan gate
        memeriksanya lagi.
      </p>
    </div>
  );
}

function InvestigatorCard({ c }: { c: CaseRecord }) {
  const refresh = useRefreshState();
  const run = useMutation({ mutationFn: () => investigateCase(c.id), onSettled: refresh });
  const report = run.data?.case_id === c.id ? run.data : c.report;
  const closed = c.state === "RESOLVED";

  return (
    <Card
      title="AI investigator"
      aside={
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending || closed}
          className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {run.isPending ? "Menyelidiki (maks 45 dtk)…" : "Run AI investigator"}
        </button>
      }
    >
      <div className="space-y-3">
        <p className="text-xs text-slate-500">Read-only: investigator hanya punya empat tool baca dan tidak bisa memindahkan uang.</p>
        {run.isError && <ErrorBox error={run.error} title="Investigasi gagal" />}
        {report ? <ReportView report={report} /> : <p className="text-sm text-slate-500">Belum diselidiki.</p>}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Aksi (dua kunci: gate + manusia)
// ---------------------------------------------------------------------------

function ActionsCard({ c, payout }: { c: CaseRecord; payout: Payout | undefined }) {
  const refresh = useRefreshState();
  const approve = useApproval();
  const closed = c.state === "RESOLVED";
  const recommended: ActionCode | null = c.report?.recommended_action ?? c.recommended_action;

  const reconcile = useMutation({
    mutationFn: (approval: NonNullable<ReturnType<typeof approve>>) => reconcileCase(c.id, approval),
    onSettled: refresh,
  });
  const recheck = useMutation({ mutationFn: () => recheckCase(c.id), onSettled: refresh });
  const retry = useMutation({
    mutationFn: (approval: NonNullable<ReturnType<typeof approve>>) => retryPayout(c.payout_id, approval),
    onSettled: refresh,
  });

  const buttonClass = (code: ActionCode) =>
    `rounded px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-40 ${
      recommended === code ? "bg-blue-700 text-white hover:bg-blue-800" : "border border-slate-300 bg-white hover:bg-slate-100"
    }`;
  const tag = (code: ActionCode) =>
    recommended === code ? (
      <span className="ml-2 rounded bg-fuchsia-100 px-1.5 py-0.5 text-[11px] text-fuchsia-800">disarankan AI</span>
    ) : null;

  return (
    <Card title="Aksi">
      <div className="space-y-3 text-sm">
        <div className="rounded border border-slate-200 px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="font-medium">
                {ACTION_LABELS.RECONCILE_MARK_PAID}
                {tag("RECONCILE_MARK_PAID")}
              </div>
              <div className="text-xs text-slate-500">
                Gate <span className="font-mono">RECONCILE</span> membaca PayPal lagi. Tidak ada uang yang dikirim; hanya
                ledger yang dikoreksi ke PAID.
              </div>
            </div>
            <button
              type="button"
              disabled={closed || reconcile.isPending}
              className={buttonClass("RECONCILE_MARK_PAID")}
              onClick={() => {
                const approval = approve(`Setujui rekonsiliasi kasus ${c.id}: tandai payout ${c.payout_id} sebagai PAID?`);
                if (approval) reconcile.mutate(approval);
              }}
            >
              {reconcile.isPending ? "Memeriksa PayPal…" : "Approve reconcile (mark paid)"}
            </button>
          </div>
          {reconcile.isSuccess && (
            <div className="mt-2 rounded border border-green-200 bg-green-50 p-2 text-green-900">
              Payout <span className="font-mono">{reconcile.data.payout.state}</span> · kasus{" "}
              <span className="font-mono">{reconcile.data.case.state}</span>
              {reconcile.data.case.resolution ? (
                <>
                  {" "}
                  (<span className="font-mono">{reconcile.data.case.resolution}</span>)
                </>
              ) : null}
            </div>
          )}
          {reconcile.isError && (
            <div className="mt-2">
              <ErrorBox error={reconcile.error} title="Rekonsiliasi ditolak" />
            </div>
          )}
        </div>

        {payout?.state === "UNKNOWN" && (
          <div className="rounded border border-slate-200 px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-medium">
                  {ACTION_LABELS.RETRY_SAME_BATCH_ID}
                  {tag("RETRY_SAME_BATCH_ID")}
                </div>
                <div className="font-mono text-xs text-slate-500">{payout.sender_batch_id}</div>
              </div>
              <button
                type="button"
                disabled={closed || retry.isPending}
                className={buttonClass("RETRY_SAME_BATCH_ID")}
                onClick={() => {
                  const approval = approve(`Retry payout ${payout.id} dengan sender_batch_id yang sama?`);
                  if (approval) retry.mutate(approval);
                }}
              >
                {retry.isPending ? "Retry…" : "Approve retry (same batch ID)"}
              </button>
            </div>
            {retry.isError && (
              <div className="mt-2">
                <ErrorBox error={retry.error} title="Retry ditolak" />
              </div>
            )}
          </div>
        )}

        <div className="rounded border border-slate-200 px-3 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="font-medium">
                Cek ulang status (read-only){tag("WAIT_AND_RECHECK")}
              </div>
              <div className="text-xs text-slate-500">Sync payout kasus. Payout yang sudah diklaim menyelesaikan kasus.</div>
            </div>
            <button
              type="button"
              disabled={closed || recheck.isPending}
              className={buttonClass("WAIT_AND_RECHECK")}
              onClick={() => recheck.mutate()}
            >
              {recheck.isPending ? "Membaca PayPal…" : "Recheck"}
            </button>
          </div>
          {recheck.isError && (
            <div className="mt-2">
              <ErrorBox error={recheck.error} title="Recheck gagal" />
            </div>
          )}
        </div>

        <div className="rounded border border-dashed border-slate-300 px-3 py-2 text-slate-500">
          <div className="font-medium">
            {ACTION_LABELS.CANCEL_UNCLAIMED_AND_REISSUE}
            {tag("CANCEL_UNCLAIMED_AND_REISSUE")}
          </div>
          <div className="text-xs">
            Route cancel dan reissue belum ada di backend (paket A5), jadi aksi ini belum bisa dijalankan dari sini.
          </div>
        </div>

        <p className="text-xs text-slate-500">
          Tidak ada tombol kirim ulang biasa. Kirim ulang tanpa memeriksa status PayPal bisa membuat pembayaran ganda.
        </p>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Inbox kontraktor (simulasi)
// ---------------------------------------------------------------------------

function InboxCard({ state }: { state: AppState }) {
  const refresh = useRefreshState();
  const [from, setFrom] = useState("");
  const [body, setBody] = useState("");
  const contractId = state.contract?.id;

  const send = useMutation({
    mutationFn: () => postMessage(contractId!, from.trim(), body),
    onSuccess: () => setBody(""),
    onSettled: refresh,
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!contractId || !from.trim() || !body.trim() || send.isPending) return;
    send.mutate();
  };

  return (
    <Card
      title="Inbox kontraktor"
      aside={<span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">simulasi</span>}
    >
      <form onSubmit={onSubmit} className="space-y-2">
        <input
          type="email"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          placeholder="Email pengirim"
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={3}
          placeholder="Pesan kontraktor, mis. pembayaran milestone belum masuk."
          className="w-full rounded border border-slate-300 p-2 text-sm"
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!contractId || !from.trim() || !body.trim() || send.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {send.isPending ? "Mengirim…" : "Kirim pesan"}
          </button>
          <span className="text-xs text-slate-500">Aturan D1 bisa membuka kasus.</span>
        </div>
        {send.isError && <ErrorBox error={send.error} title="Pesan gagal dikirim" />}
      </form>

      {state.messages.length > 0 && (
        <ul className="mt-4 space-y-2">
          {state.messages.map((m) => (
            <li key={m.id} className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
              <div className="flex justify-between gap-2 text-xs text-slate-500">
                <span className="font-mono">{m.from_email}</span>
                <span>{formatTimestamp(m.created_at)}</span>
              </div>
              <div className="mt-1 whitespace-pre-wrap">{m.body}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Halaman
// ---------------------------------------------------------------------------

export default function CasePage() {
  const [params, setParams] = useSearchParams();
  const state = useAppState();
  const s = state.data;
  const cases = s?.cases ?? [];
  const selectedId = params.get("case") ?? pickDefaultCase(cases)?.id;
  const selected = cases.find((c) => c.id === selectedId);
  const payout = selected ? s?.payouts.find((p) => p.id === selected.payout_id) : undefined;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Kasus</h1>
        <p className="text-sm text-slate-500">
          Kasus dibuka oleh aturan deterministik. AI menyarankan, gate memeriksa dengan pembacaan PayPal terbaru, dan
          manusia menyetujui di request yang sama.
        </p>
      </div>

      {state.isError && <ErrorBox error={state.error} title="Gagal memuat /api/state" onRetry={() => state.refetch()} />}

      <Card title="Semua kasus">
        {state.isPending ? (
          <p className="text-sm text-slate-500">Memuat kasus…</p>
        ) : (
          <AllCasesTable cases={cases} selected={selected?.id} onSelect={(id) => setParams({ case: id })} />
        )}
      </Card>

      {s && params.get("case") && !selected && (
        <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
          Kasus <span className="font-mono">{params.get("case")}</span> tidak ada di run ini.
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          {selected && <CaseSummaryCard c={selected} payout={payout} />}
          {selected && <ActionsCard key={selected.id} c={selected} payout={payout} />}
          {s && <InboxCard state={s} />}
        </div>
        <div className="space-y-5">{selected && <InvestigatorCard key={selected.id} c={selected} />}</div>
      </div>
    </div>
  );
}
