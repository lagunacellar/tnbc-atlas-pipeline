# Runbook — Production database on Supabase

The pilot uses sandbox Postgres that dies between sessions. Production runs on Supabase: managed Postgres 16 with PostgREST automatically exposed, daily backups, point-in-time recovery, and a SQL editor for ad-hoc queries. No VM to maintain.

## Why Supabase

Three short reasons:

1. **No server to maintain.** Supabase manages OS patches, Postgres minor versions, backups, replication, and monitoring. The Foundation does not need a Linux engineer.
2. **PostgREST included.** The public REST API at `api.tnbc.info` is generated automatically from the schema. No separate PostgREST process to host.
3. **Extension coverage.** Supabase supports the Postgres extensions our pipeline needs: `pg_trgm` (fuzzy-title dedup), `uuid-ossp` (record_id generation), and JSONB / TEXT[] are first-class.

Pricing: free tier covers a database up to 500 MB and 2 GB egress, which is plenty for the pilot (the 14k-record corpus is ~30 MB). The $25/month Pro tier raises database size to 8 GB and gives daily backups with 7-day point-in-time recovery — that's what production will land on once we backfill to 25–30k records.

## Step 1 — Create the Supabase project

In the Supabase dashboard at <https://supabase.com/dashboard>:

1. Click **New project**.
2. Organization: your organization.
3. Project name: `tnbc-atlas`.
4. Database password: generate a strong one (Supabase offers a generator); store it in your password manager. This becomes the `postgres` user password.
5. Region: pick the one closest to your editorial team and your harvest scheduler (`us-east-1`, `eu-central-1`, etc.).
6. Pricing plan: start on Free; upgrade to Pro before public launch.

Wait ~2 minutes for the project to provision.

## Step 2 — Note the connection details

From the project dashboard, **Settings → Database**:

- **Connection string (URI)**: `postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres`
- **Connection pooling URI** (recommended for serverless): `postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres`

