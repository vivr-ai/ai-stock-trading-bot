"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { fmtMoney, fmtPct, fmtNumber, timeAgo, toneFor } from "@/lib/format";

type Position = {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  allocation_pct: number | null;
  ai_confidence: number | null;
  entry_reason: string | null;
  entry_time: string | null;
  updated_at: string;
  stop_loss_price: number | null;
  take_profit_price: number | null;
};

type PositionsResponse = {
  positions: Position[];
  hasEverRun: boolean;
  updatedAt: string | null;
};

function ConfidenceBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-muted">—</span>;
  const tone = score > 0 ? "text-gain" : score < 0 ? "text-loss" : "text-muted";
  return <span className={`font-medium tabular-nums ${tone}`}>{score.toFixed(1)}</span>;
}

export default function PortfolioPage() {
  const [data, setData] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/positions");
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
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Portfolio</h1>
          <p className="text-sm text-muted">All currently open positions.</p>
        </div>
        {data?.updatedAt && (
          <div className="text-xs text-muted">Updated {timeAgo(data.updatedAt)}</div>
        )}
      </div>

      {loading && <LoadingSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && data.positions.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No open positions right now — the bot is currently all in cash.
        </div>
      )}

      {!loading && !error && data && data.positions.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-bg-border bg-bg-panel">
          <table className="w-full min-w-[1000px] text-left text-sm">
            <thead>
              <tr className="border-b border-bg-border text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-3 font-medium">Symbol</th>
                <th className="px-4 py-3 font-medium">Qty</th>
                <th className="px-4 py-3 font-medium">Avg Entry</th>
                <th className="px-4 py-3 font-medium">Current Price</th>
                <th className="px-4 py-3 font-medium">Stop Loss</th>
                <th className="px-4 py-3 font-medium">Unrealized P/L</th>
                <th className="px-4 py-3 font-medium">Allocation</th>
                <th className="px-4 py-3 font-medium">AI Confidence</th>
                <th className="px-4 py-3 font-medium">Entry Reason</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => {
                const tone = toneFor(p.unrealized_pl);
                return (
                  <tr key={p.symbol} className="border-b border-bg-border last:border-0">
                    <td className="px-4 py-3 font-semibold text-white">{p.symbol}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtNumber(p.qty)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(p.avg_entry_price)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(p.current_price)}</td>
                    <td className="px-4 py-3 tabular-nums text-loss/90" title={p.take_profit_price != null ? `Take profit: ${fmtMoney(p.take_profit_price)}` : undefined}>
                      {p.stop_loss_price != null ? fmtMoney(p.stop_loss_price) : "—"}
                    </td>
                    <td
                      className={`px-4 py-3 tabular-nums ${
                        tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : ""
                      }`}
                    >
                      {fmtMoney(p.unrealized_pl)}{" "}
                      <span className="text-xs opacity-80">
                        ({fmtPct(p.unrealized_plpc)})
                      </span>
                    </td>
                    <td className="px-4 py-3 tabular-nums">
                      {p.allocation_pct != null ? `${p.allocation_pct.toFixed(1)}%` : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <ConfidenceBadge score={p.ai_confidence} />
                    </td>
                    <td className="max-w-xs px-4 py-3 text-muted" title={p.entry_reason ?? ""}>
                      <span className="line-clamp-2">{p.entry_reason ?? "—"}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
