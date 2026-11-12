import { createContext, useContext, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getState } from "../api/milestones";
import type { Approval } from "../api/types";

/** Sama dengan app/static/app.js: snapshot diperbarui tiap 2 detik. */
export const POLL_MS = 2000;

/**
 * GET /api/state adalah satu-satunya sumber data layar. Setiap halaman membaca snapshot yang
 * sama (di-cache react-query), jadi tidak ada angka yang dihitung atau ditebak di frontend.
 */
export function useAppState() {
  return useQuery({ queryKey: ["state"], queryFn: getState, refetchInterval: POLL_MS });
}

/** Dipanggil setelah setiap aksi supaya semua layar langsung membaca snapshot baru. */
export function useRefreshState() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["state"] });
}

// ---- operator (approved_by) -------------------------------------------------

const STORAGE_KEY = "synapsepay.operator";

function readOperator(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? "ops-lead";
  } catch {
    return "ops-lead";
  }
}

const OperatorContext = createContext<{ operator: string; setOperator: (name: string) => void }>({
  operator: "ops-lead",
  setOperator: () => {},
});

export function OperatorProvider({ children }: { children: ReactNode }) {
  const [operator, setOperatorState] = useState(readOperator);
  const setOperator = (name: string) => {
    setOperatorState(name);
    try {
      window.localStorage.setItem(STORAGE_KEY, name);
    } catch {
      // localStorage tidak tersedia: nama tetap dipakai selama halaman terbuka.
    }
  };
  return <OperatorContext.Provider value={{ operator, setOperator }}>{children}</OperatorContext.Provider>;
}

export function useOperator() {
  return useContext(OperatorContext);
}

/**
 * Persetujuan manusia untuk satu klik (INV-4). Mengembalikan null kalau nama operator kosong
 * atau dialog konfirmasi dibatalkan; backend tetap memeriksa gate di request yang sama.
 */
export function useApproval() {
  const { operator } = useOperator();
  return (question: string): Approval | null => {
    const name = operator.trim();
    if (!name) {
      window.alert("Isi nama operator di kanan atas dulu. Nama itu dikirim sebagai approved_by.");
      return null;
    }
    if (!window.confirm(`${question}\n\nDisetujui oleh: ${name}`)) return null;
    return { approved_by: name, confirm: true };
  };
}
