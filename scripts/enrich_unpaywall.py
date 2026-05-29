"""TNBC Atlas — Unpaywall enricher.

For every record with a DOI but no unpaywall_enriched_at, fetch
api.unpaywall.org/v2/{doi}?email={contact} and update:
  - oa_status   (Unpaywall is more authoritative than OpenAlex on OA color)
  - oa_url      (best OA location with PDF if one exists)
  - license     (only fill if Crossref didn't)
  - source_provenance['unpaywall'] = full payload subset

Concurrent (8 workers, ~50/s aggregate). Resumable.

Usage:
    python enrich_unpaywall.py
    python enrich_unpaywall.py --max 1000
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

UNPAYWALL = "https://api.unpaywall.org/v2"

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})
_lock = Lock()


def fetch_one(doi: str) -> tuple[str, dict | None, str | None, bool]:
    """Returns (doi, data, error, mark_enriched). Transient errors return mark_enriched=False."""
    backoff = 1.0
    for _ in range(4):
        try:
            r = _session.get(f"{UNPAYWALL}/{doi}",
                              params={"email": CONTACT_EMAIL}, timeout=20)
            if r.status_code == 404:
                return doi, None, "404", True
            if r.status_code == 429:
                time.sleep(backoff); backoff *= 2; continue
            r.raise_for_status()
            return doi, r.json(), None, True
        except requests.RequestException:
            time.sleep(backoff); backoff *= 2; continue
    return doi, None, "429-or-net", False


def project(data: dict) -> dict:
    """Extract the bits we use from an Unpaywall response."""
    best = data.get("best_oa_location") or {}
    locations = data.get("oa_locations") or []
    return {
        "oa_status": data.get("oa_status"),  # gold/green/hybrid/bronze/closed
        "oa_url": best.get("url_for_pdf") or best.get("url"),
        "license": best.get("license"),
        "source_provenance_unpaywall": {
            "is_oa": data.get("is_oa"),
            "best_oa_location": {
                "url": best.get("url"),
                "url_for_pdf": best.get("url_for_pdf"),
                "license": best.get("license"),
                "host_type": best.get("host_type"),
                "version": best.get("version"),
            } if best else None,
            "n_oa_locations": len(locations),
            "journal_is_oa": data.get("journal_is_oa"),
            "journal_is_in_doaj": data.get("journal_is_in_doaj"),
            "has_repository_copy": data.get("has_repository_copy"),
        },
    }


def update_record(doi: str, projected: dict | None, error: str | None) -> None:
    """Unpaywall's oa_status overrides OpenAlex's; oa_url overrides if Unpaywall has a PDF."""
    sql = """
        UPDATE bibliography_records
           SET oa_status = COALESCE(%(oa_status)s, oa_status),
               oa_url    = COALESCE(%(oa_url)s, oa_url),
               license   = COALESCE(license, %(license)s),
               source_provenance = jsonb_set(
                   COALESCE(source_provenance, '{}'::jsonb),
                   '{unpaywall}', %(provenance)s::jsonb, true),
               unpaywall_enriched_at = now()
         WHERE canonical_doi = %(doi)s
    """
    params = {
        "doi": doi,
        "oa_status": (projected or {}).get("oa_status"),
        "oa_url": (projected or {}).get("oa_url"),
        "license": (projected or {}).get("license"),
        "provenance": Jsonb({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
            **((projected or {}).get("source_provenance_unpaywall") or {}),
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
            SELECT canonical_doi FROM bibliography_records
             WHERE canonical_doi IS NOT NULL
               AND unpaywall_enriched_at IS NULL
             ORDER BY canonical_doi
             LIMIT %s
        """, (args.max if args.max else 1_000_000,))
        dois = [r["canonical_doi"] for r in cur.fetchall()]

    log(f"to enrich: {len(dois)} DOIs (workers={args.workers})", "unpaywall")

    n_ok = n_404 = n_skipped = 0
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one, d): d for d in dois}
        for i, fut in enumerate(as_completed(futures), 1):
            doi, data, err, mark_enriched = fut.result()
            if data is not None:
                update_record(doi, project(data), None)
                n_ok += 1
            elif mark_enriched:
                update_record(doi, None, err)
                if err == "404":
                    n_404 += 1
            else:
                n_skipped += 1
            if i % 200 == 0:
                rate = i / (time.monotonic() - t0)
                log(f"  progress {i}/{len(dois)} (ok={n_ok} 404={n_404} retry={n_skipped}) {rate:.1f}/s", "unpaywall")

    log(f"DONE: ok={n_ok} 404={n_404} retry-later={n_skipped} of {len(dois)} in {time.monotonic()-t0:.1f}s", "unpaywall")


if __name__ == "__main__":
    main()
