---
name: karta-plan
description: >-
  Analyze a problem/feature description and/or a design mock or non-functional prototype and synthesize a validated binder of work items for ad-hoc orchestration; stack-agnostic (frontend, backend, CLI, data, library/SDK, IaC, mobile, ML, docs — UI is one stack among many); emits .karta/binders/<slug>.json. Trigger phrases: "plan this with karta", "synthesize a binder", "break this work into a binder", "karta-plan this feature".
---

karta-plan ingests intent and — without fail — synthesizes a binder. Give it a problem or feature description; optionally attach a design mock or non-functional prototype. It asks a minimal set of questions, runs a synthesis subagent to draft the binder, and commits on an explicit "commit" verb. The output lands at `.karta/binders/<slug>.json` and is validated by `validate_binder.py` before any build run.

## How this skill adapts to your project

karta-plan is **stack-agnostic**. It plans frontend, backend, CLI, data pipelines, libraries/SDKs, IaC, mobile, ML, and docs work in the same way — UI is one stack among many, not the default.

**How it works:**

- Intent ingestion handles design exports too: when the input is a Claude Design or runtime-JSX export, the UI-analysis path applies (component inventory, token map, icon map). This is a **stack-specific aside**, not a precondition for the whole skill.
- A synthesis subagent drafts the binder from ingested intent.
- A minimal interview loop asks only what it cannot detect.
- Commit-on-verb: the binder is presented as an editable card and committed when you say "commit."
- Review is smart-surfaced: the synthesis subagent flags items that warrant human attention (`surface.flagged` + `surface.signals`); unflagged items don't require a per-card walk.

**Scope limits (V1):**

- Repo detection is light and never blocks (see Project configuration below).
- When the scope spans multiple natural binders, it suggests a breakdown and plans one binder per run.
- Synthesis runs as a single subagent, not a panel.

## Project configuration (resolve once, never blocking)

Resolve in order: **explicit user input → detect from the repo → ask the user** (batch all unknowns into one question). Skip what you can detect. When detection conflicts with the project's stated stack, the stated stack wins — a repo can be mid-migration.

| Setting | What it is | How to resolve |
|-|-|-|
| **Stack** | The primary tech domain (frontend, backend, CLI, data, IaC, mobile, ML, docs, mixed) | Detect from repo layout, package manifests, and language files; ask when ambiguous |
| **Toolchain commands** | lint / test / build / typecheck invocations | Detect from package scripts, task runners (npm/pnpm/yarn, Makefile, Nx, Turborepo, Cargo, Poetry, etc.) |
| **CI-facing checks** | The subset of commands that gate CI (used as oracle `command` values) | Detect from CI workflow files; if absent, use the toolchain commands |
| **Env command** | The command that starts the dev/test environment (feeds `env_contract.command`) | Detect from package scripts or a `docker-compose.yml`; ask if not found |
| **Required runtime** | The language/toolchain runtime versions the commands need (feeds `runtime_contract`) | Detect from version-manager pin files (`.nvmrc`, `.tool-versions`, `.python-version`, `mise`/`asdf` config) and manifest fields (`package.json` `engines`/Volta, `pyproject.toml` `requires-python`); ask for the floor only when nothing pins it but a tool is known to gate on a minimum |
| **Repo direction docs** | Architecture docs, ADRs, or decision records | Detect: `ARCHITECTURE.md`, `docs/architecture/`, `docs/decisions/`, `adr/`; cite only filled docs (skip placeholder templates); ask when context is thin |
| **Project rules** | Conventions the repo documents (lint configs, contributor guides, rules files) | Detect; verify the doc has real content before citing |
| **Repo policy** | CI/branch/deployment policy (only when in planning scope) | Read root/area `AGENTS.md`, workflows, and CI docs; load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
| **stack packs** | Advisory domain-expert do's/don'ts to apply (each with an enforceable Review checklist) | Match the built-in [references/sme/](references/sme/) plus the project overlay `.karta/sme/*.md` against the detected deps/stack; record matched ids in the binder's `sme` |

