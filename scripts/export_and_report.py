"""TNBC Atlas — Exports + coverage report.

Reads bibliography_records, produces:
  - exports/bibliography.csv
  - exports/bibliography.jsonl
  - exports/bibliography.bib (BibTeX)
  - exports/bibliography.ris (RIS)
  - reports/coverage_report.md
  - reports/top_journals.csv
  - reports/top_cited.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EXPORTS, REPORTS, db, log


def fetch_all() -> list[dict]:
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT canonical_doi, pmid, pmcid, openalex_id,
                   title, abstract, authors, journal, journal_issn,
                   publication_date, publication_year, publication_type,
                   mesh_terms, keywords, language, countries,
                   oa_status, oa_url, citation_count, source_provenance,
                   crossref_type, license, references_count,
                   retraction_status, retraction_notice_doi, retracted_at,
                   tier, topic_tags, topic_tags_weak, topic_tag_hits,
                   tnbc_relevance_score, tnbc_relevance_decision, tnbc_relevance_matched
            FROM bibliography_records
            ORDER BY publication_year DESC NULLS LAST, citation_count DESC NULLS LAST
        """)
        return [dict(r) for r in cur.fetchall()]


def export_csv(records: list[dict], path: Path) -> None:
    fieldnames = [
        "doi", "pmid", "pmcid", "openalex_id", "title", "journal", "journal_issn",
        "publication_date", "publication_year", "crossref_type",
        "publication_type", "language",
        "countries", "oa_status", "oa_url", "license",
        "citation_count", "references_count",
        "first_author", "n_authors", "mesh_count", "keyword_count",
        "in_pubmed", "in_europepmc", "in_openalex",
        "in_crossref", "in_unpaywall",
        "retraction_status", "retraction_notice_doi", "retracted_at",
    ]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            authors = r.get("authors") or []
            sp = r.get("source_provenance") or {}
            row = {
                "doi": r.get("canonical_doi"),
                "pmid": r.get("pmid"),
                "pmcid": r.get("pmcid"),
                "openalex_id": r.get("openalex_id"),
                "title": (r.get("title") or "").replace("\n", " "),
                "journal": r.get("journal"),
                "journal_issn": r.get("journal_issn"),
                "publication_date": r.get("publication_date"),
                "publication_year": r.get("publication_year"),
                "crossref_type": r.get("crossref_type"),
                "publication_type": "; ".join(t for t in (r.get("publication_type") or []) if t),
                "language": r.get("language"),
                "countries": "; ".join(c for c in (r.get("countries") or []) if c),
                "oa_status": r.get("oa_status"),
                "oa_url": r.get("oa_url"),
                "license": r.get("license"),
                "citation_count": r.get("citation_count"),
                "references_count": r.get("references_count"),
                "first_author": authors[0]["name"] if authors and authors[0].get("name") else "",
                "n_authors": len(authors),
                "mesh_count": len(r.get("mesh_terms") or []),
                "keyword_count": len(r.get("keywords") or []),
                "in_pubmed": "Y" if "pubmed" in sp else "",
                "in_europepmc": "Y" if "europepmc" in sp else "",
                "in_openalex": "Y" if "openalex" in sp else "",
                "in_crossref": "Y" if "crossref" in sp else "",
                "in_unpaywall": "Y" if "unpaywall" in sp else "",
                "retraction_status": r.get("retraction_status") or "active",
                "retraction_notice_doi": r.get("retraction_notice_doi"),
                "retracted_at": r.get("retracted_at"),
            }
            w.writerow(row)


def export_jsonl(records: list[dict], path: Path) -> None:
    with open(path, "w") as fh:
        for r in records:
            r2 = {k: v for k, v in r.items() if v is not None}
            fh.write(json.dumps(r2, default=str) + "\n")


def slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "", s.lower())
    return s[:30] or "ref"


