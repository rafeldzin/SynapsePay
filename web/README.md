# SynapsePay Web (dashboard)

Dashboard internal untuk membayar kontraktor per milestone lewat PayPal Payouts (sandbox).
Frontend hanya memanggil backend FastAPI. Status, ID, dan nominal semuanya berasal dari API.

## Menjalankan

```bash
# 1. Jalankan backend FastAPI di http://localhost:8000 (dari root repo)
uvicorn app.main:app --reload

# 2. Jalankan frontend
cd web
npm install
npm run dev        # http://localhost:5173
```

Semua request ke `/api/*` diteruskan Vite ke `http://localhost:8000`, jadi backend tidak perlu CORS.
Ganti target dengan env `BACKEND_URL=http://host:port npm run dev`.

## Sinkron dengan backend

`src/api/types.ts` masih ditulis dari `app/models/schema.py` dan asumsi (ditandai `ASUMSI`).
Setelah backend terbaru jalan:

```bash
npm run gen:api    # membuat src/api/openapi.d.ts dari /openapi.json
```

Lalu cocokkan `types.ts` dengan file hasil generate.

## Struktur

| Path | Isi |
|---|---|
| `src/api/client.ts` | Pembungkus fetch dan `ApiError` (nama error, `debug_id`, status HTTP) |
| `src/api/types.ts` | Bentuk data backend (asumsi sementara) |
| `src/api/milestones.ts` | `GET /milestones`, `POST /demo/reset` |
| `src/pages/` | Satu file per layar |
| `src/components/` | Layout, `ErrorBox`, `StatusBadge` |

## Status layar

1. Daftar milestone dan Reset demo: **selesai**
2. Halaman milestone (verify, verdict, gate, pay): belum
3. Ledger payout: belum
4. Timeline event: belum
5. Halaman kasus: belum (menunggu endpoint backend)
