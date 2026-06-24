-- TNBC Atlas — Database security hardening
--
-- Closes the gaps found in the 2026-06-21 security audit:
--   • public_bibliography view granted anon INSERT/UPDATE/DELETE and is
--     auto-updatable with security_invoker OFF → anon could modify/delete the
--     base table THROUGH the view, bypassing base-table RLS.   [Phase 0]
--   • A blanket GRANT ... TO anon/authenticated on the whole public schema
--     gave the public roles full DML on every table.            [Phase 1]
--   • RLS was disabled on ~11 non-TNBC (B2B app) tables that share this
--     project; empty today but a breach the moment they hold data. [Phase 2]
--
-- APPLY ORDER: after 03_supabase_public_api.sql. Idempotent and re-runnable.
--
-- ⚠ Phase 0 has ALREADY been applied to the live DB (2026-06-21). It is
--   included here so the file is a complete, reproducible source of truth.
--   Phases 1–2 are NOT yet applied — review, then apply together.
--
-- Design note: we deliberately KEEP public_bibliography as a security-definer
-- view (security_invoker OFF). anon is granted SELECT on the VIEW only and has
-- no access to bibliography_records; the view's owner-privilege execution is
-- exactly what lets the public read a filtered projection without touching the
-- RLS-protected base table. Turning security_invoker ON would break public
-- reads (anon has, and should have, no base-table grant).

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 0 — Lock the public view to read-only  (ALREADY APPLIED 2026-06-21)
-- ════════════════════════════════════════════════════════════════════════
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public_bibliography FROM anon, authenticated;

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 1 — Least privilege: drop blanket grants, re-grant only what's needed
-- ════════════════════════════════════════════════════════════════════════
-- Remove the blanket DML the public roles currently hold on every public
-- table. This also strips the (harmless, RLS-blocked) grants on the TNBC base
-- tables — intended; nothing legitimate uses them.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;

-- Re-grant ONLY the public read surface: the filtered bibliography view.
GRANT SELECT ON public_bibliography TO anon, authenticated;

-- Stop new tables from auto-granting to the public roles in the future.
-- (Covers objects created by the role running migrations; if other roles
--  create tables, repeat ALTER DEFAULT PRIVILEGES FOR ROLE <role>.)
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 2 — Enable RLS on every public table (defense in depth)
-- ════════════════════════════════════════════════════════════════════════
-- With RLS on + no policies + no grants, anon/authenticated see nothing. The
-- service-role key (used by the harvest pipeline and any app backend) BYPASSES
-- RLS, so server-side writes keep working. This loop is future-proof: it
-- enables RLS on any current or future base table in `public` that lacks it.
DO $$
DECLARE t regclass;
BEGIN
  FOR t IN
    SELECT c.oid::regclass
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity = false
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY;', t);
    RAISE NOTICE 'RLS enabled on %', t;
  END LOOP;
END $$;

-- Tables this currently covers (all non-TNBC B2B app, RLS was OFF):
--   api_keys, audit_log, b2b_customers, business_numbers, businesses,
--   ingest_events, phone_numbers, reporters, reports, reputation_runs,
--   scrub_jobs
-- (The four TNBC tables already had RLS enabled in 03_supabase_public_api.sql.)

-- ════════════════════════════════════════════════════════════════════════
-- PHASE 3 — Non-TNBC (B2B) tables: secured here as a STOPGAP only
-- ════════════════════════════════════════════════════════════════════════
-- Phases 1–2 lock these tables to the public key. They remain in this project
-- for now, but the plan is to MIGRATE them to their own Supabase project so a
-- compromise of one app cannot reach the other. See
-- docs/RUNBOOK-database-security.md §"B2B separation". Until migrated, their
-- backend MUST use the service-role key (never the anon key) for writes, and
-- should define explicit policies for any authenticated access path.

-- ════════════════════════════════════════════════════════════════════════
-- Verification (run after applying; all should hold)
-- ════════════════════════════════════════════════════════════════════════
-- 1) anon has SELECT on the view and nothing else:
--      SELECT grantee, table_name, string_agg(privilege_type, ',')
--      FROM information_schema.role_table_grants
--      WHERE table_schema='public' AND grantee IN ('anon','authenticated')
--      GROUP BY 1,2 ORDER BY 2,1;
--    Expect: only public_bibliography → SELECT.
-- 2) Every public table has RLS on:
--      SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
--      WHERE n.nspname='public' AND c.relkind='r' AND c.relrowsecurity=false;
--    Expect: zero rows.
-- 3) From the anon key (PostgREST): GET /rest/v1/api_keys and
--    DELETE /rest/v1/public_bibliography both return permission denied; GET
--    /rest/v1/public_bibliography still returns rows.
