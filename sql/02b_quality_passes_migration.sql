-- TNBC Atlas — Quality-pass columns migration
--
-- Adds the columns that `filter_openalex_only.py` and `tag_topics.py` write.
-- The pilot scripts produced these as JSONL fields only; production wants
-- them in Postgres so the public API view can filter on them.
--
-- Run AFTER 02_enrichment_migration.sql and BEFORE 03_supabase_public_api.sql.

-- OpenAlex post-filter outputs ─────────────────────────────────────────
ALTER TABLE bibliography_records
  ADD COLUMN IF NOT EXISTS tnbc_relevance_score    INT,
  ADD COLUMN IF NOT EXISTS tnbc_relevance_decision TEXT,
  ADD COLUMN IF NOT EXISTS tnbc_relevance_matched  TEXT[];

COMMENT ON COLUMN bibliography_records.tnbc_relevance_decision IS
  'OpenAlex post-filter outcome: trusted_source | keep_strong | keep_moderate | downgrade | drop | keep_manual';

-- Topic-tagging pass outputs ───────────────────────────────────────────
-- topic_tags (TEXT[]) already exists from the base schema; we add the
-- weak-tag and per-domain-hit-count columns the tagger produces.
ALTER TABLE bibliography_records
  ADD COLUMN IF NOT EXISTS topic_tags_weak  TEXT[],
  ADD COLUMN IF NOT EXISTS topic_tag_hits   JSONB;

COMMENT ON COLUMN bibliography_records.topic_tags_weak IS
  'Domains with only 1 hit during rule-based tagging (flagged for editorial follow-up).';
COMMENT ON COLUMN bibliography_records.topic_tag_hits IS
  'Per-domain hit count from the rule-based tagging pass; informs LLM-assisted second-pass triage.';

-- Indexes that the public API view will benefit from ──────────────────
CREATE INDEX IF NOT EXISTS idx_records_relevance_decision
  ON bibliography_records (tnbc_relevance_decision)
  WHERE tnbc_relevance_decision IS NOT NULL;
