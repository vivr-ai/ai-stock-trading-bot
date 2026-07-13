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
