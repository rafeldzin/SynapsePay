import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "Milestone", end: true },
  { to: "/ledger", label: "Ledger payout" },
  { to: "/timeline", label: "Timeline" },
  { to: "/cases", label: "Kasus" },
];

export default function Layout() {
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
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
