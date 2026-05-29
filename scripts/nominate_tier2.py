"""TNBC Atlas — Tier-2 algorithmic nomination.

Per the Phase 1 plan §7, tier-2 records are 'landmark; high-citation and
practice-changing' — algorithmically nominated, then human-confirmed by the
editorial board before promotion in the database.

This script generates the algorithmic candidate list. It does NOT promote
records to tier-2 in the database; that's an editorial decision recorded in
a follow-up curation file (analogous to curation/tier1_seed.yml).

Scoring (additive):
  Citation percentile within publication-year cohort:
    top 5%   → +5
    top 10%  → +3
    top 25%  → +1
  Publication type:
    Clinical Trial, Phase III       → +3
    Randomized Controlled Trial     → +2
    Systematic Review / Meta-Analysis → +2
  High-impact journal               → +2
  Named pivotal-trial keyword in title (KEYNOTE, ASCENT, etc.) → +4

Records reaching score ≥ 5 are nominated.

Exclusions:
  - Records already in tier-1 seed list (`curation/tier1_seed.yml`)
  - Retracted or expression-of-concern records
  - Records dropped by the OpenAlex post-filter

Inputs:  exports/bibliography_tagged.jsonl  (post-tagging output; falls back to filtered, then raw)
         curation/tier1_seed.yml
Outputs: reports/tier2_candidates.csv
         reports/tier2_nomination.md
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import quantiles

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "exports"
REPORTS = ROOT / "reports"
CURATION = ROOT / "curation"
REPORTS.mkdir(parents=True, exist_ok=True)

INPUT_PATHS = [
    EXPORTS / "bibliography_tagged.jsonl",
    EXPORTS / "bibliography_filtered.jsonl",
    EXPORTS / "bibliography.jsonl",
]

# Journals commonly considered top-tier for breast cancer / oncology / general medicine
HIGH_IMPACT_JOURNALS = {
    j.lower() for j in [
        "New England Journal of Medicine", "NEJM",
        "The Lancet", "Lancet",
        "The Lancet Oncology", "Lancet Oncology",
        "JAMA", "JAMA Oncology",
        "Journal of Clinical Oncology", "JCO",
        "Annals of Oncology",
        "Nature", "Nature Medicine", "Nature Cancer",
        "Nature Reviews Clinical Oncology", "Nature Reviews Cancer",
        "Cell", "Cancer Cell",
        "Science", "Science Translational Medicine",
        "Cancer Discovery",
        "Clinical Cancer Research",
        "Cancer Research",
        "Journal of the National Cancer Institute",
        "JNCI",
        "Breast Cancer Research",
        "npj Breast Cancer",
    ]
}

# Substrings (case-insensitive) that signal a named pivotal trial in TNBC / breast cancer
PIVOTAL_TRIAL_KEYWORDS = [
    "KEYNOTE-522", "KEYNOTE-355", "KEYNOTE-119", "KEYNOTE-086", "KEYNOTE-173",
    "OlympiA", "OlympiAD", "ASCENT", "DESTINY-Breast", "EMBRACA",
    "IMpassion130", "IMpassion131", "BrighTNess", "GeparNuevo", "GeparX",
    "CREATE-X", "I-SPY 2", "I-SPY2", "I-SPY", "TROPiCS", "TROPION",
    "TONIC", "MEDIOLA", "TBCRC",
    # Lower-confidence general trial markers (still useful)
    "phase 3 trial", "phase III trial", "phase 3 randomized", "phase III randomized",
]


def load_tier1_dois() -> set[str]:
    tier1_path = CURATION / "tier1_seed.yml"
    if not tier1_path.exists():
        return set()
    data = yaml.safe_load(tier1_path.read_text())
    dois = set()
    for e in data.get("entries", []):
        if e.get("doi"):
            dois.add(e["doi"].lower())
    return dois


def percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Returns the percentile (0-100) of value within sorted_values."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    # Count how many are <= value
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return 100.0 * lo / n


def journal_is_high_impact(journal: str | None) -> bool:
    if not journal:
        return False
    return journal.lower().strip() in HIGH_IMPACT_JOURNALS


def has_pivotal_trial_keyword(title: str | None) -> str | None:
    if not title:
        return None
    t = title
    for kw in PIVOTAL_TRIAL_KEYWORDS:
        if kw.lower() in t.lower():
            return kw
    return None


def main() -> None:
    in_path = next((p for p in INPUT_PATHS if p.exists()), None)
    if not in_path:
        print(f"No input found. Looked in: {INPUT_PATHS}", file=sys.stderr)
        sys.exit(1)
    print(f"Reading {in_path.name}")

    records: list[dict] = []
    with open(in_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"  loaded {len(records):,} records")

    tier1_dois = load_tier1_dois()
    print(f"  loaded {len(tier1_dois)} tier-1 seed DOIs (exclude from nomination)")

    # Build per-year citation distributions (only for records with citation_count not null)
    cohort_citations: dict[int, list[int]] = defaultdict(list)
    for r in records:
        yr = r.get("publication_year")
        cc = r.get("citation_count")
        if yr and cc is not None:
            cohort_citations[yr].append(cc)
    for yr in cohort_citations:
        cohort_citations[yr].sort()
    print(f"  citation distributions ready for {len(cohort_citations)} publication years")

    # Score each record
    candidates: list[dict] = []
    excluded = {"tier1": 0, "retracted": 0, "filter_drop": 0, "no_year": 0, "no_citations": 0}

    for r in records:
        doi = (r.get("canonical_doi") or r.get("doi") or "").lower()
        if doi and doi in tier1_dois:
            excluded["tier1"] += 1; continue
        if r.get("retraction_status") in ("retracted", "concern"):
            excluded["retracted"] += 1; continue
        if r.get("tnbc_relevance_decision") == "drop":
            excluded["filter_drop"] += 1; continue

        yr = r.get("publication_year")
        cc = r.get("citation_count")
        if not yr:
            excluded["no_year"] += 1; continue

        score = 0
        rationale: list[str] = []

        # Citation percentile (skip if no citation data)
        if cc is not None and cohort_citations.get(yr):
            pct = percentile_rank(cc, cohort_citations[yr])
            if pct >= 95:
                score += 5; rationale.append(f"top 5% in {yr} cohort (cite={cc})")
            elif pct >= 90:
                score += 3; rationale.append(f"top 10% in {yr} cohort (cite={cc})")
            elif pct >= 75:
                score += 1; rationale.append(f"top 25% in {yr} cohort (cite={cc})")

        # Publication type
        ptypes = set(r.get("publication_type") or [])
        if any("Phase III" in t or "phase-3" in t.lower() or "phase 3" in t.lower() for t in ptypes):
            score += 3; rationale.append("Phase III trial")
        if any("Randomized Controlled Trial" in t for t in ptypes):
            score += 2; rationale.append("RCT")
        if any(t in ("Systematic Review", "Meta-Analysis") for t in ptypes):
            score += 2; rationale.append("Systematic Review / Meta-Analysis")

        # High-impact journal
        if journal_is_high_impact(r.get("journal")):
            score += 2; rationale.append(f"high-impact journal: {r.get('journal')}")

        # Named pivotal trial
        kw = has_pivotal_trial_keyword(r.get("title"))
        if kw:
            score += 4; rationale.append(f"named pivotal trial: {kw}")

        if score >= 5:
            candidates.append({
                "doi": doi or "",
                "pmid": r.get("pmid") or "",
                "title": (r.get("title") or "").replace("\n", " "),
                "first_author": ((r.get("authors") or [{}])[0].get("name", "") if r.get("authors") else ""),
                "year": yr,
                "journal": r.get("journal") or "",
                "citation_count": cc if cc is not None else "",
                "score": score,
                "rationale": "; ".join(rationale),
                "topic_tags": ",".join(r.get("topic_tags") or []),
            })

    candidates.sort(key=lambda c: (-c["score"], -(c["citation_count"] if isinstance(c["citation_count"], int) else 0)))

    # Per-candidate CSV
    csv_path = REPORTS / "tier2_candidates.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(candidates[0].keys()) if candidates else
                                          ["doi","pmid","title","first_author","year","journal","citation_count","score","rationale","topic_tags"])
        w.writeheader()
        for c in candidates:
            w.writerow(c)
    print(f"Wrote {csv_path}")

    # Summary report
    md: list[str] = []
    md.append("# Tier-2 Algorithmic Nomination Report")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} records (input: `{in_path.name}`)")
    md.append(f"**Tier-1 seed exclusions:** {len(tier1_dois)} DOIs already in `curation/tier1_seed.yml`")
    md.append("")
    md.append("**Nomination threshold:** score &ge; 5. Records reaching the threshold are CANDIDATES for tier-2 promotion; editorial board confirms or rejects each one before any record is actually moved from tier-3 default to tier-2 in the production database.")
    md.append("")
    md.append("## Scoring")
    md.append("")
    md.append("| Signal | Points |")
    md.append("|---|---:|")
    md.append("| Citation percentile: top 5% in publication-year cohort | +5 |")
    md.append("| Citation percentile: top 10% in publication-year cohort | +3 |")
    md.append("| Citation percentile: top 25% in publication-year cohort | +1 |")
    md.append("| Publication type: Clinical Trial, Phase III | +3 |")
    md.append("| Publication type: Randomized Controlled Trial | +2 |")
    md.append("| Publication type: Systematic Review / Meta-Analysis | +2 |")
    md.append("| High-impact journal (NEJM, Lancet, JAMA, JCO, Nat Med, Cell, etc.) | +2 |")
    md.append("| Named pivotal trial in title (KEYNOTE, ASCENT, OlympiA, etc.) | +4 |")
    md.append("")

    md.append("## Results")
    md.append("")
    md.append(f"- **Tier-2 candidates nominated:** {len(candidates):,}")
    md.append(f"- Excluded (already tier-1 seed): {excluded['tier1']:,}")
    md.append(f"- Excluded (retracted / concern): {excluded['retracted']:,}")
    md.append(f"- Excluded (OpenAlex post-filter drop): {excluded['filter_drop']:,}")
    md.append(f"- Excluded (no publication year): {excluded['no_year']:,}")
    md.append("")

    md.append("### Score distribution among nominees")
    md.append("")
    score_dist = defaultdict(int)
    for c in candidates:
        score_dist[c["score"]] += 1
    md.append("| Score | Count |")
    md.append("|---:|---:|")
    for s in sorted(score_dist, reverse=True):
        md.append(f"| {s} | {score_dist[s]:,} |")
    md.append("")

    md.append("### Top 25 candidates (by score)")
    md.append("")
    md.append("| Score | Year | Journal | Title | Cites |")
    md.append("|---:|---:|---|---|---:|")
    for c in candidates[:25]:
        title = c["title"][:80] + ("…" if len(c["title"]) > 80 else "")
        journal = (c["journal"] or "")[:30]
        md.append(f"| {c['score']} | {c['year']} | {journal} | {title} | {c['citation_count']} |")
    md.append("")

    md.append("## How to apply in production")
    md.append("")
    md.append("1. The editorial board reviews `tier2_candidates.csv` in descending score order.")
    md.append("2. Confirmed candidates are recorded in `curation/tier2_approved.yml` (analogous to the tier-1 seed file), with a reviewer initial and a one-line rationale.")
    md.append("3. A separate small script then promotes the approved candidates to `tier=2` in the production `bibliography_records` table, logging the promotion with a timestamp and reviewer.")
    md.append("4. Re-runs of this script after each harvest produce updated candidate lists; newly-nominated records that weren't previously above threshold get added to the editorial queue.")
    md.append("")
    md.append("## Caveats")
    md.append("")
    md.append("- Citation counts on recent papers (especially 2025-2026) are still accumulating; the top-decile threshold in those cohorts will be lower than in mature years. Editorial review weights this appropriately.")
    md.append("- The high-impact journal list is intentionally conservative; specialty journals (Breast Cancer Research and Treatment, ESMO Open, etc.) are not included but house genuinely high-quality work that the editorial board may want to elevate.")
    md.append("- The named-trial heuristic catches the well-known TNBC trials but not every important trial has a memorable name; a paper titled simply 'Adjuvant olaparib for breast cancer' would not match the named-trial bonus and would have to qualify on citation percentile + Phase III publication type instead.")
    md.append("- Tier-2 promotion is editorial, not algorithmic. This script's job is to surface candidates efficiently; the editorial board owns the final call.")
    md.append("")

    rep_path = REPORTS / "tier2_nomination.md"
    rep_path.write_text("\n".join(md))
    print(f"Wrote {rep_path}")

    # Console summary
    print()
    print("=== SUMMARY ===")
    print(f"Tier-2 candidates nominated: {len(candidates):,}")
    print(f"  Score distribution:")
    for s in sorted(score_dist, reverse=True):
        print(f"    score {s:>2}: {score_dist[s]:>5}")


if __name__ == "__main__":
    main()
