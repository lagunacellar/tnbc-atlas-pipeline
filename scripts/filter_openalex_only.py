"""TNBC Atlas — Stricter post-filter for OpenAlex-only records.

OpenAlex's broader phrase matching pulls in records that PubMed and Europe PMC
don't surface. Many are legitimate (preprints, datasets, dissertations); some
are search-recall noise where 'TNBC' appears in non-TNBC contexts.

This script:
  1. Scans every record in the bibliography corpus (from the JSONL export).
  2. Identifies records found ONLY by OpenAlex (no PubMed, no Europe PMC).
  3. Scores each one for TNBC relevance based on title + abstract content.
  4. Decides keep / downgrade / drop, with auditable evidence per record.
  5. Writes a summary report and per-record CSV of decisions.

Records found by PubMed or Europe PMC are not filtered — those indexes have
their own quality controls and we trust their inclusion.

Inputs:  exports/bibliography.jsonl
Outputs: reports/openalex_postfilter.md
         reports/openalex_filter_decisions.csv
         exports/bibliography_filtered.jsonl  (corpus with relevance fields added)
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "exports" / "bibliography.jsonl"
REPORTS = ROOT / "reports"
EXPORTS = ROOT / "exports"
for d in (REPORTS, EXPORTS):
    d.mkdir(parents=True, exist_ok=True)

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
    """
    Returns (score, matched_patterns, decision).

    Scoring:
      +4  TNBC-abbrev or 'triple-negative breast cancer' phrase in title
      +3  TNBC-abbrev or 'triple-negative breast cancer' phrase in first 500 chars of abstract
      +2  any pattern elsewhere in abstract
      +1  weaker match (ER/PR/HER2 negativity construction)
       0  no match at all

    Decision:
      score >= 4  → keep (strong)
      score 2-3  → keep (moderate)
      score == 1 → downgrade (weak; flag for review)
      score == 0 → drop (no TNBC signal; almost certainly search-recall noise)
    """
    title = title or ""
    abstract = abstract or ""
    abstract_lead = abstract[:500]
    abstract_tail = abstract[500:]

    score = 0
    matched: list[str] = []

    # Title match (strongest signal)
    for name in ("TNBC_abbrev", "triple_negative_bc", "triple_negative_bn", "triple_negative_t"):
        if PATTERNS[name].search(title):
            score = max(score, 4)
            matched.append(f"title:{name}")

    # Abstract lead match
    for name in ("TNBC_abbrev", "triple_negative_bc", "triple_negative_bn", "triple_negative_t"):
        if PATTERNS[name].search(abstract_lead):
            if score < 3:
                score = 3
            matched.append(f"abstract_lead:{name}")

    # Abstract tail match (any pattern)
    for name, pat in PATTERNS.items():
        if pat.search(abstract_tail):
            if score < 2:
                score = 2
            matched.append(f"abstract_tail:{name}")

    # Weak ER/PR/HER2-negative construction anywhere
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
    print(f"Reading {CORPUS}")
    records: list[dict] = []
    with open(CORPUS) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"  loaded {len(records):,} records")

    decisions: list[dict] = []
    summary_counter: Counter[str] = Counter()
    sources_present: Counter[str] = Counter()

    for r in records:
        sp = r.get("source_provenance") or {}
        in_pm = "pubmed" in sp
        in_ep = "europepmc" in sp
        in_oa = "openalex" in sp

        # Source mix counter (independent of filter)
        if in_pm and in_ep and in_oa: sources_present["all_three"] += 1
        elif in_pm and in_ep:         sources_present["pm_ep_only"] += 1
        elif in_pm and in_oa:         sources_present["pm_oa_only"] += 1
        elif in_ep and in_oa:         sources_present["ep_oa_only"] += 1
        elif in_pm:                   sources_present["pm_only"] += 1
        elif in_ep:                   sources_present["ep_only"] += 1
        elif in_oa:                   sources_present["oa_only"] += 1
        else:                          sources_present["none"] += 1

        # Only filter OpenAlex-only records; trust PubMed/Europe PMC inclusion
        if in_pm or in_ep:
            r["tnbc_relevance_score"] = None
            r["tnbc_relevance_decision"] = "trusted_source"
            continue

        if not in_oa:
            r["tnbc_relevance_score"] = None
            r["tnbc_relevance_decision"] = "no_source"
            continue

        score, matched, decision = relevance_score(r.get("title"), r.get("abstract"))
        r["tnbc_relevance_score"] = score
        r["tnbc_relevance_decision"] = decision
        r["tnbc_relevance_matched"] = matched
        summary_counter[decision] += 1

        decisions.append({
            "doi": r.get("canonical_doi") or r.get("doi"),
            "openalex_id": r.get("openalex_id"),
            "title": (r.get("title") or "")[:120],
            "year": r.get("publication_year"),
            "journal": r.get("journal"),
            "score": score,
            "decision": decision,
            "matched": "; ".join(matched),
            "has_abstract": "Y" if r.get("abstract") else "N",
        })

    oa_only_total = sum(summary_counter.values())

    # Write per-decision CSV (sorted: drops first, then downgrades, then keeps)
    decisions.sort(key=lambda d: (d["decision"] != "drop", d["decision"] != "downgrade", -d["score"]))
    out_csv = REPORTS / "openalex_filter_decisions.csv"
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(decisions[0].keys()) if decisions else
                                          ["doi","openalex_id","title","year","journal","score","decision","matched","has_abstract"])
        w.writeheader()
        for d in decisions:
            w.writerow(d)
    print(f"Wrote {out_csv}")

    # Write filtered bibliography
    out_jsonl = EXPORTS / "bibliography_filtered.jsonl"
    with open(out_jsonl, "w") as fh:
        for r in records:
            fh.write(json.dumps(r, default=str) + "\n")
    print(f"Wrote {out_jsonl} ({len(records):,} records, with relevance fields)")

    # Summary report
    md = []
    md.append("# OpenAlex Post-Filter Report")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} canonical records  ")
    md.append(f"**Filter target:** OpenAlex-only records ({summary_counter.get('keep_strong',0)+summary_counter.get('keep_moderate',0)+summary_counter.get('downgrade',0)+summary_counter.get('drop',0):,} candidates)  ")
    md.append("**Rule:** Records found by PubMed or Europe PMC are passed through unchanged ('trusted_source'). OpenAlex-only records are scored on TNBC-phrase presence in title and abstract.")
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
    md.append(f"**Net effect:** of the {oa_only_total:,} OpenAlex-only records in the pilot, the stricter filter would drop **{n_drop:,}** as search-recall noise (no TNBC signal in title or abstract), flag **{n_downgrade:,}** for editorial review (weak ER/PR/HER2-negative construction only), and keep **{n_keep:,}** with confirmed TNBC signal.")
    md.append("")

    md.append("## Scoring rubric")
    md.append("")
    md.append("- Score 4 (`keep_strong`): TNBC abbreviation or 'triple-negative breast cancer' phrase in **title**.")
    md.append("- Score 3 (`keep_moderate`): TNBC phrase in the **first 500 chars of abstract**.")
    md.append("- Score 2 (`keep_moderate`): TNBC phrase **anywhere else in abstract**.")
    md.append("- Score 1 (`downgrade`): Only the weaker ER−/PR−/HER2− construction matches.")
    md.append("- Score 0 (`drop`): No TNBC signal in title or abstract.")
    md.append("")
    md.append("## Caveats")
    md.append("")
    md.append("- Records without an abstract field (preprints from some servers, conference abstracts, datasets) can score 0 even when they are legitimate TNBC items; the per-record CSV flags `has_abstract` so editorial review can prioritize those.")
    md.append("- The filter is one-directional: it does not promote PubMed/Europe PMC records or change their inclusion. PubMed and Europe PMC have their own quality controls and are trusted.")
    md.append("- The decision is auditable: every dropped/downgraded record retains its OpenAlex ID, DOI, and title in `reports/openalex_filter_decisions.csv`. Nothing is silently deleted from the bibliography &mdash; the filter writes a new `tnbc_relevance_decision` column rather than removing rows.")
    md.append("")
    md.append("## How to apply in production")
    md.append("")
    md.append("1. Run this script as the final step of the harvest pipeline, after dedup and enrichment.")
    md.append("2. Editorial team reviews `openalex_filter_decisions.csv` filtered to `decision IN ('drop', 'downgrade')`; ~~10 minutes per 100 entries.")
    md.append("3. Overrides (records flagged as drop but actually legitimate) get a manual `tnbc_relevance_decision = 'keep_manual'` and a note in the editorial log.")
    md.append("4. The website and exports filter to `tnbc_relevance_decision IN ('trusted_source', 'keep_strong', 'keep_moderate', 'keep_manual')` by default, with a separate view that includes downgrades for completionist users.")
    md.append("")

    (REPORTS / "openalex_postfilter.md").write_text("\n".join(md))
    print(f"Wrote {REPORTS / 'openalex_postfilter.md'}")

    # Console summary
    print()
    print("=== SUMMARY ===")
    print(f"OpenAlex-only records:    {oa_only_total:,}")
    print(f"  keep_strong:            {summary_counter.get('keep_strong', 0):,}")
    print(f"  keep_moderate:          {summary_counter.get('keep_moderate', 0):,}")
    print(f"  downgrade:              {summary_counter.get('downgrade', 0):,}")
    print(f"  drop:                   {summary_counter.get('drop', 0):,}")


if __name__ == "__main__":
    main()
