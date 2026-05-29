"""TNBC Atlas — Crossref enricher.

For every record with a DOI but no crossref_enriched_at, fetch
api.crossref.org/works/{doi} and update:
  - publication_date  (Crossref is authoritative for date)
  - crossref_type     (e.g. journal-article, posted-content, book-chapter)
  - license           (URL of license attached to the canonical version)
  - references_count
  - source_provenance['crossref'] = {funders, container_title, member, etc.}

Concurrent (5 workers, polite pool ≤ 50 req/s aggregate). Resumable:
records that already have crossref_enriched_at are skipped.

Usage:
    python enrich_crossref.py            # process all unenriched
    python enrich_crossref.py --max 1000 # process up to N (use to chunk)
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import psycopg
import requests
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).parent))
from common import CONTACT_EMAIL, USER_AGENT, db, db_dsn, log

CROSSREF = "https://api.crossref.org/works"

_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Mailto": CONTACT_EMAIL,
})
_lock = Lock()
_request_count = 0
_last_log_t = time.monotonic()


def fetch_one(doi: str) -> tuple[str, dict | None, str | None, bool]:
    """Returns (doi, data, error, mark_enriched).
    mark_enriched=False means transient — caller should NOT mark the row enriched
    so it gets retried on the next run."""
    backoff = 1.0
    for attempt in range(4):
        try:
            r = _session.get(f"{CROSSREF}/{doi}", timeout=20)
            if r.status_code == 404:
                return doi, None, "404", True  # permanent
            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
            return doi, r.json().get("message"), None, True
        except requests.RequestException as e:
            time.sleep(backoff)
            backoff *= 2
            last_err = f"err:{type(e).__name__}"
            continue
    return doi, None, "429-or-net", False  # transient, don't mark


def parse_date(parts) -> str | None:
    """Crossref date-parts: {'date-parts': [[yyyy, mm, dd]]}"""
    if not parts:
        return None
    arr = parts.get("date-parts") or []
    if not arr or not arr[0]:
        return None
    y = arr[0][0]
    m = arr[0][1] if len(arr[0]) > 1 else 1
    d = arr[0][2] if len(arr[0]) > 2 else 1
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except (ValueError, TypeError):
        return None


def project(msg: dict) -> dict:
    """Extract the fields we care about from a Crossref Works message."""
    license_url = None
    licenses = msg.get("license") or []
    if licenses:
        # Prefer the most permissive / most recently active license
        license_url = licenses[0].get("URL")

    # Authoritative date: published-print > published-online > published > created
    pub_date = (
        parse_date(msg.get("published-print"))
        or parse_date(msg.get("published-online"))
        or parse_date(msg.get("published"))
        or parse_date(msg.get("issued"))
        or parse_date(msg.get("created"))
    )

    funders = []
    for f in msg.get("funder", []) or []:
        funders.append({
            "name": f.get("name"),
            "doi": f.get("DOI"),
            "awards": f.get("award") or [],
        })

    return {
        "publication_date": pub_date,
        "crossref_type": msg.get("type"),
        "license": license_url,
        "references_count": msg.get("references-count") or msg.get("reference-count"),
        "source_provenance_crossref": {
            "container_title": (msg.get("container-title") or [None])[0],
            "publisher": msg.get("publisher"),
            "member": msg.get("member"),
            "funders": funders,
            "issn": msg.get("ISSN") or [],
            "subject": msg.get("subject") or [],
            "is_referenced_by_count": msg.get("is-referenced-by-count"),
            "subtype": msg.get("subtype"),
        },
    }


def update_record(doi: str, projected: dict | None, error: str | None) -> None:
    """Update one record. error != None still timestamps the row to avoid retry storms."""
    sql = """
        UPDATE bibliography_records
           SET publication_date = COALESCE(%(pub_date)s::date, publication_date),
               publication_year = COALESCE(EXTRACT(YEAR FROM %(pub_date)s::date)::int, publication_year),
               crossref_type    = COALESCE(%(crossref_type)s, crossref_type),
               license          = COALESCE(%(license)s, license),
               references_count = COALESCE(%(references_count)s, references_count),
               source_provenance = jsonb_set(
                   COALESCE(source_provenance, '{}'::jsonb),
                   '{crossref}', %(provenance)s::jsonb, true),
               crossref_enriched_at = now()
         WHERE canonical_doi = %(doi)s
    """
    params = {
        "doi": doi,
        "pub_date": (projected or {}).get("publication_date"),
        "crossref_type": (projected or {}).get("crossref_type"),
        "license": (projected or {}).get("license"),
        "references_count": (projected or {}).get("references_count"),
        "provenance": Jsonb({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            **((projected or {}).get("source_provenance_crossref") or {}),
        }),
    }
    with psycopg.connect(db_dsn()) as conn:
        conn.execute(sql, params)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT canonical_doi
              FROM bibliography_records
             WHERE canonical_doi IS NOT NULL
               AND crossref_enriched_at IS NULL
             ORDER BY canonical_doi
             LIMIT %s
        """, (args.max if args.max else 1_000_000,))
        dois = [r["canonical_doi"] for r in cur.fetchall()]

    log(f"to enrich: {len(dois)} DOIs (workers={args.workers})", "crossref")

    n_ok = n_404 = n_skipped = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, d): d for d in dois}
        for i, fut in enumerate(as_completed(futures), 1):
            doi, msg, err, mark_enriched = fut.result()
            if msg is not None:
                update_record(doi, project(msg), None)
                n_ok += 1
            elif mark_enriched:
                update_record(doi, None, err)
                if err == "404":
                    n_404 += 1
            else:
                # transient — leave row unenriched so a later run picks it up
                n_skipped += 1
            if i % 200 == 0:
                rate = i / (time.monotonic() - t0)
                log(f"  progress {i}/{len(dois)} (ok={n_ok} 404={n_404} retry={n_skipped}) {rate:.1f}/s", "crossref")

    log(f"DONE: ok={n_ok} 404={n_404} retry-later={n_skipped} of {len(dois)} in {time.monotonic()-t0:.1f}s", "crossref")


if __name__ == "__main__":
    main()
