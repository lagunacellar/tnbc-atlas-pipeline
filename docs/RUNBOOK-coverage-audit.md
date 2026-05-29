# Runbook — Coverage audit against the 2023 Frontiers bibliometric

The Phase 1 plan §11 calls for cross-checking corpus completeness against an external bibliometric benchmark. This runbook describes the audit against the most natural reference: the 2023 *Frontiers in Medicine* analysis of 16,826 TNBC publications across 17 years (2006–2022).

> Wang Y. et al. "A bibliometric analysis of 16,826 triple-negative breast cancer publications using multiple machine learning algorithms: Progress in the past 17 years." *Frontiers in Medicine* 2023. <https://www.frontiersin.org/journals/medicine/articles/10.3389/fmed.2023.999312/full>

## Why this benchmark

It is the largest published TNBC bibliometric we have located, covers a comparable date range to our backfill, and reports counts in slices (by year, by country, by journal, by institution) that are directly comparable to the dimensions we expose in our coverage report. No other bibliometric matches this scope.

Caveat: their inclusion criteria, search query, and time window are not identical to ours. Differences are expected; the audit is calibration, not validation.

## What we are checking

Five things, in priority order:

1. **Total volume by year.** Their year-by-year counts vs ours, for the overlapping window (2006–2022). Material gaps (>15% per year) suggest a real coverage problem.
2. **Country distribution.** Their top-15 countries by author affiliation vs ours. Substantially different distributions suggest source-coverage bias.
3. **Top journals.** Their top-25 journals vs ours. Missing major journals indicate query gaps; extra journals in our list (Figshare, Zenodo, etc.) are expected because we pull preprints and datasets that the Frontiers analysis excludes.
4. **Top-cited paper overlap.** Their top-100 most-cited papers list vs our tier-1 + tier-2 candidates. We should match nearly all of them.
5. **Subject/topic distribution.** Their MeSH-term topic clusters vs our taxonomy domain counts. Order-of-magnitude agreement expected; exact percentages will differ because of taxonomy differences.

## Source data

The Frontiers paper publishes its summary counts in tables (Table 1, Table 2, Figures 2–5). The underlying data is available via the paper's supplementary materials (CSV files on the Frontiers site). Download:

- Supplementary Table S1 — year-by-year article counts
- Supplementary Table S2 — top 25 journals
- Supplementary Table S3 — top 20 countries
- Supplementary Table S4 — top 100 most-cited articles

Save these as `audit/frontiers_2023/*.csv` (folder lives in this repo).

## Audit script

`scripts/coverage_audit.py` (write this):

```python
"""Compare our corpus against the Frontiers 2023 bibliometric benchmarks."""
import csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUR_CORPUS = ROOT / "exports" / "bibliography_tagged.jsonl"
BENCH_DIR = ROOT / "audit" / "frontiers_2023"
REPORTS = ROOT / "reports"

def load_corpus():
    rows = []
    with open(OUR_CORPUS) as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows

def by_year(rows):
    c = Counter()
    for r in rows:
        if r.get("publication_year"):
            c[r["publication_year"]] += 1
    return c

def by_country(rows):
    c = Counter()
    for r in rows:
        for country in (r.get("countries") or []):
            c[country] += 1
    return c

def by_journal(rows):
    c = Counter()
    for r in rows:
        if r.get("journal"):
            c[r["journal"]] += 1
    return c

def load_benchmark(name):
    with open(BENCH_DIR / f"{name}.csv") as fh:
        return list(csv.DictReader(fh))

def compare_years(ours, theirs):
    # theirs format: [{year: 2006, count: 220}, ...]
    md = ["## Year-by-year coverage (2006-2022)\n",
          "| Year | Frontiers count | Our count | Delta | Our share of theirs |",
          "|---:|---:|---:|---:|---:|"]
    for row in theirs:
        y = int(row["year"])
        if 2006 <= y <= 2022:
            t = int(row["count"])
            o = ours.get(y, 0)
            d = o - t
            pct = 100 * o / t if t else 0
            flag = " ⚠" if pct < 85 or pct > 115 else ""
            md.append(f"| {y} | {t:,} | {o:,} | {d:+,} | {pct:.0f}%{flag} |")
    return "\n".join(md)

# Similar for journals, countries, top-cited papers
# Output to reports/coverage_audit.md
```

The script structure above is the skeleton; flesh out per-dimension comparison once the benchmark CSVs are downloaded.

## Interpreting results

### Year coverage

If our count for a given year is between 85% and 115% of the Frontiers count, that's normal — different inclusion criteria, slightly different search dates. Outside that band, investigate.

Common findings to expect:

- **2006–2010**: Our count likely lower than Frontiers because our pre-2005 seeding misses some older papers that they captured.
- **2020–2022**: Our count likely higher because we include preprints and datasets they excluded.
- **One specific year much lower**: probably a harvest hiccup for that year; re-run.

### Country distribution

The top-5 (US, China, India, UK, Korea historically) should agree to within a few percentage points. If our top-5 looks different, our OpenAlex author-affiliation parsing might be misattributing affiliations.

### Top journals

Major journals (NEJM, Lancet, JCO, JAMA, Clin Cancer Res, Cancer Research, Annals of Oncology, Nat Rev Clin Oncol) should appear in both lists with comparable rankings. If a major journal is in their top-25 but not ours, our query may be missing it — investigate.

Expected differences:
- We will have preprint servers (bioRxiv, medRxiv, Research Square, SSRN), dataset platforms (Figshare, Zenodo), and dissertation venues in our top-25 because we include those record types. The Frontiers analysis excluded them.
- Their list may include older journals we cover less well (e.g., specialty Chinese-language oncology journals).

### Top-cited paper overlap

Their top 100 most-cited TNBC papers (Supplementary Table S4) should overlap heavily with our tier-1 + tier-2 candidates. Target: ≥90% of their top 100 should be findable by DOI in our corpus. Records on their list but missing from ours indicate either:

- A real coverage gap (the paper should be in our corpus but isn't); fix.
- A DOI mismatch (the paper is in our corpus under a different DOI alias); investigate normalization.

## Output

`reports/coverage_audit.md` should contain:

- Five comparison tables (year, country, top journals, top-cited overlap, topic distribution)
- A summary section that names any flagged disagreements
- A short narrative on what we learned

Update `coverage_report.md` to reference the audit results and date the comparison.

## Cadence

Run the audit:

- **Once after the full backfill completes**, as a one-time validation.
- **Annually thereafter**, against whatever the most recent published TNBC bibliometric is. The Frontiers 2023 analysis will be superseded eventually; substitute the newest comparable benchmark.

## Limitations

- The Frontiers analysis is itself one analysis with its own choices. It is not ground truth. We are calibrating two independent estimates of the same population, not validating against a known answer.
- Bibliometric counts are very sensitive to query phrasing and to which sources are searched. A 10% discrepancy in raw counts may simply mean "we used different queries" rather than "one of us is wrong."
- The benchmark publication date (2023) means it doesn't cover 2023+. Our coverage of those recent years is unaudited by this method.

## Checklist

- [ ] Frontiers 2023 supplementary CSVs downloaded to `audit/frontiers_2023/`
- [ ] `scripts/coverage_audit.py` written and tested
- [ ] Audit run after full backfill completion
- [ ] `reports/coverage_audit.md` reviewed by editorial board
- [ ] Any flagged year / country / journal gaps investigated and either fixed or documented
- [ ] Top-cited paper overlap ≥ 90%; if lower, root cause documented
- [ ] Audit re-run cadence (annual) scheduled in the orchestration plan
