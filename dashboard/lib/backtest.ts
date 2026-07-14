// Backtesting & Simulation (Phase 6): before an approved recommendation can
// be deployed as a new strategy version, replay it against ALL-TIME closed
// trades and show current-vs-proposed performance.
//
// Honest scope, disclosed in every result: this schema stores each trade's
// entry/exit price and the realized P&L under the rules that were actually
// active at the time - it does NOT store the underlying price series or
// news the bot saw. That means this engine can accurately answer "which of
// our actual historical trades would this new rule have taken or skipped,
// and how would the resulting portfolio of realized outcomes have looked" -
// this is a real, useful backtest for any rule that FILTERS which trades to
// take (confidence threshold, sector/symbol exclusion, regime restriction).
// It CANNOT accurately simulate a different stop-loss/take-profit level or
// different position sizing, because that requires the intraday price path
// between entry and exit, which isn't recorded. Those change types are
// explicitly marked not-simulable rather than faked with a misleading
// number - same "don't fabricate what you can't measure" stance as Pattern
// Discovery's news-source and stop/take-profit-proxy findings.

import type { ClosedTradeRow } from "./strategyAnalytics";

export type ConfigChangeSpec =
  | { type: "min_confidence"; value: number }
  | { type: "exclude_sector"; sector: string }
  | { type: "exclude_symbol"; symbol: string }
  | { type: "require_regime"; regimes: string[] }
  | { type: "other"; description: string };

export type BacktestMetrics = {
  totalTrades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancyPct: number | null; // mean pnl_pct - comparable across differently-sized subsets, unlike raw dollar expectancy
  maxDrawdownPct: number | null; // from a SYNTHETIC cumulative-return curve built from this trade subset's own pnl_pct, in trade order - not the real portfolio equity curve shown elsewhere
  riskAdjustedRatio: number | null; // mean(pnl_pct) / stddev(pnl_pct), per-trade, NOT annualized - not the same thing as the Sharpe ratio shown on Performance/Strategy Health
};

