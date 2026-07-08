---
name: karta-build
model: sonnet
effort: high
description: >-
  Use when implementing one work item from a karta binder in an isolated git worktree — stack-agnostic (frontend, backend, CLI, data, IaC, …) — running the project's lint/test/build plus the item's acceptance check, tagging commits, and completing the item on its branch (in a wave the orchestrator merges; invoked directly the worker merges into the per-binder integration branch). No PR. Trigger phrases: "build this binder item", "implement work item `<id>`", "karta-build `<binder> <id>`".
---

karta-build takes **one work item from a validated binder** and carries it from pickup to a tagged set of commits that complete the item on its own branch — all inside an isolated git worktree. How the item finishes depends on how the skill was invoked: invoked directly on one item, the worker merges its commits into the binder's **integration branch**; dispatched inside a wave, the worker stops at its committed branch and the orchestrator merges. It is stack-agnostic: the same flow implements a frontend view, a backend endpoint, a CLI command, a data migration, or an IaC change. It does **not** open a PR. The user reviews and merges the integration branch.

The binder (`.karta/binders/<slug>.json`) is the cross-skill contract. Each work item carries an `oracle` (its acceptance check) and an optional `contract` (the interface it exposes or consumes). karta-build reads the binder — it never writes to it during a run (see [references/binder-reference.md](references/binder-reference.md)). The planning counterpart is `karta-plan`; the read-only acceptance and visual gates are `karta-verify` / `karta-validate`.

## How this skill adapts to your project

karta-build is **stack-agnostic**. It does not assume a frontend framework, component library, data layer, branch convention, or repo layout. It resolves a small set of project settings up front (detect → ask), then implements the item against whatever it finds. Where this document shows a concrete tool, command, or library, treat it as an **example**, not a requirement.

