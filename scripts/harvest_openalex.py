"""TNBC Atlas — OpenAlex harvester.

OpenAlex provides citation counts and ORCID-resolved author IDs for our records.
Used to enrich the canonical bibliography after PubMed/Europe PMC dedup.

Usage:
    python harvest_openalex.py            # full pilot window
    python harvest_openalex.py --max 200  # smoke test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    CONTACT_EMAIL,
    SNAPSHOTS,
    USER_AGENT,
    WINDOW_END,
    WINDOW_START,
    RateLimiter,
    log,
    record_run,
    write_jsonl,
)

OPENALEX = "https://api.openalex.org/works"


def search(start: str, end: str, out_fh=None, max_records: int | None = None,
            cursor_file: Path | None = None) -> int:
    """OpenAlex search with cursor pagination, polite-pool via mailto.
    If out_fh given, stream normalized JSONL incrementally.
    If cursor_file given, persist cursor between pages so runs can resume."""
    import json as _json
    rl = RateLimiter(qps=8.0)
    n = 0
    cursor = "*"
    if cursor_file and cursor_file.exists():
        cursor = cursor_file.read_text().strip() or "*"
        log(f"resume cursor loaded: {cursor[:30]}...", "openalex")
    per_page = 200
    # Three-era query — see harvest_pubmed.py QUERY for the rationale.
    # OpenAlex's title_and_abstract.search uses pipe-separated phrases for
    # OR semantics; the post-filter (scripts/filter_openalex_only.py)
    # scrubs OpenAlex-only records that don't pass the relevance scoring,
    # which absorbs the noise from broader pre-2005-era terms.
    filters = (
        "title_and_abstract.search:"
        '"triple-negative breast cancer"'
        '|"triple negative breast cancer"'
        "|TNBC"
        '|"basal-like breast cancer"'
        '|"basal like breast cancer"'
        '|"basal-like carcinoma"'
        '|"ER-negative breast cancer"'
        '|"estrogen receptor-negative breast cancer"'
        '|"estrogen receptor negative breast cancer"'
        '|"receptor-negative breast cancer"'
        f",from_publication_date:{start},to_publication_date:{end}"
    )
    while True:
        rl.wait()
        params = {
            "filter": filters,
            "per-page": per_page,
            "cursor": cursor,
            "mailto": CONTACT_EMAIL,
        }
        r = requests.get(OPENALEX, params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=60)
        if r.status_code != 200:
            log(f"HTTP {r.status_code}: {r.text[:200]}", "openalex")
            r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if out_fh is not None:
            for raw in results:
                out_fh.write(_json.dumps(normalize(raw), default=str) + "\n")
            out_fh.flush()
        n += len(results)
        log(f"page: +{len(results)} → cumulative {n} (count={data.get('meta', {}).get('count')})", "openalex")
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not results or not next_cursor:
            if cursor_file and cursor_file.exists():
                cursor_file.unlink()  # done — clear cursor
            break
        cursor = next_cursor
        if cursor_file:
            cursor_file.write_text(cursor)
        if max_records and n >= max_records:
            break
    return n


def normalize(w: dict) -> dict:
    """Project an OpenAlex Work into our common shape (subset; mostly enrichment fields)."""
    doi = w.get("doi")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]
    if doi:
        doi = doi.lower()

    pmid = None
    pmcid = None
    ids = w.get("ids", {}) or {}
    if (p := ids.get("pmid")):
        pmid = p.rsplit("/", 1)[-1]
    if (pmc := ids.get("pmcid")):
        pmcid = pmc.rsplit("/", 1)[-1]

    authors = []
    for au in w.get("authorships", []) or []:
        author = au.get("author", {}) or {}
        name = author.get("display_name")
        orcid = author.get("orcid")
        if orcid and orcid.startswith("https://orcid.org/"):
            orcid = orcid[len("https://orcid.org/"):]
        affs = [i.get("display_name") for i in (au.get("institutions") or []) if i.get("display_name")]
        if name:
            authors.append({"name": name, "orcid": orcid, "affiliations": affs})

    venue = (w.get("primary_location", {}) or {}).get("source", {}) or {}
    journal = venue.get("display_name")
    issn = (venue.get("issn_l") if isinstance(venue.get("issn_l"), str) else None)

    oa = w.get("open_access", {}) or {}
    oa_status = oa.get("oa_status") or "unknown"
    oa_url = oa.get("oa_url")

    countries = []
    for au in w.get("authorships", []) or []:
        for inst in au.get("institutions", []) or []:
            if (c := inst.get("country_code")):
                if c not in countries:
                    countries.append(c)

    pub_types = [t for t in [w.get("type")] if t]

    keywords = [(k.get("display_name") or k.get("keyword")) for k in (w.get("keywords") or []) if (k.get("display_name") or k.get("keyword"))]

    pub_date = w.get("publication_date")
    pub_year = w.get("publication_year")

    return {
        "openalex_id": w.get("id"),
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "title": w.get("title") or w.get("display_name"),
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index")),
        "journal": journal,
        "journal_issn": issn,
        "publication_date": pub_date,
        "publication_year": pub_year,
        "publication_type": pub_types,
        "mesh_terms": [],
        "keywords": keywords,
        "authors": authors,
        "language": w.get("language"),
        "countries": countries,
        "oa_status": oa_status,
        "oa_url": oa_url,
        "citation_count": w.get("cited_by_count"),
    }


def _reconstruct_abstract(inv: dict | None) -> str | None:
    """OpenAlex stores abstracts as an inverted index. Reconstruct word order."""
    if not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--out", default=None)
    ap.add_argument("--resume", action="store_true",
                     help="Resume from saved cursor; append to most recent JSONL")
    args = ap.parse_args()

    out_dir = SNAPSHOTS / "openalex"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    cursor_file = out_dir / f"cursor_{args.start}_{args.end}.txt"

    if args.resume:
        candidates = sorted([p for p in out_dir.glob("openalex_*.jsonl") if p.stat().st_size > 1000])
        if candidates and cursor_file.exists():
            out_path = candidates[-1]
            mode = "a"
            log(f"resume: appending to {out_path.name}", "openalex")
        else:
            out_path = Path(args.out) if args.out else out_dir / f"openalex_{ts}.jsonl"
            mode = "w"
    else:
        out_path = Path(args.out) if args.out else out_dir / f"openalex_{ts}.jsonl"
        mode = "w"
        if cursor_file.exists():
            cursor_file.unlink()

    log(f"Window {args.start} → {args.end}", "openalex")
    with open(out_path, mode) as out_fh:
        n = search(args.start, args.end, out_fh=out_fh, max_records=args.max,
                    cursor_file=cursor_file)
    log(f"Wrote/appended {n} records → {out_path}", "openalex")
    record_run("openalex", "TNBC title/abstract", args.start, args.end, n, "success", notes=f"out={out_path.name}")


if __name__ == "__main__":
    main()
