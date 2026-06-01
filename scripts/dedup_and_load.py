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


def all_snapshots(source: str) -> list[Path]:
    """Return every per-source snapshot, sorted oldest-first by modification time.

    The pilot workflow only ever wrote one snapshot per source, so an earlier
    version of this module returned a single 'latest_snapshot' — but for the
    year-by-year backfill workflow, each year produces a new per-source
    snapshot file and all of them need to be deduped together. The 'biggest
    file wins' heuristic in the prior implementation silently caused every
    backfill year to re-dedup the largest (pilot) snapshot and load nothing
    new. See logs/backfill_*.log if you suspect a recurrence.
    """
    return sorted(
        (SNAPSHOTS / source).glob(f"{source}_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
    )


# Kept for backward compatibility with any callers that still want
# the single-snapshot semantic; new code should prefer all_snapshots().
def latest_snapshot(source: str) -> Path | None:
    paths = all_snapshots(source)
    return paths[-1] if paths else None


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
    log("Phase 1 dedup + load (incremental upsert)", "dedup")
    # Read every per-source snapshot, concatenate, then let the in-memory
    # dedup engine collapse cross-snapshot duplicates. The downstream INSERT
    # uses ON CONFLICT (canonical_doi) DO NOTHING so already-loaded records
    # are safely re-processed without disturbing enrichment columns.
    sources = {
        "pubmed":    all_snapshots("pubmed"),
        "europepmc": all_snapshots("europepmc"),
        "openalex":  all_snapshots("openalex"),
    }
    raw: dict[str, list[dict]] = {}
    for src, paths in sources.items():
        raw[src] = []
        for path in paths:
            records = load_jsonl(path)
            raw[src].extend(records)
            log(f"loaded {len(records):>6} from {src}/{path.name}", "dedup")
        log(f"  → {src} total: {len(raw[src])} records across {len(paths)} snapshot(s)", "dedup")

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

    # Load into Postgres via COPY-into-temp + UPSERT.
    #
    # IMPORTANT — two distinct things to know about this load path:
    #
    # 1. Do NOT TRUNCATE the target table. An older version of this script
    #    TRUNCATEd before loading, which (a) made every run a full replace
    #    rather than an incremental upsert, and (b) wiped all enrichment
    #    columns (Crossref / Unpaywall / retraction status / topic tags /
    #    tier / tnbc_relevance_decision) populated by separate scripts.
    #    The ON CONFLICT (canonical_doi) DO NOTHING clause in the UPSERT
    #    below is sufficient for idempotency; already-loaded records are
    #    silently skipped, preserving their enrichment.
    #
    # 2. Do NOT row-by-row INSERT through the Supabase pooler. The pilot
    #    workflow's 14k-record single-INSERT-per-record loop took ~12 min
    #    over LAN and reliably dropped the connection partway when run
    #    over Supabase's Session pooler with 30k+ records. The COPY-into-
    #    temp + INSERT...SELECT...ON CONFLICT pattern below sends one
    #    bulk stream + one statement, eliminating per-record round-trips
    #    and the connection-drop failure mode. Same observable semantics.
    log(f"COPY-ing {len(canonical):,} canonical records into staging…", "dedup")
    with db() as conn:
        cur = conn.cursor()

        # 2a. Staging table — mirror the column types of bibliography_records.
        #     DATE column stored as TEXT here so invalid date strings don't
        #     break the COPY; we cast + null-out in the SELECT below.
        cur.execute("""
            CREATE TEMP TABLE staging_records (
                canonical_doi     TEXT,
                pmid              TEXT,
                pmcid             TEXT,
                openalex_id       TEXT,
                title             TEXT,
                abstract          TEXT,
                authors           JSONB,
                journal           TEXT,
                journal_issn      TEXT,
                publication_date  TEXT,
                publication_year  INT,
                publication_type  TEXT[],
                mesh_terms        TEXT[],
                keywords          TEXT[],
                language          TEXT,
                countries         TEXT[],
                funding_sources   JSONB,
                oa_status         TEXT,
                oa_url            TEXT,
                citation_count    INT,
                source_provenance JSONB
            ) ON COMMIT DROP
        """)

        # 2b. Stream all records via one COPY. psycopg's Jsonb adapter
        #     handles dict → JSONB serialization; array adapters handle
        #     Python list → TEXT[] formatting.
        with cur.copy("""
            COPY staging_records (
                canonical_doi, pmid, pmcid, openalex_id,
                title, abstract, authors, journal, journal_issn,
                publication_date, publication_year, publication_type,
                mesh_terms, keywords, language, countries,
                funding_sources, oa_status, oa_url,
                citation_count, source_provenance
            ) FROM STDIN
        """) as copy:
            for r in canonical:
                authors = r.get("authors") or []
                pub_date = r.get("publication_date")
                # Drop garbage-short date strings; let valid YYYY-MM-DD through.
                if pub_date and len(str(pub_date)) < 10:
                    pub_date = None
                copy.write_row((
                    r.get("doi"),
                    r.get("pmid"),
                    r.get("pmcid"),
                    r.get("openalex_id"),
                    r.get("title") or "[no title]",
                    r.get("abstract"),
                    Jsonb(authors),
                    r.get("journal"),
                    r.get("journal_issn"),
                    pub_date,
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
                ))

        # 2c. Upsert from staging into the main table in a single statement.
        #     ON CONFLICT (canonical_doi) DO NOTHING preserves enrichment on
        #     records that already exist. publication_date is cast back to
        #     DATE here, with invalid strings falling through to NULL.
        log("UPSERT-ing from staging into bibliography_records…", "dedup")
        cur.execute("""
            INSERT INTO bibliography_records (
                canonical_doi, pmid, pmcid, openalex_id,
                title, abstract, authors, journal, journal_issn,
                publication_date, publication_year, publication_type,
                mesh_terms, keywords, language, countries,
                funding_sources, oa_status, oa_url,
                citation_count, source_provenance
            )
            SELECT
                canonical_doi, pmid, pmcid, openalex_id,
                title, abstract, authors, journal, journal_issn,
                CASE WHEN publication_date ~ '^\d{4}-\d{2}-\d{2}'
                     THEN publication_date::date
                     ELSE NULL END,
                publication_year, publication_type,
                mesh_terms, keywords, language, countries,
                funding_sources, oa_status, oa_url,
                citation_count, source_provenance
            FROM staging_records
            ON CONFLICT (canonical_doi) DO NOTHING
        """)
        n_inserted = cur.rowcount
        n_skipped = len(canonical) - n_inserted
        log(f"INSERT-ed {n_inserted:,} new records; "
            f"skipped {n_skipped:,} already-present (ON CONFLICT)", "dedup")

    # Summary
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM bibliography_records;")
        result = cur.fetchone()
        log(f"DB row count: {result['n']:,}", "dedup")


if __name__ == "__main__":
    main()