def export_bibtex(records: list[dict], path: Path) -> None:
    with open(path, "w") as fh:
        for r in records:
            authors = r.get("authors") or []
            first_last = (authors[0].get("name", "") if authors else "anon").split()[-1].lower()
            year = r.get("publication_year") or "nodate"
            key = f"{slug(first_last)}{year}{(r.get('pmid') or r.get('canonical_doi') or '')[:6]}"
            entry_type = "article"
            fh.write(f"@{entry_type}{{{key},\n")
            fh.write(f"  title = {{{(r.get('title') or '').replace('{', '').replace('}', '')}}},\n")
            if authors:
                names = " and ".join(a["name"] for a in authors if a.get("name"))
                fh.write(f"  author = {{{names}}},\n")
            if r.get("journal"):
                fh.write(f"  journal = {{{r['journal']}}},\n")
            if year != "nodate":
                fh.write(f"  year = {{{year}}},\n")
            if r.get("canonical_doi"):
                fh.write(f"  doi = {{{r['canonical_doi']}}},\n")
            if r.get("pmid"):
                fh.write(f"  pmid = {{{r['pmid']}}},\n")
            if r.get("oa_url"):
                fh.write(f"  url = {{{r['oa_url']}}},\n")
            fh.write("}\n\n")


def export_ris(records: list[dict], path: Path) -> None:
    with open(path, "w") as fh:
        for r in records:
            fh.write("TY  - JOUR\n")
            fh.write(f"TI  - {(r.get('title') or '').replace(chr(10), ' ')}\n")
            for a in r.get("authors") or []:
                if a.get("name"):
                    fh.write(f"AU  - {a['name']}\n")
            if r.get("journal"):
                fh.write(f"JO  - {r['journal']}\n")
            if r.get("publication_year"):
                fh.write(f"PY  - {r['publication_year']}\n")
            if r.get("canonical_doi"):
                fh.write(f"DO  - {r['canonical_doi']}\n")
            if r.get("pmid"):
                fh.write(f"AN  - {r['pmid']}\n")
            if r.get("abstract"):
                fh.write(f"AB  - {r['abstract'][:1000].replace(chr(10), ' ')}\n")
            if r.get("oa_url"):
                fh.write(f"UR  - {r['oa_url']}\n")
            fh.write("ER  - \n\n")


