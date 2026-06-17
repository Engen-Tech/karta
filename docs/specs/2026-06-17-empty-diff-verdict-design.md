# Empty-but-readable diff — explicit gate verdict

**Date:** 2026-06-17. **Status:** design, approved (verdict choice made by the user: BLOCKED).

## Problem

karta's acceptance gate judges a work item's diff against the item's `oracle`. It has an explicit verdict for an *unreadable* diff (the `git diff` errors, e.g. exit 128 on a bad ref) — that is BLOCKED. It has **no** explicit rule for a diff that is **readable but empty**: the `git diff` succeeds with exit 0 and shows zero changes. Today that case falls through to the per-assertion disposition, which guesses — usually DEVIATION, but not reliably, because there is nothing to disposition. The verdict is undefined.

## Why an empty diff happens

karta has no "no-op" work-item type. Every binder item is a unit of change; an `opt_out` oracle means "do not verify," not "make no change." So an empty diff is never "correct." It means one of exactly two things:

- **A whiff** — the worker delivered nothing (no commits, or commits that net to no change versus the base).
- **Already present** — a sibling item in the same wave (or pre-existing code) already landed the change, so when the gate re-validates the item branch against the *moved* integration tip, the two differ by nothing.

Both warrant attention. Neither is ever a silent pass.

## The fix — two layers

### Layer 1 — build-time guard (catches the whiff at the source)

`karta-build`, at the top of the acceptance loop (`build:acceptance`, Phase 6) and before it dispatches the gate, confirms the item branch actually changed something versus its base:

```
git diff --quiet <integration-base>...HEAD   # exit 0 = no change, exit 1 = change present
```

If there is no change, the item is **not delivered**. The worker does **not** dispatch the gate, does **not** write `built`, and halts with the report cause "produced no changes." A build that changed nothing is not built.

This halt leaves **no new ref**. Unlike a gate halt (capped DEVIATION / SPEC-SUSPECT), there is no distinct item tip to anchor a `failed` ref to — the branch equals its base. So an empty-build worker is just a halted worker per `deliver:waveloop` Step 3: it writes no `built` marker and reports the halt. The orchestrator surfaces it as halted-incomplete.

### Layer 2 — gate precondition (the deterministic backstop)

`karta-acceptance-reviewer` runs an explicit precondition **before** any assertion disposition. It already runs `git diff <range>` to see the changes; now it classifies the result first:

- diff errors / unreadable → **BLOCKED** (the existing case: "no readable diff").
- diff readable but **empty** (exit 0, zero hunks) → **BLOCKED**, reason "the work item produced zero changes in range — there is nothing to disposition."
- diff readable and non-empty → proceed to per-assertion disposition as today.

The reviewer never dispositions assertions against an empty diff and never guesses a verdict. Because karta has no no-op item, an empty diff is never CONFORMANT. This precondition is deterministic — a length check, not a judgment — and burns no loop attempt (BLOCKED never does).

Layer 2 is the authoritative catch for the **already-present** case: the orchestrator re-validates each item's oracle against the moving integration tip in `deliver:waveloop` Step 3, dispatching the reviewer directly with no Phase-6 wrapper around it. There the build-time guard does not apply, so the gate's own precondition is what makes the empty diff explicit.

## The verdict decision — BLOCKED, not DEVIATION

The user chose BLOCKED. Rationale:

By the time an empty diff reaches the gate, the build-time guard has already caught the whiff case, so the realistic cause is **already-present**. Kicking that back to the worker as a DEVIATION ("you delivered nothing, try again") is counterproductive — the worker cannot fix a change that is already on the tip; it would produce a duplicate or nothing, burn both attempts, and only then halt. BLOCKED halts for a human immediately, costs no retry, and is honest: the gate has no work product to judge.

BLOCKED also fits the verdict's existing meaning — "the gate has no diff to judge" is the same family as the existing "no readable diff." And it keeps the human channel honest: an accept-waiver suppresses a **named unmet assertion**; with no diff there is no assertion to waive and no tip to merge, so the accept/defer hatch does not apply (see below).

The rejected alternative (DEVIATION → kick back → cap → existing Phase-4 accept/defer hatch) was simpler reuse but wastes up to two attempts on a change the worker cannot fix.

## Halt handling and the human's ways forward

A BLOCKED-empty halt takes the existing BLOCKED path (`karta-verify`: "halt with the blocking reason"), **not** the Phase-4 accept/defer hatch. The call to action names the cause and the human's options:

- **Re-dispatch** the worker — if a whiff somehow reached the gate past the build guard.
- **Drop or amend via karta-plan** — if the item is genuinely subsumed (its change is already present) or was a no-op in the plan. Removing or re-scoping the item is the durable fix; the binder is read-only to build, so this is a plan-time decision.

There is deliberately **no** "mark subsumed-done" mechanic and **no** accept-waiver for an empty diff — both would be new machinery, and neither fits "there is no diff." A human who wants the item closed re-plans it (remove it, or convert it to an explicit `opt_out` with a reason).

## Resume

An item that halts BLOCKED-empty leaves no `built`, no `done`, no `failed`. On resume the orchestrator re-derives the frontier; this item has no `done`, so it is re-dispatched and re-built. If it whiffs again it halts BLOCKED-empty again — surfaced loudly each time, never silently done. If the human re-planned it away, it is gone from the binder. This is coherent with the existing resume model and adds no new state.

## What this deliberately does not add

- No new verdict — reuses BLOCKED.
- No new ref namespace — empty-build leaves no `failed`; gate-time BLOCKED halts via the existing BLOCKED path.
- No new Phase-4 hatch routing — BLOCKED-empty is not an accept/defer candidate.
- No "subsumed-done" auto-close mechanic.

## Files to change

Canonical + byte-identical copies must move together (enforced by `scripts/check_shared_copies.py`).

|-|-|
| File | Change |
|-|-|
| `agents/karta-acceptance-reviewer.md` | Add the empty-diff precondition before assertion disposition; extend the BLOCKED verdict definition and the report/CTA to name the empty-diff trigger and ways forward. Standalone. |
| `skills/_shared/verification-gate.md` (+ 4 reference copies under `karta-verify`, `karta-build`, `karta-deliver`, `karta-validate`) | Add a short note: an empty-but-readable diff is BLOCKED, not a pass or a deviation; the two causes; the build-guard-then-gate division of labor. |
| `skills/karta-verify/SKILL.md` | Extend the BLOCKED reason wording ("a required input is unreadable") to include "or readable but empty (the item produced zero changes)." Standalone. |
| `skills/karta-build/SKILL.md` | Add the Layer-1 build guard at the top of `build:acceptance` (Phase 6): empty diff vs base → halt, no gate, no `built`. Standalone. |
| `skills/karta-deliver/SKILL.md` | Note in Phase 4 that a BLOCKED-empty halt is not an accept/defer candidate — its ways forward are re-dispatch or drop/amend via karta-plan. Standalone. |

## Verification

- 4 validators green (PLUGIN INTEGRITY, SHARED COPIES IN SYNC, validate_binder, scan_secrets).
- End-to-end on the testbed: trigger an empty diff both at build (whiff → Layer-1 halt, no `built`) and at the gate (already-present at the moved tip → Layer-2 BLOCKED). Reset the testbed pristine after.
