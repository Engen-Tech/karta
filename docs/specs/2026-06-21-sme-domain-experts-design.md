# SME domain-expert packs — design

- Date: 2026-06-21
- Status: approved (brainstorming) — pending implementation plan
- Scope: an auto-applied domain-expertise layer for karta. karta ships curated **SME packs** (knowledge files of stack-specific do's and don'ts), auto-detects which apply from the repo, pins them in the binder at plan time, and feeds them to the synthesis (plan) and implementer (build) so work is decomposed and written to each stack's norms. Each pack's **Review checklist** is enforceable: the implementer self-checks before commit, and the existing `karta-safety-auditor` flags *undeclared* checklist violations (a declared override with a rationale passes). Ships `angular` and `python-fastapi` built-ins; projects extend with their own in `.karta/sme/`. No new gate authority — enforcement is one conditional check on the boundary gate karta already has.

## 1. Goal and decisive constraints

karta is stack-agnostic by design: it makes no assumption about framework, component library, data layer, or layout, and it reads only the binder and the repo at runtime. That neutrality is a strength for orchestration, but it leaves a gap — karta has no opinion about *how* a given stack should be written. An Angular view and a FastAPI endpoint each have a body of accepted patterns and anti-patterns that a good engineer applies without being asked. This feature gives karta that voice: the right domain expert advises during planning and implementation, automatically.

Constraints (all set by the user):

- **Knowledge packs, not subagents.** An expert is a curated markdown reference loaded inline by the existing plan and build flows — not a separately dispatched advisor session. Cheapest, always-on, and in keeping with karta's "reads at runtime" minimalism.
- **Auto-detected, pinned in the binder.** karta-plan matches packs from the resolved stack/deps and records the chosen pack ids in the binder. karta-build reads that field, so plan and build apply the exact same experts. The binder stays the cross-skill contract.
- **Built-in plus project overlay.** karta ships curated built-ins; a project drops its own packs in `.karta/sme/` to cover arbitrary stacks or override a built-in. Stacks are open-ended, and packs are meant to be fine-tuned over time.
- **Advisory to write by; its checklist is enforceable — through the gate karta already has.** SME do's/don'ts are context the implementer writes against. Each pack's **Review checklist** is the enforceable subset: before commit the implementer self-checks its diff against it and records the result. A deviation is fine when **declared with a rationale** (it surfaces in the report and passes); an **undeclared** checklist violation is caught by `karta-safety-auditor` as a boundary crossing the item never justified — kickback, then escalation at its cap. The acceptance-reviewer stays SME-unaware, so there is still exactly **one acceptance authority**; SME enforcement rides the existing boundary gate as a conditional check, never a new gate.
- **Lean V1.** No kill switch (no match → nothing happens, zero cost), no per-item override, no advisor subagent sessions, no versioning or registry beyond the flat directory.
- **Cross-platform, dual-runtime.** Same canonical-vs-generated discipline as the rest of karta: canonical source under `skills/`, drift-guarded Codex projections, works on Claude Code and Codex across macOS/Linux/Windows.

## 2. Relationship to karta's "no registry, stack-agnostic" stance

There is a real tension to name: a curated catalog of stack-specific experts is, in a sense, the registry karta has avoided. The design resolves it on three points:

- **The catalog is not invariant state.** SME packs carry no run state, no per-project configuration, nothing a later stage reads back. They are static reference text — the same category as the `skills/_shared/*.md` material karta already ships. The binder's `sme[]` is a resolved-at-plan fact, exactly like `design_facts.stack`.
- **It does not narrow the orchestration.** karta's pipeline (plan → deliver → build, gated by verify/validate) is untouched. SME is additive context; remove every pack and karta behaves exactly as it does today.
- **Stack-specificity lives only in pack data, never in control flow.** No skill or agent grows a `case "angular"` branch. The packs are data; the matcher, the build self-check, and the auditor's conformance check are all generic — they read whatever checklist the matched packs carry and judge against it. The enforcement is real but stack-blind: the gate evaluates "did the diff violate a declared checklist rule without justification," never anything Angular- or FastAPI-specific in code.

