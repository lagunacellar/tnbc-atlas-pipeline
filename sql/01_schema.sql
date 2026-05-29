-- TNBC Atlas — Phase 1 bibliography schema
-- Postgres 14+. JSONB and TEXT[] used throughout.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS bibliography_records (
  record_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  canonical_doi       TEXT UNIQUE,
  pmid                TEXT,
  pmcid               TEXT,
  openalex_id         TEXT,
  title               TEXT NOT NULL,
  abstract            TEXT,
  authors             JSONB,                    -- [{name, orcid, affiliation, position}]
  journal             TEXT,
  journal_issn        TEXT,
  publication_date    DATE,
  publication_year    INT,
  publication_type    TEXT[],
  mesh_terms          TEXT[],
  keywords            TEXT[],
  language            TEXT,
  countries           TEXT[],
  funding_sources     JSONB,
  clinical_trial_ids  TEXT[],
  preprint_of         UUID REFERENCES bibliography_records(record_id),
  peer_reviewed_of    UUID REFERENCES bibliography_records(record_id),
  oa_status           TEXT,                     -- closed | bronze | green | gold | hybrid | unknown
  oa_url              TEXT,
  citation_count      INT,
  citation_percentile NUMERIC(5,2),
  retraction_status   TEXT DEFAULT 'active',    -- active | retracted | concern
  tier                INT,                      -- 1..4
  topic_tags          TEXT[],
  ml_subtopic_tags    TEXT[],
  first_seen_at       TIMESTAMPTZ DEFAULT now(),
  last_harvested_at   TIMESTAMPTZ DEFAULT now(),
  source_provenance   JSONB                     -- {pubmed: {...}, europepmc: {...}, openalex: {...}}
);

CREATE INDEX IF NOT EXISTS idx_records_pmid ON bibliography_records(pmid);
CREATE INDEX IF NOT EXISTS idx_records_doi ON bibliography_records(canonical_doi);
CREATE INDEX IF NOT EXISTS idx_records_year ON bibliography_records(publication_year);
CREATE INDEX IF NOT EXISTS idx_records_tier ON bibliography_records(tier);
CREATE INDEX IF NOT EXISTS idx_records_title_trgm ON bibliography_records USING gin (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_records_authors ON bibliography_records USING gin (authors);
CREATE INDEX IF NOT EXISTS idx_records_mesh ON bibliography_records USING gin (mesh_terms);

-- Raw upstream snapshots so we can re-derive without re-fetching
CREATE TABLE IF NOT EXISTS raw_snapshots (
  source         TEXT NOT NULL,                 -- pubmed | europepmc | openalex | crossref | unpaywall
  source_id      TEXT NOT NULL,                 -- pmid | europepmc id | openalex id | doi
  fetched_at     TIMESTAMPTZ DEFAULT now(),
  payload        JSONB NOT NULL,
  PRIMARY KEY (source, source_id, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_raw_source_id ON raw_snapshots(source, source_id);

-- Harvest run log (each crawl execution)
CREATE TABLE IF NOT EXISTS harvest_runs (
  run_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  source          TEXT NOT NULL,
  query           TEXT,
  window_start    DATE,
  window_end      DATE,
  started_at      TIMESTAMPTZ DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  records_fetched INT,
  status          TEXT,                         -- success | partial | error
  notes           TEXT
);

-- Dedup audit trail
CREATE TABLE IF NOT EXISTS dedup_decisions (
  decision_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  kept_record_id  UUID REFERENCES bibliography_records(record_id),
  collapsed_from  JSONB,                        -- list of {source, source_id, reason}
  matched_on      TEXT,                         -- doi | pmid | fuzzy_title | manual
  similarity      NUMERIC(4,3),
  decided_at      TIMESTAMPTZ DEFAULT now()
);
