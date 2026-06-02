# Pre-launch checklist

Things deferred from closed-beta setup that need to be done before removing the Cloudflare Access gate and making `tnbc.info` publicly reachable.

Each item is small on its own; collecting them here so nothing falls through the cracks. Items are tagged with where the work happens.

---

## Infrastructure upgrades ($)

- [ ] **Upgrade Cloudflare zone to Pro** (~$25/month).
  Unlocks WAF Rate Limiting Rules, image optimization, better DDoS analytics, and Smart Tiered Cache.
  *Where:* Cloudflare dashboard → Plans → Upgrade.

- [x] **Upgrade Supabase project to Pro** (~$25/month). *Completed 2026-05-30.*
  Done as part of standing up the `api.tnbc.info` Supabase Custom Domain (Custom Domain is Pro-tier-only and solves the Cloudflare error 1014 that the prior plain-CNAME approach hit). Also brings daily backups with 7-day point-in-time recovery, larger database size (8 GB), no week-of-inactivity pause.
  *Where:* Supabase dashboard → Project Settings → Plan.

- [ ] **Confirm Supabase PITR backups are active.**
  Pro tier enables 7-day point-in-time recovery by default, but verify it's populating under Supabase → Database → Backups → Point in Time Recovery, and note the recovery window. This partially relieves the "weekly logical backup" item in the Pipeline cleanup section below — PITR covers the recovery story for the database itself; the logical pg_dump backup is now defense-in-depth rather than the primary backup mechanism.
  *Where:* Supabase dashboard → Database → Backups.

Closed-beta ongoing cost (now): ~$25/month (Supabase Pro only).
Combined ongoing cost at public launch: ~$50/month (adds Cloudflare Pro).

---

## API hardening

These items mostly require flipping `api.tnbc.info` from DNS-only (grey cloud) to Proxied (orange cloud) at Cloudflare, which in turn requires Cloudflare Pro for the rate-limiting story. Currently the CNAME is grey-cloud, terminating direct at Supabase's Custom Domain edge — see `docs/RUNBOOK-public-api.md` § Architecture for the diagram.

- [ ] **Flip `api.tnbc.info` to Proxied (orange cloud).**
  Pre-requisite: set Cloudflare SSL/TLS mode for the `tnbc.info` zone to **Full (strict)** so Cloudflare end-to-end-validates the Supabase-issued cert. Without Full (strict) you may see cert-mismatch errors. After flipping, re-run the verification curl from RUNBOOK-public-api.md § Step 3 to confirm traffic still flows.
  *Where:* Cloudflare → DNS → Records → click cloud icon next to `api`; then SSL/TLS → Overview → Edit.

- [ ] **Add Cloudflare Cache Rule for `/rest/v1/public_bibliography`** (requires orange cloud above).
  See `docs/RUNBOOK-public-api.md` § 4 → Caching. Suggested: 5-minute Edge TTL, hostname=`api.tnbc.info`, path contains `/rest/v1/public_bibliography`.
  *Where:* Cloudflare → Caching → Cache Rules → Create rule.

- [ ] **Add WAF Rate Limiting Rule for `api.tnbc.info`** (requires CF Pro + orange cloud above).
  See `docs/RUNBOOK-public-api.md` § 4 → Rate limiting. Suggested: 60 req/min/IP, action Block + 429 with Retry-After.
  *Where:* Cloudflare → Security → Security rules → Rate limiting rules.

- [ ] **Rotate the Supabase anon key after initial public exposure.**
  Standard hygiene for credentials that have been observable during closed beta. Do this the day Cloudflare Access is removed, so the key never lingers from the closed-beta phase into the public-exposure phase.
  *Where:* Supabase → Settings → API → Reset anon key. Then update GitHub Actions secrets and any client-side references.

- [ ] **(Optional) Add a Cloudflare Worker proxy layer** that injects the Supabase anon key server-side, so the key never appears in client-side JS or DevTools.
  Defense-in-depth. About 30 lines of TypeScript. Pairs naturally with the anon-key rotation above. Skip if you want simplicity.

---

