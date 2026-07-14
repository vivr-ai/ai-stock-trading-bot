// Pattern Discovery: mines ALL-TIME closed_trades for statistically
// meaningful relationships. This module is pure computation (no DB access -
// the API route fetches rows, this only analyses them), matching
// dashboard/lib/strategyAnalytics.ts's separation of concerns.
//
// Anti-overfitting stance (per explicit product requirement): every finding
// here is computed from the ENTIRE trade history, never just a recent
// window, and every finding is gated by a category-specific minimum sample
// size below which it is marked "insufficient" and excluded from anything
// resembling a conclusion. Category minimums vary deliberately - a
// per-symbol sample is naturally smaller than a per-sector one, so demanding
// the same n=30 everywhere would either hide every single-symbol signal or
// force sector-level findings down to a useless n=5 bar. See MIN_SAMPLE.

import type { ClosedTradeRow } from "./strategyAnalytics";
import {
  twoProportionZTest,
  welchMeanDiffTest,
  pearsonCorrelation,
  correlationPValue,
  mean,
} from "./statistics";

export type FindingCategory =
  | "confidence_threshold" | "sector" | "holding_period" | "symbol_underperformance"
  | "stop_loss" | "take_profit" | "volatility" | "position_sizing"
  | "sentiment_reasoning" | "news_source";

export type ConfidenceLevel = "insufficient" | "low" | "medium" | "high";

export type Finding = {
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
  raw?: Record<string, unknown>;
};

// Minimum sample size PER CATEGORY before a finding is reported as anything
// more than "insufficient data" - deliberately not uniform (see file header).
const MIN_SAMPLE: Record<FindingCategory, number> = {
  confidence_threshold: 30,
  sector: 30,
  holding_period: 30,
  symbol_underperformance: 15,
  stop_loss: 20,
  take_profit: 20,
  volatility: 30,
  position_sizing: 30,
  sentiment_reasoning: 30,
  news_source: 0,
};

const SIGNIFICANCE_ALPHA = 0.05;

function confidenceLevelFor(n: number, minSample: number): ConfidenceLevel {
  if (n < minSample) return "insufficient";
  if (n < minSample * 2) return "low";
  if (n < minSample * 4) return "medium";
  return "high";
}

function costBasis(t: ClosedTradeRow): number {
  return Number(t.entry_price) * Number(t.qty);
}

function holdingDays(t: ClosedTradeRow): number | null {
  if (!t.entry_time) return null;
  const entryMs = t.entry_time instanceof Date ? t.entry_time.getTime() : new Date(t.entry_time).getTime();
  const exitMs = t.ts instanceof Date ? t.ts.getTime() : new Date(t.ts).getTime();
  const days = (exitMs - entryMs) / 86_400_000;
  return Number.isFinite(days) && days >= 0 ? days : null;
}

function winRatePct(trades: ClosedTradeRow[]): number {
  if (trades.length === 0) return 0;
  return (trades.filter((t) => Number(t.pnl) > 0).length / trades.length) * 100;
}

