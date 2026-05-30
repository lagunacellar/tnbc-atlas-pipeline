"""TNBC Atlas — shared utilities for the harvest pipeline."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

# Default to the repo root (parent of scripts/). Overridable via env for
# environments that prefer to write working data to a separate location.
PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
SNAPSHOTS = PROJECT_ROOT / "snapshots"
LOGS = PROJECT_ROOT / "logs"
EXPORTS = PROJECT_ROOT / "exports"
REPORTS = PROJECT_ROOT / "reports"
for _d in (SNAPSHOTS, LOGS, EXPORTS, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)

# Pilot harvest window: last 24 months
WINDOW_START = "2024-05-10"
WINDOW_END = "2026-05-10"

# Identify ourselves to upstream APIs (politeness pool / rate-limit etiquette)
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "syang@lagunacellar.com")
USER_AGENT = f"TNBC-Atlas-Pipeline/0.2 (mailto:{CONTACT_EMAIL})"


def db_dsn() -> str:
    """Resolve a libpq connection string from environment.

    Three modes, in priority order:
      1. DATABASE_URL  — full URI; used by GitHub Actions (Supabase pooled URI)
                         and any deployment-style environment.
      2. PGHOST / PGUSER / PGDATABASE / PGPASSWORD — classic libpq env vars;
                         used by local laptop sessions and managed Postgres.
      3. Unix socket fallback at /tmp/pgsock with user 'postgres' and
                         database 'tnbc_atlas' — the sandbox-pilot default.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        # psycopg accepts postgres:// and postgresql:// URLs directly.
        # Append sslmode=require for managed Postgres if not already specified.
        if "sslmode=" not in url and url.startswith(("postgres://", "postgresql://")):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"
        return url

    host = os.environ.get("PGHOST")
    if host:
        # Caller supplied PGHOST; psycopg picks up other PG* vars automatically
        # from the environment, but we set a sensible dbname default.
        return f"host={host} dbname={os.environ.get('PGDATABASE', 'tnbc_atlas')} user={os.environ.get('PGUSER', 'postgres')}"

    # Sandbox-pilot fallback
    return f"host={os.environ.get('PGSOCK', '/tmp/pgsock')} dbname=tnbc_atlas user=postgres"


@contextmanager
def db():
    with psycopg.connect(db_dsn(), row_factory=dict_row) as conn:
        yield conn
        conn.commit()


def log(msg: str, source: str = "harvest") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [{source}] {msg}"
    print(line, flush=True)
    with open(LOGS / f"{source}.log", "a") as fh:
        fh.write(line + "\n")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    n = 0
    with open(path, "w") as fh:
        for rec in records:
            fh.write(json.dumps(rec, default=str) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class RateLimiter:
    """Simple token-bucket-ish: ensure no more than `qps` requests per second."""

    def __init__(self, qps: float):
        self.min_interval = 1.0 / qps
        self.last = 0.0

    def wait(self):
        now = time.monotonic()
        delay = self.min_interval - (now - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def record_run(source: str, query: str, window_start: str, window_end: str, n: int, status: str, notes: str = "") -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO harvest_runs (source, query, window_start, window_end,
                                       completed_at, records_fetched, status, notes)
            VALUES (%s, %s, %s, %s, now(), %s, %s, %s)
            """,
            (source, query, window_start, window_end, n, status, notes),
        )
