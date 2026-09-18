import type { ReactNode } from "react";

/**
 * The dashboard's primary metric tile. `size="lg"` is for the small set of
 * hero numbers a page wants answered at a glance (Home's top row); the
 * default size is for everything else. Either way this stays a single,
 * clearly-bounded "fact" - denser groupings of secondary metrics belong in
 * InfoRow/Panel instead of more of these side by side (see Home for the
 * split), which is what keeps a page from turning into a wall of
 * identical boxes.
 */
export default function StatCard({
  label,
  value,
  sublabel,
  tone = "neutral",
  icon,
  size = "md",
}: {
  label: string;
  value: ReactNode;
  sublabel?: ReactNode;
  tone?: "neutral" | "gain" | "loss";
  icon?: ReactNode;
  size?: "md" | "lg";
}) {
  const toneClass =
    tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-white";
  const barClass = tone === "gain" ? "bg-gain" : tone === "loss" ? "bg-loss" : "bg-accent";

  return (
    <div
      className={`group relative overflow-hidden rounded-xl border border-bg-border bg-bg-panel transition-colors hover:border-bg-border/60 ${
        size === "lg" ? "p-5" : "p-4"
      }`}
    >
      <span className={`absolute inset-x-0 top-0 h-0.5 opacity-70 ${barClass}`} />
      <div className="flex items-center justify-between text-xs text-muted">
        <span className="uppercase tracking-wide">{label}</span>
        {icon && <span className="text-muted/80 transition-colors group-hover:text-muted">{icon}</span>}
      </div>
      <div
        className={`mt-2 font-semibold tabular-nums ${toneClass} ${
          size === "lg" ? "text-3xl" : "text-2xl"
        }`}
      >
        {value}
      </div>
      {sublabel && <div className="mt-1 text-xs text-muted">{sublabel}</div>}
    </div>
  );
}
