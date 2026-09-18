import type { ReactNode } from "react";

/** One label/value fact inside a Panel - the compact building block that
 * replaces a full StatCard for secondary metrics (see Panel's docstring). */
export default function InfoRow({
  label,
  value,
  sublabel,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  sublabel?: ReactNode;
  tone?: "neutral" | "gain" | "loss";
}) {
  const toneClass = tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-white";
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-2.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className="text-right">
        <span className={`font-medium tabular-nums ${toneClass}`}>{value}</span>
        {sublabel && <div className="text-xs text-muted">{sublabel}</div>}
      </span>
    </div>
  );
}
