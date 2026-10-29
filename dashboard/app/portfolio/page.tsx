"use client";

import { useEffect, useMemo, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { fmtMoney, fmtPct, fmtNumber, fmtDateTime, timeAgo, toneFor } from "@/lib/format";
import { nameFor } from "@/lib/symbolNames";
import { ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";

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
  trailing_peak_price: number | null;
};

type PositionsResponse = {
  positions: Position[];
  hasEverRun: boolean;
  updatedAt: string | null;
};

// Every column in the table is sortable by clicking its header - this is
// the set of keys a click can sort by, each mapped to a comparable value
// via sortValueFor() below (not always the same type as the Position field,
// e.g. entry_time becomes a numeric timestamp so it sorts chronologically
// rather than as a string).
type SortKey =
  | "symbol"
  | "entry_time"
  | "qty"
  | "avg_entry_price"
  | "current_price"
  | "stop_loss_price"
  | "trailing_peak_price"
  | "unrealized_pl"
  | "allocation_pct"
  | "ai_confidence"
  | "entry_reason";

type SortDir = "asc" | "desc";
type SortState = { key: SortKey; dir: SortDir };

function sortValueFor(p: Position, key: SortKey): string | number | null {
  switch (key) {
    case "symbol":
      return p.symbol.toLowerCase();
    case "entry_time":
      return p.entry_time ? new Date(p.entry_time).getTime() : null;
    case "qty":
      return p.qty;
    case "avg_entry_price":
      return p.avg_entry_price;
    case "current_price":
      return p.current_price;
    case "stop_loss_price":
      return p.stop_loss_price;
    case "trailing_peak_price":
      return p.trailing_peak_price;
    case "unrealized_pl":
      return p.unrealized_pl;
    case "allocation_pct":
      return p.allocation_pct;
    case "ai_confidence":
      return p.ai_confidence;
    case "entry_reason":
      return p.entry_reason ? p.entry_reason.toLowerCase() : null;
  }
}

// Rows with no value for the active sort column always sink to the bottom,
// in both directions - e.g. a position with no trailing peak yet shouldn't
// jump to the top just because you clicked "descending".
function compareSortValues(a: string | number | null, b: string | number | null, dir: SortDir) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const result =
    typeof a === "string" && typeof b === "string" ? a.localeCompare(b) : (a as number) - (b as number);
  return dir === "asc" ? result : -result;
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState | null;
  onSort: (key: SortKey) => void;
}) {
  const active = sort?.key === sortKey;
  const Icon = active ? (sort!.dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <th className="px-4 py-3 font-medium">
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`flex items-center gap-1 whitespace-nowrap hover:text-white ${
          active ? "text-white" : ""
        }`}
      >
        {label}
        <Icon size={12} className={active ? "opacity-90" : "opacity-40"} />
      </button>
    </th>
  );
}

function ConfidenceBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-muted">—</span>;
  const tone = score > 0 ? "text-gain" : score < 0 ? "text-loss" : "text-muted";
  return <span className={`font-medium tabular-nums ${tone}`}>{score.toFixed(1)}</span>;
}

/** Software trailing-stop layer (RISK_TRAILING_STOP_ENABLED) - see
 * bot/trading/strategy.py's _maybe_trailing_stop_exit. A peak is only
 * recorded once that feature is on AND has run at least one cycle for this
 * symbol, so "—" here just means "not applicable yet," not an error. */
function TrailingStopCell({ peak, current }: { peak: number | null; current: number }) {
  if (peak == null) return <span className="text-muted">—</span>;
  const pullbackPct = peak > 0 ? ((peak - current) / peak) * 100 : 0;
  return (
    <span className="tabular-nums">
      {fmtMoney(peak)}{" "}
      <span className={`text-xs ${pullbackPct > 0 ? "text-loss/90" : "text-muted"}`}>
        (-{pullbackPct.toFixed(1)}% off peak)
      </span>
    </span>
  );
}

