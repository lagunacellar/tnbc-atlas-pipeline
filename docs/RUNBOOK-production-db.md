# Runbook — Migrate from sandbox Postgres to managed Postgres

This runbook describes the one-time migration from the local pilot Postgres (which dies between sessions) to a managed production Postgres that the harvest pipeline, the public API, and the website's library search will all point at.

## Choice of provider

The Phase 3 plan defaults to **Hetzner Cloud** running a single Postgres node. Alternatives that work equally well at this scale:

| Provider | Best for | Cost (May 2026, USD) | Notes |
|---|---|---|---|
| **Hetzner Cloud** (CCX13 + managed PG add-on) | EU-hosted, GDPR-favorable, predictable cost | ~€20/month | Manual provisioning; backups via Hetzner snapshots |
| **Supabase** | Fastest to launch, includes PostgREST, RLS, auth (which we don't need) | $25/month Pro | Backups included; some lock-in around extensions |
| **Neon** | Serverless, generous free tier | $0–$19/month | Branching is useful for dev/prod separation |
| **AWS RDS Postgres** | If already on AWS | $20–$50/month depending on tier | More expensive at this scale, but worth it if other infra is in AWS |

This runbook uses **Hetzner Cloud** as the worked example; substitute provider-specific commands where noted.

## Prerequisites

- Provider account with billing enabled.
- Public SSH key on file.
- A DNS record under your control (e.g., `db.tnbc.info`) pointing at the future server, if you want a friendly hostname; otherwise the provider-assigned hostname is fine.
- The local pilot codebase (this repo) checked out.

## Step 1 — Provision the server

### Hetzner

```bash
# Using the hcloud CLI; equivalent in the web console
hcloud server create \
  --name tnbc-atlas-db \
  --type ccx13 \
  --image ubuntu-22.04 \
  --location fsn1 \
  --ssh-key <your-key-name>
```

Get the public IP from `hcloud server list`. Open SSH:

```bash
ssh root@<public-ip>
```

### Supabase / Neon / RDS

Use the provider's project-creation flow. You'll receive a Postgres connection string of the form `postgres://user:pass@host:port/db`. Skip ahead to Step 3.

## Step 2 — Install Postgres 16 on the server (Hetzner / self-managed)

```bash
# On the new server, as root
apt update && apt -y upgrade
apt -y install postgresql-16 postgresql-contrib-16

# Enable and start
systemctl enable postgresql
systemctl start postgresql

# Create the production database and a read-write app user
sudo -u postgres psql <<SQL
CREATE USER tnbc_app WITH PASSWORD '<strong-random-password>';
CREATE DATABASE tnbc_atlas OWNER tnbc_app;
\c tnbc_atlas
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
SQL

# Allow remote connections (lock down by IP allowlist in pg_hba.conf;
# require SSL for all client connections)
# Edit /etc/postgresql/16/main/postgresql.conf:
#   listen_addresses = '*'
#   ssl = on
# Edit /etc/postgresql/16/main/pg_hba.conf:
#   hostssl tnbc_atlas tnbc_app  <harvest-host-ip>/32   scram-sha-256
#   hostssl tnbc_atlas tnbc_app  <api-host-ip>/32       scram-sha-256
systemctl reload postgresql
```

## Step 3 — Apply the schema

From your local machine, with `psql` installed:

```bash
export PGHOST=<production-host>
export PGUSER=tnbc_app
export PGDATABASE=tnbc_atlas
# PGPASSWORD set from a password manager / environment variable; never commit it

psql -f sql/01_schema.sql
psql -f sql/02_enrichment_migration.sql

# Verify
psql -c "\dt"
psql -c "\d bibliography_records"
```

Expected: 4 tables (`bibliography_records`, `raw_snapshots`, `harvest_runs`, `dedup_decisions`) plus the enrichment-migration columns added to `bibliography_records`.

## Step 4 — Run a smoke harvest

Confirm the pipeline can write to the production DB before doing the full backfill.

```bash
# In the project repo, set the DSN to point at production
export PGHOST PGUSER PGDATABASE
# Pick a small recent window to limit the smoke test
python scripts/harvest_pubmed.py --start 2026-04-01 --end 2026-05-01 --max 200
python scripts/dedup_and_load.py

psql -c "SELECT COUNT(*) FROM bibliography_records;"
# Should show ~200 rows
```

If this fails, fix here before proceeding. Common causes:
- `pg_hba.conf` rejects the client IP (add your IP to the allowlist)
- SSL not enforced on the client side (add `?sslmode=require` to the DSN or `PGSSLMODE=require`)
- Schema migration not applied (re-run Step 3)

## Step 5 — One-time data load from the pilot (optional)

If you want to start production with the 14k pilot records instead of re-harvesting from scratch:

```bash
# From the local pilot environment, with both DBs accessible
pg_dump --host=/tmp/pgsock --no-owner --no-privileges --data-only \
        --table=bibliography_records \
        tnbc_atlas > pilot_dump.sql

# Apply to production
psql -h <production-host> -U tnbc_app -d tnbc_atlas -f pilot_dump.sql
```

This is a one-time bootstrap. After it, the production DB lives on its own; the local pilot DB is no longer the source of truth.

Alternative: skip this and let the full backfill (see `RUNBOOK-full-backfill.md`) populate production from scratch. The backfill takes longer but produces a more consistent result.

## Step 6 — Backups

### Hetzner / self-managed

Daily `pg_dump` to a separate volume or object storage:

```bash
# /etc/cron.daily/backup-tnbc-atlas
#!/bin/bash
set -e
TS=$(date -u +%Y%m%dT%H%M%SZ)
pg_dump -U tnbc_app -d tnbc_atlas | gzip > /var/backups/tnbc_atlas_${TS}.sql.gz
# Retain 30 days
find /var/backups -name "tnbc_atlas_*.sql.gz" -mtime +30 -delete
# Off-site replica: rsync to S3-compatible storage (Hetzner Storage Box, Backblaze B2)
rsync -a /var/backups/ <remote>:/backups/tnbc-atlas/
```

Make it executable: `chmod +x /etc/cron.daily/backup-tnbc-atlas`.

### Managed providers

Supabase, Neon, and RDS all do daily backups automatically. Verify retention (30 days is the floor for this project) and that point-in-time recovery is enabled.

## Step 7 — Backup-restore drill (quarterly)

You don't have a backup until you've restored from one.

```bash
# Spin up a temporary Postgres (Docker is fine for a drill)
docker run -d --name tnbc-restore-test \
  -e POSTGRES_PASSWORD=test \
  -p 5433:5432 postgres:16

sleep 5
gunzip -c /var/backups/tnbc_atlas_<latest>.sql.gz | \
  PGPASSWORD=test psql -h localhost -p 5433 -U postgres -d postgres

# Verify
PGPASSWORD=test psql -h localhost -p 5433 -U postgres -d postgres \
  -c "SELECT COUNT(*), MAX(last_harvested_at) FROM bibliography_records;"

# Clean up
docker rm -f tnbc-restore-test
```

Document the result (record count, timestamp, any errors) in the operations log.

## Step 8 — Secrets management

Production secrets (DB password, API keys for any future paid sources) must never be committed. Recommended setup:

- Local development: `.env` file in the project root, listed in `.gitignore` (the existing `.gitignore` already excludes `.env`).
- Production servers / CI: provider's secret manager (Hetzner doesn't have one; use HashiCorp Vault or 1Password Connect; Supabase/Neon/RDS have built-in secret stores).
- Rotation cadence: app user password rotated every 6 months, or immediately on suspected compromise.

