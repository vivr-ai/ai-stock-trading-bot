# Sell strategy v3: two-tier sentiment exit + momentum time-based exit

Design note for the sell-side changes referenced from `bot/config.py` and
`bot/trading/strategy.py` (`SentimentStrategy._sentiment_exit_reason`,
`_momentum_time_exit_reason`, `_resolve_lot`). A fuller, plain-English
write-up with worked examples exists as a "Sell Strategy Recommendation"
doc from the same conversation this shipped in - this file is the
engineering-facing summary that stays in the repo.

## Where this came from

Strategy v2 gave the buy side a weighted composite score (sentiment +
headlines + volume, partial credit instead of a hard AND-gate). The sell
side was never touched: `_do_sell`'s sentiment leg stayed a flat threshold
(`score < sell_threshold` AND `headlines >= sell_min_headlines`, or nothing
happens). Three things surfaced why that gap mattered:

1. **ORCL, 2026-09-22**: sentiment likely qualified for a sell, but with
   only 2 headlines behind it (below the 3-headline minimum) the bot logged
   "leaving price bracket to manage it" and took no software action at all.
2. A guard bug (fixed 2026-09-24, see git history) froze every software
   exit for four days - unrelated to which rule is right, but a reminder
   the sell path has more moving parts than the buy path.
3. `bot/state.py`'s open-lot record - including `entry_path`, which decides
   *which* exit rule applies - lives in a plain local file that a Railway
   redeploy wipes. Not biting anything today (Path B/mean-reversion isn't
   live), but a real risk once it is.

## What was deliberately NOT done

A weighted composite *sell* score, mirroring the buy side. Considered and
rejected: it would trade a fast, single-reason trigger for something
slower and harder to explain, in exchange for nuance an exit decision
doesn't obviously need. Systematic-trading practice generally keeps exits
simpler and quicker to fire than entries - missing a good entry costs an
opportunity, missing a bad exit costs money already at risk. Revisit only
if real data (once the changes below have run a while) shows the plain
threshold is actually missing exits or firing on noise - not before.

## What changed

- **Two-tier sentiment exit** (`_sentiment_exit_reason`): a moderate
  reading (`< sell_threshold`, default -5.0) still needs
  `sell_min_headlines` (default 3) headlines to act on. A SEVERE reading
  (`<= sell_severe_threshold`, default -8.0, config `STRATEGY_SELL_SEVERE_THRESHOLD`)
  is trusted with less confirmation - it can fire on as little as 1
  headline. Validated: `sell_severe_threshold` must be `<= sell_threshold`
  (a less-negative severe bound would invert the intent).
- **Momentum time-based exit** (`_momentum_time_exit_reason`): a
  sentiment-momentum position that never hits its stop-loss, take-profit/
  trailing-stop, or the sentiment exit closes after `momentum_max_hold_days`
  (default 10, config `STRATEGY_MOMENTUM_MAX_HOLD_DAYS`, 0 disables it) -
  mirrors `reversion_max_hold_days`, which mean-reversion positions already
  had. Frees a position/sector slot from a trade going nowhere instead of
  leaving the bracket as the only way out, indefinitely.
- **Durable lot resolution** (`_resolve_lot`): when `bot/state.py`'s local
  open-lot record is missing (redeploy wiped it), recovers `entry_path` and
  `entry_time` from the most recent `trades` row for that symbol
  (`Recorder.get_last_buy_trade`, which now also selects `entry_path`)
  instead of silently defaulting to `sentiment_momentum` with no
  `entry_time` at all. Best-effort - falls back to the old "return None"
  behavior if the DB has no matching row (e.g. a manually-opened position).

## Rollout

No new config is turned on by force - both new thresholds have real
defaults and take effect immediately, same as any other strategy config
change. If you'd rather observe before it can act, run for a while with
`STRATEGY_MOMENTUM_MAX_HOLD_DAYS=0` (disables the time-based exit) and
`STRATEGY_SELL_SEVERE_THRESHOLD` set equal to `STRATEGY_SELL_THRESHOLD`
(collapses the two-tier rule back to the old single threshold), then
tighten once you've seen a few cycles of decisions logs.

## Nothing else changed

The stop-loss (-10%), the take-profit/trailing-stop layer, the
mean-reversion path's own RSI-recovery/max-hold exit, and the buy side are
all untouched.
