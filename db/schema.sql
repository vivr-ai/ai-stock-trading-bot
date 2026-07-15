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

-- Added by the Strategy Intelligence phase - "current market regime" on the
-- dashboard reads the latest heartbeat's regime, independent of whether a
-- trade happened to be entered this cycle.
ALTER TABLE heartbeats ADD COLUMN IF NOT EXISTS market_regime TEXT;

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
    status              TEXT,
    sector              TEXT,        -- from bot/universe/static_universe.sector_of(), for Strategy Intelligence breakdowns
    market_regime       TEXT         -- 'bull' | 'bear' | 'sideways' | 'high_volatility' | 'low_volatility', see strategy.py
);
CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades (ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);

-- Added by the Strategy Intelligence phase; ALTER-for-existing-DBs, same
-- reasoning as api_latency_ms above.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sector TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS market_regime TEXT;

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
    news_summary      TEXT,
    sector              TEXT,        -- captured at entry time, from bot/universe/static_universe.sector_of()
    confidence_score    NUMERIC,     -- sentiment score at ENTRY time (not exit) - the "confidence" Strategy Intelligence analyses
    confidence_label    TEXT,
    market_regime       TEXT,        -- market regime at ENTRY time - see trades.market_regime
    strategy_version    TEXT         -- which strategy version was active when this trade was entered (see strategy_versions table, Phase 2)
);
CREATE INDEX IF NOT EXISTS idx_closed_trades_ts ON closed_trades (ts DESC);
CREATE INDEX IF NOT EXISTS idx_closed_trades_symbol ON closed_trades (symbol);

-- Added by the Strategy Intelligence phase; ALTER-for-existing-DBs.
ALTER TABLE closed_trades ADD COLUMN IF NOT EXISTS sector TEXT;
ALTER TABLE closed_trades ADD COLUMN IF NOT EXISTS confidence_score NUMERIC;
ALTER TABLE closed_trades ADD COLUMN IF NOT EXISTS confidence_label TEXT;
ALTER TABLE closed_trades ADD COLUMN IF NOT EXISTS market_regime TEXT;
ALTER TABLE closed_trades ADD COLUMN IF NOT EXISTS strategy_version TEXT;

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

-- ---------------------------------------------------------------------
-- strategy_versions: Strategy Intelligence's versioning record. 'v1' is
-- seeded below as the baseline (whatever config the bot has been running
-- with since before this feature existed) so the dashboard has something
-- to show as "current version" from day one. Phase 2 (Strategy Version
-- Management) is what actually lets new versions be created through the
-- approval workflow - until then, every trade is tagged strategy_version='v1'.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_versions (
    version           TEXT PRIMARY KEY,       -- e.g. 'v1', 'v2'
    deployed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    description       TEXT,
    config_snapshot   JSONB,                  -- key thresholds/risk params captured at deployment
    is_active         BOOLEAN NOT NULL DEFAULT false,
    created_from_recommendation_id BIGINT     -- FK to strategy_recommendations, once that table exists (Phase 2/3)
);
INSERT INTO strategy_versions (version, description, is_active) VALUES
    ('v1', 'Baseline strategy - the configuration in place before Strategy Intelligence was introduced.', true)
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- strategy_recommendations: the Approval Workflow. Pattern Discovery
-- (Phase 3) and the AI Research Assistant (Phase 4) INSERT rows here -
-- they never touch trading behaviour directly. A human (you, from the
-- dashboard) reviews each one and sets status to 'approved' or 'rejected'.
-- Only an 'approved' recommendation can be turned into a new
-- strategy_versions row (via "Deploy as new version" on the dashboard) -
-- approval alone never changes what the bot does; creating the version
-- row is the one and only action that does, and that's still a deliberate
-- extra step you take, never automatic.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_recommendations (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    source                  TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'pattern_discovery' | 'ai_research'
    title                   TEXT NOT NULL,
    observation             TEXT,
    evidence                TEXT,
    statistical_confidence  TEXT,        -- e.g. 'high (n=482, p<0.01)' - free text, source-dependent
    estimated_impact        TEXT,
    risks                   TEXT,
    recommendation          TEXT,
    priority                TEXT NOT NULL DEFAULT 'medium',  -- 'low' | 'medium' | 'high'
    proposed_config_changes JSONB,       -- e.g. {"STRATEGY_BUY_THRESHOLD": 8.5} - what would actually change
    status                  TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'approved' | 'rejected'
    reviewed_at             TIMESTAMPTZ,
    reviewed_by             TEXT,
    review_notes            TEXT,
    backtest_result         JSONB,       -- populated by Phase 6 (Backtesting & Simulation), null until then
    deployed_as_version     TEXT         -- set once an approved recommendation becomes a strategy_versions row
);
CREATE INDEX IF NOT EXISTS idx_strategy_recommendations_status ON strategy_recommendations (status);
CREATE INDEX IF NOT EXISTS idx_strategy_recommendations_created ON strategy_recommendations (created_at DESC);

