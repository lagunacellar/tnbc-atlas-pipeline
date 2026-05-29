"""TNBC Atlas — Tier-1 coverage benchmark.

Cross-references the hand-curated tier-1 seed list (curation/tier1_seed.yml)
against the bibliography corpus (exports/bibliography.jsonl).

Reports:
  - For each tier-1 entry: matched / not-matched, match basis (DOI / PMID / fuzzy-title)
  - Recall by domain (A-J taxonomy codes from Phase 2 plan)
  - In-window vs out-of-window split (most tier-1 papers predate the 24-month pilot)
  - Per-entry diagnostic for misses

Outputs:
  reports/tier1_coverage.md  — human-readable benchmark
  reports/tier1_matches.csv  — per-entry match outcomes
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
TIER1 = ROOT / "curation" / "tier1_seed.yml"
CORPUS = ROOT / "exports" / "bibliography.jsonl"
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

WINDOW_START = "2024-05-10"
WINDOW_END = "2026-05-10"


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


def first_author_last(authors_field) -> str:
    if not authors_field:
        return ""
    if isinstance(authors_field, list) and authors_field:
        first = authors_field[0]
        if isinstance(first, dict):
            name = first.get("name", "")
        else:
            name = str(first)
        return name.split()[-1].lower() if name else ""
    return ""


def load_corpus() -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """Return (by_doi, by_pmid, all_records)."""
    by_doi: dict[str, dict] = {}
    by_pmid: dict[str, dict] = {}
    all_records: list[dict] = []
    with open(CORPUS) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            all_records.append(r)
            doi = normalize_doi(r.get("canonical_doi") or r.get("doi"))
            pmid = r.get("pmid")
            if doi:
                by_doi[doi] = r
            if pmid:
                by_pmid[str(pmid)] = r
    return by_doi, by_pmid, all_records


def in_window(date_str: str | None) -> bool:
    if not date_str:
        return False
    return WINDOW_START <= str(date_str)[:10] <= WINDOW_END


def fuzzy_match(t1: dict, all_records: list[dict]) -> tuple[dict, int] | None:
    """Find best fuzzy match for a tier-1 entry across the corpus."""
    nt = normalize_title(t1.get("title"))
    if len(nt) < 20:
        return None
    la = (t1.get("authors") or [""])[0].split()[-1].lower() if t1.get("authors") else ""
    yr = t1.get("year")
    best = None
    best_score = 0
    for r in all_records:
        ct = normalize_title(r.get("title"))
        if not ct or abs(len(ct) - len(nt)) > 40:
            continue
        cl = first_author_last(r.get("authors"))
        cy = r.get("publication_year")
        if cl and la and cl != la:
            continue
        if cy and yr and abs(cy - yr) > 2:
            continue
        score = fuzz.ratio(nt, ct)
        if score > best_score:
            best_score = score
            best = r
    if best and best_score >= 88:
        return best, best_score
    return None


def main():
    print(f"Loading tier-1 seed from {TIER1}")
    seed = yaml.safe_load(TIER1.read_text())
    entries = seed["entries"]
    print(f"  {len(entries)} entries; schema v{seed.get('version')}")

    print(f"Loading corpus from {CORPUS}")
    by_doi, by_pmid, all_records = load_corpus()
    print(f"  {len(all_records)} records ({len(by_doi)} with DOI, {len(by_pmid)} with PMID)")

    matches: list[dict] = []
    missing: list[dict] = []

    for e in entries:
        doi = normalize_doi(e.get("doi"))
        pmid = str(e.get("pmid")) if e.get("pmid") else None
        match = None
        match_kind = None
        match_score = None

        if doi and doi in by_doi:
            match = by_doi[doi]
            match_kind = "doi"
            match_score = 100
        elif pmid and pmid in by_pmid:
            match = by_pmid[pmid]
            match_kind = "pmid"
            match_score = 100
        else:
            fm = fuzzy_match(e, all_records)
            if fm:
                match, match_score = fm
                match_kind = "fuzzy_title"

        record = {
            "id": e["id"],
            "year": e.get("year"),
            "domain": e.get("domain"),
            "subtopic": e.get("subtopic"),
            "in_window": WINDOW_START <= str(e.get("year", "1900")) <= WINDOW_END[:4]
                         and e.get("year") in (2024, 2025, 2026),
            "expected_doi": doi,
            "expected_pmid": pmid,
            "matched": match is not None,
            "match_kind": match_kind,
            "match_score": match_score,
            "matched_doi": (match or {}).get("canonical_doi") or (match or {}).get("doi"),
            "matched_pmid": (match or {}).get("pmid"),
            "matched_title": (match or {}).get("title"),
            "title": e.get("title"),
        }
        if match:
            matches.append(record)
        else:
            missing.append(record)

    # Per-entry CSV
    csv_path = REPORTS / "tier1_matches.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(matches[0].keys()) if matches else list(missing[0].keys()))
        w.writeheader()
        for r in matches + missing:
            w.writerow(r)
    print(f"Wrote {csv_path}")

    # Stats
    n_total = len(entries)
    n_matched = len(matches)
    n_missing = len(missing)

    in_window_total = sum(1 for e in entries if e.get("year") in (2024, 2025, 2026))
    in_window_matched = sum(
        1 for r in matches if r["year"] in (2024, 2025, 2026)
    )
    out_window_total = n_total - in_window_total
    out_window_matched = n_matched - in_window_matched

    by_domain_total: dict[str, int] = defaultdict(int)
    by_domain_matched: dict[str, int] = defaultdict(int)
    for e in entries:
        by_domain_total[e["domain"]] += 1
    for r in matches:
        by_domain_matched[r["domain"]] += 1

    by_kind: dict[str, int] = defaultdict(int)
    for r in matches:
        by_kind[r["match_kind"]] += 1

    by_confidence: dict[str, int] = defaultdict(lambda: [0, 0])  # [matched, total]
    for e in entries:
        c = e.get("confidence", "unknown")
        by_confidence[c][1] += 1
    for r in matches:
        e = next(x for x in entries if x["id"] == r["id"])
        c = e.get("confidence", "unknown")
        by_confidence[c][0] += 1

    # Markdown report
    md = []
    md.append("# TNBC Atlas — Tier-1 Coverage Benchmark")
    md.append("")
    md.append(f"**Tier-1 list:** {TIER1.name} ({n_total} entries)  ")
    md.append(f"**Corpus:** pilot harvest, 2024-05-10 → 2026-05-10 ({len(all_records):,} canonical records)  ")
    md.append(f"**Generated:** {Path(__file__).name} run")
    md.append("")
    md.append("## Headline numbers")
    md.append("")
    md.append(f"- Tier-1 entries matched in corpus: **{n_matched} / {n_total} ({100*n_matched/n_total:.1f}%)**")
    md.append(f"- Of in-window entries (2024–2026): {in_window_matched} / {in_window_total} matched ({100*in_window_matched/max(1,in_window_total):.1f}%)")
    md.append(f"- Of out-of-window entries (pre-2024): {out_window_matched} / {out_window_total} matched ({100*out_window_matched/max(1,out_window_total):.1f}%)")
    md.append("")
    md.append("**Why these numbers matter:** the in-window recall is the *real* performance signal — it tells us whether our harvest catches the field-defining papers that *should* be in our window. Out-of-window matches are bonus (they appear because OpenAlex re-dated them or they were re-issued); we shouldn't expect high recall there until full backfill to 2005.")
    md.append("")
    md.append("## Match basis")
    md.append("")
    md.append("| Match kind | Count |")
    md.append("|---|---:|")
    for k in ("doi", "pmid", "fuzzy_title"):
        md.append(f"| {k} | {by_kind.get(k, 0)} |")
    md.append("")
    md.append("## Coverage by taxonomy domain")
    md.append("")
    md.append("| Domain | Matched / Total | Recall |")
    md.append("|---|---:|---:|")
    for d in sorted(by_domain_total):
        m = by_domain_matched.get(d, 0)
        t = by_domain_total[d]
        md.append(f"| {d} | {m} / {t} | {100*m/t:.0f}% |")
    md.append("")
    md.append("## Coverage by confidence tag")
    md.append("")
    md.append("| Confidence | Matched / Total |")
    md.append("|---|---:|")
    for c, (m, t) in sorted(by_confidence.items()):
        md.append(f"| {c} | {m} / {t} ({100*m/max(1,t):.0f}%) |")
    md.append("")
    md.append("## In-window misses (should-have-been-found)")
    md.append("")
    in_window_missing = [r for r in missing if r["year"] in (2024, 2025, 2026)]
    if in_window_missing:
        md.append("These tier-1 entries fall inside our 2024-05-10 → 2026-05-10 search window but were **not** found in the corpus. Each one is a coverage gap worth investigating before public launch:")
        md.append("")
        md.append("| Year | Domain | Title | Expected DOI |")
        md.append("|---|---|---|---|")
        for r in in_window_missing:
            md.append(f"| {r['year']} | {r['domain']} | {(r['title'] or '')[:80]} | {r['expected_doi'] or ''} |")
    else:
        md.append("**None.** Every in-window tier-1 entry was matched to a corpus record. This is the strongest available signal that the harvest pipeline is catching the papers that should be there.")
    md.append("")
    md.append("## Out-of-window matches (interesting but not load-bearing)")
    md.append("")
    out_window_matched_recs = [r for r in matches if r["year"] not in (2024, 2025, 2026)]
    if out_window_matched_recs:
        md.append(f"{len(out_window_matched_recs)} tier-1 entries from pre-2024 publication years were found in the corpus anyway — these are records OpenAlex/Europe PMC re-surfaced during our window (typically because of online updates, proceedings reissues, or citation aliases). Crossref enrichment correctly re-dated most of them; see the `pre-2024` filter in the bibliography browser.")
        md.append("")
        md.append("Sample (first 10 by year):")
        md.append("")
        md.append("| Year | Domain | Match | Title |")
        md.append("|---|---|---|---|")
        for r in sorted(out_window_matched_recs, key=lambda x: x["year"])[:10]:
            md.append(f"| {r['year']} | {r['domain']} | {r['match_kind']} | {(r['title'] or '')[:70]} |")
    else:
        md.append("None.")
    md.append("")
    md.append("## How to interpret this benchmark")
    md.append("")
    md.append("- The pilot covers a 24-month window (2024-05-10 → 2026-05-10). Most tier-1 papers were published before this window — that's expected and not a coverage failure.")
    md.append("- The **in-window recall** is the operationally meaningful number. If it's 100%, the harvest reliably catches the papers that should be there. If it's lower, the missing entries point to real query / source-coverage gaps to fix before the production launch.")
    md.append("- High **out-of-window match rates** mean OpenAlex/Europe PMC are surfacing pre-window papers in our results — usually fine for discovery but should be cleaned up by Crossref date-correction (see the `pre-2024` filter in the browser).")
    md.append("- The tier-1 list itself is **versioned and auditable**. The editorial board should review the YAML before any tier=1 assignments propagate into the production database.")
    md.append("")

    rep_path = REPORTS / "tier1_coverage.md"
    rep_path.write_text("\n".join(md))
    print(f"Wrote {rep_path}")

    # Console summary
    print()
    print(f"=== SUMMARY ===")
    print(f"Tier-1 entries matched: {n_matched}/{n_total} ({100*n_matched/n_total:.1f}%)")
    print(f"In-window recall:       {in_window_matched}/{in_window_total} ({100*in_window_matched/max(1,in_window_total):.0f}%)")
    print(f"Out-of-window matches:  {out_window_matched}/{out_window_total}")
    print(f"By match kind: {dict(by_kind)}")


if __name__ == "__main__":
    main()
