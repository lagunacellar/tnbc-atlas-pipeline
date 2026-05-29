"""TNBC Atlas — Europe PMC harvester.

Uses Europe PMC's REST search API. Surfaces additional records not in PubMed
(some preprints, conference abstracts) and gives an OA flag.

Usage:
    python harvest_europepmc.py            # full pilot window
    python harvest_europepmc.py --max 200  # smoke test
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from common import (
    SNAPSHOTS,
    USER_AGENT,
    WINDOW_END,
    WINDOW_START,
    RateLimiter,
    log,
    record_run,
    write_jsonl,
)

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

QUERY_TEMPLATE = (
    '(TITLE:"triple-negative breast cancer" '
    'OR TITLE:"triple negative breast cancer" '
    'OR TITLE:"TNBC" '
    'OR ABSTRACT:"triple-negative breast cancer" '
    'OR ABSTRACT:"triple negative breast cancer") '
    'AND (FIRST_PDATE:[{start} TO {end}])'
)


def search(query: str, out_fh=None, max_records: int | None = None) -> int:
    """Cursor-paginate Europe PMC. If out_fh given, stream JSONL incrementally."""
    import json as _json
    rl = RateLimiter(qps=1.5)
    n = 0
    cursor = "*"
    page_size = 1000
    while True:
        rl.wait()
        params = {
            "query": query,
            "format": "json",
            "pageSize": page_size,
            "cursorMark": cursor,
            "resultType": "core",
        }
        r = requests.get(EPMC, params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
        data = r.json()
        results = data.get("resultList", {}).get("result", [])
        if out_fh is not None:
            for raw in results:
                out_fh.write(_json.dumps(normalize(raw), default=str) + "\n")
            out_fh.flush()
        n += len(results)
        next_cursor = data.get("nextCursorMark")
        log(f"page: +{len(results)} → cumulative {n} (hit_count={data.get('hitCount')})", "europepmc")
        if not next_cursor or next_cursor == cursor or not results:
            break
        cursor = next_cursor
        if max_records and n >= max_records:
            break
    return n


def normalize(r: dict) -> dict:
    """Project Europe PMC's response into our common shape."""
    pmid = r.get("pmid")
    pmcid = r.get("pmcid")
    doi = r.get("doi")
    title = r.get("title")
    abstract = r.get("abstractText")
    journal = r.get("journalTitle")
    issn = r.get("issn") or r.get("essn")
    pub_date = r.get("firstPublicationDate") or r.get("electronicPublicationDate")
    pub_year = None
    if pub_date and len(pub_date) >= 4:
        try:
            pub_year = int(pub_date[:4])
        except ValueError:
            pass

    authors = []
    for au in (r.get("authorList", {}) or {}).get("author", []) or []:
        name = au.get("fullName")
        affs = []
        for aff in (au.get("authorAffiliationDetailsList", {}) or {}).get("authorAffiliation", []) or []:
            if (a := aff.get("affiliation")):
                affs.append(a)
        if name:
            authors.append({"name": name, "affiliations": affs})

    pub_types = []
    if (pt := r.get("pubType")):
        pub_types = [pt]
    if (ptl := r.get("pubTypeList", {}).get("pubType")):
        pub_types = ptl if isinstance(ptl, list) else [ptl]

    keywords = []
    if (kwl := r.get("keywordList", {}).get("keyword")):
        keywords = kwl if isinstance(kwl, list) else [kwl]

    mesh = []
    for m in (r.get("meshHeadingList", {}) or {}).get("meshHeading", []) or []:
        if (n := m.get("descriptorName")):
            mesh.append(n)

    is_oa = r.get("isOpenAccess") == "Y"
    full_text_urls = []
    for url in (r.get("fullTextUrlList", {}) or {}).get("fullTextUrl", []) or []:
        if url.get("availability") == "Open access" and (u := url.get("url")):
            full_text_urls.append(u)

    return {
        "pmid": pmid,
        "doi": doi.lower() if doi else None,
        "pmcid": pmcid,
        "europepmc_id": r.get("id"),
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "journal_issn": issn,
        "publication_date": pub_date,
        "publication_year": pub_year,
        "publication_type": pub_types,
        "mesh_terms": mesh,
        "keywords": keywords,
        "authors": authors,
        "language": r.get("language"),
        "oa_status": "open" if is_oa else "unknown",
        "oa_url": full_text_urls[0] if full_text_urls else None,
        "is_preprint": r.get("source") in {"PPR"},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out_dir = SNAPSHOTS / "europepmc"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else out_dir / f"europepmc_{ts}.jsonl"

    query = QUERY_TEMPLATE.format(start=args.start, end=args.end)
    log(f"Query: {query}", "europepmc")
    with open(out_path, "w") as out_fh:
        n = search(query, out_fh=out_fh, max_records=args.max)
    log(f"Wrote {n} records → {out_path}", "europepmc")
    record_run("europepmc", query, args.start, args.end, n, "success", notes=f"out={out_path.name}")


if __name__ == "__main__":
    main()
