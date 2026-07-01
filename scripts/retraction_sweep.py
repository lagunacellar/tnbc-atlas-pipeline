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
            SELECT record_id, canonical_doi, pmid, title, journal, retraction_status
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
            prior = r.get("retraction_status")
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
                "prior_status": prior,
                # "new this run" = the record's retraction status CHANGES as a
                # result of this sweep (a brand-new retraction, or a
                # concern->retracted escalation). Records that already carry
                # this status are NOT re-notified — this is what stops the
                # workflow from opening a duplicate issue every week.
                "is_new": status != (prior or "active"),
            })

    log(f"affected: {len(affected)} records", "retraction")
    newly = [a for a in affected if a["is_new"]]
    log(f"newly flagged this run (status changed): {len(newly)}", "retraction")

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

    # Report — full current snapshot of every affected record (for the artifact).
    rep_path = REPORTS / "retracted.csv"
    with open(rep_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "record_id", "status", "matched_on", "doi", "pmid",
            "title", "journal", "notice_doi", "retracted_date", "classification"
        ], extrasaction="ignore")
        w.writeheader()
        for a in affected:
            w.writerow(a)
    log(f"wrote {rep_path}", "retraction")

    # Report — ONLY records whose status changed this run. The workflow opens
    # an editorial issue only when this file has rows, so an unchanged week
    # produces no (duplicate) issue.
    new_path = REPORTS / "new_retractions.csv"
    with open(new_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "record_id", "status", "prior_status", "matched_on", "doi", "pmid",
            "title", "journal", "notice_doi", "retracted_date", "classification"
        ], extrasaction="ignore")
        w.writeheader()
        for a in newly:
            w.writerow(a)
    log(f"wrote {new_path} ({len(newly)} rows)", "retraction")

    # Pre-rendered Markdown body for the GitHub issue (only when there are
    # changes). The workflow passes this straight to `gh issue create
    # --body-file`, which sidesteps fragile CSV parsing in bash.
    if newly:
        md_path = REPORTS / "new_retractions.md"
        with open(md_path, "w") as fh:
            fh.write(f"The weekly retraction sweep detected **{len(newly)}** record(s) whose "
                     "retraction status changed this run:\n\n")
            for a in newly:
                title = (a.get("title") or "").replace("\n", " ").strip()
                line = f"- **{a['status']}** — {title}\n  record `{a['record_id']}`"
                if a.get("doi"):
                    line += f", DOI [{a['doi']}](https://doi.org/{a['doi']})"
                if a.get("notice_doi"):
                    line += f", retraction notice `{a['notice_doi']}`"
                line += f" (was: {a.get('prior_status') or 'active'})\n"
                fh.write(line)
            fh.write("\nThe full current retracted list is attached as the workflow artifact. "
                     "Check whether any cited synthesis pages need revision.\n")
        log(f"wrote {md_path}", "retraction")

    # Summary
    by_status = {}
    for a in affected:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    for s, c in sorted(by_status.items()):
        log(f"  {s}: {c}", "retraction")


if __name__ == "__main__":
    main()
