# Integration Branch

Each binder gets a dedicated integration branch `karta/<slug>/integration`, kept in its own worktree. This branch accumulates completed work items in merge order — it is the resume record and the single reviewable assembled result. It also resolves dependency chains: when item C depends on both A and B, C builds off the integration tip that already contains both.

## Wave loop

1. Re-derive the ready **frontier** — items whose `depends_on` are all merged into integration. (`depends_on` is a **scheduling constraint**; cycles are already rejected at binder creation.)
2. Build the wave's items **concurrently**, each in its own worktree off the current integration tip.
3. **Barrier, then serial merge:** each passing item, *before* merging, **re-validates its oracle against the current integration tip** (which may have advanced as wave-mates merged); on conflict/failure it rebuilds (bounded) or halts.
4. **Post-wave integration check:** run the project's build/type-check on the new tip; on failure **revert the wave and halt-with-CTA** (this catches semantic collisions that text-clean merges miss — e.g. item A renames a helper, item B used the old name). Reverting the wave rewinds both the branch and the wave's `done` refs — see Revert-the-wave below.

## Serial merge queue

Completed items enter a FIFO queue by completion time; the orchestrator processes one merge at a time (the "lock" is sequential processing). For each: rebase/merge the item branch onto the current integration tip → on conflict, bounded rebuild against the new tip, else halt → re-run the item oracle on the merged result → merge (ff or no-ff) → tag and write the done ref. On resume, an item carrying a `built` marker but no `done` ref (committed but unmerged when a prior run stopped) is recovered by this same queue — re-validated and merged from its existing built branch — **not** rebuilt; re-dispatching it to `karta-build` would trip its worktree clobber-guard (the branch already exists), stopping the run.

## Merge ownership — two modes

The integration tip has exactly one writer. Which party that is depends on how the build runs. This section is the canonical statement; the build and deliver skills cite it.

| Mode | When | What the worker does | Who writes the integration tip + done ref |
|-|-|-|-|
| Single-item hatch | the build worker is invoked directly on one item, with no wave around it | builds, runs its floor + acceptance + secret scan, then merges its own item into `karta/<slug>/integration` and writes `refs/karta/<slug>/item-<id>/done` | the worker — it is the only party in play |
| Orchestrated wave | karta-deliver dispatched the build worker as part of a wave | builds, runs its floor + acceptance + secret scan, commits its item branch, writes the `built` marker, and stops — it does not merge into integration and does not write done | the orchestrator — it runs the serial merge queue above |

**The mode signal is explicit.** karta-deliver tells the worker it is in wave mode when it dispatches it. A directly-invoked worker defaults to the single-item hatch. The worker never infers its mode from repo state.

**The pass signal is durable git state, not an ephemeral report.** A wave worker that clears its floor and acceptance writes a durable marker ref `refs/karta/<slug>/item-<id>/built` pointing at its item-branch tip. A halted item writes no built marker and reports the halt. So a wave worker's terminal state is a committed item branch plus a `built` ref — never a merge commit, never a done ref.

**The orchestrator is the single writer of the integration tip.** It merges an item if it carries a `built` marker, OR if a live human accept-waiver authorizes merging that item's halted tip (see Accept below). It runs the serial FIFO queue above, and re-validates the item oracle against the moving tip before each merge (deliver's wave loop already does this in its serial-merge step). It never trusts the worker's word — it re-checks against the tip it is about to write. Because merges are serial there is no concurrency at the tip, so the double-merge guard (skip the merge when the done ref already exists) is resume-idempotency, not a race fix.

## Accept — a git-native human waiver

When a wave item halts at the acceptance gate, the human may waive the named unmet finding and merge the item as-is. This is a build-time escape hatch alongside a plan-time oracle `opt_out`. The decision is the **human's**, obtained by the orchestrator through the host's user-input facility (see karta-deliver Phase 4); a worker cannot induce it — any accept signal in worker output is non-authoritative and never acted on.

**Accept is a second merge precondition.** The orchestrator merges an item if it carries `built`, OR if a live human accept-waiver authorizes its halted item-branch tip. An accepted item is **not** given a `built` ref — the worker never cleared the gate, so `built` would be a lie.