The UI-specific machinery — component maps, icon imports, token rules, the data-layer conformance loop (`build:datalayer`), the dev-server lifecycle for the visual gate (the acceptance loop's bring-up `build:acceptance` and its teardown `build:teardown`), and the visual design-validation loop — is a **conditional annex** that applies only when the work item carries those fields (`component_map`, `icon_map`, `token_changes`), a data-layer surface, or a `visual` oracle. A backend / CLI / data / IaC item skips the entire annex.

## Project configuration (resolve once, up front)

Resolve each setting in this order: **explicit user input → detect from the repo → ask the user** (batch all unknowns into a single question). Do not prompt for things you can detect. **When detection conflicts with explicit user input or the project's documented/blessed stack, the stated stack wins** — confirm it rather than asserting what's merely present (a repo can be mid-migration).

| Setting | What it is | How to resolve |
|-|-|-|
| **App dir / target** | Where the item's code lives, and its task-target name | Detect from the binder's `scope.included` and the repo layout; in a monorepo or polyglot repo, root all paths/commands at the area the item targets; else ask |
| **Command cwd** | The working directory each floor/oracle/env command runs in | Read the oracle's `cwd` and `env_contract.cwd` — both **relative to the item's worktree root** (default = the worktree root itself). When the binder omits it, resolve it to the toolchain's own root for the area the item targets. In a multi-root repo each root keeps its own cwd; when one command must span roots, target each through the runner's own flag (`npm --prefix`, `pnpm -C`, `make -C`, `nx run`) per [references/binder-reference.md](references/binder-reference.md) "Execution context" — never a synthetic root or shim. Project-local tools resolve from this dir — see [references/integration-branch.md](references/integration-branch.md) for env injection |
| **Toolchain commands** | install / lint / test / build / typecheck invocations | Detect from package scripts + task runner (npm/pnpm/yarn, Make, Nx, Turbo, Cargo, Poetry, …); record `<install command>`, `<lint command>`, `<test command>`, `<build command>`, `<typecheck command>`. When both package scripts and a task runner exist, prefer the project's documented entrypoint — a bare script pick can skip orchestrated lint/coverage the runner bundles |
| **Env command** | The dev/test env command and its isolation params | Read the binder's `env_contract` (`command`, `supports_isolation`, `isolation_params`); see [references/integration-branch.md](references/integration-branch.md) for env injection |
| **Required runtime** | The runtime versions the floor/env commands need | Read the binder's optional `runtime_contract` (`runtimes[]` with `name`/`version`, `on_unavailable: halt`). When absent, fall back to detecting the repo's pin files (`.nvmrc`, `.tool-versions`, `.python-version`) and manifest fields (`engines`, `requires-python`). A preflight in `build:sanity`/`build:floor` **checks** the active runtime against the declaration and **halts** on mismatch — karta never installs or selects a runtime itself |
| **Default branch** | The repo's mainline (only the fallback base, not the build base) | Detect via `git remote show origin` (the `HEAD branch:` line), else whichever of `main`/`master` exists. Don't rely on `git symbolic-ref refs/remotes/origin/HEAD` — it's unset on many fresh clones |
| **Integration branch** | The binder's integration branch and its worktree | `karta/<slug>/integration`, where `<slug>` is the binder's `slug` field. This — not the default branch — is the base for the item's worktree (see `build:implement`) |
| **Worktree root** | Parent dir for per-item worktrees | Ask/default to a sibling dir (e.g. `../<repo>-worktrees/`) |
| **Git identity** | Author identity for commits | `git config user.name` / `user.email`. If unset, ask once or record an explicit "unattributed" note — do not silently invent one. This is **commit authorship only**, not a ticketing identity |
| **Project rules** | Component-structure, data-layer, and convention docs | Detect: contributor docs, lint configs, rules files; cite them during implementation/fixes if present, else fall back to inline generic conventions |
| **Repo policy** | Branch/CI/ruleset/deployment policy, only when the item touches those areas | Read root/area `AGENTS.md`, existing workflows, CI docs when remote policy is in scope. For details load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
| **stack packs** | Advisory expert do's/don'ts to write against, with an enforceable Review checklist | Read the binder's `sme`; resolve each id against the project overlay `.karta/sme/*.md` laid over the built-in [references/sme/](references/sme/) |

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
uv run --script skills/karta-plan/scripts/validate_binder.py --binder <binder path>
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
- `RUN_MODE` — **single-item hatch** vs **orchestrated wave**. This is an **explicit signal**, not something you infer from repo state: `karta-deliver` tells the worker it is in wave mode when it dispatches it; a worker invoked directly with no such signal defaults to single-item. This decides who owns the terminal merge in `build:merge` — see [references/integration-branch.md](references/integration-branch.md). Do not read the integration branch's existence or the presence of wave-mates to guess the mode.
- UI annex fields, **only if present**: `COMPONENT_MAP` (`component_map`), `ICON_MAP` (`icon_map`), `TOKEN_CHANGES` (`token_changes`), `DESIGN_REFERENCE` (`design_reference`), and the binder's `design_facts.source`
- `SME_PACKS` — the binder's `sme` list (advisory expert pack ids), if present; resolved and loaded in `build:implement`, self-checked before the gate in `build:acceptance` (6-sme)

### Phase 2 — Sanity-check the item against the codebase  `build:sanity`

Read the work item and the binder's `scope`, `design_facts`, and `env_contract`. Then verify the item against the current code:

- **Do referenced/reused files still exist?** Read the paths the item's plan cites as existing. (Do not existence-check files the item is meant to create.)
- **Do new files conflict with existing ones?** Flag any path the item creates that already exists with different content — a conflict to resolve, not the greenfield case.
- **Is a shared file co-owned by an earlier item?** An item may legitimately modify a file an earlier **depended-on** item created — e.g. a view item adding its route to the app shell's `app.ts`, or an endpoint item registering itself in `main.py` — as long as that file is inside the binder's `scope.included`. (There is no per-item scope field; the binder's `scope.included` is the boundary.) This is allowed when the edit is **additive and scoped to the item's own surface** (registering a route, mounting a component, adding a handler). Make the smallest change that wires in this item; do not refactor or restyle the co-owned file beyond what this item needs. If the edit would rewrite shared structure rather than extend it, that is blast-radius the item did not authorize — flag it and ask. Two wave-mates that both edit a co-owned file is a serialization concern the orchestrator's parallelism gates handle, not a build-time decision; honor a declared `serialize` / `shared_resources` rather than racing the edit.
- **Do citations still resolve?** Search for any "reuse `<path>`" target with the host's fastest code-search tool.
- **Has the surrounding code drifted?** Confirm the function signatures and data shapes the item depends on still match — including anything a merged dependency introduced on the integration tip.
- **Contract sanity.** If the item declares an `ITEM_CONTRACT`, confirm the external artifact it names (a type, a schema, a contract test) still exists and is the shape the item expects.
- **Runtime sanity.** Read the binder's optional `runtime_contract`. For each declared runtime, compare the active version on the host (`node --version`, `python --version`, the host equivalent) against the entry's `version`. When the binder carries no `runtime_contract`, do a best-effort detect from the repo's pin files (`.nvmrc`, `.tool-versions`, `.python-version`) and manifest fields (`engines`, `requires-python`). Note any mismatch — the runtime preflight in `build:floor` halts on it. Surfacing it here means a tool's hard refusal later is not a surprise. karta does **not** install or select a runtime; it only checks and reports.

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

**4c-ter. Load the stack packs (when `SME_PACKS` is non-empty).** For each id in `SME_PACKS`, resolve the pack file — the project overlay `.karta/sme/<id>.md` in the worktree, else the built-in [references/sme/](references/sme/) `<id>.md`. Read each resolved pack and hold its **Do / Don't / Patterns / Review checklist** as implementation guidance for this item; in a polyglot repo apply the pack(s) matching the area this item targets. If a pinned id resolves to no file, **halt the item with a call to action** — report each missing id and both resolution paths tried (`.karta/sme/<id>.md`, `references/sme/<id>.md`). A pinned pack the build cannot load is a binder error to fix via karta-plan, never a note to skim past; `karta-verify` treats the same gap as BLOCKED. Follow this guidance while implementing (4d) and while fixing any gate kickback.

**Precedence:** a documented convention in the target repo's own CLAUDE.md/AGENTS.md wins over a built-in pack rule. When the two conflict, follow the repo rule and declare the deviation with a `KARTA-SME-OVERRIDE` marker (see 6-sme) whose rationale cites the repo rule as `repo-rule: <path>:<line-or-section>`.

**4d. Implement the item** against the resolved conventions, stack-agnostically. Key rules:

**High-impact mutation preview.** Before editing auth, routing, guards, security, CI/CD, rulesets, branch policy, deployment, generated contracts, or environment files, state: the exact behavior change; likely files/workflows touched; what stays unchanged; and rollback/containment notes when relevant. Wait for confirmation when the user asked to approve first, or when the change introduces route/security/policy behavior the item did not already authorize.

**CI/policy items.** If the item touches CI, repository automation, branch policy, rulesets, required checks, deployment, generated contracts, or environment policy, load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) before editing. Summarize the current repo policy first, keep workflows thin, distinguish "runs" from "required", and do not add fork hardening, merge queues, CODEOWNERS, or similar controls unless repo policy or explicit direction requires them.

**Greenfield / scaffold mode (foundation or first item only).** Trigger off the existing foundation signal, not a fresh judgment call: the integration branch does not yet exist (this is the first item in the binder, `build:implement` is creating `karta/<slug>/integration` from the default branch) **and** the item's contract is to stand up the project/framework rather than edit against existing conventions. There is no prior convention to follow and no `component_map` to map against — this item establishes the conventions the rest of the binder builds on. Rules:

- **The framework's own official generator is allowed here** (e.g. `ng new`, `create-next-app`, `cargo new`, `npm create vite@latest`, `django-admin startproject`). It is deterministic and blessed — the project's own way to lay down a working baseline; hand-writing what the generator emits is not more correct. Run it from inside the worktree, against the resolved app dir/target.
- **Bound the scaffold to the item's contract.** A generator may write many files (a sample app, demo routes, starter assets); keep only what the item's `scope.included` and `ITEM_CONTRACT` call for. Do not pull in extra framework add-ons, sample features, or demo pages the item did not ask for — that is scope creep the same as in an edit-mode item. **Note generated-but-unused files in the final report** (`build:report`); do not edit the read-only binder to record them.
- **Re-resolve the toolchain and oracle commands after the generator runs.** Before the generator there was nothing to detect; the resolved `<lint command>` / `<test command>` / `<build command>` and the oracle's `command` must be re-derived from what the generator actually produced (package scripts, `angular.json` targets, `Cargo.toml`, etc.) before the floor runs.
- **Satisfy an oracle-named check the fresh scaffold lacks ONLY via the framework's own official add/plugin command — never by weakening or skipping the oracle.** A generator often omits a target the oracle names (e.g. `ng new` ships no `lint` target). The fix is the framework's own blessed mechanism to wire it (e.g. `ng add @angular-eslint/schematics`, which installs the linter and adds the `lint` target) so the named check exists and runs against your code. If no official mechanism exists, **halt with a call to action** — do not hand-invent config. And the carve-out is narrow: it covers only a check that is **genuinely absent** from a fresh scaffold. A check that **exists but fails** is a real failure under the acceptance cap, never the absent-check case — never rename the check, narrow its globs, mark it non-blocking, defer it, or edit the oracle to a check the bare scaffold happens to pass.
- **Track the lockfile the generator emits.** A scaffold generator produces a dependency lockfile (`uv.lock`, `package-lock.json`, `pnpm-lock.yaml`, `Cargo.lock`, …). For an application, **commit it** — it is part of the deliverable, and a tracked lockfile is what makes every later item's worktree and CI resolve the same dependency graph (see [references/definition-of-done.md](references/definition-of-done.md), Lockfiles). Decide this once, here at the foundation item; do not leave each item to regenerate an untracked lockfile.
- **Expect the scaffold to trip the safety gate.** A generator's footprint (many new files, config it touches) trips smart-surfaced-review #4/#5 (large/structural change; new config or policy surface) at the acceptance safety scan — pre-justify it in the high-impact mutation preview and the report so the boundary scan reads it as the item's authorized scaffold, not an unexplained blast radius.

**General implementation rules (every stack):**

- Follow the project's structure and convention docs — cite a resolved rules doc if one exists, else apply sensible inline conventions.
- Implement against the resolved `ITEM_CONTRACT` when present — produce the interface the contract names; do not diverge from it silently.
- **Declare deferrals inline.** When you skip a test, stub a dependency, or defer an edge case, place a `KARTA-DEFER(<id>)` marker at the exact site per [references/declared-debt.md](references/declared-debt.md). A deferral is recorded, never silent — it surfaces in the final report. This inline use is the implementer's call; it is **not** a way to clear the acceptance gate — a capped acceptance failure is never escaped by self-declaring debt (see the acceptance cap in `build:acceptance` and [references/declared-debt.md](references/declared-debt.md)).
- **Never weaken the oracle.** Do not edit or soften the item's `oracle`/acceptance assertions (or its `contract`) to make a check pass. On a genuine oracle-or-contract conflict — the item cannot be implemented as specified without violating one — **halt with a call to action** rather than silently diverging. Code, specs, and tests win; the implementer does not get to move the goalposts. When a *fresh scaffold* lacks a check the oracle names, that is the absent-check case, not a conflict — provision the named tooling through the framework's own add/plugin command (see Greenfield / scaffold mode above). A check that **exists but fails** is always a real failure, never the absent-check carve-out.

**Conditional UI/data implementation annex (only when the item carries UI fields):**

- Use `<component-lib>` components per `COMPONENT_MAP` — do not rebuild primitives the library provides; build the "custom" entries the map lists when there is no library.
- Use the exact icon imports from `ICON_MAP`; for each "Missing Icons" entry, add the custom SVG the plan flagged rather than substituting a different library icon.
- All styling references the project's theme/token system — never hardcode hex/px values that duplicate what the token system provides.
- **DTCG token systems:** consume only the tier the project's convention allows (typically semantic, never primitives) — look variables up in the token manifest, never by grepping generated CSS. An *additive semantic-tier token* the item's `TOKEN_CHANGES` pre-authorizes (operation `add`, semantic tier, name, per-context value) may be added autonomously; the full procedure is in **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. A `requires build-time confirmation` row, a needed token with no row, or no `TOKEN_CHANGES` at all routes to a question.
- Translate design mock data into the project's data layer (GraphQL with fragment colocation, REST calls, typed-client calls per the resolved layer) and the design's client-side navigation into the project's router.

All subsequent phases run from inside the worktree. Stay `cd`'d there until the skill finishes.

### Phase 5 — Deterministic gate (the floor)  `build:floor`

**Runtime preflight — check, then halt (never auto-provision).** A floor command can hard-refuse on a runtime mismatch: a CLI that demands a minimum Node exits non-zero before any of your code runs. So before any floor command, check the active runtime against the declaration:

1. For each runtime in the binder's `runtime_contract` (or, when absent, each version pinned by the repo's `.nvmrc` / `.tool-versions` / `.python-version` / `engines` / `requires-python` detected in `build:sanity`), compare the active host version against the entry's `version`.
2. **On a mismatch, halt with a call to action** — report the required version, the active version, and the pin file/source. karta does **not** install or select a runtime; provisioning a runtime is a hermeticity and supply-chain concern that stays the operator's. `on_unavailable` carries the single value `halt`; this is the same hard-gate idiom karta uses for the playwright/uv preflights — surface the gap, do not auto-fix.
3. When the repo declares its own version manager (a `mise`/`asdf`/Volta config), the floor and oracle commands **may** route through it (e.g. `mise exec -- <command>`) so they run under the declared version. This is using the repo's own pinned runtime, not karta selecting one.

