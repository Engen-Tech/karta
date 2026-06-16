# Verification Gate

After a work item is built, karta checks it before it counts as done. The check is a gate, not a suggestion. This file describes the gate's shape; the smart-surfaced-review reference describes the signals it re-runs, and the definition-of-done reference is the canonical source for the floor.

## Shape

The gate runs on the actual diff, in a fresh AI session, with a deliberately thin context: only the worktree, the binder, and the work item's acceptance check (its `oracle`, and `contract` if it has one). It does not inherit the build session's context, and it is independent of whoever implemented the item. The point is to judge the code that exists, not the story the implementer tells about it.

The gate is realized by dispatching two karta-owned agents — `karta-acceptance-reviewer` and `karta-safety-auditor` — plus `karta-validate` for visual oracles. Each runs read-only: it reports, it never edits.

## Three checks, all on the actual diff

1. **Acceptance.** Does the diff satisfy the oracle's assertions? Visual oracles go to `karta-validate`, which compares rendered output against the design. Unit, integration, e2e, and smoke oracles go to `karta-acceptance-reviewer`, which dispositions each assertion against the code.

2. **Contract conformance.** If the item declares a `contract`, the gate checks the diff against an external artifact — a type-checker, a schema, or a contract test — not against the binder's own claim about the contract. A binder field saying "this conforms" is not evidence; the type-checker passing is. `karta-acceptance-reviewer` owns this check.

3. **Boundary scan.** Does the diff cross a sensitive, destructive, or contract boundary the item never justified? `karta-safety-auditor` re-runs the seven smart-surfaced-review signals on the real code and flags any crossing the work item did not declare. This is the build-time pass that the smart-surfaced-review reference calls the real gate: the plan-time triage informs, this decides.

## The loop and its caps

On any finding, the gate kicks the work back to the implementer (karta-build) for bounded self-correction, then re-runs on the corrected diff. The two agents have different caps, and they are deliberately different:

- `karta-safety-auditor` — **max 3 attempts, then escalate to the human.** A boundary the item never justified is a safety question; if three rounds of self-correction cannot clear it, a person needs to look, because the invariant or the crossing itself may need a decision.
- `karta-acceptance-reviewer` — **max 2 attempts, then halt with a call to action.** No human escalation here. On the second failed attempt the gate halts and hands the implementer a choice: fix-and-rerun, or place a declared-debt marker (the declared-debt reference is the source for that marker family) that records the unmet assertion as a deliberate, named deferral.

There is no human review gate during delivery. The human is reached only on retry-exhaustion of the safety auditor — nowhere else.

## The floor

If the change does not even compile, type-check, or lint, the gate does not pass and karta does not auto-merge. It surfaces the item for a person instead. This is the floor under every non-opted-out item, and the definition-of-done reference is its canonical statement. A change that cannot clear compile/type-check/lint has not earned an acceptance review — it has earned a surfacing.

## Advisory hooks

Any pre-tool hook a project wires in stays advisory: it exits 0 and never blocks. The gate blocks, not the hook. The hook can warn at edit time, but the authoritative judgment is the gate running on the actual diff in a fresh session — which self-corrects within its caps, then escalates or halts. A hook that hard-failed would be a second, hidden gate; karta keeps exactly one.
