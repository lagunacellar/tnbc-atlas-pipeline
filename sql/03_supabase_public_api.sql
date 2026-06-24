-- TNBC Atlas — Supabase public API setup
--
-- Creates the read-only public view exposed via PostgREST (auto-served by
-- Supabase at /rest/v1/) and grants SELECT to the anon role.
--
-- Apply once after 01_schema.sql, 02_enrichment_migration.sql, and
-- 02b_quality_passes_migration.sql.
--
-- The anon role is created automatically by Supabase; we only need to grant
-- it SELECT on the public view (not on the underlying tables). Internal
-- columns like source_provenance and tnbc_relevance_matched stay private.
--
-- ── Important: do not wrap tnbc_relevance_decision in COALESCE ──
-- An earlier version of this file expressed the "kept by the relevance
-- filter, or never went through the filter (trusted_source seeds)" predicate
-- as:
--
--     WHERE COALESCE(tnbc_relevance_decision, 'trusted_source') IN (...)
--
-- That form is logically correct but Postgres cannot use a regular column
-- index on tnbc_relevance_decision when the column is wrapped in a function
-- call — the planner falls back to a sequential scan, and the per-row
-- COALESCE evaluation is just expensive enough that `SELECT count(*) FROM
-- public_bibliography` exceeds Supabase's default 3-second anon
-- statement_timeout. The current `IS NULL OR ... IN (...)` form below is
-- equivalent and "sargable" — the planner maps each branch to an index
-- lookup on the index defined in 02b_quality_passes_migration.sql.
--
-- The same principle applies to the partial index predicate at the bottom of
-- this file; both have been written in the index-friendly form.

-- ────────────────────────────────────────────────────────────────────────
-- public_bibliography view: the public projection of bibliography_records
-- ────────────────────────────────────────────────────────────────────────
-- Use DROP + CREATE rather than CREATE OR REPLACE. CREATE OR REPLACE VIEW
-- enforces a "columns may only be added at the end" rule and errors with
-- 42P16 ("cannot drop columns from view") if the column list changes order
-- or shape. DROP + CREATE has no such restriction and is therefore safer
-- when the view definition evolves over time.
DROP VIEW IF EXISTS public_bibliography;

-- ⚠ ACCEPTED ADVISORY — do NOT set security_invoker = on here.
-- The Supabase linter flags this as a SECURITY DEFINER view (security_invoker
-- OFF / default). That is INTENTIONAL and load-bearing: `anon` has no grant on
-- bibliography_records and there are no RLS policies on it, so the view runs
-- with the owner's privileges to expose a curated, WHERE-filtered, read-only
-- projection while the base table stays fully private. The WHERE clause below
-- IS the public access policy; there is no per-user RLS to "bypass" (every
-- anonymous reader gets the same public set) and `anon` holds SELECT-only.
-- Turning security_invoker ON would make the view run as the caller and return
-- ZERO rows (or require exposing the base table directly). Rationale + the two
-- rejected alternatives are documented in docs/RUNBOOK-database-security.md §7.
CREATE VIEW public_bibliography AS
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
-- ── Sanitization gates (applied in conjunction, ANDed together) ─────────
--
-- (A) Relevance decision: keep records that explicitly passed the relevance
--     filter, that came from a trusted seed source (NULL = pre-filter), or
--     that carry an editorial override. Index-friendly form of the original
--     COALESCE wrapper (see header note for context).
WHERE
  (
    tnbc_relevance_decision IS NULL
    OR tnbc_relevance_decision IN (
      'trusted_source', 'keep_strong', 'keep_moderate', 'keep_manual'
    )
  )
  -- (B) Year window: drops obvious source-database date errors (some PubMed
  --     records carry 1922 or other pre-1985 years for what are clearly more
  --     recent publications). Ceiling is the current year: future-year
  --     records (e.g. a 2027 stamp while it is still 2026) are almost always
  --     not-yet-published / in-peer-review and are excluded. NULL years are
  --     kept and bounded instead by the date gate (D) where a date exists.
  AND (
    publication_year BETWEEN 1985 AND extract(year from now())::int
    OR publication_year IS NULL  -- a few records lack a clean year; keep them
  )
  -- (D) Future-date gate: exclude any record whose publication_date is after
  --     today. Catches in-press / ahead-of-print records that carry a forward
  --     issue date (some journals stamp an upcoming issue month). A NULL date
  --     is kept — absence of a date is not evidence of a future one, and such
  --     records remain bounded by the year window in (B).
  AND (
    publication_date IS NULL
    OR publication_date <= (now() AT TIME ZONE 'UTC')::date
  )
  -- (C) Topic gate: tiered by relevance signal strength.
  --
  --     keep_strong (TNBC in title) — TRUSTED, gate bypassed. If a paper
  --     has TNBC in its title we treat it as TNBC-relevant even if the
  --     topic-tagger didn't accumulate enough keyword hits for any single
  --     domain (e.g. editorials, very-short-abstract papers, papers
  --     covering topics adjacent to the 10-domain taxonomy).
  --
  --     keep_moderate / trusted_source / NULL — REQUIRE topic-tag
  --     confirmation, OR tier assignment, OR editorial manual-keep. The
  --     relevance signal here is softer (TNBC in abstract, basal-like in
  --     title, etc.), so the topic-tagger's confirmation acts as the
  --     precision check that filters out residual broad-query noise.
  AND (
    tnbc_relevance_decision = 'keep_strong'
    OR tnbc_relevance_decision = 'keep_manual'
    OR tier IS NOT NULL
    OR (topic_tags IS NOT NULL AND array_length(topic_tags, 1) >= 1)
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
-- Statement timeout
-- ────────────────────────────────────────────────────────────────────────
-- Supabase defaults the anon role to a 3-second statement_timeout, which
-- is tight for any aggregate over a growing table. `SELECT count(*) FROM
-- public_bibliography` with `Prefer: count=exact` hits this ceiling once
-- the table is more than a few thousand rows because count(*) inherently
-- needs to touch every row that passes the WHERE clause — no index can
-- short-circuit that. Raise to 15 seconds for the anon role only; the
-- service-role used by the harvest pipeline keeps its own (much higher)
-- default.
--
-- The website should still prefer `Prefer: count=estimated` for any
-- "how many records?" surface (instant, ±a few percent); this raise
-- exists so ad-hoc verification and admin queries don't 500 on the
-- exact-count path.
ALTER ROLE anon SET statement_timeout = '15s';

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
--
-- The predicate is the index-friendly equivalent of the original
-- `COALESCE(tnbc_relevance_decision, 'trusted_source') != 'drop'` form. The
-- planner can only use a partial index when the query's WHERE clause
-- matches (or proves implies) the index's predicate; writing both in the
-- same sargable IS NULL/OR form makes the match explicit and avoids
-- relying on the planner's proof system.
DROP INDEX IF EXISTS idx_records_recent_cited;
CREATE INDEX idx_records_recent_cited
  ON bibliography_records (publication_year DESC, citation_count DESC NULLS LAST)
  WHERE retraction_status = 'active'
    AND (tnbc_relevance_decision IS NULL OR tnbc_relevance_decision != 'drop');