Resolved values feed the binder's `design_facts.stack`, `env_contract`, `runtime_contract`, and each oracle's `command`. Record them — every later phase references them.

### UI / design-token annex (conditional)

When the resolved stack has a design or token surface, also resolve:

- **Component library:** detect from package deps and import statements; may be none.
- **Icon libraries:** detect from deps/imports; may be none.
- **Token/theme system:** detect a theme object, CSS custom properties, a design-tokens file (incl. W3C DTCG JSON — see [references/dtcg-tokens.md](references/dtcg-tokens.md)), a utility-class config, or plain CSS.

When the **token system** is a W3C DTCG file (JSON leaves carrying `$value`/`$type`), resolve the additional DTCG-only settings in [references/dtcg-tokens.md](references/dtcg-tokens.md) so the binder's `token_manifest` and each work item's `token_changes` can carry tier-correct, build-actionable guidance.

Resolved UI values feed the binder's `design_facts` and each work item's `component_map`, `icon_map`, and `token_changes` (see [references/binder-reference.md](references/binder-reference.md) — these fields are UI-optional and omitted when the stack has no design surface).

## Workflow

### Phase 0 — Ingest intent (light gate)  `plan:ingest`

Accept a problem or feature description as the only hard requirement. Optionally accept a path to a design mock or non-functional prototype. Some statement of intent is required — if the user provides nothing, ask once.

**Repo detect (light, never blocking).** Check for a recognizable repo layout to resolve the binder's landing path and later feed the repo survey (`plan:survey`). Never fail on an unfamiliar structure.

**UI-format gate (conditional).** When the input includes a design file path, check whether it is a Claude Design or runtime-JSX export: verify that `.jsx` siblings exist or the HTML contains a `<script type="text/babel">` block. If the check fails, tell the user what was found and ask them to re-export or point at the `.jsx` sources. Only run this check when a design file is provided; a plain text description has no format to gate.

**Resolve the binder location.** In order: explicit user input → detect an existing `.karta/binders/` directory in the repo → default to `.karta/binders/<slug>.json`. Ask the user only when you cannot determine `<slug>` from the intent.

---

### Phase 1 — Best-effort repo + stack understanding (Explore subagent)  `plan:survey`

Use an **Explore subagent** OR an inline read-only pass to survey the repo.

**Subagent brief:**

Survey the repo at `<root>`. Identify the primary tech domain (frontend, backend, CLI, data pipeline, library/SDK, IaC, mobile, ML, docs, or mixed). Identify the toolchain: how the project lints, tests, builds, and type-checks (look at package scripts, task runners — npm/pnpm/yarn, Makefile, Nx, Turborepo, Cargo, Poetry, Gradle, etc. — and any CI workflow files). Determine the env command: what starts the dev or test environment. Determine the required runtime: read version-manager pin files (`.nvmrc`, `.tool-versions`, `.python-version`, `mise`/`asdf` config) and manifest fields (`package.json` `engines`/Volta, `pyproject.toml` `requires-python`, a `go` directive in `go.mod`, `rust-version` in `Cargo.toml`) for the language/toolchain versions the commands need. Read any architecture, ADR, or decision docs you find (`ARCHITECTURE.md`, `docs/architecture/`, `docs/decisions/`, `adr/`, `AGENTS.md`) — cite only docs with real content; skip placeholders. Read project rule and contributor convention files when present.

Report:
1. Resolved stack (one phrase, e.g. "Python/FastAPI backend + Postgres").
2. Toolchain commands for lint, test, build, and typecheck — with the exact invocations.
3. CI-facing checks: the subset of toolchain commands that gate CI (from workflow files, or fall back to the toolchain commands).
4. Env command.
5. Conventions and rules the repo documents.
6. Architecture or decision-record notes relevant to the stated intent (if any docs exist).
7. Required runtime: per runtime, the version or range and the pin file or manifest field it came from (`.nvmrc`, `.tool-versions`, `.python-version`, `engines`, `requires-python`, …). This feeds the binder's `runtime_contract`. Say so plainly when nothing pins a runtime — an absent floor is a clean result, not a gap to guess at.

