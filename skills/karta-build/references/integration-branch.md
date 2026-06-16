# Integration Branch

Each binder gets a dedicated integration branch `karta/<slug>/integration`, kept in its own worktree. This branch accumulates completed work items in merge order — it is the resume record and the single reviewable assembled result. It also resolves dependency chains: when item C depends on both A and B, C builds off the integration tip that already contains both.

## Wave loop

1. Re-derive the ready **frontier** — items whose `depends_on` are all merged into integration. (`depends_on` is a **scheduling constraint**; cycles are already rejected at binder creation.)
2. Build the wave's items **concurrently**, each in its own worktree off the current integration tip.
3. **Barrier, then serial merge:** each passing item, *before* merging, **re-validates its oracle against the current integration tip** (which may have advanced as wave-mates merged); on conflict/failure it rebuilds (bounded) or halts.
4. **Post-wave integration check:** run the project's build/type-check on the new tip; on failure **revert to the wave's pre-merge tag and halt-with-CTA** (this catches semantic collisions that text-clean merges miss — e.g. item A renames a helper, item B used the old name).

## Serial merge queue

Completed items enter a FIFO queue by completion time; the orchestrator processes one merge at a time (the "lock" is sequential processing). For each: rebase/merge the item branch onto the current integration tip → on conflict, bounded rebuild against the new tip, else halt → re-run the item oracle on the merged result → merge (ff or no-ff) → tag and write the done ref.

## Tagging and ref scheme

Item commits carry the `[karta:item-<id>]` subject marker. Before a wave merges, tag `karta/<slug>/wave-<N>-base` on the pre-merge tip (the revert anchor). After the post-wave check passes, tag `karta/<slug>/wave-<N>` on the completed tip.

Per-item outcomes:
- `refs/karta/<slug>/item-<id>/done` → merge commit
- `refs/karta/<slug>/item-<id>/failed` → failing branch tip
- `refs/karta/<slug>/item-<id>/in-progress`

Resume reads these refs/tags; no separate state file. Revert-the-wave = `git reset --hard karta/<slug>/wave-<N>-base` on the integration branch.

## Env-injection contract

The test env binds to the wave (started once, torn down once). An item needing a stateful env gets karta-injected isolation params from the binder's `env_contract.isolation_params` (e.g. `PORT`, `COMPOSE_PROJECT_NAME`) **only if `env_contract.supports_isolation` is true**; otherwise the item **serializes** (a "do not parallelise" trigger).

## Honest framing

The model is build-parallel, **merge-serial-with-revalidation** — fast because building dominates wall-clock, not "free."
