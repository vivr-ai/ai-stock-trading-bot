# News Sentiment Trading Bot (Alpaca)

An automated trading bot: every 30 minutes during US market hours it builds a
list of stocks, reads recent headlines, scores sentiment, confirms the move
with price/volume/market-regime filters, and places risk-managed orders
through Alpaca — logging every scan and every decision, and producing a daily
performance report.

> **Not investment advice.** The bot ships defaulting to paper trading and
> stays there until you deliberately opt into something else — see
> "Operating modes" below. Run it with your own keys, keep `RISK_DRY_RUN=true`
> until you've watched it for a while, and remember a simple sentiment rule
> has no proven edge.

---

## Operating modes: PAPER / DRY_RUN / LIVE

The bot has three operating modes, selected by `TRADING_MODE`. Switching
between them is **environment-variables only** — no code changes, ever.

| Mode | `TRADING_MODE` | Account | Places real orders? | Credentials used |
|------|-----------------|---------|----------------------|-------------------|
| Paper (default) | `paper` | Alpaca paper | No — paper fills only | `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` |
| Dry run | `dry_run` | Alpaca **live** | Never — logs + Telegram-notifies what it would have done | `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` |
| Live | `live` | Alpaca **live** | Yes, if fully confirmed (see below) | `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` |

`DRY_RUN` is the recommended way to rehearse against your real account —
it sees real balances/positions and evaluates every decision exactly like
`LIVE` would, but the broker layer physically refuses to submit an order in
this mode (not just a risk-layer check — the guard lives in
`AlpacaBroker.__init__`).

**Going live requires three independent switches to all agree** — this is
deliberate defense-in-depth for a system that trades real money:

1. `TRADING_MODE=live`
2. `LIVE_TRADING_CONFIRMED=true` — an explicit, separate "I mean it" flag.
   Setting `TRADING_MODE=live` alone raises a configuration error at startup.
3. `RISK_DRY_RUN=false` — the pre-existing risk-layer dry-run gate, unrelated
   to `TRADING_MODE`, and still checked on top of it.

