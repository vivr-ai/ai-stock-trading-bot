"use client";

import { useEffect, useState } from "react";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import { timeAgo } from "@/lib/format";
import { CheckCircle2, XCircle, Sparkles, BrainCircuit, Loader2, FlaskConical, Rocket, TriangleAlert } from "lucide-react";

const SOURCE_LABEL: Record<string, string> = {
  manual: "Manual",
  pattern_discovery: "Pattern Discovery",
  ai_research_assistant: "AI Research Assistant",
};

type BacktestMetrics = {
  totalTrades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancyPct: number | null;
  maxDrawdownPct: number | null;
  riskAdjustedRatio: number | null;
};

type BacktestResult = {
  simulable: boolean;
  changeSummary: string;
  baseline: BacktestMetrics | null;
  proposed: BacktestMetrics | null;
  tradesExcluded: number;
  tradesExcludedPct: number | null;
  recommendation: "deploy" | "do_not_deploy" | "inconclusive";
  confidenceLevel: "insufficient" | "low" | "medium" | "high";
  increasedRisks: string[];
  limitations: string[];
};

type Recommendation = {
  id: number;
  created_at: string;
  source: string;
  title: string;
  observation: string | null;
  evidence: string | null;
  statistical_confidence: string | null;
  estimated_impact: string | null;
  risks: string | null;
  recommendation: string | null;
  priority: "low" | "medium" | "high";
  proposed_config_changes: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected";
  reviewed_at: string | null;
  reviewed_by: string | null;
  review_notes: string | null;
  backtest_result: BacktestResult | null;
  deployed_as_version: string | null;
};

const BACKTEST_REC_STYLE: Record<string, string> = {
  deploy: "bg-gain/15 text-gain",
  do_not_deploy: "bg-loss/15 text-loss",
  inconclusive: "bg-bg-panel2 text-muted",
};

const BACKTEST_REC_LABEL: Record<string, string> = {
  deploy: "Backtest favours deploying",
  do_not_deploy: "Backtest recommends against deploying",
  inconclusive: "Backtest inconclusive",
};

function fmtPct(v: number | null | undefined, digits = 1) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function BacktestMetricRow({ label, baseline, proposed, format }: {
  label: string;
  baseline: number | null | undefined;
  proposed: number | null | undefined;
  format: (v: number | null | undefined) => string;
}) {
  return (
    <tr className="border-t border-bg-border">
      <td className="py-1.5 pr-4 text-xs text-muted">{label}</td>
      <td className="py-1.5 px-3 text-xs text-white">{format(baseline)}</td>
      <td className="py-1.5 px-3 text-xs text-white">{format(proposed)}</td>
    </tr>
  );
}

