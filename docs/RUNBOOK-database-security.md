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
| **High (structural)** | ~11 non-TNBC tables (`api_keys`, `audit_log`, `b2b_customers`, `business_numbers`, `businesses`, `ingest_events`, `phone_numbers`, `reporters`, `reports`, `reputation_runs`, `scrub_jobs`) have RLS **off** and full `anon`/`authenticated` DML grants. **Empty (0 rows) today** — no data leaked yet, but a public breach the moment they hold data (esp. `api_keys`, `phone_numbers`, `b2b_customers`). | Pending (Phases 1–2) |
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

### Phase 1 — Least privilege (pending review)
Drop the blanket grants, re-grant only the public read surface, and stop future tables from auto-granting. See `sql/04_security_hardening.sql` Phase 1.

### Phase 2 — RLS on every public table (pending review)
A future-proof `DO` loop enables RLS on any `public` base table that lacks it. No policies + no grants = locked to anon/authenticated; the service role still writes. See `sql/04` Phase 2.

### Apply Phases 1–2
```bash
# load creds (never echo), then apply
python3 - <<'PY'
import re, psycopg2
env={}
[env.__setitem__(*re.match(r'\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)$', l).groups())
 for l in open("../.secrets/cloud.env") if re.match(r'\s*(?:export\s+)?[A-Za-z_]', l)]
url=env["DATABASE_URL"].strip().strip('"').strip("'")
c=psycopg2.connect(url); c.autocommit=True
c.cursor().execute(open("sql/04_security_hardening.sql").read())
print("applied")
PY
```
Then run the three verification checks at the bottom of `sql/04`.

### Phase 5 — Verify, then re-check the Security Advisor
- `anon`/`authenticated` should have **only** `SELECT` on `public_bibliography`.
- Every `public` table reports `relrowsecurity = true`.
- From the anon key: `GET /rest/v1/api_keys` and `DELETE /rest/v1/public_bibliography` → denied; `GET /rest/v1/public_bibliography` → rows.
- Supabase → Advisors → Security: the RLS warning clears.

## 4. B2B separation (chosen direction)

The non-TNBC tables will be **migrated to their own Supabase project** so the two apps cannot expose each other. Phases 1–2 are the stopgap until then. Migration outline:
1. Stand up a new Supabase project for the B2B app.
2. `pg_dump` the B2B tables (schema + data) from this project; restore into the new one.
3. Repoint the B2B app's `DATABASE_URL` / API keys to the new project.
4. Apply the same hardening (RLS + least privilege) there from day one.
5. Once cut over and verified, `DROP` the B2B tables from this project.
6. Until then: the B2B backend must use the **service-role** key (never anon) for writes.

## 5. Operational hygiene (follow-ups)
- Confirm the harvest pipeline and any app backend use the **service-role** key for writes, not anon.
- Also audit `anon`/`authenticated` grants on **sequences** and **functions** (this runbook covers tables; `sql/04` revokes default privileges on all three going forward).
- Dashboard: enable MFA; enable leaked-password protection; confirm PITR (also in `PRELAUNCH-CHECKLIST.md`).
- Make the Security Advisor a recurring pre-deploy check.

## 6. Rollback
Phase 0 is non-destructive (a privilege revoke); to undo, re-grant the writes (not recommended). Phases 1–2 only tighten access; if a legitimate path breaks, grant the *specific* missing privilege or add a *specific* RLS policy rather than re-opening blanket access.
