# SynapsePay Web (dashboard, eksperimen)

Dashboard React untuk membayar kontraktor per milestone lewat PayPal Payouts (sandbox).
Frontend hanya memanggil backend FastAPI. Status, ID, dan nominal semuanya berasal dari API.

> **Bukan UI yang dinilai.** UI resmi adalah `app/static/` (satu halaman HTML + vanilla JS, PRD 4.1).
> PRD 3.2 menaruh "frontend build step terpisah" di luar scope, jadi `web/` hanya eksperimen di branch ini.

## Menjalankan

```bash
# 1. Backend FastAPI di http://127.0.0.1:8000 (dari root repo, .env sudah diisi)
py -m uvicorn app.main:app --reload     # Linux/macOS: uvicorn app.main:app --reload

# 2. Frontend
cd web
npm install
npm run dev        # http://localhost:5173
```

Semua request ke `/api/*` diteruskan Vite apa adanya ke `http://127.0.0.1:8000` (route backend sendiri
berawalan `/api`, PRD 12). Ganti target dengan env `BACKEND_URL=http://host:port npm run dev`.

## Sinkron dengan backend

`src/api/types.ts` disalin dari `app/contracts.py` (PRD 7, kontrak beku), kolom `app/schema.sql`, dan
bentuk respons `app/main.py`. Kalau backend berubah:

```bash
npm run gen:api    # membuat src/api/openapi.d.ts dari /openapi.json
```

Lalu cocokkan `types.ts` dengan file hasil generate.

## Route yang dipakai (15 route backend)

| Route | Dipakai di |
|---|---|
| `GET /api/state` | Semua layar (`useAppState`, polling 2 detik, sama dengan `app/static/app.js`) |
| `GET /api/health` | - (hanya dicek saat uji) |
| `POST /api/demo/reset` | Daftar milestone: Reset demo (B) / (A) |
| `POST /api/demo/arm-fault` | `armFault()` tersedia; skenario B sudah memasang `timeout_after_send` saat reset |
| `POST /api/milestones/{id}/deliverable` | Fixture seed (JSON) atau unggah CSV + laporan (multipart) |
| `GET /api/milestones/{id}/gate` | `getGatePreview()`; layar memakai `gate_preview` dari `/api/state` |
| `POST /api/milestones/{id}/pay` | Approve & pay |
| `POST /api/payouts/{id}/retry` | Retry (kunci sama), di halaman milestone dan kasus |
| `POST /api/payouts/{id}/sync`, `POST /api/sync` | Sync per payout dan Sync semua (read-only) |
| `POST /api/messages` | Inbox kontraktor (simulasi) |
| `GET /api/cases/{id}` | `getCase()`; layar memakai `cases` dari `/api/state` |
| `POST /api/cases/{id}/investigate` | Run AI investigator |
| `POST /api/cases/{id}/actions/reconcile` | Approve reconcile (mark paid) |
| `POST /api/cases/{id}/actions/recheck` | Recheck (read-only) |

Cancel dan reissue belum punya route di backend (paket A5), jadi ditampilkan sebagai "belum tersedia".

## Struktur

| Path | Isi |
|---|---|
| `src/api/client.ts` | Pembungkus fetch dan `ApiError` (nama error, status HTTP, `GateResult` untuk HTTP 409) |
| `src/api/types.ts` | Bentuk data backend |
| `src/api/milestones.ts` | State, deliverable, pratinjau gate, pay, reset, arm-fault |
| `src/api/payouts.ts` | Retry, sync |
| `src/api/cases.ts` | Investigate, reconcile, recheck, pesan kontraktor, label aksi |
| `src/lib/state.tsx` | `useAppState`, nama operator (`approved_by`), konfirmasi persetujuan |
| `src/lib/eventSummary.ts` | Ringkasan per tipe event (PRD 5.1), label "simulasi", ID PayPal |
| `src/pages/` | Satu file per layar |
| `src/components/` | Layout, `ErrorBox`, `StatusBadge`, `GateChecklist`, `VerdictPanel`, `CopyableId`, `Card` |

## Alur skenario B

1. Daftar milestone: **Reset demo (B)**.
2. Milestone `m1`: klik fixture `m1_pass`, verdict `PASS` dengan fakta yang dikutip.
3. **Approve & pay**: gate `PAY` lolos, fault simulasi membuang respons PayPal, ledger `UNKNOWN`.
4. **Retry (kunci sama)**: PayPal menemukan batch asli, backend membuka kasus `D4`. Ledger tetap `UNKNOWN`.
5. Kasus: **Run AI investigator**: rekomendasi `RECONCILE_MARK_PAID`, hipotesis, bukti, dan draf balasan.
   Rekomendasi itu baru muncul setelah item PayPal `SUCCESS` (sandbox biasanya beberapa detik); sebelum itu
   investigator menyarankan `WAIT_AND_RECHECK` dan gate `RECONCILE` menolak dengan cek `RC-3`.
6. **Approve reconcile (mark paid)**: gate membaca PayPal lagi, payout dan milestone `PAID`, kasus `RESOLVED`.

Setiap aksi yang mengubah ledger meminta konfirmasi dan mengirim `{approved_by, confirm: true}` di request
yang sama (INV-4). Penolakan gate (HTTP 409) menampilkan checklist lengkap dari backend.
