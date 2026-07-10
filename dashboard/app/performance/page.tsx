"use client";

import { useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import EquityCurveChart from "@/components/EquityCurveChart";
import ReturnsBarChart from "@/components/ReturnsBarChart";
import { fmtMoney, fmtPct, toneFor } from "@/lib/format";
import { Percent, TrendingUp, TrendingDown, ArrowDownRight, Gauge, ListChecks } from "lucide-react";

type PerformanceResponse = {
  hasAnyData: boolean;
  equityCurve: { ts: string; value: number }[];
  dailyReturns: { label: string; pct: number }[];
  monthlyReturns: { label: string; pct: number }[];
  winRatePct: number | null;
  avgGain: number | null;
  avgLoss: number | null;
  maxDrawdownPct: number | null;
  sharpeRatio: number | null;
  totalTrades: number;
};

export default function PerformancePage() {
  const [data, setData] = useState<PerformanceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/performance");
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
    const id = setInterval(load, 120_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Performance</h1>
        <p className="text-sm text-muted">How the bot has actually performed over time.</p>
      </div>

      {loading && <LoadingSkeleton rows={6} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasAnyData && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasAnyData && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard
              label="Win Rate"
              value={data.winRatePct != null ? `${data.winRatePct.toFixed(1)}%` : "—"}
              icon={<Percent size={15} />}
            />
            <StatCard
              label="Average Gain"
              value={fmtMoney(data.avgGain)}
              tone="gain"
              icon={<TrendingUp size={15} />}
            />
            <StatCard
              label="Average Loss"
              value={fmtMoney(data.avgLoss)}
              tone="loss"
              icon={<TrendingDown size={15} />}
            />
            <StatCard
              label="Max Drawdown"
              value={data.maxDrawdownPct != null ? `${data.maxDrawdownPct.toFixed(2)}%` : "—"}
              tone={data.maxDrawdownPct ? "loss" : "neutral"}
              icon={<ArrowDownRight size={15} />}
            />
            <StatCard
              label="Sharpe Ratio"
              value={data.sharpeRatio != null ? data.sharpeRatio.toFixed(2) : "—"}
              tone={toneFor(data.sharpeRatio)}
              icon={<Gauge size={15} />}
            />
            <StatCard
              label="Total Trades"
              value={data.totalTrades}
              icon={<ListChecks size={15} />}
            />
          </div>

          <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
            <h2 className="mb-2 text-sm font-medium text-white">Equity Curve</h2>
            {data.equityCurve.length > 1 ? (
              <EquityCurveChart data={data.equityCurve} />
            ) : (
              <div className="py-10 text-center text-sm text-muted">
                Not enough history yet to draw a curve.
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <h2 className="mb-2 text-sm font-medium text-white">Daily Returns</h2>
              {data.dailyReturns.length > 0 ? (
                <ReturnsBarChart data={data.dailyReturns} />
              ) : (
                <div className="py-10 text-center text-sm text-muted">Not enough history yet.</div>
              )}
            </div>
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <h2 className="mb-2 text-sm font-medium text-white">Monthly Returns</h2>
              {data.monthlyReturns.length > 0 ? (
                <ReturnsBarChart data={data.monthlyReturns} />
              ) : (
                <div className="py-10 text-center text-sm text-muted">Not enough history yet.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
