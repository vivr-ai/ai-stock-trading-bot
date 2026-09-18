"""Loads and validates configuration.

Precedence (highest wins): environment variables  >  config.ini  >  built-in
defaults. This means the bot needs ZERO files to run — set env vars (e.g. in
Railway's dashboard) and it starts. config.ini is kept only as an optional
local-dev convenience (handy on a Mac so you don't export env vars in every
new terminal tab); it is never required and never read for secrets in
production.

See .env.example for the full list of environment variables.
"""
from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from typing import List, Optional, Type, TypeVar

T = TypeVar("T")


@dataclass
class AlpacaConfig:
    api_key: str      # PAPER account credentials
    secret_key: str


@dataclass
class TradingConfig:
    """Which of the three operating modes the bot runs in, and the
    credentials/confirmation that go with it. See README.md 'Operating
    modes' section for the full explanation.

      PAPER    - connects to Alpaca's paper account, executes paper trades.
      DRY_RUN  - connects to the LIVE account, but never submits an order;
                 only logs + notifies what it would have done. The safe way
                 to rehearse against real account data/positions.
      LIVE     - connects to the LIVE account and executes real trades.
                 Requires live_confirmed=True as a second, independent gate
                 (LIVE_TRADING_CONFIRMED env var) - TRADING_MODE=live alone
                 is never sufficient.
    """
    mode: str                 # "paper" | "dry_run" | "live"
    live_confirmed: bool
    live_api_key: str          # LIVE account credentials (separate from paper's)
    live_secret_key: str

    @property
    def connects_to_paper(self) -> bool:
        return self.mode == "paper"


@dataclass
class UniverseConfig:
    provider: str
    top_n: int
    min_symbols: int


@dataclass
class NewsConfig:
    provider: str
    newsapi_key: str
    finnhub_key: str
    lookback_hours: int
    max_articles_per_symbol: int


@dataclass
class SentimentConfig:
    provider: str
    claude_api_key: str
    claude_model: str
    openai_api_key: str
    openai_model: str
    temperature: float


@dataclass
class StrategyConfig:
    buy_threshold: float
    sell_threshold: float
    min_headlines: int
    sell_min_headlines: int
    max_intraday_runup_pct: float
    market_filter_symbol: str
    market_filter_max_drop_pct: float
    # --- confirmation filters ---
    require_price_above_sma: bool
    sma_period: int
    min_volume_ratio: float
    volume_lookback_days: int
    market_regime_filter_enabled: bool
    market_regime_ma_period: int
    # --- Strategy v2, Path A: sentiment-momentum as a weighted composite
    # score instead of a hard sentiment>=buy_threshold AND-gate. See
    # SentimentStrategy._evaluate_momentum_path. Weights must sum to 1.0. ---
    sentiment_weight: float
    headline_weight: float
    volume_weight: float
    momentum_buy_score: float
    # --- Strategy v2, Path B: short-term mean-reversion (Larry Connors
    # RSI-2 style) - buys an oversold dip within an established long-term
    # uptrend, volume-confirmed, vetoed if sentiment is actively bearish. See
    # SentimentStrategy._evaluate_reversion_path. reversion_live defaults to
    # False (shadow/decision-only mode): the bot logs what it WOULD have
    # bought but places no order, until you flip it on after reviewing a few
    # days of shadow decisions. ---
    reversion_enabled: bool
    reversion_live: bool
    rsi_period: int
    rsi_oversold: float
    rsi_exit: float
    reversion_trend_sma_period: int
    reversion_min_volume_ratio: float
    reversion_sentiment_veto: float
    reversion_max_hold_days: int


@dataclass
class RiskConfig:
    dry_run: bool
    max_position_pct: float
    max_open_positions: int
    max_total_exposure_pct: float
    max_new_positions_per_cycle: int
    max_positions_per_sector: int
    reentry_cooldown_hours: float
    stop_loss_pct: float
    take_profit_pct: float
    max_order_notional: float
    daily_loss_limit_pct: float
    trailing_stop_enabled: bool
    trailing_stop_pct: float
    trailing_stop_activation_pct: float
    trailing_stop_backstop_take_profit_pct: float


