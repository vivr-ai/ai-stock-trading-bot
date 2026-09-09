// Maps the bot's internal reason codes to plain-English sentences for the
// AI Decision Log. Codes come from bot/trading/strategy.py and bot/trading/risk.py.
// Reasons not in this map (mostly risk.py's, which are already full sentences,
// or the dynamic "score X >= Y, N headlines" / "sentiment X < Y" strings) are
// shown as-is - they're already readable.
const REASON_EXPLANATIONS: Record<string, string> = {
  daily_loss_limit:
    "Paused: today's losses hit the daily limit, so no new positions are opened until tomorrow.",
  intraday_drop:
    "Paused: the broader market is down sharply today, so it's not opening new positions.",
  regime_data_unavailable:
    "Paused: couldn't confirm the market's overall trend, so it's playing it safe.",
  below_market_regime_sma:
    "Paused: the overall market is in a downtrend, so it's not buying into broad weakness.",
  too_few_headlines: "Not enough news coverage yet to be confident either way.",
  order_pending: "Already has an order in flight for this stock.",
  cooldown: "Sold this recently and is waiting a while before buying it again.",
  per_cycle_cap: "Already opened its maximum number of new positions this cycle.",
  sector_cap: "Already holds enough positions in this stock's sector.",
  runup:
    "The price already jumped since yesterday's close - the good news may already be priced in.",
  sma_unavailable: "Couldn't confirm the stock's price trend, so it's holding off.",
  below_sma:
    "The price hasn't confirmed the news yet - it's still below its recent average.",
  volume_unavailable: "Couldn't confirm today's trading volume yet.",
  low_volume: "Not enough trading activity today to confirm the move.",

  // ---- Strategy v2, Path A: weighted composite score -----------------
  low_composite_score:
    "The combined score (news, headline coverage, and volume, weighted together) didn't clear the buy bar this cycle.",

  // ---- Strategy v2, Path B: RSI-2 mean-reversion (shadow mode) --------
  trend_sma_unavailable:
    "Couldn't confirm this stock's long-term trend, so the mean-reversion signal is skipped (fail-closed).",
  rsi_unavailable:
    "Couldn't compute the short-term RSI reading yet, so the mean-reversion signal is skipped (fail-closed).",
  sentiment_veto:
    "This looks like an oversold dip, but the news behind it is bad enough that it's treated as a real decline, not a bounce worth buying.",
};

export function explainReason(reason: string | null | undefined): string {
  if (!reason) return "";
  return REASON_EXPLANATIONS[reason] ?? reason;
}

export type DecisionLabel = "Buy" | "Sell" | "Hold" | "Shadow Buy" | "Shadow Close";

export function decisionLabel(decision: string): DecisionLabel {
  if (decision === "buy") return "Buy";
  if (decision === "sell") return "Sell";
  // Strategy v2, Path B shadow mode: these are never real orders - labeled
  // distinctly so they don't read as an ordinary Hold in the Decision Log.
  if (decision === "reversion_shadow_buy") return "Shadow Buy";
  if (decision === "reversion_shadow_exit") return "Shadow Close";
  return "Hold";
}

// True for any decision that represents a Path B shadow-mode event (no real
// order was placed either way) - used by the Decision Log to style these
// rows distinctly from genuine Buy/Sell/Hold outcomes.
export function isShadowDecision(decision: string): boolean {
  return decision === "reversion_shadow_buy" || decision === "reversion_shadow_exit";
}
