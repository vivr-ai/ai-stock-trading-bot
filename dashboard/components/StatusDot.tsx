/**
 * The Home page's status-strip building block: a colored dot + short label.
 * This is the literal "answer within 5 seconds" pattern from the redesign
 * proposal (§2.5) - Datadog/Grafana-style "everything green," so a user
 * doesn't have to read the rest of the page on a normal day.
 */
export type DotTone = "gain" | "loss" | "warning" | "neutral";

const TONE_CLASSES: Record<DotTone, string> = {
  gain: "bg-gain",
  loss: "bg-loss",
  warning: "bg-amber-400",
  neutral: "bg-muted",
};

export default function StatusDot({ tone, label }: { tone: DotTone; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${TONE_CLASSES[tone]}`} />
      <span className="text-sm text-white">{label}</span>
    </div>
  );
}