When context is thin on a point, say so — the main thread will interview the user for the missing piece.

Report with `file:line` citations.

**UI annex — full design analysis (conditional).** When the resolved stack has a UI surface — a design mock or non-functional prototype as input, **or** a component-library / token system in the repo — run karta's full frontend analysis per **[references/ui-analysis.md](references/ui-analysis.md)** instead of the one-line inventory. Keep the Explore-subagent pattern: that reference drives three read-only passes (run them in parallel where the host allows), each citing `file:line`:

- **Design analysis** — component inventory (with props, complexity, state), the component-hierarchy tree, the page/view inventory, the design-token inventory *with values* (per theme context), mock-data shapes, and navigation structure (ui-analysis `ui:design`).
- **Codebase + component-library inventory** — existing components/routes/styles/data-layer/client-state/tests, the component-library catalog, icon libraries, and the theme/token inventory (incl. DTCG tier/path/name from the JSON source, resolved values from the generated output) (ui-analysis `ui:inventory`).
- **Data-layer mapping** — the type-coverage buckets (matched / gap / unused / exists-but-differs / field-level gap), operation mapping, schema gaps, and fetch-boundary planning (ui-analysis `ui:datalayer`).

When the token system is W3C DTCG (JSON leaves carrying `$value`/`$type`), also resolve the DTCG-only settings in [references/dtcg-tokens.md](references/dtcg-tokens.md) so the binder's `token_manifest` and each work item's `token_changes` carry tier-correct, build-actionable guidance.

Non-UI stacks skip this annex entirely — the base survey above is all they need.

**stack pack matching (after the survey)  `plan:sme`.** karta ships curated stack packs — advisory do's/don'ts per stack, each with an enforceable Review checklist — and a project may add its own. Once the survey resolves the stack and dependencies, select the packs that apply:

1. Enumerate available packs by `name`: the project overlay `.karta/sme/*.md` in the repo, laid over the built-ins in [references/sme/](references/sme/). On a name clash the project-local file wins. (`platform-native.md` is shared reference data, not a pack — skip it; the packs link to it via `see_also`.)
2. Select the packs that apply, by frontmatter kind:
   - An **always-on stack pack** (`always: true`, e.g. `minimalism`) applies to **every** binder, unconditionally.
   - A **matched stack pack** (`match: [tokens]`) applies when a token equals a detected dependency name or is a case-insensitive substring of the resolved stack phrase.
3. Collect the applied pack `name`s — always-on and matched together — into the binder's `sme`. Every binder gets at least the always-on stack packs (e.g. `["minimalism"]`); a polyglot repo adds several stack packs (e.g. `["minimalism", "python-fastapi", "angular"]`). A project that wants no always-on stack pack overrides it with a no-op `.karta/sme/<name>.md`.

Load the applied packs now — their guidance feeds synthesis (Phase 2) and their ids are pinned into the binder (Phase 5). These packs are **advisory for decomposition**: they shape how items are split, what each `contract` says, and which `oracle` assertions you choose; they never add a gate at plan time.

---

### Phase 2 — Synthesize the binder (synthesis subagent; main thread owns judgment)  `plan:synthesize`

Decompose the stated intent into work items. Do not delegate this judgment — the synthesis subagent drafts; you review and own the output.

**Subagent brief:**

Given the intent `<intent>` and the repo survey (`plan:survey`), draft a binder JSON that conforms to [references/binder-reference.md](references/binder-reference.md).