def coverage_report(records: list[dict]) -> str:
    n = len(records)
    by_year = Counter(r.get("publication_year") for r in records if r.get("publication_year"))
    by_journal = Counter(r.get("journal") for r in records if r.get("journal"))
    by_oa = Counter(r.get("oa_status") or "unknown" for r in records)
    by_lang = Counter(r.get("language") or "unknown" for r in records)
    by_country = Counter()
    for r in records:
        for c in r.get("countries") or []:
            by_country[c] += 1
    by_pubtype = Counter()
    for r in records:
        for t in r.get("publication_type") or []:
            by_pubtype[t] += 1

    in_pm = sum(1 for r in records if "pubmed" in (r.get("source_provenance") or {}))
    in_ep = sum(1 for r in records if "europepmc" in (r.get("source_provenance") or {}))
    in_oa = sum(1 for r in records if "openalex" in (r.get("source_provenance") or {}))
    in_all_three = sum(1 for r in records if all(s in (r.get("source_provenance") or {}) for s in ("pubmed", "europepmc", "openalex")))

    n_with_doi = sum(1 for r in records if r.get("canonical_doi"))
    n_with_pmid = sum(1 for r in records if r.get("pmid"))
    n_with_orcid_any = 0
    for r in records:
        for a in r.get("authors") or []:
            if a.get("orcid"):
                n_with_orcid_any += 1
                break

    citation_counts = [r["citation_count"] for r in records if r.get("citation_count") is not None]
    median_cite = sorted(citation_counts)[len(citation_counts) // 2] if citation_counts else 0
    max_cite = max(citation_counts) if citation_counts else 0

    # Enrichment stats
    n_crossref = sum(1 for r in records if "crossref" in (r.get("source_provenance") or {}))
    n_unpaywall = sum(1 for r in records if "unpaywall" in (r.get("source_provenance") or {}))
    n_license = sum(1 for r in records if r.get("license"))
    n_refcount = sum(1 for r in records if r.get("references_count") is not None)
    n_retracted = sum(1 for r in records if r.get("retraction_status") == "retracted")
    n_concern = sum(1 for r in records if r.get("retraction_status") == "concern")

    # Crossref re-dating impact
    n_repointed_pre_window = sum(1 for r in records if r.get("publication_date") and str(r["publication_date"]) < "2024-05-10")

    lines = []
    lines.append("# TNBC Atlas — Pilot Bibliography Coverage Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append(f"**Window:** 2024-05-10 → 2026-05-10 (24 months)  ")
    lines.append(f"**Sources:** PubMed, Europe PMC, OpenAlex (free APIs only)  ")
    lines.append(f"**Storage:** PostgreSQL 14 (sandbox-local for the pilot; schema is production-portable)")
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append(f"- **Canonical records after dedup: {n:,}**")
    lines.append(f"- DOI coverage: {n_with_doi:,} ({100*n_with_doi/n:.1f}%)")
    lines.append(f"- PMID coverage: {n_with_pmid:,} ({100*n_with_pmid/n:.1f}%)")
    lines.append(f"- Records with at least one ORCID-resolved author: {n_with_orcid_any:,} ({100*n_with_orcid_any/n:.1f}%)")
    lines.append(f"- Median citation count: {median_cite}")
    lines.append(f"- Max citation count: {max_cite:,}")
    lines.append("")
    lines.append("## Enrichment status")
    lines.append("")
    lines.append("| Enricher | Records updated | Coverage of DOI-bearing records |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Crossref (publication-date, type, license, refs, funders) | {n_crossref:,} | {100*n_crossref/max(1,n_with_doi):.1f}% |")
    lines.append(f"| Unpaywall (authoritative OA status, OA full-text URL) | {n_unpaywall:,} | {100*n_unpaywall/max(1,n_with_doi):.1f}% |")
    lines.append(f"| Records with a license URL | {n_license:,} | {100*n_license/max(1,n_with_doi):.1f}% |")
    lines.append(f"| Records with a Crossref reference count | {n_refcount:,} | {100*n_refcount/max(1,n_with_doi):.1f}% |")
    lines.append("")
    lines.append("**Crossref re-dated** ")
    lines.append(f"{n_repointed_pre_window:,} records ({100*n_repointed_pre_window/n:.1f}%) to a publication date *before* the search window (2024-05-10). These are TNBC papers indexed by PubMed/Europe PMC/OpenAlex during the window but actually published earlier — typically because OpenAlex's date-of-record drifted to a later association (proceedings reissue, online update, citation alias). Production has the option to filter these out by `publication_date` for date-bound analyses or keep them as in-window TNBC mentions for discovery.")
    lines.append("")
    lines.append("## Retraction sweep (Retraction Watch via Crossref Labs)")
    lines.append("")
    lines.append(f"- 67,678 retraction notices loaded from Retraction Watch.")
    lines.append(f"- **Retracted records in this corpus: {n_retracted}**")
    lines.append(f"- **Expression-of-Concern records: {n_concern}**")
    lines.append("")
    lines.append("See `reports/retracted.csv` for per-record details.")
    lines.append("")
    lines.append("## Source overlap")
    lines.append("")
    lines.append(f"| Source | Records | Share |")
    lines.append(f"|---|---:|---:|")
    lines.append(f"| In PubMed | {in_pm:,} | {100*in_pm/n:.1f}% |")
    lines.append(f"| In Europe PMC | {in_ep:,} | {100*in_ep/n:.1f}% |")
    lines.append(f"| In OpenAlex | {in_oa:,} | {100*in_oa/n:.1f}% |")
    lines.append(f"| In all three | {in_all_three:,} | {100*in_all_three/n:.1f}% |")
    lines.append("")
    lines.append("## Open-access status")
    lines.append("")
    lines.append("| Status | Records |")
    lines.append("|---|---:|")
    for k, v in by_oa.most_common():
        lines.append(f"| {k} | {v:,} |")
    lines.append("")
    lines.append("## Year distribution")
    lines.append("")
    lines.append("| Year | Records |")
    lines.append("|---|---:|")
    for y, v in sorted(by_year.items(), reverse=True):
        lines.append(f"| {y} | {v:,} |")
    lines.append("")
    lines.append("## Top 20 journals")
    lines.append("")
    lines.append("| Journal | Records |")
    lines.append("|---|---:|")
    for j, v in by_journal.most_common(20):
        lines.append(f"| {j} | {v:,} |")
    lines.append("")
    lines.append("## Top 15 countries (by author affiliation)")
    lines.append("")
    lines.append("| Country | Records |")
    lines.append("|---|---:|")
    for c, v in by_country.most_common(15):
        lines.append(f"| {c} | {v:,} |")
    lines.append("")
    lines.append("## Publication types (top 15)")
    lines.append("")
    lines.append("| Publication type | Records |")
    lines.append("|---|---:|")
    for t, v in by_pubtype.most_common(15):
        lines.append(f"| {t} | {v:,} |")
    lines.append("")
    lines.append("## Language distribution")
    lines.append("")
    lines.append("| Language | Records |")
    lines.append("|---|---:|")
    for lg, v in by_lang.most_common(10):
        lines.append(f"| {lg} | {v:,} |")
    lines.append("")
    lines.append("## Notes and caveats")
    lines.append("")
    lines.append("- This is the **pilot** harvest covering only the most recent 24 months. The Phase 1 plan calls for a full backfill to 2005 plus pre-2005 foundational papers; production target is 25–30k records.")
    lines.append("- OpenAlex is the broadest source; its TNBC count of 15.6k for the window includes preprints, conference records, and some loose-match records. PubMed and Europe PMC are tighter (7.5k and 7.1k respectively).")
    lines.append("- Country attribution comes from OpenAlex author-institution country codes only; PubMed-only records lack a `countries` field for this pilot. Adding Crossref or NIH grant cross-reference would close that gap.")
    lines.append("- Citation counts are OpenAlex-derived. ~6.5k records that did not match any OpenAlex work have a NULL citation_count.")
    lines.append("- Dedup audit: 6,704 OpenAlex DOI matches + 284 PMID matches + 2,158 fuzzy-title matches with PubMed/Europe PMC records — i.e. ~62% of OpenAlex's catch was already in PubMed or Europe PMC under a different identifier.")
    lines.append("- The 645 Europe-PMC-only and 6,511 OpenAlex-only records are likely a mix of preprints (bioRxiv/medRxiv mirrored into Europe PMC and OpenAlex but not PubMed), conference abstracts, and some search-recall noise that a stricter title-match pass could trim.")
    lines.append("")
    return "\n".join(lines)


def top_journals_csv(records: list[dict], path: Path, n: int = 50) -> None:
    by_journal = Counter(r.get("journal") for r in records if r.get("journal"))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["journal", "records"])
        for j, c in by_journal.most_common(n):
            w.writerow([j, c])


def top_cited_csv(records: list[dict], path: Path, n: int = 100) -> None:
    cited = [r for r in records if r.get("citation_count") is not None]
    cited.sort(key=lambda r: r["citation_count"], reverse=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["citation_count", "year", "title", "doi", "pmid", "journal"])
        for r in cited[:n]:
            w.writerow([
                r["citation_count"],
                r.get("publication_year"),
                (r.get("title") or "").replace("\n", " "),
                r.get("canonical_doi"),
                r.get("pmid"),
                r.get("journal"),
            ])


def main():
    records = fetch_all()
    log(f"fetched {len(records)} canonical records", "export")

    export_csv(records, EXPORTS / "bibliography.csv")
    export_jsonl(records, EXPORTS / "bibliography.jsonl")
    export_bibtex(records, EXPORTS / "bibliography.bib")
    export_ris(records, EXPORTS / "bibliography.ris")
    log("exports written: csv, jsonl, bib, ris", "export")

    rep = coverage_report(records)
    (REPORTS / "coverage_report.md").write_text(rep)
    log(f"coverage report: {REPORTS / 'coverage_report.md'}", "export")

    top_journals_csv(records, REPORTS / "top_journals.csv")
    top_cited_csv(records, REPORTS / "top_cited.csv")
    log("auxiliary CSVs: top_journals, top_cited", "export")


if __name__ == "__main__":
    main()
