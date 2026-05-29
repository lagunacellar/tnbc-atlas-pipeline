# Runbook — Public REST API with PostgREST

This runbook describes how to expose a read-only public REST API over the production bibliography database, with rate limiting and an OpenAPI spec.

## Why PostgREST

The Phase 3 plan §4 picks PostgREST as the API layer. It generates a REST API directly from the Postgres schema, with no hand-written endpoint code. For a read-mostly bibliography it gives us:

- **Filter, select, order, paginate** for free, via querystring (`/records?year=eq.2024&select=title,doi&limit=100`).
- **OpenAPI 3 spec** at `/` automatically, so client generators work without effort.
- **Role-based access control** via PostgreSQL roles — we expose only the read-only role to the public.
- **No code to write, audit, or maintain** beyond a single config file.

The alternative is a hand-written FastAPI service, which is more flexible but several hundred lines of code that we then have to maintain. PostgREST is the right call until we need write endpoints or computed business logic.

## Architecture

```
              ┌────────────────────────────┐
              │  api.tnbc.info             │
              │  (Cloudflare proxy)        │
              └────────────┬───────────────┘
                           │   HTTPS, rate-limited
                           ▼
              ┌────────────────────────────┐
              │  PostgREST                 │
              │  (single process on        │
              │   harvest host or its own  │
              │   VM, behind Cloudflare)   │
              └────────────┬───────────────┘
                           │   read-only role
                           ▼
              ┌────────────────────────────┐
              │  Production PostgreSQL     │
              │  (Hetzner / Supabase /     │
              │   Neon / RDS)              │
              └────────────────────────────┘
```

Cloudflare in front does TLS termination, DDoS protection, and rate limiting at the edge.

## Step 1 — Create the read-only API role

```sql
-- Run as superuser on the production DB
CREATE ROLE api_anon NOLOGIN;
GRANT USAGE ON SCHEMA public TO api_anon;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO api_anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO api_anon;

-- Restrict to the tables intended for public exposure
REVOKE SELECT ON harvest_runs FROM api_anon;
REVOKE SELECT ON raw_snapshots FROM api_anon;
REVOKE SELECT ON dedup_decisions FROM api_anon;
-- api_anon retains SELECT only on bibliography_records

-- Create the JWT-less authenticator role that PostgREST uses
CREATE ROLE authenticator NOINHERIT LOGIN PASSWORD '<strong-random-password>';
GRANT api_anon TO authenticator;
```

The public will hit the API as `api_anon`; the only data exposed is the canonical bibliography table.

## Step 2 — Create a view to shape the public response

The full `bibliography_records` table has internal columns (`source_provenance` JSONB with provider raw payloads) we don't want to expose verbatim. Create a public view:

```sql
CREATE OR REPLACE VIEW public_bibliography AS
SELECT
  record_id,
  canonical_doi AS doi,
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
FROM bibliography_records;

GRANT SELECT ON public_bibliography TO api_anon;
```

Now `api_anon` cannot see source_provenance JSONB, internal foreign keys, or any auxiliary tables.

## Step 3 — Install PostgREST

```bash
# On the API host (could be the same as the DB host, or its own small VM)
# Download from https://github.com/PostgREST/postgrest/releases
curl -L https://github.com/PostgREST/postgrest/releases/download/v12.2.0/postgrest-v12.2.0-linux-static-x64.tar.xz \
  | tar xJ -C /usr/local/bin
```

## Step 4 — Configuration

`/etc/postgrest/tnbc-atlas.conf`:

```conf
db-uri = "postgres://authenticator:<password>@<db-host>/tnbc_atlas?sslmode=require"
db-schema = "public"
db-anon-role = "api_anon"
db-pool = 10
db-pool-timeout = 10

server-host = "127.0.0.1"
server-port = 3000

# Limit response payload to prevent runaway queries
db-max-rows = 1000

# CORS — allow the website's own domain, plus anything else researchers might use
server-cors-allowed-origins = "https://tnbc.info,https://*.tnbc.info"

# Log slow queries
log-level = "info"
```

Run as a systemd service:

