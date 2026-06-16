---
name: karta-build
description: Use when implementing one work item from a karta binder in an isolated git worktree — stack-agnostic (frontend, backend, CLI, data, IaC, …) — running the project's lint/test/build plus the item's acceptance check, tagging commits, and merging into the per-binder integration branch (no PR). Trigger phrases: "build this binder item", "implement work item `<id>`", "karta-build `<binder> <id>`".
---

karta-build takes **one work item from a validated binder** and carries it from pickup to a tagged set of commits merged into the binder's **integration branch** — all inside an isolated git worktree. It is stack-agnostic: the same flow implements a frontend view, a backend endpoint, a CLI command, a data migration, or an IaC change. It does **not** open a PR. The user reviews and merges the integration branch.

The binder (`.karta/binders/<slug>.json`) is the cross-skill contract. Each work item carries an `oracle` (its acceptance check) and an optional `contract` (the interface it exposes or consumes). karta-build reads the binder — it never writes to it during a run (see [references/binder-reference.md](references/binder-reference.md)). The planning counterpart is `karta-plan`; the read-only acceptance and visual gates are `karta-verify` / `karta-validate`.

## How this skill adapts to your project

karta-build is **stack-agnostic**. It does not assume a frontend framework, component library, data layer, branch convention, or repo layout. It resolves a small set of project settings up front (detect → ask), then implements the item against whatever it finds. Where this document shows a concrete tool, command, or library, treat it as an **example**, not a requirement.