export type BacktestResult = {
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

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function stdDev(values: number[]): number | null {
  if (values.length < 2) return null;
  const m = mean(values)!;
  const variance = values.reduce((a, v) => a + (v - m) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}

function toMs(value: string | Date): number {
  return value instanceof Date ? value.getTime() : new Date(value).getTime();
}

function syntheticMaxDrawdownPct(pnlPctSeries: number[]): number | null {
  if (pnlPctSeries.length === 0) return null;
  let equity = 1;
  let peak = 1;
  let worst = 0;
  for (const p of pnlPctSeries) {
    equity *= 1 + p / 100;
    if (equity > peak) peak = equity;
    if (peak > 0) worst = Math.max(worst, ((peak - equity) / peak) * 100);
  }
  return worst;
}

function computeBacktestMetrics(trades: ClosedTradeRow[]): BacktestMetrics {
  const total = trades.length;
  if (total === 0) {
    return {
      totalTrades: 0, winRatePct: null, profitFactor: null, expectancyPct: null,
      maxDrawdownPct: null, riskAdjustedRatio: null,
    };
  }
  const sorted = [...trades].sort((a, b) => toMs(a.ts) - toMs(b.ts));
  const wins = sorted.filter((t) => Number(t.pnl) > 0);
  const losses = sorted.filter((t) => Number(t.pnl) < 0);
  const winRatePct = (wins.length / total) * 100;
  const grossWin = wins.reduce((a, t) => a + Number(t.pnl), 0);
  const grossLoss = Math.abs(losses.reduce((a, t) => a + Number(t.pnl), 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : null;
  const pnlPctSeries = sorted.map((t) => Number(t.pnl_pct));
  const expectancyPct = mean(pnlPctSeries);
  const maxDrawdownPct = syntheticMaxDrawdownPct(pnlPctSeries);
  const sd = stdDev(pnlPctSeries);
  const riskAdjustedRatio = sd != null && sd > 0 && expectancyPct != null ? expectancyPct / sd : null;

  return { totalTrades: total, winRatePct, profitFactor, expectancyPct, maxDrawdownPct, riskAdjustedRatio };
}

function changeSummaryFor(change: ConfigChangeSpec): string {
  switch (change.type) {
    case "min_confidence":
      return `Require confidence score >= ${change.value}`;
    case "exclude_sector":
      return `Stop trading the "${change.sector}" sector`;
    case "exclude_symbol":
      return `Stop trading ${change.symbol}`;
    case "require_regime":
      return `Only trade during: ${change.regimes.join(", ")}`;
    case "other":
      return change.description;
  }
}

function applyFilter(trades: ClosedTradeRow[], change: ConfigChangeSpec): ClosedTradeRow[] | null {
  switch (change.type) {
    case "min_confidence":
      // Trades with no recorded confidence score can't be verified against a
      // numeric threshold, so they're conservatively excluded from the
      // proposed set (they stay in the baseline).
      return trades.filter((t) => t.confidence_score != null && Number(t.confidence_score) >= change.value);
    case "exclude_sector":
      return trades.filter((t) => (t.sector ?? "unknown") !== change.sector);
    case "exclude_symbol":
      return trades.filter((t) => t.symbol !== change.symbol);
    case "require_regime":
      return trades.filter((t) => t.market_regime != null && change.regimes.includes(t.market_regime));
    case "other":
      return null; // not simulable
  }
}

const MIN_TRADES_FOR_BACKTEST = 15; // softer than Pattern Discovery's general 30 - backtests are inherently working with a subset of an already-limited history

function confidenceFor(n: number): BacktestResult["confidenceLevel"] {
  if (n < MIN_TRADES_FOR_BACKTEST) return "insufficient";
  if (n < 30) return "low";
  if (n < 100) return "medium";
  return "high";
}

export function runBacktest(allTrades: ClosedTradeRow[], change: ConfigChangeSpec): BacktestResult {
  const changeSummary = changeSummaryFor(change);
  const limitations = [
    "This backtest replays ALL-TIME closed trades to see which ones this rule would have kept " +
      "or excluded, using their actual realized entry/exit outcomes. It does not re-simulate a " +
      "different stop-loss/take-profit level, different position sizing, or brand-new hypothetical " +
      "trades the old rules never surfaced - that would require the intraday price path, which isn't recorded.",
  ];

  if (change.type === "other") {
    return {
      simulable: false,
      changeSummary,
      baseline: null,
      proposed: null,
      tradesExcluded: 0,
      tradesExcludedPct: null,
      recommendation: "inconclusive",
      confidenceLevel: "insufficient",
      increasedRisks: [],
      limitations: [
        ...limitations,
        "This specific change isn't one of the backtestable types (confidence threshold, sector " +
          "exclusion, symbol exclusion, market-regime restriction) - review it manually before deploying.",
      ],
    };
  }

  const filtered = applyFilter(allTrades, change);
  const proposedTrades = filtered ?? [];
  const baseline = computeBacktestMetrics(allTrades);
  const proposed = computeBacktestMetrics(proposedTrades);
  const tradesExcluded = allTrades.length - proposedTrades.length;
  const tradesExcludedPct = allTrades.length > 0 ? (tradesExcluded / allTrades.length) * 100 : null;

  const increasedRisks: string[] = [];
  if (tradesExcludedPct != null && tradesExcludedPct > 0) {
    increasedRisks.push(
      `This rule would have excluded ${tradesExcluded} of ${allTrades.length} historical trades ` +
        `(${tradesExcludedPct.toFixed(0)}%), which would have meant capital sitting idle more often.`
    );
  }
  if (proposed.totalTrades < MIN_TRADES_FOR_BACKTEST) {
    increasedRisks.push(
      `Only ${proposed.totalTrades} historical trade(s) would have passed this rule - too few to be ` +
        "confident the comparison reflects anything beyond noise."
    );
  }

  const confidenceLevel = confidenceFor(Math.min(baseline.totalTrades, proposed.totalTrades));

  let recommendation: BacktestResult["recommendation"] = "inconclusive";
  if (confidenceLevel !== "insufficient" && baseline.expectancyPct != null && proposed.expectancyPct != null) {
    let signal = 0;
    signal += proposed.expectancyPct >= baseline.expectancyPct ? 1 : -1;
    if (baseline.winRatePct != null && proposed.winRatePct != null) {
      signal += proposed.winRatePct >= baseline.winRatePct ? 1 : -1;
    }
    if (baseline.maxDrawdownPct != null && proposed.maxDrawdownPct != null) {
      signal += proposed.maxDrawdownPct <= baseline.maxDrawdownPct ? 1 : -1;
    }
    if (signal >= 2) recommendation = "deploy";
    else if (signal <= -2) recommendation = "do_not_deploy";
    else recommendation = "inconclusive";
  }

  if (recommendation === "do_not_deploy") {
    increasedRisks.unshift(
      "The backtest shows this change would have performed WORSE than the current rules across " +
        "most metrics compared - deployment is not recommended."
    );
  }

  return {
    simulable: true,
    changeSummary,
    baseline,
    proposed,
    tradesExcluded,
    tradesExcludedPct,
    recommendation,
    confidenceLevel,
    increasedRisks,
    limitations,
  };
}

export function parseConfigChangeSpec(raw: unknown): ConfigChangeSpec | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  switch (r.type) {
    case "min_confidence":
      return typeof r.value === "number" ? { type: "min_confidence", value: r.value } : null;
    case "exclude_sector":
      return typeof r.sector === "string" ? { type: "exclude_sector", sector: r.sector } : null;
    case "exclude_symbol":
      return typeof r.symbol === "string" ? { type: "exclude_symbol", symbol: r.symbol } : null;
    case "require_regime":
      return Array.isArray(r.regimes) && r.regimes.every((x) => typeof x === "string")
        ? { type: "require_regime", regimes: r.regimes as string[] }
        : null;
    case "other":
      return typeof r.description === "string" ? { type: "other", description: r.description } : null;
    default:
      return null;
  }
}
