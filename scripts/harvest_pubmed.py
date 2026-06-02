"""TNBC Atlas — PubMed harvester.

Uses NCBI E-utilities (eSearch + eFetch) to pull TNBC records for the pilot window.
Caches raw XML JSON-encoded into snapshots/pubmed/.

Usage:
    python harvest_pubmed.py            # full pilot window
    python harvest_pubmed.py --max 200  # smoke test
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
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

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Query covering three eras of nomenclature for what is now called TNBC.
# Modern era (2005+): "triple-negative breast cancer" + variants.
# Transitional era (2000-2007): "basal-like breast cancer" — the molecular-subtype
#   term that dominated before TNBC became standard nomenclature.
# Pre-modern era (pre-2000): "ER-negative breast cancer" + variants — the only
#   nomenclature available when HER2 testing wasn't routine. Anchored on the
#   "breast cancer" qualifier so we don't pull in unrelated ER-negative cancer
#   literature; the OpenAlex post-filter scrubs any OpenAlex-only records that
#   don't actually concern TNBC biology.
QUERY = (
    '('
    '"triple-negative breast cancer"[Title/Abstract] '
    'OR "triple negative breast cancer"[Title/Abstract] '
    'OR "TNBC"[Title/Abstract] '
    'OR "basal-like breast cancer"[Title/Abstract] '
    'OR "basal like breast cancer"[Title/Abstract] '
    'OR "basal-like carcinoma"[Title/Abstract] '
    'OR "ER-negative breast cancer"[Title/Abstract] '
    'OR "estrogen receptor-negative breast cancer"[Title/Abstract] '
    'OR "estrogen receptor negative breast cancer"[Title/Abstract] '
    'OR "receptor-negative breast cancer"[Title/Abstract]'
    ')'
)


def esearch_pmids(query: str, mindate: str, maxdate: str) -> list[str]:
    """Return all PMIDs matching the query in the date window."""
    rl = RateLimiter(qps=2.5)
    pmids: list[str] = []
    retstart = 0
    retmax = 10000  # eSearch supports up to 100k but smaller pages are friendlier
    while True:
        rl.wait()
        params = {
            "db": "pubmed",
            "term": query,
            "datetype": "pdat",
            "mindate": mindate.replace("-", "/"),
            "maxdate": maxdate.replace("-", "/"),
            "retstart": retstart,
            "retmax": retmax,
            "retmode": "json",
            "tool": "tnbc-atlas",
            "email": CONTACT_EMAIL,
        }
        r = requests.get(f"{EUTILS}/esearch.fcgi", params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        data = r.json()
        result = data.get("esearchresult", {})
        batch = result.get("idlist", [])
        pmids.extend(batch)
        total = int(result.get("count", "0"))
        log(f"esearch: got {len(batch)}, cumulative {len(pmids)}/{total}", "pubmed")
        if len(pmids) >= total or not batch:
            break
        retstart += retmax
    return pmids


def efetch_batch(pmids: list[str], out_fh=None, start_idx: int = 0) -> list[dict]:
    """Fetch full records, batched. If out_fh given, stream JSONL incrementally."""
    import json as _json
    rl = RateLimiter(qps=2.5)
    out: list[dict] = []
    BATCH = 500  # NCBI is fine with 500 per POST
    for i in range(start_idx, len(pmids), BATCH):
        chunk = pmids[i:i + BATCH]
        rl.wait()
        params = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
            "tool": "tnbc-atlas",
            "email": CONTACT_EMAIL,
        }
        r = requests.post(f"{EUTILS}/efetch.fcgi", data=params,
                           headers={"User-Agent": USER_AGENT}, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        batch_recs = [parse_article(a) for a in root.findall(".//PubmedArticle")]
        out.extend(batch_recs)
        if out_fh is not None:
            for rec in batch_recs:
                out_fh.write(_json.dumps(rec, default=str) + "\n")
            out_fh.flush()
        log(f"efetch: parsed +{len(batch_recs)}, cumulative {len(out)} ({i + len(chunk)}/{len(pmids)})", "pubmed")
    return out


def _text(el):
    return "".join(el.itertext()).strip() if el is not None else None


def parse_article(art: ET.Element) -> dict:
    """Extract the fields we care about from a PubmedArticle XML element."""
    pmid = _text(art.find(".//MedlineCitation/PMID"))
    article = art.find(".//Article")
    title = _text(article.find("ArticleTitle")) if article is not None else None

    # Abstract: concatenate AbstractText elements (may have labeled sections)
    abst_parts = []
    for at in art.findall(".//Abstract/AbstractText"):
        label = at.attrib.get("Label")
        text = _text(at) or ""
        abst_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(p for p in abst_parts if p) or None

    # Journal
    journal = _text(art.find(".//Journal/Title"))
    issn = _text(art.find(".//Journal/ISSN"))

    # Pub date
    pub_year = _text(art.find(".//Article/Journal/JournalIssue/PubDate/Year"))
    pub_month = _text(art.find(".//Article/Journal/JournalIssue/PubDate/Month")) or "01"
    pub_day = _text(art.find(".//Article/Journal/JournalIssue/PubDate/Day")) or "01"
    pub_date = None
    if pub_year:
        try:
            month_num = pub_month if pub_month.isdigit() else datetime.strptime(pub_month[:3], "%b").month
            pub_date = f"{int(pub_year):04d}-{int(month_num):02d}-{int(pub_day):02d}"
        except (ValueError, AttributeError):
            pub_date = f"{int(pub_year):04d}-01-01"

    # Authors
    authors = []
    for au in art.findall(".//AuthorList/Author"):
        last = _text(au.find("LastName"))
        fore = _text(au.find("ForeName"))
        name = " ".join(filter(None, [fore, last])) or _text(au.find("CollectiveName"))
        affs = [_text(a) for a in au.findall("AffiliationInfo/Affiliation")]
        if name:
            authors.append({"name": name, "affiliations": [a for a in affs if a]})

    # Pub types
    pub_types = [_text(t) for t in art.findall(".//PublicationTypeList/PublicationType")]
    pub_types = [t for t in pub_types if t]

    # MeSH headings
    mesh = []
    for h in art.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
        if (n := _text(h)):
            mesh.append(n)

    # Keywords
    keywords = [_text(k) for k in art.findall(".//KeywordList/Keyword")]
    keywords = [k for k in keywords if k]

    # IDs: DOI, PMC
    doi = None
    pmcid = None
    for aid in art.findall(".//ArticleIdList/ArticleId"):
        idtype = aid.attrib.get("IdType")
        if idtype == "doi":
            doi = _text(aid)
        elif idtype == "pmc":
            pmcid = _text(aid)

    # Language
    lang = _text(art.find(".//Article/Language"))

    # Grants / funding
    grants = []
    for g in art.findall(".//GrantList/Grant"):
        grants.append({
            "grant_id": _text(g.find("GrantID")),
            "agency": _text(g.find("Agency")),
            "country": _text(g.find("Country")),
        })

    return {
        "pmid": pmid,
        "doi": doi.lower() if doi else None,
        "pmcid": pmcid,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "journal_issn": issn,
        "publication_date": pub_date,
        "publication_year": int(pub_year) if pub_year and pub_year.isdigit() else None,
        "publication_type": pub_types,
        "mesh_terms": mesh,
        "keywords": keywords,
        "authors": authors,
        "language": lang,
        "funding_sources": grants,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="Cap number of records (smoke test)")
    ap.add_argument("--start", default=WINDOW_START)
    ap.add_argument("--end", default=WINDOW_END)
    ap.add_argument("--out", default=None, help="Output JSONL path (default: snapshots/pubmed/<timestamp>.jsonl)")
    ap.add_argument("--resume", action="store_true", help="Resume by skipping PMIDs already in latest snapshot")
    args = ap.parse_args()

    out_dir = SNAPSHOTS / "pubmed"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = Path(args.out) if args.out else out_dir / f"pubmed_{ts}.jsonl"

    log(f"Window {args.start} → {args.end}", "pubmed")

    # Cache PMID list so re-runs / resumes don't re-eSearch
    pmid_cache = SNAPSHOTS / "pubmed" / f"pmids_{args.start}_{args.end}.txt"
    if pmid_cache.exists():
        with open(pmid_cache) as fh:
            pmids = [line.strip() for line in fh if line.strip()]
        log(f"Loaded cached PMID list ({len(pmids)})", "pubmed")
    else:
        pmids = esearch_pmids(QUERY, args.start, args.end)
        with open(pmid_cache, "w") as fh:
            fh.write("\n".join(pmids) + "\n")
        log(f"esearch returned {len(pmids)} PMIDs (cached)", "pubmed")

    if args.max:
        pmids = pmids[: args.max]
        log(f"Capped to {len(pmids)} for smoke test", "pubmed")

    # Resume: if any prior JSONL exists for this window, build set of already-fetched PMIDs
    already = set()
    resume_path = None
    if args.resume:
        candidates = sorted(out_dir.glob("pubmed_*.jsonl"))
        for p in candidates:
            with open(p) as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        if rec.get("pmid"):
                            already.add(rec["pmid"])
                    except Exception:
                        continue
        if candidates:
            resume_path = candidates[-1]
        log(f"Resume: {len(already)} PMIDs already on disk", "pubmed")

    todo = [p for p in pmids if p not in already]
    log(f"To fetch: {len(todo)} (skipping {len(pmids) - len(todo)})", "pubmed")

    # Stream JSONL incrementally
    mode = "a" if (args.resume and resume_path == out_path) else "w"
    if args.resume and resume_path is not None:
        out_path = resume_path  # append to existing file
        mode = "a"
    with open(out_path, mode) as out_fh:
        records = efetch_batch(todo, out_fh=out_fh)
        n = len(records)
    log(f"Appended {n} records → {out_path}", "pubmed")
    record_run("pubmed", QUERY, args.start, args.end, n, "success",
               notes=f"out={out_path.name}")


if __name__ == "__main__":
    main()
