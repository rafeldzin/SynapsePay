import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { getGatePreview, getMilestone, getMilestoneEvents, payMilestone, verifyMilestone } from "../api/milestones";
import { getPayouts, IN_FLIGHT_STATUSES, latestPayoutFor, syncPayout } from "../api/payouts";
import type { EventRecord, MilestoneDetail, PayResponse, Payout } from "../api/types";
import Card from "../components/Card";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import GateChecklist from "../components/GateChecklist";
import StatusBadge from "../components/StatusBadge";
import VerdictPanel from "../components/VerdictPanel";
import { acceptanceCriteria, gateFromEvent, latestEvent, payloadObject, verdictFromEvent } from "../lib/events";
import { formatMoney, formatTimestamp } from "../lib/format";

const POLL_INTERVAL_MS = 4000;
const MAX_POLLS = 15;

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function MilestoneHeader({ m }: { m: MilestoneDetail }) {
  const criteria = acceptanceCriteria(m.acceptance_json);
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
          <div className="text-sm text-slate-500">
            {m.contract?.buyer_name ?? "—"} membayar {m.contract?.contractor_name ?? "—"} ·{" "}
            <span className="font-mono text-xs">{m.id}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold tabular-nums">{formatMoney(m.amount, m.contract?.currency)}</div>
          <div className="font-mono text-xs text-slate-500">ke {m.contractor_email}</div>
        </div>
      </div>
      {criteria.length > 0 && (
        <div className="text-sm">
          <span className="text-slate-500">Kriteria penerimaan: </span>
          {criteria.join(" · ")}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1. Deliverable + verifikasi
// ---------------------------------------------------------------------------

function DeliverableCard({ milestoneId }: { milestoneId: string }) {
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  const verify = useMutation({
    mutationFn: () => verifyMilestone(milestoneId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["events", milestoneId] });
      queryClient.invalidateQueries({ queryKey: ["gatePreview", milestoneId] });
    },
  });

  const onFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    setFileError(null);
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      setFileError("File lebih dari 2 MB. Untuk demo, pakai file teks/CSV yang kecil.");
      return;
    }
    try {
      setContent(await file.text());
      setFileName(file.name);
    } catch {
      setFileError("File tidak bisa dibaca sebagai teks.");
    }
  };

  return (
    <Card step={1} title="Deliverable kontraktor">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input
            type="file"
            accept=".csv,.txt,.md,.json,text/*"
            onChange={onFile}
            className="text-sm file:mr-3 file:rounded file:border file:border-slate-300 file:bg-white file:px-3 file:py-1 file:text-sm hover:file:bg-slate-100"
          />
          {fileName && <span className="text-slate-500">Dimuat: {fileName}</span>}
        </div>
        {fileError && <div className="text-sm text-red-700">{fileError}</div>}
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={6}
          placeholder="Unggah file, atau tempel isi deliverable / laporan kontraktor di sini."
          className="w-full rounded border border-slate-300 p-2 font-mono text-xs"
        />
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => verify.mutate()}
            disabled={verify.isPending || !content.trim()}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {verify.isPending ? "Memverifikasi…" : "Verifikasi"}
          </button>
          <span className="text-xs text-slate-500">Isi deliverable dikirim ke verifier sebagai data, bukan perintah.</span>
        </div>
        {verify.isError && <ErrorBox error={verify.error} title="Verifikasi gagal" />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 2. Verdict (dari event VERDICT_RECORDED terbaru)
// ---------------------------------------------------------------------------

function VerdictCard({ verdictEvent }: { verdictEvent: EventRecord | null }) {
  const verdict = verdictFromEvent(verdictEvent);
  return (
    <Card
      step={2}
      title="Hasil verifikasi"
      aside={
        verdictEvent && <span className="text-xs text-slate-500">{formatTimestamp(verdictEvent.timestamp)}</span>
      }
    >
      {verdict ? (
        <VerdictPanel verdict={verdict} />
      ) : (
        <p className="text-sm text-slate-500">Belum ada verdict. Unggah deliverable lalu klik Verifikasi.</p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 3. Gate + Approve & pay
// ---------------------------------------------------------------------------

function PayResultPanel({ result, showChecks }: { result: PayResponse; showChecks: boolean }) {
  if (result.success) {
    return (
      <div className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-900">
        <div className="font-semibold">Payout terkirim ke PayPal sandbox</div>
        <div className="mt-1">Status dan ID lengkap ada di panel Payout PayPal di bawah.</div>
      </div>
    );
  }
  if (result.reconcile_required) {
    return (
      <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        <div className="font-semibold">
          Respons PayPal tidak diterima{result.simulated_lost_response ? " (simulasi)" : ""}
        </div>
        <div className="mt-1">{result.error}</div>
        <div className="mt-1">
          Payout disimpan dengan status <span className="font-mono">{result.status}</span> dan{" "}
          <span className="font-mono">sender_batch_id {result.sender_batch_id ?? "—"}</span>. Jangan kirim ulang
          manual; status sebenarnya harus direkonsiliasi dulu.
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-900">
      <div className="font-semibold">Ditolak gate. Tidak ada panggilan ke PayPal.</div>
      {result.error && <div>{result.error}</div>}
      {showChecks && result.gate_result ? (
        <GateChecklist checks={result.gate_result.checks} />
      ) : (
        <div className="text-xs">Cek yang gagal ditandai merah di checklist gate di atas.</div>
      )}
    </div>
  );
}

function PayoutErrorEventPanel({ event }: { event: EventRecord }) {
  const p = payloadObject(event) ?? {};
  return (
    <div className="rounded border border-red-200 bg-white p-3 text-sm">
      <div className="text-xs text-slate-500">Dari event PAYOUT_ERROR #{event.id} yang dicatat backend:</div>
      <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-slate-500">Error</dt>
        <dd className="font-mono">{String(p.name ?? p.error_type ?? "—")}</dd>
        <dt className="text-slate-500">Detail</dt>
        <dd className="break-all">{String(p.detail ?? "—")}</dd>
        <dt className="text-slate-500">debug_id</dt>
        <dd className="font-mono">{String(p.debug_id ?? "—")}</dd>
      </dl>
    </div>
  );
}

function GatePayCard({
  milestone,
  events,
  verdictEvent,
}: {
  milestone: MilestoneDetail;
  events: EventRecord[];
  verdictEvent: EventRecord | null;
}) {
  const queryClient = useQueryClient();
  const [simulateLost, setSimulateLost] = useState(false);
  const [payoutErrorEvent, setPayoutErrorEvent] = useState<EventRecord | null>(null);
  const lastEventIdBeforePay = useRef(0);

  const preview = useQuery({
    queryKey: ["gatePreview", milestone.id],
    queryFn: () => getGatePreview(milestone.id),
  });

  const pay = useMutation({
    mutationFn: () => payMilestone(milestone.id, { simulateLostResponse: simulateLost }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["events", milestone.id] });
      queryClient.invalidateQueries({ queryKey: ["payouts"] });
      queryClient.invalidateQueries({ queryKey: ["gatePreview", milestone.id] });
    },
    onError: async () => {
      // Backend saat ini membalas error PayPal sebagai HTTP 500 teks polos.
      // Nama error dan debug_id tetap tercatat di event PAYOUT_ERROR, jadi diambil dari sana.
      const fresh = await queryClient.fetchQuery({
        queryKey: ["events", milestone.id],
        queryFn: () => getMilestoneEvents(milestone.id),
      });
      const found = [...fresh]
        .reverse()
        .find((e) => e.type === "PAYOUT_ERROR" && e.id > lastEventIdBeforePay.current);
      setPayoutErrorEvent(found ?? null);
    },
  });

  const onPay = () => {
    if (pay.isPending) return;
    const amount = formatMoney(milestone.amount, milestone.contract?.currency);
    const ok = window.confirm(
      `Setujui dan kirim ${amount} ke ${milestone.contractor_email} lewat PayPal Payouts (sandbox)?\n\n` +
        "Gate backend akan memeriksa semua syarat sebelum PayPal dipanggil.",
    );
    if (!ok) return;
    lastEventIdBeforePay.current = events.reduce((max, e) => Math.max(max, e.id), 0);
    setPayoutErrorEvent(null);
    pay.mutate();
  };

  // Sumber checklist: pratinjau backend kalau ada; kalau tidak, evaluasi gate terakhir yang tercatat.
  const previewResult = preview.data ?? null;
  const lastGateEvent = latestEvent(events, "GATE_EVALUATED");
  const lastGate = gateFromEvent(lastGateEvent);
  const gateIsStale = Boolean(lastGateEvent && verdictEvent && lastGateEvent.id < verdictEvent.id);

  // Tombol aktif hanya kalau semua cek dari backend hijau. Satu-satunya pengecualian adalah
  // human_approved, karena klik tombol inilah persetujuannya. Kalau backend belum punya
  // pratinjau gate, tombol tetap bisa diklik dan gate backend yang memutuskan.
  const previewBlocked =
    previewResult !== null && previewResult.checks.some((c) => !c.passed && c.name !== "human_approved");
  const disabled = pay.isPending || preview.isPending || previewBlocked;
  const amountLabel = formatMoney(milestone.amount, milestone.contract?.currency);

  return (
    <Card step={3} title="Gate dan persetujuan">
      <div className="space-y-4">
        {preview.isPending && <div className="text-sm text-slate-500">Memuat pratinjau gate…</div>}
        {preview.isError && <ErrorBox error={preview.error} title="Gagal memuat pratinjau gate" />}

        {previewResult && (
          <div className="space-y-2">
            <div className="text-sm text-slate-600">Pratinjau gate dari backend:</div>
            <GateChecklist checks={previewResult.checks} humanApprovalPending />
          </div>
        )}

        {preview.isSuccess && previewResult === null && (
          <div className="space-y-2">
            <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
              Backend belum menyediakan pratinjau gate (<span className="font-mono">GET /milestones/{"{id}"}/gate</span>
              ). Gate dievaluasi saat tombol diklik; kalau ada cek yang gagal, PayPal tidak dipanggil.
            </div>
            {lastGate ? (
              <>
                <div className="text-sm text-slate-600">
                  Evaluasi gate terakhir · {formatTimestamp(lastGateEvent?.timestamp)}
                  {gateIsStale && (
                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                      sebelum verdict terbaru
                    </span>
                  )}
                </div>
                <GateChecklist checks={lastGate.checks ?? []} />
              </>
            ) : (
              <div className="text-sm text-slate-500">Gate belum pernah dievaluasi untuk milestone ini.</div>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4 border-t border-slate-100 pt-4">
          <button
            type="button"
            onClick={onPay}
            disabled={disabled}
            className="rounded bg-blue-700 px-5 py-2 font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {pay.isPending ? "Memproses pembayaran…" : `Approve & pay ${amountLabel}`}
          </button>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={simulateLost}
              disabled={pay.isPending}
              onChange={(e) => setSimulateLost(e.target.checked)}
            />
            <span>
              <span className="rounded bg-amber-100 px-1 text-amber-800">simulasi</span> Buang respons PayPal setelah
              dikirim (demo timeout)
            </span>
          </label>
        </div>
        {previewBlocked && (
          <div className="text-xs text-slate-500">Tombol nonaktif karena ada cek gate yang belum hijau.</div>
        )}

        {/* Tanpa pratinjau, checklist di atas sudah menampilkan evaluasi dari klik ini. */}
        {pay.isSuccess && <PayResultPanel result={pay.data} showChecks={previewResult !== null} />}
        {pay.isError && (
          <div className="space-y-2">
            <ErrorBox error={pay.error} title="Pembayaran gagal" />
            {payoutErrorEvent && <PayoutErrorEventPanel event={payoutErrorEvent} />}
          </div>
        )}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 4. Payout PayPal + polling status
// ---------------------------------------------------------------------------

function PayoutCard({ payout, currency }: { payout: Payout | null; currency?: string }) {
  const queryClient = useQueryClient();
  const canSync = Boolean(payout && (payout.payout_item_id || payout.payout_batch_id));
  const shouldPoll = Boolean(payout && canSync && IN_FLIGHT_STATUSES.includes(payout.status.toUpperCase()));

  // Polling otomatis: setiap putaran memanggil POST /payouts/{id}/sync (backend membaca status live
  // dari PayPal). Berhenti saat status tidak lagi PENDING/PROCESSING atau setelah MAX_POLLS kali.
  const poll = useQuery({
    queryKey: ["syncPoll", payout?.id],
    queryFn: () => syncPayout(payout!.id),
    enabled: shouldPoll,
    refetchInterval: (query) => {
      const status = query.state.data?.new_status?.toUpperCase();
      if (status && !IN_FLIGHT_STATUSES.includes(status)) return false;
      if (query.state.dataUpdateCount >= MAX_POLLS) return false;
      return POLL_INTERVAL_MS;
    },
    gcTime: 0,
  });

  const manualSync = useMutation({
    mutationFn: () => syncPayout(payout!.id),
  });

  const lastSync = manualSync.data ?? poll.data;

  // Jumlah putaran polling yang sudah selesai, untuk ditampilkan.
  const [pollCount, setPollCount] = useState(0);
  useEffect(() => {
    if (poll.dataUpdatedAt) setPollCount((c) => c + 1);
  }, [poll.dataUpdatedAt]);

  useEffect(() => {
    if (poll.dataUpdatedAt || manualSync.isSuccess) {
      queryClient.invalidateQueries({ queryKey: ["payouts"] });
      if (payout) queryClient.invalidateQueries({ queryKey: ["events", payout.milestone_id] });
    }
  }, [poll.dataUpdatedAt, manualSync.isSuccess, manualSync.data, queryClient, payout?.milestone_id]);

  return (
    <Card
      step={4}
      title="Payout PayPal"
      aside={
        payout && (
          <button
            type="button"
            onClick={() => manualSync.mutate()}
            disabled={!canSync || manualSync.isPending || poll.isFetching}
            title={canSync ? "Ambil status terbaru dari PayPal" : "Belum ada ID PayPal untuk disinkronkan"}
            className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {manualSync.isPending || poll.isFetching ? "Sinkronisasi…" : "Sync"}
          </button>
        )
      }
    >
      {!payout ? (
        <p className="text-sm text-slate-500">Belum ada payout untuk milestone ini.</p>
      ) : (
        <div className="space-y-3">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-slate-500">Status item</dt>
            <dd className="flex items-center gap-2">
              <StatusBadge status={payout.status} />
              {shouldPoll && poll.isFetching && <span className="text-xs text-slate-500">memeriksa PayPal…</span>}
              {shouldPoll && pollCount > 0 && (
                <span className="text-xs text-slate-400">
                  cek otomatis {pollCount}/{MAX_POLLS}
                </span>
              )}
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
            <dd className="font-mono text-xs">{payout.receiver_email}</dd>
            <dt className="text-slate-500">Nominal</dt>
            <dd className="tabular-nums">{formatMoney(payout.amount, currency)}</dd>
            <dt className="text-slate-500">Percobaan</dt>
            <dd>
              #{payout.attempt} · payout lokal ID {payout.id}
            </dd>
            <dt className="text-slate-500">Diperbarui</dt>
            <dd>{formatTimestamp(payout.updated_at)}</dd>
          </dl>

          {lastSync && lastSync.flags.length > 0 && (
            <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <div className="text-xs font-medium">Flag dari backend pada sync terakhir:</div>
              <ul className="list-disc pl-5">
                {lastSync.flags.map((flag, i) => (
                  <li key={i}>{flag}</li>
                ))}
              </ul>
            </div>
          )}
          {poll.isError && <ErrorBox error={poll.error} title="Sinkronisasi otomatis gagal" />}
          {manualSync.isError && <ErrorBox error={manualSync.error} title="Sinkronisasi gagal" />}
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
  const milestone = useQuery({ queryKey: ["milestone", id], queryFn: () => getMilestone(id) });
  const events = useQuery({ queryKey: ["events", id], queryFn: () => getMilestoneEvents(id) });
  const payouts = useQuery({ queryKey: ["payouts"], queryFn: getPayouts });

  if (milestone.isPending) return <div className="text-sm text-slate-500">Memuat milestone…</div>;
  if (milestone.isError) {
    const notFound = milestone.error instanceof ApiError && milestone.error.status === 404;
    return (
      <div className="space-y-3">
        <Link to="/" className="text-sm text-blue-700 hover:underline">
          ← Semua milestone
        </Link>
        <ErrorBox
          error={milestone.error}
          title={notFound ? "Milestone tidak ditemukan" : "Gagal memuat milestone"}
          onRetry={() => milestone.refetch()}
        />
      </div>
    );
  }

  const m = milestone.data;
  const verdictEvent = latestEvent(events.data, "VERDICT_RECORDED");
  const payout = latestPayoutFor(payouts.data, m.id);

  return (
    <div className="space-y-5">
      <MilestoneHeader m={m} />
      {events.isError && <ErrorBox error={events.error} title="Gagal memuat event" onRetry={() => events.refetch()} />}
      {payouts.isError && (
        <ErrorBox error={payouts.error} title="Gagal memuat payout" onRetry={() => payouts.refetch()} />
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <DeliverableCard milestoneId={m.id} />
          <VerdictCard verdictEvent={verdictEvent} />
        </div>
        <div className="space-y-5">
          {events.isSuccess ? (
            <GatePayCard milestone={m} events={events.data} verdictEvent={verdictEvent} />
          ) : (
            <Card step={3} title="Gate dan persetujuan">
              <div className="text-sm text-slate-500">Memuat event…</div>
            </Card>
          )}
          <PayoutCard payout={payout} currency={m.contract?.currency} />
        </div>
      </div>
    </div>
  );
}
