"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import StatCard from "@/components/StatCard";
import { fmtMoney, fmtPct } from "@/lib/format";
import { AlertTriangle, AlertCircle, Info, ShieldAlert } from "lucide-react";

type SectorExposure = {
  sector: string;
  label: string;
  count: number;
  marketValue: number;
  pctOfPortfolio: number | null;
  symbols: string[];
  atCap: boolean;
};

type Alert = { severity: "critical" | "warning" | "info"; message: string };

type RiskConfig = {
  maxPositionPct: number;
  maxOpenPositions: number;
  maxTotalExposurePct: number;
  maxNewPositionsPerCycle: number;
  maxPositionsPerSector: number;
  reentryCooldownHours: number;
  stopLossPct: number;
  takeProfitPct: number;
  dailyLossLimitPct: number;
};

type RiskResponse = {
  hasEverRun: boolean;
  portfolioValue: number | null;
  cash: number | null;
  cashPct: number | null;
  totalExposurePct: number | null;
  openPositionsCount: number;
  largestPosition: { symbol: string; allocationPct: number | null } | null;
  dailyPnlPct: number | null;
  dailyLossBreached: boolean;
  sectorExposure: SectorExposure[];
  alerts: Alert[];
  config: RiskConfig;
};

function Meter({
  label,
  current,
  cap,
  suffix = "%",
  sublabel,
  danger,
}: {
  label: string;
  current: number | null;
  cap: number;
  suffix?: string;
  sublabel?: string;
  danger?: boolean;
}) {
  const pct = current != null ? Math.min((current / cap) * 100, 100) : 0;
  const over = current != null && current > cap;
  const barColor = danger || over ? "bg-loss" : pct > 80 ? "bg-accent" : "bg-gain";
  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-muted">{label}</span>
        <span className={`text-sm font-semibold tabular-nums ${over ? "text-loss" : "text-white"}`}>
          {current != null ? `${current.toFixed(1)}${suffix}` : "—"}
          <span className="ml-1 text-xs font-normal text-muted">/ {cap}{suffix}</span>
        </span>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-bg-panel2">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
      {sublabel && <div className="mt-1.5 text-xs text-muted">{sublabel}</div>}
    </div>
  );
}

