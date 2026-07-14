// Strategy Health Score (Phase 5): a single 0-100 composite meant to answer
// "how healthy is the current strategy, at a glance" - it does NOT replace
// Performance Analytics (Phase 1) or Pattern Discovery (Phase 3), it
// summarizes them. Pure computation, no DB access, mirroring the rest of
// this analytics layer - the API route fetches rows and passes them in.
//
// Composite of 7 weighted sub-scores (each 0-100), covering every factor the
// product spec calls out: win rate, drawdown, stability, risk-adjusted
// return, consistency, market adaptation, and historical trend. "Sample
// size" is NOT folded in as a multiplier that silently deflates the number -
// doing that would conflate "we don't have enough data yet" with "this is
// bad", which is misleading. Instead, sample-size adequacy is surfaced
// per-component (meetsMinSample) and as a top-level confidenceLevel, exactly
// like Phase 1's executive summary and Phase 3's findings - the score is
// always the best estimate from available data, labelled with how much to
// trust it.

import type { ClosedTradeRow } from "./strategyAnalytics";

export type HealthComponent = {
  key: string;
  label: string;
  score: number | null; // 0-100, null only if literally uncomputable (no data at all)
  weight: number; // out of 100, before renormalizing across computable components
  meetsMinSample: boolean;
  description: string;
};

export type ConfidenceLevel = "insufficient" | "low" | "medium" | "high";

export type StrategyHealth = {
  overallScore: number | null;
  confidenceLevel: ConfidenceLevel;
  totalTrades: number;
  components: HealthComponent[];
};

const MIN_TRADES_FOR_HEALTH = 30; // same general bar as Pattern Discovery
const MIN_TRADES_PER_REGIME = 15; // softer than Pattern Discovery's per-category bar - this is a descriptive score, not a hypothesis test
const MIN_RECENT_TRADES = 10;

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

function clamp(v: number, lo = 0, hi = 100): number {
  return Math.max(lo, Math.min(hi, v));
}

function winRatePct(trades: ClosedTradeRow[]): number | null {
  if (trades.length === 0) return null;
  return (trades.filter((t) => Number(t.pnl) > 0).length / trades.length) * 100;
}

function meanPnlPct(trades: ClosedTradeRow[]): number | null {
  if (trades.length === 0) return null;
  return mean(trades.map((t) => Number(t.pnl_pct)));
}

// ---- individual components -------------------------------------------------

function winRateComponent(trades: ClosedTradeRow[]): HealthComponent {
  const wr = winRatePct(trades);
  return {
    key: "win_rate",
    label: "Win Rate",
    score: wr != null ? clamp(wr) : null,
    weight: 15,
    meetsMinSample: trades.length >= MIN_TRADES_FOR_HEALTH,
    description:
      wr != null
        ? `${wr.toFixed(1)}% of ${trades.length} closed trades were profitable.`
        : "No closed trades yet.",
  };
}

function sharpeComponent(sharpe: number | null, equityPoints: number): HealthComponent {
  // Sharpe of 0 -> 50, +2 -> 100, -2 -> 0 (annualized Sharpe for a strategy
  // like this typically lands well within +/-2 - a heuristic scale, not a
  // industry-standard conversion.
  const score = sharpe != null ? clamp(50 + sharpe * 25) : null;
  return {
    key: "risk_adjusted_return",
    label: "Risk-Adjusted Return (Sharpe)",
    score,
    weight: 15,
    meetsMinSample: equityPoints >= MIN_TRADES_FOR_HEALTH,
    description:
      sharpe != null
        ? `Annualized Sharpe ratio of ${sharpe.toFixed(2)} from the portfolio equity curve.`
        : "Not enough portfolio equity history yet to compute a Sharpe ratio.",
  };
}

