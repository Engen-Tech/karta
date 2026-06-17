# karta empty-diff verdict — end-to-end run

**Date:** 2026-06-17. **Purpose:** prove the explicit handling for an empty-but-readable diff (a work item that produced zero changes) end-to-end on the testbed, with live git evidence. Two layers: the build-time guard in `karta-build` (`build:acceptance` precondition) and the gate-time BLOCKED verdict in `karta-acceptance-reviewer`. Design: `docs/specs/2026-06-17-empty-diff-verdict-design.md`. Testbed reset to pristine `d68769b` after capture.

## Binder and setup

Binder `empty-diff-e2e` (validated — the file-overlap guard first rejected `whiff`/`empty` sharing `metrics.js` in one wave, confirming fix 7 still bites; resolved by giving them distinct `touches`). Integration branch built off `main`, with item `seed` (a real `metrics.js` change) merged to give a non-empty base. Integration tip after seed: `dce8c44`.

|-|-|
| Item | Role in the test |
|-|-|
| `seed` | a real change — the non-empty base and the contrast case |
| `whiff` | branched off the tip, makes no change — exercises the build guard |
| `empty` | branch identical to the tip — handed to the gate to exercise BLOCKED |

## Test A — build-time guard (a whiff)

A worker on `item-whiff` ran the Phase-6 precondition before the gate:

```
git diff --quiet "$integration"...HEAD   ->  exit 0   (no change)  => GUARD HALTS, write no ref
```

Contrast, a real item versus its wave base:

```
git diff --quiet wave-1-base...item-seed ->  exit 1   (change present) => guard PASSES, gate runs
```

After the halt, `whiff` carries **no** `built`, `done`, or `failed` ref — exactly as designed (the branch equals its base, so there is no distinct tip to anchor a ref to). The worker writes nothing and surfaces "produced no changes." On resume the frontier re-derives `whiff` (no `done`), so it is re-dispatched, never silently skipped.

## Test B — gate-time BLOCKED (an empty diff reaches the reviewer)

The actual `karta-acceptance-reviewer` agent was dispatched on the empty diff range `integration...item-empty` (item tip `dce8c44` == integration tip `dce8c44`), with the binder item `empty` carrying a real assertion ("exports an average function"). It was given the inputs neutrally — no hint toward any verdict.

Following its updated instructions, it:

- ran the **non-empty-diff precondition first** and saw exit 0 / zero hunks,
- returned **Verdict: BLOCKED** — not DEVIATION (the old fall-through) and not CONFORMANT,
- **dispositioned no assertion** against the empty diff ("Not performed. The precondition failed."),
- classified the cause as a **whiff** by evidence (the `touches` file `average.js` exists on neither tip, so the change is not already present),
- set the envelope `verdict: blocked`, `kickback_to: null`, reason "empty diff in range (zero hunks); whiff — re-dispatch worker," and stated it is **not an accept/defer candidate** (no diff to merge, no named assertion to waive).

The verbatim envelope:

```yaml
verdict: blocked
summary: "Empty-but-readable diff for item 'empty' (item tip == integration tip dce8c44, no average.js anywhere). Nothing to disposition; reads as a whiff — work not delivered."
routing_hints:
  next: null
  kickback_to: null
  reason: "empty diff in range (zero hunks); whiff — re-dispatch worker for item 'empty'"
top_blockers: ["empty-diff: integration...item-empty resolves to same commit dce8c44, no average.js"]
```

## What this run proves

The empty-but-readable diff now has a defined outcome at both layers: the build guard catches a whiff before the gate runs (deterministic `git diff --quiet`, no ref written, re-derivable on resume), and the gate returns BLOCKED — deterministically, with no assertion dispositioned and no guessed verdict — for any empty diff that reaches it. The reviewer named the cause (whiff vs already-present) from evidence and routed it as a halt, not a kickback and not an accept/defer candidate. The verdict no longer falls through to DEVIATION.

## Methodology

The acceptance reviewer ran as a fresh read-only dispatch on the diff range, given its inputs without a hint toward the verdict — so the BLOCKED result is the agent following its instructions, not coaching. Testbed reset to pristine `d68769b` (worktree removed, all `karta/empty-diff-e2e/*` branches/tags/refs deleted, `.karta/` cleaned) after this record was captured.
