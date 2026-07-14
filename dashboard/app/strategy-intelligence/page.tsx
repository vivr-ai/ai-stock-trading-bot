"use client";

import { useEffect, useState } from "react";
import StatCard from "@/components/StatCard";
import LoadingSkeleton from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import WinRateBreakdownChart from "@/components/WinRateBreakdownChart";
import { fmtMoney, timeAgo } from "@/lib/format";
import {
  Percent, TrendingUp, TrendingDown, Gauge, ListChecks, Target,
  CalendarClock, Repeat, BrainCircuit, GitBranch, LineChart as LineChartIcon, Info,
} from "lucide-react";

// Running log of every heuristic/scope boundary across the whole Strategy
// Intelligence layer (Phases 1-6+), so it's one click away instead of buried
// in code comments. Full detail (with the reasoning behind each) lives in
// docs/strategy-intelligence-limitations.md in the repo - this is the short
// version for the dashboard.
const KNOWN_LIMITATIONS: { phase: string; items: string[] }[] = [
  {
    phase: "Performance Analytics",
    items: [
      "Market regime is a heuristic from SPY's own change % and price-vs-SMA, not a dedicated volatility/VIX model.",
      "\"Confidence score\" is the bot's raw -10..+10 sentiment score, not a 0-100% probability.",
    ],
  },
  {
    phase: "Pattern Discovery",
    items: [
      "News source predictive value isn't measurable - NEWS_PROVIDER is one global setting, not recorded per trade.",
      "Stop-loss vs. take-profit exit classification is inferred from P&L sign, not a literal field - never marked statistically significant.",
    ],
  },
  {
    phase: "AI Research Assistant",
    items: [
      "Only produces a recommendation when a finding supports it - won't invent one to fill space.",
      "A backtestable \"proposedConfigChange\" is only attached for 4 simple rule types; most recommendations need manual review.",
    ],
  },
  {
    phase: "Strategy Health Score",
    items: [
      "Sample size is shown as a confidence label, never used to numerically deflate the score.",
      "Several scaling factors (Sharpe-to-score, drawdown penalty, stability) are hand-tuned heuristics, not industry standards.",
    ],
  },
  {
    phase: "Backtesting",
    items: [
      "Can only simulate trade-filtering rules (confidence threshold, sector/symbol exclusion, regime restriction).",
      "Cannot simulate a different stop-loss/take-profit level or position size - would need intraday price data not stored.",
      "Its \"max drawdown\"/\"risk-adjusted ratio\" are synthetic, built from the trade subset's own P&L sequence - not the same numbers shown on Performance/Strategy Health.",
    ],
  },
  {
    phase: "Monthly Research Report",
    items: [
      "The automatic monthly trigger lives in the Python bot's scheduler, not the dashboard - the dashboard's \"Generate Report Now\" button always works standalone either way.",
      "If the AI call fails, the report falls back to a short data-only summary rather than skipping the month entirely.",
    ],
  },
];

function KnownLimitationsPanel() {
  return (
    <details className="rounded-xl border border-dashed border-bg-border bg-bg-panel/50 p-4">
      <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-white">
        <Info size={15} /> Known limitations across this feature
      </summary>
      <div className="mt-3 space-y-3">
        {KNOWN_LIMITATIONS.map((group) => (
          <div key={group.phase}>
            <div className="text-xs font-semibold text-muted">{group.phase}</div>
            <ul className="mt-1 list-disc space-y-1 pl-4">
              {group.items.map((item, i) => (
                <li key={i} className="text-xs text-muted">{item}</li>
              ))}
            </ul>
          </div>
        ))}
        <p className="text-xs text-muted">
          Full detail and reasoning: <code className="text-white">docs/strategy-intelligence-limitations.md</code> in the repo.
        </p>
      </div>
    </details>
  );
}

type Bucket = {
  key: string; trades: number; winRatePct: number | null; avgPnl: number | null;
  totalPnl: number; sufficientSample: boolean;
};

