# TNBC Atlas — Bibliography Coverage Report

**Generated:** 2026-07-09
**Corpus:** Full deduplicated canonical corpus, backfill complete
**Source snapshot:** `exports/bibliography.jsonl` (122,742 canonical records)
**Sources:** PubMed, Europe PMC, OpenAlex (free APIs only), enriched with Crossref + Unpaywall + Retraction Watch
**Storage:** PostgreSQL / Supabase (production). This report was regenerated from the export snapshot above; the numbers reproduce the production database.

> The historical backfill is complete: the corpus now spans **1922–2026** (effective range 1985–2026; a handful of pre-1985 and post-2026 records are upstream date-quality outliers). Of the 122,742 deduplicated canonical records, **101,106 pass the public relevance + topic-tag gate** and are served on the website at [tnbc.info](https://tnbc.info); this report covers the full canonical corpus, and flags the published-view figure where it differs.

## Headline numbers

- **Canonical records after dedup: 122,742** (101,106 surfaced in the public evidence library)
- DOI coverage: 51,266 (41.8%)
- PMID coverage: 35,346 (28.8%)
- Records with at least one ORCID-resolved author: 45,702 (37.2%)
- Median citation count: 0 · Max citation count: 30,917
- Records with an OpenAlex-derived citation count: 111,807 (10,935 have no OpenAlex match and carry a null count)

## Enrichment status

Every DOI-bearing record is submitted to Crossref and Unpaywall; coverage below is of the 51,266 DOI-bearing records.

| Enricher | Records | Coverage of DOI-bearing |
|---|---:|---:|
| Crossref (publication-date, type, license, refs, funders) | 49,362 | 96.3% |
| Unpaywall (authoritative OA status, OA full-text URL) | 48,997 | 95.6% |
| Records with a license URL | 30,116 | 58.7% |
| Records with a Crossref reference count | 49,362 | 96.3% |

The ~3.7% of DOIs without usable Crossref metadata are mostly dataset (Figshare, Zenodo) and preprint-server DOIs not registered with Crossref; they are marked enriched-with-error in `source_provenance`.

## Historical depth (backfill)

100,577 records (81.9%) predate 2024-05-10 — i.e. they are the historical literature added by the backfill, sitting behind the original 24-month pilot window. Crossref is treated as authoritative for `publication_date` across the corpus, so each record is anchored to when the work was actually published rather than when a source database first surfaced it.

## Retraction sweep (Retraction Watch via Crossref Labs)

- ~67,700 retraction notices loaded from Retraction Watch.
- **Retracted records in this corpus: 81**
- **Expression-of-Concern records: 4**
- Of these, **77 (74 retracted + 3 concern) surface in the public evidence library**; the remainder sit in records filtered out of the public view.

See `reports/retracted.csv` for per-record details.

## Source overlap

| Source | Records | Share |
|---|---:|---:|
| In PubMed | 33,343 | 27.2% |
| In Europe PMC | 40,541 | 33.0% |
| In OpenAlex | 111,807 | 91.1% |
| In all three | 29,302 | 23.9% |

OpenAlex is by far the broadest discovery source; PubMed and Europe PMC are tighter but higher-precision.

## Open-access status

| Status | Records |
|---|---:|
| closed | 68,598 |
| green | 24,906 |
| gold | 11,932 |
| bronze | 4,950 |
| open | 4,850 |
| hybrid | 3,019 |
| unknown | 4,302 |
| diamond | 10 |
| (no status) | 175 |

About 40% of the corpus (green + gold + bronze + open + hybrid + diamond ≈ 49,700 records) has a free-to-read full text.

## Year distribution

| Year | Records |
|---|---:|
| 2026 | 5,378 |
| 2025 | 11,170 |
| 2024 | 9,318 |
| 2023 | 7,512 |
| 2022 | 6,149 |
| 2021 | 7,814 |
| 2020 | 9,902 |
| 2019 | 8,880 |
| 2018 | 8,310 |
| 2017 | 7,568 |
| 2016 | 7,310 |
| 2015 | 7,118 |
| 2014 | 6,619 |
| 2013 | 4,534 |
| 2012 | 4,431 |
| 2011 | 3,485 |
| 2010 | 2,333 |
| 2009 | 1,660 |
| 2008 | 795 |
| 2007 | 333 |
| 2006 | 253 |
| 2005 | 115 |
| 2000–2004 (subtotal) | 1,088 |
| 1990–1999 (subtotal) | 561 |
| 1985–1989 (subtotal) | 34 |
| pre-1985 (subtotal) | 67 |
| post-2026 (date-quality outliers) | 5 |

2026 is a partial year. The website applies a display year-window of 1985 to current-year + 1, so the pre-1985 and post-2026 outlier records above are suppressed from the public library view but retained in the canonical corpus.

## Top 20 journals

| Journal | Records |
|---|---:|
| Cancer Research | 7,580 |
| DOAJ (DOAJ: Directory of Open Access Journals) | 2,662 |
| Journal of Clinical Oncology | 2,140 |
| American journal of cancer research | 1,615 |
| theses.fr (ABES) | 1,210 |
| Annals of Oncology | 1,128 |
| Gan to kagaku ryoho. Cancer & chemotherapy | 1,101 |
| Clinical Cancer Research | 1,025 |
| HAL (Le Centre pour la Communication Scientifique Directe) | 1,006 |
| Figshare | 1,000 |
| Research Square | 913 |
| Institutional Repositories DataBase (IRDB) | 874 |
| Journal of B.U.ON. : official journal of the Balkan Union of Oncology | 817 |
| Anticancer research | 794 |
| LA Referencia (Red Federada de Repositorios Institucionales de Publicaciones Científicas) | 776 |
| Cancers | 667 |
| bioRxiv (Cold Spring Harbor Laboratory) | 646 |
| American journal of translational research | 599 |
| International journal of clinical and experimental pathology | 553 |
| Frontiers in oncology | 526 |

Several "journals" above are repositories and aggregators (DOAJ, HAL, Figshare, Research Square, theses.fr, IRDB, LA Referencia), reflecting the OpenAlex-sourced preprints, datasets, and dissertations in the corpus.

## Top 15 countries (by author affiliation)

| Country | Records |
|---|---:|
| US | 21,890 |
| CN | 16,322 |
| FR | 3,021 |
| JP | 3,007 |
| IN | 2,952 |
| GB | 2,681 |
| DE | 2,097 |
| KR | 2,090 |
| IT | 2,014 |
| CA | 1,689 |
| ES | 1,501 |
| TW | 1,213 |
| AU | 1,041 |
| CH | 1,025 |
| TR | 981 |

## Publication types (top 15)

| Publication type | Records |
|---|---:|
| article | 58,362 |
| Journal Article | 35,153 |
| dissertation | 12,367 |
| Research Support, Non-U.S. Gov't | 7,366 |
| Abstract | 4,166 |
| Review | 3,770 |
| other | 2,875 |
| preprint | 2,364 |
| Research Support, N.I.H., Extramural | 1,876 |
| Preprint | 1,857 |
| Case Reports | 1,416 |
| English Abstract | 1,000 |
| Dissertation | 831 |
| research-article | 790 |
| Comparative Study | 667 |

Types mix PubMed publication-type vocabulary (Journal Article, Review, Case Reports, …) with Crossref/OpenAlex types (article, preprint, dissertation, …); a record can carry more than one.

## Language distribution (top 10)

| Language | Records |
|---|---:|
| en | 65,721 |
| eng | 39,796 |
| zh | 2,673 |
| ja | 1,952 |
| fr | 1,424 |
| pt | 1,189 |
| jpn | 1,117 |
| chi | 938 |
| es | 908 |
| id | 488 |

`en`/`eng`, `ja`/`jpn`, and `zh`/`chi` are the same languages under different source vocabularies (ISO 639-1 from Crossref/OpenAlex vs. three-letter codes from PubMed); combined, English is ~105,500 records.

## Notes and caveats

- **Backfill complete.** This is the full production corpus, not the 24-month pilot. It spans 1985–2026 effectively (122,742 canonical records), of which 101,106 pass the public relevance + topic-tag gate and appear on the website.
- **Country attribution** comes from OpenAlex author-institution country codes only; records without an OpenAlex match lack country data, so shares understate coverage.
- **Citation counts are OpenAlex-derived.** The 10,935 records with no OpenAlex match carry a null `citation_count`, which is why the median is 0.
- **Upstream date-quality outliers** (e.g., a 2106 or a 1922 record) originate in source metadata and are suppressed by the website's year-window, not corrected in the canonical corpus.
- **Snapshot timing.** Retraction totals reflect the `bibliography.jsonl` export; the weekly retraction sweep runs continuously, so live counts may differ slightly.
