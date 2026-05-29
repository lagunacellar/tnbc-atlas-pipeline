# Runbook — Orchestration with Prefect

This runbook describes how to take the manually-invocable scripts and run them as a scheduled, monitored production pipeline.

## Why Prefect (over Airflow)

For a team of 1–3 engineers on a single project of this size, Prefect 2 has substantially less operational overhead than Airflow: no metadata database to manage, no scheduler / web-server / worker split to deploy, and the work-pool model maps cleanly to a single-host setup. We recommend Prefect.

If your organization already standardizes on Airflow, the same flow logic translates directly; the DAGs would be near-identical in shape.

## Architecture

```
                    Prefect Cloud (free tier; UI + scheduler)
                              │
                              │ pulls work
                              ▼
                   ┌─────────────────────┐
                   │ Worker process on   │
                   │ harvest host        │
                   │ (systemd unit)      │
                   └──────────┬──────────┘
                              │
              ┌───────────────┼──────────────┬───────────────┐
              ▼               ▼              ▼               ▼
       harvest_pubmed   harvest_europepmc  enrich_*    retraction_sweep
              │               │              │               │
              └───────────────┴──────┬───────┴───────────────┘
                                     ▼
                          Production PostgreSQL
```

Prefect Cloud holds the schedule and the run history; the worker process pulls flow runs and executes them locally. The Postgres database is independent of Prefect.

## Setup

### Step 1 — Prefect Cloud account

Sign up at <https://app.prefect.cloud>. Free tier covers our needs (3 work pools, unlimited flow runs, 30-day run history).

Create a workspace, generate an API key, and store it as a secret on the harvest host:

```bash
prefect cloud login --key <api-key> --workspace <workspace-handle>
```

### Step 2 — Install Prefect

On the harvest host (same machine that runs the existing scripts):

```bash
pip install "prefect>=2.16,<3"
```

Add to `requirements.txt` (the line is currently commented out).

### Step 3 — Create a work pool

```bash
prefect work-pool create tnbc-atlas --type process
prefect worker start --pool tnbc-atlas
```

The worker process polls Prefect Cloud for flow runs and executes them in subprocesses on the harvest host.

Run the worker under systemd for production:

```ini
# /etc/systemd/system/prefect-worker.service
[Unit]
Description=Prefect worker for tnbc-atlas
After=network.target

[Service]
Type=simple
User=tnbc
WorkingDirectory=/opt/tnbc-atlas-pipeline
Environment="PREFECT_API_KEY=<your-key>"
Environment="PREFECT_API_URL=https://api.prefect.cloud/api/accounts/<acct>/workspaces/<ws>"
Environment="PGHOST=<production-db-host>"
Environment="PGUSER=tnbc_app"
Environment="PGDATABASE=tnbc_atlas"
EnvironmentFile=/etc/tnbc-atlas/secrets.env
ExecStart=/usr/local/bin/prefect worker start --pool tnbc-atlas
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable: `systemctl enable --now prefect-worker`.

## Flows

Create `flows/` at the repo root with one file per recurring job. Each flow wraps the existing CLI scripts; no script logic moves into Prefect.

### `flows/weekly_harvest.py`

```python
from datetime import timedelta
from prefect import flow, task, get_run_logger
from prefect.runtime import flow_run
import subprocess

REPO_ROOT = "/opt/tnbc-atlas-pipeline"

