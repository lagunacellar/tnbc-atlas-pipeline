"""TNBC Atlas — Stricter post-filter for OpenAlex-only records (Postgres-native).

Reads every record from `bibliography_records`, scores OpenAlex-only records for
TNBC relevance based on title + abstract phrase matching, and writes the
decision back to Postgres via a bulk COPY-into-temp + UPDATE-FROM pattern.

Records found by PubMed or Europe PMC are passed through unchanged
(`tnbc_relevance_decision = 'trusted_source'`).

Writes:
  Postgres bibliography_records.tnbc_relevance_score / _decision / _matched
  reports/openalex_filter_decisions.csv
  reports/openalex_postfilter.md

The previous JSONL output (exports/bibliography_filtered.jsonl) is no longer
produced. The canonical bibliography.jsonl from export_and_report.py now
includes these columns.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path

from psycopg.types.json import Jsonb  # noqa: F401  (kept for parity with tag_topics)

sys.path.insert(0, str(Path(__file__).parent))
from common import REPORTS, db, log

REPORTS.mkdir(parents=True, exist_ok=True)

# Strict TNBC phrase patterns (case-insensitive). \b ensures word boundaries.
PATTERNS = {
    "TNBC_abbrev":        re.compile(r"\bTNBC\b"),
    "triple_negative_bc": re.compile(r"\btriple[\s\-]negative\s+breast\s+cancer\b", re.I),
    "triple_negative_bn": re.compile(r"\btriple[\s\-]negative\s+breast\s+neoplas", re.I),
    "triple_negative_t":  re.compile(r"\btriple[\s\-]negative\s+(tumou?r|carcinoma|disease|subtype)\b", re.I),
    "er_pr_her2_neg":     re.compile(
        r"\b(?:ER[\s\-]?\(?\-?\)?|estrogen[\s\-]receptor[\s\-]negative).*"
        r"(?:PR[\s\-]?\(?\-?\)?|progesterone[\s\-]receptor[\s\-]negative).*"
        r"(?:HER2[\s\-]?\(?\-?\)?|HER2[\s\-]negative)", re.I | re.DOTALL),
}


def relevance_score(title: str | None, abstract: str | None) -> tuple[int, list[str], str]:
    """Returns (score, matched_patterns, decision). Scoring rubric in reports/openalex_postfilter.md."""
    title = title or ""
    abstract = abstract or ""
    abstract_lead = abstract[:500]
    abstract_tail = abstract[500:]

    score = 0
    matched: list[str] = []

    for name in ("TNBC_abbrev", "triple_negative_bc", "triple_negative_bn", "triple_negative_t"):
        if PATTERNS[name].search(title):
            score = max(score, 4)
            matched.append(f"title:{name}")
    for name in ("TNBC_abbrev", "triple_negative_bc", "triple_negative_bn", "triple_negative_t"):
        if PATTERNS[name].search(abstract_lead):
            if score < 3:
                score = 3
            matched.append(f"abstract_lead:{name}")
    for name, pat in PATTERNS.items():
        if pat.search(abstract_tail):
            if score < 2:
                score = 2
            matched.append(f"abstract_tail:{name}")
    if score == 0 and PATTERNS["er_pr_her2_neg"].search(title + " " + abstract):
        score = 1
        matched.append("anywhere:er_pr_her2_neg")

    if score >= 4:
        decision = "keep_strong"
    elif score >= 2:
        decision = "keep_moderate"
    elif score == 1:
        decision = "downgrade"
    else:
        decision = "drop"
    return score, matched, decision


def main() -> None:
    log("reading records from Postgres", "filter")
    with db() as conn:
        cur = conn.cursor()
        # Use JSONB ? operator to check key existence in source_provenance.
        cur.execute("""
            SELECT record_id, title, abstract, journal, publication_year,
                   canonical_doi, pmid, openalex_id,
                   (source_provenance ? 'pubmed')    AS in_pm,
                   (source_provenance ? 'europepmc') AS in_ep,
                   (source_provenance ? 'openalex')  AS in_oa
              FROM bibliography_records
        """)
        records = cur.fetchall()
    log(f"  loaded {len(records):,} records", "filter")

    updates: list[tuple] = []  # (record_id, score, decision, matched)
    decisions: list[dict] = []
    summary_counter: Counter[str] = Counter()
    sources_present: Counter[str] = Counter()

    for r in records:
        in_pm, in_ep, in_oa = r["in_pm"], r["in_ep"], r["in_oa"]

        if in_pm and in_ep and in_oa: sources_present["all_three"] += 1
        elif in_pm and in_ep:         sources_present["pm_ep_only"] += 1
        elif in_pm and in_oa:         sources_present["pm_oa_only"] += 1
        elif in_ep and in_oa:         sources_present["ep_oa_only"] += 1
        elif in_pm:                   sources_present["pm_only"] += 1
        elif in_ep:                   sources_present["ep_only"] += 1
        elif in_oa:                   sources_present["oa_only"] += 1
        else:                          sources_present["none"] += 1

        if in_pm or in_ep:
            updates.append((r["record_id"], None, "trusted_source", None))
            continue
        if not in_oa:
            updates.append((r["record_id"], None, "no_source", None))
            continue

        score, matched, decision = relevance_score(r["title"], r["abstract"])
        updates.append((r["record_id"], score, decision, matched))
        summary_counter[decision] += 1
        decisions.append({
            "doi": r.get("canonical_doi"),
            "openalex_id": r.get("openalex_id"),
            "title": (r["title"] or "")[:120],
            "year": r.get("publication_year"),
            "journal": r.get("journal"),
            "score": score,
            "decision": decision,
            "matched": "; ".join(matched),
            "has_abstract": "Y" if r["abstract"] else "N",
        })

    oa_only_total = sum(summary_counter.values())
    log(f"  classified OA-only: {oa_only_total:,}; writing back to Postgres", "filter")

    # Bulk write: COPY into temp table, then UPDATE FROM. Single transaction.
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TEMP TABLE filter_updates (
                record_id UUID PRIMARY KEY,
                score     INT,
                decision  TEXT,
                matched   TEXT[]
            ) ON COMMIT DROP
        """)
        with cur.copy("COPY filter_updates (record_id, score, decision, matched) FROM STDIN") as copy:
            for row in updates:
                copy.write_row(row)
        cur.execute("""
            UPDATE bibliography_records r
               SET tnbc_relevance_score    = u.score,
                   tnbc_relevance_decision = u.decision,
                   tnbc_relevance_matched  = u.matched
              FROM filter_updates u
             WHERE r.record_id = u.record_id
        """)
        log(f"  UPDATEd {len(updates):,} rows", "filter")

    # Per-decision CSV — same format as before
    decisions.sort(key=lambda d: (d["decision"] != "drop", d["decision"] != "downgrade", -(d["score"] or 0)))
    csv_path = REPORTS / "openalex_filter_decisions.csv"
    if decisions:
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(decisions[0].keys()))
            w.writeheader()
            for d in decisions:
                w.writerow(d)
    log(f"wrote {csv_path}", "filter")

    # Summary markdown — same content as before
    md: list[str] = []
    md.append("# OpenAlex Post-Filter Report")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} canonical records  ")
    md.append(f"**Filter target:** OpenAlex-only records ({oa_only_total:,} candidates)  ")
    md.append("**Rule:** Records found by PubMed or Europe PMC are passed through unchanged (`trusted_source`). OpenAlex-only records are scored on TNBC-phrase presence in title and abstract.")
    md.append("")
    md.append("## Source-presence breakdown (corpus-wide)")
    md.append("")
    md.append("| Source combination | Records |")
    md.append("|---|---:|")
    for k in ("all_three", "pm_ep_only", "pm_oa_only", "ep_oa_only", "pm_only", "ep_only", "oa_only", "none"):
        md.append(f"| {k.replace('_', ' ')} | {sources_present.get(k, 0):,} |")
    md.append("")
    md.append("## Filter decisions on OpenAlex-only records")
    md.append("")
    md.append("| Decision | Count | Share |")
    md.append("|---|---:|---:|")
    for k in ("keep_strong", "keep_moderate", "downgrade", "drop"):
        n = summary_counter.get(k, 0)
        pct = (100 * n / max(1, oa_only_total))
        md.append(f"| **{k}** | {n:,} | {pct:.1f}% |")
    md.append(f"| **total OA-only** | {oa_only_total:,} | 100% |")
    md.append("")
    n_drop = summary_counter.get("drop", 0)
    n_downgrade = summary_counter.get("downgrade", 0)
    n_keep = summary_counter.get("keep_strong", 0) + summary_counter.get("keep_moderate", 0)
    md.append(f"**Net effect:** of {oa_only_total:,} OpenAlex-only records, **{n_drop:,}** would be dropped (no TNBC signal), **{n_downgrade:,}** flagged for editorial review, **{n_keep:,}** kept.")
    md.append("")
    md.append("## How to apply in production")
    md.append("")
    md.append("This script now writes decisions directly to `bibliography_records.tnbc_relevance_decision`. The `public_bibliography` view filters them at query time, so `drop` records are excluded from the API and exports by default. Editorial overrides (`keep_manual`) can be applied via SQL UPDATE.")
    md.append("")
    (REPORTS / "openalex_postfilter.md").write_text("\n".join(md))
    log(f"wrote {REPORTS / 'openalex_postfilter.md'}", "filter")

    print()
    print("=== SUMMARY ===")
    print(f"OpenAlex-only records:    {oa_only_total:,}")
    print(f"  keep_strong:            {summary_counter.get('keep_strong', 0):,}")
    print(f"  keep_moderate:          {summary_counter.get('keep_moderate', 0):,}")
    print(f"  downgrade:              {summary_counter.get('downgrade', 0):,}")
    print(f"  drop:                   {summary_counter.get('drop', 0):,}")


if __name__ == "__main__":
    main()