**Domain guidance (when stack packs matched in `plan:sme`).** When one or more stack packs matched, their do's/don'ts are domain guidance for this synthesis. Decompose items, write `contract`s, and choose `oracle` assertions so they respect each matched stack's patterns and avoid its anti-patterns (e.g. an Angular slice's `contract` speaks in standalone-component / signals terms; a FastAPI item's `oracle` expects Pydantic-validated request/response shapes). This guidance shapes the plan; it never adds a plan-time gate.

For the binder level, populate:
- `slug` (kebab-case, derived from the feature name)
- `motivation` (one sentence)
- `scope.included` and `scope.excluded`
- `design_facts.source` (path to the design, or null) and `design_facts.stack` (from `plan:survey`)
- `env_contract.command`, `env_contract.supports_isolation`, and `env_contract.isolation_params` (from `plan:survey`)
- `runtime_contract` when the project pins a runtime floor: one `runtimes` entry per runtime (`name`, the required `version`/range, and an optional `manager` — the version manager that pins it, e.g. `nvm`/`mise`), plus `on_unavailable` (always `halt` — karta never auto-provisions or selects a runtime). Detect the floor from version-manager pin files and manifest fields (`engines`, `requires-python`), then record the resolved `version`; omit the whole object when no runtime floor exists
- `token_manifest` only when the stack has a token system
- `sme` — the stack pack ids matched in `plan:sme`; omit or use `[]` when none matched

For each work item, set:
- `id` (kebab-case, unique)
- `title` (short human label)
- `estimate` (`S`, `M`, or `L`)
- `depends_on` (IDs of items that must land first)
- `contract` (the open-shape interface this item exposes or consumes — be specific; vague contracts produce unverifiable oracles)
- `oracle` per [references/definition-of-done.md](references/definition-of-done.md): a real CI-facing check oracle (`type`, `command`, `assertions`) OR an explicit opt-out (`opt_out: true`, `reason: "…"`). The floor is compile + type-check + lint. Use opt-out only when a genuinely better check already exists and recording it here would be redundant — always provide a reason.
- `serialize: true` and `shared_resources` for items that must not run in parallel (e.g. DB migrations, lock-file changes).
- `touches`: the concrete files/paths each item creates or modifies. This gives the parallelism file-collision and shared-resource gates structured data instead of prose to infer from, and `validate_binder.py` uses it to flag two same-wave items (no dependency edge between them) whose `touches` overlap unless one declares `serialize` or they share a `shared_resources` entry. Populate it for every item; without it the collision gate falls back to unenforced inference.
- UI-only fields (`design_reference`, `component_map`, `icon_map`, `token_changes`) only when the stack has a design surface. Omit them entirely for backend, CLI, data, and other non-UI stacks.

**Multi-root / polyglot oracle commands.** When the repo has more than one toolchain root (e.g. a Python service at the root plus an Angular app under `frontend/`), do not emit a bare oracle `command` that assumes a single root — `ruff && pytest && npm run lint && ng build` resolves `ng` from nowhere at the root and forces build to improvise a shim. Express each segment through the runner's own root-targeting flag so every tool resolves from its own root with no synthetic structure: `uv run pytest` at the root, `npm --prefix frontend run lint`, `pnpm -C frontend build`, `make -C api test` (see [references/binder-reference.md](references/binder-reference.md), "Execution context"). Set the oracle's `cwd` to the segment's root when the whole command lives in one sub-tree; use the runner's root-targeting when one command must drive several roots. This tool-resolution rule holds for **every** oracle command, single-root included: author a Python tool through the project's venv entrypoint (`uv run ruff`, `uv run pytest`, `uv run mypy`) — never a bare `ruff`/`pytest` that assumes a global install — and a Node tool through its package script/runner (`npm … run`, `npx`). Write the command the way it actually resolves in the project so the build worker never has to rewrite it.

**For UI items — populate the UI fields from the analysis (conditional).** When the stack has a UI surface, decompose the UI work and fill its fields using **[references/ui-analysis.md](references/ui-analysis.md)** and the survey (`plan:survey`) analysis it produced:

