# Definition of Done

"Done" means the oracle passes on the merged result. The definition of done is the minimum check you'd want CI to run — the project's real CI-facing checks (its tests, build, and type-check) plus item-specific assertions. It is not a check the model grades itself on.

## Floor

The change must at least compile, type-check, and lint clean. Below that threshold karta does **not** auto-merge — it surfaces the failure and halts.

## Opt-out

Opting out of a check is explicit and **recorded**: the binder's `oracle.opt_out` field plus a `reason`. There is no silent opt-out. karta reports what it leaves unchecked whenever an opt-out is in effect.

## How to explain review to users

You don't have to review everything. karta shows you what's worth a look.

Every work item carries a check that proves it's done — a real one, the kind you'd want CI to run, not a box to tick. karta writes it and runs it for you.

When planning is done, karta hands you a short list: the items worth your eyes — anything risky, unusual, or touching something sensitive — and stays quiet about the routine ones. Review is a short list, not a slog.

You keep the controls:
- Want to see everything? You can.
- A check doesn't fit an item? Turn it off — karta tells you what that leaves unchecked.

The idea is simple: karta puts the decisions that matter in front of you early, then gets out of your way.