Missing any one of the three keeps the bot in a safe, order-free state (an
explicit config error for #1/#2 at startup; a runtime no-op for #3). See the
dashboard's **Live Readiness** page for a live checklist of everything this
implies before you flip the switch, and `.env.example` for the full variable
reference.

---

## Configuration: environment variables first

**The bot needs zero files to run.** All configuration comes from environment
variables; a local `config.ini` is an optional convenience for Mac/local dev
only (handy so you don't export env vars in every terminal tab) and is never
required. Environment variables always take precedence over `config.ini` when
both are present.

Deploying on Railway (or Render, Fly, a VPS, anywhere)? See
**[DEPLOYMENT.md](DEPLOYMENT.md)** — it covers required variables, the
ephemeral-filesystem/Volume gotcha, service type, and how to verify a deploy.
The full variable list with defaults is in **[.env.example](.env.example)**.

## Which keys do I need?

| Key | Env var | Needed? | When |
|-----|---------|---------|------|
| Alpaca API key + secret (paper) | `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | **MANDATORY** | `TRADING_MODE=paper` (default) |
| Alpaca API key + secret (live) | `ALPACA_LIVE_API_KEY` / `ALPACA_LIVE_SECRET_KEY` | Required for dry-run/live | `TRADING_MODE=dry_run` or `live` |
| Finnhub | `FINNHUB_API_KEY` | Optional | `UNIVERSE_PROVIDER=mention` or `NEWS_PROVIDER=finnhub` |
| NewsAPI | `NEWSAPI_API_KEY` | Optional | `NEWS_PROVIDER=newsapi` |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | Optional | `SENTIMENT_PROVIDER=claude` |
| OpenAI | `OPENAI_API_KEY` | Optional | `SENTIMENT_PROVIDER=openai` |

**Simplified mode (the shipped default) needs ONLY the two Alpaca keys.** It
uses the static stock list, Alpaca's own free news feed, and a built-in
offline word-list for sentiment.

> Security: never commit `config.ini` (it's gitignored) or a `.env` file. If a
> key has ever been pasted into a chat, email, or screenshot, rotate it in the
> provider's dashboard.

**Dashboard's AI Research Assistant** (Strategy Intelligence → Recommendations
→ "Run AI Research") is a separate Next.js service and needs its own
`ANTHROPIC_API_KEY` set in that service's Railway variables (the same key
value the bot uses for `SENTIMENT_PROVIDER=claude` works fine - it's just a
second copy of the env var on a different service). Two optional overrides
let you repoint the model IDs without a code change if Anthropic renames a
model: `RESEARCH_MODEL_HAIKU` (default `claude-haiku-4-5-20251001`, used for
scheduled/cheap runs) and `RESEARCH_MODEL_SONNET` (default `claude-sonnet-5`,
used when you pick "Sonnet" on demand in the dashboard).

---

## Quick start — local (Mac / any machine)

```bash
python -m pip install -U pip            # important on Python 3.14 (see note below)
python -m pip install -r requirements.txt

cp config.example.ini config.ini       # then put ONLY your Alpaca paper keys in it
python main.py --once                   # one cycle now, DRY RUN — places no orders
```

Inspect `logs/trades.csv`, `logs/closed_trades.csv`, and `logs/reports/`.
When you're satisfied, set `RISK_DRY_RUN=false` (env var, or `dry_run = false`
in `config.ini`) and run the schedule:

```bash
python main.py
```

### Useful commands

```bash
python main.py --check    # confirm keys connect + print paper balance (safe any time)
python main.py --force    # run ONE cycle now, ignoring market hours; always a
                          #   dry-run simulation (places no orders) — good for testing
python main.py --once     # run one cycle now, but only if the market is open
python main.py --eod      # write the daily performance report now and exit
python main.py            # start the 30-min scheduler (the normal way to run it)
```

### Running it unattended locally (not recommended long-term — see Railway)

`python main.py` blocks the terminal and dies if you close it or the machine
sleeps.

```bash
brew install tmux
tmux new -s bot
caffeinate -s python3 main.py
# detach with: Ctrl-b then d     |  reattach later with: tmux attach -t bot
```

US market hours are roughly 11:30pm-6:00am Brisbane time, so a Mac has to stay
on and awake overnight. **A small always-on host (Railway, a cloud VM) is the
robust option** — see [DEPLOYMENT.md](DEPLOYMENT.md).

### Note on the lexicon analyzer

The offline sentiment scorer is a finance word-list — fine for getting running,
but cruder than an LLM (it can misread phrasing like "fails to beat"). With it,
a buy threshold of 8 is fairly strict; if you see no trades, lower
`STRATEGY_BUY_THRESHOLD` (e.g. to 6), or switch `SENTIMENT_PROVIDER` to
`claude`/`openai`.

---

## Python 3.14

The code is 3.14-compatible — nothing in it needs changing for 3.14.6. The one
install gotcha is a dependency, not the code: `alpaca-py` uses `pydantic`,
whose compiled `pydantic-core` only got Python 3.14 wheels in recent releases.
Upgrade pip first and install the latest `alpaca-py`. On Railway, `.python-version`
pins 3.12 to sidestep this entirely.

---

## Trading rules (env-var driven)

**Buy** requires ALL of:
- sentiment score >= `STRATEGY_BUY_THRESHOLD` (+8) from >= `STRATEGY_MIN_HEADLINES` (5) headlines
- price above its `STRATEGY_SMA_PERIOD`-day (20) SMA
- today's volume >= `STRATEGY_MIN_VOLUME_RATIO` (1.5x) its `STRATEGY_VOLUME_LOOKBACK_DAYS`-day (20) average
- not already up more than `STRATEGY_MAX_INTRADAY_RUNUP_PCT` (8%) since yesterday's close
- SPY (or `STRATEGY_MARKET_FILTER_SYMBOL`) not down more than `STRATEGY_MARKET_FILTER_MAX_DROP_PCT` (2%) today
- SPY above its own `STRATEGY_MARKET_REGIME_MA_PERIOD`-day (50) SMA (`STRATEGY_MARKET_REGIME_FILTER_ENABLED`)
- not already held, no live order pending, not in `RISK_REENTRY_COOLDOWN_HOURS` (24h) cooldown
- under the per-cycle new-position cap, the per-sector cap, and the risk manager's sizing/position/exposure checks

Any confirmation input the bot can't confirm (e.g. today's volume not yet
readable) fails the gate closed — it skips the buy rather than assuming it's
fine.

**Sell** on sentiment below `STRATEGY_SELL_THRESHOLD` (-5) with enough
headlines; the `RISK_STOP_LOSS_PCT` (10%) / `RISK_TAKE_PROFIT_PCT` (20%) price
exits ride along as a GTC bracket on every entry regardless of sentiment.

**Risk caps:** `RISK_MAX_POSITION_PCT` (5%) per position, `RISK_MAX_OPEN_POSITIONS`
(10), `RISK_MAX_TOTAL_EXPOSURE_PCT` (50%), `RISK_MAX_NEW_POSITIONS_PER_CYCLE` (3),
plus `RISK_DAILY_LOSS_LIMIT_PCT` (4%) kill switch.

## Daily performance report

`python main.py --eod` (or the automatic 16:05 ET job) writes
`logs/reports/report_<date>.json` and `.txt` and logs a structured summary:
portfolio value, realized P/L (today + all-time), unrealized P/L on open
positions (straight from Alpaca), open positions with entry/current
price/unrealized P/L each, trade count, win rate, average gain, average loss,
and max drawdown (from Alpaca's portfolio history). Win rate / avg gain / avg
loss / max drawdown are cumulative (not single-day) since a single day rarely
has enough closed trades to be meaningful — realized/unrealized P/L and trade
counts are both today's and all-time.

## Reliability

Every external call (Alpaca trading/data, Finnhub, NewsAPI, Claude, OpenAI)
retries with exponential backoff before giving up, and degrades to a safe
default (skip the symbol, neutral sentiment, empty article list) rather than
crashing the cycle. Each symbol is processed in its own try/except so one bad
name can't take down a cycle. The scheduler restarts itself on an unexpected
internal crash instead of exiting. See [DEPLOYMENT.md](DEPLOYMENT.md) for the
Railway-specific pieces (SIGTERM handling, ephemeral-storage/Volume caveat,
restart policy).

## Project structure

```
ai-stock-trading-bot/
├── main.py                        Wires everything; starts the scheduler.
├── requirements.txt                Core + all optional provider deps (see file header).
├── config.example.ini             Optional local-dev config (env vars take precedence).
├── .env.example                    Full environment variable reference.
├── railway.toml / Procfile         Railway deploy config.
├── DEPLOYMENT.md                   Railway deployment walkthrough.
├── bot/
│   ├── config.py                   Env-var-first config loading + validation.
│   ├── state.py                    Cooldowns, exit detection, open-lot tracking (for P/L).
│   ├── scheduler.py                Cron jobs + SIGTERM handling + crash-restart supervisor.
│   ├── utils/
│   │   ├── retry.py                 Shared exponential-backoff retry helper.
│   │   └── logging_setup.py         Text/JSON structured logging.
│   ├── universe/                    static_universe.py (no key) | mention_counter.py (Finnhub)
│   ├── news/                        alpaca_news.py (no extra key) | finnhub_client.py | newsapi_client.py
│   ├── sentiment/                   lexicon_analyzer.py (no key) | claude_analyzer.py | openai_analyzer.py
│   ├── trading/                     alpaca_client.py (incl. SMA/volume/portfolio-history) · risk.py · strategy.py
│   ├── logging_utils/               trade_logger.py · closed_trade_logger.py · daily_summary.py
│   └── reporting/                   performance.py — the daily performance report
└── logs/                           trades.csv · closed_trades.csv · daily_summary.log · bot.log ·
                                     state.json · reports/report_<date>.{json,txt}
```
