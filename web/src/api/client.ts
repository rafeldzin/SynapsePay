/**
 * Pembungkus fetch untuk semua panggilan ke backend.
 * Semua error diubah jadi ApiError supaya UI selalu bisa menampilkan
 * nama error dan debug_id (kalau backend/PayPal mengirimkannya).
 */

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  /** Nama error, mis. "VALIDATION_ERROR" dari PayPal atau "HTTP_500". */
  name: string;
  /** debug_id dari PayPal (diteruskan backend), kalau ada. */
  debugId: string | null;
  /** Status HTTP; 0 berarti backend tidak bisa dihubungi. */
  status: number;
  /** Isi mentah respons error, untuk ditampilkan di bagian detail. */
  raw: unknown;

  constructor(opts: { name: string; message: string; debugId?: string | null; status: number; raw?: unknown }) {
    super(opts.message);
    this.name = opts.name;
    this.debugId = opts.debugId ?? null;
    this.status = opts.status;
    this.raw = opts.raw;
  }
}

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pickString(obj: Json, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = obj[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

/**
 * Menerima beberapa bentuk error yang mungkin dikirim backend:
 *  - { detail: { name, message, debug_id } }   (format yang diusulkan ke tim backend)
 *  - { name, message, debug_id }                (format error PayPal yang diteruskan apa adanya)
 *  - { detail: "teks" }                         (HTTPException default FastAPI)
 *  - { detail: [ {loc, msg, type} ] }           (validasi Pydantic, HTTP 422)
 */
function toApiError(status: number, body: unknown): ApiError {
  const fallbackName = `HTTP_${status}`;

  if (isObject(body)) {
    const detail = body.detail;
    const source = isObject(detail) ? detail : body;

    if (isObject(source) && (source.name || source.debug_id || source.message)) {
      return new ApiError({
        name: pickString(source, "name", "error", "code") ?? fallbackName,
        message: pickString(source, "message", "msg", "detail") ?? "Permintaan gagal.",
        debugId: pickString(source, "debug_id", "debugId"),
        status,
        raw: body,
      });
    }

    if (typeof detail === "string") {
      return new ApiError({ name: fallbackName, message: detail, status, raw: body });
    }

    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (isObject(item) ? pickString(item, "msg") : null))
        .filter(Boolean)
        .join("; ");
      return new ApiError({
        name: status === 422 ? "VALIDATION_ERROR" : fallbackName,
        message: messages || "Data yang dikirim tidak valid.",
        status,
        raw: body,
      });
    }
  }

  return new ApiError({
    name: fallbackName,
    message: typeof body === "string" && body ? body.slice(0, 300) : "Permintaan gagal.",
    status,
    raw: body,
  });
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch (cause) {
    throw new ApiError({
      name: "NETWORK_ERROR",
      message: "Backend tidak bisa dihubungi. Pastikan FastAPI jalan di http://localhost:8000.",
      status: 0,
      raw: String(cause),
    });
  }

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    // Kalau backend mati, proxy Vite membalas 500 dengan body kosong
    // (proxy lain biasanya 502/504). Error asli FastAPI selalu punya body.
    const proxyDown =
      (response.status === 500 && body === null) ||
      ((response.status === 502 || response.status === 504) && !isObject(body));
    if (proxyDown) {
      throw new ApiError({
        name: "BACKEND_UNREACHABLE",
        message: "Backend tidak bisa dihubungi. Pastikan FastAPI jalan di http://localhost:8000.",
        status: response.status,
        raw: body,
      });
    }
    throw toApiError(response.status, body);
  }

  return body as T;
}

export const api = {
  get: <T>(path: string) => apiRequest<T>(path),
  post: <T>(path: string, data?: unknown) =>
    apiRequest<T>(path, {
      method: "POST",
      body: data === undefined ? undefined : data instanceof FormData ? data : JSON.stringify(data),
    }),
};
