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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EXPORTS, db, log


# Default output: the pilot repo's exports/ directory. The existing
# weekly-harvest workflow's R2-upload step (aws s3 cp exports/ s3://.../latest/)
# picks up this file automatically, surfacing it at
# https://exports.tnbc.info/latest/bibliography_slim.json. The website's
# library page fetches from that R2 URL.
SLIM_DEFAULT_OUT = EXPORTS / "bibliography_slim.json"

# Headline corpus figures, uploaded to R2 by the same step. The website reads
# this at build time (see src/data/corpus.ts) so its "N records" claims track
# the pipeline rather than being retyped by hand each time the corpus grows.
STATS_DEFAULT_OUT = EXPORTS / "stats.json"


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


def build_stats(rows: list[dict], canonical_count: int) -> dict:
    """Headline figures for the website, so its pages never hardcode a count.

    Published vs canonical are deliberately separate: the website's corpus
    claims describe `public_bibliography` (what a visitor can actually search),
    while `canonical_records` is the full deduplicated harvest behind it.
    Conflating the two is how the site ended up claiming 101,106 "canonical"
    records when canonical was really 130,587.
    """
    years = [r.get("publication_year") for r in rows]
    years = [y for y in years if isinstance(y, int)]
    this_year = datetime.now(timezone.utc).year

    # Counted over the published view, not the canonical corpus: these figures
    # sit next to "N records" on the website, so they must describe the same
    # population a visitor is searching.
    retracted = sum(1 for r in rows if r.get("retraction_status") == "retracted")
    concern = sum(1 for r in rows if r.get("retraction_status") == "concern")
    free_oa = {"green", "gold", "bronze", "open", "hybrid", "diamond"}
    oa_free = sum(1 for r in rows if (r.get("oa_status") or "").lower() in free_oa)
    # The public view admits next-year dates for in-press records; a span
    # label reading "1985-2027" off a handful of those is just confusing.
    year_max = min(max(years), this_year) if years else None
    recent_from = this_year - 2
    recent = sum(1 for y in years if y >= recent_from)
    n = len(rows)
    return {
        "published_records": n,
        "canonical_records": canonical_count,
        "year_min": min(years) if years else None,
        "year_max": year_max,
        "recent_from": recent_from,
        "recent_to": this_year,
        "recent_records": recent,
        "recent_pct": round(recent / n * 100) if n else 0,
        "retracted_records": retracted,
        "concern_records": concern,
        "flagged_records": retracted + concern,
        "oa_free_records": oa_free,
        "oa_free_pct": round(oa_free / n * 100) if n else 0,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=SLIM_DEFAULT_OUT,
                    help=f"Output path (default: {SLIM_DEFAULT_OUT})")
    ap.add_argument("--stats-out", type=Path, default=STATS_DEFAULT_OUT,
                    help=f"Headline-stats output path (default: {STATS_DEFAULT_OUT})")
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
        cur.execute("SELECT count(*) AS n FROM bibliography_records")
        canonical_count = cur.fetchone()["n"]
    log(f"  fetched {len(rows):,} rows (canonical corpus: {canonical_count:,})", "slim")

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

    # Tiny companion file: the website reads this at build time so its corpus
    # figures track the pipeline instead of being retyped by hand.
    stats = build_stats(rows, canonical_count)
    args.stats_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.stats_out, "w") as fh:
        json.dump(stats, fh, indent=2)
        fh.write("\n")
    log(f"  wrote {args.stats_out.name}: "
        f"{stats['published_records']:,} published / "
        f"{stats['canonical_records']:,} canonical, "
        f"{stats['year_min']}-{stats['year_max']}, "
        f"{stats['recent_pct']}% since {stats['recent_from']}", "slim")

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
