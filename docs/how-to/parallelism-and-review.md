# Parallelism and review

karta builds work items in parallel by default and only serializes when running two items together would produce a wrong or broken result. When planning is done, it hands you a short list of what's worth your eyes — risky or unusual items — and stays quiet about the routine ones. This guide explains both behaviors.

## How karta decides what to run in parallel

karta builds work items at the same time by default, to save time. It only drops back to one-at-a-time when running two together would produce a wrong or broken result. There are four such cases — everything else runs in parallel.

1. One item needs another finished first.
Item B uses something Item A creates, so B can't be built until A exists. B waits for A. This isn't a special rule — it's just correctness; B literally won't work without A. Example: the profile page can't be built until the app shell and routing exist.

2. Two items change the same order-sensitive shared thing.
A few things can only be changed one at a time, in a set order — a database migration, a shared lock file, a generated file. If two items both change one of these at once, they clobber each other. karta catches this either by noticing both items plan to edit the same such file, or because the binder says these items share that resource. Example: two items that each add a database migration must run one after the other.

3. Two items in the same batch would edit the same files.
Even when neither item needs the other, if both edit the same file, building them side by side means their changes collide when karta stacks the results together. So karta runs them one at a time to avoid the merge mess. Example: two items that both edit the global stylesheet. karta knows which files an item will touch from its `touches` list when the plan provides one (otherwise it works it out from the item's description), and it checks this at plan time — flagging two same-wave items that would edit the same file unless you've marked one to run alone or said they share a resource.

4. You said so.
You can mark items in the binder as "don't run these together." This is your override for interference karta can't see — you know two items will step on each other for a reason that isn't visible in the file list.

The difference between #2 and #3, since they sound alike: #2 is about order (these changes must happen in sequence), #3 is about collision (these changes would conflict if merged). #2 and #1 keep results correct; #3 keeps the stacking clean; #4 is your manual escape hatch.

## What a delivery costs and where the turns go

We measured one. karta 2.32.0 delivered the two-item fixture-v1 benchmark for $4.82 — $2.41 an item. Here is where the money went.

| Role | Messages | Share of the $4.82 |
|-|-|-|
| Orchestrator | 44 | 48.8% |
| Builders | 66 | 41.9% |
| Gates | 19 | 9.3% — acceptance reviewers 5.0%, safety auditors 4.3% |

The bill is context multiplied by turns: cache read tokens are about 90% of the tokens billed, and cache traffic — reads plus creation — is nearly all of the spend. Each turn re-sends everything the role has read so far, so the roles that talk longest with the most in their heads cost the most. That is the orchestrator and the builders, not the gates.

Three commands now do work that used to burn those turns:

| Command | What it replaces |
|-|-|
| `deliver_preflight.py` | the orientation the orchestrator used to do by hand at the start of a delivery |
| `item_context.py` | the search a builder used to run before its first edit |
| `merge_item.py` | the per-item merge sequence |

The target — a target, not yet a result: at least 40% below the 2.32.0 baseline of $2.41 an item on fixture-v1, an unrounded cost_usd_per_item at or below 0.60 × $2.41 = $1.446. The perf-fixture-cost-baseline card judges this after the next release, and only when that run resolves the same model-ID set as the 2.32.0 baseline. A different model set makes the comparison inconclusive — neither a pass nor a fail.

## What you review (and what you don't)

You don't have to review everything. karta shows you what's worth a look.

Every work item carries a check that proves it's done — a real one, the kind you'd want CI to run, not a box to tick. karta writes it and runs it for you.

When planning is done, karta hands you a short list: the items worth your eyes — anything risky, unusual, or touching something sensitive — and stays quiet about the routine ones. Review is a short list, not a slog.

You keep the controls:
- Want to see everything? You can.
- A check doesn't fit an item? Turn it off — karta tells you what that leaves unchecked.

The idea is simple: karta puts the decisions that matter in front of you early, then gets out of your way.
