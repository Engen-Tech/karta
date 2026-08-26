# Design reference gate — making a design claim in a binder cost something

Date: 2026-08-17. Status: decided and delivered as binder `design-reference-gate-2`. Operator procedure: [docs/how-to/design-fidelity.md](../how-to/design-fidelity.md).

## The failure this came from

On the Karta Watch status-page rework, ten work items named a design view in `design_reference`, none of them carried a `visual` oracle, and four large differences reached the merged page unnoticed. Nobody bypassed anything. Every one of those items disclosed in prose that it had no browser in its check, and the disclosure changed nothing.

The mechanism: an item's oracle `type` is the only thing that routes it to the visual gate. `design_reference` never routes an item into that gate — it only vetoes it once the type is already `visual`, because `karta-build` skips `karta-validate` when the value is `none`. The validator read the field for nothing at all. So "names a view" and "gets compared against that view" were two unrelated facts, and the plan could assert the first while the second never happened.

Two things came out of that: a rule that makes the claim cost something, and a fingerprint on the frozen copy of an external design.

## Decision 1 — the rule is an error, not a warning

An item whose `design_reference` names a real view must carry a `visual` oracle or a `visual_check_waiver`. `validate_binder.py` returning a non-empty error list prints `INVALID` and exits 1, which hard-bails `karta-deliver` at preflight and equally hard-bails `karta-build` at Gate 1, which runs the same validator on the same binder. Both stop points are intended, and both are named here rather than discovered mid-run. Gate 1 grants a worker one discretion — it may take the orchestrator's preflight run as satisfying the gate rather than re-running it — so a wave does not necessarily re-validate per item, but a directly-invoked worker always does.

**Chosen over: a warning.** A warning prints after the `VALID` line and changes no exit code — precisely the strength the failing binder already had. The house rule is enforced checks over skippable prose.

An error is affordable because the rule is always satisfiable: give the item a visual oracle, record a waiver, or set `design_reference` to `none` and stop claiming the view. No honest binder is locked out.

The sentinel comparison is exact. Only the literal string `none` excuses an item, matched byte for byte with no stripping and no case folding, so `None` and `NONE` are read as design claims and rejected as such. The schema also gained `minLength: 1` on `design_reference` so an empty string cannot be written at all.

## Decision 2 — the waiver is a per-item sibling field

`visual_check_waiver` is an object of exactly `reason` and `covered_by`, both required and both non-empty, sitting on the work item beside `oracle`.

**Per item, chosen over a binder-level register.** The claim is per item. A reader judging one item should not have to scroll to a separate list to find out whether it was waived.

**Beside the oracle, chosen over a branch inside it.** Both oracle branches are `additionalProperties: false`, and the existing opt-out branch already means something else — opting out of the acceptance check altogether. A waived item still runs its own check; it is excused only from the design comparison.

**Recorded and printed, chosen over recorded only.** A recorded escape that prints nowhere is a silent escape, which karta already refuses for the opt-out. `waiver_summary` sits beside `opt_out_summary`: the `VALID` line states the waiver count, each waiver prints its `reason` and its `covered_by`, and each covering item prints how many waivers it absorbs. A gate covering seven views is stated rather than inferred.

## Decision 3 — coverage may not cross binders

`covered_by` must name a work item **in the same binder** that satisfies six conditions, each reported as its own error: the id resolves, that item's oracle `type` is `visual`, that item's own `design_reference` names a real view, that item depends on the waived item directly or through the chain, that item's oracle carries a non-empty `assertions` list, and that item names the waived item in its own `covers` array.

**Coverage may not cross binders, chosen over allowing a later binder to cover an earlier one.** A promise that a later binder will look is exactly the promise that was made last time, and it took a hand comparison to discover it had not been kept.

The other five conditions each close a way of satisfying the rule with prose:

