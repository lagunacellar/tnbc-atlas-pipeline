# Contributing to TNBC Atlas — bibliography pipeline

Thanks for your interest in contributing. This repository contains the data pipeline that builds the bibliography backing [tnbc.info](https://tnbc.info). Editorial content (the actual website prose) lives in a separate repository.

## Scope of this repository

In scope:

- Harvest scripts for PubMed, Europe PMC, OpenAlex (and additions of new sources)
- Deduplication logic
- Enrichment passes (Crossref, Unpaywall, Retraction Watch)
- Topic-tagging rules
- Tier assignment (algorithmic nomination scripts; the tier-1 / tier-2 *content* lists are editorial and reviewed by the project's editorial board)
- Reports and exports
- Schema and migration files
- Documentation about the pipeline itself

Out of scope (handled in the website repository):

- Patient-facing or researcher-facing prose
- Synthesis page authoring
- Design and UX
- Cloudflare deployment configuration

## Before you start

1. **Read [the methods page](https://tnbc.info/research/methods/)** — it explains what the pipeline does and why.
2. **Set up locally**: see `README.md` for environment requirements; `make install && make db-init && make all` will reproduce the full pilot.
3. **Open an issue first** for anything beyond a small fix, so we can align on the approach before you invest time.

## What kinds of contributions we welcome

- **Bug fixes** in any script, with a short test or reproduction.
- **New data sources** — add a harvester following the `harvest_*.py` pattern (streaming JSONL, resumable, polite-pool rate limiting, no hard dependency on paid APIs).
- **Enrichment passes** beyond the current Crossref / Unpaywall / Retraction Watch set.
- **Topic-tagging rules** — additions and refinements to the rule sets in `scripts/tag_topics.py`. Edit the MeSH and keyword rule dictionaries; explain your additions in the PR description.
- **Coverage benchmarks** — comparing the pilot or production corpus against external sources (bibliometric reviews, NCCN reference lists, etc.).
- **Performance improvements** to the harvest, dedup, or tagging pipeline.
- **Documentation** — clarifications, worked examples, troubleshooting.

## What we don't accept

- Changes that introduce dependencies on paid APIs as a hard requirement.
- Changes that silently remove records from the bibliography (use `tnbc_relevance_decision` or tier-4 archival instead).
- Direct edits to `curation/tier1_seed.yml` without editorial-board sign-off — that file is owned by the editorial process. Suggest additions in an issue.
- Web-scraping of paywalled content.

## Pull request checklist

- [ ] Code targets Python 3.10+
- [ ] New deps are added to `requirements.txt` with a version specifier
- [ ] Long-running scripts are resumable (safe to interrupt and re-run)
- [ ] API calls respect the source's documented rate limits and include a polite-pool `mailto` or User-Agent
- [ ] Any new output goes to `reports/` or `exports/` (which are `.gitignored`)
- [ ] If you add a new tagging rule or scoring threshold, add a note to the PR explaining the rationale
- [ ] If your change affects the methods description, propose a parallel update to the website's `/research/methods/` page (in the website repo)
- [ ] Run `make all` locally and confirm it completes without errors

## Editorial process for curation files

`curation/tier1_seed.yml` and any future `tier2_approved.yml` are not maintained through PRs in this repo. They are maintained by the editorial board, with reviewer initials and dates per entry. If you believe an entry is missing or incorrect:

1. Open an issue describing the proposed change with full citation details and rationale.
2. The editorial PI evaluates the suggestion against the inclusion criteria documented in `curation/README.md`.
3. Approved changes are added by the editorial board, not by code contributors.

## Reporting security issues

If you find a security issue (credential leak, injection vulnerability, dependency CVE), please email `security@tnbc.info` rather than opening a public issue. We acknowledge security reports within 24 hours.

## Conduct

Be respectful. Disagreements about technical direction are fine; disagreements that target a person are not. Project maintainers reserve the right to close issues and PRs that do not engage in good faith.

## License

By contributing, you agree that your contributions will be licensed under the MIT License (see `LICENSE`).
