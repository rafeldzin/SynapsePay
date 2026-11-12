import { useState } from "react";

/** Menampilkan ID dalam font mono dengan tombol salin. "—" kalau kosong. */
export default function CopyableId({ value }: { value: string | null | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-slate-400">—</span>;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      window.prompt("Salin ID ini:", value);
    }
  };

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-mono text-xs break-all">{value}</span>
      <button
        type="button"
        onClick={copy}
        className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-[11px] text-slate-600 hover:bg-slate-100"
        title="Salin"
      >
        {copied ? "Tersalin" : "Salin"}
      </button>
    </span>
  );
}
