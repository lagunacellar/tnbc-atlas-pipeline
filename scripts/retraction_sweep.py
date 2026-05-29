"""TNBC Atlas — Retraction sweep.

Downloads the Crossref-hosted Retraction Watch CSV (Crossref Labs API) and
cross-references against bibliography_records by DOI and PMID.

Sets retraction_status to:
  - 'retracted'        if the row is a retraction notice itself, or matches a retracted item
  - 'concern'          for "Expression of Concern" classes
  - 'active'           default (untouched)

Also stamps retraction_notice_doi and retracted_at where available.

Writes:
  reports/retracted.csv  — affected records with notice DOI and date

Usage:
    python retraction_sweep.py
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import CONTACT_EMAIL, REPORTS, USER_AGENT, db, log

# Crossref Labs hosts the Retraction Watch DB; provide email per their guidance
RW_URL = f"https://api.labs.crossref.org/data/retractionwatch?{CONTACT_EMAIL}"


def download_rw_csv() -> str:
    log(f"downloading Retraction Watch CSV from Crossref Labs", "retraction")
    r = requests.get(RW_URL, headers={"User-Agent": USER_AGENT}, timeout=120)
    r.raise_for_status()
    log(f"  payload: {len(r.content)/1024/1024:.1f} MB", "retraction")
    return r.text


def parse_rw(csv_text: str) -> list[dict]:
    """Return list of {doi, pmid, notice_doi, retracted_date, classification}."""
    out = []
    rdr = csv.DictReader(io.StringIO(csv_text))
    for row in rdr:
        original_doi = (row.get("OriginalPaperDOI") or "").strip().lower()
        original_pmid = (row.get("OriginalPaperPubMedID") or "").strip()
        notice_doi = (row.get("RetractionDOI") or "").strip().lower()
        retracted_date = (row.get("RetractionDate") or "").strip()
        notice_type = (row.get("RetractionNature") or "").strip()
        if not (original_doi or original_pmid):
            continue
        retracted_iso = None
        if retracted_date:
            for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
                try:
                    retracted_iso = datetime.strptime(retracted_date, fmt).date().isoformat()
                    break
                except ValueError:
                    pass
        out.append({
            "doi": original_doi or None,
            "pmid": original_pmid or None,
            "notice_doi": notice_doi or None,
            "retracted_date": retracted_iso,
            "classification": notice_type or None,
        })
    return out


def main():
    csv_text = download_rw_csv()
    notices = parse_rw(csv_text)
    log(f"parsed {len(notices)} retraction notices", "retraction")

    rw_by_doi = {n["doi"]: n for n in notices if n["doi"]}
    rw_by_pmid = {n["pmid"]: n for n in notices if n["pmid"]}

    # Pull all our records' identifiers
    affected = []
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT record_id, canonical_doi, pmid, title, journal
              FROM bibliography_records
        """)
        rows = cur.fetchall()

    log(f"checking {len(rows)} canonical records against retraction list", "retraction")

    for r in rows:
        doi = r.get("canonical_doi")
        pmid = r.get("pmid")
        notice = None
        match_kind = None
        if doi and doi in rw_by_doi:
            notice = rw_by_doi[doi]; match_kind = "doi"
        elif pmid and pmid in rw_by_pmid:
            notice = rw_by_pmid[pmid]; match_kind = "pmid"
        if notice:
            cls = (notice["classification"] or "").lower()
            if "concern" in cls:
                status = "concern"
            else:
                status = "retracted"
            affected.append({
                "record_id": str(r["record_id"]),
                "doi": doi,
                "pmid": pmid,
                "title": r.get("title"),
                "journal": r.get("journal"),
                "matched_on": match_kind,
                "notice_doi": notice["notice_doi"],
                "retracted_date": notice["retracted_date"],
                "classification": notice["classification"],
                "status": status,
            })

    log(f"affected: {len(affected)} records", "retraction")

    # Update DB
    with db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE bibliography_records SET retraction_status = 'active' WHERE retraction_status IS NULL;")
        for a in affected:
            cur.execute("""
                UPDATE bibliography_records
                   SET retraction_status   = %s,
                       retraction_notice_doi = %s,
                       retracted_at        = %s
                 WHERE record_id = %s
            """, (a["status"], a["notice_doi"], a["retracted_date"], a["record_id"]))

    # Report
    rep_path = REPORTS / "retracted.csv"
    with open(rep_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "record_id", "status", "matched_on", "doi", "pmid",
            "title", "journal", "notice_doi", "retracted_date", "classification"
        ])
        w.writeheader()
        for a in affected:
            w.writerow(a)
    log(f"wrote {rep_path}", "retraction")

    # Summary
    by_status = {}
    for a in affected:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    for s, c in sorted(by_status.items()):
        log(f"  {s}: {c}", "retraction")


if __name__ == "__main__":
    main()
