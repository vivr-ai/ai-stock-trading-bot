# Deploying to Railway

The bot no longer needs `config.ini` to start. All configuration comes from
environment variables (config.ini is still supported as an optional local-dev
convenience — see README.md — but Railway should use env vars only).

## 1. Required variables

At minimum, set these two in Railway (Project -> your service -> Variables):

```
ALPACA_API_KEY=<your paper key>
ALPACA_SECRET_KEY=<your paper secret>
```

That's "simplified mode": static universe, Alpaca's free news feed, offline
lexicon sentiment. Everything else has a working default. The full list of
variables, with defaults and comments, is in `.env.example` — copy whichever
ones you want to override into Railway's dashboard.

`RISK_DRY_RUN` defaults to `true`. Leave it there until you've watched a few
cycles of logs and are comfortable with what it's doing; then set it to
`false` in Railway's dashboard to place real (paper) orders.

## 2. Persistent storage — read this before your first deploy

**Railway's filesystem is ephemeral.** Anything the bot writes to disk
(`logs/state.json`, `logs/trades.csv`, `logs/closed_trades.csv`, the daily
report files) is wiped on every redeploy and every restart, unless you attach
a Volume.

Without a Volume:
- Re-entry cooldowns reset on every deploy (a stock you just sold could be
  re-bought immediately after a redeploy).
- The performance report's win rate / avg gain / avg loss / trade history
  resets to empty on every deploy.
- The "detect a bracket stop/take-profit fired between cycles" logic still
  *works* (it compares live Alpaca positions to what was held last cycle),
  but the realized-P/L ledger behind it will be incomplete after a restart.

**Fix:** in Railway, go to your service -> Volumes -> Add Volume, mount it at
e.g. `/data`, then set:

```
TRADE_LOG_PATH=/data/trades.csv
CLOSED_TRADES_PATH=/data/closed_trades.csv
DAILY_SUMMARY_PATH=/data/daily_summary.log
REPORT_DIR=/data/reports
RUN_LOG_PATH=/data/bot.log
STATE_PATH=/data/state.json
```

## 3. Service type: Worker vs. Web Service

This bot doesn't serve HTTP traffic — it's a background process. Railway's
**Worker** service type is the natural fit and needs nothing extra.

If you instead deploy it as a **Web Service** (also fine — some Railway
setups default to this), Railway will set a `$PORT` env var and expects
something to answer on it. The bot detects `$PORT` automatically and starts a
trivial health endpoint (`GET /` -> `200 ok`) in a background thread so the
deploy isn't marked unhealthy. You don't need to configure anything for this
— it only activates if `PORT` is present.

## 4. Logs

Set `LOG_FORMAT=json` in Railway for structured, queryable log lines (each
scan and trade decision becomes a JSON object with fields like `symbol`,
`decision`, `sentiment_score`, `reason`). Leave it as the default `text` if
you'd rather read plain sentences in the Railway log viewer.

## 5. Verifying a deploy

After deploying, use Railway's log stream to confirm you see a line like:

```
Starting | universe=static news=alpaca sentiment=lexicon | DRY RUN (no orders submitted) | config source: environment variables only
```

If instead you see `Configuration error: Missing required configuration:
ALPACA_API_KEY, ALPACA_SECRET_KEY`, the two required variables above aren't
set (or aren't set on the right environment/service).

To run a one-off connectivity check without waiting for the schedule, use
Railway's shell (`railway run python main.py --check`) or trigger a one-time
deploy with the start command temporarily overridden to
`python main.py --check`.

## 6. Restart behavior

`railway.toml` sets `restartPolicyType = "ON_FAILURE"` as a last-resort net.
In practice the bot shouldn't need it: every external API call retries with
backoff before giving up (`bot/utils/retry.py`), each symbol's processing is
isolated so one bad symbol can't take down a cycle, and the scheduler itself
restarts on an unexpected internal crash without exiting the process (see
`bot/scheduler.py`). Railway's restart policy exists purely as insurance
against something truly unanticipated (e.g. an OOM kill).


## One-time cleanup before your first push

The current GitHub repo has `logs/state.json` and `logs/run.out` committed
from earlier local runs. Nothing secret is in them, but they shouldn't be
tracked (the updated `.gitignore` now excludes all of `logs/` except a
`.gitkeep` placeholder). Untrack them once:

```bash
git rm --cached logs/state.json logs/run.out
git commit -m "Stop tracking runtime log/state files"
```
