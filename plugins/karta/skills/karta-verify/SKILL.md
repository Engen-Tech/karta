---
name: karta-verify
description: Verify one built work item against its behavioral acceptance check (oracle types unit/integration/e2e/smoke) in a read-only, fresh session on the actual diff; check acceptance + external-contract conformance + boundary crossings; kick findings back to build and escalate to the human only on retry-exhaustion.
triggers:
  - "verify this work item"
  - "run the behavioral gate"
  - "check acceptance for <id>"
---

`karta-verify` is the thin orchestrator for the behavioral acceptance gate. It dispatches two karta-owned gate agents — `karta-acceptance-reviewer` and `karta-safety-auditor` — aggregates their verdicts, and drives the kickback and escalation loop per `references/verification-gate.md`. This skill is read-only throughout: the agents read the diff and the binder; this skill never edits code, tests, or the binder.

Visual oracles (`type: visual`) are not this skill's concern — those belong to `karta-validate`. Opt-out oracles bypass this gate entirely; the build step skips karta-verify for items where `oracle.opt_out` is true.

## Inputs

The caller must supply:

- **Worktree path** — the checked-out tree holding the item's branch.
- **Binder path + work item id** — locates the `oracle`, `assertions`, and optional `contract` for the item. The binder is a JSON file at `.karta/binders/<slug>.json` by default; see `references/binder-reference.md`.
- **Diff range** — the item's branch versus the integration tip (e.g. `karta/<slug>/WI01..karta/<slug>/integration`).

## Phase 0 — Prerequisites  `verify:prereq`

Before dispatching either agent:

1. Confirm a fresh, thin context: only the worktree path, the binder path, the work item id, and the diff range travel to each agent — plus, for the safety-auditor when the binder pins `sme[]`, the resolved SME Review checklists (see `verify:boundary`). No build-session state.
2. Resolve the **pre-verify env command** bound to this wave: read `env_contract.command` from the binder (see `references/verification-gate.md` and `references/binder-reference.md`). If the oracle's assertions need an environment to run and no env command is present, halt with a clear message naming the missing contract — this is a hard gate.
3. Check the floor: if the diff cannot clear compile / type-check / lint, do not dispatch the agents. Surface the item for human review and halt. The floor is defined in `references/definition-of-done.md`.

## Resolving the gate agents (any runtime)  `verify:resolve`

Each gate runs as a **fresh, read-only subagent** that receives only the four inputs (worktree path, binder path, work item id, diff range) — plus, for the safety-auditor when the binder pins `sme[]`, the resolved SME Review checklists (see `verify:boundary`) — and reads the binder and diff itself. karta ships each agent so the gate runs automatically wherever it is installed — resolve the agent the way the current runtime supports:

1. **A registered subagent by that name exists** — dispatch it by name (`karta-acceptance-reviewer` / `karta-safety-auditor`). This is the path on Claude Code (the plugin bundles the agents) and on Codex when the project carries `.codex/agents/*.toml` (a repo checkout, or a project that installed them); there the agent's `sandbox_mode = "read-only"` is sandbox-enforced.
2. **No registered agent by that name** (for example a Codex plugin install, which cannot register subagents) — spawn a fresh read-only subagent (a read-only explorer-style worker) and give it, as its complete instructions, the agent file bundled with this skill: [references/karta-acceptance-reviewer.agent.md](references/karta-acceptance-reviewer.agent.md) for acceptance, [references/karta-safety-auditor.agent.md](references/karta-safety-auditor.agent.md) for the boundary scan. Those files are the agents' own instructions and are self-contained.