**Tool-imposed runtime floors — a floor that is in no pin file.** The preflight above checks *declared* floors (the `runtime_contract`, or pins in `.nvmrc` / `.tool-versions` / `.python-version` / `engines` / `requires-python`). A second class exists: a floor **imposed by a tool itself** that no pin file records — `@angular/cli@22` hard-refuses Node < 24.15.0, a bundler rejects an old Node, a formatter needs a newer Python. It surfaces only as a tool's hard-refusal at install/run, after the declared preflight has already passed clean. The adapt-vs-halt choice is **explicit and decided by mode**, never improvised in the moment:

- **Greenfield / scaffold (the foundation item is choosing the toolchain anyway).** Pinning a **tool version** compatible with the host's *active, unchanged* runtime — e.g. `@angular/cli@21` when the host is Node 24.14 — is a legitimate scaffold decision: it selects a tool, not a runtime. Adapt, pin the compatible version, and **note it in the report** (`build:report`) — valid only while the pinned version still satisfies every oracle-named check; if no host-compatible version can, that is a real floor failure, so halt. karta still installs and selects no runtime.
- **Non-greenfield (edit mode).** An existing project already pins its tools; a tool hard-refusing on the host runtime is an **environment mismatch**, not something to silently paper over by downgrading a dependency the project relies on. **Halt with a call to action** — name the tool, its required runtime, and the active runtime — the same surface-don't-fix idiom as the declared preflight. Surface the durable fix in that CTA: the floor should be recorded in the binder's `runtime_contract` (a re-plan via karta-plan — the binder is read-only to build) so the *declared* preflight catches it next run, rather than leaving it to a runtime hard-refusal.

