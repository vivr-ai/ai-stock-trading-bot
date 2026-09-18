import type { ReactNode } from "react";

/**
 * A titled container that holds several InfoRows. This is the "compact
 * list" alternative to a grid of StatCards - one bordered box holding N
 * related facts as rows, instead of N separate bordered boxes. Used on
 * Home for everything that isn't one of the 4 top-line hero numbers, which
 * is most of what made the old layout feel like "a lot of boxes" - the
 * data was right, it just had one box per fact.
 */
export default function Panel({
  title,
  icon,
  children,
  className = "",
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-bg-border bg-bg-panel ${className}`}>
      <div className="flex items-center gap-2 border-b border-bg-border px-4 py-3">
        {icon}
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      </div>
      <div className="divide-y divide-bg-border/70">{children}</div>
    </div>
  );
}