// ---- 1. Confidence thresholds -------------------------------------------
export function confidenceThresholdFinding(trades: ClosedTradeRow[]): Finding {
  const scored = trades.filter((t) => t.confidence_score != null);
  const n = scored.length;
  const min = MIN_SAMPLE.confidence_threshold;
  if (n < min) {
    return {
      category: "confidence_threshold",
      title: "Confidence threshold vs. returns",
      description: `Only ${n} closed trade(s) have a recorded confidence score - need at least ${min} to compare confidence bands.`,
      sampleSize: n, baselineSampleSize: null, statisticalMethod: "two-proportion z-test / Welch's t-test",
      pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
    };
  }
  const scores = scored.map((t) => Number(t.confidence_score)).sort((a, b) => a - b);
  const median = scores[Math.floor(scores.length / 2)];
  const above = scored.filter((t) => Number(t.confidence_score) > median);
  const atOrBelow = scored.filter((t) => Number(t.confidence_score) <= median);
  if (above.length < 5 || atOrBelow.length < 5) {
    return {
      category: "confidence_threshold",
      title: "Confidence threshold vs. returns",
      description: `Confidence scores are too clustered (median ${median.toFixed(1)}) to form two comparable groups yet.`,
      sampleSize: n, baselineSampleSize: null, statisticalMethod: "two-proportion z-test / Welch's t-test",
      pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
    };
  }
  const winsAbove = above.filter((t) => Number(t.pnl) > 0).length;
  const winsBelow = atOrBelow.filter((t) => Number(t.pnl) > 0).length;
  const propTest = twoProportionZTest(winsAbove, above.length, winsBelow, atOrBelow.length);
  const pnlTest = welchMeanDiffTest(above.map((t) => Number(t.pnl_pct)), atOrBelow.map((t) => Number(t.pnl_pct)));
  const meetsMin = above.length >= min / 2 && atOrBelow.length >= min / 2;
  const isSignificant = meetsMin && propTest.pValue < SIGNIFICANCE_ALPHA;
  return {
    category: "confidence_threshold",
    title: `Confidence above ${median.toFixed(1)} vs. at/below`,
    description:
      `Positions entered with a confidence score above ${median.toFixed(1)} (n=${above.length}) had a ` +
      `${propTest.proportionA * 100 >= propTest.proportionB * 100 ? "higher" : "lower"} win rate ` +
      `(${(propTest.proportionA * 100).toFixed(1)}% vs ${(propTest.proportionB * 100).toFixed(1)}%, ` +
      `${propTest.diffPct >= 0 ? "+" : ""}${propTest.diffPct.toFixed(1)}pp) than those at or below it (n=${atOrBelow.length})` +
      (pnlTest ? `, and averaged ${pnlTest.diff >= 0 ? "+" : ""}${pnlTest.diff.toFixed(2)}pp P&L per trade.` : "."),
    sampleSize: n, baselineSampleSize: atOrBelow.length, statisticalMethod: "two-proportion z-test on win rate; Welch's t-test on P&L%",
    pValue: propTest.pValue, effectSize: propTest.diffPct, meetsMinSample: meetsMin, isSignificant,
    confidenceLevel: confidenceLevelFor(Math.min(above.length, atOrBelow.length), min / 2),
    raw: { median, propTest, pnlTest },
  };
}

// ---- 2. Sector performance -----------------------------------------------
export function sectorFindings(trades: ClosedTradeRow[]): Finding[] {
  const min = MIN_SAMPLE.sector;
  const sectors = Array.from(new Set(trades.map((t) => t.sector ?? "unknown")));
  const findings: Finding[] = [];
  for (const sector of sectors) {
    const inSector = trades.filter((t) => (t.sector ?? "unknown") === sector);
    const outSector = trades.filter((t) => (t.sector ?? "unknown") !== sector);
    if (inSector.length < min || outSector.length < 5) continue;
    const winsIn = inSector.filter((t) => Number(t.pnl) > 0).length;
    const winsOut = outSector.filter((t) => Number(t.pnl) > 0).length;
    const propTest = twoProportionZTest(winsIn, inSector.length, winsOut, outSector.length);
    const isSignificant = propTest.pValue < SIGNIFICANCE_ALPHA;
    findings.push({
      category: "sector",
      title: `Sector: ${sector}`,
      description:
        `${sector} trades (n=${inSector.length}) had a ${(propTest.proportionA * 100).toFixed(1)}% win rate ` +
        `vs ${(propTest.proportionB * 100).toFixed(1)}% for everything else (n=${outSector.length}), ` +
        `a ${propTest.diffPct >= 0 ? "+" : ""}${propTest.diffPct.toFixed(1)} percentage-point difference.`,
      sampleSize: inSector.length, baselineSampleSize: outSector.length,
      statisticalMethod: "two-proportion z-test",
      pValue: propTest.pValue, effectSize: propTest.diffPct, meetsMinSample: true, isSignificant,
      confidenceLevel: confidenceLevelFor(inSector.length, min),
      raw: { propTest },
    });
  }
  return findings;
}

