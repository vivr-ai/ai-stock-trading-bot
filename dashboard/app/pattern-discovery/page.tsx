"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { timeAgo } from "@/lib/format";
import {
  Search, ShieldAlert, CircleAlert, CheckCircle2, Info, ChevronDown, ChevronUp,
} from "lucide-react";

type FindingCategory =
  | "confidence_threshold" | "sector" | "holding_period" | "symbol_underperformance"
  | "stop_loss" | "take_profit" | "volatility" | "position_sizing"
  | "sentiment_reasoning" | "news_source";

type ConfidenceLevel = "insufficient" | "low" | "medium" | "high";

type Finding = {
  category: FindingCategory;
  title: string;
  description: string;
  sampleSize: number;
  baselineSampleSize: number | null;
  statisticalMethod: string;
  pValue: number | null;
  effectSize: number | null;
  meetsMinSample: boolean;
  isSignificant: boolean;
  confidenceLevel: ConfidenceLevel;
};

type PatternDiscoveryResponse = {
  totalTradesAnalysed: number;
  generatedAt: string;
  findings: Finding[];
};

const CATEGORY_LABEL: Record<FindingCategory, string> = {
  confidence_threshold: "Confidence Thresholds",
  sector: "Sector Performance",
  holding_period: "Holding Period",
  symbol_underperformance: "Underperforming Symbols",
  stop_loss: "Stop-Loss Exits",
  take_profit: "Take-Profit Exits",
  volatility: "Volatility / Market Regime",
  position_sizing: "Position Sizing",
  sentiment_reasoning: "Sentiment / AI Reasoning",
  news_source: "News Source",
};

const CONFIDENCE_STYLE: Record<ConfidenceLevel, string> = {
  insufficient: "bg-bg-panel2 text-muted",
  low: "bg-accent/10 text-accent/80",
  medium: "bg-accent/15 text-accent",
  high: "bg-gain/15 text-gain",
};

const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = {
  insufficient: "Insufficient sample",
  low: "Low confidence",
  medium: "Medium confidence",
  high: "High confidence",
};

function FindingCard({ finding }: { finding: Finding }) {
  const [expanded, setExpanded] = useState(false);
  const dimmed = !finding.meetsMinSample;

  return (
    <div className={`rounded-xl border border-bg-border bg-bg-panel p-5 ${dimmed ? "opacity-60" : ""}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${CONFIDENCE_STYLE[finding.confidenceLevel]}`}>
          {CONFIDENCE_LABEL[finding.confidenceLevel]}
        </span>
        {finding.isSignificant && (
          <span className="flex items-center gap-1 rounded-full bg-gain/15 px-2 py-0.5 text-xs font-medium text-gain">
            <CheckCircle2 size={12} /> Statistically significant
          </span>
        )}
        {finding.category === "symbol_underperformance" && (
          <span className="flex items-center gap-1 rounded-full bg-loss/15 px-2 py-0.5 text-xs font-medium text-loss">
            <ShieldAlert size={12} /> Underperforming
          </span>
        )}
        <span className="text-xs text-muted">n={finding.sampleSize}</span>
      </div>

      <h3 className="mb-1 text-sm font-semibold text-white">{finding.title}</h3>
      <p className="text-sm text-muted">{finding.description}</p>

      <button
        onClick={() => setExpanded((e) => !e)}
        className="mt-2 flex items-center gap-1 text-xs text-accent"
      >
        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {expanded ? "Hide method" : "Show method"}
      </button>

      {expanded && (
        <div className="mt-2 space-y-1 border-t border-bg-border pt-2 text-xs text-muted">
          <div><span className="text-white">Method: </span>{finding.statisticalMethod}</div>
          {finding.pValue != null && <div><span className="text-white">p-value: </span>{finding.pValue.toFixed(4)}</div>}
          {finding.effectSize != null && <div><span className="text-white">Effect size: </span>{finding.effectSize.toFixed(3)}</div>}
          {finding.baselineSampleSize != null && (
            <div><span className="text-white">Baseline sample: </span>{finding.baselineSampleSize}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function PatternDiscoveryPage() {
  const [data, setData] = useState<PatternDiscoveryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState<"all" | FindingCategory>("all");
  const [showInsufficient, setShowInsufficient] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const res = await fetch("/api/pattern-discovery");
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
  }, []);

  const findings = data?.findings ?? [];
  const categoriesPresent = Array.from(new Set(findings.map((f) => f.category)));
  const visible = findings.filter(
    (f) =>
      (categoryFilter === "all" || f.category === categoryFilter) &&
      (showInsufficient || f.meetsMinSample)
  );

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Pattern Discovery</h1>
        <p className="text-sm text-muted">
          Statistical patterns mined from ALL closed trades to date - never just a recent window, to
          avoid overfitting to recent results. Findings below a category&apos;s minimum sample size are
          marked &quot;insufficient sample&quot; and hidden by default rather than presented as conclusions.
          This page is descriptive only; it never changes trading behaviour. Use Recommendations to
          propose and approve any resulting strategy change.
        </p>
        {data && (
          <p className="mt-1 text-xs text-muted">
            {data.totalTradesAnalysed} closed trades analysed · generated {timeAgo(data.generatedAt)}
          </p>
        )}
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && findings.length > 0 && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-bg-border pb-3">
            <button
              onClick={() => setCategoryFilter("all")}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                categoryFilter === "all" ? "bg-accent/15 text-accent" : "text-muted hover:text-white"
              }`}
            >
              All
            </button>
            {categoriesPresent.map((cat) => (
              <button
                key={cat}
                onClick={() => setCategoryFilter(cat)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  categoryFilter === cat ? "bg-accent/15 text-accent" : "text-muted hover:text-white"
                }`}
              >
                {CATEGORY_LABEL[cat]}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
              <input
                type="checkbox"
                checked={showInsufficient}
                onChange={(e) => setShowInsufficient(e.target.checked)}
              />
              Show insufficient-sample findings
            </label>
          </div>

          {visible.length === 0 ? (
            <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
              <Info size={20} className="mx-auto mb-2 text-muted" />
              No findings meet the minimum sample size yet for this filter.
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {visible.map((f, i) => (
                <FindingCard key={`${f.category}-${i}`} finding={f} />
              ))}
            </div>
          )}
        </>
      )}

      {!loading && !error && findings.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
          <Search size={20} className="mx-auto mb-2 text-muted" />
          No closed trades yet - patterns will appear once the bot has closed positions.
        </div>
      )}

      {!loading && !error && findings.some((f) => f.meetsMinSample && !f.isSignificant && f.category !== "news_source") && (
        <div className="mt-6 flex items-start gap-2 rounded-xl border border-dashed border-bg-border p-4 text-xs text-muted">
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          Findings shown without the &quot;statistically significant&quot; tag are directional only - real,
          but not yet strong enough evidence to act on by themselves.
        </div>
      )}
    </div>
  );
}