Either way the agent is read-only and receives only the four inputs (plus the safety-auditor's resolved SME checklists when `sme[]` is non-empty); no build-session state travels with it. The dispatch steps below say "dispatch the `<agent>` gate" — resolve it by this rule each time.

## Phase 1 — Acceptance + contract conformance  `verify:acceptance`

Dispatch the **`karta-acceptance-reviewer`** gate (resolved per *Resolving the gate agents* above) with the worktree path, binder path, work item id, and diff range.

The agent reads the binder on disk, dispositions each `oracle.assertions[i]` as inspection-verifiable or execution-required, and checks contract conformance against an external artifact (type-checker, schema, or contract test). It returns one of:

- `CONFORMANT` — all assertions disposed, contract confirmed or absent.
- `DEVIATION` — one or more unresolved assertions or a missing contract artifact.
- `BLOCKED` — a required input is unreadable, **or the diff is readable but empty** (the item produced zero changes — nothing to disposition).
- `SPEC-SUSPECT` — the code diverges intentionally and the binder looks stale; halts for human adjudication.

**On DEVIATION:** kick the findings back to karta-build for bounded self-correction and re-dispatch the agent on the corrected diff. Cap: **max 2 attempts total**. On the second attempt still returning DEVIATION, halt with a call to action — no human escalation at this gate, and **no self-clear**: the implementer may **not** make the capped failure pass by declaring debt. The capped item takes the halt path — in a wave the worker commits its item branch and writes a `failed` ref at that tip ("halted at the gate, not cleanly done"), not done. Three ways forward:

- **Fix and rerun.**
- **Re-plan the unmet assertion as an explicit oracle `opt_out` via karta-plan, then rerun.** The binder is read-only to build, and karta has no backlog.
- **Get a human accept-waiver or defer at the delivery orchestrator's Phase-4 halt.** The orchestrator asks the human directly; this gate never records the accept.

See `references/verification-gate.md`.

**On SPEC-SUSPECT:** halt for human adjudication immediately. Do not loop; do not kick back. In a wave the worker leaves the same `failed` anchor — a committed item branch + a `failed` ref carrying the spec-suspect reason (it means "halted at the gate," not "the code is bad"). The binder is amended through karta-plan; alternatively the human may accept the divergence at the Phase-4 halt (the orchestrator records the waiver against the halted tip), never by this gate.

**On BLOCKED:** halt with the blocking reason; do not proceed to the boundary scan (`verify:boundary`). For an empty diff, the reason names which it looks like — a whiff (re-dispatch the worker) or a change already present on the tip (drop or amend the item via karta-plan). An empty-diff BLOCKED is not an accept/defer candidate: there is no diff to merge and no named assertion to waive.

## Phase 2 — Boundary scan  `verify:boundary`

Dispatch the **`karta-safety-auditor`** gate (resolved per *Resolving the gate agents* above) with the same inputs: worktree path, binder path, work item id, and diff range.

**Resolve SME checklists first (only when the binder pins `sme[]`).** Read the binder's `sme` list. For each id, resolve the pack — the worktree's project overlay `.karta/sme/<id>.md` laid over this skill's built-in [references/sme/](references/sme/) `<id>.md` (project-local wins) — and extract its **Review checklist** section. Hand those checklists to the auditor in its dispatch brief. This is the one input beyond the four that travels to a gate agent, and only when `sme[]` is non-empty: a project-extensible checklist cannot be embedded in the self-contained agent, and built-in packs live in the plugin rather than the worktree, so the dispatcher resolves them. When `sme[]` is empty or absent, hand nothing and the auditor's SME-norm check no-ops.

The agent re-runs the seven smart-surfaced-review signals (see `references/smart-surfaced-review.md`) on the actual diff — plus, when handed SME checklists, the conditional SME-norm check (an undeclared `KARTA-SME-OVERRIDE` is a VIOLATION) — and returns:

- `PASS` — no undeclared crossings.
- `VIOLATION` — one or more undeclared boundary crossings.
- `BLOCKED` — a required input is unreadable.

**On VIOLATION:** kick findings back to karta-build and re-dispatch the agent on the corrected diff. Cap: **max 3 attempts total**. After the third attempt still returning VIOLATION, escalate to the human — an unjustified boundary crossing is a safety question that requires a person's decision.

**On BLOCKED:** halt with the blocking reason.

The boundary scan (`verify:boundary`) runs after acceptance (`verify:acceptance`) resolves to CONFORMANT (or is skipped on SPEC-SUSPECT/BLOCKED halt). The two agents run sequentially in the common path; if `verify:acceptance` loops, `verify:boundary` does not start until `verify:acceptance` clears or exhausts its cap.

## Phase 3 — Aggregate verdict  `verify:aggregate`

Combine both agents' return envelopes into a single verdict:

| Acceptance result | Safety result | Aggregate |
|-|-|-|
| CONFORMANT | PASS | `pass` |
| CONFORMANT | VIOLATION (cap not exhausted) | loop `verify:boundary` |
| DEVIATION (cap not exhausted) | — | loop `verify:acceptance` |
| Either cap exhausted | — | `blocked` (halt-with-CTA or escalate per cap rules) |
| SPEC-SUSPECT or BLOCKED | — | `blocked` (halt for human) |

Report the aggregate verdict to the caller (karta-build or karta-deliver). Write everything you show a person in plain language — see [references/user-facing-prose.md](references/user-facing-prose.md).

- `pass` — both agents cleared. Report PASS with a one-paragraph summary.
- `concerns` — findings went back to build. This is an intermediate state, not a terminal one.
- `blocked` — a cap is exhausted, or the verdict is SPEC-SUSPECT or BLOCKED input. Include the exact agent output and the one action the human needs to take next.

This skill is read-only throughout all phases.

## Gotchas

- **Floor first.** A change that cannot clear compile / type-check / lint never reaches the agents. See `references/definition-of-done.md`.
- **Read-only.** This skill never edits code, tests, the binder, or any other file. Neither do the agents. If an edit is needed, it goes back to karta-build.
- **Fresh session per dispatch.** Each agent dispatch is a new session with no build-session context. Pass only the four inputs — the one exception is the safety-auditor's SME Review checklists, resolved here and passed only when the binder pins `sme[]`. No build-session state travels.
- **The agents do the reading.** `karta-acceptance-reviewer` and `karta-safety-auditor` read the diff and the binder directly. This skill does not pre-read those files for them.
- **Escalate only on exhaustion; this gate never records an accept.** No human review gate fires during delivery except safety-auditor cap exhaustion (3 attempts). The acceptance gate (2 attempts) and a SPEC-SUSPECT halt with a call to action, not a human escalation. A human may accept or defer the halted item, but only at the delivery orchestrator's Phase-4 halt through the host's user-input facility — this read-only gate surfaces the halt and never writes the `accepted` ref.
- **Caps are per-agent, not shared.** The acceptance cap (2) and the safety cap (3) are independent. Exhausting one does not reset the other.
- **Opt-out items skip this gate.** Items with `oracle.opt_out: true` are not dispatched here. The build step reports the opt-out; karta-verify is not invoked.
- **Visual oracles belong to karta-validate.** If a work item arrives here with `oracle.type: visual`, return `blocked` with a note redirecting to `karta-validate`.
