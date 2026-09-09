"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import StatCard from "@/components/StatCard";
import { fmtMoney, fmtPct, fmtNumber, timeAgo, toneFor } from "@/lib/format";
import { FlaskConical, TrendingUp, Info } from "lucide-react";

type ShadowEpisode = {
  symbol: string;
  status: "open" | "closed";
  entryTs: string;
  entryPrice: number;
  entryReason: string | null;
  exitTs: string | null;
  exitPrice: number | null;
  exitReason: string | null;
  pnlPct: number | null;
  heldDays: number | null;
  lastMarkTs: string | null;
};

type ShadowStats = {
  closedCount: number;
  openCount: number;
  winRatePct: number | null;
  avgPnlPct: number | null;
  totalPnlPct: number | null;
  bestPnlPct: number | null;
  worstPnlPct: number | null;
  avgHeldDays: number | null;
};

type LiveStats = {
  totalTrades: number;
  winRatePct: number | null;
  avgConfidenceScore: number | null;
  totalPnl: number | null;
};

type ComparisonResponse = {
  hasEverRun: boolean;
  sinceTs: string | null;
  episodes: ShadowEpisode[];
  shadowStats: ShadowStats;
  liveMomentumStats: LiveStats;
  liveReversionStats: LiveStats;
  reversionLiveEnabled: boolean;
  reversionMaxHoldDays: number;
};

function daysSince(iso: string | null): number | null {
  if (!iso) return null;
  return (Date.now() - new Date(iso).getTime()) / 86_400_000;
}

// Rough rule of thumb, not a formal power calculation: with reversion_max_hold_days
// capping any one shadow round-trip at a few days, a handful of weeks should
// produce a double-digit closed sample IF the RSI(2)-oversold condition keeps
// firing at anything like its recent rate. Shown as guidance, not a hard rule.
const MIN_CLOSED_FOR_A_READ = 20;
const MIN_WEEKS_FOR_A_READ = 4;

