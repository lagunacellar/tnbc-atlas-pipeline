#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# TNBC Atlas — year-by-year backfill driver
#
# Runs the three harvesters + dedup_and_load for each year in the requested
# range, in reverse chronological order (most-recent first). Each year is a
# complete unit of work; the harvesters are individually resumable, so a kill
# + restart picks up the in-flight year from the last completed batch.
#
# Usage:
#   bash scripts/backfill_loop.sh                  # default: 2024 → 2005
#   bash scripts/backfill_loop.sh 2024 2005        # explicit range
#   bash scripts/backfill_loop.sh 2024 1995        # extend to foundational era
#
# Prerequisites:
#   - DATABASE_URL exported (see RUNBOOK-production-db.md)
#   - CONTACT_EMAIL exported (polite-pool mailto for PubMed/Crossref/OpenAlex)
#   - `make install` already run; Python deps installed
#   - Run inside tmux/screen — wall-clock estimate is 4–5 hours of harvesting
#     plus ~10 hours of enrichment after the loop finishes.
#
# Behavior:
#   - Logs each year's harvest+dedup to logs/backfill_YYYY-MM-DDThhmm.log
#   - On failure of any step, the loop continues to the next year and records
#     the failed year in logs/backfill_failures.log for re-run later. (Rationale:
#     pre-2010 records sometimes have malformed DOI casing that needs manual
#     intervention; we don't want one bad year to block the rest of the run.)
#   - Writes one summary line per year to logs/backfill_summary.log with timing
#     and record counts.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# Allow override (e.g. PYTHON=python3.12 bash scripts/backfill_loop.sh).
# macOS ships python3 but not `python`; the bare name is too fragile to assume.
PYTHON="${PYTHON:-python3}"

START_YEAR="${1:-2024}"
END_YEAR="${2:-2005}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

RUN_ID="$(date -u +%Y-%m-%dT%H%M)"
SUMMARY="logs/backfill_summary_${RUN_ID}.log"
FAILURES="logs/backfill_failures_${RUN_ID}.log"

echo "# TNBC Atlas backfill — started $(date -u +%FT%TZ)"     | tee -a "$SUMMARY"
echo "# Range: $START_YEAR → $END_YEAR (reverse chronological)" | tee -a "$SUMMARY"
echo "# Per-year log dir: logs/"                                | tee -a "$SUMMARY"
echo "# DATABASE_URL: ${DATABASE_URL:0:30}...${DATABASE_URL##*@}" | tee -a "$SUMMARY"
echo ""                                                          | tee -a "$SUMMARY"

# Sanity-check env before doing anything destructive
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set. Export it and retry." >&2
  exit 2
fi
if [[ -z "${CONTACT_EMAIL:-}" ]]; then
  echo "ERROR: CONTACT_EMAIL is not set (required for polite-pool mailto)." >&2
  exit 2
fi

for year in $(seq "$START_YEAR" -1 "$END_YEAR"); do
  YEAR_LOG="logs/backfill_${year}_${RUN_ID}.log"
  START_TS=$(date -u +%s)

  echo "=== Year $year started at $(date -u +%FT%TZ) ===" | tee -a "$SUMMARY" | tee -a "$YEAR_LOG"

  YEAR_OK=true

  # Each harvester is resumable; if it fails mid-run, re-running this script
  # for the same year will pick up where it left off.
  for source in pubmed europepmc openalex; do
    echo "--- harvest_${source} ${year} ---" | tee -a "$YEAR_LOG"
    if ! "$PYTHON" "scripts/harvest_${source}.py" \
        --start "${year}-01-01" --end "${year}-12-31" \
        >> "$YEAR_LOG" 2>&1; then
      echo "    FAILED harvest_${source} ${year} — see $YEAR_LOG" | tee -a "$SUMMARY"
      echo "${year} harvest_${source}" >> "$FAILURES"
      YEAR_OK=false
    fi
  done

  if $YEAR_OK; then
    echo "--- dedup_and_load ${year} ---" | tee -a "$YEAR_LOG"
    if ! "$PYTHON" scripts/dedup_and_load.py >> "$YEAR_LOG" 2>&1; then
      echo "    FAILED dedup_and_load ${year} — see $YEAR_LOG" | tee -a "$SUMMARY"
      echo "${year} dedup" >> "$FAILURES"
      YEAR_OK=false
    fi
  fi

  ELAPSED=$(( $(date -u +%s) - START_TS ))
  STATUS=$( $YEAR_OK && echo OK || echo FAIL )

  # Row count snapshot, best-effort
  COUNT=$(psql "$DATABASE_URL" -tAc \
    "SELECT count(*) FROM bibliography_records WHERE publication_year = $year" \
    2>/dev/null || echo "?")

  echo "=== Year $year $STATUS — ${ELAPSED}s, ${COUNT} canonical records for $year ===" \
    | tee -a "$SUMMARY" | tee -a "$YEAR_LOG"
  echo ""
done

echo "# Backfill loop complete at $(date -u +%FT%TZ)"        | tee -a "$SUMMARY"
if [[ -s "$FAILURES" ]]; then
  echo "# Failures recorded in $FAILURES:"                    | tee -a "$SUMMARY"
  cat "$FAILURES"                                             | tee -a "$SUMMARY"
  echo "# Re-run failed years by passing them as START END:"  | tee -a "$SUMMARY"
  echo "# e.g. bash scripts/backfill_loop.sh 2008 2008"       | tee -a "$SUMMARY"
fi

echo ""
echo "Next steps after this loop finishes:"
echo "  1. make enrich   # Crossref + Unpaywall + retraction sweep — overnight, in tmux"
echo "  2. make filter   # OpenAlex post-filter"
echo "  3. make tag      # rule-based topic tagging"
echo "  4. make benchmark # tier-1 recall check; target ≥ 95%"
echo "  5. make report   # exports + coverage_report.md + browser HTML"
