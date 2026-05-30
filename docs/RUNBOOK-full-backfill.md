# Runbook — Full backfill to 2005

The pilot covered a 24-month window (2024-05 → 2026-05). Production needs the full historical record. This runbook describes how to run the backfill end-to-end.

## Target volume

Rough estimates based on the pilot harvest rates and published TNBC bibliometrics:

| Source | Pilot 24-month volume | Backfill 2005–present estimate |
|---|---:|---:|
| PubMed | 7,465 | ~75,000–90,000 |
| Europe PMC | 7,109 | ~70,000–85,000 |
| OpenAlex | 15,657 | ~170,000–200,000 |
| **Raw, before dedup** | ~30,000 | ~315,000–375,000 |
| **Canonical, after dedup** | 14,319 | **~25,000–30,000** |

The dedup ratio (~50%) holds across years; the canonical-record target is the right number for editorial and storage planning.

## Prerequisites

- Production Supabase project up and reachable (see `RUNBOOK-production-db.md`).
- Schema applied (`sql/01_schema.sql`, `sql/02_enrichment_migration.sql`, `sql/03_supabase_public_api.sql`).
- `DATABASE_URL` env var set to the Supabase connection string (direct URI, not pooled — backfill is one long process, not many short ones).
- Local machine or one-shot GitHub Actions runner with this repo checked out, dependencies installed (`make install`).
- API contact email registered with all sources via the polite-pool `mailto` parameter — already set in `scripts/common.py`; no additional registration required for PubMed, Europe PMC, OpenAlex, Crossref, or Unpaywall at the free-tier rates we use.
- Approximately 5–10 GB of free disk on the runner (raw snapshots accumulate during the backfill; on a GitHub Actions runner this is well within the ~14 GB default).

## Strategy

**Do not run a single 21-year query.** Two reasons:
1. PubMed's `retmax` is 100,000; we'd hit it.
2. Crashes mid-run lose progress and complicate debugging.

**Chunk by year.** Run the harvesters one year at a time, resume-safe per chunk. Each year is a complete unit of work; failures only require re-running that year.

## Step 1 — Smoke test on a single year

Confirm the pipeline works end-to-end against production before scaling up.

```bash
# Pick a recent year with substantial volume but bounded scope
make harvest -e START=2023-01-01 END=2023-12-31
# (current scripts honor --start / --end; the Makefile passes through)

# Verify volumes
psql -c "SELECT publication_year, COUNT(*) FROM bibliography_records GROUP BY 1 ORDER BY 1 DESC LIMIT 5;"

# Dedup, enrich, filter, tag
make dedup
make enrich
make filter
make tag

# Run the benchmark — should pick up tier-1 entries from 2023
make benchmark
```

If anything fails here, fix before going further. Common issues:
- Connection limits on production Postgres (raise `max_connections` if needed)
- Rate-limit pushback from OpenAlex (the polite-pool `mailto` should prevent this)
- DOI normalization edge cases on older records (some pre-2010 DOIs have unusual casing)

## Step 2 — Year-by-year backfill

Process years in reverse chronological order (most-recent first). Each year takes roughly:

| Year cohort | PubMed records | Wall-clock estimate |
|---|---:|---:|
| 2020–2024 | ~3,000/yr | 8–12 minutes each |
| 2015–2019 | ~2,500/yr | 6–10 minutes each |
| 2010–2014 | ~1,500/yr | 4–8 minutes each |
| 2005–2009 | ~600/yr | 2–4 minutes each |

