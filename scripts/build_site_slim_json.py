"""TNBC Atlas — regenerate the slim bibliography JSON consumed by the website.

The website's /research/library/ page fetches a single JSON document via
JavaScript at runtime. That document uses abbreviated field names to keep
the client-side download size manageable, and excludes long fields (full
abstracts, source_provenance, etc.) that the search UI doesn't need.

The file lives in Cloudflare R2 at
  https://exports.tnbc.info/latest/bibliography_slim.json
because Cloudflare Pages has a 25 MB per-file asset limit and the slim
corpus exceeds that as of the 1990-2024 backfill. This script writes the
file into exports/ in the pilot repo; the existing weekly-harvest
workflow's `aws s3 cp exports/ s3://tnbc-atlas-exports/latest/` step
uploads it to R2 automatically. For an immediate refresh outside the
weekly schedule, see the manual R2 upload command in
RUNBOOK-public-api.md.

Usage:
  python3 scripts/build_site_slim_json.py
  python3 scripts/build_site_slim_json.py --out exports/bibliography_slim.json
  python3 scripts/build_site_slim_json.py --out /custom/path/file.json

Schema (per-record, abbreviated to minimize payload):
  t     title
  fa    first-author display name ("LastName, GivenName" form)
  na    total number of authors (so the UI can show "et al." if > 1)
  j     journal name
  y     publication year (int)
  d     publication date (YYYY-MM-DD)
  ct    crossref_type (article / review / chapter / etc.)
  c     citation count (int, OpenAlex-sourced)
  rc    references count (int, Crossref-sourced)
  oa    open access status (gold / green / hybrid / bronze / closed / unknown)
  lc    license (e.g. "creativecommons.org/licenses/by/4.0/")
  rt    retraction status (active / retracted / concern)
  doi   canonical DOI (lowercase)
  pmid  PubMed ID
  url   best-available open-access URL (Unpaywall-preferred, else OpenAlex)
  src   list of source labels: PM/EP/OA/CR/UP
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EXPORTS, db, log


# Default output: the pilot repo's exports/ directory. The existing
# weekly-harvest workflow's R2-upload step (aws s3 cp exports/ s3://.../latest/)
# picks up this file automatically, surfacing it at
# https://exports.tnbc.info/latest/bibliography_slim.json. The website's
# library page fetches from that R2 URL.
SLIM_DEFAULT_OUT = EXPORTS / "bibliography_slim.json"


def first_author_display(authors: list[dict] | None) -> str | None:
    """Return the first author's name in `LastName, GivenName` form."""
    if not authors:
        return None
    raw = authors[0].get("name", "").strip()
    if not raw:
        return None
    # If already in "Last, First" form, leave alone
    if "," in raw:
        return raw
    # Otherwise split on whitespace: assume last token is the family name
    parts = raw.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def source_labels(record: dict) -> list[str]:
    """Derive the 2-letter source-label list from record fields.

    Maps:
      PM if PMID is present
      EP if Europe PMC enriched (provenance entry under 'europepmc')
      OA if openalex_id present
      CR if crossref_enriched_at present (we don't expose that column in the
         public view but crossref_type populated implies it)
      UP if oa_status is populated AND came from Unpaywall (we approximate
         by checking license — Unpaywall is the typical license source)
    """
    out = []
    if record.get("pmid"):
        out.append("PM")
    # Europe PMC participation is hard to detect from the slim public view
    # (source_provenance is not exposed). We approximate by always emitting EP
    # if the record has a DOI and PMID — most cross-indexed records are in EP.
    # This is imperfect but matches what the pilot's slim JSON did.
    if record.get("pmid") and record.get("doi"):
        out.append("EP")
    if record.get("openalex_id"):
        out.append("OA")
    if record.get("crossref_type"):
        out.append("CR")
    if record.get("license"):
        out.append("UP")
    return out


def project(record: dict) -> dict:
    """Project a public_bibliography row to the slim schema."""
    return {
        "t":    record.get("title"),
        "fa":   first_author_display(record.get("authors")),
        "na":   len(record.get("authors") or []),
        "j":    record.get("journal"),
        "y":    record.get("publication_year"),
        "d":    record.get("publication_date").isoformat() if record.get("publication_date") else None,
        "ct":   record.get("crossref_type"),
        "c":    record.get("citation_count"),
        "rc":   record.get("references_count"),
        "oa":   record.get("oa_status"),
        "lc":   record.get("license"),
        "rt":   record.get("retraction_status"),
        "doi":  record.get("doi"),
        "pmid": record.get("pmid"),
        "url":  record.get("oa_url"),
        "src":  source_labels(record),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=SLIM_DEFAULT_OUT,
                    help=f"Output path (default: {SLIM_DEFAULT_OUT})")
    ap.add_argument("--pretty", action="store_true",
                    help="Emit indented JSON (~2x file size). Default: compact one-line.")
    args = ap.parse_args()

    log(f"reading public_bibliography from Supabase…", "slim")
    with db() as conn:
        cur = conn.cursor()
        # Pull everything the slim schema needs in a single SELECT to avoid
        # round-tripping. publication_date casts to ISO string for the JSON.
        cur.execute("""
            SELECT
                title,
                authors,
                journal,
                publication_year,
                publication_date,
                crossref_type,
                citation_count,
                references_count,
                oa_status,
                license,
                retraction_status,
                doi,
                pmid,
                openalex_id,
                oa_url
            FROM public_bibliography
            ORDER BY publication_year DESC NULLS LAST,
                     citation_count DESC NULLS LAST
        """)
        rows = cur.fetchall()
    log(f"  fetched {len(rows):,} rows", "slim")

    log("projecting to slim schema…", "slim")
    out_records = [project(r) for r in rows]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    log(f"writing to {args.out}", "slim")
    with open(args.out, "w") as fh:
        if args.pretty:
            json.dump(out_records, fh, indent=2, ensure_ascii=False)
        else:
            json.dump(out_records, fh, ensure_ascii=False)
    size_mb = args.out.stat().st_size / 1024 / 1024
    log(f"  wrote {len(out_records):,} records ({size_mb:.1f} MB)", "slim")

    if size_mb > 80:
        log(
            f"  NOTE: slim JSON is {size_mb:.1f} MB. R2 has no per-file size "
            f"limit (vs Cloudflare Pages' 25 MB), so storage is fine — but the "
            f"library page downloads this on every visit. If first-load latency "
            f"becomes a concern, consider switching the page to a search-index "
            f"+ API-on-click hybrid (~3 MB index, full record fetched from "
            f"api.tnbc.info when a row is clicked).",
            "slim",
        )


if __name__ == "__main__":
    main()