- The `visual` type is what routes the covering item to the gate at all.
- The covering item's own `design_reference` has to name a real view, because `karta-build` skips the visual gate when it is `none` — a covering item that is `visual` but names no view is a gate no browser ever runs.
- The dependency edge is what makes "covers" true in time. A gate cannot cover work it does not run after.
- `assertions` is optional in the schema. Without this condition the cheapest compliant shape is one bare closing visual gate absorbing every waiver in the binder, which is the deferred check the rule exists to end.
- `covers` is what makes coverage a two-sided agreement. The first five conditions all read the covering item's own properties, so a waived item could point at any qualifying gate in the binder and be accepted whatever that gate actually opens — an item naming `checkout` waived to a gate naming `dashboard` validated clean and `checkout` was never compared. **Chosen over requiring the two `design_reference` values to match.** One closing gate covering several differently-named views is the intended shape and the one `waiver_summary` exists to report: `watch-fidelity`'s single `binder-panel` gate legitimately covers seven items naming `typography`, `item-card`, `header` and `rail`, and equality would reject five of those seven. So the gate is not asked to name the same view — it is asked to name the items, which is the fact the waiver was asserting on its behalf.

A binder with no `visual` item anywhere cannot waive anything. It has to add a check or drop the claim.

One error, not two, covers a waiver doing no work — on an item with no real design claim, or on an item that already carries a visual oracle. Neither shape can produce an unchecked design claim, so neither earns a separate branch, but an inert waiver left standing would tell a reader a comparison was deferred when none was.

## Decision 4 — the pin check is a seven-outcome ladder

`.karta/design-pins.json` records a sha256 over the bytes of each committed design capture. `check_design_pins.py` runs as one more hard gate in `karta-validate`'s prerequisites phase — the one phase that is already nothing but gates that fail rather than prompt, and the last moment before the design is served where a wrong capture can still be stopped. Seven outcomes:

1. Bytes match the pin — pass.
2. Bytes disagree with the pin — fail.
3. Inside the repository, pin file present, no entry of its own — fail.
4. `recapture_after` has passed — fail.
5. No pin file at all — fail; a notice and a pass under `--allow-unpinned`.
6. Design resolved from outside the repository — fail; a notice and a pass under `--allow-unpinned`.
7. Malformed pin file — fail as malformed, never as a matching capture.

**The ladder, chosen over a single match-or-fail check.** A flat check would break every consumer on the day it landed. Rungs 5 and 6 are what let a repository that has not opted in carry on unaffected while the hole closes for any repository that has.

**Rungs 5 and 6 exit non-zero, chosen over the notice-and-pass they shipped with.** Both describe a capture the check compared against nothing, and both returned 0 — so anything gating on the exit status read "not verified" as "verified", which is the same shape of silent pass the whole rule exists to end. The escape did not go away, it became a word someone writes: `--allow-unpinned` restores the notice and the pass for exactly those two rungs and moves nothing else. An un-opted-in repository is one flag from carrying on, and the flag sits at the call site where a reader can see it. `karta-validate`'s prerequisites step passes it: pinning is opt-in, so the framework's own caller must not stop a repository that never pinned anything. The flag is scoped to the two unverifiable rungs, so a repository that has opted in still hard-stops on drift, a missing entry, an expired pin, or a malformed pin file — with the flag set or not.

Rung 4 is opt-in inside an opt-in: `recapture_after` is optional, so no existing pin gains a deadline it did not ask for, and a capture that outlives the life its author gave it stops the comparison instead of being disclosed and ignored. The rung keys on the key being present, not on its value being truthy: a present-but-falsy `recapture_after` (`0`, `""`, `false`) is a deadline written down wrong, and reading it as "no deadline" would let a pin outlive the life its author tried to give it — the one thing the rung exists to stop.

Three sub-decisions inside this one:

- **The record lives in `.karta/design-pins.json`, chosen over `design_facts` on the binder.** The capture outlives the binder that froze it and is usually read by more than one. `design_facts` is `additionalProperties: false` with room for a source and a stack, and a per-file hash there would tie a repository asset's lifecycle to whichever binder happened to mention it first. `.karta/` already holds the binders, the roundtable records and the stack packs, so a fourth file there needs no new convention.
- **The check reads only, chosen over a write mode that stamps a pin.** Every outcome where the check computed a hash prints that hash, so writing the first pin and restoring a drifted one are both a copy and paste from the check's own output. A writer would be a second way to do the same thing.
- **No `version` key on the manifest.** Nothing would read it, and the house minimalism rule is explicit that a key nothing reads does not get added. `recapture_after` is not in that category — a rung reads it.