The UI-specific machinery — component maps, icon imports, token rules, the data-layer conformance loop (`build:datalayer`), the dev-server lifecycle for the visual gate (the acceptance loop's bring-up `build:acceptance` and its teardown `build:teardown`), and the visual design-validation loop — is a **conditional annex** that applies only when the work item carries those fields (`component_map`, `icon_map`, `token_changes`), a data-layer surface, or a `visual` oracle. A backend / CLI / data / IaC item skips the entire annex.

## Project configuration (resolve once, up front)

Resolve each setting in this order: **explicit user input → detect from the repo → ask the user** (batch all unknowns into a single question). Do not prompt for things you can detect. **When detection conflicts with explicit user input or the project's documented/blessed stack, the stated stack wins** — confirm it rather than asserting what's merely present (a repo can be mid-migration).

| Setting | What it is | How to resolve |
|-|-|-|
| **App dir / target** | Where the item's code lives, and its task-target name | Detect from the binder's `scope.included` and the repo layout; in a monorepo or polyglot repo, root all paths/commands at the area the item targets; else ask |
| **Toolchain commands** | install / lint / test / build / typecheck invocations | Detect from package scripts + task runner (npm/pnpm/yarn, Make, Nx, Turbo, Cargo, Poetry, …); record `<install command>`, `<lint command>`, `<test command>`, `<build command>`, `<typecheck command>`. When both package scripts and a task runner exist, prefer the project's documented entrypoint — a bare script pick can skip orchestrated lint/coverage the runner bundles |
| **Env command** | The dev/test env command and its isolation params | Read the binder's `env_contract` (`command`, `supports_isolation`, `isolation_params`); see [references/integration-branch.md](references/integration-branch.md) for env injection |
| **Default branch** | The repo's mainline (only the fallback base, not the build base) | Detect via `git remote show origin` (the `HEAD branch:` line), else whichever of `main`/`master` exists. Don't rely on `git symbolic-ref refs/remotes/origin/HEAD` — it's unset on many fresh clones |
| **Integration branch** | The binder's integration branch and its worktree | `karta/<slug>/integration`, where `<slug>` is the binder's `slug` field. This — not the default branch — is the base for the item's worktree (see `build:implement`) |
| **Worktree root** | Parent dir for per-item worktrees | Ask/default to a sibling dir (e.g. `../<repo>-worktrees/`) |
| **Git identity** | Author identity for commits | `git config user.name` / `user.email`. If unset, ask once or record an explicit "unattributed" note — do not silently invent one. This is **commit authorship only**, not a ticketing identity |
| **Project rules** | Component-structure, data-layer, and convention docs | Detect: contributor docs, lint configs, rules files; cite them during implementation/fixes if present, else fall back to inline generic conventions |
| **Repo policy** | Branch/CI/ruleset/deployment policy, only when the item touches those areas | Read root/area `AGENTS.md`, existing workflows, CI docs when remote policy is in scope. For details load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |

### Conditional UI/data annex settings (resolve only when the item carries the surface)

These apply **only** when the work item has `component_map` / `icon_map` / `token_changes` or a `visual` oracle. A non-UI item skips them entirely.

| Setting | What it is | How to resolve |
|-|-|-|
| **Component library** | UI-primitive library/libraries (0..n) and install path | Detect from `package.json` deps + existing imports; may be none — then the item's `component_map` "custom" entries are the build list |
| **Icon libraries** | Primary icon source + fallbacks | Detect from deps/imports; may be none |
| **Theme/token system** | Source of truth for colors / spacing / radius / typography | Detect: theme object, CSS custom properties, a design-tokens file (incl. W3C DTCG JSON — see [references/dtcg-tokens.md](references/dtcg-tokens.md)), a utility-class config, or plain CSS |
| **Data layer** | The API the UI reads from, if any | Detect: a GraphQL schema + codegen, OpenAPI/REST types, a tRPC router, generated TS client types; may be none. Note the detector `<data-layer-detector>` and the generated-code dir `<generated-code-dir>` to exclude |
| **Dev server URL/port** | Where the running app is served | Detect from dev-server config / framework defaults; record `<dev-server-port>` and any transitively-started backend port `<backend-port>`. Used only by the visual `karta-validate` loop |
| **Design source** | The design file/prototype the item validates against | Read the binder's `design_facts.source` and the item's `design_reference` (view/route ID, or the literal `none`) |

Record the resolved values — every later phase references them.

### DTCG token systems

When the theme/token row detects a **W3C DTCG-format design-token file system** (JSON leaves carrying `$value`/`$type`, usually with a token build tool like Style Dictionary / Terrazzo in devDependencies), resolve the DTCG-only settings and build the **token manifest** as described in **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. Everything DTCG-specific — the manifest, the autonomous token-add procedure, and the token-conformance check — is defined there and applies only when the item carries a UI surface and these settings were resolved. A DTCG *design* export mapped into a **non-DTCG** project uses the project's own token mechanism and skips all of it.

---

## Workflow

### Always-on mutation guard

Before any file mutation, apply [references/worktree-safety.md](references/worktree-safety.md): assert the intended root with `git rev-parse --show-toplevel`, check the current branch with `git branch --show-current`, and refuse implementation edits when the root or branch is wrong. Repeat this after creating a worktree, changing directories, resuming after context compaction, or any failed patch. After the worktree is created (`build:implement`), the intended root is the implementation worktree, not the original checkout. The binder is **read-only** to build — never edit it.

### Phase 0 — Classify intent before choosing a workflow  `build:classify`

Classify the request before framing the work:

- **implementation of a binder work item** — normal `karta-build` execution (from `build:gate` onward)
- **inspection aid** — behavior works, but the user needs to see or hold a state such as a login screen
- **bug fix** — behavior is broken or regressed
- **product feature** — new behavior not already in the binder
- **CI/policy change** — workflow, ruleset, branch, deployment, generated-contract, or environment behavior

If the user corrects the category, drop the old framing immediately. For inspection aids and CI/policy changes, keep scope explicit and contained unless the user asks for a permanent product change.

**Ticketless inspection-aid mode.** If the request is a narrow inspection aid and no binder work item drives it, do not force the binder gates. Use this limited flow:

1. Resolve the app dir/target, toolchain, default branch, worktree root, and relevant project rules from Project configuration.
2. State the high-impact mutation preview from `build:implement` and wait for confirmation before editing.
3. Create an isolated worktree from the default branch with a branch like `inspect_<short-slug>`.
4. Run the mutation guard before every edit.
5. Implement only the confirmed inspection aid.
6. Run the relevant lint/test/build checks.
7. Report the worktree path and changed files. Do not merge into integration unless the user explicitly asks.

This mode is for observability/inspection only. If the change becomes product behavior, convert it to a binder work item or an explicit product-feature request.

### Phase 1 — Input, validate, and gate the work item  `build:gate`

The input is `(binder path, work-item id)` — **not** a ticket file. Resolve both from the user (default binder location `.karta/binders/<slug>.json`; see [references/binder-reference.md](references/binder-reference.md)).

Three gates, **all must pass**. Any failing is an immediate hard stop — report and exit, no "continue anyway?".

**Gate 1 — The binder validates.** Run:

```bash
uv run skills/karta-plan/scripts/validate_binder.py --binder <binder path>
```

This checks schema validity, dependency cycles, and dangling `depends_on` references. If the orchestrator already validated the binder for this run, you may take that as satisfied rather than re-running — but never skip validation when invoked directly. On a validation failure, bail with the validator's output.

**Gate 2 — The item id exists.** Find the work item whose `id` equals the requested id in the binder's `work_items`. If no item matches, bail with the requested id and the list of available ids.

**Gate 3 — Dependencies are merged.** Every id in the item's `depends_on` must already be merged into the integration branch — i.e. its done ref exists. Per [references/integration-branch.md](references/integration-branch.md), check `refs/karta/<slug>/item-<dep-id>/done` for each dependency. If any dependency is unmet (no done ref), **halt with a call to action**: list the unmet dependencies and note that they must build and merge into `karta/<slug>/integration` first. `depends_on` is a scheduling constraint — do not build off a missing dependency.

**Resolve git identity** (Project configuration) for commit authorship only. There is no ticketing system, assignee, or status field — drop all of that machinery. If `git config` has no `user.email`/`user.name`, ask once or record an explicit "unattributed" note.

**Extract and cache** from the item for later phases:

- `ITEM_ID` — the item's `id` (drives the branch name and the `[karta:item-<id>]` commit marker)
- `ITEM_ORACLE` — the item's `oracle` (acceptance check: `type`, `assertions`, `command`, or an `opt_out` + `reason`)
- `ITEM_CONTRACT` — the item's `contract`, if present (the interface it exposes/consumes)
- `ITEM_DEPS` — the resolved `depends_on` ids (all merged, per Gate 3)
- `SERIALIZE` / `SHARED_RESOURCES` — `serialize` and `shared_resources`, if present (the orchestrator's concern, but note them)
- UI annex fields, **only if present**: `COMPONENT_MAP` (`component_map`), `ICON_MAP` (`icon_map`), `TOKEN_CHANGES` (`token_changes`), `DESIGN_REFERENCE` (`design_reference`), and the binder's `design_facts.source`

### Phase 2 — Sanity-check the item against the codebase  `build:sanity`

Read the work item and the binder's `scope`, `design_facts`, and `env_contract`. Then verify the item against the current code:

- **Do referenced/reused files still exist?** Read the paths the item's plan cites as existing. (Do not existence-check files the item is meant to create.)
- **Do new files conflict with existing ones?** Flag any path the item creates that already exists with different content — a conflict to resolve, not the greenfield case.
- **Do citations still resolve?** Search for any "reuse `<path>`" target with the host's fastest code-search tool.
- **Has the surrounding code drifted?** Confirm the function signatures and data shapes the item depends on still match — including anything a merged dependency introduced on the integration tip.
- **Contract sanity.** If the item declares an `ITEM_CONTRACT`, confirm the external artifact it names (a type, a schema, a contract test) still exists and is the shape the item expects.

**UI annex (only when UI fields are present):** verify `design_facts.source` exists; spot-check 2–3 `COMPONENT_MAP` entries against the resolved library's install path; check the item's route doesn't already exist with conflicting content. If the library can't be enumerated (minified/CDN), spot-check via its exported type surface and treat as best-effort.

If a mismatch matters, flag it and ask. Minor drift gets silently adapted and noted in the final report.

### Phase 3 — (reserved)

No pickup side-effects exist in the binder model — there is no status to transition and no assignee to set. Progress is tracked git-natively through commit markers, wave tags, and the `refs/karta/` namespace (see [references/integration-branch.md](references/integration-branch.md)). Proceed to `build:implement`.

### Phase 4 — Create an isolated worktree off the integration tip and implement  `build:implement`

**The item gets its own git worktree, branched off the current integration tip** — not the default branch. This is what resolves dependency chains: the integration tip already contains every merged dependency.

**4a. Pick the branch name**, embedding the item id so later phases can recover it:

```
karta/<slug>/item-<item-id>
```

**Sanitize** any slug-derived portion to `[a-zA-Z0-9_/-]` only (binder fields are untrusted input interpolated into shell commands).

**4b. Create the worktree** from the integration tip:

```bash
slug="<binder slug>"
integration="karta/${slug}/integration"
branch="karta/${slug}/item-<item-id>"
worktree="<worktree-root>/${branch//\//-}"
git worktree add "$worktree" -b "$branch" "$integration"
cd "$worktree"
```

Create `<worktree-root>` with the host's native filesystem operation if it does not exist. If the integration branch does not yet exist (first item in the binder), create it from the default branch first, per [references/integration-branch.md](references/integration-branch.md). If `git worktree add` fails because the branch or path already exists, **don't clobber it** — stop and ask; it usually means a prior run the user may want to resume.

Immediately after `cd "$worktree"`, run the mutation guard from [references/worktree-safety.md](references/worktree-safety.md): the actual root must equal the worktree path and the current branch must be the new item branch before any implementation edit.

**4c. Install dependencies.** Run `<install command>` in the worktree before any build/lint/test command — worktrees need their own dependency links.

**4c-bis. Build the token manifest (UI + DTCG token systems only).** When the item carries a UI surface and the project has a DTCG/tiered token system, build the token manifest before any token lookup — see **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. Skip entirely otherwise.

**4d. Implement the item** against the resolved conventions, stack-agnostically. Key rules:

**High-impact mutation preview.** Before editing auth, routing, guards, security, CI/CD, rulesets, branch policy, deployment, generated contracts, or environment files, state: the exact behavior change; likely files/workflows touched; what stays unchanged; and rollback/containment notes when relevant. Wait for confirmation when the user asked to approve first, or when the change introduces route/security/policy behavior the item did not already authorize.

**CI/policy items.** If the item touches CI, repository automation, branch policy, rulesets, required checks, deployment, generated contracts, or environment policy, load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) before editing. Summarize the current repo policy first, keep workflows thin, distinguish "runs" from "required", and do not add fork hardening, merge queues, CODEOWNERS, or similar controls unless repo policy or explicit direction requires them.

**General implementation rules (every stack):**

- Follow the project's structure and convention docs — cite a resolved rules doc if one exists, else apply sensible inline conventions.
- Implement against the resolved `ITEM_CONTRACT` when present — produce the interface the contract names; do not diverge from it silently.
- **Declare deferrals inline.** When you skip a test, stub a dependency, or defer an edge case, place a `KARTA-DEFER(<id>)` marker at the exact site per [references/declared-debt.md](references/declared-debt.md). A deferral is recorded, never silent — it surfaces in the final report.
- **Never weaken the oracle.** Do not edit or soften the item's `oracle`/acceptance assertions (or its `contract`) to make a check pass. On a genuine oracle-or-contract conflict — the item cannot be implemented as specified without violating one — **halt with a call to action** rather than silently diverging. Code, specs, and tests win; the implementer does not get to move the goalposts.

**Conditional UI/data implementation annex (only when the item carries UI fields):**

- Use `<component-lib>` components per `COMPONENT_MAP` — do not rebuild primitives the library provides; build the "custom" entries the map lists when there is no library.
- Use the exact icon imports from `ICON_MAP`; for each "Missing Icons" entry, add the custom SVG the plan flagged rather than substituting a different library icon.
- All styling references the project's theme/token system — never hardcode hex/px values that duplicate what the token system provides.
- **DTCG token systems:** consume only the tier the project's convention allows (typically semantic, never primitives) — look variables up in the token manifest, never by grepping generated CSS. An *additive semantic-tier token* the item's `TOKEN_CHANGES` pre-authorizes (operation `add`, semantic tier, name, per-context value) may be added autonomously; the full procedure is in **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. A `requires build-time confirmation` row, a needed token with no row, or no `TOKEN_CHANGES` at all routes to a question.
- Translate design mock data into the project's data layer (GraphQL with fragment colocation, REST calls, typed-client calls per the resolved layer) and the design's client-side navigation into the project's router.

All subsequent phases run from inside the worktree. Stay `cd`'d there until the skill finishes.

### Phase 5 — Deterministic gate (the floor)  `build:floor`

Run from the worktree before the acceptance loop. This is the floor under every non-opted-out item — compile / type-check / lint clean (see [references/definition-of-done.md](references/definition-of-done.md)):

```bash
<lint command>
<typecheck command>
<test command>
<build command>
```

Run whichever of these the project defines. If any fails, fix it in this thread — you own the code — and do not proceed to the acceptance loop until the floor is clean. A change that cannot clear the floor has not earned an acceptance review; if fixes take more than ~2 attempts, surface to the user.

**UI annex — token-conformance check (DTCG only, single pass folded into this phase, never a loop).** When the DTCG token settings were resolved, run the deterministic three-check scan (generated-artifact reproducibility; no primitive-tier consumption in new code; no hardcoded duplicates of existing tokens), scoped to files changed vs the integration tip. Stage new files first (`git add -A`). Full definitions in **[references/dtcg-tokens.md](references/dtcg-tokens.md)**.

### Phase 5b — Data-layer conformance loop (conditional UI/data, up to 3 rounds)  `build:datalayer`

**Conditional — UI/data items only.** This phase runs **only when the project has a data layer** (e.g. GraphQL with fragment colocation/codegen, or REST/OpenAPI/tRPC) **and** the item's changed files contain data operations. **Skip the entire phase** when there is no data layer at all, or when no changed file contains a data operation (computed in 5b-1). A backend/CLI/IaC item with no data-layer surface skips it outright. A missing conventions doc is **not** a skip trigger: when a data layer exists but no rules doc was resolved, still run the loop and fall back to the inline read-only pass in 5b-3 (against whatever conventions the repo documents; if truly none, check only that data operations are typed — no `any` — and not duplicated, and note the thin coverage in the report).

This is a UI/data-specific check, distinct from the generic acceptance gate (`build:acceptance`). It validates that created or modified components follow the project's data-layer conventions (for GraphQL: fragment colocation, fragment/operation naming, imports per the project's GraphQL rules, query/mutation tier boundaries; for other layers: schema conformance, typed-client usage) — citing the project's resolved data-layer rules doc where present — before the visual gate and the merge.

