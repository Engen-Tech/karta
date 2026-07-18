---
id: field-feature-adoption-ledger
family: field
method: telemetry-mining
cadence: every-release
cost: M
probe: benchmarks/probes/field-feature-adoption-ledger.py
probe_status: implemented
results: benchmarks/field/results/
provenance: lens=field; merged_from=feature-adoption-ledger
---

# Feature adoption ledger with mandatory attribution

**Question.** Which shipped, repo-observable karta features are actually used in each consumer repo — with every absence attributed deterministically — and how fast are features adopted when the repo is active?

## Procedure

1. Maintain `benchmarks/field/feature-manifest.json`: one entry per observable feature = `{id, karta_version, ship_date, detection:{kind: grep|jq|ref, pattern, path}, durable: true|false}`.
2. `ship_date` = committer date of the version-bump commit: `git -C /mnt/agent-storage/vader/src/karta log --grep 'chore(release): bump plugin version to <ver>' --format=%cI -1`.
3. Ephemeral-state detections (`refs/karta/**/failed` — karta-deliver deletes the failed ref on accept and rewrites it in revert-the-wave, skills/karta-deliver/SKILL.md ~L85/L130, and git keeps no reflog for `refs/karta/**`) MUST be `durable:false`: they report current-state presence only and are EXCLUDED from adoption-lag and from AVAILABLE-BUT-UNUSED. Detect the failure path via durable traces instead: accepted refs (persist post-archive) and waiver/override markers in tracked content.
4. MANIFEST GATE (enforced, not asserted): `audit_adoption.py` first enumerates releases via `git log --grep 'chore(release)' --format='%cI %s'` and exits 2 if any release newer than the newest manifest entry lacks both an entry and an explicit `{version: V, observable: false}` sentinel.
5. ACTIVITY EVENTS pinned to exactly three deterministic commit classes in the consumer repo (committer dates): subject `^plan(karta):`, subject `^chore(karta): archive binder`, and merge commits of `karta/*/integration` branches. Nothing else counts.
6. FIRST-USE: tracked-content detections via `git log -S<pattern> --format='%H %cI'` (earliest hit; stable here because the owner's git doctrine forbids rebase/force-push); durable-ref detections via committer date of the ref target.
7. Once a first_use lands in a committed result JSON it is immutable — later audits carry it forward (append-only ledger), so ref cleanup or marker removal can never flip used->unused.
8. CLASSIFICATION (2 deterministic classes, replacing the undecidable PREDATES/REPO-IDLE split): NOT-EXERCISED-SINCE-SHIP (zero activity events after ship_date; record days_since_last_activity so a human can eyeball idle vs retired) and AVAILABLE-BUT-UNUSED (>=1 activity event after ship_date, detection never fired) — the only alarm class.
9. OUTPUT: commit `benchmarks/field/results/<ISO-date>-<repo>.json` containing the full per-feature attribution table; headline = AVAILABLE-BUT-UNUSED ids plus count/denominator with denominator always shown; adoption_lag_days per feature; median only when n>=5; negative lags allowed, labeled pre-release-dogfood (real precedent: 1.18.0 was delivered by karta on the real repo before its version bump), excluded from the median.

## Metric and comparability

AVAILABLE-BUT-UNUSED feature ids with count/denominator always shown, per repo + per-feature adoption_lag_days (median only at n>=5; negative lags labeled pre-release-dogfood, excluded from median); release-over-release comparison = per-feature status-transition diff on stable feature ids, never aggregate-count deltas.

Comparability rests on stable feature ids and an append-only ledger: release-over-release deltas are per-feature status transitions, never aggregate counts, so a growing manifest cannot masquerade as adoption change. The manifest gate forces an entry (or an explicit observable:false sentinel) for every release, keeping the denominator complete; committed first_use values are immutable and carried forward, so later cleanup can never flip used->unused; and durable:false detections are excluded from lag and alarm classes so ephemeral state never distorts the trend.

## Inputs

- benchmarks/field/feature-manifest.json (new, grows one entry — or an observable:false sentinel — per release as a release-checklist forcing function)
- karta repo release timeline (chore(release) version-bump commits)
- consumer repo binder JSONs, refs, configs, git grep surface (parchmark, gringotts)
- benchmarks/flow/seed-map-2026-07-17.json features_never_exercised lists (committed copy of the audit map)

## Seed observation (v2.21.0, 2026-07-17)

gringotts (active this week on 2.21.0) scores 4 AVAILABLE-BUT-UNUSED — karta-validate (despite design-sourced binders carrying design_facts), debt markers, smoke oracle type, and the failure path (tracked via durable traces: accepted refs + markers; current-state failed-ref presence is durable:false); parchmark attributes most absences as NOT-EXERCISED-SINCE-SHIP (no activity since 07-06) except binder-EOL and debt markers, which were live-and-ignored. Median adoption lag for adopted features is under one day (settings binders 45 min after 1.8.0, kaizen 30 min after 1.13.0) — so persistent non-adoption during active periods is a prune-or-fix signal, not noise.