// ---- 3. Holding period ----------------------------------------------------
const HOLDING_BUCKETS: { label: string; minDays: number; maxDays: number }[] = [
  { label: "< 1 day", minDays: 0, maxDays: 1 },
  { label: "1-3 days", minDays: 1, maxDays: 3 },
  { label: "3-7 days", minDays: 3, maxDays: 7 },
  { label: "7-14 days", minDays: 7, maxDays: 14 },
  { label: "14+ days", minDays: 14, maxDays: Infinity },
];

export function holdingPeriodFindings(trades: ClosedTradeRow[]): Finding[] {
  const min = MIN_SAMPLE.holding_period;
  const withDays = trades
    .map((t) => ({ t, days: holdingDays(t) }))
    .filter((x): x is { t: ClosedTradeRow; days: number } => x.days != null);
  const findings: Finding[] = [];
  for (const bucket of HOLDING_BUCKETS) {
    const inBucket = withDays.filter((x) => x.days >= bucket.minDays && x.days < bucket.maxDays).map((x) => x.t);
    const outBucket = withDays.filter((x) => !(x.days >= bucket.minDays && x.days < bucket.maxDays)).map((x) => x.t);
    if (inBucket.length < min || outBucket.length < 5) continue;
    const test = welchMeanDiffTest(
      inBucket.map((t) => Number(t.pnl_pct)), outBucket.map((t) => Number(t.pnl_pct))
    );
    if (!test) continue;
    const isSignificant = test.pValue < SIGNIFICANCE_ALPHA;
    findings.push({
      category: "holding_period",
      title: `Holding period: ${bucket.label}`,
      description:
        `Trades held ${bucket.label} (n=${inBucket.length}) averaged ${test.meanA >= 0 ? "+" : ""}${test.meanA.toFixed(2)}% P&L ` +
        `vs ${test.meanB >= 0 ? "+" : ""}${test.meanB.toFixed(2)}% for other holding periods (n=${outBucket.length}), ` +
        `win rate ${winRatePct(inBucket).toFixed(1)}% vs ${winRatePct(outBucket).toFixed(1)}%.`,
      sampleSize: inBucket.length, baselineSampleSize: outBucket.length,
      statisticalMethod: "Welch's t-test on P&L%",
      pValue: test.pValue, effectSize: test.diff, meetsMinSample: true, isSignificant,
      confidenceLevel: confidenceLevelFor(inBucket.length, min),
      raw: { test },
    });
  }
  return findings;
}

// ---- 4. Symbol underperformance -------------------------------------------
export function symbolUnderperformanceFindings(trades: ClosedTradeRow[]): Finding[] {
  const min = MIN_SAMPLE.symbol_underperformance;
  const symbols = Array.from(new Set(trades.map((t) => t.symbol)));
  const findings: Finding[] = [];
  for (const symbol of symbols) {
    const forSymbol = trades.filter((t) => t.symbol === symbol);
    const others = trades.filter((t) => t.symbol !== symbol);
    if (forSymbol.length < min || others.length < 5) continue;
    const winsSym = forSymbol.filter((t) => Number(t.pnl) > 0).length;
    const winsOther = others.filter((t) => Number(t.pnl) > 0).length;
    const propTest = twoProportionZTest(winsSym, forSymbol.length, winsOther, others.length);
    // Only surface symbols that are MATERIALLY underperforming - a >=10
    // percentage-point gap - to avoid flooding the list with every symbol
    // that's merely a bit below average.
    if (propTest.diffPct > -10) continue;
    findings.push({
      category: "symbol_underperformance",
      title: `${symbol} underperforming`,
      description:
        `${symbol} (n=${forSymbol.length}) has a ${(propTest.proportionA * 100).toFixed(1)}% win rate vs ` +
        `${(propTest.proportionB * 100).toFixed(1)}% for all other symbols (n=${others.length}), ` +
        `${propTest.diffPct.toFixed(1)} percentage points lower` +
        (propTest.pValue < SIGNIFICANCE_ALPHA
          ? " - statistically significant."
          : " - directional, not yet statistically significant at this sample size."),
      sampleSize: forSymbol.length, baselineSampleSize: others.length,
      statisticalMethod: "two-proportion z-test",
      pValue: propTest.pValue, effectSize: propTest.diffPct, meetsMinSample: true,
      isSignificant: propTest.pValue < SIGNIFICANCE_ALPHA,
      confidenceLevel: confidenceLevelFor(forSymbol.length, min),
      raw: { propTest },
    });
  }
  return findings.sort((a, b) => (a.effectSize ?? 0) - (b.effectSize ?? 0));
}