The line that must not blur: pinning a *tool* version to fit the host's runtime is a tool choice (allowed in greenfield); selecting or installing a *runtime* is never karta's to make.

Only once the active runtime satisfies the declaration do the floor commands run.

Run from the worktree before the acceptance loop. This is the floor under every non-opted-out item — compile / type-check / lint clean (see [references/definition-of-done.md](references/definition-of-done.md)):

```bash
<lint command>
<typecheck command>
<test command>
<build command>
```

Run whichever of these the project defines. If any fails, fix it in this thread — you own the code — and do not proceed to the acceptance loop until the floor is clean. A change that cannot clear the floor has not earned an acceptance review; if fixes take more than ~2 attempts, surface to the user.

**Run every floor and oracle command from its resolved cwd, through the project's own toolchain.** Use the **Command cwd** resolved in Project configuration — the worktree root by default, or the oracle/`env_contract` `cwd` (worktree-relative) in a multi-root repo. Project-local binaries resolve via that dir the way the runner already provides them — `npm`/`pnpm`/`yarn run` put that package's `node_modules/.bin` on `PATH`; `uv run` executes inside the project's `.venv`; `just` / Nx / Turbo carry their own project environment; `make -C <dir>` runs the recipe in that dir. The cwd is the primary mechanism — **do not invent a root `package.json`, a `bin/` shim, or a hand-assembled `PATH`** to make a bare command run. That is machinery the runner already supplies. When a single oracle command must span more than one toolchain root, drive each root through the runner's own root-targeting flag (`npm --prefix <dir> run <script>`, `pnpm -C <dir> <script>`, `make -C <dir> <target>`, `nx run <proj>:<target>`) rather than synthesizing a root — the full table is in [references/binder-reference.md](references/binder-reference.md), "Execution context".

**When the oracle command duplicates a floor check.** If the item's `ITEM_ORACLE.command` runs a check that the floor commands above already cover (e.g. the oracle command is `npm run lint`, which the floor's `<lint command>` already runs), it simply **runs here at the floor** — there is no second phase that re-executes it. The acceptance gate (`build:acceptance`) is read-only: it inspects and dispositions the oracle's assertions against the diff, it does not re-run the command. So a command-shaped oracle check lands at the floor; assertion-bearing, `visual`, and `contract` oracles are dispositioned at acceptance. Author note: do not duplicate a floor check as a separate acceptance step.

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
| Round 3 reached with residual issues | Stop and **surface to the user** (`AskUserQuestion` OR host user-input prompt) — the worker does not silently self-defer. If the user accepts the residual, it becomes an inline `KARTA-DEFER` marker naming it + an external follow-up (karta has no backlog), per [references/declared-debt.md](references/declared-debt.md) |

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

**Precondition — did this item change anything?** Before you run the gate, confirm the item branch actually changed something versus the integration tip it branched from:

```
git diff --quiet "$integration"...HEAD   # exit 0 = no change; exit 1 = change present
```

If there is no change, this item is **not delivered** — karta has no no-op work item. Do **not** run the gate and do **not** write any completion ref. Halt and report the cause "produced no changes." This is the build-time catch for a whiff. It leaves **no** ref at all — the branch equals its base, so there is no distinct tip to anchor a `failed`/`built`/`done` ref to, and there is nothing to merge or mark. In an orchestrated wave this is just another halted worker the orchestrator skips when it re-derives the frontier (`deliver:waveloop` Step 3); invoked directly (single-item mode) you stop here, before Phase 9 ever merges. The precondition applies **even to an opt-out item**: opt-out skips *verification*, not *delivery*. If the item genuinely needs no change, that is a planning error — surface it for a re-plan via karta-plan, never a silent pass. (The gate enforces this again on its side: an empty diff handed to the acceptance reviewer returns BLOCKED, the catch for a change that is already present on the moved tip at merge-time re-validation — see [references/verification-gate.md](references/verification-gate.md).)

**6-sme. stack-pack self-check before the gate (when `SME_PACKS` is non-empty).** Runs here, **before any gate dispatch and before the opt-out check** (an opt-out item still gets self-checked), so the markers exist when the safety-auditor judges the diff. For each loaded pack (4c-ter), run its **Review checklist** against the item's diff (`git diff "$integration"...HEAD`). For every checklist item, decide pass or miss. Two outcomes for a miss:

- **Fix it** — adjust the code so the checklist item passes. Preferred.
- **Declare the override** — when the deviation is deliberate and justified, leave the code and record a declared override at the deviation site, modelled on the `KARTA-DEFER` family (see [references/declared-debt.md](references/declared-debt.md)): an inline comment `KARTA-SME-OVERRIDE(<rule-id>): <rationale>`, optionally followed by `[ceiling: <bound>; upgrade: <trigger>]` — the ceiling names where the shortcut breaks, the upgrade what forces a revisit. `<rule-id>` is the checklist item's id from the pack (e.g. `min.1`, `ng.3`). The legacy form `KARTA-SME-OVERRIDE(<pack>: <rule paraphrase>): <rationale>` is tolerated where it already exists, but every **new** marker uses the rule id. The `ceiling`/`upgrade` trigger is **optional** — name it when the shortcut is knowingly temporary; a permanent justified exception needs only the rationale. A declared override is a justified crossing; the safety-auditor passes it.

Record the per-pack tally for the report (`build:report`), e.g. `stack-pack self-check (angular): 4/4 ok` or `3/4 — 1 declared override`. This self-check **never halts the build** — it produces the markers and the report line. The judgment of declared-vs-undeclared belongs to `karta-safety-auditor` at the gate: an **undeclared** checklist violation is a VIOLATION there (kickback), so leaving a miss neither fixed nor declared will fail the boundary scan. Only Review-checklist items have teeth; Do / Don't / Patterns are advisory.

