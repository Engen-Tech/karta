---
name: karta-deliver
description: Deliver a karta binder by building its work items in parallel waves onto a per-binder integration branch, serializing only where correctness or collision demands it; resume is git-native; ends at the assembled integration branch (no PR). Trigger phrases: "deliver this binder", "run the binder", "karta-deliver `<binder>`".
---

karta-deliver takes a **validated binder** and builds all its work items onto the per-binder integration branch in **parallel waves**. Default is parallel; it drops to serial only when running two items together would produce a wrong or broken result. The output is a single assembled integration branch the user reviews and merges — no PR, no push, nothing else.

The integration branch is also the resume record. karta tracks every item's outcome through commit markers, wave tags, and the `refs/karta/` ref namespace (see [references/integration-branch.md](references/integration-branch.md)). A later run detects leftovers from a prior partial run and offers to continue or clear.

The binder (`.karta/binders/<slug>.json`) is the cross-skill contract and is **immutable while a wave runs**. karta-deliver reads it; it never writes to it. For its full field reference, see [references/binder-reference.md](references/binder-reference.md). The build primitive for each item is `karta-build`. The parallelism rules live in [references/parallelism-gates.md](references/parallelism-gates.md).

---

## Phase 0 — Preflight  `deliver:preflight`

**Validate the binder.** Run:

```bash
uv run skills/karta-plan/scripts/validate_binder.py --binder <path>
```

This checks schema validity, dependency cycles, and dangling `depends_on` references. On failure, bail with the validator's output — no "continue anyway?".

**Single-item binder — skip deliver.** When the binder has exactly one work item, hand straight to `karta-build`. There is no wave to schedule and no integration branch to assemble across multiple items. This is the "just this once" hatch: fast, unceremonious, correct.

**Detect leftovers from a prior run.** Check for existing `karta/<slug>/...` wave tags and `refs/karta/<slug>/...` item refs per [references/integration-branch.md](references/integration-branch.md). When leftovers exist, offer the user two choices:

- **Resume** — continue from the last completed wave; items whose `done` ref exists are skipped.
- **Clear** — remove the wave tags, item refs, and the integration branch, then start fresh.

Never silently resume or silently clear — the user chooses.

---

## Phase 1 — Integration branch  `deliver:integration`

Create or locate `karta/<slug>/integration` in its own worktree per [references/integration-branch.md](references/integration-branch.md).

- **First run:** branch `karta/<slug>/integration` from the repo's default branch (detect via `git remote show origin`'s `HEAD branch:` line, else whichever of `main`/`master` exists).
- **Resume:** locate the existing branch. The integration branch already contains everything from prior completed waves — that is what makes git-native resume work.

The integration worktree is separate from per-item worktrees. Keep it alive for the full deliver run; tear it down at the end.

---

## Phase 2 — Wave loop  `deliver:waveloop`

The wave loop is the core mechanism. Its authoritative description is [references/integration-branch.md](references/integration-branch.md). The four steps:

