import { Link } from "react-router-dom";

export default function NotBuiltPage({ title, stage }: { title: string; stage?: number }) {
  return (
    <div className="rounded border border-dashed border-slate-300 bg-white p-8 text-center text-slate-500">
      <h1 className="text-lg font-semibold text-slate-700">{title}</h1>
      {stage && <p className="mt-1 text-sm">Belum dibuat — dikerjakan di tahap {stage}.</p>}
      <Link to="/" className="mt-4 inline-block text-sm text-blue-700 underline">
        Kembali ke daftar milestone
      </Link>
    </div>
  );
}