function drawdownComponent(maxDrawdownPct: number | null, equityPoints: number): HealthComponent {
  // 0% drawdown -> 100, 25%+ drawdown -> 0.
  const score = maxDrawdownPct != null ? clamp(100 - maxDrawdownPct * 4) : null;
  return {
    key: "drawdown",
    label: "Drawdown Control",
    score,
    weight: 15,
    meetsMinSample: equityPoints >= MIN_TRADES_FOR_HEALTH,
    description:
      maxDrawdownPct != null
        ? `Worst peak-to-trough decline in the portfolio equity curve so far: ${maxDrawdownPct.toFixed(1)}%.`
        : "Not enough portfolio equity history yet to compute a max drawdown.",
  };
}

function stabilityComponent(dailyReturns: number[]): HealthComponent {
  const sd = stdDev(dailyReturns);
  // 0% daily-return std dev -> 100, ~6.7%+ -> 0. Heuristic scaling factor
  // (15), tuned for a single-account swing-trading bot, not a general
  // benchmark - disclosed here rather than presented as a standard.
  const score = sd != null ? clamp(100 - sd * 15) : null;
  return {
    key: "stability",
    label: "Equity Curve Stability",
    score,
    weight: 15,
    meetsMinSample: dailyReturns.length >= MIN_TRADES_FOR_HEALTH,
    description:
      sd != null
        ? `Day-to-day portfolio value swings by ${sd.toFixed(2)}pp on average (standard deviation of daily % change).`
        : "Not enough portfolio equity history yet to measure day-to-day stability.",
  };
}

function consistencyComponent(trades: ClosedTradeRow[]): HealthComponent {
  const sorted = [...trades].sort((a, b) => toMs(a.ts) - toMs(b.ts));
  const chunkSize = Math.max(10, Math.floor(sorted.length / 5));
  const chunks: ClosedTradeRow[][] = [];
  for (let i = 0; i < sorted.length; i += chunkSize) {
    chunks.push(sorted.slice(i, i + chunkSize));
  }
  const meetsMin = sorted.length >= MIN_TRADES_FOR_HEALTH && chunks.length >= 3;
  if (!meetsMin) {
    return {
      key: "consistency",
      label: "Consistency Across Trade History",
      score: null,
      weight: 15,
      meetsMinSample: false,
      description: `Only ${sorted.length} closed trade(s) so far - need at least ${MIN_TRADES_FOR_HEALTH} split across a few chunks of trade history to measure consistency.`,
    };
  }
  const chunkWinRates = chunks
    .map((c) => winRatePct(c))
    .filter((r): r is number => r != null);
  const sd = stdDev(chunkWinRates);
  const score = sd != null ? clamp(100 - sd * 2) : null;
  return {
    key: "consistency",
    label: "Consistency Across Trade History",
    score,
    weight: 15,
    meetsMinSample: true,
    description:
      sd != null
        ? `Win rate across ${chunks.length} sequential chunks of ~${chunkSize} trades varies by ${sd.toFixed(1)} percentage points (standard deviation) - lower means more consistent over time.`
        : "Could not compute win-rate variance across trade history chunks.",
  };
}

function marketAdaptationComponent(trades: ClosedTradeRow[]): HealthComponent {
  const regimes = Array.from(new Set(trades.map((t) => t.market_regime).filter((r): r is string => !!r)));
  const qualifying = regimes
    .map((regime) => ({ regime, trades: trades.filter((t) => t.market_regime === regime) }))
    .filter((g) => g.trades.length >= MIN_TRADES_PER_REGIME);

  if (qualifying.length < 2) {
    return {
      key: "market_adaptation",
      label: "Market Regime Adaptation",
      score: null,
      weight: 12.5,
      meetsMinSample: false,
      description: `Fewer than 2 market regimes have at least ${MIN_TRADES_PER_REGIME} trades yet - not enough spread of market conditions to assess adaptation.`,
    };
  }
  const winRates = qualifying.map((g) => winRatePct(g.trades)).filter((r): r is number => r != null);
  const sd = stdDev(winRates);
  const score = sd != null ? clamp(100 - sd * 2) : null;
  return {
    key: "market_adaptation",
    label: "Market Regime Adaptation",
    score,
    weight: 12.5,
    meetsMinSample: true,
    description:
      sd != null
        ? `Win rate across ${qualifying.length} market regimes (${qualifying.map((g) => g.regime).join(", ")}) varies by ${sd.toFixed(1)} percentage points - lower means the strategy holds up more evenly across different market conditions.`
        : "Could not compute win-rate variance across market regimes.",
  };
}

