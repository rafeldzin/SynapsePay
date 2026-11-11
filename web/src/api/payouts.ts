import { api } from "./client";
import type { Payout, SyncResponse } from "./types";

export const getPayouts = () => api.get<Payout[]>("/payouts");

/** POST /payouts/{id}/sync: backend membaca status item dari PayPal lalu menyimpannya. */
export const syncPayout = (id: number) => api.post<SyncResponse>(`/payouts/${id}/sync`);

/** Status PayPal yang masih bergerak sendiri; selain ini polling dihentikan. */
export const IN_FLIGHT_STATUSES = ["PENDING", "PROCESSING", "NEW"];

export function latestPayoutFor(payouts: Payout[] | undefined, milestoneId: string): Payout | null {
  if (!payouts) return null;
  return payouts.filter((p) => p.milestone_id === milestoneId).sort((a, b) => b.id - a.id)[0] ?? null;
}
