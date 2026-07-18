---
id: dark-status-surface-probes
family: dark
method: deterministic-probe
cadence: every-release
cost: M (initial authoring of 12+ fixture states; per-run S thereafter)
probe: benchmarks/probes/dark-status-surface-probes.py
probe_status: implemented
results: benchmarks/dark/results/
provenance: "lens=dark; merged_from=stranded-state-actionability-matrix, ref-forgery-detection-rate, waiver-provenance-legibility"
---

# Status and Stop-gate truth on abnormal states

**Question.** When git state is stranded, forged, or waived, do karta's scripted surfaces (karta_next, serve_status, the delivery Stop-gate) surface the truth with an actionable next step — or render dead ends, silence gates, and badge waived work as PASSED?

## Procedure

1. `benchmarks/fixtures/stranded-states/make_state.sh <case-id>` fabricates each case in a throwaway repo with plain git (worktree, refs via `git update-ref`, binder JSON). S7/S8 are synthesized from scratch inside the script (shapes recorded as git commands), NEVER copied from live parchmark/gringotts at run time.
2. Alongside it, `benchmarks/fixtures/stranded-states/expected.json` commits one frozen grading anchor per case: `{case: {warning_regex, expect_command_nonnull, stop_gate_exit}}`. Anchor edits are only legal in the same commit as the surface change they track, and the anchor diff is included in the bench result.
3. Per case run three probes:
   1. `python3 skills/karta-status/scripts/karta_next.py --json` in the fixture repo -> ACTIONABLE iff `next_action.command` is non-null OR any `warnings[]` entry matches the case's committed warning_regex — no free-form judgment.
   2. `echo '<synthetic Stop payload>' | python3 hooks/scripts/guard_delivery_stop.py` -> compare exit code (0/2) against `stop_gate_exit`.
   3. serve_status state via a pinned import probe — `python3 -c 'import serve_status; print(serve_status._STATE_META[...])'` with the exact symbol names recorded in expected.json. If the import fails the case grades FAIL-LOUD (bench errors, never silently skips), and the durable fix is a `--snapshot-json` mode on `skills/karta-status/scripts/serve_status.py` added on first breakage.
4. Family S (8 stranded states), graded per anchors:
   - S1 built-unmerged mid-wave
   - S2 all-done-unmerged awaiting human merge
   - S3 accepted-done item
   - S4 crashed-build branch with no refs
   - S5 corrupt/non-dict binder JSON
   - S6 binder in HEAD but deleted from working tree (gate-vs-engine set-agreement check)
   - S7 post-merge leftovers (parchmark backend-hygiene replica, synthesized)
   - S8 done-item worktree still mounted (gringotts replica, synthesized)
5. Family F (4 forgeries via `git update-ref`): F1 done ref not first-parent-reachable from integration; F2 accepted ref whose target merge lacks Karta-Accept trailers; F3 built ref on no item branch; F4 done ref for an item absent from the binder — DETECTED iff karta_next warns per anchor OR the Stop-gate exits 2. Plus one static binary: `grep -rEn 'merge-base|--first-parent|rev-list' skills/karta-deliver/ hooks/scripts/` -> scripted-reachability-check-exists yes/no.
6. Family P (fixture-only; the live cross-repo provenance scan from the draft is cut — its sub-metric moves to field-delivery-state-audit): P1 the accepted-done fixture must surface a state key distinct from clean done in `karta_next --json` AND a `_STATE_META` badge word that is not PASSED; P2 the fabricated accepted-done item must be fully reconstructible (what was waived, why) from refs + merge trailers alone with `git for-each-ref` + `git log --format=%(trailers)` — binary.
7. Also assert `set(REF_STATES in guard_delivery_stop.py) == set of ref names the engine writes per skills/_shared/integration-branch.md` (today this catches the missing 'accepted' entry).
8. Emit the per-case stable-ID matrix `{S1..S8, F1..F4, P1..P2, gate_engine_ref_agreement}` to `benchmarks/results/<version>-status-truth.json`; new cases get new IDs, existing IDs never change meaning.

## Metric and comparability

Per-case stable-ID matrix (S1-S8 actionability, F1-F4 forgery detection, P1 accepted-distinct binary, P2 provenance-reconstructible binary, gate_engine_ref_agreement, scripted-reachability-check-exists) — diffed release-over-release by case ID, never by bare fraction.

Honesty across releases comes from the stable case IDs: results are diffed per ID, never as a bare fraction, so adding new cases (which get new IDs) cannot dilute or inflate the picture, and existing IDs never change meaning. Grading anchors are frozen in the committed expected.json, anchor edits are only legal in the same commit as the surface change they track, and the anchor diff ships inside the bench result — so a grading change can never be smuggled in separately from the behavior change it reflects.

## Inputs

- skills/karta-status/scripts/karta_next.py + skills/karta-status/scripts/serve_status.py
- hooks/scripts/guard_delivery_stop.py (REF_STATES)
- benchmarks/flow/seed-map-2026-07-17.json status/deliver dark_areas (committed copy of the audit map)
- benchmarks/fixtures/stranded-states/ (new: make_state.sh + expected.json)
- skills/_shared/integration-branch.md:56 ('nothing pretends' contract) and :64 (reachability doctrine)

## Seed observation (v2.21.0, 2026-07-17)

Expect ~2/8 actionable today: S1 reproduces the documented dead-end fallback with empty warnings in the precise state the Stop-gate exists to catch; S5 vanishes from status while crashing the swallowed subprocess; S7/S8 draw no cleanup suggestion — matching what rotted 11 days in parchmark. Forgeries: 0/4 detected — a forged done ref actively SILENCES the Stop-gate's built-without-done check, and no scripted reachability check exists. Provenance: serve_status.py:182 _STATE_META badges accepted-done as PASSED unconditionally, and the real dogfood waves-2-3 ceremony downgrade is machine-unrecoverable today. Verified on disk: karta_next.py --json emits next_action.command + warnings; guard_delivery_stop.py exits 0/2 on stdin payloads; REF_STATES omits 'accepted'.
