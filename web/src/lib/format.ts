/**
 * Timestamp SQLite (CURRENT_TIMESTAMP) disimpan dalam UTC tanpa penanda zona,
 * mis. "2026-11-11 08:44:22". Ditampilkan dalam waktu lokal browser.
 */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("id-ID", { dateStyle: "short", timeStyle: "medium" });
}

/**
 * Format nominal dari backend. Kalau backend tidak mengirim mata uang,
 * angka ditampilkan tanpa simbol (tidak menebak USD).
 */
export function formatMoney(amount: number | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  if (!currency) return amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  try {
    return amount.toLocaleString("en-US", { style: "currency", currency });
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}