// ---- 5/6. Stop-loss / take-profit (heuristic proxy) -----------------------
// exit_reason doesn't literally distinguish a stop-loss fill from a
// take-profit fill (both are logged as "auto_exit (stop_loss_or_take_profit)"
// - see bot/trading/strategy.py's _log_auto_exit). This classifies by P&L
// sign instead (losses -> stop-loss proxy, gains -> take-profit proxy),
// which is a reasonable proxy given the bot's stop/take-profit percentages
// have opposite signs, but it IS a heuristic, not a direct read of which
// bracket leg fired - disclosed in every finding's description.
function isAutoExit(t: ClosedTradeRow): boolean {
  return (t.exit_reason ?? "").includes("auto_exit");
}

export function stopLossFinding(trades: ClosedTradeRow[]): Finding {
  const min = MIN_SAMPLE.stop_loss;
  const losers = trades.filter((t) => Number(t.pnl) < 0);
  const autoExitLosers = losers.filter(isAutoExit);
  const sentimentLosers = losers.filter((t) => !isAutoExit(t));
  const n = autoExitLosers.length;
  if (n < min || sentimentLosers.length < 5) {
    return {
      category: "stop_loss",
      title: "Stop-loss exit pattern",
      description: `Only ${n} loss(es) look stop-triggered so far (need ${min}) - not enough to compare against sentiment-driven exits.`,
      sampleSize: n, baselineSampleSize: sentimentLosers.length,
      statisticalMethod: "descriptive share + Welch's t-test (heuristic: losses classified as stop-triggered by P&L sign, not a literal exit-type field)",
      pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
    };
  }
  const test = welchMeanDiffTest(
    autoExitLosers.map((t) => Number(t.pnl_pct)), sentimentLosers.map((t) => Number(t.pnl_pct))
  );
  const shareOfLosses = (autoExitLosers.length / losers.length) * 100;
  return {
    category: "stop_loss",
    title: "Stop-loss exit pattern",
    description:
      `${autoExitLosers.length} of ${losers.length} losing trades (${shareOfLosses.toFixed(0)}%) appear to have been ` +
      `closed by the stop-loss bracket rather than the sentiment sell rule` +
      (test ? `, averaging ${test.meanA.toFixed(2)}% vs ${test.meanB.toFixed(2)}% for sentiment-driven losses.` : ".") +
      " Proxy classification (P&L sign), not a literal stop-vs-take-profit field - treat as directional, not conclusive.",
    sampleSize: n, baselineSampleSize: sentimentLosers.length,
    statisticalMethod: "descriptive share + Welch's t-test (heuristic proxy)",
    pValue: test?.pValue ?? null, effectSize: test?.diff ?? null, meetsMinSample: true,
    isSignificant: false, // deliberately never claimed significant - this is a proxy, not a direct measurement
    confidenceLevel: confidenceLevelFor(n, min) === "insufficient" ? "insufficient" : "low",
    raw: { shareOfLosses, test },
  };
}

