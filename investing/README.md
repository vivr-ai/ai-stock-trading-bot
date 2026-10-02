# Investment portfolio tracker (v1 + v2)

A daily watch-list scan, plus intraday follow-up checks, for Vivek's
separate, long-term Yahoo-Finance-tracked investment portfolio - independent
of the Alpaca trading bot in `bot/`. No shared imports or state with `bot/`;
it's a second, small Railway service in the same project.

See the "Investment Portfolio Tracker" design doc in the ClaudeTrader project
for the full rules/thresholds this implements. Quick summary:

- **Daily scan** (v1, once near market open): every holding in
  `holdings.json` is checked against three rules - cost basis, trend
  pullback, relative-to-index underperformance (any one is enough, OR'd) -
  and anything that trips becomes "today's watch list", sent as one
  Telegram message.
- **Intraday checks** (v2, every 30 min during market hours): only the
  symbols on today's watch list are re-checked for RSI<35 (oversold) or a
  new intraday low at least 0.3% below the last one already flagged today,
  with a volume-pace caution note (>=1.7x the 20-day average) attached when
  either of those fires. Quiet rounds send nothing.
- **End-of-day nudge** (v2, once, 45 min before the ASX close): a reminder
  for anything still on today's watch list that never got an intraday
  alert, so it isn't missed just because nothing crossed the intraday
  threshold.

## Files

- `holdings.json` - your current holdings snapshot (symbol, exchange,
  benchmark index, shares, AC/share). Seeded from your Yahoo Finance export.
- `config.py` - reads env vars + `holdings.json`.
- `yahoo_client.py` - fetches ~1y of daily closes/volume, plus today's
  day_low/day_high/current_volume, from Yahoo's unofficial chart endpoint
  (works for ASX/NZX/US tickers alike).
- `indicators.py` - the stage 1-3 daily rules and OR-promotion logic, plus
  the RSI/average-volume math stage 4-5 build on.
- `intraday.py` - v2: holds today's watch list in memory (set once by
  `scan.py` after the daily scan), and runs the intraday checks / EOD
  nudge against just that list.
- `telegram_client.py` - this feature's own Telegram sender (separate bot
  from the trading bot's).
- `scan.py` - runs one daily scan; `python -m investing.scan` for a manual
  check. Also hands today's triggered holdings to `intraday.py`.
- `run_scheduler.py` - the long-running entrypoint: one persistent worker
  running three APScheduler cron jobs (daily scan, intraday check, EOD
  nudge), all with `timezone="Australia/Sydney"` (the same technique
  `bot/scheduler.py` already uses for the trading bot's own schedule) so
  they track the ASX open/close correctly through AEST/AEDT daylight-saving
  changes automatically. A single process is also what lets the intraday
  job see today's watch list without a shared file or database - it's just
  an in-memory module variable in `intraday.py`, set by the scan job and
  read by the other two in the same process.

## Updating holdings.json

It's a snapshot, not a transaction log - AC/share is already a weighted
average across every buy and every dividend reinvestment (same as what
Yahoo Finance shows you). When your share count or AC/share changes for any
reason (a DRIP event, a manual buy), just copy the two updated numbers from
Yahoo Finance into the matching entry here. Nothing else needs to change.

## Environment variables (set on the NEW Railway service, not the bot's)

| Variable | Required | Notes |
|---|---|---|
| `INVEST_TELEGRAM_BOT_TOKEN` | For alerts to send | From the bot you created via @BotFather for this feature - deliberately a different bot/token from the trading bot's `TELEGRAM_BOT_TOKEN`. |
| `INVEST_TELEGRAM_CHAT_ID` | For alerts to send | From `https://api.telegram.org/bot<TOKEN>/getUpdates` after messaging your bot once. |
| `INVEST_HOLDINGS_FILE` | No | Defaults to `investing/holdings.json` alongside this code. |
| `INVEST_MARKET_TIMEZONE` | No | Defaults to `Australia/Sydney`. |
| `INVEST_SCAN_HOUR` / `INVEST_SCAN_MINUTE` | No | Local time (in the timezone above) to run. Defaults to `10:15`, a little after the ASX/NZX open. |
| `LOG_LEVEL` | No | Defaults to `INFO`. |

The scan runs and logs its findings even without the Telegram vars set - it
just can't send the alert, so you can verify it's working from the Railway
logs before wiring up Telegram if you want to check it independently.

## Deploying as a new Railway service

This is a **second service** in the same Railway project as the bot and
dashboard, not a change to either of them:

1. In the Railway project, add a new service from the same GitHub repo
   (same source, same root directory - no separate repo needed).
2. Set its **Start Command** (Settings → Deploy) to:
   `python investing/run_scheduler.py`
3. Add the environment variables from the table above (Settings → Variables).
4. Deploy. Check the logs for `"Investment tracker scheduler started..."` -
   that confirms the schedule registered correctly. It'll then sit idle
   until the next weekday 10:15 Sydney time, or you can trigger a one-off
   check any time with `python -m investing.scan` via Railway's shell /
   a local run with the same env vars set.

## v2: intraday checks and EOD nudge

Built on the same worker/service as v1 - no new Railway service, no new
env vars. Nothing to configure beyond what's already in the table above;
the intraday cadence (every 30 min, 10:00-15:30 Sydney) and the EOD nudge
time (15:15 Sydney) are locked in `run_scheduler.py` rather than
env-configurable, since there's no real reason for them to vary per
deployment the way the daily scan's time might.

One thing worth knowing operationally: v2 adds Yahoo requests on top of
v1's - one fetch per watch-list symbol, every 30 minutes, only on days
where the watch list isn't empty. On a quiet day (nothing triggers the
daily scan) it adds nothing at all. If Yahoo's rate limiting (see
`yahoo_client.py`'s module docstring) becomes an issue again, this is the
first place extra request volume is coming from.
