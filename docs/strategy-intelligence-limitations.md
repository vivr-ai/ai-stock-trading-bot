# Strategy Intelligence: known limitations & future revisit list

This is a running log of every simplification, heuristic, or scope boundary
made while building the Strategy Intelligence & Continuous Improvement
layer (Phases 1-6+). Each item was a deliberate choice given what the
current schema records, not an oversight - but they're worth revisiting as
more trade history accumulates or if the schema is extended. Nothing here
blocks using the feature today; it's a map of "where the numbers are exact
vs. where they're a reasonable approximation."

Also mirrored as a "Known Limitations" panel on the Strategy Intelligence
dashboard page (`/strategy-intelligence`) so it's visible without digging
into the repo.

## Phase 1 - Performance Analytics

- **Market regime classification is a heuristic, not a real volatility
  model.** It's derived from SPY's own daily `change_pct` (volatility proxy)
  and price-vs-SMA (trend proxy) - see `bot/trading/strategy.py`'s
  `_compute_market_regime()`. It is NOT a dedicated realized-volatility or
  VIX-based classifier. Revisit if: regime tags start looking wrong in
  practice, or a real vol data source becomes available.
- **"Confidence score" is the bot's raw sentiment score (-10 to +10), not a
  0-100% probability.** The bot only ever enters trades with a score above
  `STRATEGY_BUY_THRESHOLD` (positive), so all confidence-based breakdowns
  bucket that same scale. This was a deliberate choice not to fabricate a
  fake percentage scale.
- **Trade context (sector, confidence, regime, strategy version) is captured
  at ENTRY time, not re-evaluated at exit.** This is correct for attribution
  purposes (conditions at entry drove the decision) but means a trade that
  spans a regime change or strategy version bump is attributed to whatever
  was active when it opened.

## Phase 3 - Pattern Discovery

- **News source predictive value is not measurable at all.** `NEWS_PROVIDER`
  is one global setting for the whole bot deployment, not recorded
  per-trade. Revisit if: the bot is changed to record which provider
  supplied each trade's headlines.
- **Stop-loss vs. take-profit exit classification is a proxy, not a direct
  read.** `exit_reason` doesn't literally distinguish which bracket leg
  fired, so trades are classified as "stop-loss" or "take-profit" purely by
  P&L sign (loss = stop, gain = take-profit). Findings from this category
  are deliberately never marked "statistically significant," only
  directional.
- **p-values are approximated via the normal distribution** (via Fisher
  z-transform for correlations), not the exact t-distribution. This is a
  standard, safe approximation once each group has ~30+ observations - which
  is also this project's own minimum sample bar, so it's never relied on
  below that range.
- **Minimum sample sizes vary by category** (30 general, 15 for individual
  symbols, 20 for exit-pattern proxies) - findings below the bar are marked
  "insufficient" and hidden by default rather than presented as conclusions.
- **Symbol underperformance is only flagged when the gap is large** (≥10
  percentage points of win rate vs. all other symbols), not for every
  symbol that's merely a bit below average - intentional, to avoid noise.

## Phase 4 - AI Research Assistant

- **Output quality is bounded by Pattern Discovery's findings** - the model
  is instructed to return an empty list rather than invent a recommendation
  when nothing qualifies, and to cite a specific finding for every
  recommendation it does produce.
- **`proposedConfigChange` is only populated for 4 mechanically simple rule
  types** (confidence threshold, sector exclusion, symbol exclusion, regime
  restriction). Most recommendations won't have one, and won't be
  backtestable via Phase 6 as a result - they still need manual review.

## Phase 5 - Strategy Health Score

- **Sample size is surfaced as a confidence label, not used to numerically
  deflate the score.** Doing the latter would make "not enough data yet"
  look identical to "this strategy is performing badly," which is
  misleading. Instead every component carries `meetsMinSample` and the page
  shows an overall confidence tier.
