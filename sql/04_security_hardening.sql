-- TNBC Atlas — Database security remediation
--
-- Closes the gaps from the 2026-06-21 security audit. ALL STEPS BELOW HAVE
-- BEEN APPLIED to the live DB (project euuhbwgnflhbjtkrgjki) on 2026-06-21.
-- The file is the reproducible source of truth. Idempotent / re-runnable.
--
-- Audit findings and resolution:
--   • public_bibliography view granted anon INSERT/UPDATE/DELETE and is
--     auto-updatable with security_invoker OFF → anon could modify/delete the
--     base table THROUGH the view, bypassing base-table RLS.   → Phase 0
--   • A blanket GRANT ... TO anon/authenticated on the whole public schema
--     gave the public roles full DML on every table.            → Phase 1
--   • ~11 non-TNBC tables (a B2B app) had RLS off + full anon grants. They
--     were EMPTY (0 rows) design-phase overlap; the B2B app's canonical
--     schema/data lives in a SEPARATE Supabase project. Resolution: DROP
--     them here rather than secure-in-place.                    → Drop block
--
-- Design note: public_bibliography stays a security-definer view
-- (security_invoker OFF). anon holds SELECT on the VIEW only and has no
-- base-table grant; the owner-privilege execution is exactly what exposes a
-- safe, filtered, read-only projection. security_invoker ON would break reads.

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 0 — Lock the public view to read-only
-- ════════════════════════════════════════════════════════════════════════
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public_bibliography FROM anon, authenticated;

-- ════════════════════════════════════════════════════════════════════════
-- DROP — Remove the empty B2B design-overlap tables
-- ════════════════════════════════════════════════════════════════════════
-- Verified 0 rows each immediately before dropping; FKs were internal to the
-- set; nothing in TNBC (or any view/function) depended on them. The live B2B
-- app is a separate Supabase project that owns the canonical schema.
DROP TABLE IF EXISTS
  public.api_keys,
  public.audit_log,
  public.b2b_customers,
  public.business_numbers,
  public.businesses,
  public.ingest_events,
  public.phone_numbers,
  public.reporters,
  public.reports,
  public.reputation_runs,
  public.scrub_jobs
CASCADE;

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 1 — Least privilege: drop blanket grants, re-grant only the read view
-- ════════════════════════════════════════════════════════════════════════
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
GRANT SELECT ON public_bibliography TO anon, authenticated;

-- Stop future tables/sequences/functions from auto-granting to public roles.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

-- ════════════════════════════════════════════════════════════════════════
-- RLS — no separate phase needed
-- ════════════════════════════════════════════════════════════════════════
-- After the drop, the only remaining public base tables are the four TNBC
-- tables, all of which already have RLS enabled (03_supabase_public_api.sql).
-- Confirm zero public tables lack RLS:
--   SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
--   WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity=false;  -- expect 0 rows

-- ════════════════════════════════════════════════════════════════════════
-- Verification (post-apply, all confirmed 2026-06-21)
-- ════════════════════════════════════════════════════════════════════════
-- • Remaining public objects: bibliography_records, dedup_decisions,
--   harvest_runs, raw_snapshots (all RLS=true) + public_bibliography (view).
-- • anon/authenticated grants: ONLY public_bibliography → SELECT.
-- • Public tables with RLS off: 0.
-- • anon SELECT on public_bibliography still returns rows (read path intact).
-- • From the anon key (PostgREST): GET /rest/v1/api_keys → 404/not-exposed;
--   DELETE /rest/v1/public_bibliography → permission denied; GET on the view → rows.