```ini
# /etc/systemd/system/postgrest.service
[Unit]
Description=PostgREST for tnbc-atlas
After=network.target

[Service]
Type=simple
User=postgrest
ExecStart=/usr/local/bin/postgrest /etc/postgrest/tnbc-atlas.conf
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable: `systemctl enable --now postgrest`.

## Step 5 — Cloudflare front

In the Cloudflare dashboard for `tnbc.info`:

1. Add a DNS record: `api.tnbc.info` → A → `<api-host-ip>`. Proxy through Cloudflare (orange cloud).
2. Origin rules: send requests to `api.tnbc.info` to `<api-host-ip>:3000`.
3. **Cloudflare Workers or Rules** for rate limiting:
   - Anonymous IP limit: 60 requests / minute per IP.
   - Burst allowance: 10 requests / second short-burst.
   - Response on limit: HTTP 429 with `Retry-After: 60`.
4. **Cache rule**: cache GET responses for 5 minutes (bibliography updates weekly; staleness is acceptable). Pass-through on `?` querystring variations.
5. **SSL/TLS**: Full (strict). Cloudflare-issued cert at the edge.

## Step 6 — Verify

```bash
# Basic count
curl https://api.tnbc.info/public_bibliography?select=count

# Recent records
curl 'https://api.tnbc.info/public_bibliography?publication_year=eq.2025&select=title,doi,citation_count&order=citation_count.desc&limit=10'

# OpenAPI spec
curl https://api.tnbc.info/ | jq .info
```

Expected: JSON responses with the requested fields. The OpenAPI spec at `/` documents every column, every operator, and every endpoint automatically.

## Step 7 — Bulk-export endpoints (separate from the API)

For users who want the full corpus rather than paginated API calls, expose static dumps regenerated nightly:

```bash
# Cron job on the harvest host
# /etc/cron.daily/refresh-bulk-exports
#!/bin/bash
set -e
cd /opt/tnbc-atlas-pipeline
python scripts/export_and_report.py
rsync -a exports/bibliography.csv exports/bibliography.jsonl exports/bibliography.bib exports/bibliography.ris \
  <cdn-host>:/var/www/tnbc-cdn/exports/
```

Surface these at `https://exports.tnbc.info/bibliography.csv` etc. Update the website's `/research/api/` page to list both the live API and the bulk exports.

## Rate-limit and abuse-prevention policy

- Anonymous (no key): 60 req/min / IP, 1,000 req/hour / IP.
- Registered (post-launch, optional): higher limits with an API key sent via `X-API-Key` header.
- Hard caps: `db-max-rows = 1000` in PostgREST prevents any single response from returning more than 1,000 rows. Pagination via `Range` header or `?limit=N&offset=M`.

## OpenAPI consumption

Client libraries are auto-generatable from the live OpenAPI spec at `https://api.tnbc.info/`:

```bash
# Python
openapi-python-client generate --url https://api.tnbc.info/

# TypeScript
openapi-typescript https://api.tnbc.info/ -o api.d.ts
```

Document this on `/research/api/` so researchers don't have to figure it out themselves.

## Monitoring

- HTTP availability check on `/public_bibliography?limit=1` every 60 seconds.
- 5xx-rate alarm: alert if >1% of requests over a 5-minute window return 5xx.
- 429-rate signal (not alarm): high 429 rate suggests a misbehaving client; investigate.

## Cost

PostgREST is open source, free. Cloudflare's free tier covers the proxying and basic rate limiting. The only meaningful cost is the small VM running PostgREST (~$5/month on Hetzner), and even that can be co-hosted on the harvest host.

## Checklist

- [ ] `api_anon` role created with `SELECT` limited to `public_bibliography` view
- [ ] `authenticator` role created with a strong password
- [ ] `public_bibliography` view created and granted to `api_anon`
- [ ] PostgREST installed, configured, running as systemd service
- [ ] Cloudflare DNS record for `api.tnbc.info` created and proxied
- [ ] Rate-limit rule active at the Cloudflare edge
- [ ] Cache rule for 5-minute response caching
- [ ] OpenAPI spec served at `https://api.tnbc.info/`
- [ ] Sample queries documented on the website's `/research/api/` page
- [ ] Bulk exports refreshed nightly and surfaced at `exports.tnbc.info`
- [ ] Monitoring + 5xx alarm wired to on-call