(Times include eFetch parsing at ~100 records/second; Europe PMC and OpenAlex run in parallel and don't extend wall-clock.)

Total estimate: roughly 3–4 hours of continuous harvest time across all 21 years.

### Recommended invocation

A simple driver script:

```bash
#!/bin/bash
# scripts/backfill_loop.sh
set -e

for year in $(seq 2024 -1 2005); do
  echo "=== Year $year ==="
  python scripts/harvest_pubmed.py     --start ${year}-01-01 --end ${year}-12-31
  python scripts/harvest_europepmc.py  --start ${year}-01-01 --end ${year}-12-31
  python scripts/harvest_openalex.py   --start ${year}-01-01 --end ${year}-12-31
  python scripts/dedup_and_load.py
  echo "=== Year $year done at $(date -u) ==="
done
```

**Two ways to run the loop:**

- **From your laptop in `tmux`/`screen`** — the simplest. Open `tmux`, source `DATABASE_URL`, run the loop, detach, come back hours later. The harvesters are resumable; a kill+restart picks up where it left off.
- **One-shot GitHub Actions workflow** — `.github/workflows/full-backfill.yml` (not on a schedule; trigger manually via `workflow_dispatch`). 6-hour timeout on the free plan covers ~12–15 years per run; for the full 1995→2024 range you may need to split into two manual runs (e.g., 2005–2024 first, then 1995–2004). The workflow uses the same scripts and the same `DATABASE_URL` secret as the weekly harvest workflow.

## Step 3 — Pre-2005 foundational seeding

Most of the tier-1 list is pre-2005 (Perou 2000, Sørlie 2001/2003, etc.). These won't be picked up by the year-by-year backfill if the date filter excludes them. Two approaches:

### Option A — Extend the backfill to 1995

```bash
for year in $(seq 2004 -1 1995); do
  # same as above
done
```

Adds maybe an hour and pulls in the foundational era organically. **Recommended.**

### Option B — Hand-seed only

Use the tier-1 list as a DOI-targeted fetch:

```python
# scripts/seed_tier1_records.py (write this; ~30 lines)
import yaml, requests
seed = yaml.safe_load(open("curation/tier1_seed.yml"))
for entry in seed["entries"]:
    if entry.get("doi"):
        # Use the existing harvest_pubmed PMID-targeted helper
        # to fetch the record into raw_snapshots, then run dedup_and_load
        ...
```

Only seeds the ~60 tier-1 papers; misses everything else from that era. Use this if you must defer the full pre-2005 sweep.

## Step 4 — Bulk enrichment

Once dedup is complete, enrich the entire corpus:

```bash
python scripts/enrich_crossref.py    # idempotent; safe to run for hours
python scripts/enrich_unpaywall.py   # idempotent; faster than Crossref
python scripts/retraction_sweep.py   # one-shot; runs in under a minute
```

Crossref enrichment at the production scale will take roughly **8–10 hours** for 30,000 DOIs at the conservative 6-worker polite-pool rate. Unpaywall is faster, maybe **2 hours**.

Run these in `tmux` overnight; both scripts are resumable. If you want them faster, increase `--workers` (we've seen up to 16 workers behave well; beyond that the polite pool starts returning 429s).

## Step 5 — Quality passes

```bash
python scripts/filter_openalex_only.py     # ~1 minute
python scripts/tag_topics.py                # ~5 minutes at 30k records
python scripts/tier1_benchmark.py           # ~1 minute
python scripts/nominate_tier2.py            # ~2 minutes
```

Inspect the tier-1 benchmark output — in-window recall should now be much higher because the backfill covers the full publication-date range of the tier-1 list. Goal: **≥ 95% tier-1 recall** at the end of backfill. If recall is lower, investigate which entries are missing and why (most common cause: a tier-1 DOI uses a non-standard casing or aliasing in our index — fixable by tweaking the dedup normalization).

## Step 6 — Coverage audit

See `RUNBOOK-coverage-audit.md` for the cross-check methodology against the 2023 *Frontiers in Medicine* bibliometric (16,826 TNBC papers across 17 years). Run this before declaring the backfill complete.

## Step 7 — Final reports and exports

```bash
python scripts/export_and_report.py    # CSV / JSONL / BibTeX / RIS + coverage_report.md
python scripts/build_browser.py        # standalone HTML browser
```

The coverage report should now show ~25,000–30,000 records, broad year distribution from 2005 onwards, and substantially expanded country / journal coverage relative to the pilot.

## Step 8 — Cut over the website

The website currently consumes the pilot's `bibliography.jsonl` via a slim-JSON build step. Update that step to read from the production database (the slim-JSON is the only piece that needs to change; the website's frontend filter UI works unchanged).

See `tnbc_info_site/README.md` for the slim-JSON regeneration step.

## Failure recovery

### Year N harvest dies mid-run

The harvesters write JSONL incrementally and keep a PMID cache. Re-run the same command; it resumes from the last completed batch. No special intervention required.

### Dedup load fails on a specific record

Inspect the offending JSONL line, fix or skip:

```bash
# Find the bad line in the latest snapshot
python -c "
import json
with open('snapshots/pubmed/<latest>.jsonl') as fh:
    for i, line in enumerate(fh, 1):
        try: json.loads(line)
        except Exception as e: print(i, e)
"
```

Skip the bad line and re-run dedup_and_load.

### Enrichment 404 rates spike

A spike in Crossref 404s (>20% over a 1,000-record window) likely means a wave of dataset / preprint DOIs that Crossref doesn't index. This is expected for OpenAlex-only records (Figshare, Zenodo, preprint-server DOIs). Confirm by sampling a few of the 404'd DOIs; they should be `10.6084/m9.figshare.*` or similar non-Crossref prefixes. No action needed.

## Time and cost budget

| Phase | Wall-clock estimate |
|---|---|
| Year-by-year harvest (2005–2024) | 4–5 hours |
| Pre-2005 (1995–2004) | 1 hour |
| Dedup + load (incremental across years) | runs concurrently |
| Crossref enrichment | 8–10 hours |
| Unpaywall enrichment | 2 hours |
| Retraction sweep | < 1 minute |
| Quality passes | ~10 minutes |
| **Total elapsed** | **~16–18 hours** (mostly unattended) |

Realistic plan: start the backfill loop in a `tmux` session before lunch; come back to it the next morning to find it complete or near-complete. The Postgres on Hetzner CCX13 will use roughly 8 GB of disk for 30k canonical records plus raw snapshots; well within budget.

## Checklist

- [ ] Production Postgres up, schema applied, smoke-test harvest succeeded
- [ ] Disk space verified (~100 GB free on harvest host)
- [ ] `backfill_loop.sh` script written and tested on a single recent year
- [ ] Full backfill loop running in `tmux`
- [ ] Pre-2005 foundational seeding completed (Option A or B)
- [ ] Crossref enrichment completed (overnight)
- [ ] Unpaywall enrichment completed
- [ ] Retraction sweep completed; one-line summary recorded
- [ ] Quality passes run; tier-1 in-window recall ≥ 95%
- [ ] Coverage audit completed (see `RUNBOOK-coverage-audit.md`)
- [ ] Final reports generated; coverage_report.md reviewed
- [ ] Website's slim-JSON refresh cut over to production DB
- [ ] Backup taken before cutover (so we can roll back if needed)