export default function ShadowComparisonPage() {
  const [data, setData] = useState<ComparisonResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/shadow-comparison");
        if (!res.ok) throw new Error((await res.json()).error || "Request failed");
        setData(await res.json());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const weeksObserved = data?.sinceTs ? (daysSince(data.sinceTs) ?? 0) / 7 : 0;
  const readyForVerdict =
    !!data && data.shadowStats.closedCount >= MIN_CLOSED_FOR_A_READ && weeksObserved >= MIN_WEEKS_FOR_A_READ;

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Shadow vs Live</h1>
        <p className="text-sm text-muted">
          Path B (technical mean-reversion) runs in shadow mode — it never places a real order. This page
          reconstructs what it would have done and compares it against what actually traded.
        </p>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && (
        <div className="space-y-6">
          {/* Verdict guidance */}
          <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
            <div className="flex items-start gap-2">
              <Info size={16} className="mt-0.5 shrink-0 text-accent" />
              <div className="text-sm text-muted">
                <p className="text-white">
                  {data.sinceTs
                    ? `Observing since ${new Date(data.sinceTs).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })} (${weeksObserved.toFixed(1)} week${weeksObserved >= 2 ? "s" : ""}).`
                    : "No shadow signals recorded yet."}
                </p>
                <p className="mt-1">
                  Rule of thumb, not a hard cutoff: wait for at least{" "}
                  <span className="text-white">{MIN_CLOSED_FOR_A_READ} closed shadow round-trips</span> and{" "}
                  <span className="text-white">{MIN_WEEKS_FOR_A_READ}+ weeks</span> of observation before
                  drawing a conclusion — Path B only trades a handful of names at a time (RSI(2) oversold is a
                  rare condition), and its own exit rule caps any one round-trip at{" "}
                  {data.reversionMaxHoldDays} days, so a short window under-samples how it performs
                  across different market conditions. Right now:{" "}
                  <span className={readyForVerdict ? "text-gain" : "text-amber-400"}>
                    {data.shadowStats.closedCount} closed, {weeksObserved.toFixed(1)} weeks —{" "}
                    {readyForVerdict ? "enough to start forming a view" : "too early to call it"}.
                  </span>
                </p>
                <p className="mt-1 text-xs">
                  This data is captured durably in Postgres (survives redeploys) as soon as each shadow signal
                  resolves, so it&apos;s safe to leave this running for a month or more and check back — nothing
                  needs to be done to keep collecting it.
                </p>
                {!readyForVerdict && (
                  <p className="mt-1 text-xs">
                    No need to keep checking, either — a &ldquo;Path B shadow data ready for a verdict&rdquo;
                    notification fires automatically (Telegram, if configured, and always in the dashboard&apos;s
                    Notifications Centre) the moment this bar is crossed, once, ever.
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Shadow stats */}
          <section>
            <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-amber-400">
              <FlaskConical size={15} /> Path B — shadow (simulated, no real orders)
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatCard label="Closed round-trips" value={fmtNumber(data.shadowStats.closedCount)} />
              <StatCard label="Still open" value={fmtNumber(data.shadowStats.openCount)} />
              <StatCard
                label="Win rate"
                value={data.shadowStats.winRatePct != null ? `${data.shadowStats.winRatePct.toFixed(1)}%` : "—"}
              />
              <StatCard
                label="Avg return"
                value={fmtPct(data.shadowStats.avgPnlPct)}
                tone={toneFor(data.shadowStats.avgPnlPct)}
              />
              <StatCard
                label="Total return (summed)"
                value={fmtPct(data.shadowStats.totalPnlPct)}
                tone={toneFor(data.shadowStats.totalPnlPct)}
                sublabel="Simple sum across round-trips, not compounded"
              />
              <StatCard
                label="Best"
                value={fmtPct(data.shadowStats.bestPnlPct)}
                tone={toneFor(data.shadowStats.bestPnlPct)}
              />
              <StatCard
                label="Worst"
                value={fmtPct(data.shadowStats.worstPnlPct)}
                tone={toneFor(data.shadowStats.worstPnlPct)}
              />
              <StatCard
                label="Avg hold"
                value={data.shadowStats.avgHeldDays != null ? `${data.shadowStats.avgHeldDays.toFixed(1)}d` : "—"}
              />
            </div>
          </section>

          {/* Live stats */}
          <section>
            <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-accent">
              <TrendingUp size={15} /> Live, same window
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
                <div className="mb-2 text-xs font-medium text-muted">Path A — news-driven momentum (live)</div>
                <div className="grid grid-cols-3 gap-3">
                  <StatCard label="Trades" value={fmtNumber(data.liveMomentumStats.totalTrades)} />
                  <StatCard
                    label="Win rate"
                    value={
                      data.liveMomentumStats.winRatePct != null
                        ? `${data.liveMomentumStats.winRatePct.toFixed(1)}%`
                        : "—"
                    }
                  />
                  <StatCard
                    label="Total P/L"
                    value={fmtMoney(data.liveMomentumStats.totalPnl)}
                    tone={toneFor(data.liveMomentumStats.totalPnl)}
                  />
                </div>
              </div>
              <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
                <div className="mb-2 text-xs font-medium text-muted">Path B — mean-reversion (live)</div>
                {data.reversionLiveEnabled ? (
                  <div className="grid grid-cols-3 gap-3">
                    <StatCard label="Trades" value={fmtNumber(data.liveReversionStats.totalTrades)} />
                    <StatCard
                      label="Win rate"
                      value={
                        data.liveReversionStats.winRatePct != null
                          ? `${data.liveReversionStats.winRatePct.toFixed(1)}%`
                          : "—"
                      }
                    />
                    <StatCard
                      label="Total P/L"
                      value={fmtMoney(data.liveReversionStats.totalPnl)}
                      tone={toneFor(data.liveReversionStats.totalPnl)}
                    />
                  </div>
                ) : (
                  <p className="py-2 text-sm text-muted">
                    Still in shadow mode (<code className="text-xs">STRATEGY_REVERSION_LIVE=false</code>) — no
                    real Path B trades yet. This card fills in automatically once it&apos;s switched on.
                  </p>
                )}
              </div>
            </div>
          </section>

          {/* Episode log */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-white">Shadow round-trips</h2>
            {data.episodes.length === 0 && (
              <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
                No Path B signals have fired yet.
              </div>
            )}
            <div className="space-y-2">
              {data.episodes.map((e, i) => (
                <div key={`${e.symbol}-${e.entryTs}-${i}`} className="rounded-xl border border-bg-border bg-bg-panel p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-white">{e.symbol}</span>
                      <span
                        className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                          e.status === "open"
                            ? "bg-amber-500/15 text-amber-400"
                            : "bg-bg-panel2 text-muted"
                        }`}
                      >
                        {e.status === "open" ? "Still open (unrealized)" : "Closed"}
                      </span>
                      <span className="text-xs text-muted">
                        entered {new Date(e.entryTs).toLocaleDateString(undefined, { month: "short", day: "numeric" })} @{" "}
                        {e.entryPrice.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 text-sm">
                      {e.heldDays != null && <span className="text-xs text-muted">{e.heldDays.toFixed(1)}d held</span>}
                      <span className={`font-semibold tabular-nums ${toneFor(e.pnlPct) === "gain" ? "text-gain" : toneFor(e.pnlPct) === "loss" ? "text-loss" : "text-muted"}`}>
                        {fmtPct(e.pnlPct)}
                      </span>
                    </div>
                  </div>
                  {e.entryReason && (
                    <p className="mt-2 text-xs text-muted">
                      <span className="text-white">Entry: </span>
                      {e.entryReason}
                    </p>
                  )}
                  {e.exitReason && (
                    <p className="mt-1 text-xs text-muted">
                      <span className="text-white">Exit: </span>
                      {e.exitReason}
                    </p>
                  )}
                  {e.status === "open" && e.lastMarkTs && (
                    <p className="mt-1 text-xs text-muted">
                      Last marked {timeAgo(e.lastMarkTs)} — may lag the live price by up to one cycle.
                    </p>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