export function takeProfitFinding(trades: ClosedTradeRow[]): Finding {
  const min = MIN_SAMPLE.take_profit;
  const winners = trades.filter((t) => Number(t.pnl) > 0);
  const autoExitWinners = winners.filter(isAutoExit);
  const sentimentWinners = winners.filter((t) => !isAutoExit(t));
  const n = autoExitWinners.length;
  if (n < min || sentimentWinners.length < 5) {
    return {
      category: "take_profit",
      title: "Take-profit exit pattern",
      description: `Only ${n} gain(s) look take-profit-triggered so far (need ${min}) - not enough to compare against sentiment-driven exits.`,
      sampleSize: n, baselineSampleSize: sentimentWinners.length,
      statisticalMethod: "descriptive share + Welch's t-test (heuristic proxy)",
      pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
    };
  }
  const test = welchMeanDiffTest(
    autoExitWinners.map((t) => Number(t.pnl_pct)), sentimentWinners.map((t) => Number(t.pnl_pct))
  );
  const shareOfWins = (autoExitWinners.length / winners.length) * 100;
  const cappedUpside = test != null && test.diff < 0;
  return {
    category: "take_profit",
    title: "Take-profit exit pattern",
    description:
      `${autoExitWinners.length} of ${winners.length} winning trades (${shareOfWins.toFixed(0)}%) appear to have been ` +
      `closed by the take-profit bracket` +
      (test ? `, averaging ${test.meanA.toFixed(2)}% vs ${test.meanB.toFixed(2)}% for sentiment-driven wins.` : ".") +
      (cappedUpside
        ? " Take-profit-triggered wins average SMALLER than sentiment-driven wins - the current take-profit level may be capping upside prematurely on the ones that would have run further, but this can't be confirmed without post-exit price data (see Phase 6 backtesting)."
        : "") +
      " Proxy classification (P&L sign), not a literal exit-type field.",
    sampleSize: n, baselineSampleSize: sentimentWinners.length,
    statisticalMethod: "descriptive share + Welch's t-test (heuristic proxy)",
    pValue: test?.pValue ?? null, effectSize: test?.diff ?? null, meetsMinSample: true,
    isSignificant: false,
    confidenceLevel: confidenceLevelFor(n, min) === "insufficient" ? "insufficient" : "low",
    raw: { shareOfWins, test },
  };
}

// ---- 7. Volatility / market regime ----------------------------------------
export function volatilityFindings(trades: ClosedTradeRow[]): Finding[] {
  const min = MIN_SAMPLE.volatility;
  const regimes = Array.from(new Set(trades.map((t) => t.market_regime ?? "unknown")));
  const findings: Finding[] = [];
  for (const regime of regimes) {
    if (regime === "unknown") continue;
    const inRegime = trades.filter((t) => (t.market_regime ?? "unknown") === regime);
    const outRegime = trades.filter((t) => (t.market_regime ?? "unknown") !== regime);
    if (inRegime.length < min || outRegime.length < 5) continue;
    const winsIn = inRegime.filter((t) => Number(t.pnl) > 0).length;
    const winsOut = outRegime.filter((t) => Number(t.pnl) > 0).length;
    const propTest = twoProportionZTest(winsIn, inRegime.length, winsOut, outRegime.length);
    findings.push({
      category: "volatility",
      title: `Market regime: ${regime.replace("_", " ")}`,
      description:
        `Trades entered during ${regime.replace("_", " ")} conditions (n=${inRegime.length}) had a ` +
        `${(propTest.proportionA * 100).toFixed(1)}% win rate vs ${(propTest.proportionB * 100).toFixed(1)}% otherwise ` +
        `(n=${outRegime.length}), a ${propTest.diffPct >= 0 ? "+" : ""}${propTest.diffPct.toFixed(1)}pp difference.`,
      sampleSize: inRegime.length, baselineSampleSize: outRegime.length,
      statisticalMethod: "two-proportion z-test",
      pValue: propTest.pValue, effectSize: propTest.diffPct, meetsMinSample: true,
      isSignificant: propTest.pValue < SIGNIFICANCE_ALPHA,
      confidenceLevel: confidenceLevelFor(inRegime.length, min),
      raw: { propTest },
    });
  }
  return findings;
}