function historicalTrendComponent(allTrades: ClosedTradeRow[], recentCutoffIso: string): HealthComponent {
  const cutoffMs = toMs(recentCutoffIso);
  const recent = allTrades.filter((t) => toMs(t.ts) >= cutoffMs);
  const prior = allTrades.filter((t) => toMs(t.ts) < cutoffMs);

  if (recent.length < MIN_RECENT_TRADES || prior.length < MIN_RECENT_TRADES) {
    return {
      key: "historical_trend",
      label: "Historical Trend",
      score: null,
      weight: 12.5,
      meetsMinSample: false,
      description: `Need at least ${MIN_RECENT_TRADES} trades in the recent window and ${MIN_RECENT_TRADES} before it to compare - currently ${recent.length} recent vs ${prior.length} prior.`,
    };
  }
  const recentExp = meanPnlPct(recent)!;
  const priorExp = meanPnlPct(prior)!;
  const diff = recentExp - priorExp;
  // Each 1 percentage point of extra average P&L per trade shifts the score
  // by 10 points, capped at the 0-100 range - a heuristic, not a statistical
  // test (Pattern Discovery's holding-period/confidence findings are the
  // hypothesis-tested version of "is recent performance really different").
  const score = clamp(50 + diff * 10);
  return {
    key: "historical_trend",
    label: "Historical Trend",
    score,
    weight: 12.5,
    meetsMinSample: true,
    description:
      `Average P&L per trade in the recent window is ${recentExp >= 0 ? "+" : ""}${recentExp.toFixed(2)}% ` +
      `vs ${priorExp >= 0 ? "+" : ""}${priorExp.toFixed(2)}% before that - ` +
      `${diff >= 0 ? "trending better" : "trending worse"} (${diff >= 0 ? "+" : ""}${diff.toFixed(2)}pp).`,
  };
}

// ---- composite --------------------------------------------------------------

export function computeStrategyHealth(
  allTrades: ClosedTradeRow[],
  recentCutoffIso: string,
  equity: { maxDrawdownPct: number | null; sharpeRatio: number | null; dailyReturns: number[] }
): StrategyHealth {
  const totalTrades = allTrades.length;

  if (totalTrades === 0) {
    return { overallScore: null, confidenceLevel: "insufficient", totalTrades: 0, components: [] };
  }

  const components: HealthComponent[] = [
    winRateComponent(allTrades),
    sharpeComponent(equity.sharpeRatio, equity.dailyReturns.length + 1),
    drawdownComponent(equity.maxDrawdownPct, equity.dailyReturns.length + 1),
    stabilityComponent(equity.dailyReturns),
    consistencyComponent(allTrades),
    marketAdaptationComponent(allTrades),
    historicalTrendComponent(allTrades, recentCutoffIso),
  ];

  const computable = components.filter((c) => c.score != null);
  const totalWeight = computable.reduce((a, c) => a + c.weight, 0);
  const overallScore =
    totalWeight > 0
      ? clamp(computable.reduce((a, c) => a + (c.score as number) * c.weight, 0) / totalWeight)
      : null;

  const confidenceLevel: ConfidenceLevel =
    totalTrades >= MIN_TRADES_FOR_HEALTH * 4
      ? "high"
      : totalTrades >= MIN_TRADES_FOR_HEALTH
        ? "medium"
        : totalTrades > 0
          ? "low"
          : "insufficient";

  return { overallScore, confidenceLevel, totalTrades, components };
}