## 3. The model

### 3a. An SME pack

A markdown knowledge file. Frontmatter declares identity and match tokens; the body is the advisory content. The body sections are a light convention (Do / Don't / Patterns / Review checklist), not a rigid schema — packs are meant to be edited freely. One section carries a defined consumer: **Review checklist** — its checkboxes are what the build self-check runs against the diff (3e) **and the enforceable subset the safety gate judges (3f)**. Do / Don't / Patterns stay purely advisory context; only the checklist has teeth. Author the checklist as concrete, checkable rules and keep aspirational prose in the other sections, so the gate only ever judges objective items. Every pack should carry a checklist.

```
skills/_shared/sme/angular.md
---
name: angular
description: Angular architecture do's and don'ts
match: ["@angular/core", "angular", "@angular/cli"]
---
## Do
- Standalone components; signals for local state; `inject()` over constructor DI.
- OnPush change detection; `takeUntilDestroyed` for subscription cleanup.
## Don't
- No logic in templates; no `any`; no manual `.subscribe()` without teardown.
## Patterns
- Smart/dumb component split; typed reactive forms; route-level lazy loading.
## Review checklist
- [ ] Change-detection strategy set
- [ ] No leaked subscriptions
- [ ] Inputs/outputs typed
```

- `name` — the pack id (kebab-case, unique). This is what lands in the binder's `sme[]` and what a project-local file overrides by reusing.
- `description` — one line, shown in the plan report.
- `match` — an array of tokens compared against the repo's detected dependencies and stack phrase (see 3c). A token matches if it appears as a dependency name (e.g. `@angular/core` in a manifest) or as a substring of the resolved stack phrase (e.g. `angular`, `fastapi`).

### 3b. Where packs live

- **Built-in (canonical):** `skills/_shared/sme/*.md`. Per karta's `_shared` convention, each consumed file is copied byte-equal into the consuming skills' `references/` trees: `skills/karta-plan/references/sme/` and `skills/karta-build/references/sme/`. The Codex mirror under `.agents/skills/` and the marketplace projection under `plugins/karta/` are regenerated by `sync_codex_skills.py`. V1 ships two: `angular`, `python-fastapi`.
- **Project-local overlay:** `.karta/sme/*.md` in the user's repo. A new `name` adds an expert for an unsupported stack; a reused `name` overrides the built-in of that name. This is the open-ended, fine-tune-as-you-go surface and needs no plugin change. It sits beside the existing `.karta/binders/` directory.

**Resolution order is project-local first, then built-in**, keyed by `name`. A `.karta/sme/angular.md` fully replaces the built-in `angular` for that repo.

### 3c. Matching — auto-detect at plan, pin in the binder

karta-plan's survey (`plan:survey`) already resolves the stack phrase and reads dependency manifests. A small, generic matcher runs after the survey:

1. Enumerate available packs (project-local overlaid on built-in, by `name`).
2. For each pack, test its `match` tokens against the detected dependency set and the resolved stack phrase.
3. The matched pack ids become the binder field `sme`.

```jsonc
{
  "design_facts": { "stack": "Python/FastAPI + Angular SPA" },
  "sme": ["python-fastapi", "angular"]   // polyglot repo pins both
}
```

`sme` is an optional array of strings at the binder top level. Absent or `[]` means no expert applied — the zero-cost default.

### 3d. Plan integration

After matching, karta-plan loads the matched packs and threads their guidance into Phase 2 (`plan:synthesize`): the synthesis subagent brief gains a **domain-guidance** section carrying the matched packs' do's/don'ts, so decomposition, `contract`s, and `oracle`s reflect each stack's norms (an Angular item's contract speaks in standalone-component terms; a FastAPI item's oracle expects Pydantic-validated shapes). The main thread leans on the same guidance during its review of the draft.

The pinned ids are written into the binder at Phase 5 (`plan:emit`) alongside the other resolved facts. The Phase 6 report (`plan:report`) gains one line: **Experts applied: angular, python-fastapi** (or *none*).

### 3e. Build integration

karta-build reads the pinned `sme[]` during Phase 1 (`build:gate`) as a cached value, resolves each pack (project-local → built-in), and loads it **before Phase 4 (`build:implement`)**. The implementer follows the loaded guidance while writing code and while fixing gate kickbacks. In a polyglot repo, the implementer applies the pack(s) relevant to the area the item targets (the same area resolution build already does for `App dir / target`).

**Resolution is best-effort and never blocks.** A pinned pack that cannot be resolved at build time (e.g. a project-local pack present at plan time but absent on this machine) produces a **non-fatal note in the build report and the run continues**. Advisory guidance is never on the critical path.

**Self-check before commit.** Before the implementer commits (Phase 9 `build:merge`), it runs each loaded pack's **Review checklist** against the item's diff and records the per-pack result in the build report (Phase 10) — e.g. `SME self-check (angular): 4/4 ok`, or a named miss. When the implementer deliberately goes against a checklist rule, it records a **declared override**: a one-line rationale in the report **and** an inline marker on the diff (the same declared-debt marker family karta already uses — see `skills/karta-build/references/declared-debt.md`), e.g. `// karta-sme-override(angular: no-manual-subscribe): bridging a third-party callback API; teardown handled in ngOnDestroy`. A declared override surfaces and passes; an undeclared violation does not (3f). The self-check itself never halts the build — it produces the report and the markers; the gate is what judges them.

### 3f. The safety gate enforces undeclared overrides

`karta-safety-auditor` already scans the diff for "crossings the work item never justified" and returns `PASS | VIOLATION` with a 3-attempt cap and human escalation. SME enforcement is **one conditional check added there** — it runs **only when the binder declares a non-empty `sme[]`**, so a repo with no packs sees the auditor behave exactly as today.

- **What it judges:** the matched packs' **Review checklist** items against the diff — nothing from Do / Don't / Patterns. A checklist violation the diff/report **declares with a rationale** (an inline `karta-sme-override(...)` marker) is a justified crossing → contributes to `PASS`. An **undeclared** checklist violation → `VIOLATION` → kickback to build; unresolved at the cap → escalate to the human (the auditor's existing path). The implementer can always clear it by *fixing the code* or *declaring the override* — never by suppressing the check.
- **How the packs reach the auditor:** the auditor runs as a fresh dispatched session in the worktree and cannot assume the plugin's files travel with it — and built-in packs live in the plugin, not the repo. `karta-verify` is the **sole dispatcher** of the gate agents (karta-build runs its acceptance check *through* karta-verify; it never dispatches the auditor itself), so karta-verify resolves the packs: it reads the binder's `sme[]`, resolves each against its own shipped `references/sme/` (built-in) overlaid by the worktree's `.karta/sme/` (project-local), and includes the resolved **Review checklists** in the auditor's dispatch brief. This is a deliberate, narrow exception to karta-verify's "only the four inputs travel" rule — scoped to when `sme[]` is non-empty — because a project-extensible checklist cannot be embedded in the self-contained agent. It makes `karta-verify` a third pack consumer (it carries a `references/sme/` copy).
- **The acceptance-reviewer stays SME-unaware** — it judges `oracle`/`contract` only. One acceptance authority is preserved; the boundary gate gains one conditional signal.
- `validate_binder.py` validates `sme` as an optional array of strings (schema only). It does **not** require the named packs to resolve — project-local packs may live on another machine, and requiring resolution would couple the validator to the filesystem overlay. Runtime resolution is the dispatcher's concern; an unresolved pack only warns.

## 4. Wiring (canonical-vs-generated discipline)

- New canonical content: `skills/_shared/sme/angular.md`, `skills/_shared/sme/python-fastapi.md`, each carrying a concrete **Review checklist**.
- Per-consumer copies kept byte-equal to `_shared` and checked by `check_shared_copies.py`: `skills/karta-plan/references/sme/*.md` (matching + guidance), `skills/karta-build/references/sme/*.md` (implement + self-check), and `skills/karta-verify/references/sme/*.md` (resolve checklists for the auditor dispatch).
- `check_shared_copies.py` is generalized to compare nested `_shared` subdirs (path-relative keying) so the `sme/` subdirectory is covered; it is backward-compatible with the existing flat copies.
- `sync_codex_skills.py` regenerates the `.agents/skills/` mirror and the `plugins/karta/` projection so the new reference dirs travel to Codex (it already recurses); `validate_plugin.py` covers them in its single pass.
- `agents/karta-safety-auditor.md` gains the conditional **SME-norm conformance** check (runs only when `sme[]` is non-empty; judges declared vs. undeclared Review-checklist violations); `sync_codex_agents.py` regenerates `.codex/agents/karta-safety-auditor.toml` and the bundled `skills/karta-verify/references/karta-safety-auditor.agent.md`.
- SKILL.md edits: `karta-plan` (matcher after `plan:survey`, domain-guidance in `plan:synthesize`, `sme` in `plan:emit`, report line in `plan:report`); `karta-build` (read `sme[]` in `build:gate`, load packs before `build:implement`, self-check + declared-override markers before commit in `build:merge`, results + missing-pack note in `build:report`); `karta-verify` (resolve `sme[]` from its own `references/sme/` + the worktree's `.karta/sme/`, and pass the Review checklists into the safety-auditor dispatch — the sole dispatch site).
- `references/binder-reference.md` gains the `sme` field; `binder-schema.json` + `validate_binder.py` gain the optional-string-array check and self-test cases.
- `AGENTS.md` layout table gains a row for `skills/_shared/sme/` and the per-skill copies; `README.md` gains a short SME section.

## 5. Non-goals (YAGNI)

- No advisor subagent sessions — packs are inline context for the writer and a resolved checklist for the auditor; no extra dispatched advisor.
- **No new gate authority** — the acceptance-reviewer stays SME-unaware; enforcement rides the existing safety-auditor as one conditional check, scoped to the **Review checklist**, and any deviation is passable by declaring a rationale.
- **No style-nit blocking** — only *undeclared* violations of concrete *checklist* rules block; Do / Don't / Patterns prose never blocks.
- No kill switch — no match is the off state (and the auditor check no-ops on an empty `sme[]`).
- No per-item `sme[]` override — packs apply by detected stack/area.
- No pack versioning, dependency, or registry beyond the flat `sme/` directory.
- No automated authoring of project-local packs — that is the user's content.

## 6. Build sequence (outline for the plan)

1. Author the two built-in packs (with Review checklists); generalize `check_shared_copies.py` for nested subdirs; add the byte-equal copies into the three consumers (plan, build, verify).
2. Add the `sme` field to `binder-schema.json`, `binder-reference.md`, and the `validate_binder.py` self-test.
3. karta-plan: matcher after `plan:survey`, domain-guidance in synthesis, pin in `plan:emit`, report line.
4. karta-build: read `sme[]`, resolve + load packs before implement, self-check + declared-override markers before commit, report results + missing-pack note.
5. karta-safety-auditor: the conditional SME-norm conformance check; karta-verify resolves and passes the resolved checklists into its dispatch.
6. Regenerate Codex projections (`sync_codex_agents.py`, then `sync_codex_skills.py`); update `AGENTS.md` and `README.md`.
7. Run the four pre-commit checks (`validate_plugin.py --self-test`, `check_shared_copies.py --self-test`, both `--check` syncs).

## 7. Open questions

None blocking. Naming (`sme` as the field/dir token vs. `experts`) is settled as `sme` to match the approved previews; trivially renameable before the plan lands if preferred.