type PeriodMetrics = {
  totalTrades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancy: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  avgHoldingPeriodDays: number | null;
  tradeFrequencyPerWeek: number | null;
  avgConfidenceScore: number | null;
  maxDrawdownPct: number | null;
  sharpeRatio: number | null;
  breakdowns: {
    byConfidence: Bucket[]; bySector: Bucket[]; bySymbol: Bucket[];
    byDayOfWeek: Bucket[]; byHourOfDay: Bucket[]; bySentimentLabel: Bucket[];
    byMarketRegime: Bucket[];
  };
};

type StrategyIntelligenceResponse = {
  hasAnyData: boolean;
  executiveSummary: {
    currentStrategyVersion: string;
    strategyDeployedAt: string | null;
    tradesAnalysedSinceVersion: number;
    analysisConfidence: "none" | "low" | "medium" | "high";
    lastAnalysisDate: string;
    currentMarketRegime: string | null;
  };
  periods: { allTime: PeriodMetrics; last30Days: PeriodMetrics; last90Days: PeriodMetrics };
};

const CONFIDENCE_LABEL: Record<string, string> = {
  none: "No data yet", low: "Low (small sample)", medium: "Medium", high: "High",
};

const REGIME_LABEL: Record<string, string> = {
  bull: "Bull", bear: "Bear", sideways: "Sideways",
  high_volatility: "High Volatility", low_volatility: "Low Volatility",
};

function PeriodPanel({ data }: { data: PeriodMetrics }) {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard
          label="Win Rate"
          value={data.winRatePct != null ? `${data.winRatePct.toFixed(1)}%` : "—"}
          icon={<Percent size={15} />}
        />
        <StatCard
          label="Profit Factor"
          value={data.profitFactor != null ? data.profitFactor.toFixed(2) : "—"}
          sublabel="Gross win / gross loss"
          icon={<Gauge size={15} />}
        />
        <StatCard
          label="Expectancy"
          value={fmtMoney(data.expectancy)}
          sublabel="Avg P&L per trade"
          tone={data.expectancy != null ? (data.expectancy >= 0 ? "gain" : "loss") : "neutral"}
          icon={<Target size={15} />}
        />
        <StatCard label="Average Win" value={fmtMoney(data.avgWin)} tone="gain" icon={<TrendingUp size={15} />} />
        <StatCard label="Average Loss" value={fmtMoney(data.avgLoss)} tone="loss" icon={<TrendingDown size={15} />} />
        <StatCard
          label="Max Drawdown"
          value={data.maxDrawdownPct != null ? `${data.maxDrawdownPct.toFixed(2)}%` : "—"}
          tone={data.maxDrawdownPct ? "loss" : "neutral"}
          icon={<TrendingDown size={15} />}
        />
        <StatCard
          label="Sharpe Ratio"
          value={data.sharpeRatio != null ? data.sharpeRatio.toFixed(2) : "—"}
          icon={<LineChartIcon size={15} />}
        />
        <StatCard
          label="Avg Holding Period"
          value={data.avgHoldingPeriodDays != null ? `${data.avgHoldingPeriodDays.toFixed(1)}d` : "—"}
          icon={<CalendarClock size={15} />}
        />
        <StatCard
          label="Trade Frequency"
          value={data.tradeFrequencyPerWeek != null ? `${data.tradeFrequencyPerWeek.toFixed(1)}/wk` : "—"}
          icon={<Repeat size={15} />}
        />
        <StatCard
          label="Avg Confidence Score"
          value={data.avgConfidenceScore != null ? data.avgConfidenceScore.toFixed(1) : "—"}
          sublabel="Sentiment score scale (-10 to +10)"
          icon={<BrainCircuit size={15} />}
        />
        <StatCard label="Trades Analysed" value={data.totalTrades} icon={<ListChecks size={15} />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Confidence Range</h3>
          <p className="mb-2 text-xs text-muted">
            Sentiment score bucket (-10 to +10 scale) - grey bars have fewer than 20 trades.
          </p>
          <WinRateBreakdownChart data={data.breakdowns.byConfidence} />
        </div>
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Sector</h3>
          <WinRateBreakdownChart data={data.breakdowns.bySector} />
        </div>
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Day of Week</h3>
          <WinRateBreakdownChart data={data.breakdowns.byDayOfWeek} />
        </div>
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Hour of Day (UTC)</h3>
          <WinRateBreakdownChart data={data.breakdowns.byHourOfDay} />
        </div>
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Sentiment Label</h3>
          <WinRateBreakdownChart data={data.breakdowns.bySentimentLabel} />
        </div>
        <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
          <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Market Regime</h3>
          <WinRateBreakdownChart data={data.breakdowns.byMarketRegime} />
        </div>
      </div>

      <div className="rounded-xl border border-bg-border bg-bg-panel p-4">
        <h3 className="mb-2 text-sm font-medium text-white">Win Rate by Symbol</h3>
        <WinRateBreakdownChart data={data.breakdowns.bySymbol} />
      </div>
    </div>
  );
}

