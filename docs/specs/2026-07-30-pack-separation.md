# Pack separation — karta becomes a regular consumer, and composition reaches the gate

Date: 2026-07-30. Status: decided — records a direct user edict and the enforcement change it exposed. Companion to the [2026-07-30 user pack sovereignty spec](2026-07-30-user-pack-sovereignty.md), which retires the deterrent machinery; this spec retires the karta-only shadow and makes `extends` composition real at the checks.

## The edict

karta-the-repo stops being a special case of its own framework. It had carried a managed `minimalism` shadow — a byte-identical copy of the shipped built-in pinned in place by a validator guard — so the repo that authors the packs would never drift from them. The user's direction: karta becomes an ordinary consumer of karta-the-framework. It narrows a built-in the same way any project does, through a project pack with `extends`, and it earns no private guard for doing so.

## The discovery

`extends` / `exclude_rules` composition was documentation only. The validator checked its *shape* — a project pack could declare `extends`, name `exclude_rules`, carry an `id_prefix` — and planning reported which rules an exclusion dropped. But nothing ever expanded the composition at a gate. The safety-auditor at the verify and build checks still judged each pack's own literal checklist. A narrowing you wrote with `extends` changed what the plan mentioned and nothing the checker enforced.

## What changes

**A resolver expands the composition.** `skills/karta-kaizen/scripts/resolve_pack_checklist.py` takes a project pack that declares `extends`, drops the `exclude_rules` ids from the base pack's checklist, and appends the project pack's own rules — the composed Review checklist the auditor actually judges against. It runs at two points:

- `karta-verify` at its `verify:boundary` step, so the gate enforces the composed checklist.
- `karta-build` at step `4c-ter`, so the builder's own self-check sees the same composed checklist the gate will.

Both points now enforce the composed Review checklist — the built-in's rules minus the project pack's `exclude_rules`, then the project pack's own rules.

**Planning dedups the base pin.** When a project pack declares `extends`, that pack pins its base for it. `karta-plan`'s `plan:sme` step no longer adds a second independent pin for the base built-in — the `extends` pack carries the base into the binder once, not twice.

**The managed shadow is retired.** karta's private `minimalism` shadow is gone:

- The shadow enforcement guard, the stamp-strip helper it relied on, and the self-test fixtures that exercised it are deleted from `scripts/validate_plugin.py`.
- The shadow file itself — `.karta/sme/minimalism.md` — is deleted.

In its place, karta uses an ordinary project pack, `.karta/sme/karta-house-minimalism.md`. It declares `extends: minimalism`, drops `min.4` via `exclude_rules`, and carries one replacement rule whose text begins `Narrows min.4:`. From here karta narrows its own built-in exactly the way the stack-packs guide tells every other project to.

## The consumer-upgrade effect

State it plainly: a project that already carries an `extends` pack will, from this release, have that pack's composed checklist enforced at the verify and build gates for the first time. Before, the composition surfaced only in the plan. So the rules that pack excludes stop being checked, and the base pack's other rules start being checked. Any project with an `extends` pack should review it on upgrade.

## What stays unchanged

- `skills/karta-kaizen/scripts/validate_packs.py` — the pack shape checks are untouched; the resolver expands a composition the validator already accepted.
- `consumers.json` — karta is still not enrolled as a tracked consumer; this change does not enroll it.
- `seed_drift.py` and every committed bench baseline, result, and claim — the bench vocabulary and its recorded numbers are left exactly as they stand.
- The hooks — `hooks/hooks.json` wiring and every guard keep their registration and behavior.
- The two in-code `KARTA-SME-OVERRIDE(min.4)` markers — they stay where they are; the house pack narrows the rule, it does not erase the sites that already declared against it.
- The shipped built-in `skills/_shared/sme/minimalism.md` — untouched. Promoting a lesson into a built-in stays a human act in the karta repo.

## Delivery-time note: a mis-specified oracle sub-check

The house-pack item's acceptance oracle carried a sub-check (number 6) that grepped for the bare substring `min.4`. That is the wrong assertion: it matches the mandated `Narrows min.4:` text inside rule `hmin.1` rather than confirming a rule *id*. The check passed for the wrong reason, so the item was accepted with a recorded waiver. The property the oracle meant to hold — that the `min.4` rule id is excluded by the house pack — does hold. A future revision of that oracle should assert on the excluded rule id, not on a substring that the replacement rule's own prose happens to contain.