- **Several scaling factors are hand-tuned heuristics, not industry
  standards** - e.g. Sharpe-to-score mapping (`50 + sharpe*25`), drawdown
  penalty (`100 - drawdown*4`), equity-curve-stability penalty
  (`100 - stddev*15`). These were chosen to produce sensible-looking scores
  for a single-account swing-trading bot, not derived from a benchmark.
  Revisit once there's enough real history to see if the tuning holds up.
- **"Historical trend" compares mean P&L% over the last 30 days vs.
  everything before that** using a simple heuristic multiplier
  (`50 + diff*10`), not a hypothesis test. Pattern Discovery's
  holding-period/confidence findings are the statistically-tested version
  of "is recent performance really different."

## Phase 6 - Backtesting & Simulation

- **Can only simulate trade-FILTERING rule changes** - confidence
  threshold, sector exclusion, symbol exclusion, market-regime restriction -
  by replaying which historical trades would have been kept or excluded,
  using their actual realized entry/exit outcomes.
- **Cannot simulate a different stop-loss/take-profit level or different
  position sizing.** That would require the intraday price path between
  entry and exit, which isn't recorded anywhere in the schema. These change
  types are explicitly marked "not simulable" rather than faked with a
  number. Revisit if: the bot starts recording intraday price snapshots or
  integrates a historical price data provider.
- **The backtest's "max drawdown" and "risk-adjusted ratio" are synthetic**,
  built from the trade subset's own P&L sequence in trade order - not the
  same thing as the real portfolio equity curve's Sharpe/drawdown shown on
  the Performance and Strategy Health pages. Labelled differently in the UI
  on purpose to avoid conflating the two.
- **Trades with no recorded confidence score are conservatively excluded**
  from the "proposed" set when backtesting a `min_confidence` change (can't
  verify they'd clear a numeric bar), but stay in the baseline.

## Phase 7 - Monthly Research Report automation

- **The automatic monthly trigger lives in the Python bot's scheduler, not
  the dashboard.** The bot calls the dashboard's `/api/monthly-report` route
  over HTTP once a month (1st, 06:00 market-timezone) and relays the result
  to Telegram via the existing notification pipeline - this avoids
  duplicating the TypeScript analytics logic in Python, but does mean the
  dashboard alone can't self-schedule; it always needs the bot (or a manual
  click) to trigger a new report. The dashboard's own "Generate Report Now"
  button always works standalone.
- **`sent_via_telegram` is not currently updated by the round trip.** The
  bot decides independently (via `notification_settings`) whether to
  actually send the summary to Telegram after the report is generated, but
  it doesn't currently report that outcome back to the dashboard, so this
  column stays `false` even when a message was sent. Cosmetic only - the
  report content itself is correct either way. Revisit if: this flag needs
  to be accurate for an audit trail.
- **If the Anthropic API call fails, the report falls back to a short,
  data-only summary** (no AI narrative) rather than failing the whole
  report - so a transient API outage doesn't mean no report for the month,
  just a blander one.
- **Internal service auth for the bot's scheduled call is a single shared
  secret** (`DASHBOARD_INTERNAL_API_KEY`), not a scoped token - it grants
  the same POST access as a real dashboard session. Fine for a
  single-operator project; would need real scoping if this dashboard ever
  had multiple bot instances or external callers.

## Dashboard redesign - Bot control (pause / resume / Emergency Stop)

- **Pause only blocks NEW entries, not liquidation.** "Emergency Stop" uses
  the exact same mechanism as Pause, just with more urgent framing/reason
  text - it does NOT close open positions. Sentiment-driven sells and
  broker-side stop-loss/take-profit brackets keep managing existing
  positions while paused, so pausing never leaves a position unprotected.
  A dedicated liquidating "Kill Switch" is out of scope here and listed as
  a possible future page.
- **The bot reads `bot_control` on up to a 60-second cache**, the same
  pattern as `bot/strategy_version.py`'s active-version read. A pause set
  on the dashboard takes effect on the bot's current or next cycle, not
  instantly mid-cycle.
