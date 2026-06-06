# Post-mortem — Corpus pipeline recovery & 1990 backfill

**Date written:** 2026-06-06
**Author:** S. Yang (with Claude)
**Status:** Resolved
**Severity:** High (production data-integrity loss during closed beta; no public/patient impact)
**Window:** 2026-05-29 → 2026-06-06

This is a blameless engineering post-mortem. It is an internal document and is deliberately **not** published to the public `/changes/` errata page — no published medical content was ever wrong, and the incident detail (database wipe, credential rotation) is operational, not reader-facing.

---

## 1. Summary

Over the week following the initial pipeline release (2026-05-29), an attempt to broaden the bibliography from the 24-month pilot (14,319 records) to a full 1990–2026 backfill surfaced **two compounding bugs in `dedup_and_load.py`** that silently corrupted the production corpus, plus a **third performance failure** at scale. The damage was amplified by the **weekly GitHub Actions cron running the old code** before fixes were pushed. Recovery required patching the loader, re-running the backfill, rotating a leaked database credential, broadening the harvest query, migrating the bulk export off Cloudflare Pages onto R2, and building a relevance + topic-tag sanitization layer. A separate, later **R2 custom-domain misconfiguration (CF error 1014)** then took the public library to zero records until the domain binding was repaired.

End state: **122,742 raw records / 100,612 public-visible (82%)**, spanning 1990–2026, served from R2, with the library page rendering correctly.

## 2. Impact

- **Data integrity:** Enrichment columns (Crossref/Unpaywall/Retraction Watch) were wiped on multiple loader runs; the corpus was repeatedly reduced to a partial set.
- **Public exposure:** None to patient-layer content. The site was behind Cloudflare Access throughout. The Evidence Library (researcher layer) showed stale or, at the worst point, zero records.
- **Security:** A database connection string was exposed in chat scrollback and had to be treated as compromised.
- **Time:** ~1 week of intermittent debugging and re-runs.

## 3. Timeline (anchored to commits)

| Date | Event |
|---|---|
| 2026-05-29 | `cb78403` Initial public release of the pipeline. Pilot corpus = 14,319 records, 24-month window. |
| 2026-05-30 | `825237a` Fix NULL elements in Postgres `TEXT[]` columns breaking joins. `69bccf8`/`859df99` move filter+tag to Postgres-native and fix workflow ordering. `9860df0` replace partial index with full `NULLS FIRST` index (partial form excluded trusted-source seeds with NULL relevance, forcing seq scans). `c0a8088` Supabase Pro + Custom Domain for `api.tnbc.info` (resolved an earlier 1014 on the API path). |
| 2026-05-31 | `0f96610` Add `backfill_loop.sh` — year-by-year harvest+dedup driver. Running it exposed the loader bugs below. |
| 2026-06-01 | `d832cfc` **The fix:** bulk-load via COPY-into-temp + UPSERT; remove TRUNCATE. |
| 2026-06-02 | `dd8805a` Broaden harvester queries (three eras of nomenclature). `3c46010` prelaunch checklist. **Supabase DB password rotated** after credential leak in scrollback. |
| 2026-06-05 | `a777a3d` slim-JSON regenerator writes to `exports/` for R2 pickup. `1d638bd` relevance-filter regex fixes + topic-tag gate with `keep_strong` bypass → 100,612 public-visible. |
| 2026-06-06 | R2 custom-domain `exports.tnbc.info` returning CF **error 1014** on every object → library at 0 records. DNS/custom-domain rebinding fixed it (200 + correct CORS). Library page pilot-era filter artifacts removed. |

## 4. Root causes

### 4.1 Single-snapshot dedup (silent data loss)
`dedup_and_load.py` originally resolved each source to a **single `latest_snapshot`**. The year-by-year backfill writes one snapshot *per year per source*, so each loader run ingested only the most recent snapshot and ignored every other year. The corpus never accumulated.
**Fix:** `all_snapshots()` reads *every* per-source snapshot (sorted oldest-first by mtime). `latest_snapshot()` retained only as a deprecated shim. (See `scripts/dedup_and_load.py` lines ~35–55, 121–126.)

### 4.2 TRUNCATE-before-load (enrichment wipe)
The loader `TRUNCATE`d the target table before each load, making every run a **full replace** and destroying the Crossref/Unpaywall/Retraction-Watch enrichment columns that are populated by a *separate* downstream pass. Any incremental backfill run reset enrichment to empty.
**Fix:** removed TRUNCATE entirely; loads are now incremental via `ON CONFLICT (canonical_doi) DO NOTHING`, which preserves existing rows and their enrichment. (Lines ~217–222, 310, 334.)

