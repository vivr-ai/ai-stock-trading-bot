"""Investment portfolio tracker - daily watch-list scan + Telegram alert.

Deliberately independent of `bot/` (the Alpaca trading bot): no shared
imports, no shared state, different Telegram bot/env vars. The only thing
in common is the repo and the Railway project. See investing/README.md for
what this is, how it's deployed, and how to maintain holdings.json.
"""
