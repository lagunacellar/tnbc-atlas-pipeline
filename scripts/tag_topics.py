"""TNBC Atlas — Rule-based topic-tagging pass (Postgres-native).

Reads every record from `bibliography_records`, maps MeSH terms and
title+abstract keywords to the 10-domain Phase 2 taxonomy, and writes the
tags back to Postgres via COPY-into-temp + UPDATE-FROM.

Writes:
  Postgres bibliography_records.topic_tags / _weak / topic_tag_hits
  reports/topic_distribution.csv
  reports/topic_tagging.md
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).parent))
from common import REPORTS, db, log

REPORTS.mkdir(parents=True, exist_ok=True)

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

MESH_RULES: dict[str, set[str]] = {
    "A": {"Incidence", "Prevalence", "Mortality", "Survival Rate", "Risk Factors",
          "Healthcare Disparities", "Ethnic Groups", "African Americans",
          "Black or African American", "Health Status Disparities",
          "Socioeconomic Factors", "Epidemiology"},
    "B": {"Gene Expression Profiling", "Transcriptome", "Genomics", "Proteomics",
          "Single-Cell Analysis", "RNA-Seq", "Sequence Analysis, RNA",
          "BRCA1 Protein", "BRCA2 Protein", "Homologous Recombination",
          "Tumor Microenvironment", "Lymphocytes, Tumor-Infiltrating",
          "Receptor, ErbB-2", "Receptors, Estrogen", "Receptors, Progesterone",
          "Androgen Receptor Antagonists", "Cell Line, Tumor",
          "Molecular Classification", "Gene Expression Regulation, Neoplastic"},
    "C": {"Immunohistochemistry", "Biopsy", "Biopsy, Large-Core Needle",
          "Mammography", "Magnetic Resonance Imaging", "Ultrasonography, Mammary",
          "In Situ Hybridization, Fluorescence", "Pathology, Clinical",
          "Biomarkers, Tumor", "Neoplasm Staging", "Image Processing, Computer-Assisted"},
    "D": {"Neoadjuvant Therapy", "Mastectomy", "Lumpectomy", "Segmental Mastectomy",
          "Radiotherapy, Adjuvant", "Chemotherapy, Adjuvant",
          "Antineoplastic Combined Chemotherapy Protocols", "Mastectomy, Segmental"},
    "E": {"Neoplasm Metastasis", "Brain Neoplasms", "Lung Neoplasms",
          "Liver Neoplasms", "Bone Neoplasms", "Antibodies, Monoclonal, Humanized",
          "Immunoconjugates", "Poly(ADP-ribose) Polymerase Inhibitors"},
    "F": {"Immunotherapy", "Immune Checkpoint Inhibitors", "B7-H1 Antigen",
          "Programmed Cell Death 1 Receptor", "T-Lymphocytes",
          "Immunotherapy, Adoptive"},
    "G": {"Artificial Intelligence", "Deep Learning", "Neural Networks, Computer",
          "Machine Learning", "Computational Biology", "Algorithms"},
    "H": {"Clinical Trials as Topic", "Randomized Controlled Trials as Topic",
          "Research Design", "Endpoint Determination", "Bayes Theorem"},
    "I": {"Survivorship", "Quality of Life", "Cardiotoxicity", "Lymphedema",
          "Fatigue", "Sleep Wake Disorders", "Palliative Care",
          "Patient Reported Outcome Measures"},
    "J": {"Decision Making, Shared", "Patient Participation", "Genetic Counseling",
          "Health Services Accessibility", "Bioethics", "Health Equity"},
}


def kw(*terms: str) -> list[re.Pattern[str]]:
    return [re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in terms]


KEYWORD_RULES: dict[str, list[re.Pattern[str]]] = {
    "A": kw("epidemiology", "incidence", "prevalence", "disparities", "ancestry",
            "African American", "Black women", "socioeconomic", "underrepresented",
            "BRCA1 carriers", "risk factors") + [re.compile(r"\bdispar(?:ity|ities)\b", re.I)],
    "B": kw("subtype", "subtypes", "molecular classification", "transcriptomic",
            "genomic", "BRCA", "HRD", "homologous recombination", "BL1", "BL2",
            "BLIA", "BLIS", "MSL", "LAR", "luminal androgen receptor",
            "mesenchymal", "basal-like", "intrinsic subtype", "PAM50", "TILs",
            "tumor-infiltrating lymphocyte", "tumor infiltrating lymphocyte",
            "single-cell", "single cell", "spatial transcriptom") + [
            re.compile(r"\bER[\s\-]?negative\b", re.I),
            re.compile(r"\bPR[\s\-]?negative\b", re.I),
            re.compile(r"\bHER2[\s\-]?(?:low|negative|zero)\b", re.I)],
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

# Precompute: combined alternation regex per domain (one search per domain instead of N).
_KEYWORD_COMBINED: dict[str, re.Pattern[str]] = {
    domain: re.compile("|".join(p.pattern for p in pats))
    for domain, pats in KEYWORD_RULES.items()
}
_MESH_LOWER: dict[str, set[str]] = {
    domain: {t.lower() for t in rules}
    for domain, rules in MESH_RULES.items()
}


def tag_record(title: str | None, abstract: str | None, keywords: list[str] | None,
               mesh_terms: list[str] | None) -> dict[str, int]:
    """Returns {domain_letter: hit_count}."""
    mesh_lower = {(m or "").lower() for m in (mesh_terms or [])}
    text = " ".join([title or "", abstract or "", " ".join(keywords or [])])

    hits: dict[str, int] = defaultdict(int)
    for domain, terms in _MESH_LOWER.items():
        n = sum(1 for t in terms if t in mesh_lower)
        if n:
            hits[domain] += 2 * n
    for domain, combined in _KEYWORD_COMBINED.items():
        matches = combined.findall(text)
        if matches:
            hits[domain] += len(matches)
    return dict(hits)


def main() -> None:
    log("reading records from Postgres", "tag")
    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT record_id, title, abstract, keywords, mesh_terms
              FROM bibliography_records
        """)
        records = cur.fetchall()
    log(f"  loaded {len(records):,} records", "tag")

    updates: list[tuple] = []  # (record_id, topic_tags, topic_tags_weak, topic_tag_hits)
    domain_counts: Counter[str] = Counter()
    multi_tag_distribution: Counter[int] = Counter()
    untagged_records = 0

    for r in records:
        hits = tag_record(r["title"], r["abstract"], r["keywords"], r["mesh_terms"])
        domains = sorted([d for d, n in hits.items() if n >= 2])
        weak_domains = sorted([d for d, n in hits.items() if n == 1])
        updates.append((
            r["record_id"],
            domains,
            weak_domains,
            Jsonb(hits) if hits else Jsonb({}),
        ))
        for d in domains:
            domain_counts[d] += 1
        multi_tag_distribution[len(domains)] += 1
        if not domains and not weak_domains:
            untagged_records += 1

    log(f"  classified; writing back to Postgres", "tag")

    with db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TEMP TABLE tag_updates (
                record_id        UUID PRIMARY KEY,
                topic_tags       TEXT[],
                topic_tags_weak  TEXT[],
                topic_tag_hits   JSONB
            ) ON COMMIT DROP
        """)
        with cur.copy("COPY tag_updates (record_id, topic_tags, topic_tags_weak, topic_tag_hits) FROM STDIN") as copy:
            for row in updates:
                copy.write_row(row)
        cur.execute("""
            UPDATE bibliography_records r
               SET topic_tags      = u.topic_tags,
                   topic_tags_weak = u.topic_tags_weak,
                   topic_tag_hits  = u.topic_tag_hits
              FROM tag_updates u
             WHERE r.record_id = u.record_id
        """)
        log(f"  UPDATEd {len(updates):,} rows", "tag")

    # Per-domain CSV
    csv_path = REPORTS / "topic_distribution.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "name", "tagged_records", "share_pct"])
        for d in sorted(DOMAINS):
            n = domain_counts.get(d, 0)
            w.writerow([d, DOMAINS[d], n, f"{100*n/len(records):.1f}"])
    log(f"wrote {csv_path}", "tag")

    # Markdown report
    md: list[str] = []
    md.append("# Topic Tagging Report (Rule-Based First Pass)")
    md.append("")
    md.append(f"**Corpus:** {len(records):,} records (read from Postgres)")
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
    md.append("| Domains tagged per record | Count | Share |")
    md.append("|---:|---:|---:|")
    for k in sorted(multi_tag_distribution):
        n = multi_tag_distribution[k]
        pct = 100 * n / len(records)
        md.append(f"| {k} | {n:,} | {pct:.1f}% |")
    md.append("")
    md.append(f"**Untagged:** {untagged_records:,} ({100*untagged_records/len(records):.1f}%) — deferred to LLM-assisted second pass.")
    md.append("")
    (REPORTS / "topic_tagging.md").write_text("\n".join(md))
    log(f"wrote {REPORTS / 'topic_tagging.md'}", "tag")

    print()
    print("=== SUMMARY ===")
    print(f"Records tagged in at least one domain: {len(records) - untagged_records:,} ({100*(len(records)-untagged_records)/len(records):.1f}%)")
    print(f"Records untagged: {untagged_records:,}")
    for d in sorted(DOMAINS):
        print(f"  Domain {d} ({DOMAINS[d][:30]:30s}): {domain_counts.get(d,0):>5} records ({100*domain_counts.get(d,0)/len(records):.1f}%)")


if __name__ == "__main__":
    main()