- Break UI work into items with the **vertical-slice heuristics** (foundation slice if the library/theme isn't integrated; one slice per page/view; split list + detail; complex reusable components; cross-cutting features; modals bundled with their view) — ui-analysis `ui:slice`.
- Set `estimate` to the **S/M/L** size from the slice's component count and per-component complexity — ui-analysis `ui:slice`.
- Set `design_reference` to the view/route the slice renders (or `none` for a pure setup/foundation item).
- Populate `component_map` from the **component-to-library mapping** (Library match / Library + wrapper / Custom / Composite), `icon_map` from the **icon mapping** (primary → fallback → custom SVG, with the Source column and any missing-icon flags), and `token_changes` from the **design-token mapping** (no-consumable-tier-match tokens recorded as semantic-additive entries with per-context values and Auth) — ui-analysis `ui:components`, `ui:icons`, `ui:tokens`. Put the shared design→project token map in the binder-level `token_manifest`.

Non-UI items keep the stack-agnostic synthesis above and carry none of these fields.

**Foundation / greenfield items (any stack).** A foundation item creates a project from nothing — the first item in a binder that has no integration branch yet and whose contract is to stand up the project, not edit against existing conventions. Trigger greenfield mode off that existing first-item/foundation signal, not a separate judgment call: the project does not exist yet, so there is nothing to detect and no convention to match. When you plan such an item:

- State in the item's `scope.included` and `contract` that it runs the framework's own official generator (e.g. `ng new`, `create-next-app`, `cargo new`, `npm create vite@latest`, `django-admin startproject`). These generators are deterministic and blessed — the framework's own way to lay down a working baseline. Bound the scaffold to the item's contract: set `scope.excluded` to keep generated sample features, demo pages, and unrequested add-ons out, since the generator's footprint is wider than the item. Build trims it and notes generated-but-unused files in its report (it does not edit the read-only binder).
- Keep the oracle as the real CI-facing check. When the oracle names a check a bare scaffold won't ship (a fresh `ng new` has no `ng lint` / `npm run lint` target), note in the contract that the item must satisfy it through the framework's own official add/plugin command — e.g. `ng add @angular-eslint`, which installs the linter and wires the `lint` target. Provisioning the named tooling is part of the foundation item's work. Do not opt the check out, downgrade it to what the empty scaffold passes, or hand-invent config. A named check that exists but fails is a real failure, never the absent-check carve-out. If no official mechanism exists to satisfy a named check, the item must halt with a call to action rather than improvise.
- Note that the project's toolchain and oracle commands are re-resolved after the generator runs — before the scaffold there is nothing to detect. The pre-scaffold survey is best-effort; the real commands land once the project exists.
- Decide the project's lockfile policy at this foundation item, not per later item: an application commits its generated lockfile (`uv.lock`, `package-lock.json`, `Cargo.lock`, …) so every worktree and CI resolve the same dependency graph (see [references/definition-of-done.md](references/definition-of-done.md), Lockfiles). State it in the item's `scope.included`/`contract` so build tracks it rather than leaving it untracked.
- Expect the scaffold's footprint to trip the smart-surfaced-review boundary signals at the safety gate (a foundation item touches many new files at once). Pre-justify it in the item's surfacing note so it reads as expected, not as a red flag.

**Oracle traceability rule.** Each oracle assertion must be traceable to the item's `contract`. If an assertion references a field, shape, or behavior the `contract` does not declare, flag the gap now — emit the work item with a note that the contract needs expanding rather than writing an assertion that cannot be verified.

Return the draft binder JSON.

---

After the subagent returns, review the draft. Check:
- Every work item has an `id`, a `contract`, and an `oracle`.
- `depends_on` references resolve to real IDs in this binder (no dangling refs).
- Oracle assertions trace to the item's contract.
- `serialize`/`shared_resources` are set for any items that write shared state (migrations, lock files, config that multiple items touch).
- UI fields are present only on items with a UI surface.
- `sme` lists the packs matched in `plan:sme` (or is absent / `[]` when none matched).

Fix gaps in the main thread before proceeding.

---

### Phase 3 — Smart-surfaced review (the one human-in-the-loop point)  `plan:surface`

Per [references/smart-surfaced-review.md](references/smart-surfaced-review.md): compute the seven boundary signals for each work item and write `surface { flagged, signals }` into the binder. When a signal cannot be computed yet (no diff, no path conventions), record `not-computed:<signal-name>` in `surface.signals` rather than giving a clean pass.

Write everything you show a person in plain language — see [references/user-facing-prose.md](references/user-facing-prose.md).

Show the user which items are flagged and why. Then give them three ways to go:

- **Review all** — walk every work item.
- **Review flagged only** — walk only the flagged items.
- **Accept as-is** — skip the walk and move on.

Keep the list short: don't show oracle details for routine, unflagged items. Settle any decisions here so the deliver and build steps can run hands-off.

---

### Phase 4 — Cost education  `plan:cost`

When the binder has many work items or several large (`L`) estimates, tell the user plainly: this scope will cost time and real money before anything lands. Suggest a smaller first slice — the items with no `depends_on` that form the first wave — as a lower-risk start.

Educate; don't forbid. If the user wants the full scope, move on.

---

### Phase 5 — Emit, validate, and commit  `plan:emit`

**Write the binder** to the resolved location (`.karta/binders/<slug>.json`).

**Validate it.** Run:

```
uv run --script skills/karta-plan/scripts/validate_binder.py --binder <path>
```

The validator is pure stdlib (no dependencies), so `uv run --script`, `uv run`, or `python3` all run it. Do not proceed on a validation failure. Fix the binder and re-validate until it passes.

**Single-work-item hatch.** A binder with exactly one work item can skip the deliver phase and go straight to build. Tell the user this option exists; let them make the call.

**Commit on the `commit` verb.** Show the binder as an editable card. Commit only when the user says "commit." Once committed, the binder is read-only to every later build step.

---

### Phase 6 — Report back  `plan:report`

Lead with the binder path and the total work-item count, then give the user:

- **Work order** — the item IDs in dependency order (topological sort), and the dependency chain.
- **Flagged for review** — the IDs and the signals that flagged each one.
- **Opted-out items** — the IDs and their recorded reasons (from the validator's opt-out summary).
- **First wave** — the items with no `depends_on`. These can start right away.
- **Experts applied** — the `sme` packs in effect for this binder (or *none*).

---

## Gotchas

- **The binder is always synthesized.** karta-plan never exits early with a plan outline or a list of tickets. The output is a validated `.json` binder or nothing.
- **UI fields are conditional, not universal.** `design_reference`, `component_map`, `icon_map`, and `token_changes` belong only on items with a UI surface. Emitting them on a migration, a CLI command, or a data pipeline item is a schema error — the validator will catch it, but don't write it in the first place.
- **The oracle is a real CI-facing check.** It is not a self-grading statement ("implementation looks correct") or a description of what the item does. It is the command you'd want CI to run and the assertions you'd want to confirm.
- **Opt-out is explicit and recorded.** There is no silent opt-out. Every `opt_out: true` requires a `reason`. karta reports opted-out items after every run so nothing slips through unnoticed.
- **Don't delegate synthesis judgment.** The synthesis subagent drafts; the main thread reviews, corrects, and owns the binder. Cross-referencing contracts, oracle traceability, and dependency order requires judgment — do not hand that off.
- **Validate before commit.** A binder that fails `validate_binder.py` is not a valid binder. Fix it before presenting it to the user for commit.
- **One binder per run in V1.** When the scope spans multiple natural binders, suggest a breakdown and plan one binder this run. Multi-binder partitioning is not supported in V1.
- **The binder is read-only once committed.** Build steps read the binder; they do not modify it. A build step that tries to rewrite its own work item's oracle or estimate is corrupting its own governance.
