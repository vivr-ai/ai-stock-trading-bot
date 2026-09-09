// Display-only copies of the bot's strategy thresholds (bot/config.py). Same
// pattern as lib/riskConfig.ts - the dashboard never makes trading decisions,
// it just needs these numbers to explain *why* a Hold happened in the AI
// Decision Log (see app/api/decisions/route.ts) and to describe the
// strategy consistently on the Strategy and Shadow vs Live pages.
//
// If you've customized any STRATEGY_* env vars on the bot service, set the
// same ones on this dashboard service and these will pick them up
// automatically. Otherwise they fall back to the bot's own built-in defaults
// (see bot/config.py's load_config for the source of truth on every default
// below).
function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function bool(name: string, fallback: boolean): boolean {
  const raw = process.env[name];
  if (!raw) return fallback;
  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

export const strategyConfig = {
  // Legacy / still-used-as-a-sell-rule thresholds. Note buyThreshold is no
  // longer a hard gate for Path A as of Strategy v2 - see momentumBuyScore
  // and the sentiment/headline/volume weights below instead.
  buyThreshold: num("STRATEGY_BUY_THRESHOLD", 8.0),
  sellThreshold: num("STRATEGY_SELL_THRESHOLD", -5.0),
  minHeadlines: num("STRATEGY_MIN_HEADLINES", 5),
  sellMinHeadlines: num("STRATEGY_SELL_MIN_HEADLINES", 3),
  maxIntradayRunupPct: num("STRATEGY_MAX_INTRADAY_RUNUP_PCT", 8.0),
  smaPeriod: num("STRATEGY_SMA_PERIOD", 20),
  minVolumeRatio: num("STRATEGY_MIN_VOLUME_RATIO", 1.5),

  // --- Strategy v2, Path A: weighted composite sentiment-momentum score ---
  sentimentWeight: num("STRATEGY_SENTIMENT_WEIGHT", 0.5),
  headlineWeight: num("STRATEGY_HEADLINE_WEIGHT", 0.2),
  volumeWeight: num("STRATEGY_VOLUME_WEIGHT", 0.3),
  momentumBuyScore: num("STRATEGY_MOMENTUM_BUY_SCORE", 0.6),

  // --- Strategy v2, Path B: RSI-2 mean-reversion (shadow mode by default) ---
  reversionEnabled: bool("STRATEGY_REVERSION_ENABLED", true),
  reversionLive: bool("STRATEGY_REVERSION_LIVE", false),
  rsiPeriod: num("STRATEGY_RSI_PERIOD", 2),
  rsiOversold: num("STRATEGY_RSI_OVERSOLD", 10.0),
  rsiExit: num("STRATEGY_RSI_EXIT", 65.0),
  reversionTrendSmaPeriod: num("STRATEGY_REVERSION_TREND_SMA_PERIOD", 200),
  reversionMinVolumeRatio: num("STRATEGY_REVERSION_MIN_VOLUME_RATIO", 1.3),
  reversionSentimentVeto: num("STRATEGY_REVERSION_SENTIMENT_VETO", -5.0),
  reversionMaxHoldDays: num("STRATEGY_REVERSION_MAX_HOLD_DAYS", 5),
};
