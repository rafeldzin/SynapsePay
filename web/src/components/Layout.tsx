import { NavLink, Outlet } from "react-router-dom";
import { useAppState, useOperator } from "../lib/state";

const NAV = [
  { to: "/", label: "Milestone", end: true },
  { to: "/ledger", label: "Ledger payout" },
  { to: "/cases", label: "Kasus" },
  { to: "/timeline", label: "Timeline" },
];

function ModeBanners() {
  const state = useAppState();
  const s = state.data;
  if (!s) return null;
  return (
    <>
      {s.paypal_mode === "fake" && (
        <div className="bg-amber-100 px-6 py-1.5 text-center text-xs text-amber-900">
          PAYPAL_MODE=fake: PayPal disimulasikan, tidak ada ID PayPal yang asli.
        </div>
      )}
      {s.llm_mode === "fake" && (
        <div className="bg-amber-100 px-6 py-1.5 text-center text-xs text-amber-900">
          LLM_MODE=fake: verifier dan investigator memakai jawaban uji yang sudah disiapkan.
        </div>
      )}
    </>
  );
}

export default function Layout() {
  const { operator, setOperator } = useOperator();
  const state = useAppState();

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3">
          <div className="whitespace-nowrap">
            <span className="font-semibold">SynapsePay</span>
            <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
              PayPal sandbox
            </span>
          </div>
          <nav className="flex flex-wrap gap-1 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded px-3 py-1.5 ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex flex-wrap items-center gap-3 text-xs text-slate-500">
            {state.data && (
              <span>
                Skenario <span className="font-mono text-slate-800">{state.data.scenario ?? "—"}</span> · run{" "}
                <span className="font-mono">{state.data.run_id ?? "—"}</span>
              </span>
            )}
            <label className="flex items-center gap-1.5">
              Operator
              <input
                value={operator}
                onChange={(e) => setOperator(e.target.value)}
                size={10}
                autoComplete="off"
                className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              />
            </label>
          </div>
        </div>
      </header>
      <ModeBanners />
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
