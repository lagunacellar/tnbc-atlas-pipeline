# Runbook — Public REST API via Supabase + Cloudflare DNS

Supabase exposes PostgREST automatically from its managed Postgres. We configure a public read-only role and view inside Supabase, then point `api.tnbc.info` at the Supabase API URL via Cloudflare DNS proxy. There is no PostgREST process to host and no separate API server to maintain.

## Architecture

```
        ┌─────────────────────────────────────┐
        │  api.tnbc.info                      │
        │  (Cloudflare DNS + edge cache       │
        │   + rate limit + WAF)               │
        └─────────────────┬───────────────────┘
                          │  CNAME proxied (orange cloud)
                          ▼
        ┌─────────────────────────────────────┐
        │  <project-ref>.supabase.co          │
        │  PostgREST auto-served by Supabase  │
        └─────────────────┬───────────────────┘
                          │ enforces SELECT on api_anon role
                          ▼
        ┌─────────────────────────────────────┐
        │  Supabase Postgres                  │
        │  public_bibliography view           │
        └─────────────────────────────────────┘
```

Cloudflare gives us:
- TLS termination at `api.tnbc.info` (Supabase's own URL is also TLS but the friendly hostname matters for permalink stability and DNS branding).
- DDoS protection.
- Edge cache (so the same query doesn't hit Supabase repeatedly).
- Rate limiting at the IP level.

Supabase gives us:
- Managed PostgREST, generated automatically from the schema.
- Built-in OpenAPI spec at the project's API URL.
- API key infrastructure (anon key, service-role key) if we want to require keys later.

## Step 1 — Apply the public API schema

The view and role are defined in `sql/03_supabase_public_api.sql`. Apply it once during initial setup (already covered in `RUNBOOK-production-db.md` Step 3):

```bash
psql "$DATABASE_URL" -f sql/03_supabase_public_api.sql
```

Verify in Supabase **Table Editor → Views**: you should see `public_bibliography`.

Verify in **Authentication → Roles**: `api_anon` role exists with `SELECT` on `public_bibliography` only.

## Step 2 — Test the Supabase-native URL

Supabase exposes the REST API at `https://<project-ref>.supabase.co/rest/v1/`. Without a friendly hostname yet:

```bash
# The anon JWT key is in Supabase: Settings → API → anon (public) key
ANON_KEY="<paste-from-supabase-dashboard>"

curl "https://<project-ref>.supabase.co/rest/v1/public_bibliography?select=count" \
  -H "apikey: ${ANON_KEY}" \
  -H "Authorization: Bearer ${ANON_KEY}"
```

Expected: a JSON response with the row count. If you get a 401, the anon key is wrong; if you get a 404, the view wasn't created.

## Step 3 — Add the friendly hostname via Cloudflare

In the Cloudflare dashboard for the `tnbc.info` zone:

1. **DNS → Add record**.
2. Type: `CNAME`.
3. Name: `api`.
4. Target: `<project-ref>.supabase.co`.
5. Proxy status: **Proxied** (orange cloud).
6. TTL: Auto.

After ~30 seconds DNS propagates. Test:

```bash
curl "https://api.tnbc.info/rest/v1/public_bibliography?select=count" \
  -H "apikey: ${ANON_KEY}"
```

Same response as before, now via the friendly hostname.

## Step 4 — Cloudflare edge configuration

In **Rules → Configuration Rules** (or **Page Rules** on older accounts), add:

### Caching

- **If URL path contains** `/rest/v1/public_bibliography` **AND request method is** `GET`
- **Then** set Edge Cache TTL to 5 minutes (300 seconds).
- Bypass cache on querystring change is the default.

Rationale: the bibliography updates weekly; serving the same query from edge cache for up to 5 minutes is harmless and dramatically reduces hits to Supabase.

### Rate limiting

In **Security → WAF → Rate limiting rules**:

- **Match**: hostname equals `api.tnbc.info` AND path matches `/rest/v1/*`.
- **Limit**: 60 requests per IP per minute, 1,000 requests per IP per hour.
- **Action**: Block with HTTP 429 and `Retry-After: 60`.

Bumps the limits as needed for known good consumers (the website's own JS issues at most ~10 requests per page load; well below the limit).

### Hide the anon key (optional, recommended)

The Supabase anon key is intended to be public-visible, but exposing it at all means a malicious user could send unlimited queries from anywhere. To enforce that all access goes through Cloudflare (and is thus subject to rate limiting):

1. In Supabase **Settings → API**, you can't disable the anon key entirely, but you can rotate it.
2. Set up a small Cloudflare Worker that injects the anon key from a Cloudflare secret before forwarding to Supabase. Then site JS only knows about `api.tnbc.info` and never sees the raw anon key.

This is a security hardening optional for the closed-beta phase; recommended before public launch. Documented as a separate task in the Phase 3 hardening list.

## Step 5 — Bulk-export endpoints on R2

For users who want the full corpus rather than paginated API calls, the GitHub Actions harvest workflow uploads the full export files to a Cloudflare R2 bucket. Surface them at `exports.tnbc.info`:

1. **DNS → Add record**: CNAME `exports.tnbc.info` → R2 bucket public URL, proxied.
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

Three layers:

1. **Supabase dashboard → API → Logs**: per-request log of all PostgREST traffic. Watch for 5xx rates and slow queries.
2. **Cloudflare Analytics → Traffic**: edge-level request volume, cache hit rate, country distribution.
3. **External uptime monitor**: UptimeRobot or similar, GETting `https://api.tnbc.info/rest/v1/public_bibliography?limit=1&apikey=...` every 60 seconds. Alert on 5xx or no-response.

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

# Count
curl "https://api.tnbc.info/rest/v1/public_bibliography?select=count" \
  -H "apikey: ${ANON_KEY}" \
  -H "Prefer: count=exact"
```

## Cost

- **Supabase**: PostgREST is included in all tiers, including free.
- **Cloudflare**: DNS, proxying, edge cache, rate limiting all on the free plan.
- **R2 storage**: 10 GB free; bibliography exports total < 100 MB, well within free tier.
- **R2 egress**: 1 million reads/month free, which is far more than expected use.

Net additional cost over the website: **$0**.

## Checklist

- [ ] `sql/03_supabase_public_api.sql` applied to the Supabase project
- [ ] `public_bibliography` view visible in Supabase Table Editor
- [ ] `api_anon` role created with `SELECT` only on the public view
- [ ] DNS CNAME `api.tnbc.info` → `<project-ref>.supabase.co` proxied through Cloudflare
- [ ] Edge cache rule for `/rest/v1/public_bibliography` (5-min TTL)
- [ ] Rate-limit rule on `/rest/v1/*` (60/min/IP, 1000/hour/IP)
- [ ] R2 bucket `tnbc-atlas-exports` created with public-read on `/latest/`
- [ ] DNS CNAME `exports.tnbc.info` → R2 public URL
- [ ] GitHub Actions harvest workflow successfully uploads to R2 on completion
- [ ] OpenAPI spec available at `https://api.tnbc.info/rest/v1/`
- [ ] Sample queries documented on the website's `/research/api/` page
- [ ] External uptime monitor configured with alerting destination
