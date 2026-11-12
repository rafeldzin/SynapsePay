import type { EventRecord } from "../api/types";

/** Event terbaru dengan tipe tertentu (event dari backend urut naik berdasarkan id). */
export function latestEvent(events: EventRecord[] | undefined, type: string): EventRecord | null {
  if (!events) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === type) return events[i];
  }
  return null;
}
