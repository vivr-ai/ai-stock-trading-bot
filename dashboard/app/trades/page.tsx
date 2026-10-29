"use client";

import { useEffect, useMemo, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { fmtMoney, fmtPct, fmtDateTime, toneFor } from "@/lib/format";
import { nameFor } from "@/lib/symbolNames";
import { Search, ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";

type ClosedTrade = {
  id: number;
  ts: string;
  symbol: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string | null;
  entry_time: string | null;
  buy_reason: string | null;
  news_summary: string | null;
};

type TradesResponse = {
  trades: ClosedTrade[];
  hasEverRun: boolean;
};

type SortKey = "symbol" | "entry_time" | "ts" | "entry_price" | "exit_price" | "pnl" | "buy_reason" | "news_summary";
type SortDir = "asc" | "desc";
type SortState = { key: SortKey; dir: SortDir };

function sortValueFor(t: ClosedTrade, key: SortKey): string | number | null {
  switch (key) {
    case "symbol":
      return t.symbol.toLowerCase();
    case "entry_time":
      return t.entry_time ? new Date(t.entry_time).getTime() : null;
    case "ts":
      return t.ts ? new Date(t.ts).getTime() : null;
    case "entry_price":
      return Number(t.entry_price);
    case "exit_price":
      return Number(t.exit_price);
    case "pnl":
      return Number(t.pnl);
    case "buy_reason":
      return t.buy_reason ? t.buy_reason.toLowerCase() : null;
    case "news_summary":
      return t.news_summary ? t.news_summary.toLowerCase() : null;
  }
}

// Rows with no value for the active sort column always sink to the bottom.
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
        className={`flex items-center gap-1 whitespace-nowrap hover:text-white ${active ? "text-white" : ""}`}
      >
        {label}
        <Icon size={12} className={active ? "opacity-90" : "opacity-40"} />
      </button>
    </th>
  );
}

export default function TradeHistoryPage() {
  const [data, setData] = useState<TradesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [outcome, setOutcome] = useState<"all" | "win" | "loss">("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [sort, setSort] = useState<SortState | null>(null);

  function handleSort(key: SortKey) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: "asc" };
      return { key, dir: prev.dir === "asc" ? "desc" : "asc" };
    });
  }

  const sortedTrades = useMemo(() => {
    const trades = data?.trades ?? [];
    if (!sort) return trades;
    return [...trades].sort((a, b) =>
      compareSortValues(sortValueFor(a, sort.key), sortValueFor(b, sort.key), sort.dir)
    );
  }, [data, sort]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (outcome !== "all") params.set("outcome", outcome);
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    return params.toString();
  }, [search, outcome, from, to]);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`/api/trades?${queryString}`);
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
    const id = setTimeout(load, 300); // debounce search/filter changes
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryString]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Trade History</h1>
        <p className="text-sm text-muted">Every completed round-trip trade.</p>
      </div>

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search symbol, reason, or news..."
            className="w-full rounded-lg border border-bg-border bg-bg-panel2 py-2 pl-9 pr-3 text-sm text-white outline-none focus:border-accent"
          />
        </div>
        <select
          value={outcome}
          onChange={(e) => setOutcome(e.target.value as "all" | "win" | "loss")}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        >
          <option value="all">All outcomes</option>
          <option value="win">Wins only</option>
          <option value="loss">Losses only</option>
        </select>
        <input
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        />
        <input
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        />
      </div>

      {loading && <LoadingSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && data.trades.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No completed trades match these filters yet.
        </div>
      )}

      {!loading && !error && data && data.trades.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-bg-border bg-bg-panel">
          <table className="w-full min-w-[1000px] text-left text-sm">
            <thead>
              <tr className="border-b border-bg-border text-xs uppercase tracking-wide text-muted">
                <SortableHeader label="Symbol" sortKey="symbol" sort={sort} onSort={handleSort} />
                <SortableHeader label="Buy Time" sortKey="entry_time" sort={sort} onSort={handleSort} />
                <SortableHeader label="Sell Time" sortKey="ts" sort={sort} onSort={handleSort} />
                <SortableHeader label="Entry" sortKey="entry_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="Exit" sortKey="exit_price" sort={sort} onSort={handleSort} />
                <SortableHeader label="P/L" sortKey="pnl" sort={sort} onSort={handleSort} />
                <SortableHeader label="AI Explanation" sortKey="buy_reason" sort={sort} onSort={handleSort} />
                <SortableHeader label="News That Triggered It" sortKey="news_summary" sort={sort} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {sortedTrades.map((t) => {
                const tone = toneFor(t.pnl);
                return (
                  <tr key={t.id} className="border-b border-bg-border align-top last:border-0">
                    <td className="px-4 py-3">
                      <div className="font-semibold text-white">{t.symbol}</div>
                      {nameFor(t.symbol) && (
                        <div className="text-xs text-muted">{nameFor(t.symbol)}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap text-muted">{fmtDateTime(t.entry_time)}</td>
                    <td className="px-4 py-3 whitespace-nowrap text-muted">{fmtDateTime(t.ts)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(t.entry_price)}</td>
                    <td className="px-4 py-3 tabular-nums">{fmtMoney(t.exit_price)}</td>
                    <td
                      className={`px-4 py-3 tabular-nums ${
                        tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : ""
                      }`}
                    >
                      {fmtMoney(t.pnl)} <span className="text-xs opacity-80">({fmtPct(t.pnl_pct)})</span>
                    </td>
                    <td className="max-w-[220px] px-4 py-3 text-muted">
                      <div title={t.buy_reason ?? ""} className="line-clamp-2">
                        {t.buy_reason ?? "—"}
                      </div>
                      {t.exit_reason && (
                        <div className="mt-1 text-xs opacity-70">Exited: {t.exit_reason}</div>
                      )}
                    </td>
                    <td className="max-w-[240px] px-4 py-3 text-muted">
                      <div title={t.news_summary ?? ""} className="line-clamp-2">
                        {t.news_summary ?? "—"}
                      </div>
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