#### 5b-1. Identify target files

Only validate files created or modified **in this item** that contain data-layer operations. Use the resolved `<data-layer-detector>` from Project configuration — the literal `graphql(` is just the GraphQL example; for REST/tRPC the detector is "files importing the client or calling the typed endpoints." Anchor on the resolved detector, not the example token. Exclude generated code (the `<generated-code-dir>`, if any) and test files.

Compute the changed-file set **relative to the current integration tip** — the item branched off integration (`build:implement`), so the integration branch is the base, **not** the default branch. Stage new files first (`git add -A`) so untracked, just-created files are included, then enumerate changed files relative to the integration tip:

```bash
integration="karta/<slug>/integration"
git diff --name-only --diff-filter=ACMR "$integration"...HEAD -- <app-dir/target>
```

Filter the result in memory or with the host's native tools, keeping only files that:

- match the resolved framework's source extensions
- are not under `<generated-code-dir>` when one exists
- are not test/spec files
- contain the resolved `<data-layer-detector>` pattern or import

Use a repo-owned helper script when this logic becomes non-trivial; do not assume Bash pipelines, `grep`, `find`, or WSL exist locally. This produces a list of modified source files (excluding generated code and tests) that contain data-layer operations. If the list is empty, log "No data-layer files modified — skipping data-layer validation" and proceed to the acceptance loop (`build:acceptance`).

