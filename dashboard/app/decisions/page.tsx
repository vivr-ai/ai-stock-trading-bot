"use client";

import { useEffect, useMemo, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { explainReason, decisionLabel } from "@/lib/explain";
import { Search, TrendingUp, TrendingDown, Minus } from "lucide-react";

type DecisionRow = {
  id: number;
  ts: string;
  symbol: string;
  decision: string;
  reason: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  headline_count: number | null;
  rationale: string | null;
  price: number | null;
  outcome: string | null;
};

type DecisionsResponse = {
  decisions: DecisionRow[];
  hasEverRun: boolean;
};

function DecisionBadge({ decision }: { decision: string }) {
  const label = decisionLabel(decision);
  if (label === "Buy") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gain/15 px-2.5 py-1 text-xs font-medium text-gain">
        <TrendingUp size={12} /> Buy
      </span>
    );
  }
  if (label === "Sell") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-loss/15 px-2.5 py-1 text-xs font-medium text-loss">
        <TrendingDown size={12} /> Sell
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-bg-panel2 px-2.5 py-1 text-xs font-medium text-muted">
      <Minus size={12} /> Hold
    </span>
  );
}

function ConfidenceBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="text-muted">—</span>;
  const tone = score > 0 ? "text-gain" : score < 0 ? "text-loss" : "text-muted";
  return <span className={`font-medium tabular-nums ${tone}`}>{score.toFixed(1)}</span>;
}

export default function DecisionLogPage() {
  const [data, setData] = useState<DecisionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [type, setType] = useState<"all" | "buy" | "sell" | "hold">("all");
  const [limit, setLimit] = useState(150);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (type !== "all") params.set("type", type);
    params.set("limit", String(limit));
    return params.toString();
  }, [search, type, limit]);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch(`/api/decisions?${queryString}`);
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
    const id = setTimeout(load, 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryString]);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">AI Decision Log</h1>
        <p className="text-sm text-muted">Every decision the bot has made, in plain English.</p>
      </div>

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search symbol or reason..."
            className="w-full rounded-lg border border-bg-border bg-bg-panel2 py-2 pl-9 pr-3 text-sm text-white outline-none focus:border-accent"
          />
        </div>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as "all" | "buy" | "sell" | "hold")}
          className="rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-sm text-white outline-none focus:border-accent"
        >
          <option value="all">All decisions</option>
          <option value="buy">Buys only</option>
          <option value="sell">Sells only</option>
          <option value="hold">Holds only</option>
        </select>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasEverRun && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No data yet. This fills in once the bot has run at least one cycle.
        </div>
      )}

      {!loading && !error && data && data.hasEverRun && data.decisions.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No decisions match these filters yet.
        </div>
      )}

      {!loading && !error && data && data.decisions.length > 0 && (
        <div className="space-y-3">
          {data.decisions.map((d) => (
            <div key={d.id} className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-3">
                  <span className="font-semibold text-white">{d.symbol}</span>
                  <DecisionBadge decision={d.decision} />
                  <span className="text-xs text-muted">
                    {new Date(d.ts).toLocaleString(undefined, {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-xs text-muted">
                  <span>
                    Confidence: <ConfidenceBadge score={d.sentiment_score} />
                  </span>
                  {d.headline_count != null && <span>{d.headline_count} headlines</span>}
                  {d.outcome && (
                    <span
                      className={
                        d.outcome.startsWith("Closed +")
                          ? "text-gain"
                          : d.outcome.startsWith("Closed -")
                          ? "text-loss"
                          : "text-muted"
                      }
                    >
                      {d.outcome}
                    </span>
                  )}
                </div>
              </div>
              {d.rationale && (
                <p className="mt-2 text-sm text-muted">
                  <span className="text-white">News summary: </span>
                  {d.rationale}
                </p>
              )}
              {d.reason && (
                <p className="mt-1 text-sm text-muted">
                  <span className="text-white">Why: </span>
                  {explainReason(d.reason)}
                </p>
              )}
            </div>
          ))}
          {data.decisions.length >= limit && (
            <button
              onClick={() => setLimit((l) => l + 150)}
              className="w-full rounded-lg border border-bg-border bg-bg-panel py-2 text-sm text-muted hover:text-white"
            >
              Load more
            </button>
          )}
        </div>
      )}
    </div>
  );
}
