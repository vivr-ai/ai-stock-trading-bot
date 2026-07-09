"use client";

import { useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import StatusBadge from "@/components/StatusBadge";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { Activity, Wallet, Banknote, TrendingUp, Layers, Repeat, Radio, Clock } from "lucide-react";

type StatusResponse = {
  botStatus: "running" | "stopped" | "error";
  lastHeartbeat: string | null;
  schedulerStatus: string | null;
  marketOpen: boolean | null;
  dryRun: boolean | null;
  portfolioValue: number | null;
  cash: number | null;
  equity: number | null;
  buyingPower: number | null;
  totalReturnPct: number | null;
  openPositionsCount: number;
  todaysPl: number;
  realizedToday: number;
  unrealizedNow: number;
  lastTrade: {
    ts: string;
    action: string;
    symbol: string;
    qty: number | null;
    price: number | null;
    dryRun: boolean;
    status: string | null;
  } | null;
  hasAnyData: boolean;
};

function fmtMoney(v: number | null | undefined) {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

function fmtPct(v: number | null | undefined) {
  if (v == null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function timeAgo(iso: string | null) {
  if (!iso) return "never";
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function HomePage() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/status");
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
    const id = setInterval(load, 60_000); // refresh every minute
    return () => clearInterval(id);
  }, []);

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
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Bot Status"
            value={<StatusBadge status={data.botStatus} />}
            sublabel={data.dryRun ? "Dry run (no real orders)" : "Live paper trading"}
            icon={<Activity size={15} />}
          />
          <StatCard
            label="Last Heartbeat"
            value={timeAgo(data.lastHeartbeat)}
            sublabel={data.lastHeartbeat ? new Date(data.lastHeartbeat).toLocaleString() : undefined}
            icon={<Clock size={15} />}
          />
          <StatCard
            label="Portfolio Value"
            value={fmtMoney(data.portfolioValue)}
            icon={<Wallet size={15} />}
          />
          <StatCard
            label="Cash Available"
            value={fmtMoney(data.cash)}
            icon={<Banknote size={15} />}
          />
          <StatCard
            label="Today's P/L"
            value={fmtMoney(data.todaysPl)}
            tone={data.todaysPl > 0 ? "gain" : data.todaysPl < 0 ? "loss" : "neutral"}
            sublabel={`Realized ${fmtMoney(data.realizedToday)} + unrealized ${fmtMoney(
              data.unrealizedNow
            )}`}
            icon={<TrendingUp size={15} />}
          />
          <StatCard
            label="Total Return"
            value={fmtPct(data.totalReturnPct)}
            tone={
              data.totalReturnPct == null
                ? "neutral"
                : data.totalReturnPct > 0
                ? "gain"
                : data.totalReturnPct < 0
                ? "loss"
                : "neutral"
            }
            sublabel="Since dashboard tracking began"
            icon={<TrendingUp size={15} />}
          />
          <StatCard
            label="Open Positions"
            value={data.openPositionsCount}
            icon={<Layers size={15} />}
          />
          <StatCard
            label="Last Executed Trade"
            value={
              data.lastTrade
                ? `${data.lastTrade.action.toUpperCase()} ${data.lastTrade.symbol}`
                : "—"
            }
            sublabel={
              data.lastTrade
                ? `${data.lastTrade.qty ?? ""} @ ${fmtMoney(data.lastTrade.price)} · ${timeAgo(
                    data.lastTrade.ts
                  )}`
                : "No trades yet"
            }
            icon={<Repeat size={15} />}
          />
          <StatCard
            label="Market Status"
            value={
              data.marketOpen == null ? "—" : data.marketOpen ? "Open" : "Closed"
            }
            tone={data.marketOpen ? "gain" : "neutral"}
            icon={<Radio size={15} />}
          />
          <StatCard
            label="Scheduler Status"
            value={data.schedulerStatus ?? "—"}
            icon={<Activity size={15} />}
          />
        </div>
      )}
    </div>
  );
}
