// Strategy Intelligence's analytics engine: pure functions over closed_trades
// rows (plus the equity curve from portfolio_snapshots for Sharpe/drawdown).
// No DB access here on purpose - the API route fetches rows, this module
// only computes. Keeping the two separate makes it possible to unit-test
// this module later and reuse it from the Pattern Discovery engine (Phase 3)
// without duplicating the math.
//
// A note on "confidence": this bot's sentiment analyzer scores headlines on
// a -10..+10 scale (see bot/config.py's STRATEGY_BUY_THRESHOLD /
// STRATEGY_SELL_THRESHOLD), not a 0-100% probability. Entered trades only
// ever have a score >= buy_threshold (positive), so "confidence" breakdowns
// below bucket that same -10..+10 score, not a percentage - this is the
// actual scale the bot uses, not a stand-in for one.

export type ClosedTradeRow = {
  symbol: string;
  qty: number;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_pct: number;
  exit_reason: string | null;
  entry_time: string | Date | null;
  ts: string | Date; // exit time
  buy_reason: string | null;
  sector: string | null;
  confidence_score: number | null;
  confidence_label: string | null;
  market_regime: string | null;
  strategy_version: string | null;
};

export type PerformanceMetrics = {
  totalTrades: number;
  winRatePct: number | null;
  profitFactor: number | null;
  expectancy: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  avgHoldingPeriodDays: number | null;
  tradeFrequencyPerWeek: number | null;
  avgConfidenceScore: number | null;
};

export type BreakdownBucket = {
  key: string;
  trades: number;
  winRatePct: number | null;
  avgPnl: number | null;
  totalPnl: number;
  sufficientSample: boolean;
};

// Descriptive-stats threshold, deliberately looser than Phase 3's Pattern
// Discovery significance testing (n>=30 + a two-proportion z-test there).
// Here it just flags "too few trades to trust this row" on a chart.
const MIN_SAMPLE_FOR_BREAKDOWN = 20;

function toIso(value: string | Date): string {
  return value instanceof Date ? value.toISOString() : value;
}
function toMs(value: string | Date): number {
  return value instanceof Date ? value.getTime() : new Date(value).getTime();
}

function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

export function computeTradeMetrics(
  trades: ClosedTradeRow[],
  periodDays: number | null = null
): PerformanceMetrics {
  const total = trades.length;
  if (total === 0) {
    return {
      totalTrades: 0, winRatePct: null, profitFactor: null, expectancy: null,
      avgWin: null, avgLoss: null, avgHoldingPeriodDays: null,
      tradeFrequencyPerWeek: null, avgConfidenceScore: null,
    };
  }
  const wins = trades.filter((t) => Number(t.pnl) > 0);
  const losses = trades.filter((t) => Number(t.pnl) < 0);
  const winRatePct = (wins.length / total) * 100;
  const grossWin = wins.reduce((a, t) => a + Number(t.pnl), 0);
  const grossLoss = Math.abs(losses.reduce((a, t) => a + Number(t.pnl), 0));
  const profitFactor = grossLoss > 0 ? grossWin / grossLoss : null;
  const expectancy = mean(trades.map((t) => Number(t.pnl)));
  const avgWin = wins.length > 0 ? mean(wins.map((t) => Number(t.pnl))) : null;
  const avgLoss = losses.length > 0 ? mean(losses.map((t) => Number(t.pnl))) : null;

  const holdingDays = trades
    .filter((t) => t.entry_time != null)
    .map((t) => (toMs(t.ts) - toMs(t.entry_time as string | Date)) / 86_400_000)
    .filter((d) => Number.isFinite(d) && d >= 0);
  const avgHoldingPeriodDays = holdingDays.length > 0 ? mean(holdingDays) : null;

  const confidenceScores = trades
    .map((t) => t.confidence_score)
    .filter((s): s is number => s != null);
  const avgConfidenceScore = confidenceScores.length > 0 ? mean(confidenceScores) : null;

  const tradeFrequencyPerWeek =
    periodDays && periodDays > 0 ? (total / periodDays) * 7 : null;

  return {
    totalTrades: total, winRatePct, profitFactor, expectancy, avgWin, avgLoss,
    avgHoldingPeriodDays, tradeFrequencyPerWeek, avgConfidenceScore,
  };
}