#### 5b-2. Per-round structure

```
round = 1
while round <= 3:
  # Re-run the floor if we made fixes (skip for round 1 — the floor `build:floor` already passed)
  if round > 1:
    run <lint command> && <test command>
    if fail: fix, re-run lint/test

  invoke the data-layer conformance validator (see 5b-3)
  parse the structured report

  issues_count = count of Issues (not Warnings) across all files

  if issues_count == 0:
    break — validation passed

  if round == 3:
    surface residual issues to the user (AskUserQuestion OR host user-input prompt)
    break

  implement fixes based on the report   # 5b-5
  round += 1
```

#### 5b-3. Invoke the data-layer conformance validator

Run a **read-only conformance check** scoped to the target file list from 5b-1. **Strongly prefer a separate read-only subagent OR host worker so the check runs in an isolated context** — the implementer must not grade its own work. If the project provides a dedicated data-layer conformance validator (a subagent, host worker, or skill — e.g. a GraphQL-conventions checker for a GraphQL/Apollo stack, or the project's REST/OpenAPI/tRPC schema-conformance equivalent), use it. Pass it the file list and ask it to check each file against the project's data-layer rules.

Only when the environment provides no subagent, host worker, OR skill mechanism, fall back to an inline read-only pass against the project's data-layer-rules doc, noting that this loses context isolation (the implementer is reviewing its own output). Either way the validator must be **read-only** — it reports, it never edits — and **MUST** return a per-file `STATUS: PASS | ISSUES_FOUND` line plus a summary containing an explicit issue count (e.g. `Issues found: N across M files`), so the loop parses the exit condition deterministically.

#### 5b-4. Parse the report and decide

The report MUST include a per-file `STATUS: PASS | ISSUES_FOUND` line and a summary line with an explicit issue count (e.g. `Issues found: N across M files`); the loop parses that count as its exit condition. Since 5b-1 already excludes generated code and tests, only Issues in non-generated files count.

| Condition | Action |
|-|-|
| `Issues found: 0` (all files PASS) | Exit loop — validation passed |
| Issues found, round < 3 | Fix issues in the main thread, re-run lint/test, re-validate |
| Round 3 reached with residual issues | Stop — surface to the user via `AskUserQuestion` OR host user-input prompt; record the residual as a `KARTA-DEFER` declared-debt marker per [references/declared-debt.md](references/declared-debt.md) |

**Warnings are acceptable** — only Issues (clear rule violations) trigger fixes. Do not attempt to fix Warnings.

#### 5b-5. Implement fixes (main thread)

When fixing issues between rounds:

- Use the report's category and line hints to locate each violation.
- Cross-reference the project's data-layer-rules doc for the correct pattern when the category isn't self-explanatory.
- After fixes, re-run `<lint command> && <test command>` before the next validation round — fixes must not break the floor.

#### 5b-6. Edge cases

- **The validator crashes or returns no output:** treat as a failed round. Retry once. If it fails again, skip data-layer validation and note the failure in the final report — don't block on a tooling failure.
- **All issues are in generated code:** shouldn't happen (generated code is excluded in 5b-1), but if it does, treat as a pass.
- **A file was deleted between rounds:** re-compute the target file list before each round to avoid passing stale paths.

### Phase 6 — Acceptance loop  `build:acceptance`

Once the floor is clean, run the item's acceptance check through the verification gate. The gate is **read-only** — it reports, it never edits — and it runs in a fresh, thin context (only the worktree, the binder, and the item's `oracle`/`contract`). See [references/verification-gate.md](references/verification-gate.md).

