# Staging environment

A second, fully separate copy of the bot that trades its **own** Alpaca paper
account and writes to its **own** Postgres. Every reliability change is tested
here first; production only changes after an explicit go-ahead.

## Isolation, in one table

| Layer | Production | Staging | What stops them mixing |
|---|---|---|---|
| Railway | project `adaptable-hope` | a **separate project**, e.g. `trading-bot-staging` | Different projects share no variables, databases or private network. (Railway's "Duplicate Environment" copies variables, including API keys, so it is deliberately not used.) |
| Code | deploys from `main` | deploys from a feature branch (e.g. `stage2-db-failsafe`) | Production never sees a branch until it is merged to `main`, which only happens after approval. |
| Alpaca | existing paper account | a **new** paper account | `EXPECTED_ALPACA_ACCOUNT_NUMBER` - the bot asks Alpaca which account its keys belong to at start-up and refuses to run on a mismatch. |
| Database | production Postgres | staging Postgres in the staging project | `DATABASE_URL` is a reference to the staging project's own Postgres; production's internal hostname isn't reachable from another project. |
| Mode | `TRADING_MODE=paper` | `TRADING_MODE=paper` (enforced) | With `BOT_ENVIRONMENT=staging` the bot refuses to start unless it is in paper mode, has an expected account number, and has **no** live-account keys. |
| Alerts | Telegram | Telegram, every message prefixed `[STAGING]` | Label added automatically from `BOT_ENVIRONMENT`. |

## One-time setup (Vivek)

### A. Alpaca: create the staging paper account and keys

1. Log in at app.alpaca.markets and make sure you are on **Paper Trading**.
2. Click the paper account number in the top-left corner and choose **Open New Paper Account**.
3. With the **new** account selected (check the number in the top-left), generate API keys from its home page. Copy the key and the secret; the secret is shown only once.
4. Note the new account's number (starts with `PA`).
5. **Do not** regenerate keys while the production account is selected - that would break the running production bot.

### B. Railway: create the staging project

1. Railway dashboard, **New Project**, then **Empty project**; name it `trading-bot-staging`.
2. In it: **New**, then **Database**, then **PostgreSQL**.
3. **New**, then **GitHub Repo**, then `vivr-ai/ai-stock-trading-bot`. In the new service's **Settings, Source**, set the branch to the feature branch under test (not `main`).
4. In the service's **Variables, Raw Editor**, paste (filling in the three values from step A):

```
BOT_ENVIRONMENT=staging
EXPECTED_ALPACA_ACCOUNT_NUMBER=<new paper account number, PA...>
ALPACA_API_KEY=<staging key>
ALPACA_SECRET_KEY=<staging secret>
TRADING_MODE=paper
RISK_DRY_RUN=false
DATABASE_URL=${{Postgres.DATABASE_URL}}
RISK_TRAILING_STOP_ENABLED=true
STRATEGY_MARKET_REGIME_FILTER_ENABLED=true
STRATEGY_REVERSION_ENABLED=false
TELEGRAM_BOT_TOKEN=<same bot, or a separate one>
TELEGRAM_CHAT_ID=<same chat, or a separate one>
```

   `RISK_DRY_RUN=false` is intended here: staging must place *paper* orders so
   broker behaviour can be tested. It can never reach a live account (paper
   mode is enforced and live keys are rejected).
5. Deploy. In the service's logs, look for
   `Alpaca account verified for staging: *******XXXX (paper)`.
   If you instead see `Refusing to start`, the keys or the account number don't match - fix the variables, nothing was traded.

Leave **PR environments** switched off in the production project
(Project Settings, Environments) so opening a pull request never spins up a
copy of production.

### C. Cost

A second always-on worker plus a second Postgres adds roughly the same usage as
the production pair. Check the staging project's **Usage** page after a few
days. No persistent volume is added (see the Stage 2 notes on the journal).

## Getting code onto staging

This Claude session can read the GitHub repo but cannot push to it. Each stage
is delivered as a git bundle; importing and pushing it is two commands (see
the stage report). Alternatively, install the Claude GitHub App for this
repository to let Claude push feature branches directly - it would still never
push to `main`.

## Rules that apply to staging

- Paper only. Never live keys.
- Never point staging at production's `DATABASE_URL` or keys.
- A change reaches production only by merging to `main`, after explicit approval.