export default function PortfolioPage() {
  const [data, setData] = useState<PositionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortState | null>(null);

  function handleSort(key: SortKey) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      return { key, dir: prev.dir === "asc" ? "desc" : "asc" };
    });
  }

  const sortedPositions = useMemo(() => {
    const positions = data?.positions ?? [];
    if (!sort) return positions;
    return [...positions].sort((a, b) =>
      compareSortValues(sortValueFor(a, sort.key), sortValueFor(b, sort.key), sort.dir)
    );
  }, [data, sort]);

  const totals = useMemo(() => {
    const positions = data?.positions ?? [];
    const unrealized = positions.reduce((sum, p) => sum + Number(p.unrealized_pl || 0), 0);
    const costBasis = positions.reduce((sum, p) => sum + Number(p.qty || 0) * Number(p.avg_entry_price || 0), 0);
    const allocation = positions.reduce((sum, p) => sum + Number(p.allocation_pct || 0), 0);
    return { unrealized, pct: costBasis > 0 ? (unrealized / costBasis) * 100 : null, allocation };
  }, [data]);

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
          <table className="w-full min-w-[1300px] text-left text-sm">
            <thead>
              <tr className="border-b border-bg-border text-xs uppercase tracking-wide text-muted">
                <SortableHeader label="Symbol" sortKey="symbol" sort={sort} onSort={handleSort} />
                <SortableHeader label="Bought" sortKey="entry_time" sort={sort} onSort={handleSort} />
                <SortableHeader label="Qty" sortKey="qty" sort={sort} onSort={handleSort} />
                <SortableHeader label="Avg Entry" sortKey="avg_entry_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="Current Price" sortKey="current_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="Stop Loss" sortKey="stop_loss_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="Trailing Peak" sortKey="trailing_peak_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="Unrealized P/L" sortKey="unrealized_pl" sort={sort} onSort={handleSort} />
                <SortableHeader label="Allocation" sortKey="allocation_pct" sort={sort} onSort={handleSort} />
                <SortableHeader label="AI Confidence" sortKey="ai_confidence" sort={sort} onSort={handleSort} />
                <SortableHeader label="Entry Reason" sortKey="entry_reason" sort={sort} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {sortedPositions.map((p) => {
                const tone = toneFor(p.unrealized_pl);
                return (
                  <tr key={p.symbol} className="border-b border-bg-border last:border-0">
                    <td className="px-4 py-3">
                      <div className="font-semibold text-white">{p.symbol}</div>
                      {nameFor(p.symbol) && (
                        <div className="text-xs text-muted">{nameFor(p.symbol)}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-muted">
                      {fmtDateTime(p.entry_time)}
                    </td>
                    <td className="px-4 py-3 tabular-nums">{fmtNumber(p.qty)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(p.avg_entry_price)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(p.current_price)}</td>
                    <td
                      className="px-4 py-3 tabular-nums text-loss/90"
                      title={
                        p.take_profit_price != null
                          ? p.trailing_peak_price != null
                            ? `Take profit (backstop): ${fmtMoney(p.take_profit_price)} - the trailing stop, not this, is the primary exit while it's tracking a peak`
                            : `Take profit: ${fmtMoney(p.take_profit_price)}`
                          : undefined
                      }
                    >
                      {p.stop_loss_price != null ? fmtMoney(p.stop_loss_price) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <TrailingStopCell peak={p.trailing_peak_price} current={p.current_price} />
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
            <tfoot>
              <tr className="border-t border-bg-border bg-bg-panel2 font-semibold">
                <td className="px-4 py-3 text-white">Total ({data.positions.length})</td>
                <td colSpan={6}></td>
                <td
                  className={`px-4 py-3 tabular-nums ${
                    toneFor(totals.unrealized) === "gain"
                      ? "text-gain"
                      : toneFor(totals.unrealized) === "loss"
                      ? "text-loss"
                      : ""
                  }`}
                >
                  {fmtMoney(totals.unrealized)}{" "}
                  {totals.pct != null && <span className="text-xs opacity-80">({fmtPct(totals.pct)})</span>}
                </td>
                <td className="px-4 py-3 tabular-nums">{totals.allocation.toFixed(1)}%</td>
                <td colSpan={2}></td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </div>
  );
}
