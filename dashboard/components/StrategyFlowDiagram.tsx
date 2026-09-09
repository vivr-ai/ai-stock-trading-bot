import { ArrowDown, TrendingUp, TrendingDown, Minus, FlaskConical } from "lucide-react";
import type { ReactNode } from "react";

function Step({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2.5 text-center text-xs leading-relaxed text-white">
      {children}
    </div>
  );
}

function Connector() {
  return (
    <div className="flex justify-center py-1">
      <ArrowDown size={14} className="text-muted" />
    </div>
  );
}

function Outcome({
  tone,
  children,
}: {
  tone: "gain" | "amber";
  children: ReactNode;
}) {
  const toneClass =
    tone === "gain" ? "border-gain/50 bg-gain/10 text-gain" : "border-amber-500/50 bg-amber-500/10 text-amber-400";
  return (
    <div className={`rounded-lg border px-3 py-2.5 text-center text-xs font-semibold leading-relaxed ${toneClass}`}>
      {children}
    </div>
  );
}

export default function StrategyFlowDiagram() {
  return (
    <div className="w-full" role="img" aria-label="Flow diagram of how the bot evaluates a buy signal along its two paths">
      {/* shared intake */}
      <div className="mx-auto max-w-md rounded-xl border border-bg-border bg-bg-panel2 px-4 py-3 text-center text-sm text-white">
        Every 30 minutes during market hours — for each stock on the watch-list, after confirming the overall
        market isn&apos;t down sharply or in a downtrend
      </div>
      <Connector />

      {/* two paths side by side */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Path A */}
        <div className="rounded-xl border border-accent/40 bg-accent/5 p-3">
          <div className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-accent">
            <TrendingUp size={13} /> Path A — News-driven momentum
          </div>
          <Step>Read today&apos;s headlines, score sentiment -10 to +10</Step>
          <Connector />
          <Step>
            Weighted score: 50% sentiment quality + 20% headline coverage + 30% volume confirmation — partial
            credit on each, no single weak leg auto-fails it
          </Step>
          <Connector />
          <Step>Still hard gates: price above its 20-day average, hasn&apos;t already jumped &gt;8% today</Step>
          <Connector />
          <Outcome tone="gain">Composite score ≥ 0.60 → BUY signal</Outcome>
        </div>

        {/* Path B */}
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-3">
          <div className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-400">
            <FlaskConical size={13} /> Path B — Technical mean-reversion
            <span className="ml-auto rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-semibold text-amber-400">
              SHADOW MODE
            </span>
          </div>
          <Step>Price above its 200-day average? (confirms a genuine long-term uptrend, not a stock in decline)</Step>
          <Connector />
          <Step>2-day RSI at or below 10? (sharply, briefly oversold)</Step>
          <Connector />
          <Step>Volume ≥1.3x normal, and news sentiment isn&apos;t actively bearish (above -5)</Step>
          <Connector />
          <Outcome tone="amber">All yes → WOULD buy — logged only, no order is placed</Outcome>
        </div>
      </div>

      <Connector />
      <div className="mx-auto max-w-md rounded-xl border border-bg-border bg-bg-panel2 px-4 py-3 text-center text-sm text-white">
        Every real buy signal still has to clear the risk rules — position size, sector cap, available cash —
        before anything is acted on
      </div>
      <Connector />

      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col items-center gap-1 rounded-lg border border-loss/40 bg-loss/10 px-2 py-2.5 text-xs font-semibold text-loss">
          <TrendingDown size={14} /> SELL
        </div>
        <div className="flex flex-col items-center gap-1 rounded-lg border border-bg-border bg-bg-panel2 px-2 py-2.5 text-xs font-semibold text-muted">
          <Minus size={14} /> HOLD
        </div>
        <div className="flex flex-col items-center gap-1 rounded-lg border border-gain/40 bg-gain/10 px-2 py-2.5 text-xs font-semibold text-gain">
          <TrendingUp size={14} /> BUY
        </div>
      </div>
      <p className="mt-2 text-center text-[11px] text-muted">
        Then it waits for the next cycle (~30 minutes) and repeats — nothing carries over except its actual open
        positions.
      </p>
    </div>
  );
}