**Opt-out items skip the loop.** When `ITEM_ORACLE.opt_out` is true, record the `reason` and skip acceptance (the floor still applies). Report the opt-out in the final summary — opt-outs are explicit and surfaced, never silent (see [references/definition-of-done.md](references/definition-of-done.md)).

**Choose the gate by oracle type:**

- **`oracle.type == visual`** → `karta-validate`. It compares rendered output against the design (UI annex; resolve `<dev-server-port>`, the design source, and the item's `design_reference`). The per-round capture/compare mechanism `karta-validate` uses is in **[references/design-validation-loop.md](references/design-validation-loop.md)**. Skip the visual gate when `design_reference` is `none`. **Before invoking `karta-validate`, the app must be up** — bring it up per the dev-server lifecycle below.
- **any other type** (`unit` / `integration` / `e2e` / `smoke`) → `karta-verify`. It dispositions each of the oracle's `assertions` against the actual diff, and — when the item declares an `ITEM_CONTRACT` — checks the diff against the external contract artifact (a type-checker, schema, or contract test), not against the binder's claim.

**Dev-server lifecycle for the visual gate (conditional — `oracle.type == visual` only).** A `karta-verify` (non-visual) item skips all of this. For a visual item, `karta-validate` needs the app running before it can capture and compare, so bring it up here, before invoking the gate.

**First, honor a provided env.** The env may already be supplied by the binder's `env_contract` or by the orchestrator (a wave-bound env, started once and torn down once for the whole wave per [references/integration-branch.md](references/integration-branch.md)). When the wave env is present, use the env it exposes (`env_contract.command`, and `env_contract.isolation_params` such as `PORT` when `supports_isolation` is true) instead of starting your own — and **do not tear it down** (the orchestrator owns it). Only when the item is directly invoked with no provided env do you manage the dev server yourself, per the steps below.

Do not assume Bash, WSL, POSIX background syntax, `/tmp`, `curl`, `grep`, `lsof`, or `kill` exist on the developer machine. Use the host's native process and HTTP facilities, or a repo-owned helper script, and record the exact command/handle you used.

- **6-dev-a. Check port availability** with a host-native mechanism (a Python socket probe, a PowerShell TCP lookup, the project's dev-server status command, or the platform's equivalent). Check both `<dev-server-port>` and `<backend-port>` when the dev target starts a backend. **If something is already on either port, bail and ask the user to stop it first — never stop another process's dev server.** (This guards against *other* processes. When you must **restart** your own dev server — e.g. after a token rebuild or a degraded server mid-loop — first stop the recorded handle to free the port, then repeat these steps; otherwise this check sees your own still-running server and bails.)
- **6-dev-b. Start the dev server as a managed background process/session.** Record its process id or host process handle (call it `DEV_SERVER_PID` or the host equivalent), plus its log location. Do not use POSIX `&` unless the host shell is known to support it. If the dev target transitively starts a backend/API service (resolved in Project configuration), that service comes up on `<backend-port>` too — both are needed when the view depends on the backend for data. Note that port for the teardown (`build:teardown`).
- **6-dev-c. Health-poll the actual `design_reference` route** (not just `/`) with a host-native HTTP client until it returns an expected status such as `200`, `307`, or `308` — many dev servers compile/warm pages on demand, so `/` warming proves nothing about the target view. Use an explicit retry limit around **60 seconds** and capture failure output. If the route is not responding after ~60s, stop the recorded handle and bail with the error (common causes: port conflict, a build error the floor didn't catch, missing env vars). **A bare 2xx/3xx is not proof the view rendered when it's behind auth** — an unauthenticated request to a protected route can return `200` on a login page or `3xx` to `/login`, passing this poll while the target view is still unreachable. If the route requires authentication, detect the auth-redirect / login-page response here and treat establishing a logged-in session (and ensuring any backend service the view needs is up) as a `karta-validate` prerequisite, not something this poll satisfies — see [references/design-validation-loop.md](references/design-validation-loop.md) (`dvl:invoke:auth`).
- **6-dev-d. Store the recorded handle** (`DEV_SERVER_PID` and `<backend-port>`, if any) for the teardown (`build:teardown`).

**Kickback and caps.** On any finding, the gate kicks the work back to this skill for **bounded self-correction**, then re-runs on the corrected diff. Per [references/verification-gate.md](references/verification-gate.md) the caps differ by gate:

- **Safety / boundary scan** (the seven smart-surfaced-review signals re-run on the real diff; see [references/smart-surfaced-review.md](references/smart-surfaced-review.md)) — **max 3 attempts, then escalate to the human.** A boundary the item never justified is a safety question.
- **Acceptance / contract gate** — **max 2 attempts, then halt with a call to action.** On the second failed attempt, the choice is fix-and-rerun or place a `KARTA-DEFER` declared-debt marker per [references/declared-debt.md](references/declared-debt.md) that records the unmet assertion as a named deferral.

Only on cap exhaustion does the gate halt or escalate — otherwise it self-corrects within the caps and moves on.

### Phase 7 — Dev-server teardown (cleanup for the visual gate)  `build:teardown`

**Conditional — visual items that started a dev server.** A non-visual item is a no-op here. **Always runs when this run started a server**, regardless of outcome — whether the skill succeeded, failed at a gate, or errored after bring-up, the ports it opened must be freed. Structure the teardown to run on every exit path after the acceptance loop's bring-up (`build:acceptance`).

Stop **only the process or process tree this run started**, using the host's native process handle (the `DEV_SERVER_PID` recorded in 6-dev-b/d). If the port is still held afterward, clean up an orphan only when you can prove it was spawned by this run's recorded dev command — **never stop an unrelated process** that happens to bind the same port (the mirror of 6-dev-a's "do not stop another process's dev server"). Apply the same guard to `<backend-port>` when the dev target started a backend/API service: stop only what this run started, then free the backend port too.

**Do not tear down a provided env.** When the acceptance loop (`build:acceptance`) used a wave-bound env from the binder's `env_contract` / the orchestrator instead of starting its own server, leave it running — the orchestrator owns its lifecycle and tears it down once for the whole wave (see [references/integration-branch.md](references/integration-branch.md)). This phase only stops servers *this run* started. If the skill exited before the acceptance loop (`build:acceptance`) brought up a server (e.g. at the input gate `build:gate`), this phase is a no-op. (Port-conflict and process-handling details also live in [references/design-validation-loop.md](references/design-validation-loop.md).)

### Phase 8 — (reserved)

### Phase 9 — Commit, secret-scan, and merge into integration — NO PR  `build:merge`

Run from inside the worktree. There is **no PR**. The terminal state is a tagged item merged into the integration branch; the user reviews and merges that branch.

**9a. Secret scan before every commit.** Before each commit, run the secret scan from [references/secret-scan.md](references/secret-scan.md) against the **staged diff** only. On a hit, **block the commit and surface the finding** (file, line, matched pattern); mark the item failed with the scan output, preserve the worktree, and halt. Resolution requires removing or rotating the secret (or an in-repo allow-list entry, reviewed alongside the code) before retry.

**9b. Commit** with the item marker in the subject line:

```
[karta:item-<item-id>] <summary>
```

**9c. Merge into the integration branch** per [references/integration-branch.md](references/integration-branch.md):

1. Rebase/merge the item branch onto the **current** integration tip (which may have advanced as wave-mates merged).
2. **Re-validate the oracle against the merged result** — the tip moved, so the acceptance check must pass on what actually lands, not on the pre-merge branch. On a merge conflict or a re-validation failure, do a **bounded rebuild** against the new tip, or **halt** if the cap is exhausted.
3. Merge (ff or no-ff).
4. Write `refs/karta/<slug>/item-<item-id>/done` → the merge commit. On a halt, write `refs/karta/<slug>/item-<item-id>/failed` → the failing tip instead.

**Do not open a PR.** No `gh`/`glab`/`tea`, no push-to-review, no review-status transition.

### Phase 10 — Report back  `build:report`

Brief summary to the user (~8 lines):

- **Item id** and the binder slug
- **Worktree path** — so the user knows where the checkout lives
- **Integration tip** the item merged to (the merge commit / done ref), or the failed ref on a halt
- **Acceptance result** — which gate ran (`karta-verify` / `karta-validate` / opted out), final disposition, rounds used, any residual finding
- **Declared-debt summary** — every `KARTA-DEFER` marker placed (what, why, follow-up), per [references/declared-debt.md](references/declared-debt.md); a deferred item is never reported as fully complete without its deferral list
- **Secret-scan status** — clean, or blocked-with-finding
- A self-assessment from the automated gates, explicitly flagging anything nothing checked (e.g. accessibility) as needing manual review rather than implying it passed

**On a halt, preserve the failing item's worktree and print its path.** Leave the worktree in place on success too — re-runs and review iterations frequently need it back.

---

## Gotchas

- **No PR — ever.** The terminal state is a tagged item merged into `karta/<slug>/integration`. The user reviews and merges the integration branch. No `gh`/`glab`/`tea`, no review transition.
- **Branch off the integration tip, not the default branch.** That tip already contains every merged dependency; building off the default branch would lose them.
- **Dependencies must be merged before pickup.** Gate 3 checks `refs/karta/<slug>/item-<dep>/done` for every `depends_on`; an unmet dependency halts.
- **The binder is read-only to build.** A build step never edits the plan that governs it — that would corrupt its own governance.
- **Never weaken the oracle.** Don't edit or soften the acceptance assertions or contract to make a check pass. On a genuine conflict, halt — code/specs/tests win.
- **Commit marker is mandatory.** Every commit subject carries `[karta:item-<id>]` so resume and integration can trace it.
- **Secret scan before every commit.** It inspects the staged diff and blocks on a hit. Block, surface, mark failed, preserve the worktree — don't write the commit.
- **Acceptance caps differ on purpose.** Safety/boundary gate: 3 attempts then escalate to the human. Acceptance/contract gate: 2 attempts then halt-with-CTA. The gate kicks findings back to build for bounded self-correction; only exhaustion halts.
- **Re-validate the oracle against the merged tip.** A text-clean merge can still break semantics (a wave-mate renamed a helper). The acceptance check must pass on what lands, not on the pre-merge branch.
- **Always work in the worktree.** After the worktree is created (`build:implement`), every implementation path resolves under the worktree root. The mutation guard in [references/worktree-safety.md](references/worktree-safety.md) is mandatory before every edit.
- **Don't clobber an existing worktree.** If `git worktree add` fails, stop and ask — it usually means a resumable prior run.
- **UI rules are conditional.** Component maps, icon imports, token rules, and the visual `karta-validate` loop apply only when the item carries `component_map` / `icon_map` / `token_changes` or a `visual` oracle. A backend / CLI / data / IaC item skips the whole annex.
- **The visual gate is expensive.** Each `karta-validate` round can spawn a browser session and capture/compare workers; the loop is capped — don't exceed it.
- **Data-layer conformance is read-only and isolated.** The data-layer conformance loop (`build:datalayer`) runs a separate read-only subagent/host worker so the implementer doesn't grade its own work; the validator returns a `STATUS` + `Issues found: N` contract the loop parses. It's conditional — no data layer, or no changed file with a data op, skips it. Compute the changed-file set vs the **integration tip**, not the default branch.
- **The visual gate needs the app up — and the route, not `/`.** Health-poll the actual `design_reference` route (200/307/308, ~60s cap); a 2xx/3xx on a protected route can be the login page, not the view. Honor a provided wave env (`env_contract`/orchestrator) when present; else manage the dev server yourself.
- **Never stop another process's dev server.** Bring-up bails if a port is already taken; teardown stops only the handle this run recorded (frontend and backend ports), and leaves a wave-bound env alone — the orchestrator owns that one. Teardown runs on every exit path after bring-up.
- **Declare deferrals inline.** A skipped test or stubbed dependency gets a `KARTA-DEFER` marker at the site; the report surfaces every one. A deferred item is never reported as fully done without its list.
- **Opt-outs are explicit and surfaced.** When `oracle.opt_out` is set, skip acceptance (not the floor), record the reason, and report it. There is no silent opt-out.
- **The floor is non-negotiable.** A change that won't compile / type-check / lint does not earn an acceptance review — it earns a surfacing.
- **Preserve the failing worktree on halt and print its path.** Don't tear it down — the user needs it to resume.
- **Don't re-plan.** The plan lives in the binder. Your job is execution of one item, not re-planning. The planning counterpart is `karta-plan`.
