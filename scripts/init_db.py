#!/usr/bin/env python3
"""One-time (and re-runnable) setup: apply db/schema.sql to DATABASE_URL.

Usage:
    DATABASE_URL=postgres://... python scripts/init_db.py

Safe to run repeatedly - every statement in schema.sql is CREATE TABLE/INDEX
IF NOT EXISTS.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 1

    try:
        import psycopg2
    except ImportError:
        print("psycopg2-binary is not installed. Run: pip install psycopg2-binary",
              file=sys.stderr)
        return 1

    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "db", "schema.sql")
    with open(schema_path) as f:
        sql = f.read()

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print("Schema applied successfully.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
