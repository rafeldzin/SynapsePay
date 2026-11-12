import { useState, type ChangeEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { FIXTURES, payMilestone, submitFixture, uploadDeliverable } from "../api/milestones";
import { latestPayoutFor, retryPayout, syncPayout } from "../api/payouts";
import type { AppState, Milestone, Payout } from "../api/types";
import Card from "../components/Card";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import GateChecklist from "../components/GateChecklist";
import StatusBadge from "../components/StatusBadge";
import VerdictPanel from "../components/VerdictPanel";
import { formatCents, formatTimestamp } from "../lib/format";
import { useAppState, useApproval, useRefreshState } from "../lib/state";

const MAX_UPLOAD_BYTES = 5 * 1024 * 1024; // per file (PRD 12)

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function MilestoneHeader({ m, state }: { m: Milestone; state: AppState }) {
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-sm">
        <Link to="/" className="text-blue-700 hover:underline">
          ← Semua milestone
        </Link>
        <Link to={`/timeline?milestone=${encodeURIComponent(m.id)}`} className="text-blue-700 hover:underline">
          Lihat timeline event →
        </Link>
      </div>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{m.title}</h1>
          <div className="flex items-center gap-2 text-sm text-slate-500">
            {state.contract?.buyer_name ?? "—"} membayar {state.contract?.contractor_name ?? "—"} ·{" "}
            <span className="font-mono text-xs">{m.id}</span>
            <StatusBadge status={m.state} />
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">{formatCents(m.amount_cents, state.contract?.currency)}</div>
          <div className="font-mono text-xs text-slate-500">ke {state.contract?.payee_email ?? "—"}</div>
        </div>
      </div>
      {m.criteria && m.criteria.length > 0 && (
        <ul className="space-y-0.5 text-sm">
          {m.criteria.map((c) => (
            <li key={c.id}>
              <span className="font-mono text-xs text-slate-500">{c.id}</span> {c.text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Deliverable + verifikasi
// ---------------------------------------------------------------------------

function DeliverableCard({ milestone }: { milestone: Milestone }) {
  const refresh = useRefreshState();
  const [csv, setCsv] = useState<File | null>(null);
  const [report, setReport] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const locked = milestone.state === "PAYOUT_IN_FLIGHT" || milestone.state === "PAID";

  const fixture = useMutation({
    mutationFn: (name: string) => submitFixture(milestone.id, name),
    onSettled: refresh,
  });
  const upload = useMutation({
    mutationFn: () => uploadDeliverable(milestone.id, csv!, report!),
    onSettled: refresh,
  });
  const busy = fixture.isPending || upload.isPending;

  const pick = (setter: (f: File | null) => void) => (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    setFileError(null);
    if (file && file.size > MAX_UPLOAD_BYTES) {
      setFileError(`${file.name} lebih dari 5 MB.`);
      setter(null);
      return;
    }
    setter(file);
  };

  const fixtures = FIXTURES.filter((f) => f.milestoneId === milestone.id);
  const lastError = fixture.error ?? upload.error;

  return (
    <Card step={1} title="Deliverable kontraktor">
      <div className="space-y-4">
        {locked && (
          <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
            Milestone sudah dalam proses pembayaran atau sudah dibayar, jadi deliverable baru ditolak backend.
          </div>
        )}
        {fixtures.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-xs font-medium text-slate-500">Fixture yang di-seed</div>
            <div className="flex flex-wrap gap-2">
              {fixtures.map((f) => (
                <button
                  key={f.name}
                  type="button"
                  disabled={busy || locked}
                  onClick={() => fixture.mutate(f.name)}
                  className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <span className="mr-1.5 rounded bg-slate-100 px-1 text-[11px] text-slate-600">seed</span>
                  <span className="font-mono">{fixture.isPending && fixture.variables === f.name ? "Memverifikasi…" : f.name}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2 border-t border-slate-100 pt-3">
          <div className="text-xs font-medium text-slate-500">Atau unggah CSV + laporan (maks 5 MB per file)</div>
          <label className="block text-sm">
            CSV{" "}
            <input type="file" accept=".csv,text/csv" onChange={pick(setCsv)} className="text-sm" disabled={busy || locked} />
          </label>
          <label className="block text-sm">
            Laporan{" "}
            <input
              type="file"
              accept=".md,.txt,text/*"
              onChange={pick(setReport)}
              className="text-sm"
              disabled={busy || locked}
            />
          </label>
          {fileError && <div className="text-sm text-red-700">{fileError}</div>}
          <button
            type="button"
            onClick={() => upload.mutate()}
            disabled={busy || locked || !csv || !report}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {upload.isPending ? "Memverifikasi…" : "Unggah dan verifikasi"}
          </button>
          <p className="text-xs text-slate-500">Isi deliverable dikirim ke verifier sebagai data, bukan perintah.</p>
        </div>
        {lastError && <ErrorBox error={lastError} title="Verifikasi gagal" />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 2. Verdict
// ---------------------------------------------------------------------------

function VerdictCard({ milestone }: { milestone: Milestone }) {
  const v = milestone.verification;
  return (
    <Card
      step={2}
      title="Hasil verifikasi"
      aside={v && <span className="text-xs text-slate-500">{formatTimestamp(v.created_at)}</span>}
    >
      {v ? (
        <VerdictPanel result={v} criteria={milestone.criteria} />
      ) : (
        <p className="text-sm text-slate-500">Belum ada verdict. Pilih fixture atau unggah deliverable.</p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 3. Gate + Approve & pay
// ---------------------------------------------------------------------------

function GatePayCard({ milestone, currency }: { milestone: Milestone; currency?: string }) {
  const refresh = useRefreshState();
  const approve = useApproval();
  const preview = milestone.gate_preview;
  const amountLabel = formatCents(milestone.amount_cents, currency);

  const pay = useMutation({
    mutationFn: (approval: NonNullable<ReturnType<typeof approve>>) =>
      payMilestone(milestone.id, milestone.amount_cents, approval),
    onSettled: refresh,
  });

  // Tombol aktif hanya kalau semua cek non-manusia di pratinjau backend hijau. Gate tetap
  // dievaluasi ulang di server saat klik, dengan persetujuan di request yang sama.
  const ready = preview.checks.every((c) => c.ok !== false);

  const onPay = () => {
    if (pay.isPending) return;
    const approval = approve(`Setujui dan bayar ${amountLabel} untuk ${milestone.id} lewat PayPal Payouts (sandbox)?`);
    if (approval) pay.mutate(approval);
  };

  return (
    <Card step={3} title="Gate dan persetujuan">
      <div className="space-y-4">
        <div className="space-y-2">
          <div className="text-sm text-slate-600">
            Pratinjau gate <span className="font-mono">{preview.action}</span> dari backend ·{" "}
            {formatTimestamp(preview.evaluated_at)}
          </div>
          <GateChecklist checks={preview.checks} pendingLabel="Menunggu klik Approve & pay." />
        </div>

        <div className="flex flex-wrap items-center gap-4 border-t border-slate-100 pt-4">
          <button
            type="button"
            onClick={onPay}
            disabled={pay.isPending || !ready}
            className="rounded bg-blue-700 px-5 py-2 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {pay.isPending ? "Memproses pembayaran…" : `Approve & pay ${amountLabel}`}
          </button>
        </div>
        {!ready && <div className="text-xs text-slate-500">Tombol nonaktif karena ada cek gate yang belum hijau.</div>}

        {pay.isSuccess && (
          <div
            className={`rounded border p-3 text-sm ${
              pay.data.payout.state === "UNKNOWN"
                ? "border-amber-300 bg-amber-50 text-amber-900"
                : "border-green-200 bg-green-50 text-green-900"
            }`}
          >
            <div className="font-semibold">
              Gate lolos. Payout <span className="font-mono">{pay.data.payout.id}</span> berstatus{" "}
              <span className="font-mono">{pay.data.payout.state}</span>.
            </div>
            {pay.data.payout.state === "UNKNOWN" && (
              <div className="mt-1">
                Respons PayPal tidak diterima, jadi hasilnya belum pasti. Jangan kirim ulang manual: pakai{" "}
                <strong>Retry (kunci sama)</strong> di panel payout. Retry memakai sender_batch_id yang sama, jadi PayPal
                tidak akan membayar dua kali.
              </div>
            )}
          </div>
        )}
        {pay.isError && <ErrorBox error={pay.error} title="Pembayaran ditolak atau gagal" />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 4. Payout PayPal
// ---------------------------------------------------------------------------

function PayoutCard({ payout, state }: { payout: Payout | null; state: AppState }) {
  const refresh = useRefreshState();
  const approve = useApproval();
  const sync = useMutation({ mutationFn: () => syncPayout(payout!.id), onSettled: refresh });
  const retry = useMutation({
    mutationFn: (approval: NonNullable<ReturnType<typeof approve>>) => retryPayout(payout!.id, approval),
    onSettled: refresh,
  });

  const simulated = payout ? state.events.some((e) => e.payout_id === payout.id && e.payload?.simulated_fault) : false;
  const canSync = Boolean(payout && payout.payout_batch_id && payout.state !== "UNKNOWN");
  const openCase = payout ? state.cases.find((c) => c.payout_id === payout.id && c.state !== "RESOLVED") : undefined;

  const onRetry = () => {
    if (!payout || retry.isPending) return;
    const approval = approve(
      `Retry payout ${payout.id} dengan sender_batch_id yang sama (${payout.sender_batch_id})?\n\nKalau PayPal sudah punya batch-nya, tidak ada pembayaran kedua.`,
    );
    if (approval) retry.mutate(approval);
  };

  return (
    <Card
      step={4}
      title="Payout PayPal"
      aside={
        payout && (
          <button
            type="button"
            onClick={() => sync.mutate()}
            disabled={!canSync || sync.isPending}
            title={canSync ? "Baca status terbaru dari PayPal (read-only)" : "Payout UNKNOWN tidak disinkronkan; pakai Retry"}
            className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sync.isPending ? "Sinkronisasi…" : "Sync"}
          </button>
        )
      }
    >
      {!payout ? (
        <p className="text-sm text-slate-500">Belum ada payout untuk milestone ini.</p>
      ) : (
        <div className="space-y-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-slate-500">Status ledger</dt>
            <dd className="flex items-center gap-2">
              <StatusBadge status={payout.state} />
              {simulated && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">fault simulasi</span>
              )}
            </dd>
            <dt className="text-slate-500">Batch / item PayPal</dt>
            <dd className="flex gap-2">
              <StatusBadge status={payout.batch_status} /> <StatusBadge status={payout.item_status} />
            </dd>
            <dt className="text-slate-500">payout_batch_id</dt>
            <dd>
              <CopyableId value={payout.payout_batch_id} />
            </dd>
            <dt className="text-slate-500">payout_item_id</dt>
            <dd>
              <CopyableId value={payout.payout_item_id} />
            </dd>
            <dt className="text-slate-500">sender_batch_id</dt>
            <dd>
              <CopyableId value={payout.sender_batch_id} />
            </dd>
            <dt className="text-slate-500">Penerima</dt>
            <dd className="font-mono text-xs">{payout.recipient_email}</dd>
            <dt className="text-slate-500">Nominal</dt>
            <dd className="tabular-nums">{formatCents(payout.amount_cents, payout.currency)}</dd>
            <dt className="text-slate-500">Percobaan</dt>
            <dd>
              #{payout.attempt} · <span className="font-mono text-xs">{payout.id}</span> · disetujui {payout.approved_by}
            </dd>
            <dt className="text-slate-500">Sync terakhir</dt>
            <dd>{formatTimestamp(payout.last_synced_at)}</dd>
          </dl>

          {payout.state === "UNKNOWN" && (
            <div className="space-y-2 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              <div>
                Hasil PayPal belum pasti. Retry memakai sender_batch_id yang sama: kalau batch asli ada, PayPal
                menolaknya dengan link ke batch asli, dan backend membuka kasus <span className="font-mono">D4</span>.
              </div>
              <button
                type="button"
                onClick={onRetry}
                disabled={retry.isPending}
                className="rounded bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {retry.isPending ? "Retry…" : "Retry (kunci sama)"}
              </button>
            </div>
          )}

          {retry.isSuccess && (
            <div className="rounded border border-slate-200 bg-slate-50 p-3 text-sm">
              Retry: payout <span className="font-mono">{retry.data.payout.state}</span>
              {retry.data.case_id ? (
                <>
                  {" "}
                  · kasus <span className="font-mono">{retry.data.case_id}</span> dibuka.{" "}
                  <Link to={`/cases?case=${encodeURIComponent(retry.data.case_id)}`} className="text-blue-700 hover:underline">
                    Buka kasus →
                  </Link>
                </>
              ) : null}
            </div>
          )}
          {openCase && !retry.isSuccess && (
            <div className="text-sm">
              Kasus terbuka <span className="font-mono">{openCase.id}</span> ({openCase.trigger_rule}).{" "}
              <Link to={`/cases?case=${encodeURIComponent(openCase.id)}`} className="text-blue-700 hover:underline">
                Buka kasus →
              </Link>
            </div>
          )}
          {retry.isError && <ErrorBox error={retry.error} title="Retry ditolak atau gagal" />}
          {sync.isError && <ErrorBox error={sync.error} title="Sinkronisasi gagal" />}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Halaman
// ---------------------------------------------------------------------------

export default function MilestoneDetailPage() {
  const { id = "" } = useParams();
  const state = useAppState();

  if (state.isPending) return <div className="text-sm text-slate-500">Memuat milestone…</div>;
  if (state.isError) {
    return <ErrorBox error={state.error} title="Gagal memuat /api/state" onRetry={() => state.refetch()} />;
  }

  const s = state.data;
  const m = s.milestones.find((x) => x.id === id);
  if (!m) {
    return (
      <div className="space-y-3">
        <Link to="/" className="text-sm text-blue-700 hover:underline">
          ← Semua milestone
        </Link>
        <div className="rounded border border-slate-200 bg-white p-6 text-sm text-slate-500">
          Milestone <span className="font-mono">{id}</span> tidak ada di run ini.
        </div>
      </div>
    );
  }

  const payout = latestPayoutFor(s.payouts, m.id);

  return (
    <div className="space-y-5">
      <MilestoneHeader m={m} state={s} />
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <DeliverableCard milestone={m} />
          <VerdictCard milestone={m} />
        </div>
        <div className="space-y-5">
          <GatePayCard milestone={m} currency={s.contract?.currency} />
          <PayoutCard payout={payout} state={s} />
        </div>
      </div>
    </div>
  );
}
