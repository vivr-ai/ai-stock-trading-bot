// Display-only copies of the bot's risk limits (bot/config.py). The dashboard
// never enforces these - the bot is the sole source of truth for actual
// trading decisions - it only shows them alongside the real, computed
// portfolio numbers so you can see how close the book is to each limit.
//
// If you've customized any RISK_* env vars on the bot service, set the same
// ones on this dashboard service and these will pick them up automatically.
// Otherwise they fall back to the bot's own built-in defaults.
function num(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const riskConfig = {
  maxPositionPct: num("RISK_MAX_POSITION_PCT", 5.0),
  maxOpenPositions: num("RISK_MAX_OPEN_POSITIONS", 10),
  maxTotalExposurePct: num("RISK_MAX_TOTAL_EXPOSURE_PCT", 50.0),
  maxNewPositionsPerCycle: num("RISK_MAX_NEW_POSITIONS_PER_CYCLE", 3),
  maxPositionsPerSector: num("RISK_MAX_POSITIONS_PER_SECTOR", 3),
  reentryCooldownHours: num("RISK_REENTRY_COOLDOWN_HOURS", 24.0),
  stopLossPct: num("RISK_STOP_LOSS_PCT", 10.0),
  takeProfitPct: num("RISK_TAKE_PROFIT_PCT", 20.0),
  dailyLossLimitPct: num("RISK_DAILY_LOSS_LIMIT_PCT", 4.0),
};