@task(retries=2, retry_delay_seconds=60)
def run_script(script: str, args: list[str] | None = None):
    log = get_run_logger()
    cmd = ["python", f"scripts/{script}", *(args or [])]
    log.info("Running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        log.error("STDERR: %s", result.stderr[-2000:])
        raise RuntimeError(f"{script} exited {result.returncode}")
    log.info("STDOUT tail: %s", result.stdout[-1000:])

@flow(name="weekly-harvest", retries=1, retry_delay_seconds=600)
def weekly_harvest():
    """Weekly incremental harvest: PubMed + Europe PMC + OpenAlex, dedup, enrich, filter, tag."""
    log = get_run_logger()
    log.info("Starting weekly harvest run %s", flow_run.id)

    # Primary sources — run sequentially; could parallelize but rate-limit-friendly to serialize
    run_script("harvest_pubmed.py")
    run_script("harvest_europepmc.py")
    run_script("harvest_openalex.py")

    # Dedup + load
    run_script("dedup_and_load.py")

    # Enrichment (Crossref and Unpaywall are resumable; safe to call repeatedly)
    run_script("enrich_crossref.py")
    run_script("enrich_unpaywall.py")

    # Quality passes
    run_script("filter_openalex_only.py")
    run_script("tag_topics.py")

    # Exports + browser refresh
    run_script("export_and_report.py")
    run_script("build_browser.py")

    log.info("Weekly harvest complete")

if __name__ == "__main__":
    weekly_harvest.serve(
        name="weekly-harvest-prod",
        cron="0 6 * * 1",  # Mondays at 06:00 UTC
        tags=["harvest", "weekly"],
    )
```

### `flows/weekly_retraction.py`

```python
from prefect import flow, task, get_run_logger
import subprocess

@task(retries=3, retry_delay_seconds=300)
def run_sweep():
    log = get_run_logger()
    result = subprocess.run(
        ["python", "scripts/retraction_sweep.py"],
        cwd="/opt/tnbc-atlas-pipeline", capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        log.error(result.stderr[-2000:])
        raise RuntimeError("retraction_sweep failed")
    log.info(result.stdout[-1000:])

@flow(name="weekly-retraction-sweep")
def weekly_retraction():
    """Weekly cross-reference against Retraction Watch."""
    run_sweep()

if __name__ == "__main__":
    weekly_retraction.serve(
        name="weekly-retraction-prod",
        cron="0 12 * * 2",  # Tuesdays at 12:00 UTC
        tags=["retraction", "weekly"],
    )
```

### `flows/quarterly_recite.py`

```python
from prefect import flow
import subprocess

@flow(name="quarterly-citation-refresh")
def quarterly_recite():
    """Re-fetch OpenAlex for every record to refresh citation counts."""
    # Implementation: a script that walks bibliography_records and re-queries OpenAlex
    # for cited_by_count only. Implementable as scripts/refresh_citations.py;
    # not built in the pilot but the flow placeholder reserves the slot.
    raise NotImplementedError("Add scripts/refresh_citations.py before scheduling")

if __name__ == "__main__":
    quarterly_recite.serve(
        name="quarterly-recite-prod",
        cron="0 8 1 1,4,7,10 *",  # 1st of Jan/Apr/Jul/Oct at 08:00 UTC
        tags=["citation-refresh", "quarterly"],
    )
```

### `flows/tier_review.py`

```python
from prefect import flow
import subprocess

@flow(name="quarterly-tier-review")
def quarterly_tier_review():
    """Regenerate tier-1 benchmark and tier-2 candidate list for editorial review."""
    for s in ("tier1_benchmark.py", "nominate_tier2.py"):
        subprocess.run(["python", f"scripts/{s}"], cwd="/opt/tnbc-atlas-pipeline", check=True, timeout=300)

if __name__ == "__main__":
    quarterly_tier_review.serve(
        name="quarterly-tier-review-prod",
        cron="0 8 15 1,4,7,10 *",  # 15th of Jan/Apr/Jul/Oct
        tags=["editorial", "quarterly"],
    )
```

## Deployment

Each flow is deployed once with `python flows/<name>.py`, which registers the schedule with Prefect Cloud. The worker picks up runs automatically.

```bash
cd /opt/tnbc-atlas-pipeline
python flows/weekly_harvest.py        # registers + schedules
python flows/weekly_retraction.py
python flows/quarterly_recite.py
python flows/tier_review.py
```

In the Prefect Cloud UI:

- **Deployments** view shows all four registered flows with their next scheduled run.
- **Flow runs** view shows historical executions, logs, and durations.
- **Notifications** can be configured to email or Slack on failure.

## Alerting

Configure two notification rules in Prefect Cloud:

1. **Flow run failure** → email on-call address. Triggered for any state in (`Failed`, `Crashed`, `TimedOut`).
2. **Flow run taking longer than expected** → notice (not alarm) if a flow runs more than 2× its historical average. The harvest flow should typically complete in under 30 minutes once steady-state.

Also alert from the database layer (Step 9 of `RUNBOOK-production-db.md`): if no `harvest_runs` row is inserted for >8 days, page on-call. This catches failure modes where Prefect itself goes down silently.

## On-call runbook

### Symptom: weekly harvest failed

1. Open the failed flow run in Prefect Cloud.
2. Read the last 50 lines of logs (Prefect captures stdout + stderr).
3. Most common causes:
   - Upstream API rate limit (HTTP 429) — Prefect retries with exponential backoff; if it still fails, wait an hour and re-trigger manually.
   - Upstream API schema change — surfaces as a parse error in a harvester. Fix the parser; re-run.
   - Database connection refused — check production Postgres health (Step 9 of the DB runbook).
   - Disk full on harvest host — clean `/var/log` and check `df -h`.

4. If you need to re-run just the failed step rather than the whole flow, find the step in the flow run's task list and "Restart" from there.

### Symptom: weekly retraction sweep failed

Same diagnostic flow. The most common cause is Crossref Labs returning HTML instead of CSV (a brief outage on their side); just re-run.

### Symptom: monitoring says no harvest in 8 days

1. Check Prefect worker status: `systemctl status prefect-worker`.
2. Check the schedule in Prefect Cloud — has it been paused?
3. Check the harvest host: is it reachable, is disk space available, is the worker process actually running?
4. If the worker is healthy but no runs are firing, restart it: `systemctl restart prefect-worker`. Schedules will resume.

## Cost

Prefect Cloud free tier covers the workload described here (4 deployments, ~6 flow runs per week, well under the 2,000-run/month free-tier ceiling).

## Migration from the manual cron / Makefile workflow

The Makefile targets continue to work for manual / local invocation. Prefect is layered on top for the scheduled production runs; it doesn't replace the local workflow. A developer testing a script locally still uses `make harvest`; the production environment uses Prefect.

## Checklist

- [ ] Prefect Cloud account and workspace created
- [ ] API key stored as a server-side environment variable, not in source
- [ ] Worker installed as a systemd unit; auto-starts on boot
- [ ] `flows/` directory created with the four flow files
- [ ] Each flow deployed and visible in Prefect Cloud UI
- [ ] Notification rules configured (email or Slack)
- [ ] Manual smoke run of `weekly_harvest` from the UI completes successfully
- [ ] On-call runbook (this document) circulated to whoever is on rotation