function BacktestPanel({ result }: { result: BacktestResult }) {
  if (!result.simulable) {
    return (
      <div className="mt-3 rounded-lg border border-dashed border-bg-border p-3 text-xs text-muted">
        <div className="mb-1 font-medium text-white">Not simulable: {result.changeSummary}</div>
        {result.limitations.map((l, i) => <p key={i} className="mt-1">{l}</p>)}
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-lg border border-bg-border bg-bg-panel2/40 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${BACKTEST_REC_STYLE[result.recommendation]}`}>
          {BACKTEST_REC_LABEL[result.recommendation]}
        </span>
        <span className="text-xs text-muted">{result.confidenceLevel} confidence</span>
      </div>
      <table className="w-full">
        <thead>
          <tr>
            <th className="pb-1 text-left text-xs text-muted">Metric</th>
            <th className="pb-1 px-3 text-left text-xs text-muted">Current</th>
            <th className="pb-1 px-3 text-left text-xs text-muted">Proposed</th>
          </tr>
        </thead>
        <tbody>
          <BacktestMetricRow label="Trades" baseline={result.baseline?.totalTrades} proposed={result.proposed?.totalTrades} format={(v) => (v ?? "—").toString()} />
          <BacktestMetricRow label="Win rate" baseline={result.baseline?.winRatePct} proposed={result.proposed?.winRatePct} format={(v) => (v != null ? `${v.toFixed(1)}%` : "—")} />
          <BacktestMetricRow label="Profit factor" baseline={result.baseline?.profitFactor} proposed={result.proposed?.profitFactor} format={(v) => (v != null ? v.toFixed(2) : "—")} />
          <BacktestMetricRow label="Expectancy (avg P&L%)" baseline={result.baseline?.expectancyPct} proposed={result.proposed?.expectancyPct} format={fmtPct} />
          <BacktestMetricRow label="Max drawdown (synthetic)" baseline={result.baseline?.maxDrawdownPct} proposed={result.proposed?.maxDrawdownPct} format={(v) => (v != null ? `${v.toFixed(1)}%` : "—")} />
        </tbody>
      </table>
      {result.increasedRisks.length > 0 && (
        <div className="mt-2 space-y-1">
          {result.increasedRisks.map((r, i) => (
            <p key={i} className="flex items-start gap-1.5 text-xs text-accent/90">
              <TriangleAlert size={12} className="mt-0.5 shrink-0" /> {r}
            </p>
          ))}
        </div>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer text-xs text-muted">Limitations of this backtest</summary>
        <div className="mt-1 space-y-1 text-xs text-muted">
          {result.limitations.map((l, i) => <p key={i}>{l}</p>)}
        </div>
      </details>
    </div>
  );
}

const PRIORITY_STYLE: Record<string, string> = {
  high: "bg-loss/15 text-loss",
  medium: "bg-accent/15 text-accent",
  low: "bg-bg-panel2 text-muted",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-bg-panel2 text-muted",
  approved: "bg-gain/15 text-gain",
  rejected: "bg-loss/15 text-loss",
};

function RecCard({
  rec,
  onReview,
  onBacktest,
  onDeploy,
}: {
  rec: Recommendation;
  onReview: (id: number, status: "approved" | "rejected", notes: string) => void;
  onBacktest: (id: number) => Promise<void>;
  onDeploy: (id: number, version: string, description: string) => Promise<void>;
}) {
  const [notes, setNotes] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [backtesting, setBacktesting] = useState(false);
  const [backtestError, setBacktestError] = useState<string | null>(null);
  const [showDeploy, setShowDeploy] = useState(false);
  const [deployVersion, setDeployVersion] = useState("");
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);

  const hasConfigChange = rec.proposed_config_changes != null;
  const backtestBlocksDeploy = rec.backtest_result?.simulable && rec.backtest_result.recommendation === "do_not_deploy";

  async function runBacktest() {
    setBacktesting(true);
    setBacktestError(null);
    try {
      await onBacktest(rec.id);
    } catch (err) {
      setBacktestError(err instanceof Error ? err.message : "Backtest failed");
    } finally {
      setBacktesting(false);
    }
  }

  async function deploy() {
    setDeploying(true);
    setDeployError(null);
    try {
      await onDeploy(rec.id, deployVersion, rec.title);
      setShowDeploy(false);
    } catch (err) {
      setDeployError(err instanceof Error ? err.message : "Deploy failed");
    } finally {
      setDeploying(false);
    }
  }

  return (
    <div className="rounded-xl border border-bg-border bg-bg-panel p-5">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${PRIORITY_STYLE[rec.priority]}`}>
          {rec.priority} priority
        </span>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[rec.status]}`}>
          {rec.status}
        </span>
        <span className="text-xs text-muted">{SOURCE_LABEL[rec.source] ?? rec.source.replace(/_/g, " ")}</span>
        <span className="text-xs text-muted">· {timeAgo(rec.created_at)}</span>
        {rec.deployed_as_version && (
          <span className="rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">
            Deployed as {rec.deployed_as_version}
          </span>
        )}
      </div>
      <h3 className="mb-2 text-sm font-semibold text-white">{rec.title}</h3>

      <button onClick={() => setExpanded((e) => !e)} className="mb-2 text-xs text-accent">
        {expanded ? "Hide details" : "Show details"}
      </button>

      {expanded && (
        <div className="mb-3 space-y-2 text-xs text-muted">
          {rec.observation && <div><span className="text-white">Observation: </span>{rec.observation}</div>}
          {rec.evidence && <div><span className="text-white">Evidence: </span>{rec.evidence}</div>}
          {rec.statistical_confidence && (
            <div><span className="text-white">Statistical confidence: </span>{rec.statistical_confidence}</div>
          )}
          {rec.estimated_impact && (
            <div><span className="text-white">Estimated impact: </span>{rec.estimated_impact}</div>
          )}
          {rec.risks && <div><span className="text-white">Risks: </span>{rec.risks}</div>}
          {rec.recommendation && (
            <div><span className="text-white">Recommendation: </span>{rec.recommendation}</div>
          )}
          {rec.review_notes && (
            <div className="border-t border-bg-border pt-2">
              <span className="text-white">Review notes: </span>{rec.review_notes}
              {rec.reviewed_by && ` — ${rec.reviewed_by}`}
              {rec.reviewed_at && ` (${timeAgo(rec.reviewed_at)})`}
            </div>
          )}
        </div>
      )}

      {rec.status === "pending" && (
        <div className="space-y-2 border-t border-bg-border pt-3">
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional review notes"
            rows={2}
            className="w-full rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-xs text-white"
          />
          <div className="flex gap-2">
            <button
              onClick={() => onReview(rec.id, "approved", notes)}
              className="flex items-center gap-1.5 rounded-lg bg-gain/15 px-3 py-1.5 text-xs font-medium text-gain hover:bg-gain/25"
            >
              <CheckCircle2 size={14} /> Approve
            </button>
            <button
              onClick={() => onReview(rec.id, "rejected", notes)}
              className="flex items-center gap-1.5 rounded-lg bg-loss/15 px-3 py-1.5 text-xs font-medium text-loss hover:bg-loss/25"
            >
              <XCircle size={14} /> Reject
            </button>
          </div>
        </div>
      )}

      {rec.status === "approved" && !rec.deployed_as_version && (
        <div className="mt-2 space-y-2 border-t border-bg-border pt-3">
          {!hasConfigChange && (
            <div className="rounded-lg border border-dashed border-bg-border p-2 text-xs text-muted">
              Approved. No structured config change attached to this recommendation, so it can&apos;t be
              backtested automatically - review it manually, then deploy a new version on the Strategy
              Versions page (or below) if you decide to proceed.
            </div>
          )}

          {hasConfigChange && !rec.backtest_result && (
            <button
              onClick={runBacktest}
              disabled={backtesting}
              className="flex items-center gap-1.5 rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
            >
              {backtesting ? <Loader2 size={14} className="animate-spin" /> : <FlaskConical size={14} />}
              {backtesting ? "Backtesting..." : "Run Backtest"}
            </button>
          )}
          {backtestError && <p className="text-xs text-loss">{backtestError}</p>}

          {rec.backtest_result && <BacktestPanel result={rec.backtest_result} />}
          {hasConfigChange && rec.backtest_result && (
            <button
              onClick={runBacktest}
              disabled={backtesting}
              className="text-xs text-accent hover:underline disabled:opacity-50"
            >
              {backtesting ? "Re-running..." : "Re-run backtest"}
            </button>
          )}

          {!showDeploy ? (
            <button
              onClick={() => setShowDeploy(true)}
              className="flex items-center gap-1.5 rounded-lg bg-bg-panel2 px-3 py-1.5 text-xs font-medium text-white hover:bg-bg-panel2/70"
            >
              <Rocket size={14} /> Deploy as new version
            </button>
          ) : (
            <div className="space-y-2 rounded-lg border border-bg-border p-3">
              {backtestBlocksDeploy && (
                <div className="flex items-start gap-1.5 rounded-lg bg-loss/10 p-2 text-xs text-loss">
                  <TriangleAlert size={13} className="mt-0.5 shrink-0" />
                  The backtest recommends against deploying this. Proceed only if you have a specific
                  reason to override it.
                </div>
              )}
              <input
                value={deployVersion}
                onChange={(e) => setDeployVersion(e.target.value)}
                placeholder="New version label, e.g. v2"
                className="w-full rounded-lg border border-bg-border bg-bg-panel2 px-3 py-2 text-xs text-white"
              />
              <p className="text-xs text-muted">
                This deactivates the current version and tags new trades with this one going forward.
                It does not change any environment variables or code - you still need to make the
                actual config change yourself to match this recommendation.
              </p>
              {deployError && <p className="text-xs text-loss">{deployError}</p>}
              <div className="flex gap-2">
                <button
                  onClick={deploy}
                  disabled={deploying || !deployVersion}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-50 ${
                    backtestBlocksDeploy ? "bg-loss/15 text-loss hover:bg-loss/25" : "bg-accent px-3 py-1.5 text-white hover:opacity-90"
                  }`}
                >
                  {deploying ? "Deploying..." : backtestBlocksDeploy ? "Deploy anyway" : "Deploy"}
                </button>
                <button
                  onClick={() => setShowDeploy(false)}
                  className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted hover:text-white"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function RecommendationsPage() {
  const [recs, setRecs] = useState<Recommendation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"pending" | "approved" | "rejected" | "all">("pending");
  const [model, setModel] = useState<"haiku" | "sonnet">("haiku");
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<string | null>(null);

  async function load() {
    try {
      const res = await fetch("/api/strategy-recommendations");
      if (!res.ok) throw new Error((await res.json()).error || "Request failed");
      const json = await res.json();
      setRecs(json.recommendations);
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

  async function onReview(id: number, status: "approved" | "rejected", notes: string) {
    await fetch("/api/strategy-recommendations", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, status, reviewNotes: notes }),
    });
    await load();
  }

  async function onBacktest(id: number) {
    const res = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recommendationId: id }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Backtest failed");
    await load();
  }

  async function onDeploy(id: number, version: string, description: string) {
    const res = await fetch("/api/strategy-versions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version, description, fromRecommendationId: id }),
    });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || "Deploy failed");
    await load();
  }

  async function runResearch() {
    setRunning(true);
    setRunResult(null);
    try {
      const res = await fetch("/api/ai-research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.error || "Request failed");
      if (json.created > 0) {
        setRunResult(`Generated ${json.created} new recommendation${json.created === 1 ? "" : "s"}.`);
        setFilter("pending");
        await load();
      } else {
        setRunResult(json.note || "No new recommendations generated.");
      }
    } catch (err) {
      setRunResult(err instanceof Error ? err.message : "Failed to run AI Research Assistant.");
    } finally {
      setRunning(false);
    }
  }

  const filtered = recs?.filter((r) => filter === "all" || r.status === filter) ?? [];

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-white">Recommendations</h1>
        <p className="text-sm text-muted">
          Advisory only - approving a recommendation here never changes trading behaviour by itself.
          To actually change trading behaviour, an approved recommendation must be explicitly deployed
          as a new strategy version on the Strategy Versions page.
        </p>
      </div>

      <div className="mb-6 rounded-xl border border-bg-border bg-bg-panel p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white">
          <BrainCircuit size={16} /> AI Research Assistant
        </div>
        <p className="mb-3 text-xs text-muted">
          Reads Pattern Discovery&apos;s findings that meet their minimum sample size and drafts
          plain-English recommendations. Grounded only in those findings - if none qualify yet, no
          report is generated. Haiku is cheaper and is what a scheduled run would use; Sonnet gives
          deeper analysis on demand.
        </p>
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
              Sonnet (deeper, on demand)
            </button>
          </div>
          <button
            onClick={runResearch}
            disabled={running}
            className="flex items-center gap-1.5 rounded-lg bg-accent/15 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/25 disabled:opacity-50"
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {running ? "Analysing..." : "Run AI Research"}
          </button>
        </div>
        {runResult && <p className="mt-2 text-xs text-muted">{runResult}</p>}
      </div>

      <div className="mb-4 flex gap-2 border-b border-bg-border">
        {(["pending", "approved", "rejected", "all"] as const).map((key) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`px-3 py-2 text-sm font-medium capitalize transition-colors ${
              filter === key ? "border-b-2 border-accent text-white" : "text-muted hover:text-white"
            }`}
          >
            {key}
          </button>
        ))}
      </div>

      {loading && <LoadingSkeleton rows={3} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && filtered.length === 0 && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-center text-sm text-muted">
          <Sparkles size={20} className="mx-auto mb-2 text-muted" />
          No {filter !== "all" ? filter : ""} recommendations yet.
        </div>
      )}

      {!loading && !error && filtered.length > 0 && (
        <div className="space-y-4">
          {filtered.map((rec) => (
            <RecCard key={rec.id} rec={rec} onReview={onReview} onBacktest={onBacktest} onDeploy={onDeploy} />
          ))}
        </div>
      )}
    </div>
  );
}
