# Repair a partially delivered binder

A committed binder is never edited. That holds between waves as much as during one: the binder immutability guard (`hooks/scripts/guard_binder_immutability.py`) denies every write to a committed binder, live or archived. When a wave shows the plan is wrong and some items are already delivered, you repair it with a **successor binder**: a new plan under a new slug that names the old one and carries its finished work forward.

This keeps the audit trail whole. The original plan, the refs that record what it delivered, and the corrected plan all stay in git.

## When to use this

Use a successor when all of these are true:

- the binder is committed;
- at least one item has a `done` ref (it was merged onto `karta/<slug>/integration`);
- the remaining work needs a different plan: new items, changed oracles, a changed dependency order.

If nothing has been delivered yet, you don't need a successor. Withdraw the binder instead: commit its deletion, then commit the corrected plan (see "When the retroactive panel rejects a hatch-committed binder" in `AGENTS.md`).

## The steps

1. **Stop after the current wave.** Let the wave in flight finish: the Phase-4 decisions are made and the post-wave check has run. Don't start the next wave.

2. **Plan the successor.** Run `karta-plan` for the remaining work. Give the new binder:
   - the slug `<slug>-r2` (then `-r3` for a second repair, and so on);
   - a top-level `supersedes` object naming the predecessor and the items it already delivered:

     ```json
     "supersedes": { "slug": "tag-editing", "carried": ["tag-model", "tag-api"] }
     ```

   - every carried item copied into `work_items` **unchanged** from the predecessor. New items may `depends_on` a carried item like any other.
   - any predecessor item that is done but should **not** survive (the wave proved it wrong) listed in `dropped`, with a successor work item that reverts or replaces its code:

     ```json
     "supersedes": { "slug": "tag-editing", "carried": ["tag-model"], "dropped": ["tag-api"] }
     ```

     Dropping is needed because the successor's integration branch starts from the predecessor's tip, so every merged item's code is already on it. An item that is neither carried nor dropped would keep its code on the branch with no plan entry describing it.

   `validate_binder.py` checks that each carried id is one of the binder's own work items and that the binder does not supersede its own slug.

3. **Commit the successor with its review**, the same way as any binder.

4. **Deliver the successor.** Run `karta-deliver` on `<slug>-r2`. Its preflight (`deliver_preflight.py`) does the checking before any wave runs.

## What preflight checks

The preflight packet carries a `supersedes` report. It sets `halt`, and the run stops, unless all of these hold:

| Check | What it proves |
|-|-|
| `karta/<predecessor>/integration` exists | the delivered work is still there to build on |
| each carried item has `refs/karta/<predecessor>/item-<id>/done` | the predecessor recorded it as done |
| that done ref passes `check_item_provenance.py --check-accepted` over its own merge, and sits on the predecessor integration branch's first-parent history | the same checks a resumed run applies to its own done refs; a forged or off-branch done ref fails |
| each carried item equals the predecessor's committed work item | the successor carries the item that was delivered, not a rewritten one. The predecessor's plan is read from its integration tip, at the live or the archive path |
| every predecessor item with a `done` ref is carried or listed in `dropped` | no delivered code rides onto the successor's branch unaccounted for |
| every `dropped` id has a predecessor `done` ref | the drop names real delivered work; the packet reports each dropped id with its merge SHA for the revert item to target |
| an existing `karta/<slug>-r2/integration` contains the predecessor's integration tip | a resumed successor run still has the carried work |

Preflight does not check that the revert happened: that is the reverting work item's job, proven by its own acceptance oracle like any other item. It also sees only done refs, so a predecessor merge whose done ref was never written is not listed.

Proven carried items count as done: they never enter the frontier, and a new item that depends on one is ready at once. The packet's `integration_base` names the predecessor's integration branch.

## How the successor's integration branch starts

On the first run, deliver creates the successor's branch from the predecessor's integration tip, not from the default branch, so the merged work is already present:

```bash
git branch karta/<slug>-r2/integration karta/<slug>/integration
```

In the new integration worktree it then:

1. merges the default branch in (`git merge --no-ff --no-edit <default-branch>`), which brings in the successor binder's own commit;
2. retires the predecessor by the sanctioned archive move, the same one a completed run uses:

   ```bash
   mkdir -p .karta/binders/archive
   git mv .karta/binders/<slug>.json .karta/binders/archive/<slug>.json
   git commit -m "chore(karta): archive binder <slug> — superseded by <slug>-r2"
   ```

A move leaves the content unchanged, so it is not an edit. The guard watches file writes, and the archive path is covered by it too. The retirement travels with the successor's integration branch: when you merge that branch, the default branch stops listing the predecessor.

## What to keep until the successor lands

Keep the predecessor's integration branch and its `refs/karta/<slug>/` refs and wave tags. Preflight re-proves the carried items from them on every successor run. If you clear them, the successor halts, because its carried work can no longer be proven. The predecessor's slug is retired, like any archived slug: don't reuse it.

## Why not edit the binder between waves?

The binder is the plan of record that run state is derived from. An edit after items are delivered would make the refs describe a plan that no longer exists, and it would reach the default branch with no review. A successor gets its own review, names what it inherits, and has that inheritance checked against git before any work runs.
