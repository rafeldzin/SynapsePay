/**
 * Menampilkan status persis seperti yang dikirim backend (PRD 6.1 - 6.3, status PayPal).
 * Warna hanya dipilih dari kata kunci; teksnya tidak pernah diubah atau ditebak.
 */
const GREEN = ["PASS", "PAID", "SUCCESS", "RESOLVED", "CLAIMED", "RECONCILED_PAID"];
const RED = ["FAIL", "FAILED", "DENIED", "BLOCKED", "RETURNED", "REFUNDED", "REVERSED", "NEEDS_HUMAN"];
const AMBER = [
  "PENDING",
  "PROCESSING",
  "UNCLAIMED",
  "ONHOLD",
  "NEEDS_REVIEW",
  "UNKNOWN",
  "OPEN",
  "CREATING",
  "AWAITING_DELIVERABLE",
];
const BLUE = ["SENT", "PAYOUT_IN_FLIGHT", "INVESTIGATED", "AWAITING_REISSUE"];

export default function StatusBadge({ status }: { status?: string | null }) {
  if (!status) {
    return (
      <span className="text-xs text-slate-400" title="Backend belum mengirim field status">
        —
      </span>
    );
  }
  const key = status.toUpperCase();
  const color = GREEN.includes(key)
    ? "bg-green-100 text-green-800"
    : RED.includes(key)
      ? "bg-red-100 text-red-800"
      : AMBER.includes(key)
        ? "bg-amber-100 text-amber-800"
        : BLUE.includes(key)
          ? "bg-blue-100 text-blue-800"
          : "bg-slate-100 text-slate-700";
  return <span className={`rounded px-2 py-0.5 font-mono text-xs font-medium ${color}`}>{status}</span>;
}
