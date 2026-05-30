# Runbook — Orchestration with GitHub Actions

The pipeline runs as a set of scheduled GitHub Actions workflows. No servers to maintain, no scheduler process to keep alive, no Prefect Cloud account. Logs and run history live in the GitHub Actions UI. Cost: free for public repositories.

## Why GitHub Actions over Prefect / Airflow

For a single-project pipeline with weekly cadence and no need for inter-flow data passing, GitHub Actions wins on simplicity:

- **Already on GitHub.** No additional vendor account, no API keys to manage.
- **Free for public repos.** Unlimited minutes; 6-hour job timeout; well within our needs.
- **Secrets are first-class.** `${{ secrets.SUPABASE_DATABASE_URL }}` in workflows; rotation in repo settings.
- **Logs are right there.** Every run's stdout/stderr captured, browsable from the Actions tab.
- **No worker host.** No always-on infrastructure required.

We give up: rich flow visualization (Prefect's graph view), automatic retries with backoff (GitHub Actions has it but it's less ergonomic), and elegant data passing between tasks. None of those matter for our four-flow schedule.

## Architecture

```
                    GitHub
            ┌──────────────────────┐
            │  pipeline repo       │
            │  .github/workflows/  │
            └──────────┬───────────┘
                       │  cron triggers fire
                       ▼
            ┌──────────────────────┐
            │  GitHub Actions      │
            │  runner (Ubuntu)     │
            │  ephemeral VM,       │
            │  spun up per run     │
            └──────────┬───────────┘
                       │
            uses SUPABASE_DATABASE_URL
                       ▼
            ┌──────────────────────┐
            │  Supabase            │
            │  (Postgres + REST)   │
            └──────────────────────┘
```

The runner clones the repo, runs `make install`, executes the appropriate Python scripts, and shuts down. No persistent state between runs except what lives in the database.

## Step 1 — Set repository secrets

In the pipeline repo: **Settings → Secrets and variables → Actions → New repository secret**.

Add:

| Secret | Value | Used by |
|---|---|---|
| `SUPABASE_DATABASE_URL` | `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres` | All workflows that hit Postgres |
| `CONTACT_EMAIL` | Your project contact email (for polite-pool API headers) | Harvest workflows |
| `R2_ACCOUNT_ID` | Cloudflare R2 account ID (for export uploads) | Export workflow |
| `R2_ACCESS_KEY` | R2 access key | Export workflow |
| `R2_SECRET_KEY` | R2 secret key | Export workflow |

The pooled Supabase URI is recommended for short-lived workflow runs (lower connection-establishment overhead than the direct URI).

## Step 2 — Workflow files

Four workflows in `.github/workflows/`:

### `weekly-harvest.yml`

Runs the full harvest + enrich + filter + tag pipeline once a week.

```yaml
name: Weekly harvest
on:
  schedule:
    - cron: '0 6 * * 1'   # Mondays 06:00 UTC
  workflow_dispatch:        # manual trigger button in the Actions tab

jobs:
  harvest:
    runs-on: ubuntu-latest
    timeout-minutes: 350    # 6-hour ceiling; harvest finishes well inside this
    env:
      DATABASE_URL: ${{ secrets.SUPABASE_DATABASE_URL }}
      CONTACT_EMAIL: ${{ secrets.CONTACT_EMAIL }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Harvest PubMed
        run: python scripts/harvest_pubmed.py
      - name: Harvest Europe PMC
        run: python scripts/harvest_europepmc.py
      - name: Harvest OpenAlex
        run: python scripts/harvest_openalex.py
      - name: Dedup and load
        run: python scripts/dedup_and_load.py
      - name: Enrich Crossref
        run: python scripts/enrich_crossref.py
      - name: Enrich Unpaywall
        run: python scripts/enrich_unpaywall.py
      - name: OpenAlex post-filter
        run: python scripts/filter_openalex_only.py
      - name: Topic tagging
        run: python scripts/tag_topics.py
      - name: Generate exports
        run: python scripts/export_and_report.py
      - name: Upload exports to R2
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_KEY }}
        run: |
          aws --endpoint-url https://${{ secrets.R2_ACCOUNT_ID }}.r2.cloudflarestorage.com \
              s3 cp exports/ s3://tnbc-atlas-exports/latest/ --recursive
```

### `weekly-retraction-sweep.yml`

Lightweight; runs the Retraction Watch cross-reference. 5-minute job.

```yaml
name: Weekly retraction sweep
on:
  schedule:
    - cron: '0 12 * * 2'    # Tuesdays 12:00 UTC
  workflow_dispatch:

jobs:
  sweep:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      DATABASE_URL: ${{ secrets.SUPABASE_DATABASE_URL }}
      CONTACT_EMAIL: ${{ secrets.CONTACT_EMAIL }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: python scripts/retraction_sweep.py
      - name: Upload retraction report if anything changed
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: retracted-records-${{ github.run_id }}
          path: reports/retracted.csv
          retention-days: 90
```

