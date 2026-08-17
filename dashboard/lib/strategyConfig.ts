// Display-only copies of the bot's strategy thresholds (bot/config.py). Same
// pattern as lib/riskConfig.ts - the dashboard never makes trading decisions,
// it just needs these numbers to explain *why* a Hold happened in the AI
// Decision Log (see app/api/decisions/route.ts).
//
// If you've customized any STRATEGY_* env vars on the bot service, set the
// same ones on this dashboard service and these will pick them up
// automatically. Otherwise they fall back to the bot's own built-in defaults.
function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const strategyConfig = {
  buyThreshold: num("STRATEGY_BUY_THRESHOLD", 8.0),
  sellThreshold: num("STRATEGY_SELL_THRESHOLD", -5.0),
  minHeadlines: num("STRATEGY_MIN_HEADLINES", 5),
};
