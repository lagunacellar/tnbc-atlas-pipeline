"""TNBC Atlas — Rule-based topic-tagging pass.

Maps MeSH terms and title+abstract keywords to the controlled 10-domain Phase 2
taxonomy. This is the first pass; the Phase 1 plan calls for an LLM-assisted
second pass on records that fall through the rule-based net (not implemented
here — requires an LLM API beyond the sandbox).

Rules are intentionally inclusive: a paper that touches a topic gets tagged
for it. Editorial review demotes false positives during tier-1/2 promotion.

Inputs:  exports/bibliography_filtered.jsonl  (post-filter output)
         (falls back to bibliography.jsonl if not present)
Outputs: exports/bibliography_tagged.jsonl
         reports/topic_tagging.md
         reports/topic_distribution.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "exports"
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

INPUT_PATHS = [
    EXPORTS / "bibliography_filtered.jsonl",
    EXPORTS / "bibliography.jsonl",
]

# Domain definitions, from Phase 2 plan §2 and surfaced on /research/synthesis/.
DOMAINS = {
    "A": "Epidemiology and disparities",
    "B": "Biology and molecular subtypes",
    "C": "Diagnosis and pathology",
    "D": "Treatment — early-stage",
    "E": "Treatment — metastatic",
    "F": "Immunotherapy",
    "G": "Computational / ML / AI",
    "H": "Clinical trial methodology",
    "I": "Survivorship and supportive care",
    "J": "Patient experience and ethics",
}

# MeSH terms that strongly indicate a domain. Match is exact (case-insensitive).
MESH_RULES: dict[str, set[str]] = {
    "A": {
        "Incidence", "Prevalence", "Mortality", "Survival Rate", "Risk Factors",
        "Healthcare Disparities", "Ethnic Groups", "African Americans",
        "Black or African American", "Health Status Disparities",
        "Socioeconomic Factors", "Epidemiology",
    },
    "B": {
        "Gene Expression Profiling", "Transcriptome", "Genomics", "Proteomics",
        "Single-Cell Analysis", "RNA-Seq", "Sequence Analysis, RNA",
        "BRCA1 Protein", "BRCA2 Protein", "Homologous Recombination",
        "Tumor Microenvironment", "Lymphocytes, Tumor-Infiltrating",
        "Receptor, ErbB-2", "Receptors, Estrogen", "Receptors, Progesterone",
        "Androgen Receptor Antagonists", "Cell Line, Tumor",
        "Molecular Classification", "Gene Expression Regulation, Neoplastic",
    },
    "C": {
        "Immunohistochemistry", "Biopsy", "Biopsy, Large-Core Needle",
        "Mammography", "Magnetic Resonance Imaging", "Ultrasonography, Mammary",
        "In Situ Hybridization, Fluorescence", "Pathology, Clinical",
        "Biomarkers, Tumor", "Neoplasm Staging", "Image Processing, Computer-Assisted",
    },
    "D": {
        "Neoadjuvant Therapy", "Mastectomy", "Lumpectomy", "Segmental Mastectomy",
        "Radiotherapy, Adjuvant", "Chemotherapy, Adjuvant",
        "Antineoplastic Combined Chemotherapy Protocols",
        "Mastectomy, Segmental",
    },
    "E": {
        "Neoplasm Metastasis", "Brain Neoplasms", "Lung Neoplasms",
        "Liver Neoplasms", "Bone Neoplasms", "Antibodies, Monoclonal, Humanized",
        "Immunoconjugates", "Poly(ADP-ribose) Polymerase Inhibitors",
    },
    "F": {
        "Immunotherapy", "Immune Checkpoint Inhibitors", "B7-H1 Antigen",
        "Programmed Cell Death 1 Receptor", "T-Lymphocytes",
        "Immunotherapy, Adoptive",
    },
    "G": {
        "Artificial Intelligence", "Deep Learning", "Neural Networks, Computer",
        "Machine Learning", "Computational Biology", "Algorithms",
    },
    "H": {
        "Clinical Trials as Topic", "Randomized Controlled Trials as Topic",
        "Research Design", "Endpoint Determination", "Bayes Theorem",
    },
    "I": {
        "Survivorship", "Quality of Life", "Cardiotoxicity", "Lymphedema",
        "Fatigue", "Sleep Wake Disorders", "Palliative Care",
        "Patient Reported Outcome Measures",
    },
    "J": {
        "Decision Making, Shared", "Patient Participation", "Genetic Counseling",
        "Health Services Accessibility", "Bioethics", "Health Equity",
    },
}

# Keyword rules over title + abstract + keywords. Word-boundary regex (case-insensitive).
def kw(*terms: str) -> list[re.Pattern[str]]:
    return [re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in terms]

KEYWORD_RULES: dict[str, list[re.Pattern[str]]] = {
    "A": kw("epidemiology", "incidence", "prevalence", "disparities", "ancestry",
            "African American", "Black women", "socioeconomic", "underrepresented",
            "BRCA1 carriers", "risk factors") + [
            re.compile(r"\bdispar(?:ity|ities)\b", re.I),
        ],
    "B": kw("subtype", "subtypes", "molecular classification", "transcriptomic",
            "genomic", "BRCA", "HRD", "homologous recombination", "BL1", "BL2",
            "BLIA", "BLIS", "MSL", "LAR", "luminal androgen receptor",
            "mesenchymal", "basal-like", "intrinsic subtype", "PAM50", "TILs",
            "tumor-infiltrating lymphocyte", "tumor infiltrating lymphocyte",
            "single-cell", "single cell", "spatial transcriptom") + [
            re.compile(r"\bER[\s\-]?negative\b", re.I),
            re.compile(r"\bPR[\s\-]?negative\b", re.I),
            re.compile(r"\bHER2[\s\-]?(?:low|negative|zero)\b", re.I),
        ],
    "C": kw("immunohistochemistry", "IHC", "FISH", "biopsy", "diagnosis",
            "pathology", "mammography", "MRI", "ultrasound", "biomarker",
            "PD-L1 expression", "CPS score", "Ki-67", "Ki67", "staging",
            "digital pathology", "histopathology", "histology"),
    "D": kw("neoadjuvant", "adjuvant", "early-stage", "early stage",
            "lumpectomy", "mastectomy", "surgery", "pCR",
            "pathologic complete response", "pathological complete response",
            "residual cancer burden", "RCB", "KEYNOTE-522", "CALGB 40603",
            "OlympiA", "CREATE-X", "BrighTNess", "GeparNuevo"),
    "E": kw("metastatic", "metastasis", "metastases", "ASCENT", "sacituzumab",
            "sacituzumab govitecan", "Trodelvy", "trastuzumab deruxtecan",
            "Enhertu", "DESTINY-Breast", "olaparib", "talazoparib", "Lynparza",
            "Talzenna", "OlympiAD", "EMBRACA", "PARP inhibitor", "Trop-2",
            "TROP2", "brain metastasis", "brain metastases", "CNS metastasis",
            "antibody-drug conjugate", "antibody drug conjugate", "ADC"),
    "F": kw("pembrolizumab", "Keytruda", "KEYNOTE", "atezolizumab", "Tecentriq",
            "IMpassion", "PD-L1", "PD-1", "checkpoint inhibitor",
            "immunotherapy", "immune checkpoint", "TIGIT", "LAG-3"),
    "G": kw("deep learning", "machine learning", "neural network", "CNN",
            "convolutional neural network", "artificial intelligence",
            "predictive model", "computational pathology", "radiomics",
            "transformer model", "graph neural network", "AI"),
    "H": kw("I-SPY", "I-SPY2", "platform trial", "basket trial", "umbrella trial",
            "adaptive design", "adaptive randomization", "Bayesian",
            "biomarker-stratified", "phase 1", "phase 2", "phase 3",
            "phase I", "phase II", "phase III"),
    "I": kw("survivorship", "quality of life", "QoL", "patient-reported outcome",
            "patient reported outcome", "PRO", "fatigue", "cardiotoxicity",
            "lymphedema", "supportive care", "palliative care",
            "fertility preservation", "psychosocial"),
    "J": kw("shared decision", "shared decision-making", "patient experience",
            "genetic counseling", "access to care", "affordability",
            "financial toxicity", "bioethics", "informed consent",
            "patient navigation"),
}


# Precompute: combined alternation regex per domain (much faster than N separate searches).
# Also precompute lowercased MeSH rule sets.
_KEYWORD_COMBINED: dict[str, re.Pattern[str]] = {
    domain: re.compile("|".join(p.pattern for p in pats))  # patterns already have re.I via compile
    for domain, pats in KEYWORD_RULES.items()
}
_MESH_LOWER: dict[str, set[str]] = {
    domain: {t.lower() for t in rules}
    for domain, rules in MESH_RULES.items()
}


def tag_record(rec: dict) -> dict[str, int]:
    """Returns {domain_letter: hit_count}. Optimized: one regex search per domain."""
    mesh_lower = {m.lower() for m in (rec.get("mesh_terms") or [])}
    text = " ".join([
        rec.get("title") or "",
        rec.get("abstract") or "",
        " ".join(rec.get("keywords") or []),
    ])

    hits: dict[str, int] = defaultdict(int)

    # MeSH pass
    for domain, terms in _MESH_LOWER.items():
        n = sum(1 for t in terms if t in mesh_lower)
        if n:
            hits[domain] += 2 * n

    # Keyword pass — one combined search per domain, count matches via findall
    for domain, combined in _KEYWORD_COMBINED.items():
        matches = combined.findall(text)
        if matches:
            hits[domain] += len(matches)

    return dict(hits)


def main() -> None:
    in_path = next((p for p in INPUT_PATHS if p.exists()), None)
    if not in_path:
        print(f"No input found. Looked in: {INPUT_PATHS}", file=sys.stderr)
        sys.exit(1)
    print(f"Reading {in_path.name}")

    records: list[dict] = []
    with open(in_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"  loaded {len(records):,} records")

    # Tag
    domain_counts: Counter[str] = Counter()
    multi_tag_distribution: Counter[int] = Counter()
    untagged_records = 0
    for r in records:
        hits = tag_record(r)
        # Only tag domains with at least 2 hits to reduce noise from single-keyword brushes
        domains = sorted([d for d, n in hits.items() if n >= 2])
        # Records with only 1 hit are tagged anyway but flagged low-confidence
        weak_domains = sorted([d for d, n in hits.items() if n == 1])
        r["topic_tags"] = domains
        r["topic_tags_weak"] = weak_domains
        r["topic_tag_hits"] = hits
        for d in domains:
            domain_counts[d] += 1
        multi_tag_distribution[len(domains)] += 1
        if not domains and not weak_domains:
            untagged_records += 1

    # Write tagged corpus
    out_jsonl = EXPORTS / "bibliography_tagged.jsonl"
    with open(out_jsonl, "w") as fh:
        for r in records:
            fh.write(json.dumps(r, default=str) + "\n")
    print(f"Wrote {out_jsonl}")

    # Per-domain CSV
    csv_path = REPORTS / "topic_distribution.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "name", "tagged_records", "share_pct"])
        for d in sorted(DOMAINS):
            n = domain_counts.get(d, 0)
            w.writerow([d, DOMAINS[d], n, f"{100*n/len(records):.1f}"])
    print(f"Wrote {csv_path}")

    # Markdown report
    md: list[str] = []
    md.append("# Topic Tagging Report (Rule-Based First Pass)")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} records (input: `{in_path.name}`)")
    md.append("")
    md.append("**Approach:** Each record is scored against MeSH-term and title/abstract/keyword rule sets per the 10-domain Phase 2 taxonomy. MeSH matches are weighted 2 points; keyword matches 1 point. A domain is tagged when a record accumulates &ge; 2 hits. Records with only 1 hit per domain are recorded as weak tags for editorial follow-up.")
    md.append("")

    md.append("## Tags per domain")
    md.append("")
    md.append("| Code | Domain | Tagged records | Share of corpus |")
    md.append("|---|---|---:|---:|")
    for d in sorted(DOMAINS):
        n = domain_counts.get(d, 0)
        pct = 100 * n / len(records)
        md.append(f"| {d} | {DOMAINS[d]} | {n:,} | {pct:.1f}% |")
    md.append("")

    md.append("## Multi-domain tagging distribution")
    md.append("")
    md.append("Many TNBC papers legitimately span multiple domains (e.g., a paper on ML-assisted prediction of neoadjuvant response tags B, C, D, G). The distribution:")
    md.append("")
    md.append("| Domains tagged per record | Count | Share |")
    md.append("|---:|---:|---:|")
    for k in sorted(multi_tag_distribution):
        n = multi_tag_distribution[k]
        pct = 100 * n / len(records)
        md.append(f"| {k} | {n:,} | {pct:.1f}% |")
    md.append("")

    md.append("## Untagged records")
    md.append("")
    md.append(f"**{untagged_records:,}** records ({100*untagged_records/len(records):.1f}%) did not match any rule. These fall through to the LLM-assisted second pass in production (not run here). Common reasons in the pilot:")
    md.append("")
    md.append("- Preprint or dataset records without abstracts")
    md.append("- Conference abstracts with very terse content")
    md.append("- Papers where TNBC is mentioned only in passing (the OpenAlex post-filter should have caught most of these; remainder is for editorial review)")
    md.append("- Non-English records where the keyword regex misses the term")
    md.append("")

    md.append("## Caveats")
    md.append("")
    md.append("- Rule-based first pass is intentionally inclusive. Editorial review demotes false positives during tier-1 / tier-2 promotion.")
    md.append("- The keyword set is biased toward English-language clinical and biology terminology. Records in other languages or in adjacent fields (basic biology, drug discovery) may be under-tagged. The LLM-assisted second pass is the planned mitigation.")
    md.append("- MeSH terms are only present on PubMed-derived records (~50% of the corpus). OpenAlex-only and Europe-PMC-only records rely entirely on keyword matching against title + abstract.")
    md.append("- The taxonomy is versioned in the project repo. When the editorial board adds, splits, or merges domains, a re-tagging pass over the corpus is required.")
    md.append("")

    md.append("## How to apply in production")
    md.append("")
    md.append("1. Run after `filter_openalex_only.py` so OpenAlex-only drops don't get tagged.")
    md.append("2. The LLM-assisted second pass (not implemented in this script) takes the rule-based output, looks at untagged records and records with only weak tags, and proposes additional tags from the same controlled taxonomy. Every LLM-suggested tag goes into `ml_subtopic_tags` (separate column) until reviewed.")
    md.append("3. Editorial review focuses on (a) untagged records, (b) records tagged only weakly, (c) records tagged in unexpected domain combinations.")
    md.append("4. Promoted tags flow into the website's library filter and into the synthesis-page bibliography slices.")
    md.append("")

    rep_path = REPORTS / "topic_tagging.md"
    rep_path.write_text("\n".join(md))
    print(f"Wrote {rep_path}")

    # Console summary
    print()
    print("=== SUMMARY ===")
    print(f"Records tagged in at least one domain: {len(records) - untagged_records:,} ({100*(len(records)-untagged_records)/len(records):.1f}%)")
    print(f"Records untagged (deferred to LLM pass): {untagged_records:,}")
    for d in sorted(DOMAINS):
        print(f"  Domain {d} ({DOMAINS[d][:30]:30s}): {domain_counts.get(d,0):>5} records ({100*domain_counts.get(d,0)/len(records):.1f}%)")


if __name__ == "__main__":
    main()