- **A database outage fails OPEN (not paused), not fail-closed.** If the
  bot can't reach Postgres to check `bot_control`, it keeps trading as
  before rather than treating the outage itself as an implicit pause. This
  mirrors how every other dashboard-persistence failure in this project is
  handled (best-effort, never blocks trading) - a DB hiccup and a deliberate
  human pause are different events and are not conflated.
- **Pausing/resuming from the dashboard writes a `notifications` row
  directly (TypeScript), but does not itself send a Telegram alert.** Only
  the Python bot's `NotificationService` sends Telegram, and it does so the
  next time `run_cycle()` notices the state changed. In the (rare) case the
  bot's process is fully stopped or never runs again after a pause is set,
  no Telegram alert for that pause will go out - the dashboard's own
  Notifications feed and Home status strip still reflect it immediately,
  since that write happens synchronously in the API route.

## Dashboard redesign - Home page & navigation

- **"Next Scheduled Run" and "Time Until Next Session" are heuristics, not
  reads from Alpaca or the bot's actual scheduler process.** The dashboard
  has no direct connection to either - `/api/home` computes both from
  known, hard-coded assumptions (bot/scheduler.py's default cron: weekdays,
  :00/:30 marks, 09:00-16:00 America/New_York; a plain Mon-Fri 9:30-16:00
  regular session). Neither accounts for market holidays, early closes, or
  a customized `SCHEDULE_RUN_MINUTES` env var. Both are labelled "Estimate"
  in the UI. Revisit if: the dashboard gains a real market-calendar data
  source, or the bot starts reporting its own next-run time directly.
- **"Market Sentiment" on Home is just the most recent decision's sentiment
  label**, not an aggregate across symbols or a time window. A single
  outlier headline can swing this label. Revisit if: a proper aggregation
  (e.g. mean sentiment across the last full cycle's scans) is wanted here
  instead.
- **"Win Rate" on Home is all-time**, matching this project's standing
  "don't overfit to a recent streak" principle - deliberately not a
  trailing-N-trades number, even though that's a common pattern elsewhere.
- **"Current Risk Level" (normal/elevated/high) is a new, small derived
  heuristic**, not a risk-industry-standard score - it combines daily P/L
  vs. the daily loss limit and total exposure vs. the configured exposure
  cap using hand-picked thresholds (70%/80% of each limit = "elevated"),
  same spirit as Strategy Health's confidence tiers. Revisit once there's
  more real history to see if the thresholds hold up.
- **The command palette (⌘K) uses a small hand-written substring/
  subsequence matcher, not a fuzzy-search library.** Deliberately minimal
  per the approved proposal - fine at ~20 pages today and the ~50-page
  target; revisit if search quality noticeably degrades at a much larger
  page count.
- **Section/page renames (e.g. "Strategy Intelligence" -> "Learning &
  Insights") are DISPLAY LABELS ONLY** - see dashboard/lib/navigation.ts.
  Every underlying route/URL is unchanged, so no bookmarks or links break.
- **`dashboard/components/Nav.tsx` (the old flat link list) could not be
  deleted from this environment** and is left in the repo with a
  `SUPERSEDED` comment at the top - nothing imports it anymore (replaced by
  Sidebar.tsx + SectionTabs.tsx + MobileSectionBar.tsx). Safe to delete the
  file next time you're editing locally.

## Cross-cutting, applies to the whole layer

- The research/analytics layer is read-only with respect to trading
  behaviour. The bot only ever *reads* the active strategy version
  (`bot/strategy_version.py`); it never writes one. Every recommendation
  requires explicit human approval, and a separate explicit "deploy new
  version" action, before anything the bot does changes.
- All findings and recommendations are computed from **ALL-TIME** data by
  default, not a recent window, to avoid overfitting to a lucky or unlucky
  recent streak.
