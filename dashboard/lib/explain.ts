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
};

export function explainReason(reason: string | null | undefined): string {
  if (!reason) return "";
  return REASON_EXPLANATIONS[reason] ?? reason;
}

export function decisionLabel(decision: string): "Buy" | "Sell" | "Hold" {
  if (decision === "buy") return "Buy";
  if (decision === "sell") return "Sell";
  return "Hold";
}
