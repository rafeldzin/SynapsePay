import type { ReactNode } from "react";

export default function Card({
  title,
  step,
  aside,
  children,
}: {
  title: string;
  step?: number;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded border border-slate-200 bg-white">
      <header className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-2.5">
        <h2 className="font-semibold">
          {step !== undefined && <span className="mr-2 text-slate-400">{step}.</span>}
          {title}
        </h2>
        {aside}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