## Step 9 — Monitoring

Minimum monitoring stack:

- **Disk usage** — alert at 80% full. Bibliography grows ~5 MB / 1,000 records.
- **Connection count** — alert if approaching `max_connections` (default 100); the harvest pipeline should never need more than 10 concurrent.
- **Query duration** — alert if any query exceeds 30 seconds; the existing queries are all simple and should be sub-second.
- **Replication lag** (if you add a read replica later) — alert at >60 seconds.
- **Backup status** — alert if a daily backup is missing or smaller than 80% of the previous day's.

Tools: any of Grafana + Prometheus + postgres_exporter, Datadog, or the provider's built-in monitoring.

## Step 10 — Cutover from local pilot to production

Once production is verified working:

1. Update the harvest scripts' DSN to point at production permanently (set `PGHOST` / `PGUSER` / `PGDATABASE` in the production environment; the scripts respect these).
2. Update the website's bibliography refresh script to read from production (see `tnbc_info_site/` README for the slim-JSON build step).
3. Decommission the local pilot DB (no migration needed; just stop relying on it).

## Rollback plan

If the migration goes wrong, the local pilot continues to work independently. Production is additive, not replacing. You can always re-export from the pilot's JSONL artifacts (`exports/bibliography.jsonl`) and re-import.

If a production deploy corrupts the database, restore from the most recent backup (Step 7's drill validates this works).

## Checklist

- [ ] Provider account created, billing verified
- [ ] Server provisioned (or managed instance created)
- [ ] Postgres 16 installed with `pg_trgm` and `uuid-ossp` extensions
- [ ] App user (`tnbc_app`) created with strong password
- [ ] `pg_hba.conf` allows only specific client IPs over SSL
- [ ] Schema files applied (`sql/01_schema.sql`, `sql/02_enrichment_migration.sql`)
- [ ] Smoke harvest succeeds (200 records loaded)
- [ ] Backup script installed in `/etc/cron.daily/`
- [ ] Backup-restore drill completed; result documented
- [ ] Secrets stored outside source control (not in any git-tracked file)
- [ ] Monitoring configured with alerting destinations (email or Slack)
- [ ] Methods page on the website updated to reflect production hosting
