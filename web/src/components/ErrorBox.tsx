import { ApiError } from "../api/client";
import GateChecklist from "./GateChecklist";

interface Props {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}

/** Menampilkan nama error, debug_id, dan (untuk HTTP 409) checklist gate apa adanya dari backend. */
export default function ErrorBox({ error, title = "Permintaan gagal", onRetry }: Props) {
  const isApi = error instanceof ApiError;
  const name = isApi ? error.name : error instanceof Error ? error.name : "UNKNOWN_ERROR";
  const message = error instanceof Error ? error.message : String(error);
  const gate = isApi ? error.gate : null;

  return (
    <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-900">
      <div className="font-semibold">{title}</div>
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
        <dt className="text-red-700">Error</dt>
        <dd className="font-mono">{name}</dd>
        <dt className="text-red-700">Pesan</dt>
        <dd>{message}</dd>
        {isApi && error.status > 0 && (
          <>
            <dt className="text-red-700">HTTP</dt>
            <dd className="font-mono">{error.status}</dd>
          </>
        )}
        {isApi && error.debugId && (
          <>
            <dt className="text-red-700">debug_id</dt>
            <dd className="font-mono">{error.debugId}</dd>
          </>
        )}
      </dl>
      {gate && (
        <div className="mt-3 text-slate-900">
          <GateChecklist checks={gate.checks} />
        </div>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-red-300 bg-white px-3 py-1 text-red-800 hover:bg-red-100"
        >
          Coba lagi
        </button>
      )}
    </div>
  );
}