export function breakdownBy(
  trades: ClosedTradeRow[],
  keyFn: (t: ClosedTradeRow) => string | null
): BreakdownBucket[] {
  const groups = new Map<string, ClosedTradeRow[]>();
  for (const t of trades) {
    const key = keyFn(t);
    if (key == null) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }
  const out: BreakdownBucket[] = [];
  for (const [key, rows] of groups.entries()) {
    const wins = rows.filter((r) => Number(r.pnl) > 0).length;
    const totalPnl = rows.reduce((a, r) => a + Number(r.pnl), 0);
    out.push({
      key,
      trades: rows.length,
      winRatePct: rows.length > 0 ? (wins / rows.length) * 100 : null,
      avgPnl: rows.length > 0 ? totalPnl / rows.length : null,
      totalPnl,
      sufficientSample: rows.length >= MIN_SAMPLE_FOR_BREAKDOWN,
    });
  }
  return out.sort((a, b) => b.trades - a.trades);
}

// ---- breakdown key functions --------------------------------------------

export function confidenceBucketKey(t: ClosedTradeRow): string | null {
  if (t.confidence_score == null) return null;
  const score = Number(t.confidence_score);
  const lower = Math.floor(score * 2) / 2; // 0.5-wide bins
  return `${lower.toFixed(1)} to ${(lower + 0.5).toFixed(1)}`;
}

export function sectorKey(t: ClosedTradeRow): string | null {
  return t.sector ?? "unknown";
}

export function symbolKey(t: ClosedTradeRow): string | null {
  return t.symbol;
}

const DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export function dayOfWeekKey(t: ClosedTradeRow): string | null {
  const ts = t.entry_time ?? t.ts;
  if (!ts) return null;
  const d = new Date(toIso(ts));
  return DOW_LABELS[d.getUTCDay()];
}

export function hourOfDayKey(t: ClosedTradeRow): string | null {
  const ts = t.entry_time ?? t.ts;
  if (!ts) return null;
  const d = new Date(toIso(ts));
  return `${String(d.getUTCHours()).padStart(2, "0")}:00 UTC`;
}

export function sentimentLabelKey(t: ClosedTradeRow): string | null {
  return t.confidence_label ?? "unknown";
}

export function marketRegimeKey(t: ClosedTradeRow): string | null {
  return t.market_regime ?? "unknown";
}

// ---- equity-curve-based metrics (Sharpe / drawdown) ----------------------
// Mirrors dashboard/app/api/performance/route.ts's math exactly, so the two
// pages never disagree about what "Sharpe ratio" or "max drawdown" means.

export type EquityPoint = { ts: string | Date; portfolio_value: number | null };

export function collapseToLastPerDay(points: EquityPoint[]): { key: string; value: number }[] {
  const map = new Map<string, number>();
  for (const p of points) {
    if (p.portfolio_value == null) continue;
    map.set(toIso(p.ts).slice(0, 10), p.portfolio_value);
  }
  return Array.from(map.entries()).map(([key, value]) => ({ key, value }));
}

export function pctReturns(series: { key: string; value: number }[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < series.length; i++) {
    const prev = series[i - 1].value;
    const cur = series[i].value;
    if (prev > 0) out.push(((cur - prev) / prev) * 100);
  }
  return out;
}

export function maxDrawdownPct(values: number[]): number | null {
  if (values.length === 0) return null;
  let peak = values[0];
  let worst = 0;
  for (const v of values) {
    if (v > peak) peak = v;
    if (peak > 0) worst = Math.max(worst, ((peak - v) / peak) * 100);
  }
  return worst;
}

export function sharpeRatio(dailyPctReturns: number[]): number | null {
  if (dailyPctReturns.length < 2) return null;
  const decimals = dailyPctReturns.map((p) => p / 100);
  const avg = decimals.reduce((a, b) => a + b, 0) / decimals.length;
  const variance = decimals.reduce((a, b) => a + (b - avg) ** 2, 0) / (decimals.length - 1);
  const stddev = Math.sqrt(variance);
  if (stddev === 0) return null;
  return (avg / stddev) * Math.sqrt(252);
}
