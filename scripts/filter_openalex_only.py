"""TNBC Atlas — TNBC relevance filter (Postgres-native, all sources).

Reads every record from `bibliography_records`, scores it for TNBC relevance
based on title + abstract phrase matching, and writes the decision back to
Postgres via a bulk COPY-into-temp + UPDATE-FROM pattern.

Originally only scored OpenAlex-only records (PubMed/Europe PMC records were
treated as `trusted_source`). After the harvester queries were broadened to
capture pre-2005 TNBC literature under its historical names ("basal-like
breast cancer", "ER-negative breast cancer"), false positives started
appearing in PubMed/EuropePMC records as well — so the filter now scores
ALL records uniformly. Records the original 'trusted source' rule would
have whitelisted (high score from title/abstract) keep the `keep_strong`
or `keep_moderate` decision and remain in the public view; records with
weak or no TNBC signal get `downgrade` or `drop` regardless of source.

Writes:
  Postgres bibliography_records.tnbc_relevance_score / _decision / _matched
  reports/openalex_filter_decisions.csv
  reports/openalex_postfilter.md

The public_bibliography view filters at query time on
  tnbc_relevance_decision IN ('trusted_source', 'keep_strong',
                              'keep_moderate', 'keep_manual')
plus a year-window and topic-tag gate (see sql/03_supabase_public_api.sql).

The script name kept for backward compatibility with the Makefile target
(`make filter`) and existing runbook references.
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

# TNBC and TNBC-adjacent phrase patterns (case-insensitive).
#
# Modern-era TNBC patterns score 4 in title / 3 in abstract lead.
# Transitional-era basal-like patterns score 3 in title / 2 in abstract lead — same
#   "kept in the corpus" treatment but a notch lower confidence than explicit TNBC.
# Pre-modern ER/PR/HER2-negative pattern is the weakest signal at score 1 (downgrade
#   unless something stronger also matches).
#
# Hyphen handling: scholarly titles often use typographically-correct Unicode
# dashes (U+2010 hyphen, U+2011 non-breaking hyphen, U+2013 en-dash, U+2014
# em-dash, U+2015 horizontal bar) where one might expect an ASCII hyphen-minus.
# The HYPHEN class catches all of them plus whitespace.
HYPHEN = r"[\s\-‐‑‒–—―]"

# Word-boundary handling: Python's \b is Unicode-aware, which means in mixed-script
# titles (e.g. "TNBC腫瘍における…") it treats the boundary between Latin "TNBC" and
# Japanese kanji as NOT a word boundary (both are \w characters). For TNBC
# specifically — an all-caps Latin abbreviation we want to recognize even when
# adjacent to non-Latin script — use an ASCII-only lookaround instead of \b.
ASCII_LETTERS_OR_DIGITS_BEFORE = r"(?<![A-Za-z0-9_])"
ASCII_LETTERS_OR_DIGITS_AFTER  = r"(?![A-Za-z0-9_])"

# Allow up to 5 non-word characters between key phrase words to handle
# parenthetical wrappers, commas, etc. E.g.: "(Triple Negative) Breast Cancer"
# has a closing-paren plus space between "Negative" and "Breast".
GAP = r"\W{1,5}"

PATTERNS = {
    # Modern explicit TNBC — lookaround instead of \b so mixed-script titles work
    "TNBC_abbrev":        re.compile(ASCII_LETTERS_OR_DIGITS_BEFORE + r"TNBC" + ASCII_LETTERS_OR_DIGITS_AFTER),
    # Allow plural "cancers", Unicode hyphens, and small punctuation gaps between words
    "triple_negative_bc": re.compile(rf"\btriple{HYPHEN}negative{GAP}breast{GAP}cancers?\b", re.I),
    "triple_negative_bn": re.compile(rf"\btriple{HYPHEN}negative{GAP}breast{GAP}neoplas", re.I),
    "triple_negative_t":  re.compile(rf"\btriple{HYPHEN}negative{GAP}(tumou?rs?|carcinomas?|disease|subtypes?)\b", re.I),
    # Transitional-era basal-like terminology (2000-2007 dominant)
    "basal_like_bc":      re.compile(rf"\bbasal{HYPHEN}?like{GAP}breast{GAP}cancers?\b", re.I),
    "basal_like_t":       re.compile(rf"\bbasal{HYPHEN}?like{GAP}(tumou?rs?|carcinomas?|subtypes?|phenotypes?)\b", re.I),
    # Pre-modern ER/PR/HER2-negative pattern (also widened for Unicode hyphens)
    "er_pr_her2_neg":     re.compile(
        rf"\b(?:ER{HYPHEN}?\(?\-?\)?|estrogen{HYPHEN}receptor{HYPHEN}negative).*"
        rf"(?:PR{HYPHEN}?\(?\-?\)?|progesterone{HYPHEN}receptor{HYPHEN}negative).*"
        rf"(?:HER2{HYPHEN}?\(?\-?\)?|HER2{HYPHEN}negative)", re.I | re.DOTALL),
}

MODERN_TNBC = ("TNBC_abbrev", "triple_negative_bc", "triple_negative_bn", "triple_negative_t")
BASAL_LIKE  = ("basal_like_bc", "basal_like_t")


def relevance_score(title: str | None, abstract: str | None) -> tuple[int, list[str], str]:
    """Returns (score, matched_patterns, decision). Scoring rubric in reports/openalex_postfilter.md.

    Rubric (highest match wins; matched_patterns lists every match found):
      4  Modern TNBC term in title                                    → keep_strong
      3  Modern TNBC in abstract lead (first 500 chars)
         OR basal-like term in title                                  → keep_moderate
      2  Modern TNBC anywhere else (abstract tail / late mention)
         OR basal-like term in abstract                               → keep_moderate
      1  ER/PR/HER2-negative explicit pattern only (no TNBC mention)  → downgrade
      0  No TNBC, no basal-like, no explicit triple-negative pattern  → drop
    """
    title = title or ""
    abstract = abstract or ""
    abstract_lead = abstract[:500]
    abstract_tail = abstract[500:]

    score = 0
    matched: list[str] = []

    # Tier 1: modern TNBC in title (strongest signal, score 4)
    for name in MODERN_TNBC:
        if PATTERNS[name].search(title):
            score = max(score, 4)
            matched.append(f"title:{name}")

    # Tier 2a: modern TNBC in abstract lead (score 3)
    for name in MODERN_TNBC:
        if PATTERNS[name].search(abstract_lead):
            score = max(score, 3)
            matched.append(f"abstract_lead:{name}")

    # Tier 2b: basal-like in title (score 3 — transitional-era equivalent)
    for name in BASAL_LIKE:
        if PATTERNS[name].search(title):
            score = max(score, 3)
            matched.append(f"title:{name}")

    # Tier 3: modern TNBC in abstract tail, OR basal-like anywhere in abstract (score 2)
    for name in MODERN_TNBC:
        if PATTERNS[name].search(abstract_tail):
            score = max(score, 2)
            matched.append(f"abstract_tail:{name}")
    for name in BASAL_LIKE:
        if PATTERNS[name].search(abstract_lead) or PATTERNS[name].search(abstract_tail):
            score = max(score, 2)
            matched.append(f"abstract:{name}")

    # Tier 4: explicit ER/PR/HER2-negative pattern, no TNBC or basal-like (score 1)
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

    # Snapshot of which records currently have a manual-keep flag; we never
    # overwrite those — editorial overrides win regardless of phrase scoring.
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT record_id FROM bibliography_records
             WHERE tnbc_relevance_decision = 'keep_manual'
        """)
        keep_manual_ids = {r["record_id"] for r in cur.fetchall()}
    log(f"  preserving {len(keep_manual_ids)} keep_manual editorial overrides", "filter")

    for r in records:
        in_pm, in_ep, in_oa = r["in_pm"], r["in_ep"], r["in_oa"]

        if in_pm and in_ep and in_oa: sources_present["all_three"] += 1
        elif in_pm and in_ep:         sources_present["pm_ep_only"] += 1
        elif in_pm and in_oa:         sources_present["pm_oa_only"] += 1
        elif in_ep and in_oa:         sources_present["ep_oa_only"] += 1
        elif in_pm:                   sources_present["pm_only"] += 1
        elif in_ep:                   sources_present["ep_only"] += 1
        elif in_oa:                   sources_present["oa_only"] += 1
        else:                         sources_present["none"] += 1

        # Editorial override wins; don't rescore.
        if r["record_id"] in keep_manual_ids:
            summary_counter["keep_manual"] += 1
            continue

        # No source at all (shouldn't happen post-dedup) — flag and skip.
        if not (in_pm or in_ep or in_oa):
            updates.append((r["record_id"], None, "no_source", None))
            summary_counter["no_source"] += 1
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
            "source_mix": ("+".join(s for s, v in (("PM", in_pm), ("EP", in_ep), ("OA", in_oa)) if v) or "none"),
            "score": score,
            "decision": decision,
            "matched": "; ".join(matched),
            "has_abstract": "Y" if r["abstract"] else "N",
        })

    scored_total = sum(summary_counter.values())
    log(f"  classified all sources: {scored_total:,}; writing back to Postgres", "filter")

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

    # Summary markdown — updated for the all-sources scope
    md: list[str] = []
    md.append("# TNBC Relevance Filter Report")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} canonical records  ")
    md.append(f"**Scope:** all records (PubMed, Europe PMC, OpenAlex)  ")
    md.append("**Rule:** every record scored on TNBC and TNBC-adjacent phrase presence in title and abstract. The previous 'PubMed/EPMC are trusted-source pass-through' rule was retired after the 2026-06 query broadening introduced false positives in those sources too. Editorial overrides (`keep_manual`) are never overwritten.")
    md.append("")
    md.append("## Source-presence breakdown (corpus-wide)")
    md.append("")
    md.append("| Source combination | Records |")
    md.append("|---|---:|")
    for k in ("all_three", "pm_ep_only", "pm_oa_only", "ep_oa_only", "pm_only", "ep_only", "oa_only", "none"):
        md.append(f"| {k.replace('_', ' ')} | {sources_present.get(k, 0):,} |")
    md.append("")
    md.append("## Filter decisions (all sources)")
    md.append("")
    md.append("| Decision | Count | Share |")
    md.append("|---|---:|---:|")
    for k in ("keep_strong", "keep_moderate", "downgrade", "drop", "keep_manual", "no_source"):
        n = summary_counter.get(k, 0)
        pct = (100 * n / max(1, scored_total))
        md.append(f"| **{k}** | {n:,} | {pct:.1f}% |")
    md.append(f"| **total** | {scored_total:,} | 100% |")
    md.append("")
    n_drop = summary_counter.get("drop", 0)
    n_downgrade = summary_counter.get("downgrade", 0)
    n_keep = (summary_counter.get("keep_strong", 0) + summary_counter.get("keep_moderate", 0)
              + summary_counter.get("keep_manual", 0))
    md.append(f"**Net effect:** of {scored_total:,} records, **{n_drop:,}** dropped (no TNBC / basal-like / ER−PR−HER2− signal), **{n_downgrade:,}** flagged for editorial review (downgrade), **{n_keep:,}** kept in the public view (keep_strong / keep_moderate / keep_manual).")
    md.append("")
    md.append("## How to apply in production")
    md.append("")
    md.append("This script writes decisions directly to `bibliography_records.tnbc_relevance_decision`. The `public_bibliography` view filters at query time:")
    md.append("")
    md.append("```sql")
    md.append("WHERE tnbc_relevance_decision IN")
    md.append("  ('keep_strong', 'keep_moderate', 'keep_manual', 'trusted_source')")
    md.append("  AND publication_year BETWEEN 1985 AND (extract(year from now())::int + 1)")
    md.append("  AND (array_length(topic_tags, 1) >= 1 OR tier IS NOT NULL")
    md.append("       OR tnbc_relevance_decision = 'keep_manual')")
    md.append("```")
    md.append("")
    md.append("So `drop` and `downgrade` records are excluded from the API, exports, and the website's library page by default — but remain in `bibliography_records` for audit and potential re-classification.")
    md.append("")
    (REPORTS / "openalex_postfilter.md").write_text("\n".join(md))
    log(f"wrote {REPORTS / 'openalex_postfilter.md'}", "filter")

    print()
    print("=== SUMMARY ===")
    print(f"All scored records:       {scored_total:,}")
    print(f"  keep_strong:            {summary_counter.get('keep_strong', 0):,}")
    print(f"  keep_moderate:          {summary_counter.get('keep_moderate', 0):,}")
    print(f"  downgrade:              {summary_counter.get('downgrade', 0):,}")
    print(f"  drop:                   {summary_counter.get('drop', 0):,}")
    print(f"  keep_manual (preserved):{summary_counter.get('keep_manual', 0):,}")
    print(f"  no_source (anomaly):    {summary_counter.get('no_source', 0):,}")


if __name__ == "__main__":
    main()
