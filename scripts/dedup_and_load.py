"""TNBC Atlas — Dedup and load.

Reads the latest snapshot per source, dedups via DOI → PMID → fuzzy title,
and loads canonical records into Postgres bibliography_records.

Dedup pipeline (priority order):
    1. canonical_doi exact match (lowercased)
    2. pmid exact match
    3. (title fuzzy ≥ 0.92, first-author last name match, year within ±1)

Source priority for the canonical record body when conflicts occur:
    1. PubMed (richest MeSH and pub-type metadata)
    2. Europe PMC (broader coverage, includes preprints)
    3. OpenAlex (best for citations and ORCID)

OpenAlex citation_count and oa_status are merged onto whichever source won.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).parent))
from common import SNAPSHOTS, db, log


def latest_snapshot(source: str) -> Path | None:
    paths = sorted((SNAPSHOTS / source).glob(f"{source}_*.jsonl"), key=lambda p: p.stat().st_size, reverse=True)
    return paths[0] if paths else None


def load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def normalize_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().lower()
    if d.startswith("https://doi.org/"):
        d = d[len("https://doi.org/"):]
    if d.startswith("doi:"):
        d = d[4:]
    return d or None


def normalize_title(t: str | None) -> str:
    if not t:
        return ""
    t = re.sub(r"[^a-z0-9 ]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def first_author_last(authors: list[dict] | None) -> str:
    if not authors:
        return ""
    name = authors[0].get("name", "")
    return name.split()[-1].lower() if name else ""


def merge(canonical: dict, other: dict, source_label: str) -> dict:
    """Merge fields from `other` into `canonical` where canonical is missing them.
    Always overlay OpenAlex enrichment fields if newer."""
    for k, v in other.items():
        if v in (None, "", [], {}):
            continue
        if canonical.get(k) in (None, "", [], {}):
            canonical[k] = v
    # OpenAlex enrichment always wins for these
    if source_label == "openalex":
        for k in ("citation_count", "oa_status", "oa_url", "openalex_id", "countries"):
            if other.get(k) is not None:
                canonical[k] = other[k]
    canonical.setdefault("source_provenance", {})[source_label] = {
        k: other.get(k) for k in ("pmid", "doi", "pmcid", "openalex_id", "europepmc_id") if other.get(k)
    }
    return canonical


def main():
    log("=" * 60, "dedup")
    log("Phase 1 dedup + load", "dedup")
    sources = {
        "pubmed": latest_snapshot("pubmed"),
        "europepmc": latest_snapshot("europepmc"),
        "openalex": latest_snapshot("openalex"),
    }
    raw = {}
    for src, path in sources.items():
        if path:
            raw[src] = load_jsonl(path)
            log(f"loaded {len(raw[src])} from {src} ({path.name})", "dedup")
        else:
            raw[src] = []

    # Build dedup index over all records
    by_doi: dict[str, dict] = {}
    by_pmid: dict[str, dict] = {}
    canonical: list[dict] = []
    fuzzy_index: list[tuple[str, str, int, int]] = []  # (norm_title, last_name, year, canonical_idx)

    stats = defaultdict(int)

    # Process in priority order: PubMed → Europe PMC → OpenAlex
    for source_label in ("pubmed", "europepmc", "openalex"):
        for rec in raw[source_label]:
            stats[f"{source_label}_seen"] += 1
            doi = normalize_doi(rec.get("doi"))
            pmid = rec.get("pmid")
            if doi:
                rec["doi"] = doi

            target_idx = None
            matched_on = None

            # 1. DOI match
            if doi and doi in by_doi:
                target_idx = by_doi[doi]
                matched_on = "doi"
            # 2. PMID match
            elif pmid and pmid in by_pmid:
                target_idx = by_pmid[pmid]
                matched_on = "pmid"
            else:
                # 3. Fuzzy title + first-author last name + year ±1
                nt = normalize_title(rec.get("title"))
                la = first_author_last(rec.get("authors"))
                yr = rec.get("publication_year")
                if nt and la and yr and len(nt) > 20:
                    for cand_title, cand_last, cand_year, cand_idx in fuzzy_index:
                        if cand_last != la or abs(cand_year - yr) > 1:
                            continue
                        # quick length filter
                        if abs(len(cand_title) - len(nt)) > 30:
                            continue
                        if fuzz.ratio(cand_title, nt) >= 92:
                            target_idx = cand_idx
                            matched_on = "fuzzy_title"
                            break

            if target_idx is None:
                # New canonical record
                canonical.append(rec)
                idx = len(canonical) - 1
                if doi:
                    by_doi[doi] = idx
                if pmid:
                    by_pmid[pmid] = idx
                nt = normalize_title(rec.get("title"))
                la = first_author_last(rec.get("authors"))
                yr = rec.get("publication_year")
                if nt and la and yr:
                    fuzzy_index.append((nt, la, yr, idx))
                rec.setdefault("source_provenance", {})[source_label] = {
                    k: rec.get(k) for k in ("pmid", "doi", "pmcid", "openalex_id", "europepmc_id") if rec.get(k)
                }
                stats[f"{source_label}_new"] += 1
            else:
                # Merge into canonical
                canonical[target_idx] = merge(canonical[target_idx], rec, source_label)
                # Backfill DOI/PMID indexes if new IDs surfaced
                if doi and doi not in by_doi:
                    by_doi[doi] = target_idx
                if pmid and pmid not in by_pmid:
                    by_pmid[pmid] = target_idx
                stats[f"{source_label}_merged_{matched_on}"] += 1

    log(f"canonical records: {len(canonical)}", "dedup")
    for k in sorted(stats):
        log(f"  {k}: {stats[k]}", "dedup")

    # Load into Postgres
    with db() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE bibliography_records CASCADE;")
        cur.execute("TRUNCATE dedup_decisions;")
        n_loaded = 0
        for r in canonical:
            authors = r.get("authors") or []
            cur.execute(
                """
                INSERT INTO bibliography_records (
                    canonical_doi, pmid, pmcid, openalex_id,
                    title, abstract, authors, journal, journal_issn,
                    publication_date, publication_year, publication_type,
                    mesh_terms, keywords, language, countries,
                    funding_sources, oa_status, oa_url,
                    citation_count, source_provenance
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (canonical_doi) DO NOTHING
                """,
                (
                    r.get("doi"),
                    r.get("pmid"),
                    r.get("pmcid"),
                    r.get("openalex_id"),
                    r.get("title") or "[no title]",
                    r.get("abstract"),
                    Jsonb(authors),
                    r.get("journal"),
                    r.get("journal_issn"),
                    r.get("publication_date") if r.get("publication_date") and len(str(r.get("publication_date"))) >= 10 else None,
                    r.get("publication_year"),
                    r.get("publication_type") or [],
                    r.get("mesh_terms") or [],
                    r.get("keywords") or [],
                    r.get("language"),
                    r.get("countries") or [],
                    Jsonb(r.get("funding_sources") or []),
                    r.get("oa_status"),
                    r.get("oa_url"),
                    r.get("citation_count"),
                    Jsonb(r.get("source_provenance") or {}),
                ),
            )
            n_loaded += 1
        log(f"loaded {n_loaded} into Postgres", "dedup")

    # Summary
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM bibliography_records;")
        result = cur.fetchone()
        log(f"DB row count: {result['n']}", "dedup")


if __name__ == "__main__":
    main()
