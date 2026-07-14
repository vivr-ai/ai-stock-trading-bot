"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { timeAgo } from "@/lib/format";
import { FileClock, Sparkles, Loader2, ChevronDown, ChevronUp, Send } from "lucide-react";

type MonthlyReport = {
  id: number;
  generated_at: string;
  period_start: string;
  period_end: string;
  model_used: string;
  total_trades: number;
  strategy_health_score: number | null;
  overall_performance: Record<string, unknown> | null;
  lessons_learned: string | null;
  emerging_patterns: string | null;
  potential_optimizations: string | null;
  market_observations: string | null;
  recommended_improvements: string | null;
  telegram_summary: string | null;
  sent_via_telegram: boolean;
};

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function ReportCard({ report }: { report: MonthlyReport }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-white">
          {fmtDate(report.period_start)} – {fmtDate(report.period_end)}
        </span>
        <span className="text-xs text-muted">· {report.total_trades} trades</span>
        {report.strategy_health_score != null && (
          <span className="text-xs text-muted">· health {Number(report.strategy_health_score).toFixed(0)}</span>
        )}
        <span className="text-xs text-muted">· {report.model_used}</span>
        <span className="text-xs text-muted">· generated {timeAgo(report.generated_at)}</span>
        {report.sent_via_telegram && (
          <span className="flex items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">
            <Send size={11} /> sent
          </span>
        )}
      </div>

      {report.telegram_summary && <p className="mb-2 text-sm text-white">{report.telegram_summary}</p>}

      <button onClick={() => setExpanded((e) => !e)} className="flex items-center gap-1 text-xs text-accent">
        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {expanded ? "Hide full report" : "Show full report"}
      </button>

      {expanded && (
        <div className="mt-3 space-y-3 border-t border-bg-border pt-3 text-xs text-muted">
          {report.lessons_learned && (
            <div><span className="text-sm font-medium text-white">Lessons Learned</span><p className="mt-1">{report.lessons_learned}</p></div>
          )}
          {report.emerging_patterns && (
            <div><span className="text-sm font-medium text-white">Emerging Patterns</span><p className="mt-1">{report.emerging_patterns}</p></div>
          )}
          {report.potential_optimizations && (
            <div><span className="text-sm font-medium text-white">Potential Optimisations</span><p className="mt-1">{report.potential_optimizations}</p></div>
          )}
          {report.market_observations && (
            <div><span className="text-sm font-medium text-white">Market Observations</span><p className="mt-1">{report.market_observations}</p></div>
          )}
          {report.recommended_improvements && (
            <div>
              <span className="text-sm font-medium text-white">Recommended Improvements</span>
              <p className="mt-1">{report.recommended_improvements}</p>
              <p className="mt-1 text-accent/80">
                Advisory only - none of this takes effect until it goes through Recommendations approval and an explicit strategy version deploy.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function MonthlyReportPage() {
  const [reports, setReports] = useState<MonthlyReport[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [model, setModel] = useState<"haiku" | "sonnet">("haiku");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  async function load() {
    try {
      const res = await fetch("/api/monthly-report");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      const json = await res.json();
      setReports(json.reports);
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

  async function generate() {
    setGenerating(true);
    setGenError(null);
    try {
      const res = await fetch("/api/monthly-report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, periodDays: 30 }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      await load();
    } catch (err) {
      setGenError(err instanceof Error ? err.message : "Failed to generate report");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Monthly Research Report</h1>
        <p className="text-sm text-muted">
          Combines Performance Analytics, Pattern Discovery, and Strategy Health into one plain-English
          rollup. Descriptive only - never changes trading behaviour. Runs automatically once a month if
          the bot is configured with DASHBOARD_INTERNAL_URL / DASHBOARD_INTERNAL_API_KEY (see README);
          otherwise generate one on demand below.
        </p>
      </div>

      <div className="mb-6 rounded-xl border border-bg-border bg-bg-panel p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white">
          <FileClock size={16} /> Generate a report now
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex overflow-hidden rounded-lg border border-bg-border text-xs">
            <button
              onClick={() => setModel("haiku")}
              className={`px-3 py-1.5 font-medium ${model === "haiku" ? "bg-accent/15 text-accent" : "text-muted hover:text-white"}`}
            >
              Haiku (default)
            </button>
            <button
              onClick={() => setModel("sonnet")}
              className={`px-3 py-1.5 font-medium ${model === "sonnet" ? "bg-accent/15 text-accent" : "text-muted hover:text-white"}`}
            >
              Sonnet (deeper)
            </button>
          </div>
          <button
            onClick={generate}
            disabled={generating}
            className="flex items-center gap-1.5 rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {generating ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {generating ? "Generating..." : "Generate Report Now (last 30 days)"}
          </button>
        </div>
        {genError && <p className="mt-2 text-xs text-loss">{genError}</p>}
      </div>

      {loading && <LoadingSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && (reports?.length ?? 0) === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
          No reports yet - generate one above, or wait for the automatic monthly run.
        </div>
      )}

      {!loading && !error && reports && reports.length > 0 && (
        <div className="space-y-4">
          {reports.map((r) => <ReportCard key={r.id} report={r} />)}
        </div>
      )}
    </div>
  );
}
