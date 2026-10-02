# Investment portfolio tracker (v1)

A daily watch-list scan for Vivek's separate, long-term Yahoo-Finance-tracked
investment portfolio - independent of the Alpaca trading bot in `bot/`. No
shared imports or state with `bot/`; it's a second, small Railway service in
the same project.

See the "Investment Portfolio Tracker" design doc in the ClaudeTrader project
for the full rules/thresholds this implements. Quick summary: once daily,
near market open, every holding in `holdings.json` is checked against three
rules (cost basis, trend pullback, relative-to-index underperformance - any
one is enough) and anything that trips gets sent as a single Telegram
message. v2 (intraday RSI + day-low tracking, not built yet) will poll more
often during the day for whatever's on that day's list.

## Files

- `holdings.json` - your current holdings snapshot (symbol, exchange,
  benchmark index, shares, AC/share). Seeded from your Yahoo Finance export;
  **one row (`LOC.AX`) is flagged `"note"` as unconfirmed - check it before
  relying on alerts for it.**
- `config.py` - reads env vars + `holdings.json`.
- `yahoo_client.py` - fetches ~1y of daily closes/volume from Yahoo's
  unofficial chart endpoint (works for ASX/NZX/US tickers alike).
- `indicators.py` - the stage 1-3 rules and OR-promotion logic.
- `telegram_client.py` - this feature's own Telegram sender (separate bot
  from the trading bot's).
- `scan.py` - runs one scan; `python -m investing.scan` for a manual check.
- `run_scheduler.py` - the long-running entrypoint: fires the scan once a
  day, Mon-Fri, via APScheduler with `timezone="Australia/Sydney"` (the
  same technique `bot/scheduler.py` already uses for the trading bot's own
  schedule, just a different timezone/cadence) so it tracks the ASX open
  correctly through AEST/AEDT daylight-saving changes automatically.

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

## What's NOT in v1 (deferred to v2)

Intraday RSI, day's-low tracking, the volume caution flag, and the
end-of-day "last call" nudge - all specified in the design doc but not
implemented here. v1 is the daily cost-basis/trend/relative-performance
scan and the Telegram ping only.
