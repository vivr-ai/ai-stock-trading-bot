import { ArrowDown, TrendingUp, TrendingDown, FlaskConical, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";

function Box({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "gain" | "loss";
}) {
  const toneClass =
    tone === "gain"
      ? "border-gain/40 bg-gain/5"
      : tone === "loss"
        ? "border-loss/40 bg-loss/5"
        : "border-bg-border bg-bg-panel2";
  return (
    <div className={`mx-auto max-w-lg rounded-lg border px-4 py-2.5 text-center text-sm text-white ${toneClass}`}>
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

function MiniStep({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-bg-border bg-bg-panel px-2.5 py-1.5 text-center text-[11px] leading-snug text-muted">
      {children}
    </div>
  );
}

export default function StrategyFlowDiagram() {
  return (
    <div className="w-full" role="img" aria-label="Diagram of the bot's full buy-to-sell lifecycle for one stock">
      <Box>Watch-list scan, every ~30 min while the market&apos;s open</Box>
      <Connector />

      {/* BUY decision — green theme */}
      <div className="rounded-xl border border-gain/30 bg-gain/[0.03] p-3">
        <div className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gain">
          <TrendingUp size={13} /> Buy check
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-accent/30 bg-bg-panel2 p-2.5">
            <div className="mb-2 text-[11px] font-semibold text-accent">Path A · news momentum</div>
            <div className="space-y-1.5">
              <MiniStep>Sentiment + headlines + volume → weighted score ≥ 0.60</MiniStep>
              <MiniStep>Not already +8% today, price above 20-day average</MiniStep>
            </div>
          </div>
          <div className="rounded-lg border border-amber-500/30 bg-bg-panel2 p-2.5">
            <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-amber-400">
              <FlaskConical size={11} /> Path B · mean-reversion
              <span className="ml-auto rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[9px] font-semibold">
                SHADOW
              </span>
            </div>
            <div className="space-y-1.5">
              <MiniStep>Above 200-day average, 2-day RSI ≤ 10, volume ≥ 1.3x</MiniStep>
              <MiniStep>Logged only — no order placed yet</MiniStep>
            </div>
          </div>
        </div>
      </div>
      <Connector />
      <Box tone="gain">Position opened — stop-loss &amp; take-profit/trailing attached immediately</Box>
      <Connector />
      <Box>Held position re-checked against every sell rule, every cycle</Box>
      <Connector />

      {/* SELL decision — red theme */}
      <div className="rounded-xl border border-loss/30 bg-loss/[0.03] p-3">
        <div className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-loss">
          <TrendingDown size={13} /> Sell check — any one rule firing is enough
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <MiniStep>Stop-loss −10%</MiniStep>
          <MiniStep>Take-profit +20% or trailing pullback</MiniStep>
          <MiniStep>Sentiment −5 (or −8 severe)</MiniStep>
          <MiniStep>10 trading days, going nowhere</MiniStep>
        </div>
      </div>
      <Connector />
      <Box tone="loss">Position closed</Box>
      <Connector />
      <div className="mx-auto flex max-w-lg items-center justify-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 px-4 py-2.5 text-center text-xs text-muted">
        <RotateCcw size={13} className="shrink-0" /> 24-hour cooldown, then eligible to be bought again
      </div>
    </div>
  );
}
