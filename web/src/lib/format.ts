/**
 * Timestamp backend selalu UTC ISO 8601 (mis. "2026-11-12T15:48:09Z").
 * Ditampilkan dalam waktu lokal browser.
 */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("id-ID", { dateStyle: "short", timeStyle: "medium" });
}

/**
 * Nominal dari backend dalam sen (integer, INV-6). Kalau mata uang tidak dikirim,
 * angka ditampilkan tanpa simbol (tidak menebak USD).
 */
export function formatCents(cents: number | null | undefined, currency?: string | null): string {
  if (cents === null || cents === undefined || Number.isNaN(cents)) return "—";
  const amount = cents / 100;
  if (!currency) return amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  try {
    return amount.toLocaleString("en-US", { style: "currency", currency });
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}
