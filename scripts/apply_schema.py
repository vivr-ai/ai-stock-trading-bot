#!/usr/bin/env python3
"""Applies db/schema.sql to DATABASE_URL. Safe to run every deploy.

Why this exists: DATABASE_URL on Railway is typically the *internal*
hostname (postgres.railway.internal), which only resolves from inside
Railway's network — not from a laptop, and free-tier Railway doesn't
include the web Postgres Query editor either. Running this as part of
`releaseCommand` (see railway.toml) means the schema gets applied from
inside Railway automatically on every deploy, with no manual psql step and
no need for a public database URL.

Idempotent by construction: db/schema.sql only uses `CREATE TABLE IF NOT
EXISTS`, `CREATE INDEX IF NOT EXISTS`, and `INSERT ... ON CONFLICT DO
NOTHING`, so re-running it against an already-migrated database is a no-op.
No-ops (exit 0) if DATABASE_URL isn't set, so it never blocks a deploy where
the dashboard's Postgres isn't configured yet.
"""
from __future__ import annotations

import os
import sys

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "schema.sql")


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("apply_schema: DATABASE_URL not set; skipping (dashboard persistence is optional).")
        return 0

    try:
        import psycopg2
    except ImportError:
        print("apply_schema: psycopg2-binary not installed; skipping.", file=sys.stderr)
        return 0

    if not os.path.exists(SCHEMA_PATH):
        print(f"apply_schema: {SCHEMA_PATH} not found; skipping.", file=sys.stderr)
        return 0

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        sql = f.read()

    try:
        conn = psycopg2.connect(database_url, connect_timeout=10)
    except Exception as exc:  # noqa: BLE001
        # Don't fail the deploy over this - the bot still runs fine on
        # CSV/JSON logs without dashboard persistence; main.py's own
        # DB-health alerting will surface a real outage separately.
        print(f"apply_schema: could not connect to DATABASE_URL: {exc}", file=sys.stderr)
        return 0

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print("apply_schema: db/schema.sql applied successfully.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"apply_schema: applying schema.sql failed: {exc}", file=sys.stderr)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
