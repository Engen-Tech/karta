---
id: field-delivery-state-audit
family: field
method: field-audit
cadence: every-release
cost: M
probe: benchmarks/probes/field-delivery-state-audit.py
probe_status: implemented
results: benchmarks/field/results/
provenance: lens=field; merged_from=delivery-ref-topology-linter, eol-hygiene-audit, commit-marker-conformance, marker-conformance, binder-mutation-audit
---

# Consumer-repo delivery state audit

**Question.** Do completed and in-flight deliveries in consumer repos satisfy the declared git invariants and hygiene doctrine — done refs reachable, wave tags ordered, post-merge cleanup performed, work commits marker-tagged, and committed binders free of manual surgery?

## Procedure

1. Scripts live in `benchmarks/flow/` (build once; pure git plumbing + jq, no LLM).
2. GATE: `benchmarks/flow/fixtures/delivery-state/` is a committed bare repo built by a deterministic `build_fixture.sh` (dates pinned via `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`) seeding >=1 instance of EVERY reported violation class — forged done ref (not first-parent-reachable via `git merge-base --first-parent`), wave-tag disorder, wave-base non-ancestor, missing Karta-Accept trailer, leftover `refs/**/in-progress`, surviving branch/tag/worktree post-archive, complete-but-unarchived binder, UNMARKED work commit, dangling marker id, MANUAL-SURGERY binder blob edit, karta-message stash >48h, missing doctrine ref — PLUS two pinned negative cases that must NOT flag: a legit fast-forward landing and a discarded-integration-branch binder (integration never merged AND binder still live on main).
3. `run_all.py` must score 100% detection and 0 false positives on the fixture or consumer results are discarded; a violation class with no fixture case may not be reported from the field.
4. Doctrine anchors are content-greps, never line numbers: grep `skills/karta-deliver/SKILL.md` for 'refs and wave tags remain in git', `skills/karta-build/SKILL.md` for 'Either form satisfies the requirement', `skills/karta-plan/SKILL.md` for 'read-only once committed'; hard-fail the run if a sentence is gone (doctrine changed — update linter first).
5. Per repo (parchmark, gringotts, karta itself), run four auditors:
   1. `lint_delivery_refs.py` — per slug under `refs/karta/**` and `.karta/binders/{,archive/}`: done-ref first-parent reachability from `karta/<slug>/integration` or post-merge main; built->done/failed pairing on archived binders; `wave-<N>-base` ancestor-of `wave-<N>`, tags strictly ordered; Karta-Accept via `git interpret-trailers --parse`; no in-progress refs; zero survivors post-archive; complete implies archived (discard-path binders exempt per the fixture ruling).
   2. `audit_hygiene.py` — enumerate refs/tags/branches/worktrees (`git worktree list --porcelain`)/stashes grouped by slug; classify IN-FLIGHT / STALE (>48h post-delivery measured at the recorded audit_timestamp) / MISSING-DOCTRINE-REF.
   3. `check_markers.py` — `git rev-list done ^wave-base --no-merges`; subject `^\[karta:item-[a-z0-9-]+\]` or Karta-Item trailer; cross-check ids against binder item ids.
   4. `audit_binder_mutations.py` — jq field-level blob diff of post-birth binder commits: RE-PLAN / ARCHIVE-MOVE / METADATA-RETROFIT / MANUAL-SURGERY.
6. ATTRIBUTION (the comparability fix): every finding keys to its delivery cohort — binder slug + delivery date (integration-merge commit or last wave tag) + karta plugin version active at that date from the release ledger.
7. Output committed as `benchmarks/flow/results/<date>-<repo>.json` recording linter_version and audit_timestamp.
8. TRENDING RULES: version-over-version comparison uses ONLY per-cohort invariant-violation counts and per-binder marker coverage for cohorts delivered since the previous audit; stale_artifact_count and oldest_stale_age_days are point-in-time dashboard numbers, never trends; binder-EOL-never-invoked and cleanup-not-run facts are exported as adoption rows for field-feature-adoption-ledger, not counted as violations here; the provenance-recoverable sub-metric (what fraction of done refs carry any machine-recoverable waiver/ceremony record) also lives here, cut from dark-status-surface-probes.
9. Classes with zero field occurrences ever (failed/accepted/kickback — currently 0/54) report 'n/a (fixture-only)', never '0 violations'.
10. On any linter change, recompute all historical cohorts (git history is immutable, backfill is cheap) so every published trend comes from one linter version.

## Metric and comparability

Per-cohort (slug + delivery date + plugin version) invariant-violation counts by class + per-binder marker coverage %, trended only for cohorts delivered since the previous audit; stale_artifact_count, oldest_stale_age_days, missing_doctrine_ref_count, provenance-recoverable % as point-in-time dashboard numbers; manual-surgery mutations per delivered binder (target 0); linter detection on the seeded fixture must stay 100% with 0 false positives; never-exercised classes report n/a-fixture-only.

The metric stays honest across releases through per-cohort attribution: every finding is keyed to slug + delivery date + the plugin version active at that date, so violations land on the release that produced them, not on the release running the audit. The seeded fixture gate blocks reporting any class the linter cannot demonstrably detect, point-in-time dashboard numbers are never trended, and any linter change forces a backfill of all historical cohorts so every published trend comes from a single linter version.

## Inputs

- /mnt/agent-storage/vader/src/parchmark (refs/karta/**, tags, branches, worktrees, stashes, .karta/binders/ + archive/)
- /mnt/agent-storage/vader/src/gringotts (same surfaces)
- /mnt/agent-storage/vader/src/karta (dogfood slugs)
- skills/karta-deliver references (integration-branch.md invariants; SKILL.md refs-survive doctrine, content-grep anchored)
- skills/karta-build/SKILL.md (sanctioned marker forms, content-grep anchored)
- skills/karta-plan/SKILL.md (read-only doctrine, content-grep anchored)
- benchmarks/flow/fixtures/delivery-state/ (new seeded-violation fixture)

## Seed observation (v2.21.0, 2026-07-17)

parchmark: backend-hygiene complete-unarchived with ~19-20 stranded artifacts (10 refs, 4 tags, 5 branches, month-old stash) 11 days post-merge — binder-EOL shipped and never used — and marker coverage 0/5 on backend-hygiene vs 2/3 on deps-upgrade (earlier binders near-100%: a real regression signal the manual dry run caught). gringotts: done-item worktree for wide-screen-layout, 2 stale stashes, upload-reliability landed as fast-forward with no integration merge (pinned negative case), 14/14 markers (both sanctioned forms), and 1 MANUAL-SURGERY (1f548e5 runtime_contract bump). karta itself: 2 MISSING-DOCTRINE-REF slugs (kaizen-frame, codex-119-compatibility). Both repos confirm zero failed/accepted/kickback artifacts across 54 items — those flows have never run in the field; provenance-recoverable reads 0% today.
