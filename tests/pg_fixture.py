"""Throwaway local Postgres for integration tests (lease SQL, ledger apply).

Starts a private cluster in a temp directory on a random port, applies
db/schema.sql, and yields a DATABASE_URL. Skips the test cleanly if Postgres
binaries aren't installed. Never touches any real database.
"""
import glob
import os
import shutil
import socket
import subprocess
import tempfile
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bin(name):
    found = sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}")) or (
        [shutil.which(name)] if shutil.which(name) else [])
    return found[-1] if found else None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def pg_url():
    initdb, pg_ctl = _bin("initdb"), _bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("Postgres binaries not available")
    base = tempfile.mkdtemp(prefix="pgtest-")
    data = os.path.join(base, "data")
    as_user = []
    if os.geteuid() == 0:                       # initdb refuses to run as root
        shutil.chown(base, user="postgres")
        as_user = ["runuser", "-u", "postgres", "--"]
    port = _free_port()
    try:
        subprocess.run(as_user + [initdb, "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                       check=True, capture_output=True)
        subprocess.run(as_user + [pg_ctl, "-D", data, "-l", os.path.join(base, "log"), "-w", "-o",
                                  f"-p {port} -k {base} -c listen_addresses=127.0.0.1", "start"],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError, KeyError, LookupError) as exc:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"could not start a test Postgres: {exc}")
    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    import psycopg2

    for _ in range(50):
        try:
            psycopg2.connect(url).close()
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    with psycopg2.connect(url) as conn, conn.cursor() as cur:
        cur.execute(open(os.path.join(ROOT, "db", "schema.sql")).read())
    yield url
    subprocess.run(as_user + [pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(base, ignore_errors=True)
