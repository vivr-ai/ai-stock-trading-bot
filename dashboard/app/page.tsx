"use client";

/**
 * Home: the "operations cockpit" redesigned per docs/dashboard-ux-redesign-
 * proposal.md §2.5, further condensed (Sep 2026) from a 6-section grid of
 * 24 uniform StatCards down to:
 *   1. A status strip pinned above everything (Bot / Market / Risk /
 *      Today's P/L) - the literal "answer within 5 seconds" affordance.
 *   2. Alerts as a distinct banner, not a card, hidden entirely when empty.
 *   3. A 4-card hero row for the numbers that matter most at a glance
 *      (Portfolio Value, Today's P/L, Open Positions, Risk Level).
 *   4. Everything else grouped into 4 compact Panels of InfoRows (Portfolio,
 *      Trading Activity, Bot & Market, AI & Risk) instead of one StatCard
 *      per fact - same data, far less vertical space and far fewer boxes,
 *      so the page reads as a small number of coherent panels rather than
 *      a wall of identical tiles.
 * All data comes from one aggregator, /api/home (see that route for the
 * heuristics disclosure on "next scheduled run" / "time until next
 * session").
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import StatCard from "@/components/StatCard";
import StatusBadge from "@/components/StatusBadge";
import StatusDot, { type DotTone } from "@/components/StatusDot";
import AlertsBanner, { type HomeAlert } from "@/components/AlertsBanner";
import Panel from "@/components/Panel";
import InfoRow from "@/components/InfoRow";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { fmtMoney, fmtPct, timeAgo, toneFor } from "@/lib/format";
import {
  Wallet,
  TrendingUp,
  Layers,
  Repeat,
  Bot,
  Gauge,
  ShieldAlert,
  Pause,
  Play,
  OctagonAlert,
  Eye,
  Briefcase,
  HeartPulse,
} from "lucide-react";

type HomeResponse = {
  hasAnyData: boolean;
  statusStrip: {
    bot: "running" | "stopped" | "error";
    market: "open" | "closed";
    risk: "normal" | "elevated" | "high";
    todaysPl: number;
  };
  portfolio: {
    value: number | null;
    cash: number | null;
    todaysPl: number;
    weeklyReturnPct: number | null;
    monthlyReturnPct: number | null;
    lifetimeReturnPct: number | null;
    isAsOfLastActiveCycle: boolean;
  };
  tradingActivity: {
    openPositions: number;
    openOrders: null;
    lastTrade: { ts: string; action: string; symbol: string; qty: number | null; price: number | null } | null;
    tradesToday: number;
    winRatePct: number | null;
  };
  botStatusPanel: {
    running: boolean;
    lastHeartbeat: string | null;
    schedulerStatus: string | null;
    dryRun: boolean | null;
    lastSuccessfulRun: string | null;
    nextScheduledRun: string;
  };
  marketStatus: {
    open: boolean | null;
    sessionLabel: string;
    minutesUntilNextOpen: number | null;
  };
  aiActivity: {
    latestDecision: {
      ts: string;
      symbol: string;
      decision: string;
      reason: string | null;
      confidence: number | null;
      rationale: string | null;
    } | null;
    activeStrategyVersion: string;
    marketSentimentLabel: string | null;
  };
  riskSnapshot: {
    totalExposurePct: number | null;
    largestPosition: { symbol: string; allocationPct: number | null } | null;
    drawdownPct: number | null;
    riskLevel: "normal" | "elevated" | "high";
    dailyLossLimitPct: number;
    dailyPnlPct: number | null;
  };
  alerts: HomeAlert[];
  botControl: { isPaused: boolean; reason: string | null };
};

function riskDotTone(risk: "normal" | "elevated" | "high"): DotTone {
  return risk === "high" ? "loss" : risk === "elevated" ? "warning" : "gain";
}

function fmtMinutes(mins: number | null): string {
  if (mins == null) return "—";
  if (mins < 60) return `${Math.round(mins)}m`;
  const hours = Math.floor(mins / 60);
  const rem = Math.round(mins % 60);
  return `${hours}h ${rem}m`;
}

export default function HomePage() {
  const [data, setData] = useState<HomeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function load() {
    try {
      const res = await fetch("/api/home");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      setData(await res.json());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  async function setBotControl(action: "pause" | "resume", reason?: string) {
    setActionPending(true);
    setActionError(null);
    try {
      const res = await fetch("/api/bot-control", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action, reason }),
      });
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      await load();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionPending(false);
    }
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Home</h1>
          <p className="text-sm text-muted">Live overview of the trading bot.</p>
        </div>
      </div>

      {loading && <LoadingSkeleton rows={8} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasAnyData && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. Once the bot runs its next cycle with{" "}
          <code className="rounded bg-bg-panel2 px-1 py-0.5">DATABASE_URL</code> configured, this
          page will populate automatically.
        </div>
      )}

      {!loading && !error && data && data.hasAnyData && (
        <>
          {/* ---- Status strip: the 5-second answer ---- */}
          <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-bg-border bg-bg-panel px-4 py-3">
            <StatusDot
              tone={
                data.statusStrip.bot === "running" ? "gain" : data.statusStrip.bot === "error" ? "loss" : "neutral"
              }
              label={`Bot ${data.statusStrip.bot === "running" ? "running" : data.statusStrip.bot === "error" ? "error" : "stopped"}`}
            />
            <StatusDot
              tone={data.statusStrip.market === "open" ? "gain" : "neutral"}
              label={`Market ${data.statusStrip.market}`}
            />
            <StatusDot tone={riskDotTone(data.statusStrip.risk)} label={`Risk ${data.statusStrip.risk}`} />
            <StatusDot
              tone={toneFor(data.statusStrip.todaysPl) === "loss" ? "loss" : toneFor(data.statusStrip.todaysPl) === "gain" ? "gain" : "neutral"}
              label={`Today's P/L ${fmtMoney(data.statusStrip.todaysPl)}`}
            />
            {data.botControl.isPaused && (
              <span className="ml-auto">
                <StatusBadge status="paused" label={data.botControl.reason ? `Paused: ${data.botControl.reason}` : "Trading paused"} />
              </span>
            )}
          </div>

          <AlertsBanner alerts={data.alerts} />

          {/* ---- Hero row: the 4 numbers that matter most, answered without
              any scrolling - Portfolio's the daily-use question ("what's my
              money doing"), the other 3 round out "is anything wrong." Every
              other fact on this page is one click away in a Panel below,
              not because it's unimportant, but because it's not what you
              open Home to check every 30 minutes. */}
          <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              size="lg"
              label="Portfolio Value"
              value={fmtMoney(data.portfolio.value)}
              sublabel={data.portfolio.isAsOfLastActiveCycle ? "As of last active cycle (market closed)" : "Live"}
              icon={<Wallet size={15} />}
            />
            <StatCard
              size="lg"
              label="Today's P/L"
              value={fmtMoney(data.portfolio.todaysPl)}
              tone={toneFor(data.portfolio.todaysPl)}
              icon={<TrendingUp size={15} />}
            />
            <StatCard
              size="lg"
              label="Open Positions"
              value={data.tradingActivity.openPositions}
              sublabel={fmtPct(data.riskSnapshot.totalExposurePct, 1) + " of capital deployed"}
              icon={<Layers size={15} />}
            />
            <StatCard
              size="lg"
              label="Risk Level"
              value={data.riskSnapshot.riskLevel[0].toUpperCase() + data.riskSnapshot.riskLevel.slice(1)}
              tone={data.riskSnapshot.riskLevel === "high" ? "loss" : data.riskSnapshot.riskLevel === "elevated" ? "neutral" : "gain"}
              sublabel={`Daily loss limit ${data.riskSnapshot.dailyLossLimitPct}%`}
              icon={<ShieldAlert size={15} />}
            />
          </div>

          {/* ---- Everything else: grouped into a few compact panels of rows
              instead of one StatCard per fact - same information, far fewer
              boxes and far less vertical space per fact. */}
          <div className="mb-6 grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="flex flex-col gap-5">
              <Panel title="Portfolio" icon={<Wallet size={14} className="text-accent" />}>
                <InfoRow
                  label="Cash Available"
                  value={fmtMoney(data.portfolio.cash)}
                  sublabel={data.portfolio.isAsOfLastActiveCycle ? "As of last active cycle" : undefined}
                />
                <InfoRow
                  label="Weekly Return"
                  value={fmtPct(data.portfolio.weeklyReturnPct)}
                  tone={toneFor(data.portfolio.weeklyReturnPct)}
                />
                <InfoRow
                  label="Monthly Return"
                  value={fmtPct(data.portfolio.monthlyReturnPct)}
                  tone={toneFor(data.portfolio.monthlyReturnPct)}
                />
                <InfoRow
                  label="Lifetime Return"
                  value={fmtPct(data.portfolio.lifetimeReturnPct)}
                  tone={toneFor(data.portfolio.lifetimeReturnPct)}
                />
              </Panel>

              <Panel title="Trading Activity" icon={<Repeat size={14} className="text-accent" />}>
                <InfoRow
                  label="Last Executed Trade"
                  value={
                    data.tradingActivity.lastTrade
                      ? `${data.tradingActivity.lastTrade.action.toUpperCase()} ${data.tradingActivity.lastTrade.symbol}`
                      : "—"
                  }
                  sublabel={
                    data.tradingActivity.lastTrade
                      ? `${data.tradingActivity.lastTrade.qty ?? ""} @ ${fmtMoney(
                          data.tradingActivity.lastTrade.price
                        )} · ${timeAgo(data.tradingActivity.lastTrade.ts)}`
                      : "No trades yet"
                  }
                />
                <InfoRow label="Trades Today" value={data.tradingActivity.tradesToday} />
                <InfoRow
                  label="Win Rate (all-time)"
                  value={data.tradingActivity.winRatePct != null ? `${data.tradingActivity.winRatePct.toFixed(1)}%` : "—"}
                />
              </Panel>
            </div>

            <div className="flex flex-col gap-5">
              <Panel title="Bot & Market" icon={<Bot size={14} className="text-accent" />}>
                <InfoRow
                  label="Running / Paused"
                  value={
                    <StatusBadge status={data.botControl.isPaused ? "paused" : data.botStatusPanel.running ? "running" : "stopped"} />
                  }
                  sublabel={data.botStatusPanel.dryRun ? "Dry run (no real orders)" : "Live paper trading"}
                />
                <InfoRow
                  label="Last Heartbeat"
                  value={timeAgo(data.botStatusPanel.lastHeartbeat)}
                  sublabel={data.botStatusPanel.lastHeartbeat ? new Date(data.botStatusPanel.lastHeartbeat).toLocaleString() : undefined}
                />
                <InfoRow label="Scheduler Status" value={data.botStatusPanel.schedulerStatus ?? "—"} />
                <InfoRow
                  label="Next Scheduled Run"
                  value={new Date(data.botStatusPanel.nextScheduledRun).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  sublabel="Estimate - doesn't account for market holidays"
                />
                <InfoRow
                  label="Market Status"
                  value={data.marketStatus.open == null ? "—" : data.marketStatus.open ? "Open" : "Closed"}
                  tone={data.marketStatus.open ? "gain" : "neutral"}
                />
                <InfoRow label="Current Session" value={data.marketStatus.sessionLabel} />
                <InfoRow
                  label="Time Until Next Session"
                  value={fmtMinutes(data.marketStatus.minutesUntilNextOpen)}
                  sublabel={data.marketStatus.minutesUntilNextOpen == null ? "Already in session" : "Estimate - no holiday calendar"}
                />
              </Panel>

              <Panel title="AI & Risk" icon={<Gauge size={14} className="text-accent2" />}>
                <InfoRow
                  label="Latest AI Decision"
                  value={
                    data.aiActivity.latestDecision
                      ? `${data.aiActivity.latestDecision.decision.toUpperCase()} ${data.aiActivity.latestDecision.symbol}`
                      : "—"
                  }
                  sublabel={data.aiActivity.latestDecision ? timeAgo(data.aiActivity.latestDecision.ts) : "No decisions yet"}
                />
                <InfoRow
                  label="Confidence Score"
                  value={
                    data.aiActivity.latestDecision?.confidence != null
                      ? data.aiActivity.latestDecision.confidence.toFixed(1)
                      : "—"
                  }
                  sublabel="Sentiment score (-10 to +10), not a probability"
                />
                <InfoRow label="Strategy Version" value={data.aiActivity.activeStrategyVersion} />
                <InfoRow label="Market Sentiment" value={data.aiActivity.marketSentimentLabel ?? "—"} />
                <InfoRow
                  label="Current Drawdown"
                  value={fmtPct(data.riskSnapshot.drawdownPct != null ? -data.riskSnapshot.drawdownPct : null, 1)}
                  tone={data.riskSnapshot.drawdownPct ? "loss" : "neutral"}
                />
                <InfoRow
                  label="Largest Position"
                  value={data.riskSnapshot.largestPosition?.symbol ?? "—"}
                  sublabel={
                    data.riskSnapshot.largestPosition
                      ? fmtPct(data.riskSnapshot.largestPosition.allocationPct, 1)
                      : undefined
                  }
                />
              </Panel>
            </div>
          </div>

          <div className="mb-8">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Quick Actions</h2>
            {actionError && <div className="mb-3"><ErrorState message={actionError} /></div>}
            <div className="flex flex-wrap gap-3">
              {data.botControl.isPaused ? (
                <button
                  onClick={() => setBotControl("resume")}
                  disabled={actionPending}
                  className="flex items-center gap-2 rounded-lg bg-gain/15 px-4 py-2 text-sm font-medium text-gain hover:bg-gain/25 disabled:opacity-50"
                >
                  <Play size={16} /> Resume Trading
                </button>
              ) : (
                <button
                  onClick={() => setBotControl("pause", "Paused from Home")}
                  disabled={actionPending}
                  className="flex items-center gap-2 rounded-lg bg-bg-panel2 px-4 py-2 text-sm font-medium text-white hover:bg-bg-border disabled:opacity-50"
                >
                  <Pause size={16} /> Pause Trading
                </button>
              )}
              <button
                onClick={() => {
                  if (window.confirm("Emergency Stop blocks all new trading activity immediately. Existing positions keep being managed by sentiment exits and stop-loss/take-profit brackets - it does NOT sell them. Continue?")) {
                    setBotControl("pause", "Emergency Stop");
                  }
                }}
                disabled={actionPending || data.botControl.isPaused}
                className="flex items-center gap-2 rounded-lg bg-loss/15 px-4 py-2 text-sm font-medium text-loss hover:bg-loss/25 disabled:opacity-50"
              >
                <OctagonAlert size={16} /> Emergency Stop
              </button>
              <Link
                href="/decisions"
                className="flex items-center gap-2 rounded-lg bg-bg-panel2 px-4 py-2 text-sm font-medium text-white hover:bg-bg-border"
              >
                <Eye size={16} /> View Latest Decision
              </Link>
              <Link
                href="/portfolio"
                className="flex items-center gap-2 rounded-lg bg-bg-panel2 px-4 py-2 text-sm font-medium text-white hover:bg-bg-border"
              >
                <Briefcase size={16} /> Open Portfolio
              </Link>
              <Link
                href="/system-health"
                className="flex items-center gap-2 rounded-lg bg-bg-panel2 px-4 py-2 text-sm font-medium text-white hover:bg-bg-border"
              >
                <HeartPulse size={16} /> Run Health Check
              </Link>
            </div>
            <p className="mt-3 text-xs text-muted">
              Pause and Emergency Stop only block NEW trading activity - sentiment-driven sells and
              broker-side stop-loss/take-profit brackets keep managing any positions you already hold.
              Neither one sells anything for you.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
