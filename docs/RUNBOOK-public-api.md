# Runbook — Public REST API via Supabase Custom Domain + Cloudflare DNS

Supabase exposes PostgREST automatically from its managed Postgres. We surface it under our own branded hostname `api.tnbc.info` using Supabase's Custom Domain feature (Pro tier). Cloudflare hosts the DNS record. There is no PostgREST process to host and no separate API server to maintain.

> **Why Custom Domain rather than a plain Cloudflare CNAME?**
> The earlier version of this runbook described pointing `api.tnbc.info` at `<project-ref>.supabase.co` with the Cloudflare proxy turned on (orange cloud). That configuration triggers Cloudflare **error 1014 ("CNAME cross-user banned")** because Supabase's hostnames sit behind a different Cloudflare account and the cross-account proxy isn't authorized. Supabase Custom Domain solves this cleanly: Supabase issues a TLS cert for `api.tnbc.info` itself and gives us a CNAME target that *is* authorized for cross-account proxying.

## Architecture

Recommended configuration during closed beta (DNS-only, grey cloud):

```
        ┌─────────────────────────────────────┐
        │  api.tnbc.info                      │
        │  Cloudflare DNS only (grey cloud)   │
        └─────────────────┬───────────────────┘
                          │  CNAME, not proxied
                          ▼
        ┌─────────────────────────────────────┐
        │  Supabase Custom Domain edge        │
        │  TLS cert: api.tnbc.info            │
        │  PostgREST auto-served by Supabase  │
        └─────────────────┬───────────────────┘
                          │ enforces SELECT on api_anon role
                          ▼
        ┌─────────────────────────────────────┐
        │  Supabase Postgres                  │
        │  public_bibliography view           │
        └─────────────────────────────────────┘
```

Optional configuration for public launch (Cloudflare proxied, orange cloud — requires Cloudflare Pro for rate limiting and SSL/TLS mode set to Full (strict)):

```
        ┌─────────────────────────────────────┐
        │  api.tnbc.info                      │
        │  Cloudflare proxied (orange cloud)  │
        │  + edge cache + WAF + rate limit    │
        │  SSL/TLS mode: Full (strict)        │
        └─────────────────┬───────────────────┘
                          │ HTTPS (CF re-validates Supabase cert)
                          ▼
        Supabase Custom Domain edge → Supabase Postgres
```

The Supabase layer gives us in both configurations:
- Managed PostgREST, generated automatically from the schema.
- TLS termination at `api.tnbc.info`.
- Built-in OpenAPI spec at the project's API URL.
- API key infrastructure (anon key, service-role key).

