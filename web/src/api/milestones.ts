import { ApiError, api } from "./client";
import type {
  DemoScenario,
  EventRecord,
  Milestone,
  MilestoneDetail,
  PayResponse,
  PolicyGateResult,
  ResetResponse,
  Verdict,
} from "./types";

export const getMilestones = () => api.get<Milestone[]>("/milestones");

export const getMilestone = (id: string) => api.get<MilestoneDetail>(`/milestones/${encodeURIComponent(id)}`);

export const getMilestoneEvents = (id: string) =>
  api.get<EventRecord[]>(`/milestones/${encodeURIComponent(id)}/events`);

/** POST /milestones/{id}/verify dengan isi deliverable sebagai teks. */
export const verifyMilestone = (id: string, content: string) =>
  api.post<Verdict>(`/milestones/${encodeURIComponent(id)}/verify`, { content });

/**
 * POST /milestones/{id}/pay. Klik tombol adalah persetujuan manusia, jadi
 * human_approved selalu true di sini; gate backend tetap memutuskan.
 */
export const payMilestone = (id: string, opts: { simulateLostResponse: boolean }) =>
  api.post<PayResponse>(`/milestones/${encodeURIComponent(id)}/pay`, {
    human_approved: true,
    simulate_lost_response: opts.simulateLostResponse,
  });

export const resetDemo = (scenario: DemoScenario) => api.post<ResetResponse>("/demo/reset", { scenario });

/**
 * Pratinjau gate tanpa membayar: GET /milestones/{id}/gate.
 * Endpoint ini BELUM ADA di backend (diusulkan ke tim backend). Selama belum
 * ada, fungsi ini mengembalikan null dan UI memberi tahu bahwa gate baru
 * dievaluasi saat tombol diklik.
 */
export async function getGatePreview(id: string): Promise<PolicyGateResult | null> {
  try {
    return await api.get<PolicyGateResult>(`/milestones/${encodeURIComponent(id)}/gate`);
  } catch (error) {
    // 404 dengan detail "Not Found" = route belum ada (bukan milestone tidak ditemukan).
    if (error instanceof ApiError && error.status === 404 && error.message === "Not Found") return null;
    throw error;
  }
}