### `quarterly-tier-review.yml`

Regenerates the tier-1 benchmark and tier-2 candidate list for editorial review. The candidates CSV is uploaded as a workflow artifact so the editorial board can download it from the Actions UI.

```yaml
name: Quarterly tier review
on:
  schedule:
    - cron: '0 8 15 1,4,7,10 *'   # 15th of Jan/Apr/Jul/Oct, 08:00 UTC
  workflow_dispatch:

jobs:
  tier-review:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      DATABASE_URL: ${{ secrets.SUPABASE_DATABASE_URL }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: python scripts/tier1_benchmark.py
      - run: python scripts/nominate_tier2.py
      - uses: actions/upload-artifact@v4
        with:
          name: tier-review-${{ github.run_id }}
          path: |
            reports/tier1_coverage.md
            reports/tier1_matches.csv
            reports/tier2_nomination.md
            reports/tier2_candidates.csv
          retention-days: 365
```

### `weekly-pg-dump.yml`

Defense-in-depth backup to R2 (in addition to Supabase's built-in backups). See `RUNBOOK-production-db.md` for the YAML.

## Step 3 — Deploy

Just commit the workflow files to the repo's `.github/workflows/` directory and push to `main`. GitHub picks them up immediately. The first scheduled run fires at the next matching cron time.

To trigger a workflow manually for testing: **Actions tab → Weekly harvest → Run workflow → Run workflow**. Watch logs stream in real time.

## Step 4 — Notifications

GitHub sends email notifications to the repo owner on workflow failures by default. To route to Slack or other channels:

- **Slack**: Use the [Slack GitHub Actions integration](https://github.com/marketplace/actions/slack-notify) as a final step that runs `if: failure()`.
- **PagerDuty / Opsgenie / Discord**: same pattern, different action.

Example final step:

```yaml
      - name: Notify Slack on failure
        if: failure()
        uses: slackapi/slack-github-action@v1
        with:
          payload: |
            {"text": "TNBC Atlas weekly harvest failed: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"}
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

For the closed-beta phase, email-on-failure is sufficient.

## On-call runbook

There is no on-call in the traditional sense. The workflows run unattended; you're notified by GitHub on failure; you can re-run them on demand.

### Symptom: weekly harvest failed

1. Open the failed run in the **Actions** tab.
2. Expand the failed step to see the error.
3. Most common causes:
   - **Upstream API rate limit (HTTP 429)**: re-run the workflow manually in 30 minutes; the scripts are resumable.
   - **Upstream API schema change** (parse error in a harvester): fix the parser locally, commit, push; next scheduled run will succeed. Or re-run the workflow manually after the fix.
   - **Supabase connection refused**: Supabase free-tier projects pause after a week of inactivity. Log into the dashboard once to wake it; re-run the workflow. (Upgrade to Pro avoids this entirely.)
   - **Disk space on the runner**: unlikely for our volumes; GitHub runners have ~14 GB free.

4. To resume from a partial run rather than restart from scratch: harvest, enrich, and filter scripts are all idempotent — re-running them picks up where they left off. Simply re-run the workflow; redundant work is skipped.

### Symptom: no harvest in 8 days

The repository's owner gets notified on failure. If you don't see notifications, check **Settings → Notifications** on your GitHub account.

If a scheduled workflow stops firing entirely, GitHub may have auto-disabled it (this happens to scheduled workflows on inactive repos). To re-enable: **Actions tab → Weekly harvest → Enable workflow**.

### Symptom: workflow runs longer than expected

The 6-hour timeout is much more than we need; if a run approaches 4 hours, something is wrong (upstream API slow, dedup runaway). Cancel the run from the Actions tab and investigate.

## Cost

GitHub Actions is **free for public repositories** with unlimited minutes. Private repos get 2,000 minutes/month free; our four workflows total ~30 minutes/week ≈ 130 minutes/month, well within the free private-repo budget if you ever choose to make the repo private.

## Migration from the manual Makefile workflow

The Makefile targets continue to work for local invocation. A developer testing a script on their laptop still uses `make harvest`; the production environment uses GitHub Actions. Both call the same Python scripts; the only difference is where they run.

## Checklist

- [ ] GitHub repo secrets configured: `SUPABASE_DATABASE_URL`, `CONTACT_EMAIL`, `R2_*` credentials
- [ ] Four workflow files committed to `.github/workflows/`
- [ ] Each workflow tested via manual trigger (`workflow_dispatch`) before being left on its cron schedule
- [ ] Failure-notification destination confirmed (email by default; Slack/PagerDuty optional)
- [ ] One-line operations doc points the editorial team at the Actions tab to find tier-review artifacts each quarter