**Opt-out items skip the loop.** When `ITEM_ORACLE.opt_out` is true, record the `reason` and skip acceptance (the floor still applies). Report the opt-out in the final summary — opt-outs are explicit and surfaced, never silent (see [references/definition-of-done.md](references/definition-of-done.md)).

**Choose the gate by oracle type:**

- **`oracle.type == visual`** → boundary gate first, then `karta-validate`. First dispatch `karta-verify` in **boundary-only mode** — it runs only the safety-auditor boundary gate on the diff (no acceptance phase) and must PASS before any dev-server work begins; a VIOLATION kicks back under the safety cap, a BLOCKED halts here. Only then does acceptance go to `karta-validate`, which compares rendered output against the design (UI annex; resolve `<dev-server-port>`, the design source, and the item's `design_reference`). The per-round capture/compare mechanism `karta-validate` uses is in **[references/design-validation-loop.md](references/design-validation-loop.md)**. Skip the `karta-validate` gate when `design_reference` is `none` — the boundary-only pass still runs. **Before invoking `karta-validate`, the app must be up** — bring it up per the dev-server lifecycle below.
- **any other type** (`unit` / `integration` / `e2e` / `smoke`) → `karta-verify`. It dispositions each of the oracle's `assertions` against the actual diff, and — when the item declares an `ITEM_CONTRACT` — checks the diff against the external contract artifact (a type-checker, schema, or contract test), not against the binder's claim.

**One command, distinct altitudes.** When the oracle `command` overlaps a floor check, the command actually **runs at the floor** (`build:floor`); this read-only gate does **not** re-execute it — it inspects and dispositions the assertions against the diff. The serial merge re-validates the oracle against the merged tip (`build:merge`, single-item mode), and CI is the final word. So a command-shaped check has one execution site (the floor) and one read-only disposition site (here); assertion-bearing, `visual`, and `contract` oracles are dispositioned here regardless of what ran at the floor.

**Dev-server lifecycle for the visual gate (conditional — `oracle.type == visual` only).** A `karta-verify` (non-visual) item skips all of this. For a visual item, `karta-validate` needs the app running before it can capture and compare, so bring it up here, before invoking the gate.

**First, honor a provided env.** The env may already be supplied by the binder's `env_contract` or by the orchestrator (a wave-bound env, started once and torn down once for the whole wave per [references/integration-branch.md](references/integration-branch.md)). When the wave env is present, use the env it exposes (`env_contract.command`, and `env_contract.isolation_params` such as `PORT` when `supports_isolation` is true) instead of starting your own — and **do not tear it down** (the orchestrator owns it). Only when the item is directly invoked with no provided env do you manage the dev server yourself, per the steps below.

Do not assume Bash, WSL, POSIX background syntax, `/tmp`, `curl`, `grep`, `lsof`, or `kill` exist on the developer machine. Use the host's native process and HTTP facilities, or a repo-owned helper script, and record the exact command/handle you used.

- **6-dev-a. Check port availability** with a host-native mechanism (a Python socket probe, a PowerShell TCP lookup, the project's dev-server status command, or the platform's equivalent). Check both `<dev-server-port>` and `<backend-port>` when the dev target starts a backend. **If something is already on either port, bail and ask the user to stop it first — never stop another process's dev server.** (This guards against *other* processes. When you must **restart** your own dev server — e.g. after a token rebuild or a degraded server mid-loop — first stop the recorded handle to free the port, then repeat these steps; otherwise this check sees your own still-running server and bails.)
- **6-dev-b. Start the dev server as a managed background process/session.** Record its process id or host process handle (call it `DEV_SERVER_PID` or the host equivalent), plus its log location. Do not use POSIX `&` unless the host shell is known to support it. If the dev target transitively starts a backend/API service (resolved in Project configuration), that service comes up on `<backend-port>` too — both are needed when the view depends on the backend for data. Note that port for the teardown (`build:teardown`).
- **6-dev-c. Health-poll the actual `design_reference` route** (not just `/`) with a host-native HTTP client until it returns an expected status such as `200`, `307`, or `308` — many dev servers compile/warm pages on demand, so `/` warming proves nothing about the target view. Use an explicit retry limit around **60 seconds** and capture failure output. If the route is not responding after ~60s, stop the recorded handle and bail with the error (common causes: port conflict, a build error the floor didn't catch, missing env vars). **A bare 2xx/3xx is not proof the view rendered when it's behind auth** — an unauthenticated request to a protected route can return `200` on a login page or `3xx` to `/login`, passing this poll while the target view is still unreachable. If the route requires authentication, detect the auth-redirect / login-page response here and treat establishing a logged-in session (and ensuring any backend service the view needs is up) as a `karta-validate` prerequisite, not something this poll satisfies — see [references/design-validation-loop.md](references/design-validation-loop.md) (`dvl:invoke:auth`).
- **6-dev-d. Store the recorded handle** (`DEV_SERVER_PID` and `<backend-port>`, if any) for the teardown (`build:teardown`).

**Kickback and caps.** On any finding, the gate kicks the work back to this skill for **bounded self-correction**, then re-runs on the corrected diff. Per [references/verification-gate.md](references/verification-gate.md) the caps differ by gate:

- **Safety / boundary scan** (the seven smart-surfaced-review signals re-run on the real diff; see [references/smart-surfaced-review.md](references/smart-surfaced-review.md)) — **max 3 attempts, then escalate to the human.** A boundary the item never justified is a safety question.
- **Acceptance / contract gate** — **max 2 attempts, then halt with a call to action.** On the second failed attempt the gate halts and the item takes the halt path — in a wave you commit the item branch and write a `failed` ref at that tip, no `built`/`done`, not done. You do **not** clear a capped acceptance failure by declaring debt — that would let the implementer grade its own escape. The ways forward are fix-and-rerun; **re-plan the unmet assertion as an explicit oracle `opt_out` (with a reason) via karta-plan** and re-run (the binder is read-only to build, so accepting it is a plan-time decision; karta has no backlog); or — in a wave only — a **human accept-waiver** the orchestrator obtains at its Phase-4 halt and records in git. You never obtain or write that waiver yourself. In single-item mode there is no accept (see `build:merge`, 9c-single). See [references/declared-debt.md](references/declared-debt.md).

Only on cap exhaustion does the gate halt or escalate — otherwise it self-corrects within the caps and moves on.

### Phase 7 — Dev-server teardown (cleanup for the visual gate)  `build:teardown`

**Conditional — visual items that started a dev server.** A non-visual item is a no-op here. **Always runs when this run started a server**, regardless of outcome — whether the skill succeeded, failed at a gate, or errored after bring-up, the ports it opened must be freed. Structure the teardown to run on every exit path after the acceptance loop's bring-up (`build:acceptance`).

Stop **only the process or process tree this run started**, using the host's native process handle (the `DEV_SERVER_PID` recorded in 6-dev-b/d). If the port is still held afterward, clean up an orphan only when you can prove it was spawned by this run's recorded dev command — **never stop an unrelated process** that happens to bind the same port (the mirror of 6-dev-a's "do not stop another process's dev server"). Apply the same guard to `<backend-port>` when the dev target started a backend/API service: stop only what this run started, then free the backend port too.

**Do not tear down a provided env.** When the acceptance loop (`build:acceptance`) used a wave-bound env from the binder's `env_contract` / the orchestrator instead of starting its own server, leave it running — the orchestrator owns its lifecycle and tears it down once for the whole wave (see [references/integration-branch.md](references/integration-branch.md)). This phase only stops servers *this run* started. If the skill exited before the acceptance loop (`build:acceptance`) brought up a server (e.g. at the input gate `build:gate`), this phase is a no-op. (Port-conflict and process-handling details also live in [references/design-validation-loop.md](references/design-validation-loop.md).)

### Phase 8 — (reserved)

### Phase 9 — Commit, secret-scan, and finish the item — NO PR  `build:merge`

Run from inside the worktree. There is **no PR**. The terminal state depends on `RUN_MODE` (Phase 1): a single-item hatch ends at a tagged item *merged* into the integration branch; an orchestrated wave ends at a *committed, secret-scanned item branch* carrying a durable `built` marker, which the orchestrator merges. Either way the user ultimately reviews and merges the integration branch.

The integration tip has exactly one writer per [references/integration-branch.md](references/integration-branch.md). Steps 9a (secret scan) and 9b (commit) run in **both** modes; step 9c branches on `RUN_MODE`.

**9-recheck. Post-gate mutation re-check (hard rule).** The stack-pack self-check runs pre-gate (6-sme); it is not repeated here. But a gate verdict binds only the diff it judged. If **any** step after the acceptance gate mutated the diff — a kickback fix, a late correction, any edit in a later phase — re-run the definition-of-done floor (`build:floor` commands) and the boundary scan on the mutated diff before committing. This is mandatory, not advice: no commit lands on a post-gate mutation without the re-run.

**9a. Secret scan before every commit.** Before each commit, run the bundled scanner [scripts/scan_secrets.py](scripts/scan_secrets.py) — `uv run skills/karta-build/scripts/scan_secrets.py` — against the **staged diff** only. One scanner for every build keeps the gate reproducible. The pattern set, the allow-list format, and the on-hit behavior are defined in [references/secret-scan.md](references/secret-scan.md). On a hit, **block the commit and surface the finding** (file, line, matched pattern); mark the item failed with the scan output, preserve the worktree, and halt. Resolution requires removing or rotating the secret (or an in-repo allow-list entry, reviewed alongside the code) before retry.

**9b. Commit** with the item marker in the subject line. The canonical commit **subject** marker is:

```
[karta:item-<item-id>] <summary>
```

The `[karta:item-<item-id>]` marker is mandatory and appears verbatim — resume and integration parse it to trace the commit. `<summary>` is a short imperative description of the change.

**Coexisting with Conventional Commits.** When the project's convention puts a single type prefix on the subject (`feat:`/`fix:`/`chore:`), do **not** stack the karta marker into that prefix and do **not** add a second type — a Conventional-Commits subject carries exactly one type. Keep the single CC prefix on the subject and carry the marker as a git trailer instead, one blank line after the body:

```
feat(profile): <summary>

<optional body>

Karta-Item: item-<item-id>
```

Either form satisfies the requirement: the bracket marker in the subject (canonical default), or the `Karta-Item: item-<item-id>` trailer when a CC prefix owns the subject. Use the trailer only when the project's convention requires a single typed subject; otherwise prefer the subject marker. Apply one form consistently across the item's commits so resume can recover the id.

**9c. Finish — who merges depends on `RUN_MODE`.** The integration tip has exactly one writer; pick the branch that matches how this run was invoked, per the two modes in [references/integration-branch.md](references/integration-branch.md).

**9c-single — single-item hatch (the worker owns the merge).** When `RUN_MODE` is single-item (invoked directly on one item, no wave), the worker is the only party in play, so it completes the merge itself:

1. Rebase/merge the item branch onto the **current** integration tip (which may have advanced if you are resuming a partial binder).
2. **Re-validate the oracle against the merged result** — the tip can differ from the pre-merge branch, so the acceptance check must pass on what actually lands. On a merge conflict or a re-validation failure, do a **bounded rebuild** against the new tip, or **halt** if the cap is exhausted.
3. **Then check shared terms** — the single-item hatch **owns** this step because there is no orchestrator to run the whole-binder check. On the re-validated merged result, before the merge completes, run the binder's declared-term check: `uv run --script skills/karta-plan/scripts/check_shared_terms.py --binder <binder path>`. A non-zero exit (`[FAIL]` — a declared `shared_terms` string drifted between items that both landed) halts on the **same footing as a failed post-wave build** in `karta-deliver`: take the halt path (write `refs/karta/<slug>/item-<item-id>/failed` → the item-branch tip and stop) or the human fixes the wording and you re-run. `[PENDING]` entries — items not yet delivered — are skipped by the script itself and are **not** a failure; an absent or empty `shared_terms` is a clean no-op pass. This is the single-item hatch's equivalent of `karta-deliver`'s post-wave shared-terms check (`deliver:waveloop` step 4).
4. Merge (ff or no-ff).
5. Write `refs/karta/<slug>/item-<item-id>/done` → the merge commit. On a halt, write `refs/karta/<slug>/item-<item-id>/failed` → the halted item-branch tip instead, and stop. **Accept is not available in single-item mode** — you are the sole party, with no orchestrator to hold the human boundary, so a cap-out (or SPEC-SUSPECT) writes only `failed` and halts. Accepting an unmet assertion here requires escalating to an orchestrated context or a karta-plan opt_out; you never write `accepted`.
6. **Doc-gardner (opt-in).** If `.karta/doc-gardner.json` exists with `enabled: true`, invoke the `karta-doc-gardner` skill in `delivery` mode over this item's diff range (its merge versus the pre-item integration tip), passing the file's `focus`. It corrects any drifted docs to match the merged code and commits a `docs: gardner <slug>` commit on the integration branch — fully automatic, no human, no halt. Absent or disabled, skip it. This is the single-item hatch's equivalent of `karta-deliver`'s `deliver:docgardner`. (A **wave** worker never runs doc-gardner; the orchestrator runs it once for the whole delivery.)

7. **Kaizen (opt-in).** If `.karta/kaizen.json` exists with `enabled: true`, invoke the `karta-kaizen` skill in `delivery` mode over this item's diff range (its merge versus the pre-item integration tip), passing the file's `focus`. On the first enabled run the skill seeds every used pack into `.karta/sme/`; any pack edits land as labeled `kaizen:` commits on the integration branch — fully automatic, no human, no halt. Absent or disabled, skip it. This is the single-item hatch's equivalent of `karta-deliver`'s `deliver:kaizen`. (A **wave** worker never runs kaizen; the orchestrator runs it once for the whole delivery.)

8. **Binder end-of-life.** The one item is the whole binder, so a clean merge completes the run: archive the binder — `mkdir -p .karta/binders/archive && git mv .karta/binders/<slug>.json .karta/binders/archive/<slug>.json`, committed on the integration branch as `chore(karta): archive binder <slug> — delivered`. Skip on a halt (the `failed` path — the binder stays live) and skip when the binder already sits at the archive path on this branch (a resumed run — the step is idempotent). This is the single-item hatch's equivalent of `karta-deliver`'s `deliver:archive`; the archival travels with the user's merge of the integration branch. (A **wave** worker never archives; end-of-life for a multi-item delivery is the orchestrator's, once, after the last wave.)

9. **Surface what's next.** After the single-item merge completes, print the condensed next-step footer so the run ends pointing forward:

   `uv run --script skills/karta-status/scripts/karta_next.py --footer --binder <slug>`

   This is read-only — it derives the next action from git, never writes. It is the same engine the `karta-status` skill uses, so the footer and the command never disagree. A **wave worker does not print a footer** — the orchestrator owns the run's end.

**9c-wave — orchestrated wave (the worker commits and stops; the orchestrator merges).** When `RUN_MODE` is orchestrated, **stop at the committed item branch** — do **not** touch `karta/<slug>/integration` and do **not** write the `done` ref. The pass signal is durable git state, not an ephemeral report:

1. Leave the item branch `karta/<slug>/item-<item-id>` committed (9b) and secret-scanned (9a) at its tip — this is your terminal artifact.
2. On a clean floor + acceptance + secret scan, write the durable marker ref `refs/karta/<slug>/item-<item-id>/built` → the item-branch tip. This is the worker's pass signal; the orchestrator merges only items carrying a `built` marker (or an item the human accept-waives — never your call). Do **not** write `done`, and **never** write the `accepted` namespace under any mode — that ref is the orchestrator's, recorded from a live human decision (see [references/integration-branch.md](references/integration-branch.md)).
3. On a halt at the acceptance gate (a capped DEVIATION **or** a SPEC-SUSPECT), write **no** `built` marker — commit the item branch and write `refs/karta/<slug>/item-<item-id>/failed` → that tip instead, then surface the cause. The `failed` ref means "halted at the gate, not cleanly done" (for a SPEC-SUSPECT its note carries the spec-suspect reason; it does not claim the code is bad). A halted item produces no pass signal; its committed branch + `failed` ref is the uniform anchor the orchestrator's human accept-waiver merges from if the human chooses to accept it.
4. **Stop here.** Report the committed item branch and its tip (Phase 10). The orchestrator (`karta-deliver`, `deliver:waveloop` Step 3) is the single writer of the integration tip: it runs the serial FIFO merge queue, re-validates the oracle against the moving tip before each merge (so it re-checks rather than trusting the `built` marker's word), merges, tags the wave, and writes `done`. Because the queue is serial there is no concurrency at the tip; the orchestrator's done-ref guard is resume-idempotency, not a race fix.

**Do not open a PR** in either mode. No `gh`/`glab`/`tea`, no push-to-review, no review-status transition.

### Phase 10 — Report back  `build:report`

Write everything you show a person in plain language — see [references/user-facing-prose.md](references/user-facing-prose.md). Give the user a short summary (~8 lines):

- **Item id** and the binder slug
- **Worktree path** — so the user knows where the checkout lives
- **Terminal artifact** — single-item hatch: the integration tip the item merged to (merge commit / `done` ref); orchestrated wave: the committed item branch and its tip (`built` marker ref) that the orchestrator will merge; on a halt, the `failed` ref
- **Binder end-of-life** — single-item hatch only: archived to `.karta/binders/archive/<slug>.json` on a clean merge, or left live with the reason (a halt). A wave worker omits this line — end-of-life is the orchestrator's
- **Runtime** — the active runtime version(s) the floor ran under, against the `runtime_contract` (or detected pin files); note a clean match or the mismatch that halted
- **Generated-but-unused files** (greenfield/scaffold items only) — anything the framework generator emitted that fell outside the item's `scope`/`contract`, noted here rather than written to the read-only binder
- **Acceptance result** — which gate ran (`karta-verify` / `karta-validate` / opted out), final disposition, rounds used, any residual finding
- **stack-pack self-check** — per applied pack, the Review-checklist tally and any `KARTA-SME-OVERRIDE` declared (what, which rule id, why); on a 4c-ter resolution halt, name each pinned `sme` id that resolved to no pack file and the paths tried. Omit the whole line when `SME_PACKS` is empty
- **Declared-debt summary** — every `KARTA-DEFER` marker you placed (what, why, external follow-up), per [references/declared-debt.md](references/declared-debt.md). **Flag any `KARTA-DEFER` marker whose `follow-up:` trigger is missing or empty as `no-trigger`** — the deferral that silently rots — and end the register with `<N> markers, <M> no-trigger`. This reads what you already scanned; it adds no state. Markers are inline deferrals only. An item carrying inline debt is never reported as done without its deferral list shown. A capped acceptance failure is never turned into a marker: it halts to a `failed` ref. Accepting the unmet assertion is a re-plan opt-out via karta-plan or a human accept-waiver the orchestrator records, never a worker-placed marker. karta surfaces the debt register once and never tracks it (no backlog)
- **Secret-scan status** — clean, or blocked-with-finding
- A self-assessment from the automated gates. Flag anything no gate checked (e.g. accessibility) as needing manual review — do not imply it passed

**On a halt, preserve the failing item's worktree and print its path.** Leave the worktree in place on success too — re-runs and review iterations frequently need it back.

---

## Gotchas

- **No PR — ever.** The user reviews and merges the integration branch. No `gh`/`glab`/`tea`, no review transition.
- **One writer to the integration tip — the explicit mode decides who.** Single-item hatch: the worker merges its item into `karta/<slug>/integration` and writes the `done` ref. Orchestrated wave: the worker **stops at the committed item branch** and writes the durable `built` marker (not `done`); the orchestrator/`karta-deliver` runs the serial merge queue and writes `done`. The mode is told to the worker explicitly — never inferred from repo state. See [references/integration-branch.md](references/integration-branch.md).
- **The worker never writes `accepted`, in any mode.** The `accepted` ref records a human accept-waiver and is the orchestrator's alone, written from a live human decision at its Phase-4 halt. A wave acceptance halt (capped DEVIATION or SPEC-SUSPECT) commits the item branch and writes `failed` at that tip — the anchor an accept merges from. Accept is unavailable in single-item mode: a directly-invoked worker that caps out writes only `failed` and halts.
- **Branch off the integration tip, not the default branch.** That tip already contains every merged dependency; building off the default branch would lose them.
- **Dependencies must be merged before pickup.** Gate 3 checks `refs/karta/<slug>/item-<dep>/done` for every `depends_on`; an unmet dependency halts.
- **The binder is read-only to build.** A build step never edits the plan that governs it — that would corrupt its own governance.
- **Never weaken the oracle.** Don't edit or soften the acceptance assertions or contract to make a check pass. On a genuine conflict, halt — code/specs/tests win.
- **Commit marker is mandatory.** Every commit carries `[karta:item-<id>]` so resume and integration can trace it — in the subject by default, or as a `Karta-Item: item-<id>` git trailer when a Conventional-Commits type prefix owns the subject (never stack the marker into the CC prefix or add a second type).
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
- **Declare deferrals inline — never to clear a gate.** A skipped test or stubbed dependency gets a `KARTA-DEFER` marker at the site (with an external follow-up; karta has no backlog); the report surfaces every one, and an item carrying inline debt is never reported as done without its list. A marker never clears a capped acceptance failure — that halts to a `failed` ref. The marker is the worker's inline note; it is a different thing from the **human accept-waiver** and the **human defer choice**, which are the orchestrator's decisions at the Phase-4 halt (a worker can never read its own marker as a self-accept path). Accepting the unmet assertion is a re-plan opt-out via karta-plan or a human accept-waiver the orchestrator records.
- **Opt-outs are explicit and surfaced.** When `oracle.opt_out` is set, skip acceptance (not the floor), record the reason, and report it. There is no silent opt-out.
- **The floor is non-negotiable.** A change that won't compile / type-check / lint does not earn an acceptance review — it earns a surfacing.
- **Check the runtime before the floor — never auto-provision.** A floor command can hard-refuse on a runtime mismatch. The preflight compares the active runtime against the binder's `runtime_contract` (or detected pin files) and **halts with a CTA** on a mismatch; `on_unavailable` carries the single value `halt`. karta does not install or select a runtime — provisioning is the operator's, a hermeticity/supply-chain concern. The floor/oracle commands may route through the repo's own declared version manager (`mise exec -- …`).
- **Tool-imposed runtime floors are mode-gated.** A floor no pin file records — a CLI that hard-refuses the host's Node/Python — is decided by mode: greenfield may pin a *tool* version compatible with the host's active, unchanged runtime (e.g. `@angular/cli@21` on Node 24.14) and note it in the report; edit mode halts with a CTA, and the floor should then be recorded in `runtime_contract` so the declared preflight catches it next run. Pinning a tool ≠ selecting a runtime — karta never does the latter.
- **Floor RUNS, acceptance INSPECTS — one check, two altitudes.** Floor commands execute in-worktree; the acceptance gate is read-only and dispositions assertions against the diff (it does not re-run the command). When the oracle `command` overlaps a floor check it simply runs at the floor — not a second phase. The merge re-validates on the merged tip (single-item mode); CI is final.
- **Multi-root oracles use the runner's own root-targeting, never a shim.** A polyglot/multi-root repo drives each toolchain from its own root via `npm --prefix <dir>` / `pnpm -C <dir>` / `make -C <dir>` / `nx run <proj>:<target>` (full table in [references/binder-reference.md](references/binder-reference.md), "Execution context"), or sets the oracle `cwd` per segment. Inventing a root `package.json` or a `bin/` shim to make a bare command resolve is the anti-pattern the cwd + runner-targeting design exists to prevent.
- **Greenfield items scaffold, then provision the named check.** Foundation/first item only (no integration branch yet, contract is to stand up the project): the framework's own official generator (`ng new`, `create-next-app`, …) is allowed; bound the result to the item's `scope.included`/`contract`, clean the framework's placeholder branding, re-resolve the toolchain/oracle commands after the generator runs, and note generated-but-unused files in the report. When the bare scaffold lacks a check the oracle names, add it through the framework's own add/plugin command (e.g. `ng add @angular-eslint/schematics`) — and if no official mechanism exists, halt. A check that exists but fails is a real failure, never the absent-check carve-out; never weaken or skip the oracle.
- **Co-owned files are additive only.** An item may extend a file an earlier depended-on item created (registering a route, mounting a component) when it is inside the binder's `scope.included` — smallest wiring change, no broader refactor. A rewrite of shared structure is unauthorized blast-radius: flag and ask. Two wave-mates on one file is the orchestrator's serialization concern (`serialize` / `shared_resources`).
- **Preserve the failing worktree on halt and print its path.** Don't tear it down — the user needs it to resume.
- **Don't re-plan.** The plan lives in the binder. Your job is execution of one item, not re-planning. The planning counterpart is `karta-plan`.