### 4.3 Row-by-row INSERT vs. the Supabase pooler (scale failure)
With 30k+ records, the original row-by-row INSERT pattern dropped the Supabase **Session pooler** connection mid-load, leaving partial state.
**Fix:** COPY-into-temp-table + a single `INSERT … SELECT … ON CONFLICT`. One bulk stream over the pooler instead of tens of thousands of round-trips. (Lines ~213–339.)

### 4.4 GitHub Actions cron running old code (damage amplifier)
The weekly cron (`.github/workflows/weekly-harvest.yml`, `0 6 * * 1`) checks out `main` and runs whatever is there. While the loader fixes sat **unpushed locally**, the Monday cron ran the *old TRUNCATE-ing* loader against production and re-wiped the corpus. The local working tree being correct was irrelevant — the cron only sees `origin/main`.

### 4.5 Cloudflare Pages 25 MB asset limit
The slim JSON the library page fetches outgrew Pages' 25 MB per-file asset cap once the corpus expanded (now ~42 MB at 100k records).
**Fix:** serve it from Cloudflare R2 (`exports.tnbc.info`, no per-file cap); deleted the in-repo `public/data/bibliography.json` and gitignored it.

### 4.6 R2 custom-domain 1014 (later, separate)
`exports.tnbc.info` was wired as a plain proxied CNAME rather than a *connected R2 custom domain*, so Cloudflare returned **error 1014 ("CNAME Cross-User Banned")** on every object. The object in the bucket was healthy the whole time; only the domain binding was broken.
**Fix:** reconnect the custom domain through the R2 bucket settings; ensure a single R2-managed DNS record (no stale manual CNAME to `*.r2.cloudflarestorage.com` / `*.r2.dev`). Same failure class previously seen on `api.tnbc.info`.

### 4.7 Credential leak
A full Postgres connection string (with password) appeared in chat scrollback.
**Fix:** rotated the Supabase database password (2026-06-02); updated the GHA `SUPABASE_DATABASE_URL` secret. Any value from before that date is compromised and must not be reused.

## 5. What went wrong (contributing factors)

- **Compounding, silent failures.** None of 4.1–4.3 threw an error; each produced a plausible-looking smaller corpus, so the loss was attributed to the harvest rather than the loader for too long.
- **Deploy lag as a hazard.** Treating "fixed locally" as "fixed" ignored that an automated job runs `origin/main` on a schedule. Unpushed fixes are worse than no fixes when a cron can run the old path against prod.
- **Destructive-by-default loader.** TRUNCATE + full-replace is the wrong default for an incrementally-enriched table.
- **Secrets handling.** A live credential reached an unsafe surface.

## 6. What went well

- The COPY/UPSERT rewrite is both the correctness fix *and* the performance fix — one change closed three failure modes.
- Closed-beta posture (Cloudflare Access) meant **zero patient/public impact** throughout.
- The raw audit table was never the thing being destroyed — re-running the (fixed) loader and enrichment fully reconstructed the corpus. No primary data was unrecoverable.
- The 1014 incident was diagnosed quickly by checking the bucket object (healthy) separately from the domain (403/1014), avoiding a wasted re-upload.

## 7. Action items

- [x] Remove TRUNCATE; incremental UPSERT with enrichment preservation (`d832cfc`).
- [x] Read all per-source snapshots in the loader.
- [x] COPY-into-temp bulk load for pooler stability.
- [x] Rotate leaked DB credential; update GHA secret.
- [x] Migrate bulk export to R2; remove in-repo JSON.
- [x] Reconnect `exports.tnbc.info` as a proper R2 custom domain.
- [ ] **Verify the GHA `SUPABASE_DATABASE_URL` secret matches the rotated password before the next Monday cron.** (Prelaunch checklist dependency.)
- [ ] **Add a loader guard:** abort if a run would reduce live row count by more than a threshold (e.g. >5%) without an explicit `--allow-shrink` flag. Turns silent data loss into a hard stop.
- [ ] **Add a post-load assertion** in the workflow: fail the job if enrichment-column NULL rate jumps run-over-run.
- [ ] **Never echo connection strings.** Load creds by parsing a gitignored file (`.secrets/cloud.env`), never print values. Prefer short-lived/scoped tokens.
- [ ] Consider PITR/backups verification (Supabase Pro) as a recovery backstop — see `PRELAUNCH-CHECKLIST.md`.

## 8. Related docs

- `docs/RUNBOOK-public-api.md` — Supabase Custom Domain + 1014 troubleshooting (the API-side precedent for §4.6).
- `docs/RUNBOOK-full-backfill.md` — the backfill procedure whose first real run surfaced §4.1–4.3.
- `docs/PRELAUNCH-CHECKLIST.md` — open dependencies, including the cron-secret verification.
- `scripts/dedup_and_load.py` — the loader; inline comments document the TRUNCATE/COPY rationale.
