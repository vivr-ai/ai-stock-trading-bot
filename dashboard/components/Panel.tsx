import type { ReactNode } from "react";

const TONE_BAR: Record<string, string> = {
  accent: "bg-accent",
  accent2: "bg-accent2",
  gain: "bg-gain",
  loss: "bg-loss",
  amber: "bg-amber-500",
};

/**
 * A titled container that holds several InfoRows. This is the "compact
 * list" alternative to a grid of StatCards - one bordered box holding N
 * related facts as rows, instead of N separate bordered boxes. Used on
 * Home for everything that isn't one of the top-line hero numbers, which
 * is most of what made the old layout feel like "a lot of boxes" - the
 * data was right, it just had one box per fact.
 *
 * `tone` gives the panel the same thin colored top accent StatCard uses,
 * so a page with several Panels reads as distinct, color-coded zones at a
 * glance instead of a stack of visually identical gray boxes.
 */
export default function Panel({
  title,
  icon,
  tone = "accent",
  children,
  className = "",
}: {
  title: string;
  icon?: ReactNode;
  tone?: "accent" | "accent2" | "gain" | "loss" | "amber";
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`relative overflow-hidden rounded-xl border border-bg-border bg-bg-panel ${className}`}>
      <span className={`absolute inset-x-0 top-0 h-0.5 opacity-70 ${TONE_BAR[tone]}`} />
      <div className="flex items-center gap-2 border-b border-bg-border px-4 py-3">
        {icon}
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      </div>
      <div className="divide-y divide-bg-border/70">{children}</div>
    </div>
  );
}
