# Telegram Notifications — Setup

The bot can send Telegram messages for the events listed in the table below.
Every event is always recorded in the dashboard's Notifications Centre
regardless of Telegram; Telegram is an additional, more immediate channel on
top of that.

## 1. Create a Telegram bot (one-time, ~2 minutes)

1. In Telegram, search for **@BotFather** and open a chat with it.
2. Send `/newbot`.
3. Give it a name (anything, e.g. "My Trading Bot Alerts") and a username
   (must end in `bot`, e.g. `viv_trading_alerts_bot`).
4. BotFather replies with an HTTP API token that looks like
   `123456789:AAExampleTokenTextGoesHere`. This is your `TELEGRAM_BOT_TOKEN`
   — copy it somewhere safe (it's a secret; treat it like a password).

## 2. Get your chat ID

1. In Telegram, search for the bot you just created (by the username you
   picked) and send it any message, e.g. "hi".
2. In a browser, visit:
   `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   (replace `<YOUR_BOT_TOKEN>` with the token from step 1).
3. Look for `"chat":{"id":123456789, ...}` in the JSON response — that
   number (can be negative for group chats) is your `TELEGRAM_CHAT_ID`.
   If the response is empty, make sure you sent the bot a message first,
   then reload the URL.

## 3. Configure Railway

In Railway: your bot service → **Variables**, add:

```
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenTextGoesHere
TELEGRAM_CHAT_ID=123456789
```

That's it — notifications are auto-enabled once both are set. No redeploy
code change needed, just add the variables and Railway will restart the
service with them.

To verify: use Railway's shell to run `python main.py --check` — it sends a
one-off Telegram test message as part of the connectivity check, or wait for
the next "Bot started" message on the next deploy/restart.

To temporarily silence Telegram without deleting the credentials, set
`TELEGRAM_ENABLED=false`.

## 4. What gets sent, and when

| Event | Type (dashboard) | Default channel |
|---|---|---|
| Bot started / restarted | `bot_restart` | Immediate |
| Bot stopped unexpectedly | `bot_stopped_unexpectedly` | Immediate |
| Railway deployment completed | `deployment_completed` | Immediate |
| Trade executed (buy/sell/auto-exit) | `trade_executed` | Immediate |
| Daily trading summary | `daily_summary` | Immediate |
| Weekly performance summary | `weekly_summary` | Immediate |
| Daily loss limit reached | `daily_loss_limit` | Immediate |
| Broker/API connection failure | `broker_issue` | Immediate |
| Database failure | `database_failure` | Immediate |
| Scheduler failure | `scheduler_failure` | Immediate |
| Critical application errors | `error` | Immediate |

"Immediate" means sent to Telegram the moment it happens. Any type can
instead be set to **daily summary only**, **weekly summary only**, or
**off** (Telegram muted, still logged) from the dashboard's
**Notification Settings** page — no redeploy required, the bot picks up
changes within about a minute.

## 5. Database failure notifications are special

Every other notification type is written to Postgres first and then (if
configured "immediate") sent to Telegram. Database-failure notifications
obviously can't rely on the database that just failed — they bypass it and
go straight to Telegram, rate-limited to at most one alert per 15 minutes
plus one "recovered" message once writes succeed again. Trading itself is
never affected by a database outage; only the dashboard's data freshness is.

## 6. Applying the new `notification_settings` table

**You don't need to run anything manually.** `railway.toml`'s
`releaseCommand` now runs `scripts/apply_schema.py` automatically on every
deploy of the bot service — it (re-)applies `db/schema.sql` from inside
Railway's network, which is what makes this work even though `DATABASE_URL`
is Railway's internal hostname (`postgres.railway.internal`) and unreachable
from your Mac, and even on Railway's free tier without the Postgres Query
editor. `db/schema.sql` uses `CREATE TABLE IF NOT EXISTS` and
`ON CONFLICT DO NOTHING` throughout, so this is safe to run on every deploy —
it only adds what's missing and never touches existing data.

Just push/redeploy the bot service and the table will be there. You can
confirm it worked from Railway's deploy logs (the release phase, before the
"Starting" line): look for `apply_schema: db/schema.sql applied
successfully.` If it instead says `could not connect to DATABASE_URL`, check
that the bot service actually has `DATABASE_URL` set (Railway → bot service →
Variables → it should show as a reference to the Postgres plugin, not blank).

If you ever do need to run ad-hoc SQL against this database from your Mac
(free tier, no Query editor), the Railway CLI can proxy the connection
without exposing it publicly:

```bash
railway login
railway link                # select this project
railway connect postgres    # opens a psql shell straight to it
```
