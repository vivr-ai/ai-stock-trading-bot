-- Dashboard database schema (PostgreSQL)
--
-- This is the source of truth for the monitoring dashboard. The trading bot
-- writes to these tables in parallel with its existing CSV/JSON logs (which
-- are left untouched as a local fallback). The dashboard only ever reads
-- from this database - it never talks to the bot directly.
--
-- Apply with:
--   psql "$DATABASE_URL" -f db/schema.sql
-- or via the helper script:
--   python scripts/init_db.py

-- ---------------------------------------------------------------------
-- heartbeats: one row per scheduler tick (and per one-shot run), so the
-- dashboard can show "Running" vs "Stopped" from recency, plus a live
-- account snapshot without hitting Alpaca directly.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS heartbeats (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL,              -- 'running' | 'error'
    scheduler_status  TEXT NOT NULL,               -- 'scheduled' | 'one_shot' | 'eod'
    market_open       BOOLEAN,
    dry_run           BOOLEAN,
    portfolio_value   NUMERIC,
    cash              NUMERIC,
    equity            NUMERIC,
    buying_power      NUMERIC,
    open_positions    INTEGER,
    message           TEXT,
    api_latency_ms    NUMERIC,        -- Alpaca account_snapshot() round-trip time, for System Health
    trading_mode      TEXT,           -- 'paper' | 'dry_run' | 'live', from cfg.trading.mode
    daytrade_count    INTEGER,        -- Alpaca's own day-trade counter (PDT rule uses a 5-business-day window)
    pattern_day_trader BOOLEAN        -- Alpaca's own PDT-flagged status for this account
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_ts ON heartbeats (ts DESC);

-- Added by the System Health phase; ALTER here (rather than only in the
-- CREATE TABLE above) so this column also lands on databases where
-- heartbeats already existed before this column was introduced.
ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS api_latency_ms NUMERIC;

-- Added by the 3-mode / Live Readiness phase; same ALTER-for-existing-DBs
-- reasoning as api_latency_ms above.
ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS trading_mode TEXT;
ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS daytrade_count INTEGER;
ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS pattern_day_trader BOOLEAN;

-- ---------------------------------------------------------------------
-- decisions: EVERY decision the strategy makes each cycle, whether or not
-- it resulted in an order - buy, sell, hold/skip, or blocked. This is the
-- full AI Decision Log.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decisions (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol              TEXT NOT NULL,
    decision            TEXT NOT NULL,           -- 'buy' | 'sell' | 'hold' | 'buy_skipped' | 'buy_blocked' | 'sell_skipped' | 'scan' | 'block_new_entries' | 'error'
    reason              TEXT,                    -- machine reason code, e.g. 'below_sma', 'cooldown'
    sentiment_score     NUMERIC,
    sentiment_label     TEXT,
    headline_count      INTEGER,
    positive_headlines  INTEGER,
    negative_headlines  INTEGER,
    rationale           TEXT,                     -- plain-English sentiment rationale
    price               NUMERIC,
    sma                 NUMERIC,
    change_pct          NUMERIC,
    volume_ratio        NUMERIC,
    extra               JSONB                     -- anything else (sector, dry_run, etc.)
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions (symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_decision ON decisions (decision);

-- ---------------------------------------------------------------------
-- trades: one row per order the bot actually placed (mirrors trades.csv),
-- including dry-run rows.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    action              TEXT NOT NULL,            -- 'buy' | 'sell'
    symbol              TEXT NOT NULL,
    qty                 NUMERIC,
    price               NUMERIC,
    notional            NUMERIC,
    stop_price          NUMERIC,
    take_profit         NUMERIC,
    sentiment_score     NUMERIC,
    sentiment_label     TEXT,
    headline_count      INTEGER,
    positive_headlines  INTEGER,
    negative_headlines  INTEGER,
    reason              TEXT,
    rationale           TEXT,
    dry_run             BOOLEAN NOT NULL DEFAULT false,
    order_id            TEXT,
    status              TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades (ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);

-- ---------------------------------------------------------------------
-- closed_trades: one row per completed round-trip (mirrors closed_trades.csv)
-- - the source of truth for win rate / avg gain / avg loss / trade history.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS closed_trades (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),  -- exit time
    symbol            TEXT NOT NULL,
    qty                NUMERIC,
    entry_price       NUMERIC,
    exit_price        NUMERIC,
    pnl               NUMERIC,
    pnl_pct           NUMERIC,
    exit_reason       TEXT,
    entry_time        TIMESTAMPTZ,
    buy_reason        TEXT,        -- original reason/rationale captured at entry, joined from trades if available
    news_summary      TEXT
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_ts ON closed_trades (ts DESC);
CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON closed_trades (symbol);

-- ---------------------------------------------------------------------
-- portfolio_snapshots: periodic account snapshots for the equity curve /
-- performance charts (independent of Alpaca's own portfolio_history, which
-- can be unavailable on some SDK versions).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id                 BIGSERIAL PRIMARY KEY,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    portfolio_value    NUMERIC,
    cash               NUMERIC,
    equity             NUMERIC,
    buying_power       NUMERIC,
    unrealized_pl      NUMERIC,
    open_positions     INTEGER,
    exposure           NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON portfolio_snapshots (ts DESC);

-- ---------------------------------------------------------------------
-- open_positions: the CURRENT book, one row per held symbol, upserted every
-- cycle and deleted the moment a position closes. This is what the
-- Portfolio page and Home page read - not a history table.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS open_positions (
    symbol            TEXT PRIMARY KEY,
    qty               NUMERIC,
    avg_entry_price   NUMERIC,
    current_price     NUMERIC,
    market_value      NUMERIC,
    unrealized_pl     NUMERIC,
    unrealized_plpc   NUMERIC,
    allocation_pct    NUMERIC,      -- % of portfolio value
    ai_confidence     NUMERIC,      -- sentiment score at entry
    entry_reason      TEXT,         -- why the bot bought it
    entry_time        TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- notifications: alert history for the Notifications Centre.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    type        TEXT NOT NULL,     -- 'trade_executed' | 'error' | 'broker_issue' | 'bot_restart' | 'daily_summary' | 'weekly_summary'
    severity    TEXT NOT NULL DEFAULT 'info',  -- 'info' | 'warning' | 'critical'
    title       TEXT NOT NULL,
    message     TEXT,
    metadata    JSONB,
    read        BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications (ts DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications (type);

-- ---------------------------------------------------------------------
-- notification_settings: one row per notification type, editable from the
-- dashboard's Notification Settings page. `channel` controls the Telegram
-- delivery path; `enabled=false` mutes Telegram entirely for that type
-- (the notifications table audit trail is always written regardless).
-- The bot reads this table (bot/notifications/settings.py) with a ~60s
-- cache, so a change here takes effect on the bot's next cycle without a
-- redeploy.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_settings (
    type        TEXT PRIMARY KEY,
    channel     TEXT NOT NULL DEFAULT 'immediate',  -- 'immediate' | 'daily_summary' | 'weekly_summary' | 'off'
    enabled     BOOLEAN NOT NULL DEFAULT true,
    label       TEXT,          -- human-readable name for the settings UI
    description TEXT,          -- short explanation for the settings UI
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- fx_rates: cached RBA daily AUD/USD exchange rates, used by the
-- Accountant Export to convert each transaction leg (buy and sell
-- separately) to AUD at the rate applicable on its own date - the
-- ATO-correct method, not a single blended rate for the whole trade.
--
-- Convention: aud_usd_rate follows the RBA's own quoting convention
-- (Series ID FXRUSD in their F11 table), i.e. "1 AUD = aud_usd_rate USD".
-- To convert a USD amount to AUD: aud_amount = usd_amount / aud_usd_rate.
-- Populated lazily by the dashboard (dashboard/lib/fx.ts) the first time an
-- export needs a date range, then reused - reproducible even if RBA's site
-- is ever unreachable later.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fx_rates (
    rate_date     DATE PRIMARY KEY,
    aud_usd_rate  NUMERIC NOT NULL,
    source        TEXT NOT NULL DEFAULT 'RBA',
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO notification_settings (type, channel, enabled, label, description) VALUES
    ('bot_restart', 'immediate', true, 'Bot started / restarted', 'Fires whenever the process starts, including redeploys and crash-restarts.'),
    ('bot_stopped_unexpectedly', 'immediate', true, 'Bot stopped unexpectedly', 'Fires when the process exits due to an unhandled error, not a deliberate stop.'),
    ('deployment_completed', 'immediate', true, 'Railway deployment completed', 'Fires once a new Railway deploy finishes (release command).'),
    ('trade_executed', 'immediate', true, 'Trade executed', 'Every buy/sell/auto-exit the bot places.'),
    ('daily_summary', 'immediate', true, 'Daily trading summary', 'The end-of-day performance report.'),
    ('weekly_summary', 'immediate', true, 'Weekly performance summary', 'The Friday end-of-week rollup.'),
    ('daily_loss_limit', 'immediate', true, 'Daily loss limit reached', 'Fires once per day when the kill switch engages.'),
    ('broker_issue', 'immediate', true, 'Broker/API connection failure', 'Alpaca account snapshot or data calls failing after retries.'),
    ('database_failure', 'immediate', true, 'Database failure', 'Dashboard Postgres writes failing (trading itself is unaffected).'),
    ('scheduler_failure', 'immediate', true, 'Scheduler failure', 'The internal job scheduler crashed and is restarting itself.'),
    ('error', 'immediate', true, 'Critical application errors', 'Order failures, startup failures, and other unexpected errors.'),
    ('pdt_warning', 'immediate', true, 'Pattern Day Trader warning', 'Fires once per day when day-trade count is approaching the 4-in-5-business-days PDT threshold on an account under $25,000.')
ON CONFLICT (type) DO NOTHING;

-- ---------------------------------------------------------------------
-- config_status: one row per bot startup, recording which operating mode
-- and safety switches were resolved (never secret VALUES - only booleans
-- for "is this credential present") plus deploy metadata. The dashboard's
-- Live Readiness page reads the latest row, since it can only see what's in
-- Postgres - it has no way to read Railway's environment variables directly.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_status (
    id                    BIGSERIAL PRIMARY KEY,
    ts                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    trading_mode          TEXT NOT NULL,      -- 'paper' | 'dry_run' | 'live'
    live_confirmed        BOOLEAN NOT NULL DEFAULT false,
    risk_dry_run          BOOLEAN NOT NULL DEFAULT true,
    allow_submit          BOOLEAN NOT NULL DEFAULT false,  -- resolved AlpacaBroker.allow_submit
    has_paper_keys        BOOLEAN NOT NULL DEFAULT false,
    has_live_keys         BOOLEAN NOT NULL DEFAULT false,
    has_telegram          BOOLEAN NOT NULL DEFAULT false,
    has_database          BOOLEAN NOT NULL DEFAULT true,   -- always true if this row exists at all
    commit_short          TEXT,
    environment           TEXT
);
CREATE INDEX IF NOT EXISTS idx_config_status_ts ON config_status (ts DESC);

-- ---------------------------------------------------------------------
-- account_activities: dividends, regulatory fees, and non-resident
-- withholding tax, sourced from Alpaca's account activities endpoint
-- (activity types DIV / DIVNRA / FEE, among others). Populated by the
-- bot's daily EOD job once it's connected to a real (dry_run/live) Alpaca
-- account - paper accounts don't generate these. Upserted on Alpaca's own
-- activity id, so re-fetching an overlapping window is always safe.
-- Consumed directly by the dashboard's Accountant Export, replacing the
-- placeholder dividend/fee/withholding columns from the Phase 4 export.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS account_activities (
    activity_id     TEXT PRIMARY KEY,   -- Alpaca's own activity id
    activity_type   TEXT NOT NULL,      -- 'DIV' | 'DIVNRA' | 'FEE' | ...
    activity_date   DATE NOT NULL,
    symbol          TEXT,
    net_amount      NUMERIC,
    qty             NUMERIC,
    per_share_amount NUMERIC,
    description     TEXT,
    raw             JSONB,              -- full activity payload, for audit / future fields
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_account_activities_date ON account_activities (activity_date);
CREATE INDEX IF NOT EXISTS idx_account_activities_type ON account_activities (activity_type);
