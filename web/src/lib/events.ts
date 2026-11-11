import type { EventRecord, PolicyGateResult, Verdict } from "../api/types";

/** Event terbaru dengan tipe tertentu (event selalu urut naik berdasarkan id). */
export function latestEvent(events: EventRecord[] | undefined, type: string): EventRecord | null {
  if (!events) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === type) return events[i];
  }
  return null;
}

export function payloadObject(event: EventRecord | null): Record<string, unknown> | null {
  const p = event?.payload;
  return p && typeof p === "object" && !Array.isArray(p) ? (p as Record<string, unknown>) : null;
}

export const verdictFromEvent = (event: EventRecord | null) => payloadObject(event) as Verdict | null;

export const gateFromEvent = (event: EventRecord | null) => payloadObject(event) as PolicyGateResult | null;

/** Daftar kriteria dari acceptance_json milestone, mis. {"criteria": [...]}. */
export function acceptanceCriteria(acceptanceJson: string | null | undefined): string[] {
  if (!acceptanceJson) return [];
  try {
    const parsed = JSON.parse(acceptanceJson);
    const list = Array.isArray(parsed) ? parsed : parsed?.criteria;
    return Array.isArray(list) ? list.map(String) : [];
  } catch {
    return [];
  }
}