@dataclass
class ScheduleConfig:
    run_minutes: List[int]
    market_timezone: str


@dataclass
class LoggingConfig:
    trade_log_path: str
    closed_trades_path: str
    daily_summary_path: str
    report_dir: str
    run_log_path: str
    state_path: str
    log_level: str
    log_format: str  # "text" | "json"


@dataclass
class RetryConfig:
    max_attempts: int
    base_delay_seconds: float


@dataclass
class ServerConfig:
    port: Optional[int]  # if set (Railway sets $PORT for "web" services), bind a tiny health server


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    enabled: bool


@dataclass
class DashboardConfig:
    # Used only for the monthly research report (see main.py's
    # run_monthly_report / bot/scheduler.py): the bot calls the dashboard's
    # own /api/monthly-report route (which has all the Strategy Intelligence
    # analysis logic) over HTTP, rather than duplicating that TypeScript
    # logic in Python. Both blank means the monthly job silently no-ops -
    # this is optional, not required for the bot's core trading loop.
    internal_url: str
    internal_api_key: str


@dataclass
class Config:
    alpaca: AlpacaConfig
    universe: UniverseConfig
    news: NewsConfig
    sentiment: SentimentConfig
    strategy: StrategyConfig
    risk: RiskConfig
    schedule: ScheduleConfig
    logging: LoggingConfig
    retry: RetryConfig
    server: ServerConfig
    telegram: TelegramConfig
    trading: TradingConfig
    dashboard: DashboardConfig
    project_root: str = field(default="")
    config_file_used: Optional[str] = field(default=None)


