import type { ReactNode } from "react";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

const STYLES = {
  buy: { icon: TrendingUp, label: "Example BUY", badge: "bg-gain/15 text-gain" },
  sell: { icon: TrendingDown, label: "Example SELL", badge: "bg-loss/15 text-loss" },
  hold: { icon: Minus, label: "Example HOLD", badge: "bg-bg-panel2 text-muted" },
} as const;

export default function ExampleCard({
  type,
  title,
  children,
}: {
  type: "buy" | "sell" | "hold";
  title: string;
  children: ReactNode;
}) {
  const { icon: Icon, label, badge } = STYLES[type];
  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
      <div className={`mb-2 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${badge}`}>
        <Icon size={13} />
        {label}
      </div>
      <h3 className="mb-1 text-sm font-semibold text-white">{title}</h3>
      <div className="text-sm leading-relaxed text-muted">{children}</div>
    </div>
  );
}
