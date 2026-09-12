# TNBC Atlas — Bibliography Pipeline

This repo is the data pipeline for the TNBC Atlas project. It harvests bibliographic records for triple-negative breast cancer literature from PubMed, Europe PMC, and OpenAlex; deduplicates them; enriches them with Crossref, Unpaywall, and Retraction Watch; assigns topic tags and tier candidates; and produces the bibliography exports consumed by the website at [tnbc.info](https://tnbc.info).

The pipeline runs on free, public-API sources; targets **Supabase** as the production database; and is scheduled by **GitHub Actions cron**. The website is hosted separately on **Cloudflare Pages**. See `docs/RUNBOOK-production-db.md` and `docs/RUNBOOK-orchestration.md` for the production architecture; this README focuses on the pipeline itself.

## Origin

This folder began as the Phase 1 pilot harvest (a 24-month window, 2024-05 → 2026-05, against sandbox Postgres). The pilot validated the architecture end-to-end. The folder is now structured as a standalone repository ready to be published as `tnbc-atlas-pipeline`; see `PUBLISH.md` for the publishing checklist.

## What's in the box

```
tnbc_atlas_pilot/
├── README.md                      ← this file
├── bibliography_browser.html      ← self-contained read-only browser (open in any browser)
├── .github/workflows/             ← scheduled GitHub Actions cron workflows
│   ├── weekly-harvest.yml
│   ├── weekly-retraction-sweep.yml
│   └── quarterly-tier-review.yml
├── docs/                          ← deployment + operations runbooks
│   ├── RUNBOOK-production-db.md   ← Supabase setup
│   ├── RUNBOOK-orchestration.md   ← GitHub Actions cron
│   ├── RUNBOOK-public-api.md      ← Supabase PostgREST + Cloudflare DNS
│   ├── RUNBOOK-full-backfill.md   ← 2005-forward backfill procedure
│   └── RUNBOOK-coverage-audit.md  ← cross-check vs Frontiers 2023 bibliometric
├── sql/
│   ├── 01_schema.sql              ← Postgres 14+ schema (production-portable)
│   ├── 02_enrichment_migration.sql ← columns added by Crossref/Unpaywall/Retraction sweep
│   └── 03_supabase_public_api.sql ← public read-only view + RLS for Supabase PostgREST
├── curation/
│   ├── README.md                  ← editorial-owned hand-curated lists
│   └── tier1_seed.yml             ← 60 foundational TNBC papers (provenance + rationale)
├── scripts/
│   ├── common.py                  ← shared helpers (DB, rate limiter, logging)
│   ├── harvest_pubmed.py          ← NCBI E-utilities (eSearch + eFetch)
│   ├── harvest_europepmc.py       ← Europe PMC REST API
│   ├── harvest_openalex.py        ← OpenAlex (citations + ORCID)
│   ├── enrich_crossref.py         ← Crossref Works (publication date, type, license, refs, funders)
│   ├── enrich_unpaywall.py        ← Unpaywall (authoritative OA status + best PDF URL)
│   ├── retraction_sweep.py        ← Crossref Labs Retraction Watch CSV → flag retracted/concern
│   ├── filter_openalex_only.py    ← stricter TNBC-relevance filter on OpenAlex-only records
│   ├── tag_topics.py              ← rule-based topic tagging (MeSH + keyword → 10-domain taxonomy)
│   ├── nominate_tier2.py          ← algorithmic tier-2 candidate scoring (top decile, high-impact journal, named trial)
│   ├── tier1_benchmark.py         ← cross-references tier-1 seed list against the corpus; recall benchmark
│   ├── dedup_and_load.py          ← DOI → PMID → fuzzy-title dedup, load to Postgres
│   ├── export_and_report.py       ← CSV / JSONL / BibTeX / RIS exports + coverage report
│   └── build_browser.py           ← emits bibliography_browser.html with embedded JSON
├── snapshots/                     ← raw upstream JSONL snapshots (one per source)
│   ├── pubmed/
│   ├── europepmc/
│   └── openalex/
├── exports/                       ← canonical bibliography in 4 formats
│   ├── bibliography.csv
│   ├── bibliography.jsonl
│   ├── bibliography.bib
│   └── bibliography.ris
├── reports/
│   ├── coverage_report.md         ← headline numbers, enrichment status, source overlap, year/country/journal/OA breakdowns, retraction summary
│   ├── retracted.csv              ← per-record retraction details (matched to Retraction Watch)
│   ├── tier1_coverage.md          ← tier-1 recall benchmark (in-window vs out-of-window, by domain, by confidence)
│   ├── tier1_matches.csv          ← per-tier-1-entry match outcome
│   ├── top_cited.csv              ← top 100 by citation count
│   └── top_journals.csv           ← top 50 journals
└── logs/                          ← per-source run logs
```

## Browsing the bibliography without spreadsheets

Open `bibliography_browser.html` in any modern browser (double-click it from this project folder). It is a single self-contained file with the canonical records embedded; only Tailwind and Grid.js load from CDNs (small) so first open requires an internet connection, but the data and all filtering logic run locally with no further network calls. The current build lives at `exports/bibliography_browser.html` (~46 MB, full corpus); the copy in the repo root is an early 14,319-record pilot snapshot kept for reference.

**Filters:** free-text (title / journal / first author), year (incl. a "pre-2024 (Crossref re-dated)" bucket), OA status, Crossref publication type (journal-article / preprint / proceedings / book chapter / dissertation / dataset / etc.), source presence (PubMed / Europe PMC / OpenAlex / all-three / OpenAlex-only), minimum citation count.

**Toggles:** Retracted only · Hide pre-window (records Crossref re-dated to before 2024-05-10) · Has license URL.

**Visual cues:** retracted records show a red `RETRACTED` pill next to the title and have a red left border. Source pills now include `CR` (Crossref-enriched) and `UP` (Unpaywall-enriched) alongside `PM`/`EP`/`OA`. OA-status pill colors track Unpaywall's authoritative classification (gold / green / hybrid / bronze / closed / open / unknown).

Click DOI / PMID / OA links to open the source in a new tab.

## Corpus at a glance (production)

The historical backfill is complete; the corpus now spans the full TNBC literature back to the mid-1980s.

- **122,742 canonical records** after dedup, spanning 1985–2026. Present in PubMed (33,343), Europe PMC (40,541), and OpenAlex (111,807), with 29,302 in all three.
- **101,106 records pass the public relevance + topic-tag gate** and are served on the website; the rest remain in the canonical corpus and exports.
- DOI coverage 41.8% (51,266); PMID coverage 28.8% (35,346); at least one ORCID-resolved author on 37.2% (45,702).
- Every DOI-bearing record is enriched against Crossref and Unpaywall (96.3% / 95.6% return usable metadata; the remainder are mostly dataset and preprint-server DOIs). License URL on 58.7% of DOI-bearing records; reference counts on 96.3%.
- ~40% of the corpus has a free-to-read open-access full text (green/gold/bronze/open/hybrid).
- Retraction sweep against ~67,700 Retraction Watch notices found **81 retracted + 4 expression-of-concern records** (77 of which surface in the public library); details in `reports/retracted.csv`.
- Country distribution mirrors published TNBC bibliometric reviews: US and China dominate (~50% combined), then France/Japan/India/UK/Germany/Korea/Italy.

See `reports/coverage_report.md` for the full breakdown.

## How to reproduce (local development)

Python 3.10+ with deps from `requirements.txt`. The scripts resolve their database connection from environment variables, in priority order:

1. `DATABASE_URL` — full Postgres URI (production / Supabase)
2. `PGHOST` + `PGUSER` + `PGDATABASE` — classic libpq env vars
3. `/tmp/pgsock` socket — sandbox-pilot default

For local development against a laptop Postgres or Supabase:

```bash
# Install deps
make install

# Apply schema (works against either local Postgres or DATABASE_URL=supabase URI)
make db-init

# Full pipeline end-to-end
make all
```

For production this all runs unattended on GitHub Actions cron schedules (see `docs/RUNBOOK-orchestration.md`).

Each harvester accepts `--start YYYY-MM-DD --end YYYY-MM-DD --max N`. Each enricher accepts `--max N --workers K` so you can run them in chunks. The Makefile exposes `make harvest`, `make enrich`, `make filter`, `make tag`, `make tier2`, `make benchmark`, `make report` as individual stages.

## Current status and caveats

- **Window:** backfill complete. The corpus spans 1985–2026 (122,742 canonical records), well past the original Phase 1 target of ~25–30k; the earlier 24-month pilot (2024-05-10 → 2026-05-10) has been superseded.
- **Storage:** production runs against managed Postgres / Supabase; the schema is unchanged from the sandbox pilot (only the DSN differs).
- **Sources:** PubMed + Europe PMC + OpenAlex for discovery, enriched with Crossref, Unpaywall, and Retraction Watch — all implemented and running on the weekly cadence. ClinicalTrials.gov and preprint-server-direct integration remain specced but not yet wired in.
- **Tiering:** the tier-1 seed list (~60 foundational papers) and the algorithmic tier-2 nomination pass have both run (`reports/tier1_coverage.md`, `reports/tier2_candidates.csv`); per-page editorial sign-off is the remaining step.
- **Topic tagging:** the rule-based topic tagger has run against the corpus (`reports/topic_tagging.md`); it plus a TNBC-relevance filter defines the 101,106-record public view. Tag coverage is intentionally conservative — records the tagger can't classify are excluded from the public view but retained in the canonical corpus.
- **Data quality:** the Crossref publication-date cross-check is now applied (Crossref is authoritative for `publication_date`), so the citation-alias date drift seen in the pilot is corrected in the canonical corpus; a small number of upstream date outliers (e.g., 2106, 1922) remain in the raw metadata and are suppressed by the website's year-window.

## What this proves about the architecture

1. The free-API spine (PubMed + Europe PMC + OpenAlex) is sufficient to assemble a real bibliography without paying for Scopus or Web of Science.
2. Streaming JSONL + cursor checkpointing makes the harvest **resumable**, which matters for the production weekly job.
3. The DOI → PMID → fuzzy-title dedup ladder collapses ~53% of raw records (matching expectation) with auditable provenance per canonical record.
4. The same pipeline scaled past the full Phase 1 target: widening the date window and completing the backfill took it to 122,742 canonical records with no architectural changes.