Cloudflare gives us only in orange-cloud mode:
- Edge cache (so the same query doesn't hit Supabase repeatedly).
- WAF and rate limiting at the IP level (Pro tier).
- DDoS mitigation.

In grey-cloud mode, Cloudflare is doing DNS resolution only; requests bypass the Cloudflare edge entirely and terminate at Supabase.

## Step 1 — Apply the public API schema

The view and role are defined in `sql/03_supabase_public_api.sql`. Apply it once during initial setup (already covered in `RUNBOOK-production-db.md` Step 3):

```bash
psql "$DATABASE_URL" -f sql/03_supabase_public_api.sql
```

Verify in Supabase **Table Editor → Views**: you should see `public_bibliography`.

Verify in **Authentication → Roles**: `api_anon` role exists with `SELECT` on `public_bibliography` only.

## Step 2 — Test the Supabase-native URL

Before adding the custom domain, sanity-check that the API works through Supabase's built-in URL. Supabase exposes the REST API at `https://<project-ref>.supabase.co/rest/v1/`:

```bash
# The anon JWT key is in Supabase: Settings → API → anon (public) key
ANON_KEY="<paste-from-supabase-dashboard>"

# Fetch one row — confirms the view exists, anon has SELECT, and the
# data path is healthy. Should be sub-second.
curl -i "https://<project-ref>.supabase.co/rest/v1/public_bibliography?limit=1" \
  -H "apikey: ${ANON_KEY}" \
  -H "Authorization: Bearer ${ANON_KEY}"
```

Expected: HTTP 200, `content-type: application/json`, body is a single-element JSON array. If you get a 401, the anon key is wrong; if you get a 403, the anon role lacks SELECT on the view (see `sql/03_supabase_public_api.sql`); if you get a 404, the view wasn't created.

> **Don't use `?select=count` with `Prefer: count=exact` for this probe.** The count aggregate must visit every row matching the view's WHERE clause and is the slowest possible thing to ask the database for. See the "When counts time out" section under Step 3 for the dedicated count-friendly verify pattern.

## Step 3 — Provision the Supabase Custom Domain

**Requires Supabase Pro (~$25/month).** Custom domains aren't available on the Free tier.

In the Supabase dashboard for the project:

1. **Settings → Custom Domains**.
2. Click **Add custom domain**, enter `api.tnbc.info`, and submit.
3. Supabase displays a CNAME verification target — something like `<custom-id>.<project-ref>.supabase.co` or `<custom-id>.cname.supabase.com`. Copy this exact value.

Then in the Cloudflare dashboard for the `tnbc.info` zone:

4. **DNS → Records → Add record**.
5. Type: `CNAME`.
6. Name: `api`.
7. Target: paste the Supabase-provided CNAME target from step 3.
8. Proxy status: **DNS only** (grey cloud). This is required for the initial verification — Supabase needs to see the CNAME resolve directly to their edge to issue the TLS cert.
9. TTL: Auto.

Back in Supabase:

10. Click **Verify**. Supabase resolves the CNAME, confirms the chain, and issues a Let's Encrypt cert for `api.tnbc.info`. This usually completes within 1–2 minutes; refresh the page if it doesn't update immediately.

Verify the custom domain end-to-end. Use this **two-step probe** — the first call checks that the API is reachable and the view returns real data; the second checks that the count infrastructure works without paying the full aggregate cost:

```bash
# Probe 1: fetch one row through the custom domain. Should return HTTP 200
# with a JSON array containing one full record, in well under a second.
curl -i "https://api.tnbc.info/rest/v1/public_bibliography?limit=1" \
  -H "apikey: ${ANON_KEY}"

# Probe 2: get an estimated row count via the content-range header.
# `count=estimated` uses Postgres's pg_class.reltuples (no scan) and is
# instant; `count=exact` would force a full aggregate over the view's
# WHERE clause, which is expensive on growing tables.
curl -I "https://api.tnbc.info/rest/v1/public_bibliography?limit=1" \
  -H "apikey: ${ANON_KEY}" \
  -H "Prefer: count=estimated"
```

Expected response shapes:

- **Probe 1**: `HTTP/2 200`, `content-type: application/json`, body is a single-element JSON array with all 31 view columns populated. `x-envoy-upstream-service-time` under 500ms (typically ~100–200ms once the connection is warm).
- **Probe 2**: `HTTP/2 206`, `content-range: 0-0/~14319` (the `~` prefix signals estimated). Sub-second.

If you see a TLS error, DNS propagation may not be complete yet (give it another minute). If you see Cloudflare error 1014, the CNAME target is wrong — confirm it's pointed at the Supabase custom-domain target and not at the bare `<project-ref>.supabase.co`. If you see HTTP 500 with `code: 57014` ("canceling statement due to statement timeout"), the schema-fix migrations haven't been applied yet — see the "When counts time out" troubleshooting section below.

### When counts time out

Symptom: a verify request with `Prefer: count=exact` (or any aggregate over the full view) returns HTTP 500 with body `{"code":"57014","message":"canceling statement due to statement timeout"}`. Two compounding causes, both addressed in the SQL migrations:

1. **The original view's WHERE clause wrapped `tnbc_relevance_decision` in `COALESCE(...)`, defeating the index.** Postgres can't use a regular column index when the column is wrapped in a function call, so the planner fell back to a sequential scan. The view in `sql/03_supabase_public_api.sql` now uses an equivalent `IS NULL OR ... IN (...)` form that the planner can map to index lookups; the partial index in `sql/02b_quality_passes_migration.sql` was also rebuilt without its `WHERE tnbc_relevance_decision IS NOT NULL` clause so NULL rows are covered too.
2. **Supabase's default anon `statement_timeout` is 3 seconds**, which is genuinely tight for any aggregate over a multi-thousand-row table — even with the right indexes, `count(*)` must visit every row that passes the predicate, and no index can short-circuit that work. `sql/03_supabase_public_api.sql` raises the anon timeout to 15 seconds with `ALTER ROLE anon SET statement_timeout = '15s';`.

If you hit this on a fresh Supabase project, reapply both SQL files in order:

```bash
psql "$DATABASE_URL" -f sql/02b_quality_passes_migration.sql
psql "$DATABASE_URL" -f sql/03_supabase_public_api.sql
```

Then rerun Probe 2 above. Even with both fixes, **the website should still use `Prefer: count=estimated` for any visible "X records" badge** — it's instant, accurate to within a few percent, and avoids the count-aggregate path entirely. Reserve `count=exact` for admin queries and ad-hoc verification where the small extra latency is acceptable.

## Step 4 — Cloudflare edge configuration (optional, orange-cloud mode only)

During closed beta, leaving the CNAME as DNS-only (grey cloud) is the simpler and recommended path. Everything in this section is only relevant if you later flip the record to **Proxied** (orange cloud) for edge caching, WAF, and rate limiting.

### Prerequisite: SSL/TLS mode

Before turning the cloud orange, set Cloudflare's SSL/TLS mode for the `tnbc.info` zone to **Full (strict)**:

**SSL/TLS → Overview → Edit → Full (strict)**

This tells Cloudflare to validate the origin certificate (the one Supabase issued for `api.tnbc.info`) end-to-end. Without it you risk certificate-mismatch errors when Cloudflare terminates TLS at its edge and re-establishes TLS to Supabase.

### Caching

Cache settings live under their own rules type (not under Configuration Rules — that controls zone-level toggles like SSL mode). Navigate to:

**Caching → Cache Rules → Create rule**

(Equivalent path: **Rules → Cache Rules** in some account layouts.)

- **Rule name**: `Cache PostgREST GETs`
- **If…**: Hostname *equals* `api.tnbc.info` AND URI Path *contains* `/rest/v1/public_bibliography`
- **Then…**:
  - Eligible for cache: ✓
  - Edge TTL: **Override origin** (also labelled "Ignore cache-control header and use this TTL")
  - Override TTL: **5 minutes** (300 seconds)
  - Browser TTL: leave at default ("Respect origin")
- Save and deploy.

Verify with two `curl -I` requests in quick succession; the second should show `cf-cache-status: HIT` in the response headers.

Rationale: the bibliography updates weekly; serving the same query from edge cache for up to 5 minutes is harmless and dramatically reduces hits to Supabase.

### Rate limiting

**Requires Cloudflare Pro (~$25/month).** The Free plan no longer includes dedicated WAF Rate Limiting Rules (the 10k-requests/month legacy feature was deprecated). Free-tier alternatives:

1. **Defer until you upgrade to Pro at public launch.** During closed-beta the site is behind Cloudflare Access; nothing is exposed to abuse. This is the recommended path.
2. **Implement rate limiting in a Cloudflare Worker** (free tier covers 100k requests/day). About 30 lines of TypeScript intercepting requests to `api.tnbc.info`, tracking per-IP counts in Workers KV, returning HTTP 429 when limits are exceeded.
3. **Rely on Supabase's connection-pool ceiling** as soft rate-limiting. Abusive clients get queued or 503'd at the database level. Imperfect but provides a backstop.

On **Cloudflare Pro**, the path is **Security → Security rules → Rate limiting rules**:

- **Rule name**: `API rate limit`
- **If…**: Hostname *equals* `api.tnbc.info` AND URI Path *starts with* `/rest/v1/`
- **When rate exceeds**: 60 requests in 1 minute (counting by IP address)
- **Action**: Block, duration 1 minute, HTTP 429 with `Retry-After: 60`

(The website's own JS issues at most ~10 requests per page load, well below the limit.)

### Hide the anon key (optional, recommended before public launch)

The Supabase anon key is intended to be public-visible, but exposing it at all means a malicious user could send unlimited queries from anywhere. To enforce that all access goes through Cloudflare (and is thus subject to rate limiting):

1. In Supabase **Settings → API**, you can't disable the anon key entirely, but you can rotate it.
2. Set up a small Cloudflare Worker that injects the anon key from a Cloudflare secret before forwarding to Supabase. Then site JS only knows about `api.tnbc.info` and never sees the raw anon key.

This is a security hardening optional for the closed-beta phase; recommended before public launch. Documented as a separate task in `PRELAUNCH-CHECKLIST.md`.

## Step 5 — Bulk-export endpoints on R2

For users who want the full corpus rather than paginated API calls, the GitHub Actions harvest workflow uploads the full export files to a Cloudflare R2 bucket. Surface them at `exports.tnbc.info`:

1. **DNS → Add record**: CNAME `exports.tnbc.info` → R2 bucket public URL, proxied (orange cloud works fine for R2; the 1014 issue doesn't apply because R2 is on the same Cloudflare account as the `tnbc.info` zone).
2. R2 bucket: `tnbc-atlas-exports`, public-read on the `/latest/` prefix.
3. Files refresh nightly via the harvest workflow's final step.

Final URLs:
- `https://exports.tnbc.info/latest/bibliography.csv`
- `https://exports.tnbc.info/latest/bibliography.jsonl`
- `https://exports.tnbc.info/latest/bibliography.bib`
- `https://exports.tnbc.info/latest/bibliography.ris`

Link these from the website's `/research/api/` page.

## Step 6 — OpenAPI consumption

PostgREST's OpenAPI 3 spec is served automatically:

```bash
curl https://api.tnbc.info/rest/v1/ -H "apikey: ${ANON_KEY}" | jq .info
```

Client libraries are auto-generatable:

```bash
# Python
openapi-python-client generate --url https://api.tnbc.info/rest/v1/

# TypeScript
openapi-typescript https://api.tnbc.info/rest/v1/ -o api.d.ts
```

Document this generation step on the website's `/research/api/` page so researchers don't have to figure it out themselves.

## Step 7 — Monitoring

Three layers (the third only applies in orange-cloud mode):

1. **Supabase dashboard → API → Logs**: per-request log of all PostgREST traffic. Watch for 5xx rates and slow queries. Works in both grey- and orange-cloud modes.
2. **External uptime monitor**: UptimeRobot or similar, GETting `https://api.tnbc.info/rest/v1/public_bibliography?limit=1&apikey=...` every 60 seconds. Alert on 5xx or no-response. Works in both modes.
3. **Cloudflare Analytics → Traffic**: edge-level request volume, cache hit rate, country distribution. Only useful when the CNAME is proxied (orange cloud); in DNS-only mode the request never touches Cloudflare's edge so there's nothing to measure.

## Querying examples

PostgREST's query syntax is documented at <https://postgrest.org/en/stable/api.html>. Highlights:

```bash
# Filter by year, select specific fields, order by citations descending, limit 10
curl "https://api.tnbc.info/rest/v1/public_bibliography?publication_year=eq.2025&select=title,doi,citation_count&order=citation_count.desc&limit=10" \
  -H "apikey: ${ANON_KEY}"

# Full-text-like search on title
curl "https://api.tnbc.info/rest/v1/public_bibliography?title=ilike.*sacituzumab*&select=title,doi" \
  -H "apikey: ${ANON_KEY}"

# Pagination via Range header
curl "https://api.tnbc.info/rest/v1/public_bibliography?select=title,year&order=publication_year.desc" \
  -H "apikey: ${ANON_KEY}" \
  -H "Range: 0-49"

# Count — fast (estimated, from pg_class.reltuples; sub-millisecond)
curl -I "https://api.tnbc.info/rest/v1/public_bibliography?limit=1" \
  -H "apikey: ${ANON_KEY}" \
  -H "Prefer: count=estimated"

# Count — exact (full aggregate; only use for admin / ad-hoc queries —
# the anon role's statement_timeout is raised to 15s to accommodate this
# path, but the website should never rely on it)
curl "https://api.tnbc.info/rest/v1/public_bibliography?select=count" \
  -H "apikey: ${ANON_KEY}" \
  -H "Prefer: count=exact"
```

## Cost

- **Supabase Pro**: ~$25/month. Required for the Custom Domain feature; also unlocks daily backups with 7-day point-in-time recovery, larger database size (8 GB), and the no-week-of-inactivity-pause guarantee.
- **Cloudflare Free**: DNS, R2 storage and egress, and grey-cloud DNS-only for `api.tnbc.info` are all $0.
- **Cloudflare Pro (optional, ~$25/month)**: only needed if you want orange-cloud proxying with edge cache, WAF, and Rate Limiting Rules on `api.tnbc.info`. Decision point lives in `PRELAUNCH-CHECKLIST.md`.
- **R2 storage**: 10 GB free; bibliography exports total < 100 MB, well within free tier.
- **R2 egress**: 1 million reads/month free, which is far more than expected use.

Net cost at closed-beta launch: **~$25/month** (Supabase Pro only). At public launch with full edge protection: **~$50/month** (Supabase Pro + Cloudflare Pro).

## Checklist

- [ ] `sql/03_supabase_public_api.sql` applied to the Supabase project
- [ ] `public_bibliography` view visible in Supabase Table Editor
- [ ] `api_anon` role created with `SELECT` only on the public view
- [ ] Supabase project upgraded to Pro (required for Custom Domain)
- [ ] Custom domain `api.tnbc.info` added in Supabase **Settings → Custom Domains** and verified
- [ ] DNS CNAME `api.tnbc.info` → Supabase-provided custom-domain target, **DNS only** (grey cloud) at Cloudflare
- [ ] `curl -i https://api.tnbc.info/rest/v1/public_bibliography?limit=1` returns HTTP 200 with a real record (Probe 1 from Step 3) — no TLS error, no 1014, sub-second
- [ ] `curl -I https://api.tnbc.info/rest/v1/public_bibliography?limit=1` with `Prefer: count=estimated` returns HTTP 206 with a `content-range` header showing the estimated total (Probe 2 from Step 3)
- [ ] R2 bucket `tnbc-atlas-exports` created with public-read on `/latest/`
- [ ] DNS CNAME `exports.tnbc.info` → R2 public URL (proxied — orange cloud OK here)
- [ ] GitHub Actions harvest workflow successfully uploads to R2 on completion
- [ ] OpenAPI spec available at `https://api.tnbc.info/rest/v1/`
- [ ] Sample queries documented on the website's `/research/api/` page
- [ ] External uptime monitor configured with alerting destination

Items below are deferred to `PRELAUNCH-CHECKLIST.md` as they require Cloudflare Pro and/or a public-launch posture:

- [ ] (Pre-launch) Flip `api.tnbc.info` to proxied (orange cloud) with SSL/TLS mode Full (strict)
- [ ] (Pre-launch) Edge cache rule for `/rest/v1/public_bibliography` (5-min TTL)
- [ ] (Pre-launch) Rate-limit rule on `/rest/v1/*` (60/min/IP)
- [ ] (Pre-launch) Cloudflare Worker proxy layer to hide the anon key from client JS