// ---- 8. Position sizing effectiveness --------------------------------------
export function positionSizingFinding(trades: ClosedTradeRow[]): Finding {
  const min = MIN_SAMPLE.position_sizing;
  const pairs = trades
    .map((t) => ({ size: costBasis(t), pnlPct: Number(t.pnl_pct) }))
    .filter((p) => Number.isFinite(p.size) && p.size > 0 && Number.isFinite(p.pnlPct));
  const n = pairs.length;
  if (n < min) {
    return {
      category: "position_sizing",
      title: "Position sizing vs. outcome",
      description: `Only ${n} trade(s) with usable size/outcome data (need ${min}).`,
      sampleSize: n, baselineSampleSize: null, statisticalMethod: "Pearson correlation (size vs P&L%)",
      pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
    };
  }
  const r = pearsonCorrelation(pairs.map((p) => p.size), pairs.map((p) => p.pnlPct));
  const pValue = r != null ? correlationPValue(r, n) : null;
  const isSignificant = r != null && pValue != null && pValue < SIGNIFICANCE_ALPHA;
  const direction = r == null ? "no measurable relationship" : r > 0 ? "larger positions tended to do better" : "larger positions tended to do worse";
  return {
    category: "position_sizing",
    title: "Position sizing vs. outcome",
    description:
      `Across ${n} trades, the correlation between position size (cost basis) and P&L% is ` +
      `r=${r != null ? r.toFixed(3) : "n/a"} - ${direction}` +
      (isSignificant ? " (statistically significant)." : " (not statistically significant at this sample size)."),
    sampleSize: n, baselineSampleSize: null, statisticalMethod: "Pearson correlation, Fisher z-transform for p-value",
    pValue, effectSize: r, meetsMinSample: true, isSignificant,
    confidenceLevel: confidenceLevelFor(n, min),
    raw: { r },
  };
}

// ---- 9. Sentiment label / AI reasoning correlation -------------------------
export function sentimentReasoningFindings(trades: ClosedTradeRow[]): Finding[] {
  const min = MIN_SAMPLE.sentiment_reasoning;
  const labels = Array.from(new Set(trades.map((t) => t.confidence_label ?? "unknown")));
  const findings: Finding[] = [];
  for (const label of labels) {
    if (label === "unknown") continue;
    const inLabel = trades.filter((t) => (t.confidence_label ?? "unknown") === label);
    const outLabel = trades.filter((t) => (t.confidence_label ?? "unknown") !== label);
    if (inLabel.length < min || outLabel.length < 5) continue;
    const winsIn = inLabel.filter((t) => Number(t.pnl) > 0).length;
    const winsOut = outLabel.filter((t) => Number(t.pnl) > 0).length;
    const propTest = twoProportionZTest(winsIn, inLabel.length, winsOut, outLabel.length);
    findings.push({
      category: "sentiment_reasoning",
      title: `Sentiment label: ${label}`,
      description:
        `Trades entered with sentiment label "${label}" (n=${inLabel.length}) had a ` +
        `${(propTest.proportionA * 100).toFixed(1)}% win rate vs ${(propTest.proportionB * 100).toFixed(1)}% for other labels ` +
        `(n=${outLabel.length}).`,
      sampleSize: inLabel.length, baselineSampleSize: outLabel.length,
      statisticalMethod: "two-proportion z-test",
      pValue: propTest.pValue, effectSize: propTest.diffPct, meetsMinSample: true,
      isSignificant: propTest.pValue < SIGNIFICANCE_ALPHA,
      confidenceLevel: confidenceLevelFor(inLabel.length, min),
      raw: { propTest },
    });
  }
  return findings;
}

// ---- 10. News source predictive value --------------------------------------
// Not currently measurable: NEWS_PROVIDER is one global setting for the
// whole bot deployment (see bot/config.py), not recorded per-trade, so
// there is no per-trade variation to test. Reported honestly as a known
// limitation rather than fabricated or silently dropped.
export function newsSourceFinding(): Finding {
  return {
    category: "news_source",
    title: "News source predictive value",
    description:
      "Not currently measurable: the bot's news provider (NEWS_PROVIDER) is a single global setting " +
      "for the whole deployment, not recorded per-trade, so there's no per-trade variation to compare. " +
      "Would require the bot to record which provider supplied each trade's headlines - not implemented.",
    sampleSize: 0, baselineSampleSize: null, statisticalMethod: "n/a",
    pValue: null, effectSize: null, meetsMinSample: false, isSignificant: false, confidenceLevel: "insufficient",
  };
}

export function runPatternDiscovery(trades: ClosedTradeRow[]): Finding[] {
  return [
    confidenceThresholdFinding(trades),
    ...sectorFindings(trades),
    ...holdingPeriodFindings(trades),
    ...symbolUnderperformanceFindings(trades),
    stopLossFinding(trades),
    takeProfitFinding(trades),
    ...volatilityFindings(trades),
    positionSizingFinding(trades),
    ...sentimentReasoningFindings(trades),
    newsSourceFinding(),
  ];
}