-- ---------------------------------------------------------------------
-- pattern_discovery_findings: one row per statistical finding, per analysis
-- run (see dashboard/lib/patternDiscovery.ts). Recomputed fresh from
-- ALL-TIME closed_trades every time the Pattern Discovery page loads (or its
-- API route is hit) - deliberately not recency-weighted, so a handful of
-- lucky/unlucky recent trades can't dominate a "pattern." Every finding
-- records its own sample size and whether it cleared that category's
-- minimum-sample bar, so the dashboard can grey out anything under-evidenced
-- instead of presenting it as a real conclusion. This table is a history/
-- audit log read by the AI Research Assistant (Phase 4) and the Monthly
-- Report (Phase 7) - Pattern Discovery itself never writes to
-- strategy_recommendations directly.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pattern_discovery_findings (
    id                    BIGSERIAL PRIMARY KEY,
    run_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    category              TEXT NOT NULL,   -- 'confidence_threshold' | 'sector' | 'holding_period' |
                                            -- 'symbol_underperformance' | 'stop_loss' | 'take_profit' |
                                            -- 'volatility' | 'position_sizing' | 'sentiment_reasoning' | 'news_source'
    title                 TEXT NOT NULL,
    description           TEXT NOT NULL,
    sample_size           INTEGER NOT NULL,
    baseline_sample_size  INTEGER,
    statistical_method     TEXT,
    p_value               NUMERIC,
    effect_size           NUMERIC,
    meets_min_sample      BOOLEAN NOT NULL DEFAULT false,
    is_significant        BOOLEAN NOT NULL DEFAULT false,
    confidence_level      TEXT NOT NULL DEFAULT 'insufficient', -- 'insufficient' | 'low' | 'medium' | 'high'
    raw                   JSONB
);
CREATE INDEX IF NOT EXISTS idx_pattern_findings_run ON pattern_discovery_findings (run_at DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_findings_category ON pattern_discovery_findings (category);

-- ---------------------------------------------------------------------
-- strategy_health_scores: one row per computation of the Phase 5 Strategy
-- Health Score (see dashboard/lib/strategyHealth.ts). Recomputed fresh from
-- current data every time the Strategy Health page loads, then a snapshot is
-- persisted here purely so the page can chart the score's trend over time -
-- the live "current" score shown on the page always comes from the fresh
-- computation, never a stale row.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_health_scores (
    id                BIGSERIAL PRIMARY KEY,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    overall_score     NUMERIC,          -- NULL if there wasn't enough data to compute anything
    confidence_level  TEXT NOT NULL DEFAULT 'insufficient', -- 'insufficient' | 'low' | 'medium' | 'high'
    total_trades      INTEGER NOT NULL DEFAULT 0,
    strategy_version  TEXT,
    components        JSONB             -- full per-component breakdown, see HealthComponent type
);
CREATE INDEX IF NOT EXISTS idx_strategy_health_computed ON strategy_health_scores (computed_at DESC);

-- ---------------------------------------------------------------------
-- monthly_research_reports: one row per Phase 7 monthly rollup (see
-- dashboard/lib/monthlyReport.ts). Generated either on-demand from the
-- dashboard's Monthly Report page, or automatically once a month by the
-- Python bot's scheduler calling POST /api/monthly-report (see main.py's
-- run_monthly_report and bot/scheduler.py) - both paths write here.
-- telegram_summary is a short plain-text digest suitable for a Telegram
-- message; the rest are full plain-English sections shown on the dashboard.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS monthly_research_reports (
    id                        BIGSERIAL PRIMARY KEY,
    generated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_start              TIMESTAMPTZ NOT NULL,
    period_end                TIMESTAMPTZ NOT NULL,
    model_used                TEXT NOT NULL,
    total_trades              INTEGER NOT NULL DEFAULT 0,
    strategy_health_score     NUMERIC,
    overall_performance       JSONB,
    lessons_learned           TEXT,
    emerging_patterns         TEXT,
    potential_optimizations   TEXT,
    market_observations       TEXT,
    recommended_improvements  TEXT,
    telegram_summary          TEXT,
    sent_via_telegram         BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_monthly_reports_generated ON monthly_research_reports (generated_at DESC);

-- ---------------------------------------------------------------------
-- bot_control: a single-row switch for pausing/resuming new trading
-- activity from the dashboard (see dashboard/app/api/bot-control and
-- bot/bot_control.py's BotControlProvider). This is a deliberate, explicit
-- human action taken on the dashboard - the bot never sets is_paused
-- itself. Pausing only blocks NEW entries (the same "block_new_entries"
-- gate used for the daily loss limit and market filters); sentiment-driven
-- sells and broker-side stop-loss/take-profit brackets keep managing
-- existing positions while paused, so pausing never leaves open positions
-- unprotected. "Emergency Stop" (surfaced on the dashboard) uses this same
-- mechanism with more urgent framing - it does NOT liquidate positions;
-- that is explicitly out of scope here and reserved for a future dedicated
-- Kill Switch feature.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_control (
    id            INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- singleton row
    is_paused     BOOLEAN NOT NULL DEFAULT false,
    reason        TEXT,               -- optional human-entered note, e.g. "manual pause" / "emergency stop"
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT                -- session user's email, best-effort audit trail
);
INSERT INTO bot_control (id, is_paused) VALUES (1, false) ON CONFLICT (id) DO NOTHING;

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
    ('pdt_warning', 'immediate', true, 'Pattern Day Trader warning', 'Fires once per day when day-trade count is approaching the 4-in-5-business-days PDT threshold on an account under $25,000.'),
    ('strategy_recommendation', 'daily_summary', true, 'New strategy recommendation', 'Fires when Pattern Discovery or the AI Research Assistant produces a new recommendation for your review - advisory only, never applied automatically.'),
    ('monthly_research_report', 'immediate', true, 'Monthly research report', 'Fires once a month (or whenever you generate one on-demand) with a short performance/pattern-health summary - full detail lives on the Monthly Report dashboard page.'),
    ('bot_paused', 'immediate', true, 'Trading paused', 'Fires when new trading activity is paused (or Emergency Stop is used) from the dashboard. Existing positions keep being managed.'),
    ('bot_resumed', 'immediate', true, 'Trading resumed', 'Fires when trading is resumed from the dashboard after a pause.')
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
