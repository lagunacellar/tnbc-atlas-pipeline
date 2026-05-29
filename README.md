# TNBC Atlas — Phase 1 Pilot

This folder contains the **Phase 1 pilot harvest** for the TNBC Atlas project (see `../00_master_project_plan.md` and `../01_phase1_bibliography_plan.md`).

It demonstrates the bibliography pipeline end-to-end against the most recent 24 months of TNBC literature, using only free public APIs.

## What's in the box

```
tnbc_atlas_pilot/
├── README.md                      ← this file
├── bibliography_browser.html      ← self-contained read-only browser (open in any browser)
├── sql/
│   ├── 01_schema.sql              ← Postgres 14 schema (production-portable)
│   └── 02_enrichment_migration.sql ← columns added by Crossref/Unpaywall/Retraction sweep
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

Open `bibliography_browser.html` in any modern browser (double-click it from your AI ML Lab folder). It is a single self-contained 6 MB file with all 14,319 records embedded; only Tailwind and Grid.js load from CDNs (small) so first open requires an internet connection, but the data and all filtering logic run locally with no further network calls.

**Filters:** free-text (title / journal / first author), year (incl. a "pre-2024 (Crossref re-dated)" bucket), OA status, Crossref publication type (journal-article / preprint / proceedings / book chapter / dissertation / dataset / etc.), source presence (PubMed / Europe PMC / OpenAlex / all-three / OpenAlex-only), minimum citation count.

**Toggles:** Retracted only · Hide pre-window (records Crossref re-dated to before 2024-05-10) · Has license URL.

**Visual cues:** retracted records show a red `RETRACTED` pill next to the title and have a red left border. Source pills now include `CR` (Crossref-enriched) and `UP` (Unpaywall-enriched) alongside `PM`/`EP`/`OA`. OA-status pill colors track Unpaywall's authoritative classification (gold / green / hybrid / bronze / closed / open / unknown).

Click DOI / PMID / OA links to open the source in a new tab.

## Pilot results at a glance

- **14,319 canonical records** after dedup of 30,231 raw records across PubMed (7,465), Europe PMC (7,109), and OpenAlex (15,657).
- DOI coverage 96.8%; **all 13,867 DOIs enriched against Crossref and Unpaywall (100%)**.
- **Crossref re-dated 3,165 records (22.1%)** to a publication date *before* the search window — these are TNBC papers that PubMed/Europe PMC/OpenAlex listed during 2024–2026 but Crossref shows were actually published earlier. The Year filter in the browser includes a "pre-2024 (Crossref re-dated)" option.
- License URL on 9,139 records (65.9% of DOI-bearing).
- Reference counts on 13,281 records (95.8%).
- Retraction sweep against 67,678 Retraction Watch notices found **1 retracted record** in the corpus (highlighted with a red pill in the browser; details in `reports/retracted.csv`).
- Country distribution mirrors published TNBC bibliometric reviews: US and China dominate (~50% combined), India third, then UK/Korea/Italy/France/Canada/Germany/Spain.

See `reports/coverage_report.md` for the full breakdown.

## How to reproduce

The harvest scripts assume a Postgres instance on the Unix socket at `/tmp/pgsock` with database `tnbc_atlas`. Adjust `common.py:db_dsn()` for your environment.

Python deps: `psycopg[binary] requests biopython rapidfuzz pandas pyarrow lxml`.

```bash
# Apply schema (and the enrichment migration)
psql -d tnbc_atlas -f sql/01_schema.sql
psql -d tnbc_atlas -f sql/02_enrichment_migration.sql

# Run the three primary harvesters (PubMed has --resume; OpenAlex has --resume)
python scripts/harvest_pubmed.py
python scripts/harvest_europepmc.py
python scripts/harvest_openalex.py

# Dedup and load
python scripts/dedup_and_load.py

# Enrich (idempotent and resumable; safe to call repeatedly)
python scripts/enrich_crossref.py     # ~20 req/s on the polite pool, 6 workers
python scripts/enrich_unpaywall.py    # ~60 req/s, 6 workers
python scripts/retraction_sweep.py    # one-shot CSV download + match

# Generate exports + coverage report + read-only browser
python scripts/export_and_report.py
python scripts/build_browser.py
```

Each harvester accepts `--start YYYY-MM-DD --end YYYY-MM-DD --max N`. Each enricher accepts `--max N --workers K` so you can run them in chunks.

## Constraints and caveats specific to the pilot

- **Window:** last 24 months (2024-05-10 → 2026-05-10) only. Production calls for backfill to 2005 plus pre-2005 foundational seeding.
- **Storage:** Postgres 14 in the sandbox (single-node, in-memory between sessions). The schema is production-portable; only the DSN changes for a managed Postgres deployment.
- **Sources:** PubMed + Europe PMC + OpenAlex. Crossref / Unpaywall / Retraction Watch / ClinicalTrials.gov / preprint-server-direct integration are specced in `../01_phase1_bibliography_plan.md` but not implemented in this pilot.
- **No tiering applied yet.** Tier assignment is a Phase 1 §7 deliverable that requires editorial input plus a citation-percentile pass — both deferred until full backfill.
- **Topic tagging not run yet.** MeSH terms are imported but the controlled topic taxonomy from Phase 2 is not yet applied.
- **Data-quality findings worth a curation pass:** spot-checking the top-cited records surfaced a few cases where OpenAlex assigned a current-window publication date to a paper whose DOI points to an older work (likely citation aliases or OpenAlex's "primary location" heuristic). These would be caught by a Crossref publication-date cross-check, which is an easy follow-up.

## What this proves about the architecture

1. The free-API spine (PubMed + Europe PMC + OpenAlex) is sufficient to assemble a real bibliography without paying for Scopus or Web of Science.
2. Streaming JSONL + cursor checkpointing makes the harvest **resumable**, which matters for the production weekly job.
3. The DOI → PMID → fuzzy-title dedup ladder collapses ~53% of raw records (matching expectation) with auditable provenance per canonical record.
4. The same pipeline scales to the full Phase 1 target (~25–30k records) by widening the date window and adding the remaining sources — no architectural changes required.