def _csv_list(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _to_bool(raw: str) -> bool:
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _get(
    parser: Optional[configparser.ConfigParser],
    section: str,
    key: str,
    env_name: str,
    fallback: T,
    cast: Type = str,
) -> T:
    """Resolve one setting: env var > config.ini > fallback."""
    raw = os.environ.get(env_name)
    if raw is None or raw == "":
        if parser is not None and parser.has_option(section, key):
            raw = parser.get(section, key)
        else:
            return fallback
    if cast is bool:
        return _to_bool(raw)  # type: ignore[return-value]
    if raw == "" :
        return fallback
    return cast(raw)  # type: ignore[return-value]


def _load_ini(path: str) -> Optional[configparser.ConfigParser]:
    """Load config.ini if present. It is entirely optional — used only to
    fill in values not supplied via environment variables (local-dev
    convenience). Missing file is NOT an error."""
    if not path or not os.path.exists(path):
        return None
    p = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    p.read(path)
    return p


def load_config(path: str = "config.ini") -> Config:
    parser = _load_ini(path)
    project_root = os.path.dirname(os.path.abspath(path)) if parser else os.getcwd()
    port_raw = os.environ.get("PORT", "")

    cfg = Config(
        alpaca=AlpacaConfig(
            api_key=_get(parser, "alpaca", "api_key", "ALPACA_API_KEY", ""),
            secret_key=_get(parser, "alpaca", "secret_key", "ALPACA_SECRET_KEY", ""),
        ),
        universe=UniverseConfig(
            provider=_get(parser, "universe", "provider", "UNIVERSE_PROVIDER", "static").lower(),
            top_n=_get(parser, "universe", "top_n", "UNIVERSE_TOP_N", 50, int),
            min_symbols=_get(parser, "universe", "min_symbols", "UNIVERSE_MIN_SYMBOLS", 25, int),
        ),
        news=NewsConfig(
            provider=_get(parser, "news", "provider", "NEWS_PROVIDER", "alpaca").lower(),
            newsapi_key=_get(parser, "news", "newsapi_key", "NEWSAPI_API_KEY", ""),
            finnhub_key=_get(parser, "news", "finnhub_key", "FINNHUB_API_KEY", ""),
            lookback_hours=_get(parser, "news", "lookback_hours", "NEWS_LOOKBACK_HOURS", 12, int),
            max_articles_per_symbol=_get(
                parser, "news", "max_articles_per_symbol", "NEWS_MAX_ARTICLES_PER_SYMBOL", 15, int
            ),
        ),
        sentiment=SentimentConfig(
            provider=_get(parser, "sentiment", "provider", "SENTIMENT_PROVIDER", "lexicon").lower(),
            claude_api_key=_get(parser, "sentiment", "claude_api_key", "ANTHROPIC_API_KEY", ""),
            claude_model=_get(
                parser, "sentiment", "claude_model", "CLAUDE_MODEL", "claude-haiku-4-5-20251001"
            ),
            openai_api_key=_get(parser, "sentiment", "openai_api_key", "OPENAI_API_KEY", ""),
            openai_model=_get(parser, "sentiment", "openai_model", "OPENAI_MODEL", "gpt-4o-mini"),
            temperature=_get(
                parser, "sentiment", "temperature", "SENTIMENT_TEMPERATURE", 0.0, float
            ),
        ),
        strategy=StrategyConfig(
            buy_threshold=_get(parser, "strategy", "buy_threshold", "STRATEGY_BUY_THRESHOLD", 8.0, float),
            sell_threshold=_get(parser, "strategy", "sell_threshold", "STRATEGY_SELL_THRESHOLD", -5.0, float),
            min_headlines=_get(parser, "strategy", "min_headlines", "STRATEGY_MIN_HEADLINES", 5, int),
            sell_min_headlines=_get(
                parser, "strategy", "sell_min_headlines", "STRATEGY_SELL_MIN_HEADLINES", 3, int
            ),
            max_intraday_runup_pct=_get(
                parser, "strategy", "max_intraday_runup_pct", "STRATEGY_MAX_INTRADAY_RUNUP_PCT", 8.0, float
            ),
            market_filter_symbol=_get(
                parser, "strategy", "market_filter_symbol", "STRATEGY_MARKET_FILTER_SYMBOL", "SPY"
            ).upper(),
            market_filter_max_drop_pct=_get(
                parser, "strategy", "market_filter_max_drop_pct",
                "STRATEGY_MARKET_FILTER_MAX_DROP_PCT", 2.0, float
            ),
            require_price_above_sma=_get(
                parser, "strategy", "require_price_above_sma",
                "STRATEGY_REQUIRE_PRICE_ABOVE_SMA", True, bool
            ),
            sma_period=_get(parser, "strategy", "sma_period", "STRATEGY_SMA_PERIOD", 20, int),
            min_volume_ratio=_get(
                parser, "strategy", "min_volume_ratio", "STRATEGY_MIN_VOLUME_RATIO", 1.5, float
            ),
            volume_lookback_days=_get(
                parser, "strategy", "volume_lookback_days", "STRATEGY_VOLUME_LOOKBACK_DAYS", 20, int
            ),
            market_regime_filter_enabled=_get(
                parser, "strategy", "market_regime_filter_enabled",
                "STRATEGY_MARKET_REGIME_FILTER_ENABLED", True, bool
            ),
            market_regime_ma_period=_get(
                parser, "strategy", "market_regime_ma_period", "STRATEGY_MARKET_REGIME_MA_PERIOD", 50, int
            ),
            sentiment_weight=_get(
                parser, "strategy", "sentiment_weight", "STRATEGY_SENTIMENT_WEIGHT", 0.5, float
            ),
            headline_weight=_get(
                parser, "strategy", "headline_weight", "STRATEGY_HEADLINE_WEIGHT", 0.2, float
            ),
            volume_weight=_get(
                parser, "strategy", "volume_weight", "STRATEGY_VOLUME_WEIGHT", 0.3, float
            ),
            momentum_buy_score=_get(
                parser, "strategy", "momentum_buy_score", "STRATEGY_MOMENTUM_BUY_SCORE", 0.6, float
            ),
            reversion_enabled=_get(
                parser, "strategy", "reversion_enabled", "STRATEGY_REVERSION_ENABLED", True, bool
            ),
            reversion_live=_get(
                parser, "strategy", "reversion_live", "STRATEGY_REVERSION_LIVE", False, bool
            ),
            rsi_period=_get(parser, "strategy", "rsi_period", "STRATEGY_RSI_PERIOD", 2, int),
            rsi_oversold=_get(
                parser, "strategy", "rsi_oversold", "STRATEGY_RSI_OVERSOLD", 10.0, float
            ),
            rsi_exit=_get(parser, "strategy", "rsi_exit", "STRATEGY_RSI_EXIT", 65.0, float),
            reversion_trend_sma_period=_get(
                parser, "strategy", "reversion_trend_sma_period",
                "STRATEGY_REVERSION_TREND_SMA_PERIOD", 200, int
            ),
            reversion_min_volume_ratio=_get(
                parser, "strategy", "reversion_min_volume_ratio",
                "STRATEGY_REVERSION_MIN_VOLUME_RATIO", 1.3, float
            ),
            reversion_sentiment_veto=_get(
                parser, "strategy", "reversion_sentiment_veto",
                "STRATEGY_REVERSION_SENTIMENT_VETO", -5.0, float
            ),
            reversion_max_hold_days=_get(
                parser, "strategy", "reversion_max_hold_days",
                "STRATEGY_REVERSION_MAX_HOLD_DAYS", 5, int
            ),
        ),
        risk=RiskConfig(
            dry_run=_get(parser, "risk", "dry_run", "RISK_DRY_RUN", True, bool),
            max_position_pct=_get(parser, "risk", "max_position_pct", "RISK_MAX_POSITION_PCT", 5.0, float),
            max_open_positions=_get(
                parser, "risk", "max_open_positions", "RISK_MAX_OPEN_POSITIONS", 10, int
            ),
            max_total_exposure_pct=_get(
                parser, "risk", "max_total_exposure_pct", "RISK_MAX_TOTAL_EXPOSURE_PCT", 50.0, float
            ),
            max_new_positions_per_cycle=_get(
                parser, "risk", "max_new_positions_per_cycle", "RISK_MAX_NEW_POSITIONS_PER_CYCLE", 3, int
            ),
            max_positions_per_sector=_get(
                parser, "risk", "max_positions_per_sector", "RISK_MAX_POSITIONS_PER_SECTOR", 3, int
            ),
            reentry_cooldown_hours=_get(
                parser, "risk", "reentry_cooldown_hours", "RISK_REENTRY_COOLDOWN_HOURS", 24.0, float
            ),
            stop_loss_pct=_get(parser, "risk", "stop_loss_pct", "RISK_STOP_LOSS_PCT", 10.0, float),
            take_profit_pct=_get(parser, "risk", "take_profit_pct", "RISK_TAKE_PROFIT_PCT", 20.0, float),
            max_order_notional=_get(
                parser, "risk", "max_order_notional", "RISK_MAX_ORDER_NOTIONAL", 0.0, float
            ),
            daily_loss_limit_pct=_get(
                parser, "risk", "daily_loss_limit_pct", "RISK_DAILY_LOSS_LIMIT_PCT", 4.0, float
            ),
            # Software trailing-stop layer (SentimentStrategy
            # ._maybe_trailing_stop_exit) - off by default, a behavior
            # change to live trading logic like reversion_live. When on,
            # the broker bracket's take-profit leg widens to
            # trailing_stop_backstop_take_profit_pct instead of
            # take_profit_pct (see RiskManager.evaluate) - a distant
            # backstop rather than the real profit-taking mechanism, so
            # the trailing check (not the fixed +20% cap) decides when a
            # winner actually gets sold.
            trailing_stop_enabled=_get(
                parser, "risk", "trailing_stop_enabled", "RISK_TRAILING_STOP_ENABLED", False, bool
            ),
            trailing_stop_pct=_get(
                parser, "risk", "trailing_stop_pct", "RISK_TRAILING_STOP_PCT", 7.0, float
            ),
            # Minimum profit above entry before the trail arms - below
            # this, only the original fixed stop-loss protects the
            # position. Without an activation floor, a trail this tight
            # would also apply to a position sitting barely above entry,
            # selling it on ordinary noise for close to a wash instead of
            # protecting a genuine gain.
            trailing_stop_activation_pct=_get(
                parser, "risk", "trailing_stop_activation_pct",
                "RISK_TRAILING_STOP_ACTIVATION_PCT", 3.0, float
            ),
            trailing_stop_backstop_take_profit_pct=_get(
                parser, "risk", "trailing_stop_backstop_take_profit_pct",
                "RISK_TRAILING_STOP_BACKSTOP_TAKE_PROFIT_PCT", 50.0, float
            ),
        ),
        schedule=ScheduleConfig(
            run_minutes=[
                int(m) for m in _csv_list(
                    _get(parser, "schedule", "run_minutes", "SCHEDULE_RUN_MINUTES", "0,30")
                )
            ],
            market_timezone=_get(
                parser, "schedule", "market_timezone", "SCHEDULE_MARKET_TIMEZONE", "America/New_York"
            ),
        ),
        logging=LoggingConfig(
            trade_log_path=_get(parser, "logging", "trade_log_path", "TRADE_LOG_PATH", "logs/trades.csv"),
            closed_trades_path=_get(
                parser, "logging", "closed_trades_path", "CLOSED_TRADES_PATH", "logs/closed_trades.csv"
            ),
            daily_summary_path=_get(
                parser, "logging", "daily_summary_path", "DAILY_SUMMARY_PATH", "logs/daily_summary.log"
            ),
            report_dir=_get(parser, "logging", "report_dir", "REPORT_DIR", "logs/reports"),
            run_log_path=_get(parser, "logging", "run_log_path", "RUN_LOG_PATH", "logs/bot.log"),
            state_path=_get(parser, "logging", "state_path", "STATE_PATH", "logs/state.json"),
            log_level=_get(parser, "logging", "log_level", "LOG_LEVEL", "INFO").upper(),
            log_format=_get(parser, "logging", "log_format", "LOG_FORMAT", "text").lower(),
        ),
        retry=RetryConfig(
            max_attempts=_get(parser, "retry", "max_attempts", "RETRY_MAX_ATTEMPTS", 4, int),
            base_delay_seconds=_get(
                parser, "retry", "base_delay_seconds", "RETRY_BASE_DELAY_SECONDS", 1.0, float
            ),
        ),
        server=ServerConfig(port=int(port_raw) if port_raw.strip().isdigit() else None),
        telegram=_build_telegram_config(parser),
        trading=TradingConfig(
            mode=_get(parser, "trading", "mode", "TRADING_MODE", "paper").lower(),
            live_confirmed=_get(parser, "trading", "live_confirmed", "LIVE_TRADING_CONFIRMED", False, bool),
            live_api_key=_get(parser, "trading", "live_api_key", "ALPACA_LIVE_API_KEY", ""),
            live_secret_key=_get(parser, "trading", "live_secret_key", "ALPACA_LIVE_SECRET_KEY", ""),
        ),
        dashboard=DashboardConfig(
            internal_url=_get(parser, "dashboard", "internal_url", "DASHBOARD_INTERNAL_URL", ""),
            internal_api_key=_get(
                parser, "dashboard", "internal_api_key", "DASHBOARD_INTERNAL_API_KEY", ""
            ),
        ),
        project_root=project_root,
        config_file_used=path if parser is not None else None,
    )
    _validate(cfg)
    return cfg


def _build_telegram_config(parser: Optional[configparser.ConfigParser]) -> "TelegramConfig":
    bot_token = _get(parser, "telegram", "bot_token", "TELEGRAM_BOT_TOKEN", "")
    chat_id = _get(parser, "telegram", "chat_id", "TELEGRAM_CHAT_ID", "")
    # Auto-enabled once both credentials are present; TELEGRAM_ENABLED=false
    # is an explicit opt-out (e.g. to silence notifications temporarily
    # without deleting the credentials).
    default_enabled = bool(bot_token and chat_id)
    enabled = _get(parser, "telegram", "enabled", "TELEGRAM_ENABLED", default_enabled, bool)
    return TelegramConfig(bot_token=bot_token, chat_id=chat_id, enabled=enabled and default_enabled)


def _validate(cfg: Config) -> None:
    missing = []
    if not cfg.alpaca.api_key:
        missing.append("ALPACA_API_KEY")
    if not cfg.alpaca.secret_key:
        missing.append("ALPACA_SECRET_KEY")
    if missing:
        raise ValueError(
            "Missing required configuration: " + ", ".join(missing) + ". "
            "Set these as environment variables (e.g. in Railway: Project -> "
            "Variables), or in a local config.ini for local dev. "
            "See .env.example for the full list."
        )

    if cfg.universe.provider not in ("mention", "static"):
        raise ValueError("universe.provider / UNIVERSE_PROVIDER must be 'mention' or 'static'")
    if cfg.news.provider not in ("alpaca", "newsapi", "finnhub"):
        raise ValueError("news.provider / NEWS_PROVIDER must be 'alpaca', 'newsapi', or 'finnhub'")
    if cfg.sentiment.provider not in ("lexicon", "claude", "openai"):
        raise ValueError("sentiment.provider / SENTIMENT_PROVIDER must be 'lexicon', 'claude', or 'openai'")

    # ---- Operating mode: PAPER / DRY_RUN / LIVE --------------------------
    # This replaces the old hard-coded "refuses to run against a live
    # account" check with a deliberate three-mode architecture. PAPER is
    # the only mode that doesn't need live credentials or confirmation, so
    # a fresh deploy with no TRADING_MODE set behaves exactly as before.
    if cfg.trading.mode not in ("paper", "dry_run", "live"):
        raise ValueError(
            "TRADING_MODE must be 'paper', 'dry_run', or 'live' (got "
            f"'{cfg.trading.mode}')."
        )
    if cfg.trading.mode in ("dry_run", "live"):
        live_missing = []
        if not cfg.trading.live_api_key:
            live_missing.append("ALPACA_LIVE_API_KEY")
        if not cfg.trading.live_secret_key:
            live_missing.append("ALPACA_LIVE_SECRET_KEY")
        if live_missing:
            raise ValueError(
                f"TRADING_MODE={cfg.trading.mode} connects to your LIVE Alpaca account, which "
                "needs its own credentials (separate from your paper keys). Missing: "
                + ", ".join(live_missing) + ". Get these from Alpaca's live account dashboard."
            )
    if cfg.trading.mode == "live" and not cfg.trading.live_confirmed:
        raise ValueError(
            "TRADING_MODE=live requires LIVE_TRADING_CONFIRMED=true as a second, independent "
            "confirmation that you intend to place real orders with real money. Setting "
            "TRADING_MODE=live alone is never sufficient. If you want to rehearse against the "
            "live account first without risk, use TRADING_MODE=dry_run instead."
        )

    if not (-10 <= cfg.strategy.sell_threshold < cfg.strategy.buy_threshold <= 10):
        raise ValueError("thresholds must satisfy -10 <= sell < buy <= 10")
    if cfg.logging.log_format not in ("text", "json"):
        raise ValueError("logging.log_format / LOG_FORMAT must be 'text' or 'json'")

    # ---- Strategy v2: Path A weighted composite -------------------------
    weight_sum = (cfg.strategy.sentiment_weight + cfg.strategy.headline_weight
                  + cfg.strategy.volume_weight)
    if abs(weight_sum - 1.0) > 0.01:
        raise ValueError(
            "strategy sentiment/headline/volume weights must sum to 1.0 (got "
            f"{weight_sum:.3f}). Adjust STRATEGY_SENTIMENT_WEIGHT / "
            "STRATEGY_HEADLINE_WEIGHT / STRATEGY_VOLUME_WEIGHT."
        )
    # The composite score itself can never exceed 1.0 (three components each
    # capped at 1.0, weights enforced to sum to 1.0 above) - a threshold
    # above that would validate cleanly but make Path A silently unbuyable
    # forever, with no error anywhere. Cap the allowed range at the score's
    # actual ceiling instead of a number that looks plausible but isn't
    # reachable.
    if not (0.0 < cfg.strategy.momentum_buy_score <= 1.0):
        raise ValueError(
            "strategy.momentum_buy_score / STRATEGY_MOMENTUM_BUY_SCORE must be in (0, 1.0] - "
            "the weighted composite score it's compared against can never exceed 1.0."
        )

    # ---- Strategy v2: Path B mean-reversion ------------------------------
    if cfg.strategy.reversion_enabled:
        if cfg.strategy.rsi_period < 2:
            raise ValueError("strategy.rsi_period / STRATEGY_RSI_PERIOD must be >= 2")
        if not (0 < cfg.strategy.rsi_oversold < cfg.strategy.rsi_exit < 100):
            raise ValueError(
                "strategy RSI thresholds must satisfy "
                "0 < STRATEGY_RSI_OVERSOLD < STRATEGY_RSI_EXIT < 100"
            )
        if cfg.strategy.reversion_trend_sma_period < 2:
            raise ValueError(
                "strategy.reversion_trend_sma_period / STRATEGY_REVERSION_TREND_SMA_PERIOD "
                "must be >= 2"
            )
        if cfg.strategy.reversion_min_volume_ratio < 0:
            raise ValueError(
                "strategy.reversion_min_volume_ratio / STRATEGY_REVERSION_MIN_VOLUME_RATIO "
                "must be >= 0"
            )
        if cfg.strategy.reversion_max_hold_days < 1:
            raise ValueError(
                "strategy.reversion_max_hold_days / STRATEGY_REVERSION_MAX_HOLD_DAYS must be >= 1"
            )

    # ---- Software trailing-stop layer ------------------------------------
    if not (0.0 < cfg.risk.trailing_stop_pct < 100.0):
        raise ValueError(
            "risk.trailing_stop_pct / RISK_TRAILING_STOP_PCT must be in (0, 100)."
        )
    if cfg.risk.trailing_stop_activation_pct < 0.0:
        raise ValueError(
            "risk.trailing_stop_activation_pct / RISK_TRAILING_STOP_ACTIVATION_PCT must be >= 0."
        )
    if cfg.risk.trailing_stop_enabled and (
        cfg.risk.trailing_stop_backstop_take_profit_pct <= cfg.risk.trailing_stop_activation_pct
    ):
        # If the backstop take-profit sits at or below the activation
        # floor, the broker's own bracket would sell at the backstop
        # before the trail ever has a chance to arm - silently defeating
        # the entire point of turning this on (same class of footgun as
        # the momentum_buy_score bound above: a config that validates
        # cleanly but makes the feature it names do nothing).
        raise ValueError(
            "risk.trailing_stop_backstop_take_profit_pct / "
            "RISK_TRAILING_STOP_BACKSTOP_TAKE_PROFIT_PCT must be greater than "
            "trailing_stop_activation_pct, or the broker's own take-profit leg fires "
            "before the trailing stop ever arms."
        )
