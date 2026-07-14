"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import HealthTrendChart from "@/components/HealthTrendChart";
import { HeartPulse, Info } from "lucide-react";

type HealthComponent = {
  key: string;
  label: string;
  score: number | null;
  weight: number;
  meetsMinSample: boolean;
  description: string;
};

type ConfidenceLevel = "insufficient" | "low" | "medium" | "high";

type StrategyHealth = {
  overallScore: number | null;
  confidenceLevel: ConfidenceLevel;
  totalTrades: number;
  components: HealthComponent[];
};

type HistoryPoint = { computed_at: string; overall_score: number | null; confidence_level: string };

type StrategyHealthResponse = {
  current: StrategyHealth;
  activeStrategyVersion: string;
  history: HistoryPoint[];
};

const CONFIDENCE_LABEL: Record<ConfidenceLevel, string> = {
  insufficient: "No data yet", low: "Low (small sample)", medium: "Medium", high: "High",
};

function scoreTone(score: number | null): { text: string; ring: string } {
  if (score == null) return { text: "text-muted", ring: "border-bg-border" };
  if (score >= 75) return { text: "text-gain", ring: "border-gain/40" };
  if (score >= 50) return { text: "text-accent", ring: "border-accent/40" };
  return { text: "text-loss", ring: "border-loss/40" };
}

function ComponentRow({ c }: { c: HealthComponent }) {
  const tone = scoreTone(c.score);
  return (
    <div className={`rounded-xl border border-bg-border bg-bg-panel p-4 ${!c.meetsMinSample ? "opacity-70" : ""}`}>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-medium text-white">{c.label}</span>
        <span className={`text-lg font-semibold tabular-nums ${tone.text}`}>
          {c.score != null ? c.score.toFixed(0) : "—"}
        </span>
      </div>
      <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-bg-panel2">
        <div
          className={`h-full rounded-full ${c.score != null && c.score >= 75 ? "bg-gain" : c.score != null && c.score >= 50 ? "bg-accent" : "bg-loss"}`}
          style={{ width: `${c.score ?? 0}%` }}
        />
      </div>
      <p className="text-xs text-muted">{c.description}</p>
      {!c.meetsMinSample && (
        <p className="mt-1 text-xs text-accent/80">Insufficient sample - treat as provisional.</p>
      )}
      <p className="mt-1 text-xs text-muted">Weight: {c.weight}%</p>
    </div>
  );
}

export default function StrategyHealthPage() {
  const [data, setData] = useState<StrategyHealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/strategy-health");
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

  const health = data?.current;
  const tone = scoreTone(health?.overallScore ?? null);

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Strategy Health</h1>
        <p className="text-sm text-muted">
          A single 0-100 composite summarising win rate, risk-adjusted return, drawdown control,
          equity curve stability, consistency over time, market regime adaptation, and historical
          trend. It doesn&apos;t replace Performance Analytics or Pattern Discovery - it summarises them.
          Purely descriptive; never changes trading behaviour.
        </p>
      </div>

      {loading && <LoadingSkeleton rows={4} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && health && health.totalTrades === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
          <HeartPulse size={20} className="mx-auto mb-2 text-muted" />
          No closed trades yet - a health score fills in once the bot has closed at least one position.
        </div>
      )}

      {!loading && !error && health && health.totalTrades > 0 && (
        <div className="space-y-6">
          <div className="flex flex-col items-center gap-3 rounded-xl border border-bg-border bg-bg-panel p-6 sm:flex-row sm:justify-between">
            <div className="flex items-center gap-4">
              <div className={`flex h-24 w-24 items-center justify-center rounded-full border-4 ${tone.ring}`}>
                <span className={`text-3xl font-bold tabular-nums ${tone.text}`}>
                  {health.overallScore != null ? health.overallScore.toFixed(0) : "—"}
                </span>
              </div>
              <div>
                <div className="text-sm font-semibold text-white">Overall Health Score</div>
                <div className="text-xs text-muted">
                  {health.totalTrades} closed trades analysed · active strategy {data?.activeStrategyVersion}
                </div>
                <div className="text-xs text-muted">
                  Confidence: {CONFIDENCE_LABEL[health.confidenceLevel]}
                </div>
              </div>
            </div>
            {health.confidenceLevel === "low" && (
              <div className="flex items-start gap-2 rounded-lg border border-dashed border-bg-border p-3 text-xs text-muted sm:max-w-xs">
                <Info size={14} className="mt-0.5 shrink-0" />
                Based on a small number of trades so far - treat this score as provisional until more
                history accumulates.
              </div>
            )}
          </div>

          <div>
            <h2 className="mb-3 text-sm font-semibold text-white">Score History</h2>
            <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
              <HealthTrendChart data={data?.history ?? []} />
            </div>
          </div>

          <div>
            <h2 className="mb-3 text-sm font-semibold text-white">Components</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {health.components.map((c) => (
                <ComponentRow key={c.key} c={c} />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