Use the pooled URI for GitHub Actions runs (they're short-lived and connection-establishment overhead matters). Use the direct URI for ad-hoc psql sessions from your laptop.

Store both in your password manager and as a GitHub Actions secret (see RUNBOOK-orchestration.md).

## Step 3 — Apply the schema

From your laptop with `psql` installed (or via Supabase's SQL editor — paste the file contents in):

```bash
export DATABASE_URL="postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres"

psql "$DATABASE_URL" -f sql/01_schema.sql
psql "$DATABASE_URL" -f sql/02_enrichment_migration.sql
psql "$DATABASE_URL" -f sql/03_supabase_public_api.sql
```

Verify in the Supabase dashboard **Table Editor** that you see four tables (`bibliography_records`, `raw_snapshots`, `harvest_runs`, `dedup_decisions`) plus the `public_bibliography` view.

Extensions: `sql/01_schema.sql` enables `pg_trgm` and `uuid-ossp`. Both are pre-allowed on Supabase; no separate action required.

## Step 4 — Smoke harvest

From your laptop, with the connection string in your environment:

```bash
cd "/path/to/tnbc_atlas_pilot"
export DATABASE_URL="postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres"

# Tiny window so the smoke test finishes in seconds, not minutes
python scripts/harvest_pubmed.py --start 2026-04-01 --end 2026-04-30 --max 100
python scripts/dedup_and_load.py

psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM bibliography_records;"
# Should show ~100 rows
```

If this fails, fix here before scheduling anything. Common causes:

- **Connection refused / timeout**: Supabase projects on the free tier pause after a week of inactivity. Open the dashboard once to wake it; subsequent connections will work.
- **SSL required**: Supabase requires SSL. Append `?sslmode=require` if your psycopg version doesn't default to it. (The current `common.py` handles this automatically; included for completeness.)
- **Authentication failed**: Double-check the `postgres` password from Settings → Database.

## Step 5 — Backups

Supabase Pro includes daily backups with 7-day retention and point-in-time recovery to any second in that window. Free tier has manual-only backups.

To verify the backup configuration on Pro:

1. **Database → Backups** in the dashboard.
2. Confirm a backup from the last 24 hours is listed.
3. Click **Restore** to see the restoration flow (don't actually restore — just confirm the option exists and is documented for the team).

Optional extra: export a logical dump weekly to your own object storage as a defense-in-depth measure. A GitHub Actions workflow can do this:

```yaml
# .github/workflows/weekly-pg-dump.yml
name: Weekly logical backup
on:
  schedule:
    - cron: '0 3 * * 0'   # Sundays 03:00 UTC
  workflow_dispatch:
jobs:
  dump:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install pg client
        run: sudo apt-get install -y postgresql-client
      - name: Dump
        env:
          DATABASE_URL: ${{ secrets.SUPABASE_DATABASE_URL }}
        run: |
          TS=$(date -u +%Y%m%dT%H%M%SZ)
          pg_dump --no-owner --no-privileges --clean --if-exists "$DATABASE_URL" \
            | gzip > tnbc_atlas_${TS}.sql.gz
      - name: Upload to R2
        env:
          R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
          AWS_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_KEY }}
        run: |
          aws --endpoint-url https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com \
              s3 cp tnbc_atlas_*.sql.gz s3://tnbc-atlas-backups/
```

## Step 6 — Backup-restore drill (quarterly)

Even with managed backups, you don't have a backup until you've restored from one. Quarterly:

1. Spin up a temporary local Postgres in Docker:
   ```bash
   docker run -d --name tnbc-restore-test -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16
   sleep 5
   ```
2. Download the most recent R2 backup (from Step 5's defense-in-depth dump) and restore:
   ```bash
   gunzip -c tnbc_atlas_<latest>.sql.gz | PGPASSWORD=test psql -h localhost -p 5433 -U postgres -d postgres
   ```
3. Verify:
   ```bash
   PGPASSWORD=test psql -h localhost -p 5433 -U postgres \
     -c "SELECT COUNT(*), MAX(last_harvested_at) FROM bibliography_records;"
   ```
4. Tear down: `docker rm -f tnbc-restore-test`.

Record the result (row count, timestamp, any errors) in the operations log. If the drill fails, fix the backup process before declaring it operational.

## Step 7 — Secrets management

The Supabase connection string is the single most sensitive secret in the pipeline. Store it in exactly three places:

1. Your password manager (1Password, Bitwarden, etc.).
2. GitHub Actions secrets at the pipeline repo level: `Settings → Secrets and variables → Actions → SUPABASE_DATABASE_URL`.
3. Cloudflare for the API frontend (see RUNBOOK-public-api.md): used only in Cloudflare Workers if you add a custom proxy layer, otherwise not needed.

Never commit the connection string. The `.gitignore` excludes `.env` for local development; use that for laptop work.

Rotation: rotate the database password every 6 months or immediately on suspected compromise. Supabase dashboard → Settings → Database → Reset database password. Then update the secret in GitHub Actions and your password manager.

## Step 8 — Monitoring

Supabase provides built-in monitoring:

- **Database → Reports** in the dashboard shows query duration, connection count, CPU, memory, disk usage.
- **Database → Logs** for SQL-level debugging.
- **API → Logs** for PostgREST request logs.

What to actively watch:

- Disk usage > 80% → upgrade plan or archive old `raw_snapshots`.
- Connection count approaching pool limit → confirm GitHub Actions workflows aren't running concurrently.
- 5xx errors on the PostgREST API → check Supabase status page first.

Free tier monitoring is dashboard-only. For email/Slack alerts, enable Supabase notifications (Pro tier) or use an uptime monitor like UptimeRobot pointing at `https://api.tnbc.info/public_bibliography?limit=1`.

## Cutover from sandbox / pilot

Once production Supabase is verified working with the smoke harvest:

1. Update local development environment variable: `export DATABASE_URL="<supabase-uri>"` (in `.env` for convenience).
2. Update GitHub Actions secret `SUPABASE_DATABASE_URL` to the production URI.
3. Run the full backfill (see RUNBOOK-full-backfill.md) or load the pilot's existing JSONL exports:
   ```bash
   # Optional: bootstrap production with the pilot's 14k records
   pg_dump --no-owner --no-privileges --data-only --table=bibliography_records \
           "$PILOT_LOCAL_URL" > pilot_data.sql
   psql "$DATABASE_URL" -f pilot_data.sql
   ```
4. Decommission the local pilot (no migration needed; pilot DB and exports remain on disk as a reference).

## Checklist

- [ ] Supabase project created in the chosen region
- [ ] Database password stored in password manager
- [ ] Connection URIs (direct + pooled) noted in operations doc
- [ ] Schema files applied (`01_schema.sql`, `02_enrichment_migration.sql`, `03_supabase_public_api.sql`)
- [ ] Smoke harvest of 100 records succeeded
- [ ] Pro tier upgrade completed (before public launch)
- [ ] Defense-in-depth weekly dump workflow installed in GitHub Actions
- [ ] R2 bucket for backups created and credentials in GitHub Actions secrets
- [ ] First backup-restore drill completed and logged
- [ ] Monitoring confirmed in Supabase dashboard
- [ ] Connection string distributed via GitHub Actions secrets (not committed)
