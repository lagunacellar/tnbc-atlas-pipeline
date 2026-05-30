-- TNBC Atlas — Supabase public API setup
--
-- Creates the read-only public view exposed via PostgREST (auto-served by
-- Supabase at /rest/v1/) and grants SELECT to the anon role.
--
-- Apply once after 01_schema.sql and 02_enrichment_migration.sql.
--
-- The anon role is created automatically by Supabase; we only need to grant
-- it SELECT on the public view (not on the underlying tables). Internal
-- columns like source_provenance and tnbc_relevance_matched stay private.

-- ────────────────────────────────────────────────────────────────────────
-- public_bibliography view: the public projection of bibliography_records
-- ────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW public_bibliography AS
SELECT
  record_id,
  canonical_doi          AS doi,
  pmid,
  pmcid,
  openalex_id,
  title,
  abstract,
  authors,
  journal,
  journal_issn,
  publication_date,
  publication_year,
  publication_type,
  crossref_type,
  mesh_terms,
  keywords,
  language,
  countries,
  oa_status,
  oa_url,
  license,
  citation_count,
  references_count,
  retraction_status,
  retraction_notice_doi,
  retracted_at,
  topic_tags,
  tier,
  tnbc_relevance_decision,
  first_seen_at,
  last_harvested_at
FROM bibliography_records
-- Exclude records the OpenAlex post-filter dropped as search-recall noise.
-- Editorial overrides (tnbc_relevance_decision = 'keep_manual') are included.
WHERE COALESCE(tnbc_relevance_decision, 'trusted_source') IN (
  'trusted_source', 'keep_strong', 'keep_moderate', 'keep_manual'
);

-- Comment for the auto-generated OpenAPI spec
COMMENT ON VIEW public_bibliography IS
  'Public read-only projection of bibliography_records. Excludes records dropped by the OpenAlex post-filter and internal provenance fields. See https://tnbc.info/research/methods/ for the pipeline that produces this data.';

-- ────────────────────────────────────────────────────────────────────────
-- Grants
-- ────────────────────────────────────────────────────────────────────────
-- On Supabase, the `anon` role is the unauthenticated public role
-- attached to requests bearing the project's anon (public) API key.
-- The `authenticated` role is for logged-in users; we don't use it.

GRANT USAGE ON SCHEMA public TO anon;
GRANT SELECT ON public_bibliography TO anon;

-- Explicitly REVOKE on the underlying internal tables so we never
-- accidentally expose them by future migration.
REVOKE ALL ON bibliography_records FROM anon;
REVOKE ALL ON raw_snapshots         FROM anon;
REVOKE ALL ON harvest_runs          FROM anon;
REVOKE ALL ON dedup_decisions       FROM anon;

-- ────────────────────────────────────────────────────────────────────────
-- Row Level Security
-- ────────────────────────────────────────────────────────────────────────
-- Supabase recommends enabling RLS on every table even when access is
-- entirely public, so future schema additions don't silently expose data.
-- The view itself doesn't need RLS (it's not a table); the underlying
-- table is locked down via the REVOKE above plus RLS as defense in depth.

ALTER TABLE bibliography_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_snapshots         ENABLE ROW LEVEL SECURITY;
ALTER TABLE harvest_runs          ENABLE ROW LEVEL SECURITY;
ALTER TABLE dedup_decisions       ENABLE ROW LEVEL SECURITY;

-- No policies = no rows visible. The service-role key (used by the harvest
-- pipeline writing into the DB) bypasses RLS, so the pipeline keeps working.

-- ────────────────────────────────────────────────────────────────────────
-- PostgREST hints
-- ────────────────────────────────────────────────────────────────────────
-- PostgREST honors per-column COMMENT for OpenAPI description fields.
-- Add a few for the most-queried columns; full annotation can come later.

COMMENT ON COLUMN public_bibliography.doi IS
  'Canonical DOI (lowercased). Use for cross-reference with external systems.';
COMMENT ON COLUMN public_bibliography.publication_year IS
  'Crossref-authoritative publication year. May differ from harvest year.';
COMMENT ON COLUMN public_bibliography.oa_status IS
  'Unpaywall open-access classification: gold | green | hybrid | bronze | diamond | open | closed | unknown. See /research/library/ on the website for definitions.';
COMMENT ON COLUMN public_bibliography.tier IS
  'Editorial tier (1=foundational, 2=landmark, 3=supporting, 4=archival). Tier-1 and tier-2 entries are editorially reviewed; tier-3 is the default.';
COMMENT ON COLUMN public_bibliography.retraction_status IS
  'active | retracted | concern. Cross-referenced weekly against Retraction Watch.';

-- ────────────────────────────────────────────────────────────────────────
-- Index hints for common query patterns
-- ────────────────────────────────────────────────────────────────────────
-- The base schema already indexes pmid, canonical_doi, publication_year, tier.
-- Add a partial index for the common 'recent + cited' query pattern.
CREATE INDEX IF NOT EXISTS idx_records_recent_cited
  ON bibliography_records (publication_year DESC, citation_count DESC NULLS LAST)
  WHERE retraction_status = 'active'
    AND COALESCE(tnbc_relevance_decision, 'trusted_source') != 'drop';
