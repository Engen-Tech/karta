# Karta 1.19 compatibility with Codex

Karta 1.19's installed Codex plugin passed live tests for fallback agents and gates, Kaizen, and Plannotator. Use the installed plugin for these features. Repo-local projections are in sync and expose the same skills, but this test drive did not repeat the live feature scenarios in repo-local mode.

Codex does not provide Karta's Claude Code hooks. Where a Codex sandbox does not enforce a narrower boundary, Karta relies on instructions and deterministic scripts. Do not treat the results below as hook parity.

## What works

| Feature | Installed plugin | Repo-local checkout | Enforcement | Evidence |
|-|-|-|-|-|
| Acceptance and safety fallback agents | **Passed.** With no `.codex/agents`, `karta-verify` loaded its bundled agent instructions, dispatched fresh read-only sessions, returned both verdicts, and made no writes. | Registered `.codex/agents/*.toml` are generated and sync-checked; this binder did not repeat the live gate scenario in repo-local mode. | Installed test: sandbox-enforced by the read-only Codex sessions and instruction-enforced by the bundled agents. Repo-local registered gate agents: sandbox-enforced as read-only. | [Fallback agents and gates](fallback-agents-and-gates.md#acceptance-and-safety-fallbacks) |
| Missing stack-pack input | **Passed.** An unknown pack id halted before dispatch, and a missing checklist returned `BLOCKED` with provenance instead of skipping the check. | The same resolver, bundled instructions, and generated projections are present; no separate live repo-local run was recorded. | Pack resolution is script-enforced. The fallback gate's fail-closed response is instruction-enforced. | [Unknown id](fallback-agents-and-gates.md#unknown-stack-pack-id) · [missing checklist](fallback-agents-and-gates.md#missing-stack-pack-checklist) |
| Doc-gardner fallback writer | **Passed.** The writer corrected only the stale README sentence and changed no code, binder, commit, or ref. | A registered workspace-write agent is generated and sync-checked; no separate live repo-local run was recorded. | The workspace sandbox is broad. The documentation-only boundary is instruction-enforced. | [Doc-gardner fallback](fallback-agents-and-gates.md#doc-gardner-fallback) |
| Kaizen switch, direct mode, and delivery mode | **Passed.** Absent and disabled switches were no-ops. Direct mode used detected packs and left changes uncommitted. Delivery mode seeded exactly the binder pins and committed on the supplied integration branch. | The repo-local skill is byte-synced, but these live scenarios were tested only through the installed plugin. | Switch checks and pack validation are script-enforced. The fallback writer's exact path boundary is instruction-enforced; Codex has no Karta confinement hook. | [Kaizen evidence](kaizen.md#scenario-evidence) |
| Plannotator planning and delivery surfaces | **Passed with a human acceptance waiver.** The capability probe, review offer, browser annotations, unambiguous mapping, ambiguous-note return, binder validation, no-implicit-commit rule, and delivery review offer all worked. | The repo-local skills are byte-synced, but these live scenarios were tested only through the installed plugin. | The executable probe and binder validation are script-enforced. Annotation mapping and the no-implicit-commit boundary are instruction-enforced. | [Plannotator evidence](plannotator.md) |
| Karta plugin hooks | **Unavailable.** The installed Codex plugin has no Karta hooks manifest. | **Unavailable.** Repo-local Codex uses agents, skills, rules, and scripts, not the Claude Code hooks. | Unavailable on Codex. | [Kaizen confinement note](kaizen.md#codex-confinement-is-weaker-than-claudes-hook) |

## What the waivers mean

Two items are recorded as `accepted-done`; neither should be presented as an ordinary automated-gate pass.

- **Fallback gates:** attempt 2 passed every scenario. The human waiver accepted that the binder's wording was stale because attempt 1 remains in the record as honest retry history.
- **Plannotator:** the live annotation round-trip and repository checks passed. The human waiver accepted the result because the binder's transcript-backed assertions were not implemented as executable contract tests. This leaves an automated-test gap, not a known failure in the observed flow.

## Test envelope and limits

- The fallback and Plannotator runs used `codex-cli 0.143.0` and Karta `1.19.0` from an isolated installed-plugin cache.
- Kaizen used `codex-cli 0.143.0` for shell checks and `0.144.0-alpha.4` for its live installed-plugin runs.
- No scenario pushed or opened a pull request.
- These records prove the named scenarios only. They do not certify Codex hooks, untested repo-local runtime behavior, or boundaries narrower than the active Codex sandbox.