function AlertRow({ alert }: { alert: Alert }) {
  const styles = {
    critical: { icon: ShieldAlert, cls: "border-loss/40 bg-loss/10 text-loss" },
    warning: { icon: AlertTriangle, cls: "border-accent/40 bg-accent/10 text-accent" },
    info: { icon: Info, cls: "border-bg-border bg-bg-panel2 text-muted" },
  } as const;
  const { icon: Icon, cls } = styles[alert.severity];
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border p-3 text-sm ${cls}`}>
      <Icon size={16} className="mt-0.5 shrink-0" />
      <span>{alert.message}</span>
    </div>
  );
}

export default function RiskDashboardPage() {
  const [data, setData] = useState<RiskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/risk");
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

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Risk Dashboard</h1>
        <p className="text-sm text-muted">How close the current book is to each of the bot&apos;s risk limits.</p>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && (
        <div className="space-y-5">
          {data.alerts.length > 0 && (
            <div className="space-y-2">
              {data.alerts.map((a, i) => (
                <AlertRow key={i} alert={a} />
              ))}
            </div>
          )}
          {data.alerts.length === 0 && (
            <div className="flex items-center gap-2.5 rounded-lg border border-gain/30 bg-gain/5 p-3 text-sm text-gain">
              <AlertCircle size={16} className="shrink-0" />
              No active risk alerts — the book is within all configured limits.
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Cash" value={fmtMoney(data.cash)} sublabel={data.cashPct != null ? `${data.cashPct.toFixed(1)}% of portfolio` : undefined} />
            <StatCard
              label="Open positions"
              value={`${data.openPositionsCount} / ${data.config.maxOpenPositions}`}
              tone={data.openPositionsCount >= data.config.maxOpenPositions ? "loss" : "neutral"}
            />
            <StatCard
              label="Largest position"
              value={data.largestPosition ? `${data.largestPosition.symbol}` : "—"}
              sublabel={
                data.largestPosition?.allocationPct != null
                  ? `${data.largestPosition.allocationPct.toFixed(1)}% of portfolio`
                  : undefined
              }
            />
            <StatCard
              label="Today's P/L"
              value={fmtPct(data.dailyPnlPct)}
              tone={data.dailyLossBreached ? "loss" : data.dailyPnlPct != null && data.dailyPnlPct > 0 ? "gain" : "neutral"}
              sublabel={data.dailyLossBreached ? "Daily loss limit breached" : undefined}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Meter
              label="Total portfolio exposure"
              current={data.totalExposurePct}
              cap={data.config.maxTotalExposurePct}
              sublabel="Share of the portfolio currently invested in stocks, vs. the max the bot will deploy at once."
            />
            <Meter
              label="Largest single position"
              current={data.largestPosition?.allocationPct ?? null}
              cap={data.config.maxPositionPct}
              sublabel="Biggest position as a % of the portfolio, vs. the per-position cap set at entry."
            />
            <Meter
              label="Open position count"
              current={data.openPositionsCount}
              cap={data.config.maxOpenPositions}
              suffix=""
              sublabel="Number of stocks currently held, vs. the max the bot will hold at once."
            />
            <Meter
              label="Daily loss"
              current={data.dailyPnlPct != null ? Math.max(-data.dailyPnlPct, 0) : null}
              cap={data.config.dailyLossLimitPct}
              danger={data.dailyLossBreached}
              sublabel="Today's drawdown so far, vs. the limit that pauses new buys for the rest of the day."
            />
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <h2 className="mb-3 text-base font-semibold text-white">Sector exposure</h2>
            {data.sectorExposure.length === 0 ? (
              <p className="text-sm text-muted">No open positions right now — the bot is currently all in cash.</p>
            ) : (
              <div className="space-y-3">
                {data.sectorExposure.map((s) => (
                  <div key={s.sector}>
                    <div className="mb-1 flex items-baseline justify-between text-sm">
                      <span className="text-white">
                        {s.label}{" "}
                        <span className="text-xs text-muted">({s.symbols.join(", ")})</span>
                      </span>
                      <span className={`tabular-nums ${s.atCap ? "text-loss" : "text-muted"}`}>
                        {s.count} / {data.config.maxPositionsPerSector} positions
                        {s.pctOfPortfolio != null ? ` · ${s.pctOfPortfolio.toFixed(1)}% of portfolio` : ""}
                      </span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-panel2">
                      <div
                        className={`h-full rounded-full ${s.atCap ? "bg-loss" : "bg-accent"}`}
                        style={{ width: `${Math.min((s.count / data.config.maxPositionsPerSector) * 100, 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
            <h2 className="mb-3 text-base font-semibold text-white">Configured risk limits</h2>
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm text-muted sm:grid-cols-3">
              <div>Max per position: <span className="text-white">{data.config.maxPositionPct}%</span></div>
              <div>Max total exposure: <span className="text-white">{data.config.maxTotalExposurePct}%</span></div>
              <div>Max open positions: <span className="text-white">{data.config.maxOpenPositions}</span></div>
              <div>Max new positions/cycle: <span className="text-white">{data.config.maxNewPositionsPerCycle}</span></div>
              <div>Max positions/sector: <span className="text-white">{data.config.maxPositionsPerSector}</span></div>
              <div>Re-entry cooldown: <span className="text-white">{data.config.reentryCooldownHours}h</span></div>
              <div>Stop-loss: <span className="text-white">-{data.config.stopLossPct}%</span></div>
              <div>Take-profit: <span className="text-white">+{data.config.takeProfitPct}%</span></div>
              <div>Daily loss limit: <span className="text-white">-{data.config.dailyLossLimitPct}%</span></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
