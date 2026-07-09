# TNBC Atlas — Bibliography Coverage Report (Public Evidence Library)

**Generated:** 2026-07-09
**Corpus:** Public evidence-library corpus — the 101,106 records served on the website
**Source snapshot:** `exports/bibliography_slim.json` (101,106 records), joined to `exports/bibliography.jsonl` for author-affiliation, language, and ORCID
**Sources:** PubMed, Europe PMC, OpenAlex (free APIs only), enriched with Crossref + Unpaywall + Retraction Watch
**Storage:** PostgreSQL / Supabase (production), `public_bibliography` view. This report was regenerated from the export snapshots above.

> This report covers the **published evidence-library corpus** — the 101,106 records that pass the public relevance + topic-tag gate and are served at [tnbc.info](https://tnbc.info). They are drawn from a full deduplicated canonical corpus of 122,742 records; the ~21,600 records held back from the public view (weak-relevance, untagged, or outside the display year-window) are excluded from every count below. Headline and distribution tables come directly from the published slim export; author-affiliation, language, and ORCID come from joining the published records back to their full metadata (99.7% matched).

## Headline numbers

- **Published records: 101,106** (of a 122,742-record deduplicated canonical corpus)
- DOI coverage: 43,442 (43.0%)
- PMID coverage: 31,139 (30.8%)
- Records with at least one ORCID-resolved author: 36,821 (36.4%)
- Median citation count: 0 · Max citation count: 30,917
- Records with an OpenAlex-derived citation count: 91,871 (9,235 have no OpenAlex match and carry a null count)
- Span: 1985–2026 · 22,224 records (22%) published in 2024–2026

## Enrichment status

Coverage below is of the 43,442 DOI-bearing records; every one is submitted to Crossref and Unpaywall.

| Enrichment field | Records | Coverage of DOI-bearing |
|---|---:|---:|
| Crossref-typed (publication type resolved) | 41,821 | 96.3% |
| Records with a Crossref reference count | 41,847 | 96.3% |
| Records with a license URL | 25,104 | 57.8% |

The ~3.7% of DOIs without usable Crossref metadata are mostly dataset (Figshare, Zenodo) and preprint-server DOIs not registered with Crossref.

## Retraction sweep (Retraction Watch via Crossref Labs)

- ~67,700 retraction notices loaded from Retraction Watch.
- **Retracted records in the published corpus: 74**
- **Expression-of-Concern records: 3**

The published corpus surfaces 77 flagged records; the full canonical corpus holds 85 (81 retracted + 4 concern), so 8 flagged records sit in records filtered out of the public view. See `reports/retracted.csv` for per-record details.

## Source overlap

Provenance is available for 100,776 published records (99.7%); shares are of the full 101,106 published corpus.

| Source | Records | Share |
|---|---:|---:|
| In PubMed | 29,877 | 29.5% |
| In Europe PMC | 36,707 | 36.3% |
| In OpenAlex | 91,647 | 90.6% |
| In all three | 26,918 | 26.6% |

OpenAlex is by far the broadest discovery source; PubMed and Europe PMC are tighter but higher-precision.

## Open-access status

| Status | Records |
|---|---:|
| closed | 56,498 |
| green | 19,374 |
| gold | 10,330 |
| open | 4,749 |
| bronze | 3,972 |
| unknown | 3,413 |
| hybrid | 2,669 |
| diamond | 10 |
| (no status) | 91 |

About 41% of the published corpus (green + gold + open + bronze + hybrid + diamond = 41,104 records) has a free-to-read full text.

## Year distribution

| Year | Records |
|---|---:|
| 2026 | 4,706 |
| 2025 | 9,284 |
| 2024 | 8,234 |
| 2023 | 6,500 |
| 2022 | 5,139 |
| 2021 | 6,571 |
| 2020 | 8,164 |
| 2019 | 7,259 |
| 2018 | 6,495 |
| 2017 | 6,282 |
| 2016 | 5,756 |
| 2015 | 5,877 |
| 2014 | 5,219 |
| 2013 | 3,757 |
| 2012 | 3,855 |
| 2011 | 2,824 |
| 2010 | 1,958 |
| 2009 | 1,399 |
| 2008 | 712 |
| 2007 | 292 |
| 2006 | 224 |
| 2005 | 91 |
| 2000–2004 (subtotal) | 304 |
| 1990–1999 (subtotal) | 175 |
| 1985–1989 (subtotal) | 29 |

2026 is a partial year. The published corpus is bounded to 1985–2026 by the website's display year-window, so the upstream date-quality outliers present in the full canonical corpus (e.g., 2106, 1922) do not appear here.

## Top 20 journals

| Journal | Records |
|---|---:|
| Cancer Research | 6,608 |
| DOAJ (DOAJ: Directory of Open Access Journals) | 2,066 |
| Journal of Clinical Oncology | 2,021 |
| American journal of cancer research | 1,367 |
| Gan to kagaku ryoho. Cancer & chemotherapy | 1,072 |
| Clinical Cancer Research | 979 |
| theses.fr (ABES) | 804 |
| Institutional Repositories DataBase (IRDB) | 801 |
| Journal of B.U.ON. : official journal of the Balkan Union of Oncology | 798 |
| HAL (Le Centre pour la Communication Scientifique Directe) | 793 |
| Research Square | 784 |
| Anticancer research | 750 |
| Figshare | 737 |
| LA Referencia (Red Federada de Repositorios Institucionales de Publicaciones Científicas) | 684 |
| Annals of Oncology | 672 |
| American journal of translational research | 561 |
| Cancers | 561 |
| International journal of clinical and experimental pathology | 535 |
| Breast cancer research and treatment | 492 |
| bioRxiv (Cold Spring Harbor Laboratory) | 485 |

Several "journals" above are repositories and aggregators (DOAJ, HAL, Figshare, Research Square, theses.fr, IRDB, LA Referencia), reflecting the OpenAlex-sourced preprints, datasets, and dissertations in the corpus.

## Publication type (Crossref)

Crossref publication type is resolved for the 41,821 DOI-bearing, Crossref-enriched records; the remaining ~59,000 records (PubMed/OpenAlex without a registered Crossref type) are not typed here.

| Crossref type | Records |
|---|---:|
| journal-article | 37,313 |
| posted-content (preprint) | 2,644 |
| proceedings-article | 546 |
| peer-review | 428 |
| book-chapter | 281 |
| component | 247 |
| dataset | 156 |
| dissertation | 137 |
| report | 30 |
| other | 16 |
| journal-issue | 14 |
| edited-book | 9 |

## Top 15 countries (by author affiliation)

From the 100,776 published records matched to full metadata.

| Country | Records |
|---|---:|
| US | 18,661 |
| CN | 14,837 |
| JP | 2,735 |
| FR | 2,458 |
| IN | 2,427 |
| GB | 2,273 |
| KR | 1,921 |
| DE | 1,777 |
| IT | 1,726 |
| CA | 1,404 |
| ES | 1,282 |
| TW | 1,027 |
| CH | 911 |
| AU | 900 |
| TR | 852 |

## Language distribution (top 10)

From the 100,776 published records matched to full metadata.

| Language | Records |
|---|---:|
| en | 53,681 |
| eng | 35,504 |
| ja | 1,412 |
| jpn | 1,070 |
| fr | 936 |
| chi | 911 |
| zh | 907 |
| pt | 828 |
| es | 498 |
| id | 384 |

`en`/`eng`, `ja`/`jpn`, and `zh`/`chi` are the same languages under different source vocabularies (ISO 639-1 from Crossref/OpenAlex vs. three-letter codes from PubMed); combined, English is ~89,200 records.

## Notes and caveats

- **Scope.** This report covers the published evidence-library corpus (101,106 records). The full deduplicated canonical corpus is 122,742; records held back by the public relevance + topic-tag gate are excluded from every count above. For the full-corpus view, regenerate from `exports/bibliography.jsonl`.
- **Country, language, and ORCID** are computed by joining the published records back to full metadata; 330 records (0.3%) without a confident DOI/PMID/title match are excluded from those three sections only.
- **Citation counts are OpenAlex-derived.** The 9,235 records with no OpenAlex match carry a null `citation_count`, which is why the median is 0.
- **Snapshot timing.** Retraction totals reflect the current export; the weekly retraction sweep runs continuously, so live counts may differ slightly.