**Step 1 — Derive the frontier.** Re-derive the ready frontier: items whose `depends_on` are all merged into integration (i.e. each dep's `refs/karta/<slug>/item-<dep-id>/done` ref exists). On resume, items already in `done` are excluded. If the frontier is empty and items remain unbuilt, there is a dependency bottleneck — surface it and halt.

**Step 2 — Build concurrently.** Dispatch a `karta-build` per frontier item using the host's parallel primitive. Each item gets its own worktree, branched off the current integration tip. If no parallel primitive is available, build serially in frontier order.

Before dispatching, apply the gates from [references/parallelism-gates.md](references/parallelism-gates.md) to decide what serializes within this wave:

| Gate | Trigger |
|-|-|
| Dependency edge | dep not yet merged (correctness) |
| Shared / order-sensitive resource | wave-mates touch the same stateful resource — inferred file-overlap or a declared annotation |
| Stateful env without injectable isolation | the repo's env command can't be parameterized |
| File-collision risk | wave-mates likely edit the same files |
| Explicit `serialize` | the binder marks the item must-serialize |

An item with `serialize: true` runs alone — no parallel build mates for that slot.

**Step 3 — Barrier, then serial merge.** Wait for all wave builds to complete or halt (barrier). Then process passing items one at a time through the serial merge queue from [references/integration-branch.md](references/integration-branch.md): each item re-validates its oracle against the **current** integration tip (which may have advanced as wave-mates merged), then merges. On conflict or re-validation failure, do a bounded rebuild against the new tip or halt.

Before the merge pass, tag `karta/<slug>/wave-<N>-base` on the pre-merge integration tip — this is the revert anchor for partial-wave failure (see `deliver:lifecycle`).

After each merge: write `refs/karta/<slug>/item-<id>/done` → the merge commit.

**Step 4 — Post-wave integration check.** Run the project's build/type-check on the new integration tip. On failure, revert the integration branch to `karta/<slug>/wave-<N>-base` and halt with a call to action — this catches semantic collisions that text-clean merges miss (e.g. item A renames a helper, item B used the old name). After the post-wave check passes, tag `karta/<slug>/wave-<N>` on the completed tip.

Repeat the loop for the next frontier until all items are built or a halt stops the run.

---

## Phase 3 — Env binding  `deliver:env`

Start the project's env (`env_contract.command`) **once per wave**, before the wave's builds dispatch. Per [references/binder-reference.md](references/binder-reference.md):

- When `env_contract.supports_isolation` is true, inject `env_contract.isolation_params` (e.g. `PORT`, `COMPOSE_PROJECT_NAME`) per item so concurrent builds get isolated environments.
- When `supports_isolation` is false, items that need a stateful env are serialized — running them concurrently would produce interference. The gate in the wave loop (`deliver:waveloop`) catches this.

Tear the wave env down once at the end of the wave, after the post-wave check (Step 4). Do not tear it down on partial failure — the post-wave check still needs it. When `karta-build` runs a visual oracle and uses the wave env, it must not tear it down itself (the orchestrator owns the lifecycle). Per [references/integration-branch.md](references/integration-branch.md), `karta-build` leaves a provided wave env alone.

---

## Phase 4 — Lifecycle  `deliver:lifecycle`

**Partial-wave failure.** When one or more items halt during a wave:

- Items that passed merge normally.
- The failing item halts with a call to action naming the cause.
- Only the failing item's dependents wait; the rest of the frontier continues.
- After the wave, the user may **revert the wave** (`git reset --hard karta/<slug>/wave-<N>-base`) or continue with the partial result.

**Cleanup.** At the end of each wave:

- Remove worktrees for items that passed or were abandoned.
- Tear down any wave env this run started.
- **Preserve the failing item's worktree and print its path.** The user needs it to diagnose and retry.

Committed item branches and the integration branch persist. A later `karta-deliver` run detects them via preflight (`deliver:preflight`) and offers to resume.

---

## Phase 5 — Cost education  `deliver:cost`

When the binder scope is large (many items, estimates of L, or long dependency chains), echo the plan-time cost note before the wave loop starts:

> This scope will burn time and money before you see tangible results. Consider a small first slice — one to three items — to validate the direction before delivering the rest.

This is education, not a gate. The user may proceed immediately. If they do, start the wave loop.

---

## Phase 6 — Report back  `deliver:report`

After the final wave (or halt), report:

- **Waves run** — wave numbers and item counts per wave.
- **Items merged** — ids and the integration tip commit they landed on.
- **Items halted** — ids, causes, and the path to each preserved worktree.
- **The integration branch** — `karta/<slug>/integration` is the single reviewable assembled result. No PR has been opened. The user reviews this branch and merges it.

---

## Gotchas

- **Build-parallel, merge-serial-with-revalidation.** Concurrent builds save time; serial merging with oracle re-validation keeps the integration tip correct. "Serial" is fast (a FIFO queue), not free (each item re-checks its oracle against the tip that just moved).
- **The binder is immutable while a wave runs.** You can edit the binder between waves (then re-validate it), but not while a wave is in flight.
- **Backlog curation is the user's job.** karta-deliver executes the binder as written. It does not add, remove, or reorder work items — that is `karta-plan`'s job.
- **Resume is git-native.** No state file. karta recovers from the tags and refs in the `karta/<slug>/` and `refs/karta/<slug>/` namespace per [references/integration-branch.md](references/integration-branch.md). Preflight (`deliver:preflight`) detects them; the user chooses to resume or clear.
- **The human enters delivery only on escalation.** The safety gate caps at 3 attempts and escalates; the acceptance gate caps at 2 and halts with a call to action. Outside those caps, karta self-corrects. The user is not consulted mid-wave except on partial-wave failure (their choice to revert or continue) or a `deliver:preflight` resume/clear prompt.
- **A single-item binder skips deliver.** Hand directly to `karta-build`. There is no wave to schedule, no integration branch to assemble across items.
- **No PR — ever.** The terminal state is a tagged, assembled integration branch. No `gh`/`glab`/`tea`, no review transition.
- **Post-wave check reverts on failure.** The pre-merge tag (`wave-<N>-base`) is the revert anchor. A semantic collision the floor missed (e.g. two items independently modifying the same helper) is caught here, not silently merged.
- **Preserve failing worktrees.** Clean up passing and abandoned worktrees; leave the failing item's worktree in place and print the path.