## What is deliberately not detected

Neither mechanism can tell you a frozen capture has fallen behind the living design upstream. The design lives behind agent-invoked tools, karta's gate scripts are stdlib with no network, and no script here pretends otherwise. What the pin check proves offline is the capture matching its recorded fingerprint, and the capture not outliving its own `recapture_after` date. Whether the upstream moved is a human or agent look, and the runbook gives recapture as a step a person performs rather than implying a check will raise it.

Every pass prints the capture date, the upstream address, and the recapture triggers, so the person about to trust the comparison is reading the terms at the moment they matter.

## The known erosion path

The cheapest way out of the rule is to set `design_reference` to `none` and stop claiming the view. That resolution is sanctioned and stays sanctioned — but it costs one token, needs no reason, and is recorded nowhere. A repository with no design export can reach permanent compliance by making the field always say `none`, and nothing in the rule notices.

Call it the none-flight, and give it one detection: `design_source_advisory` emits a warning when a binder names a design source in `design_facts` and carries no `visual` oracle on any item. That is exactly the shape of the failure this rule exists to catch — every design claim reaching the gate through waivers pointing nowhere, or reaching it nowhere at all.

It is a warning and not an error on purpose: a project may legitimately be all backend work under a design-bearing repository. It prints on every run so the shape cannot pass unnoticed, the way the previous failure did.

## The two binders that existed when this rule was written

The rule rejected both live binders on the day it was drafted. Neither was edited; both were handled by sequencing, recorded in this binder's `after` edges so the ordering lives where karta reads it.

**`watch-fidelity`** — ten items, seven of which name a design view and are checked deterministically, against a single visual oracle, its closing `design-fidelity-gate`. Its dependency chain is a single spine and that gate transitively depends on all seven, so seven waivers are expressible and all point at the same covering item — which, under the `covers` condition, would also list all seven. It is sequenced before this binder; **if that order is ever broken, it needs seven waivers**. Seven were written into it ahead of time, parked inside each item's `contract` because the sibling field did not exist yet and the validator rejects an undeclared work-item property outright. That makes the retrofit mechanical, but it does not satisfy the rule, which reads the sibling field and not a contract clause.

**`watch-redesign`** — ten items naming a design view and **no visual item at all**. No waiver can bring it into compliance, correctly, since it is the binder this rule exists to prevent. It can only leave the live set by landing, and would be rejected outright if it did not. Its own integration branch already carried the archive move, so landing retired it.

`karta-deliver` archives a binder at end of life, and the validator reads `archive/*.json` for its slug only. An archived binder is never re-validated, so a delivered binder cannot be retroactively rejected.

A third binder, `watch-drill-in`, was still live when this binder was re-planned as `design-reference-gate-2`: eight view-naming items under a unit-typed gate with no visual oracle anywhere, plus seven waivers parked in `contract` that the rule never reads. Like `watch-redesign`, it could only leave the live set by landing, which is the third `after` edge.

## What was ruled out

- **Keying the rule on whether the repository can run a browser.** The validator is stdlib with no network and no way to know. A rule that changes shape with the machine it runs on is worse than one that is the same everywhere.
- **Making `design_reference` itself route to the visual gate.** It would silently make every existing UI item expensive. This work changes no routing: an item still reaches the visual gate by carrying oracle type `visual`, and the only thing removed is the third option of naming a view and checking nothing.

## What a consumer installs

Nothing here changes it. Both documents live under `docs/`, which is outside the plugin projection — `.agents/skills/` and `plugins/karta/` mirror `skills/` only, and the projection checks report both mirror trees unchanged by this item. The behavior a consumer gets comes from the schema, the validator rule and the pin check that landed ahead of these pages.