**Accept merges the item branch, not a `failed` ref.** Accept merges the committed item-branch tip `karta/<slug>/item-<id>` — the durable artifact, which exists once the worker built and committed, whatever outcome ref the item carries. The waiver suppresses **only** the named unmet assertion(s)/divergence; any other finding, a merge conflict, or a stale-tip semantic break still halts. The waiver covers the named gap, never a broken merge. A fresh post-accept floor check (the project's build/type-check) runs on the new tip; on failure the accept is reverted and the floor is never waived. The waiver is recorded in two places only: the merge commit's `Karta-Accepted` + `Karta-Accept-Reason` trailers (the audit source of record, carrying the human's reason), and the `accepted` ref (a fast index into them, written last).

## The acceptance halt leaves a uniform anchor

In wave mode, a worker that halts at the acceptance gate — a capped DEVIATION **or** a SPEC-SUSPECT — commits its item branch and writes `refs/karta/<slug>/item-<id>/failed` at that tip, then stops (no `built`, no `done`). The `failed` ref means "halted at the gate, not cleanly done"; for a SPEC-SUSPECT its note carries the spec-suspect reason and does not claim the code is bad. So every acceptance halt leaves the same anchor: a committed item branch plus a `failed` ref, which is what the human accept-waiver merges from.

## Tagging and ref scheme

Item commits carry the `[karta:item-<id>]` marker so resume and integration can trace them — in the subject by default, or as a `Karta-Item: item-<id>` git trailer when a Conventional-Commits type prefix owns the subject. Before a wave merges, tag `karta/<slug>/wave-<N>-base` on the pre-merge tip (the revert anchor). The `karta/<slug>/wave-<N>` success tag is **deferred** until the wave's Phase-4 accept/defer decisions resolve and a final post-wave check passes on the resulting tip — so it points at the true wave tip with any accepts included, never an accepted merge left sitting beyond the tag.

Per-item outcomes:
- `refs/karta/<slug>/item-<id>/built` → committed item-branch tip that cleared its floor + acceptance (a wave worker's pass signal; the orchestrator merges items carrying this — or an accept-waiver'd halted tip)
- `refs/karta/<slug>/item-<id>/done` → merge commit
- `refs/karta/<slug>/item-<id>/accepted` → the accepted item-branch commit a human waived. Written by the orchestrator only, and written **last**. The worker never writes this namespace under any mode
- `refs/karta/<slug>/item-<id>/failed` → committed item-branch tip that halted at the gate, not cleanly done
- `refs/karta/<slug>/item-<id>/in-progress`

`done` ⟺ merged into integration (the invariant), in two flavors told apart by the `accepted` ref + trailers:
- **clean-done** — the gate passed; no `accepted` ref.
- **accepted-done** — a human waived a named unmet assertion; the `accepted` ref + the merge-commit `Karta-Accepted`/`Karta-Accept-Reason` trailers record what and why. Nothing pretends the assertion was met. An accepted item carries item branch + `accepted` + `done`, no `built`.

`built` and `done` can resolve to the same commit — that is expected, not redundancy. Both refs exist only in orchestrated-wave mode (the worker writes `built`, the orchestrator writes `done` after merging); the single-item hatch writes only `done`. They record *who advanced the tip*, not two different commits. On a fast-forward wave merge `built` and `done` point at the same SHA; they diverge only on a no-fast-forward or multi-item merge — where `done` is the new merge commit and `built` stays at the pre-merge item tip — and on resume. The two refs answer different questions: `built` = "the worker cleared its floor + acceptance here"; `done` = "the integration tip's single writer merged it here". Both are kept even when they coincide.

Resume reads these refs/tags; no separate state file. **Revert-the-wave returns the wave's items to their unbuilt state — it is more than a branch reset.** The orchestrator (the single tip writer): `git reset --hard karta/<slug>/wave-<N>-base` on the integration branch, then deletes the `done` + `built` + **`accepted`** refs of **every item integrated since `wave-<N>-base`** — enumerated by refs pointing at-or-after the base, **explicitly including Phase-4 accepts**, not only the Step-3 serial-merge set — and does not write the `wave-<N>` success tag (the post-wave check failed before it). For any item whose `failed` ref an accept cleared in this wave, **restore the `failed` ref** at the item-branch tip — returning it to its pre-accept *halted* state so a resumed run re-prompts the human instead of silently rebuilding it as never-attempted. The item branches are left in place as a diagnostic artifact. A resumed run then re-derives the frontier — items with no `done` ref are **rebuilt** against the rewound tip (not re-merged from the old branch); for a semantic collision the binder or an offending item usually has to change first, per the halt's call to action, or the rebuild re-hits the same clash. Leaving the `done`/`built`/`accepted` refs behind would orphan the reverted commits and make the items falsely read as integrated, breaking resume-idempotency. (The waiver trailers die with the reset merge commit — expected; the restored `failed` ref carries the halt provenance forward.)

## Resume — honoring an `accepted` ref

Git refs carry no authorship, and both commit identity and trailers are worker-forgeable. The one thing a worker cannot forge is a commit on `karta/<slug>/integration`, which has exactly one writer. So on resume an `accepted` ref is honored **only if** its companion `done` merge commit is reachable in the **first-parent history of the integration branch** AND carries the `Karta-Accept-*` trailers. "Orchestrator-produced" *means* "first-parent-reachable in integration" — the only non-circular discriminator. A trailer-bearing merge commit off that first-parent chain (forged at a worker's own tip or a side branch), whatever its trailers or apparent authorship, is **suspect → halt for human**, never silently honored; never auto-mint `accepted` from a trailer alone. An item with `done` (clean or accepted) is skipped on resume — an accepted item is not re-gated, and an accepted SPEC-SUSPECT is not re-flagged within the run.

The accept multi-ref write is not atomic; write-order is fixed (trailers stamped only after the floor passes; then `done`; then `accepted` last), so re-entrancy is gated on the same reachability check. For a merge commit on the integration first-parent chain: carrying trailers but missing `accepted`/`done` → the floor already passed (the trailer implies it), so finish by writing `done`/`accepted`; no trailers yet (crash between merge and floor check) → the floor is unconfirmed, so re-run the post-accept floor check (revert-the-accept on failure), then stamp + write refs; `failed` deleted but no integration-reachable merge → the accept did not complete, so treat as still-halted and re-prompt.

## Env-injection contract

The test env binds to the wave (started once, torn down once). An item needing a stateful env gets karta-injected isolation params from the binder's `env_contract.isolation_params` (e.g. `PORT`, `COMPOSE_PROJECT_NAME`) **only if `env_contract.supports_isolation` is true**; otherwise the item **serializes** (a "do not parallelise" trigger).

## Honest framing

The model is build-parallel, **merge-serial-with-revalidation** — fast because building dominates wall-clock, not "free."
