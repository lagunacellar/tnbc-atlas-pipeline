# TNBC Atlas — Curation Layer

This folder holds the **hand-curated** seed lists that the editorial board owns. Unlike the bibliography (which is automatically harvested), these files require human judgment and are versioned in Git so every change is auditable.

## What's here

```
curation/
├── README.md          ← this file
└── tier1_seed.yml     ← ~60 foundational TNBC papers, with provenance and rationale
```

A future v2 of this directory will likely add `tier2_candidates.yml` (algorithmically nominated, human-confirmed), `topic_taxonomy.yml` (the controlled topic vocabulary from Phase 2 plan §2), and `synonyms.yml` (the search synonym dictionary from Phase 3 plan §5).

---

## The tier-1 seed list

### What it is

A YAML file enumerating the foundational, field-defining TNBC papers — the ones a competent oncology trainee would expect to see referenced in any serious review of the field. Currently 60 entries spanning biology, diagnosis, treatment, immunotherapy, ADCs, ML/AI, epidemiology, and clinical trial methodology.

### What it's for

Three uses, in order of immediate impact:

1. **Recall benchmark** for the bibliography harvester. After every harvest, `scripts/tier1_benchmark.py` cross-references this list against the corpus and reports which entries were found, by what match basis, and which were missed. The in-window recall number is the single best signal for "did the harvest do its job."
2. **Tier=1 seed** for the production tier assignment process described in Phase 1 plan §7. When the production tier-1 review runs, every entry in this list (after editorial review) gets `tier=1` in the bibliography database. Algorithmic candidates for tier-2 are nominated separately and require human confirmation.
3. **Reading list** for Phase 2 synthesis writers. When drafting a topic page, writers can filter this list by domain to surface the canonical primary literature they should cite first, before reaching for tier-2 or tier-3 references.

### Schema

Each entry has:

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Stable identifier `tier1-<firstauthor><year>-<short>` |
| `title` | yes | Full paper title |
| `authors` | yes | List of author names; first 3 + final author at minimum |
| `year` | yes | Publication year |
| `journal` | yes | Full journal name, not abbreviation |
| `doi` | preferred | Lowercased, no `https://doi.org/` prefix |
| `pmid` | preferred | String of digits |
| `domain` | yes | One of A–J from Phase 2 plan §2 (epidemiology, biology, diagnosis, etc.) |
| `subtopic` | yes | Free-form tag; informs taxonomy v2 |
| `rationale` | yes | One sentence explaining why this is foundational |
| `confidence` | yes | `high` / `medium` / `low` — how confident the curator is in inclusion |
| `source` | yes | Where the entry came from: `bib2022`, `adc2024`, `io2024`, `neoadj24`, or `editorial` |
| `note` | optional | Caveats, identifier verification reminders, etc. |

### How the list was assembled

The current v1 list was drafted by triangulating four sources:

1. The 2022 *Clin Exp Med* "top 100 most cited TNBC articles" bibliometric — most-cited trial and biology papers across all years through 2022.
2. The 2024 *Discover Oncology* ADC-in-TNBC bibliometric — sacituzumab govitecan, trastuzumab deruxtecan, ASCENT, DESTINY-Breast04 lineage.
3. The 2024 *Frontiers in Oncology* immunotherapy-in-TNBC bibliometric — KEYNOTE-522, IMpassion130, KEYNOTE-355, IMpassion131, KEYNOTE-119.
4. The 2024 *Frontiers* neoadjuvant-TNBC bibliometric — CALGB 40603, BrighTNess, GeparNuevo, KEYNOTE-522.

Plus editorial picks for the foundational subtype/biology papers (Perou 2000, Sørlie 2001/2003, Lehmann 2011/2016, Burstein 2015, METABRIC, TCGA, Bareche 2018, Jiang 2019), the ASCO/CAP testing guidelines (Allison 2020 ER/PR, Wolff 2018 HER2), the TILs Working Group consensus (Salgado 2015, Loi 2019, Denkert 2018), the PARP inhibitor lineage (Tutt 2010 → OlympiAD → EMBRACA → OlympiA), and a small set of ML/AI representative papers (Saltz 2018, Li 2023, Krishnamurthy 2023).

**This is a draft.** It is explicitly marked as needing editorial review. Some entries are flagged with confidence `medium` or `low` and `note:` fields where the curator's confidence in identifier accuracy or inclusion-worthiness is lower.

### Editorial review process

Before any entry is promoted into the production database with `tier=1`, the editorial PI plus at least one domain expert should:

1. Confirm the entry is genuinely foundational — not just frequently cited.
2. Verify the DOI and PMID resolve to the correct paper (the curator drafted these from memory cross-checked against Crossref/PubMed but the editorial board owns the final accuracy claim).
3. Decide whether to upgrade `confidence` to `high` or downgrade / remove.
4. Flag any obvious omissions for inclusion in v2.

Reviewed entries should be moved to `tier1_approved.yml` (planned for v2 of this folder) and stamped with the reviewer's identifier and date.

### Maintaining the list over time

The tier-1 list is meant to grow slowly. Roughly:

- **New trial readout** that changes practice (next pivotal Phase 3) → add immediately.
- **New foundational biology paper** that proposes a new taxonomy or replaces an existing classifier → add after one year of citation accumulation.
- **Existing entry retracted** → mark `confidence: retracted` (don't delete — preserves audit trail) and remove from tier-1 promotion eligibility. The retraction sweep already flags this in the bibliography itself.
- **Entry superseded** by a definitive update or larger pooled analysis → add the update; demote the predecessor to `confidence: superseded`.

A tier-1 entry leaving the list is rare and significant; every change should carry a one-line changelog entry in the YAML header.

---

## Benchmark output reference

After running `scripts/tier1_benchmark.py`, two files appear in `reports/`:

- `tier1_coverage.md` — human-readable summary with recall by domain, by confidence, and per-entry diagnostics for in-window misses.
- `tier1_matches.csv` — one row per tier-1 entry with the matched corpus record (or NULL).

The current benchmark (against the 24-month pilot corpus) shows **30 / 60 tier-1 entries matched**, all via DOI. None are in-window matches because the tier-1 list is, by design, dominated by pre-2024 foundational papers. The 30 matches are pre-window papers that OpenAlex re-surfaced during our search window — bonus coverage we can keep, but not the operational signal we'll care about post-backfill.

The operationally meaningful number — **in-window recall** — is presently 0/0 only because the v1 tier-1 list contains no 2024–2026 entries. Once 2024–2026 trials and methodology papers are added (KEYNOTE-522 long-term follow-ups, the next pivotal ADC trials, the next TNBC subtype refinement), the benchmark will start producing a meaningful in-window recall percentage.
