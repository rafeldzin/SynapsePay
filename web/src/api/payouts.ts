import { api } from "./client";
import type { Approval, Payout, RetryResponse, SyncResponse } from "./types";

const enc = encodeURIComponent;

/** POST /api/payouts/{id}/sync: baca status batch/item dari PayPal (read-only). */
export const syncPayout = (payoutId: string) => api.post<SyncResponse>(`/payouts/${enc(payoutId)}/sync`);

/** POST /api/sync: sync semua payout SENT dan UNCLAIMED. Payout UNKNOWN tidak pernah disentuh. */
export const syncAll = () => api.post<{ synced: unknown }>("/sync");

/**
 * POST /api/payouts/{id}/retry: retry payout UNKNOWN dengan sender_batch_id yang SAMA.
 * Kalau PayPal sudah punya batch-nya, backend mencatat PAYPAL_ORIGINAL_FOUND dan membuka kasus D4.
 * Ledger tetap UNKNOWN sampai ada RECONCILE yang disetujui manusia.
 */
export const retryPayout = (payoutId: string, approval: Approval) =>
  api.post<RetryResponse>(`/payouts/${enc(payoutId)}/retry`, approval);

export function latestPayoutFor(payouts: Payout[] | undefined, milestoneId: string): Payout | null {
  if (!payouts) return null;
  return payouts.filter((p) => p.milestone_id === milestoneId).sort((a, b) => b.attempt - a.attempt)[0] ?? null;
}
