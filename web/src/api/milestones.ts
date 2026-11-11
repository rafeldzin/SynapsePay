import { api } from "./client";
import type { Milestone } from "./types";

/**
 * GET /milestones
 * ASUMSI: backend mengembalikan array Milestone. Bentuk { milestones: [...] }
 * atau { items: [...] } juga diterima supaya tidak rusak kalau dibungkus.
 */
export async function getMilestones(): Promise<Milestone[]> {
  const body = await api.get<unknown>("/milestones");
  if (Array.isArray(body)) return body as Milestone[];
  if (body && typeof body === "object") {
    const wrapped = (body as Record<string, unknown>).milestones ?? (body as Record<string, unknown>).items;
    if (Array.isArray(wrapped)) return wrapped as Milestone[];
  }
  throw new Error("Bentuk respons GET /milestones tidak dikenali (bukan array).");
}

/** POST /demo/reset. Isi respons tidak dipakai UI selain untuk ditampilkan. */
export function resetDemo(): Promise<unknown> {
  return api.post<unknown>("/demo/reset");
}

/** Info kontrak untuk satu milestone, dari field nested atau datar. Tidak mengarang. */
export interface ContractInfo {
  id: string | null;
  buyer_name: string | null;
  contractor_name: string | null;
  currency: string | undefined;
}

export function contractInfo(m: Milestone): ContractInfo {
  return {
    id: m.contract?.id ?? m.contract_id ?? null,
    buyer_name: m.contract?.buyer_name ?? m.buyer_name ?? null,
    contractor_name: m.contract?.contractor_name ?? m.contractor_name ?? null,
    currency: m.currency ?? m.contract?.currency ?? undefined,
  };
}