export default function StrategyIntelligencePage() {
  const [data, setData] = useState<StrategyIntelligenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"allTime" | "last30Days" | "last90Days">("allTime");

  async function load() {
    try {
      const res = await fetch("/api/strategy-intelligence");
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
        <h1 className="text-xl font-semibold text-white">Strategy Intelligence</h1>
        <p className="text-sm text-muted">
          Analytics only - this page never changes trading behaviour. Recommendations and approval
          workflow live on the Recommendations page.
        </p>
      </div>

      <div className="mb-6">
        <KnownLimitationsPanel />
      </div>

      {loading && <LoadingSkeleton rows={6} />}
      {!loading && error && <ErrorState message={error} />}

      {!loading && !error && data && !data.hasAnyData && (
        <div className="rounded-xl border border-bg-border bg-bg-panel p-6 text-sm text-muted">
          No completed trades yet. This fills in once the bot has closed at least one position.
        </div>
      )}

      {!loading && !error && data && data.hasAnyData && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <StatCard
              label="Current Strategy Version"
              value={data.executiveSummary.currentStrategyVersion}
              sublabel={
                data.executiveSummary.strategyDeployedAt
                  ? `Deployed ${timeAgo(data.executiveSummary.strategyDeployedAt)}`
                  : undefined
              }
              icon={<GitBranch size={15} />}
            />
            <StatCard
              label="Trades Analysed (this version)"
              value={data.executiveSummary.tradesAnalysedSinceVersion}
              icon={<ListChecks size={15} />}
            />
            <StatCard
              label="Analysis Confidence"
              value={CONFIDENCE_LABEL[data.executiveSummary.analysisConfidence]}
              sublabel="Based on total trade count - not a formal significance test"
              icon={<Gauge size={15} />}
            />
            <StatCard
              label="Current Market Regime"
              value={
                data.executiveSummary.currentMarketRegime
                  ? REGIME_LABEL[data.executiveSummary.currentMarketRegime] ?? data.executiveSummary.currentMarketRegime
                  : "—"
              }
              sublabel="SPY-based heuristic, see System Health"
              icon={<LineChartIcon size={15} />}
            />
            <StatCard
              label="Last Analysis"
              value={timeAgo(data.executiveSummary.lastAnalysisDate)}
              icon={<CalendarClock size={15} />}
            />
          </div>

          <div className="flex gap-2 border-b border-bg-border">
            {(["allTime", "last30Days", "last90Days"] as const).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-3 py-2 text-sm font-medium transition-colors ${
                  tab === key
                    ? "border-b-2 border-accent text-white"
                    : "text-muted hover:text-white"
                }`}
              >
                {key === "allTime" ? "All Time" : key === "last30Days" ? "Last 30 Days" : "Last 90 Days"}
              </button>
            ))}
          </div>

          <PeriodPanel data={data.periods[tab]} />
        </div>
      )}
    </div>
  );
}
