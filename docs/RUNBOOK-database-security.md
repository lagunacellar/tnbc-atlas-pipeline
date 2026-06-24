# Runbook — Database security hardening

**Created:** 2026-06-21
**Trigger:** Supabase Security Advisor flagged a public table with RLS disabled.
**Audit basis:** live introspection of project `euuhbwgnflhbjtkrgjki` on 2026-06-21.
**Migration:** `sql/04_security_hardening.sql`

---

## 1. What the audit found

| Severity | Finding | State |
|---|---|---|
| **Critical (live)** | `public_bibliography` view granted `anon` `INSERT/UPDATE/DELETE`; view is auto-updatable and `security_invoker` is OFF → anon could modify/delete `bibliography_records` *through the view*, bypassing base-table RLS. | **Fixed (Phase 0, 2026-06-21)** |
| **High (structural)** | ~11 non-TNBC tables (`api_keys`, `audit_log`, `b2b_customers`, `business_numbers`, `businesses`, `ingest_events`, `phone_numbers`, `reporters`, `reports`, `reputation_runs`, `scrub_jobs`) had RLS **off** and full `anon`/`authenticated` DML grants. Confirmed **empty (0 rows)** design-phase overlap; the B2B app's canonical schema/data lives in a **separate Supabase project**. | **Resolved — DROPPED 2026-06-21** |
| Good | The four TNBC base tables (`bibliography_records`, `raw_snapshots`, `harvest_runs`, `dedup_decisions`) have RLS enabled, no policies → anon cannot read them. | OK (set in `03_*.sql`) |
| Root cause | A blanket `GRANT ... TO anon, authenticated` on the whole `public` schema gave the public roles full DML on every table. RLS is the only barrier, and it was off on most tables. | Addressed in Phase 1 |

Two distinct applications share this one Supabase project: the **TNBC Atlas** and a **B2B reputation / phone-scrubbing** app (the second group of tables). That shared-project arrangement is itself a blast-radius risk (see §4).

## 2. Mental model (why this is the right fix)

In Supabase, the **`anon`** role is the identity behind the *public* API key, which is embedded in client apps by design. It is safe **only** when two things hold for every reachable object: (a) the role has no privileges it shouldn't, and (b) Row-Level Security gates every row. RLS is the load-bearing control; grants are the secondary one. We restore both: least-privilege grants (Phase 1) **and** RLS everywhere (Phase 2). The **service-role** key bypasses RLS and is what server-side code (the harvest pipeline, any app backend) uses for writes — it must never be shipped to a browser.

`public_bibliography` stays a **security-definer** view (`security_invoker` OFF) on purpose: anon holds `SELECT` on the *view only* and nothing on the base table, so the view's owner-privilege execution is exactly what exposes a safe, filtered, read-only projection. Turning `security_invoker` ON would break public reads.

## 3. Implementation steps

### Phase 0 — Contain the live hole — ✅ DONE 2026-06-21
```sql
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON public_bibliography FROM anon, authenticated;
```
Verified: `anon` keeps `SELECT` (reads return rows), `INSERT/UPDATE/DELETE` denied, `DELETE` via PostgREST returns "permission denied for view." Zero site impact (the site only reads).

### Drop the B2B overlap — ✅ DONE 2026-06-21
Re-confirmed 0 rows on all 11 tables, then `DROP TABLE ... CASCADE` (FKs were internal to the set; nothing external depended on them). See `sql/04` Drop block.

### Phase 1 — Least privilege — ✅ DONE 2026-06-21
`REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated`, re-`GRANT SELECT ON public_bibliography`, and `ALTER DEFAULT PRIVILEGES` to stop future auto-grants. See `sql/04` Phase 1.

### RLS — no separate phase needed
After the drop, the only remaining public base tables are the four TNBC tables, which already have RLS enabled (`03_*.sql`). Verified: zero public tables lack RLS.

### Verify, then re-check the Security Advisor
Confirmed 2026-06-21:
- `anon`/`authenticated` have **only** `SELECT` on `public_bibliography`.
- Every remaining `public` table reports `relrowsecurity = true`; **0** tables without RLS.
- `anon` read of the view still returns rows (101,649).
- **Action remaining for you:** Supabase → Advisors → Security — confirm the RLS warning has cleared.

## 4. B2B separation — resolved by dropping the overlap

The B2B app already has its **own Supabase project** holding the canonical schema and data. The 11 tables here were empty design-phase overlap, so they were **dropped** from the TNBC project (2026-06-21) rather than migrated — this removes the cross-app attack surface entirely. No further separation work is needed on the TNBC side. (If those table *names* are ever recreated here by accident, the `ALTER DEFAULT PRIVILEGES` from Phase 1 ensures they won't auto-expose to the public roles, but they'd still need RLS — keep them out of this project.)

## 5. Operational hygiene (follow-ups)
- Confirm the harvest pipeline and any app backend use the **service-role** key for writes, not anon.
- Also audit `anon`/`authenticated` grants on **sequences** and **functions** (this runbook covers tables; `sql/04` revokes default privileges on all three going forward).
- Dashboard: enable MFA; enable leaked-password protection; confirm PITR (also in `PRELAUNCH-CHECKLIST.md`).
- Make the Security Advisor a recurring pre-deploy check.

## 6. Rollback
Phase 0 is non-destructive (a privilege revoke); to undo, re-grant the writes (not recommended). Phases 1–2 only tighten access; if a legitimate path breaks, grant the *specific* missing privilege or add a *specific* RLS policy rather than re-opening blanket access.
