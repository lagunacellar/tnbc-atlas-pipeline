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

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/sessions/clever-compassionate-heisenberg/mnt/outputs/tnbc_atlas"))
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
USER_AGENT = "TNBC-Atlas-Pilot/0.1 (mailto:syang@lagunacellar.com)"
CONTACT_EMAIL = "syang@lagunacellar.com"


def db_dsn() -> str:
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
