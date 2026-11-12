import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getAiRecommendation, type AiRecommendation } from "../api/aiRecommendation";
import { ACTION_CATALOG, getCaseEvidence, getCases, postMessage, runCaseAction } from "../api/cases";
import { getMilestone, getMilestones } from "../api/milestones";
import type { CaseActionResponse, CaseEvidence, CaseRecord } from "../api/types";
import Card from "../components/Card";
import CopyableId from "../components/CopyButton";
import ErrorBox from "../components/ErrorBox";
import StatusBadge from "../components/StatusBadge";
import { formatMoney, formatTimestamp } from "../lib/format";

// ---------------------------------------------------------------------------
// Semua kasus (GET /cases)
// ---------------------------------------------------------------------------

function AllCasesTable({
  cases,
  selected,
  onSelect,
}: {
  cases: CaseRecord[];
  selected: string;
  onSelect: (milestoneId: string) => void;
}) {
  if (cases.length === 0) {
    return <p className="text-sm text-slate-500">Belum ada kasus terbuka di backend.</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
        <tr>
          <th className="py-1.5 pr-3 font-medium">Kasus</th>
          <th className="py-1.5 pr-3 font-medium">Milestone</th>
          <th className="py-1.5 pr-3 font-medium">Alasan dibuka</th>
          <th className="py-1.5 pr-3 font-medium">Status</th>
          <th className="py-1.5 font-medium">Dibuka</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {cases.map((c) => (
          <tr key={c.id} className={c.milestone_id === selected ? "bg-blue-50" : ""}>
            <td className="py-1.5 pr-3 font-mono">#{c.id}</td>
            <td className="py-1.5 pr-3">
              <button
                type="button"
                onClick={() => onSelect(c.milestone_id)}
                className="font-mono text-blue-700 hover:underline"
              >
                {c.milestone_id}
              </button>
            </td>
            <td className="py-1.5 pr-3">{c.reason}</td>
            <td className="py-1.5 pr-3">
              <StatusBadge status={c.status} />
            </td>
            <td className="py-1.5 text-xs text-slate-500">{formatTimestamp(c.opened_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------
// Inbox kontraktor (simulasi)
// ---------------------------------------------------------------------------

function InboxCard({ milestoneId, evidence }: { milestoneId: string; evidence: CaseEvidence | undefined }) {
  const queryClient = useQueryClient();
  const [sender, setSender] = useState("");
  const [body, setBody] = useState("");

  const send = useMutation({
    mutationFn: () => postMessage(milestoneId, sender, body),
    onSuccess: () => {
      setBody("");
      queryClient.invalidateQueries({ queryKey: ["caseEvidence", milestoneId] });
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["events", milestoneId] });
    },
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!body.trim() || send.isPending) return;
    send.mutate();
  };

  const messages = evidence?.inbound_messages ?? [];

  return (
    <Card
      title="Inbox kontraktor"
      aside={<span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">simulasi</span>}
    >
      <form onSubmit={onSubmit} className="space-y-2">
        <input
          type="email"
          value={sender}
          onChange={(e) => setSender(e.target.value)}
          placeholder="Email pengirim (opsional)"
          className="w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={3}
          placeholder="Ketik pesan kontraktor, mis. pembayaran milestone belum masuk dan alamat PayPal yang benar."
          className="w-full rounded border border-slate-300 p-2 text-sm"
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={!body.trim() || send.isPending}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {send.isPending ? "Mengirim…" : "Kirim pesan"}
          </button>
          <span className="text-xs text-slate-500">Backend mencatat pesan dan mengecek apakah kasus perlu dibuka.</span>
        </div>
        {send.isError && <ErrorBox error={send.error} title="Pesan gagal dikirim" />}
      </form>

      {messages.length > 0 && (
        <ul className="mt-4 space-y-2">
          {messages.map((m) => (
            <li key={m.id} className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm">
              <div className="flex justify-between gap-2 text-xs text-slate-500">
                <span className="font-mono">{m.sender ?? "pengirim tidak diketahui"}</span>
                <span>{formatTimestamp(m.received_at)}</span>
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
// Bukti dari backend: status PayPal live + risiko pengalihan
// ---------------------------------------------------------------------------

function EvidenceCard({
  evidence,
  currency,
  refreshing,
  onRefresh,
}: {
  evidence: CaseEvidence;
  currency?: string;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const distances = Object.entries(evidence.email_edit_distances ?? {});
  return (
    <Card
      title="Bukti kasus"
      aside={
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-100 disabled:opacity-40"
        >
          {refreshing ? "Membaca PayPal…" : "Baca ulang dari PayPal"}
        </button>
      }
    >
      <div className="space-y-4 text-sm">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="text-slate-500">Email di kontrak</dt>
          <dd className="font-mono text-xs">{evidence.contract_email}</dd>
          <dt className="text-slate-500">Nominal milestone</dt>
          <dd className="tabular-nums">{formatMoney(evidence.milestone_amount, currency)}</dd>
        </dl>

        {evidence.diversion_risk ? (
          <div role="alert" className="rounded border-2 border-red-500 bg-red-50 p-3 text-red-900">
            <div className="font-semibold">Risiko pengalihan pembayaran</div>
            <div className="mt-0.5">
              Pesan kontraktor menyebut email yang berbeda dengan kontrak. Perubahan penerima lewat chat harus
              dikonfirmasi lewat kanal asli (mis. telepon) sebelum uang bergerak.
            </div>
            {evidence.extracted_message_emails.length > 0 && (
              <div className="mt-1.5 text-xs">
                Email di pesan:{" "}
                {evidence.extracted_message_emails.map((e) => (
                  <span key={e} className="mr-2 font-mono">
                    {e}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
            <span className="font-mono">diversion_risk</span>: false
            {evidence.requires_out_of_band_verification ? "" : " · tidak perlu verifikasi di luar chat"}
          </div>
        )}

        {distances.length > 0 && (
          <div>
            <div className="text-xs font-medium text-slate-500">Jarak edit email (Levenshtein, dari backend)</div>
            <ul className="mt-1 space-y-0.5 text-xs">
              {distances.map(([key, d]) => (
                <li key={key}>
                  <span className="font-mono">{d.contract_email ?? d.live_payout_email}</span> vs{" "}
                  <span className="font-mono">{d.message_email}</span>: <strong>{d.distance}</strong> karakter
                  berbeda
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <div className="mb-1 flex items-center gap-2 text-xs font-medium text-slate-500">
            Status PayPal
            <span className="rounded bg-green-100 px-1.5 py-0.5 text-green-800">LIVE</span>
          </div>
          {evidence.live_paypal_statuses.length === 0 ? (
            <p className="text-slate-500">Belum ada payout untuk milestone ini.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="py-1 pr-3 font-medium">Payout</th>
                    <th className="py-1 pr-3 font-medium">Status lokal</th>
                    <th className="py-1 pr-3 font-medium">Item live</th>
                    <th className="py-1 pr-3 font-medium">Batch live</th>
                    <th className="py-1 font-medium">ID PayPal</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 align-top">
                  {evidence.live_paypal_statuses.map((s) => (
                    <tr key={s.payout_id}>
                      <td className="py-1.5 pr-3 font-mono">
                        {s.payout_id} · #{s.attempt}
                      </td>
                      <td className="py-1.5 pr-3">
                        <StatusBadge status={s.local_status} />
                      </td>
                      <td className="py-1.5 pr-3">
                        <StatusBadge status={s.live_item_status} />
                      </td>
                      <td className="py-1.5 pr-3">
                        <StatusBadge status={s.live_batch_status} />
                      </td>
                      <td className="space-y-0.5 py-1.5">
                        <div>
                          batch <CopyableId value={s.payout_batch_id} />
                        </div>
                        <div>
                          item <CopyableId value={s.payout_item_id} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Rekomendasi AI (dummy)
// ---------------------------------------------------------------------------

function AiRecommendationCard({ rec }: { rec: AiRecommendation | undefined }) {
  return (
    <Card
      title="Rekomendasi AI"
      aside={
        rec?.isDummy && (
          <span className="rounded bg-fuchsia-100 px-1.5 py-0.5 text-xs font-semibold text-fuchsia-800">
            DATA DUMMY
          </span>
        )
      }
    >
      {!rec ? (
        <p className="text-sm text-slate-500">Memuat…</p>
      ) : (
        <div className="space-y-3 text-sm">
          {rec.isDummy && (
            <div className="rounded border border-fuchsia-200 bg-fuchsia-50 p-2 text-xs text-fuchsia-900">
              Endpoint AI investigator belum ada di backend. Isi panel ini contoh statis dan tidak membaca data kasus.
            </div>
          )}
          <div>
            <div className="text-xs font-medium text-slate-500">Hipotesis</div>
            <ol className="mt-1 space-y-2">
              {rec.hypotheses.map((h) => (
                <li key={h.rank} className="rounded border border-slate-200 px-3 py-2">
                  <div className="font-medium">
                    {h.rank}. {h.title}
                  </div>
                  <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                    {h.evidence.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          </div>
          <div>
            <div className="text-xs font-medium text-slate-500">Aksi yang disarankan</div>
            <div className="mt-0.5">
              <span className="font-mono">{rec.recommendedAction}</span>
            </div>
            <p className="mt-1 text-slate-700">{rec.rationale}</p>
          </div>
          <div>
            <div className="text-xs font-medium text-slate-500">Draf balasan ke kontraktor</div>
            <p className="mt-1 whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 text-slate-700">
              {rec.replyDraft}
            </p>
          </div>
          <p className="text-xs text-slate-500">AI hanya memberi saran. Aksi dijalankan lewat tombol di bawah.</p>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Aksi kasus
// ---------------------------------------------------------------------------

function ActionResult({ result }: { result: CaseActionResponse }) {
  const ok = result.success === true;
  return (
    <div
      className={`rounded border p-3 text-sm ${ok ? "border-green-200 bg-green-50 text-green-900" : "border-amber-300 bg-amber-50 text-amber-900"}`}
    >
      <div className="font-semibold">
        {ok ? "Aksi berhasil" : "Aksi tidak selesai"}
        {result.action ? <span className="ml-1 font-mono font-normal">{result.action}</span> : null}
      </div>
      {result.status && (
        <div>
          Status kasus: <span className="font-mono">{result.status}</span>
        </div>
      )}
      {result.error && <div>{result.error}</div>}
      {typeof result.cancelled_item_id === "string" && (
        <div className="mt-1 space-y-0.5 text-xs">
          <div>
            Item lama <span className="font-mono">{result.cancelled_item_id}</span> dibatalkan, status{" "}
            <span className="font-mono">{String(result.old_status ?? "—")}</span>.
          </div>
          {typeof result.new_payout === "object" && result.new_payout !== null && (
            <div>
              Payout baru ke <span className="font-mono">{String(result.new_email ?? "—")}</span>: ID{" "}
              <span className="font-mono">{String((result.new_payout as Record<string, unknown>).payout_id ?? "—")}</span>
              , status{" "}
              <span className="font-mono">{String((result.new_payout as Record<string, unknown>).status ?? "—")}</span>.
              Lihat ledger untuk ID PayPal lengkap.
            </div>
          )}
        </div>
      )}
      {result.cancel_unavailable && (
        <div className="mt-1 text-xs">
          Cancel tidak tersedia dari PayPal: <span className="font-mono">{result.paypal_name ?? "—"}</span> (HTTP{" "}
          {result.status_code ?? "—"}) · debug_id <span className="font-mono">{result.debug_id ?? "—"}</span>. Tidak
          ada penerbitan ulang.
        </div>
      )}
      <details className="mt-1.5">
        <summary className="cursor-pointer text-xs opacity-70">Respons lengkap</summary>
        <pre className="mt-1 max-h-64 overflow-auto rounded bg-white/70 p-2 text-[11px]">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </div>
  );
}

function ActionsCard({
  evidence,
  openCase,
  recommendedAction,
}: {
  evidence: CaseEvidence;
  openCase: CaseRecord | null;
  recommendedAction: string | undefined;
}) {
  const queryClient = useQueryClient();
  const suggestedEmail =
    evidence.extracted_message_emails.find((e) => e.toLowerCase() !== evidence.contract_email.toLowerCase()) ?? "";
  const [newEmail, setNewEmail] = useState(suggestedEmail);
  const [phoneConfirmed, setPhoneConfirmed] = useState(false);

  // Isi email baru dari pesan kontraktor saat bukti berubah, kalau user belum mengetik.
  useEffect(() => {
    setNewEmail((current) => current || suggestedEmail);
  }, [suggestedEmail]);

  const run = useMutation({
    mutationFn: (action: string) =>
      runCaseAction(openCase!.id, {
        action,
        // Klik tombol + konfirmasi adalah persetujuan manusia; backend tetap memeriksa allowed_actions.
        human_approved: true,
        ...(action === "cancel_and_reissue" ? { new_email: newEmail.trim(), new_email_verified: phoneConfirmed } : {}),
      }),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["caseEvidence", evidence.milestone_id] });
      queryClient.invalidateQueries({ queryKey: ["cases"] });
      queryClient.invalidateQueries({ queryKey: ["payouts"] });
      queryClient.invalidateQueries({ queryKey: ["events", evidence.milestone_id] });
    },
  });

  const allowed = new Map(evidence.allowed_actions.map((a) => [a.action, a.reason]));
  // Aksi dari backend yang belum ada di katalog tetap ditampilkan.
  const catalog = [
    ...ACTION_CATALOG,
    ...evidence.allowed_actions
      .filter((a) => !ACTION_CATALOG.some((c) => c.action === a.action))
      .map((a) => ({ action: a.action, label: a.action, condition: "", movesMoney: false })),
  ];

  const onRun = (action: string, label: string) => {
    if (run.isPending || !openCase) return;
    let message = `Jalankan aksi "${label}" untuk kasus #${openCase.id}?`;
    if (action === "cancel_and_reissue") {
      message +=
        `\n\nItem yang belum diklaim akan dibatalkan di PayPal, lalu payout baru dikirim ke:\n${newEmail.trim() || "(email kosong)"}` +
        `\nKonfirmasi telepon: ${phoneConfirmed ? "sudah" : "BELUM"}`;
    }
    if (window.confirm(message)) run.mutate(action);
  };

  return (
    <Card title="Aksi yang diizinkan">
      <div className="space-y-3">
        {!openCase && (
          <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
            Belum ada kasus terbuka untuk milestone ini, jadi aksi belum bisa dijalankan. Kasus dibuka otomatis oleh
            backend, mis. setelah ada payout lalu kontraktor mengirim pesan.
          </div>
        )}
        {openCase && (
          <div className="text-xs text-slate-500">
            Aksi dijalankan untuk kasus <span className="font-mono">#{openCase.id}</span> · {openCase.reason}
          </div>
        )}

        <div className="space-y-2 rounded border border-slate-200 p-3">
          <label className="block text-xs font-medium text-slate-600">
            Email PayPal baru (untuk terbitkan ulang)
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="email@contoh.com"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm font-normal"
            />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={phoneConfirmed}
              onChange={(e) => setPhoneConfirmed(e.target.checked)}
              disabled={run.isPending}
              className="mt-1"
            />
            <span>
              Email baru sudah dikonfirmasi lewat telepon{" "}
              <span className="rounded bg-amber-100 px-1 text-xs text-amber-800">simulasi</span>
            </span>
          </label>
        </div>

        <ul className="space-y-2">
          {catalog.map((item) => {
            const reason = allowed.get(item.action);
            const isAllowed = reason !== undefined;
            const disabled = !isAllowed || !openCase || run.isPending;
            const isRecommended = recommendedAction === item.action;
            return (
              <li
                key={item.action}
                className={`rounded border px-3 py-2 ${isAllowed ? "border-slate-300 bg-white" : "border-slate-200 bg-slate-50"}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className={isAllowed ? "" : "text-slate-400"}>
                    <div className="text-sm font-medium">
                      {item.label}
                      {isRecommended && (
                        <span className="ml-2 rounded bg-fuchsia-100 px-1.5 py-0.5 text-[11px] font-normal text-fuchsia-800">
                          disarankan AI (dummy)
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-[11px]">{item.action}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onRun(item.action, item.label)}
                    disabled={disabled}
                    className={`rounded px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed ${
                      item.movesMoney
                        ? "bg-blue-700 text-white hover:bg-blue-800 disabled:bg-slate-200 disabled:text-slate-400"
                        : "border border-slate-300 bg-white hover:bg-slate-100 disabled:text-slate-400"
                    }`}
                  >
                    {run.isPending && run.variables === item.action ? "Menjalankan…" : "Jalankan"}
                  </button>
                </div>
                <div className={`mt-1 text-xs ${isAllowed ? "text-green-800" : "text-slate-500"}`}>
                  {isAllowed ? (
                    <>Diizinkan backend: {reason}</>
                  ) : (
                    <>
                      Tidak ada di <span className="font-mono">allowed_actions</span> dari backend untuk kondisi saat ini.
                      {item.condition && <> Syarat menurut kebijakan backend: {item.condition}</>}
                    </>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        <p className="text-xs text-slate-500">
          Tidak ada tombol kirim ulang biasa. Kirim ulang tanpa memeriksa status PayPal bisa membuat pembayaran ganda.
        </p>

        {run.isSuccess && <ActionResult result={run.data} />}
        {run.isError && <ErrorBox error={run.error} title="Aksi ditolak atau gagal" />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Halaman
// ---------------------------------------------------------------------------

export default function CasePage() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const milestones = useQuery({ queryKey: ["milestones"], queryFn: getMilestones });
  const cases = useQuery({ queryKey: ["cases"], queryFn: getCases });

  const selected = params.get("milestone") ?? milestones.data?.[0]?.id ?? "";
  const evidence = useQuery({
    queryKey: ["caseEvidence", selected],
    queryFn: () => getCaseEvidence(selected),
    enabled: Boolean(selected),
    // Setiap pembacaan memanggil PayPal live, jadi tidak di-refresh otomatis.
    staleTime: Infinity,
  });
  // Mata uang kontrak untuk nominal (di-cache bersama halaman lain).
  const detail = useQuery({
    queryKey: ["milestone", selected],
    queryFn: () => getMilestone(selected),
    enabled: Boolean(selected),
  });
  const ai = useQuery({
    queryKey: ["aiRecommendation", selected],
    queryFn: () => getAiRecommendation(selected),
    enabled: Boolean(selected),
  });

  // Membaca bukti bisa membuka kasus baru di backend, jadi daftar kasus ikut diperbarui.
  useEffect(() => {
    if (evidence.dataUpdatedAt) queryClient.invalidateQueries({ queryKey: ["cases"] });
  }, [evidence.dataUpdatedAt, queryClient]);

  const ev = evidence.data;
  const milestone = milestones.data?.find((m) => m.id === selected);
  const openCases = (ev?.cases ?? []).filter((c) => c.status === "OPEN").sort((a, b) => b.id - a.id);
  const openCase = openCases[0] ?? null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Kasus</h1>
          <p className="text-sm text-slate-500">
            Kasus dibuka otomatis oleh aturan backend. AI menyarankan, backend menentukan aksi yang diizinkan, dan
            manusia yang menjalankannya.
          </p>
        </div>
        <select
          value={selected}
          onChange={(e) => setParams({ milestone: e.target.value })}
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          aria-label="Pilih milestone"
        >
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

      <Card title="Semua kasus">
        {cases.isPending && <p className="text-sm text-slate-500">Memuat kasus…</p>}
        {cases.isError && <ErrorBox error={cases.error} title="Gagal memuat kasus" onRetry={() => cases.refetch()} />}
        {cases.isSuccess && (
          <AllCasesTable cases={cases.data} selected={selected} onSelect={(id) => setParams({ milestone: id })} />
        )}
      </Card>

      {selected && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">Milestone:</span>
          <Link to={`/milestones/${encodeURIComponent(selected)}`} className="text-blue-700 hover:underline">
            {milestone?.title ?? selected}
          </Link>
          <span className="font-mono text-xs text-slate-400">{selected}</span>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          {selected && <InboxCard milestoneId={selected} evidence={ev} />}
          <AiRecommendationCard rec={ai.data} />
        </div>
        <div className="space-y-5">
          {evidence.isPending && selected && (
            <Card title="Bukti kasus">
              <p className="text-sm text-slate-500">Membaca bukti dan status live dari PayPal…</p>
            </Card>
          )}
          {evidence.isError && (
            <ErrorBox error={evidence.error} title="Gagal memuat bukti kasus" onRetry={() => evidence.refetch()} />
          )}
          {ev && (
            <>
              <EvidenceCard
                evidence={ev}
                currency={detail.data?.contract?.currency}
                refreshing={evidence.isFetching}
                onRefresh={() => evidence.refetch()}
              />
              <ActionsCard
                key={ev.milestone_id}
                evidence={ev}
                openCase={openCase}
                recommendedAction={ai.data?.recommendedAction}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
