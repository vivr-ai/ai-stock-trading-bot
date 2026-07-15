import { AlertTriangle, AlertOctagon, Info } from "lucide-react";

export type HomeAlert = { severity: "critical" | "warning" | "info"; message: string };

const SEVERITY_STYLE: Record<HomeAlert["severity"], { border: string; bg: string; icon: JSX.Element }> = {
  critical: { border: "border-loss/40", bg: "bg-loss/10", icon: <AlertOctagon size={16} className="text-loss" /> },
  warning: { border: "border-amber-400/40", bg: "bg-amber-400/10", icon: <AlertTriangle size={16} className="text-amber-400" /> },
  info: { border: "border-accent/40", bg: "bg-accent/10", icon: <Info size={16} className="text-accent" /> },
};

/** Distinct, non-card treatment for alerts (redesign proposal §2.5) - a
 * banner strip, not another StatCard, and renders NOTHING when there are
 * no alerts (an empty Alerts section should disappear, not show an empty
 * card). Home passes in a merged feed: risk breaches, recent system
 * notifications, and pending recommendations. */
export default function AlertsBanner({ alerts }: { alerts: HomeAlert[] }) {
  if (alerts.length === 0) return null;
  return (
    <div className="mb-6 flex flex-col gap-2">
      {alerts.map((alert, i) => {
        const style = SEVERITY_STYLE[alert.severity];
        return (
          <div
            key={i}
            className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm text-white ${style.border} ${style.bg}`}
          >
            {style.icon}
            <span>{alert.message}</span>
          </div>
        );
      })}
    </div>
  );
}
