# SynapsePay Web (dashboard)

Dashboard internal untuk membayar kontraktor per milestone lewat PayPal Payouts (sandbox).
Frontend hanya memanggil backend FastAPI. Status, ID, dan nominal semuanya berasal dari API.

## Menjalankan

```bash
# 1. Jalankan backend FastAPI di http://localhost:8000 (dari root repo, branch backend)
uvicorn app.main:app --reload --port 8000

# 2. Jalankan frontend
cd web
npm install
npm run dev        # http://localhost:5173
```

Semua request ke `/api/*` diteruskan Vite ke `http://localhost:8000`.
Ganti target dengan env `BACKEND_URL=http://host:port npm run dev`.

## Sinkron dengan backend

`src/api/types.ts` diselaraskan dengan branch backend `feat/rafel-paypal-engine` (commit 7942aa7).
Kalau backend berubah:

```bash
npm run gen:api    # membuat src/api/openapi.d.ts dari /openapi.json
```

Lalu cocokkan `types.ts` dengan file hasil generate.

## Struktur

| Path | Isi |
|---|---|
| `src/api/client.ts` | Pembungkus fetch dan `ApiError` (nama error, `debug_id`, status HTTP) |
| `src/api/types.ts` | Bentuk data backend |
| `src/api/milestones.ts` | Milestone, verify, pay, events, reset, pratinjau gate |
| `src/api/payouts.ts` | Daftar payout dan sync status |
| `src/lib/events.ts` | Membaca verdict dan gate terbaru dari event log |
| `src/pages/` | Satu file per layar |
| `src/components/` | Layout, `ErrorBox`, `StatusBadge`, `GateChecklist`, `VerdictPanel`, `CopyableId`, `Card` |

## Cara kerja halaman milestone

- **Verdict** diambil dari event `VERDICT_RECORDED` terbaru, jadi tetap tampil setelah reload.
- **Checklist gate** dirender apa adanya dari backend, tidak dihitung ulang di frontend:
  - Kalau backend punya `GET /milestones/{id}/gate`, checklist itu dipakai sebagai pratinjau. Tombol
    Approve & pay nonaktif selama ada cek merah. Cek `human_approved` ditampilkan "menunggu klik",
    karena klik tombol itulah persetujuannya.
  - Kalau endpoint itu belum ada, UI menampilkan evaluasi gate terakhir (event `GATE_EVALUATED`), dan
    gate backend memutuskan saat tombol diklik.
- **Approve & pay** meminta konfirmasi, terkunci selama request berjalan, dan tidak pernah diulang otomatis.
- **Error PayPal**: backend saat ini membalas HTTP 500 teks polos. UI lalu membaca event `PAYOUT_ERROR`
  terbaru untuk menampilkan nama error dan `debug_id`.
- **Polling**: selama status item `PENDING`/`PROCESSING`, UI memanggil `POST /payouts/{id}/sync` setiap
  4 detik, maksimal 15 kali.

## Status layar

1. Daftar milestone dan Reset demo (pilih skenario `timeout`/`unclaimed`): **selesai**
2. Halaman milestone (deliverable, verdict, gate, pay, status payout): **selesai**
3. Ledger payout (semua percobaan payout, ID bisa disalin, Sync per baris): **selesai**
4. Timeline event: belum
5. Halaman kasus: belum