## Website changes (in `tnbc_info_site` repo)

- [ ] **Remove the Cloudflare Access gate.**
  Cloudflare → Zero Trust → Access → Applications → delete the "TNBC Atlas — internal demo" application.

- [ ] **Remove `noindex` meta tag.**
  Edit `src/layouts/BaseLayout.astro` and delete the `<meta name="robots" content="noindex" />` line.

- [ ] **Remove the "Internal demo — not for public distribution" badge.**
  Edit `src/components/Footer.astro` and remove the amber span at the bottom.

- [ ] **Restore Public API / exports navigation.** Temporarily hidden 2026-06-02 while the API hardening items above are pending. To restore:
  - Uncomment the Public API card in `src/pages/research/index.astro`.
  - Uncomment the Public API link in `src/components/Footer.astro`.
  - Replace the "Not yet released" notice on `src/pages/research/api.astro` with the actual API documentation (querying examples, OpenAPI spec link, bulk-export URLs).
  - Restore affirmative wording in `src/pages/about.astro` and `src/pages/accessibility.astro` referring to bulk exports as available.

- [ ] **Add `robots.txt` and `sitemap.xml`.**
  Install `@astrojs/sitemap` integration; configure `robots.txt` to allow indexing.

- [ ] **Confirm performance + accessibility budgets pass in CI.**
  Lighthouse and axe should already be green from the closed-beta builds; one final check before launch.

---

## Editorial and content

- [ ] **Editorial board constituted and named on `/about/`.**
  Replace the "internal-demo" notice in the funding section once members are confirmed.

- [ ] **First-round editorial review of tier-1 list.**
  PI signs off on the 60-entry tier-1 seed before any record is promoted to `tier=1` in production.

- [ ] **Patient-advocate review of every public-layer page.**
  Required gate before any patient-facing page is publicly visible. Currently 6 patient pages drafted.

- [ ] **Counsel review of `/privacy/` and `/about/`.**
  Tighten legal language; confirm CC BY 4.0 licensing footer; confirm "not medical advice" disclaimers meet jurisdiction requirements.

- [ ] **Backfill bibliography to 2005.**
  See `docs/RUNBOOK-full-backfill.md`. Target: ~25–30k records.

---

## Pipeline cleanup

- [ ] **Coverage audit against the 2023 Frontiers bibliometric.**
  See `docs/RUNBOOK-coverage-audit.md`. Run after the full backfill completes.

- [ ] **Defense-in-depth weekly logical backup.**
  See `docs/RUNBOOK-production-db.md` → Step 5. GitHub Actions workflow uploading `pg_dump` snapshots to R2. *Now lower priority since Supabase Pro PITR backups (see Infrastructure upgrades above) handle the primary database-recovery story — this remains useful as a portable, vendor-independent snapshot.*

- [ ] **Set up uptime monitoring on `https://api.tnbc.info/rest/v1/public_bibliography?limit=1`.**
  UptimeRobot or similar. Alert destination = editorial team email.

- [ ] **Quarterly tier review actually performed.**
  First quarterly tier-review workflow run completes; editorial board reviews the candidates issue.

---

## Operational

- [ ] **Errata page (`/changes/`) has a real entry for the public launch itself.**
  Initial entry: "Site moved from closed beta to public launch. Cloudflare Access removed. Privacy policy approved by counsel."

- [ ] **Document the on-call contact path.**
  Right now there's no real on-call. At public launch decide: just GitHub email notifications? Slack? PagerDuty? Update the `RUNBOOK-orchestration.md` on-call section with the decision.

- [ ] **Soft launch announcement plan.**
  Decide on initial-visitor channels (TNBC-advocacy organizations, ASCO/AACR community lists, oncology journalist relationships) and the timing relative to lifting the Access gate.

---

*Last updated: 2026-05-30. Most recent change: Supabase upgraded to Pro for Custom Domain on `api.tnbc.info`; checklist re-flowed to make the Cloudflare-Pro / orange-cloud / rate-limit chain the main remaining infra block. Add new items as they come up during validation and the final pre-launch period.*
