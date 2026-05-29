-- TNBC Atlas — Phase 1 enrichment migration
-- Adds columns populated by Crossref, Unpaywall, and Retraction Watch enrichers.

ALTER TABLE bibliography_records
  ADD COLUMN IF NOT EXISTS crossref_enriched_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS unpaywall_enriched_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS license               TEXT,
  ADD COLUMN IF NOT EXISTS references_count      INT,
  ADD COLUMN IF NOT EXISTS crossref_type         TEXT,
  ADD COLUMN IF NOT EXISTS retraction_notice_doi TEXT,
  ADD COLUMN IF NOT EXISTS retracted_at          DATE;

CREATE INDEX IF NOT EXISTS idx_records_retraction ON bibliography_records(retraction_status)
  WHERE retraction_status IS NOT NULL AND retraction_status != 'active';

CREATE INDEX IF NOT EXISTS idx_records_crossref_enriched ON bibliography_records(crossref_enriched_at);
CREATE INDEX IF NOT EXISTS idx_records_unpaywall_enriched ON bibliography_records(unpaywall_enriched_at);
