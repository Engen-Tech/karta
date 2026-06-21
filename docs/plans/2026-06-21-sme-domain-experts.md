# SME Domain-Expert Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give karta a curated, auto-applied domain-expertise layer — stack-specific "SME packs" of do's/don'ts that shape planning and implementation, with an enforceable per-pack Review checklist gated by the existing safety-auditor.

**Architecture:** SME packs are markdown reference files (built-in under `skills/_shared/sme/`, project overlay under `.karta/sme/`). karta-plan auto-matches them from the resolved stack/deps and pins their ids in the binder's new `sme[]` field. karta-build loads the matched packs to write against and self-checks its diff before commit, recording declared overrides as inline `KARTA-SME-OVERRIDE` markers. karta-verify resolves the pinned packs' Review checklists and passes them to `karta-safety-auditor`, which — only when `sme[]` is non-empty — flags any **undeclared** checklist violation as a boundary VIOLATION. The acceptance-reviewer stays SME-unaware; there is still exactly one acceptance authority.

**Tech Stack:** Markdown skills/agents (Claude + Codex plugin), small Python 3.11 scripts run via `uv` (`jsonschema`), JSON Schema (Draft 2020-12). No new runtime dependency; no new script.

## Global Constraints

- **Spec:** `docs/specs/2026-06-21-sme-domain-experts-design.md` is the source of truth. Read it before starting.
- **Advisory to write by; only the Review checklist is enforceable.** Do / Don't / Patterns never block. A checklist deviation passes when declared with a rationale (an inline `KARTA-SME-OVERRIDE(<pack>: <rule>): <reason>` marker); an **undeclared** checklist violation is a safety-auditor VIOLATION.
- **One acceptance authority.** `karta-acceptance-reviewer` stays SME-unaware. SME enforcement is one **conditional** check on `karta-safety-auditor`, active only when the binder's `sme[]` is non-empty. No new gate, no new agent.
- **No per-stack control flow.** Packs are data; the matcher, self-check, and auditor check are generic. No `case "angular"` anywhere in code or prose.
- **No new script and no new runtime dependency.** Matching and the build self-check are SKILL.md prose the agent follows. Only existing scripts change (`validate_binder.py`, `check_shared_copies.py`).
- **Token is `sme`.** Built-in packs: `skills/_shared/sme/<id>.md`. Per-consumer byte-equal copies: `skills/{karta-plan,karta-build,karta-verify}/references/sme/<id>.md`. Project overlay: `.karta/sme/<id>.md` (project-local wins on a `name` clash).
- **Canonical → generated discipline (AGENTS.md).** Edit canonical, then regenerate. Canonical: `skills/<name>/`, `skills/_shared/`, `agents/<name>.md`. Generated (never hand-edit): `.agents/skills/`, `plugins/karta/`, `.codex/agents/*.toml`, `skills/karta-verify/references/*.agent.md`. After editing a gate agent run `uv run scripts/sync_codex_agents.py` **then** `uv run scripts/sync_codex_skills.py`; after editing any skill/reference run `uv run scripts/sync_codex_skills.py`. Each commit must leave all four pre-commit checks green: `uv run scripts/validate_plugin.py --self-test`, `uv run scripts/check_shared_copies.py --self-test`, `uv run scripts/sync_codex_agents.py --check`, `uv run scripts/sync_codex_skills.py --check`.
- **Markdown rules:** tables use minimum separators (`|-|-|`); never box-drawing characters. Never write the phrase "load bearing" or "fencing".
- **Branch:** all work lands on `feat/sme-domain-experts` (already checked out).

---

## File Structure

New canonical files:
- `skills/_shared/sme/angular.md` — Angular do's/don'ts + Review checklist.
- `skills/_shared/sme/python-fastapi.md` — Python/Pydantic/FastAPI do's/don'ts + Review checklist.

New byte-equal copies (one per consumer; kept equal to `_shared` by `check_shared_copies.py`):
- `skills/karta-plan/references/sme/{angular,python-fastapi}.md`
- `skills/karta-build/references/sme/{angular,python-fastapi}.md`
- `skills/karta-verify/references/sme/{angular,python-fastapi}.md`

Modified canonical files:
- `scripts/check_shared_copies.py` — recurse into `_shared` subdirs; path-relative keying.
- `skills/karta-plan/references/binder-schema.json` — add top-level `sme`.
- `skills/karta-plan/references/binder-reference.md` — document `sme`.
- `skills/karta-plan/scripts/validate_binder.py` — self-test cases for `sme`.
- `skills/karta-plan/SKILL.md` — config row, matcher, domain-guidance, pin, report line.
- `skills/karta-build/SKILL.md` — config row, cache `SME_PACKS`, load packs, self-check + markers, report.
- `agents/karta-safety-auditor.md` — conditional SME-norm conformance check.
- `skills/karta-verify/SKILL.md` — resolve checklists, pass into the auditor dispatch.
- `AGENTS.md`, `README.md` — layout row + SME section.

Regenerated (by the sync scripts; commit them, never hand-edit):
- `.agents/skills/**`, `plugins/karta/**`, `.codex/agents/karta-safety-auditor.toml`, `skills/karta-verify/references/karta-safety-auditor.agent.md`.

---

### Task 1: Author the two built-in SME packs

**Files:**
- Create: `skills/_shared/sme/angular.md`
- Create: `skills/_shared/sme/python-fastapi.md`

**Interfaces:**
- Produces: two packs, each with frontmatter `name` (kebab-case, matches the filename stem), `description`, `match` (string array), and a body containing a `## Review checklist` section of `- [ ]` items. Pack `name`s: `angular`, `python-fastapi`. These ids are what later tasks match, pin, load, and enforce.

- [ ] **Step 1: Write `skills/_shared/sme/angular.md`**

```markdown
---
name: angular
description: Angular architecture do's and don'ts
match: ["@angular/core", "@angular/cli", "angular"]
---
## Do
- Use standalone components, directives, and pipes; avoid declaring them in NgModules.
- Use signals (`signal`, `computed`, `effect`) for local component state; prefer the `inject()` function over constructor injection.
- Set `changeDetection: ChangeDetectionStrategy.OnPush` on components.
- Use typed reactive forms (`FormGroup`/`FormControl` with explicit types) for non-trivial input.
- Clean up subscriptions with `takeUntilDestroyed()` or the `async` pipe; prefer the `async` pipe over manual subscription.
- Lazy-load feature routes with `loadComponent` / `loadChildren`.

## Don't
- Don't put logic in templates beyond simple expressions; move it to the component or a pipe.
- Don't use `any`; type inputs, outputs, and service results.
- Don't call `.subscribe()` without a teardown path.
- Don't mutate `@Input()` values; treat inputs as read-only.
- Don't reach into the DOM with `ElementRef.nativeElement` when a binding or directive will do.

## Patterns
- Smart/presentational split: container components own data and effects; presentational components take inputs and emit outputs.
- One responsibility per service; provide app-wide singletons with `providedIn: 'root'`.
- Co-locate a component's template, styles, and spec with the component.

## Review checklist
- [ ] No `any` in changed component/service signatures.
- [ ] Every new component declares `ChangeDetectionStrategy.OnPush`.
- [ ] No `.subscribe()` without `takeUntilDestroyed()`, an `async` pipe, or an explicit unsubscribe.
- [ ] New components/directives/pipes are `standalone`, not added to an NgModule's `declarations`.
- [ ] No business logic embedded in a template expression.
```

- [ ] **Step 2: Write `skills/_shared/sme/python-fastapi.md`**

```markdown
---
name: python-fastapi
description: Python + Pydantic + FastAPI do's and don'ts
match: ["fastapi", "pydantic", "python"]
---
## Do
- Define request and response bodies as Pydantic models; set `response_model` on routes.
- Use type hints on every function signature; let FastAPI derive validation from them.
- Use dependency injection (`Depends`) for shared resources (DB sessions, auth, settings).
- Use `async def` for I/O-bound path operations; keep blocking work off the event loop.
- Load configuration through `pydantic-settings` (`BaseSettings`), not bare `os.environ` reads scattered in code.
- Raise `HTTPException` (or a registered exception handler) for error responses; return typed models for success.

## Don't
- Don't return raw `dict`s from routes when a response model fits; don't leak ORM models directly as response bodies.
- Don't use mutable default arguments; don't use bare `except:`.
- Don't do blocking I/O (sync DB driver, `requests`, `time.sleep`) inside an `async def` route.
- Don't put secrets or environment-specific values in source; read them through settings.
- Don't disable Pydantic validation to make a check pass.

## Patterns
- Routers per resource (`APIRouter`), included into the app; keep `main.py` thin.
- A service/repository layer between routes and the data store; routes stay declarative.
- Pydantic v2 idioms: `model_config`, `field_validator`, `model_validator`; `ConfigDict` over class-based `Config`.

## Review checklist
- [ ] Every changed route declares request/response types (Pydantic model or explicit `response_model`).
- [ ] No bare `except:` and no mutable default arguments in changed code.
- [ ] No blocking I/O inside an `async def` route.
- [ ] New config/secrets read through a settings object, not inline `os.environ`.
- [ ] Changed public functions carry type hints on params and return.
```

- [ ] **Step 3: Verify both packs are well-formed**

Run:
```bash
for p in angular python-fastapi; do
  f="skills/_shared/sme/$p.md"
  grep -q "^name: $p$" "$f" && grep -q "^match:" "$f" && grep -q "^## Review checklist$" "$f" \
    && grep -qE "^- \[ \] " "$f" && echo "$p OK" || echo "$p MALFORMED"
done
```
Expected: `angular OK` and `python-fastapi OK`.

- [ ] **Step 4: Confirm the pre-commit checks are still green** (`_shared` files are not mirrored and have no copies yet)

Run: `uv run scripts/check_shared_copies.py --self-test`
Expected: `SHARED COPIES: IN SYNC`

- [ ] **Step 5: Commit**

```bash
git add skills/_shared/sme/
git commit -m "feat(sme): add built-in angular and python-fastapi expert packs"
```

---

### Task 2: Generalize `check_shared_copies.py` for nested subdirs; add the byte-equal copies

This task closes a real gap: the current checker only globs `skills/_shared/*.md` and `skills/*/references/*.md` (flat), so a copy under `references/sme/` would never be drift-checked. We generalize it to recurse with path-relative keying (backward-compatible with the existing flat copies), then add the six copies.

**Files:**
- Modify: `scripts/check_shared_copies.py` (the `check()` function, lines 17-25)
- Create: `skills/karta-plan/references/sme/{angular,python-fastapi}.md`
- Create: `skills/karta-build/references/sme/{angular,python-fastapi}.md`
- Create: `skills/karta-verify/references/sme/{angular,python-fastapi}.md`

**Interfaces:**
- Consumes: the two `_shared/sme/*.md` packs from Task 1.
- Produces: `check()` keyed by path relative to `_shared` (e.g. `sme/angular.md`), recursing both sides; six byte-equal reference copies.

- [ ] **Step 1: Create the six byte-equal copies**

```bash
for skill in karta-plan karta-build karta-verify; do
  mkdir -p "skills/$skill/references/sme"
  cp skills/_shared/sme/angular.md        "skills/$skill/references/sme/angular.md"
  cp skills/_shared/sme/python-fastapi.md "skills/$skill/references/sme/python-fastapi.md"
done
```

- [ ] **Step 2: Prove the gap — corrupt one copy, run the OLD checker, watch it MISS the drift**

```bash
printf '\n<!-- drift probe -->\n' >> skills/karta-plan/references/sme/angular.md
uv run scripts/check_shared_copies.py --self-test
```
Expected: `SHARED COPIES: IN SYNC` — **wrong**. The flat globber never looks inside `references/sme/`, so it cannot see the drift. This is the gap this task closes.

- [ ] **Step 3: Rewrite `check()` to recurse with path-relative keying**

Replace the `check()` function (currently lines 17-25) entirely:

```python
def check() -> list[str]:
    errors: list[str] = []
    shared = {p.relative_to(SHARED).as_posix(): p.read_text()
              for p in SHARED.rglob("*.md")}
    for skill_dir in sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir()):
        if skill_dir.name == "_shared":
            continue
        refs = skill_dir / "references"
        if not refs.is_dir():
            continue
        for ref in refs.rglob("*.md"):
            rel = ref.relative_to(refs).as_posix()
            if rel in shared and ref.read_text() != shared[rel]:
                errors.append(f"{ref.relative_to(ROOT)} drifted from skills/_shared/{rel}")
    return errors
```

(Backward-compatible: a flat `_shared/foo.md` keys as `foo.md`, exactly as before; the matching reference `skills/*/references/foo.md` keys as `foo.md` too.)

- [ ] **Step 4: Run the NEW checker — it now CATCHES the drift**

Run: `uv run scripts/check_shared_copies.py --self-test`
Expected: `SHARED COPIES: DRIFT` listing `skills/karta-plan/references/sme/angular.md drifted from skills/_shared/sme/angular.md`.

- [ ] **Step 5: Fix the corrupted copy and confirm green**

```bash
cp skills/_shared/sme/angular.md skills/karta-plan/references/sme/angular.md
uv run scripts/check_shared_copies.py --self-test
```
Expected: `SHARED COPIES: IN SYNC`

- [ ] **Step 6: Regenerate Codex projections (the new `references/sme/` dirs must travel) and verify**

Run:
```bash
uv run scripts/sync_codex_skills.py
uv run scripts/sync_codex_skills.py --check
```
Expected: the write run reports files written; `--check` prints `CODEX SKILLS PROJECTIONS: IN SYNC`.

- [ ] **Step 7: Commit (canonical + generated together)**

```bash
git add scripts/check_shared_copies.py skills/*/references/sme/ .agents/ plugins/
git commit -m "feat(sme): drift-check nested _shared subdirs; add sme reference copies"
```

---

### Task 3: Add the `sme` binder field — schema, reference, validator self-test

**Files:**
- Modify: `skills/karta-plan/references/binder-schema.json` (top-level `properties`, after `design_facts`)
- Modify: `skills/karta-plan/scripts/validate_binder.py` (`_run_self_test`, before `cases = [`)
- Modify: `skills/karta-plan/references/binder-reference.md` (binder-level field table)

**Interfaces:**
- Produces: an optional top-level binder field `sme`: an array of kebab-case strings. `validate_binder()` accepts it; `additionalProperties: false` would otherwise reject it.

- [ ] **Step 1: Add the failing self-test cases first**

In `skills/karta-plan/scripts/validate_binder.py`, find the line `    cases = [` (followed by `        ("valid example", valid, True),`) inside `_run_self_test()` and insert the three fixtures plus three case rows immediately before/within it. Replace:

```python
    cases = [
        ("valid example", valid, True),
```

with:

```python
    sme_valid = {
        "slug": "sme-ok", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["angular", "python-fastapi"],
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    sme_not_array = {
        "slug": "sme-bad", "motivation": "x", "scope": {"included": ["x"]},
        "sme": "angular",
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    sme_bad_id = {
        "slug": "sme-badid", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["Angular_Expert"],
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    cases = [
        ("valid example", valid, True),
        ("binder with sme packs", sme_valid, True),
        ("sme not an array", sme_not_array, False),
        ("sme id bad pattern", sme_bad_id, False),
```

(`_u = {"type": "unit"}` is already defined earlier in the function and is in scope here.)

- [ ] **Step 2: Run the self-test — the new VALID case FAILS**

Run: `uv run skills/karta-plan/scripts/validate_binder.py --self-test`
Expected: a `[FAIL] binder with sme packs` line — the schema's `additionalProperties: false` rejects the unknown `sme` property, so the "should be valid" case is reported invalid. Overall exit non-zero.

- [ ] **Step 3: Add `sme` to the schema**

In `skills/karta-plan/references/binder-schema.json`, replace:

```json
        "stack": { "type": "string", "description": "resolved stack, recorded once" }
      }
    },
    "token_manifest": {
```

with:

```json
        "stack": { "type": "string", "description": "resolved stack, recorded once" }
      }
    },
    "sme": {
      "type": "array",
      "items": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$" },
      "description": "ids of advisory SME packs auto-matched at plan time; the Review checklist of each is the enforceable subset the safety-auditor judges (project-local .karta/sme/<id>.md overrides the built-in references/sme/<id>.md)"
    },
    "token_manifest": {
```

- [ ] **Step 4: Run the self-test — all cases PASS**

Run: `uv run skills/karta-plan/scripts/validate_binder.py --self-test`
Expected: every line `[PASS]`, final line `16/16 checks passed` (12 prior + 3 new + the opt-out check), exit 0.

- [ ] **Step 5: Document the field in `binder-reference.md`**

In `skills/karta-plan/references/binder-reference.md`, replace:

```
| `design_facts.stack` | string | no | Resolved tech stack, recorded once at plan time |
```

with:

```
| `design_facts.stack` | string | no | Resolved tech stack, recorded once at plan time |
| `sme` | string[] | no | Ids of advisory SME packs matched at plan time; the implementer writes against them and `karta-safety-auditor` enforces each pack's Review checklist (undeclared violations only). Absent or `[]` = none |
```

- [ ] **Step 6: Regenerate projections (binder-schema/reference/validator live under the karta-plan skill) and verify all checks**

Run:
```bash
uv run scripts/sync_codex_skills.py
uv run scripts/check_shared_copies.py --self-test
uv run scripts/sync_codex_skills.py --check
uv run scripts/validate_plugin.py --self-test
```
Expected: `IN SYNC` / `IN SYNC` and the plugin self-test passes.

- [ ] **Step 7: Commit**

```bash
git add skills/karta-plan/ .agents/ plugins/
git commit -m "feat(sme): add optional sme[] binder field (schema, reference, validator)"
```

---

### Task 4: karta-plan — match packs, feed synthesis, pin, report

All edits are to `skills/karta-plan/SKILL.md`. The matcher is prose the planner follows (no script). Each edit is an exact anchored replacement.

**Files:**
- Modify: `skills/karta-plan/SKILL.md`

**Interfaces:**
- Consumes: the resolved stack/deps from `plan:survey`; the packs under `references/sme/` + `.karta/sme/`.
- Produces: the binder's `sme[]` (matched ids), and SME do's/don'ts threaded into the synthesis brief.

- [ ] **Step 1: Add the SME config row**

Replace:
```
| **Repo policy** | CI/branch/deployment policy (only when in planning scope) | Read root/area `AGENTS.md`, workflows, and CI docs; load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
```
with:
```
| **Repo policy** | CI/branch/deployment policy (only when in planning scope) | Read root/area `AGENTS.md`, workflows, and CI docs; load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
| **SME packs** | Advisory domain-expert do's/don'ts to apply (each with an enforceable Review checklist) | Match the built-in [references/sme/](references/sme/) plus the project overlay `.karta/sme/*.md` against the detected deps/stack; record matched ids in the binder's `sme` |
```

- [ ] **Step 2: Add the matcher subsection at the end of Phase 1**

Replace:
```
Non-UI stacks skip this annex entirely — the base survey above is all they need.

---

### Phase 2 — Synthesize the binder (synthesis subagent; main thread owns judgment)  `plan:synthesize`
```
with:
```
Non-UI stacks skip this annex entirely — the base survey above is all they need.

**SME expert matching (after the survey)  `plan:sme`.** karta ships curated SME packs — advisory do's/don'ts per stack, each with an enforceable Review checklist — and a project may add its own. Once the survey resolves the stack and dependencies, select the packs that apply:

1. Enumerate available packs by `name`: the project overlay `.karta/sme/*.md` in the repo, laid over the built-ins in [references/sme/](references/sme/). On a name clash the project-local file wins.
2. For each pack, read its frontmatter `match` tokens and test them against the survey's detected dependency names and the resolved stack phrase. A token matches if it equals a detected dependency name or is a case-insensitive substring of the stack phrase.
3. Collect the matched pack `name`s — this list becomes the binder's `sme`. A polyglot repo matches more than one (e.g. `["python-fastapi", "angular"]`); a repo with no matching pack gets `[]` and SME is silently absent.

Load the matched packs now — their guidance feeds synthesis (Phase 2) and their ids are pinned into the binder (Phase 5). These packs are **advisory for decomposition**: they shape how items are split, what each `contract` says, and which `oracle` assertions you choose; they never add a gate at plan time.

---

### Phase 2 — Synthesize the binder (synthesis subagent; main thread owns judgment)  `plan:synthesize`
```

- [ ] **Step 3: Add the domain-guidance paragraph to the synthesis brief**

Replace:
```
Given the intent `<intent>` and the repo survey (`plan:survey`), draft a binder JSON that conforms to [references/binder-reference.md](references/binder-reference.md).
```
with:
```
Given the intent `<intent>` and the repo survey (`plan:survey`), draft a binder JSON that conforms to [references/binder-reference.md](references/binder-reference.md).

**Domain guidance (when SME packs matched in `plan:sme`).** When one or more SME packs matched, their do's/don'ts are domain guidance for this synthesis. Decompose items, write `contract`s, and choose `oracle` assertions so they respect each matched stack's patterns and avoid its anti-patterns (e.g. an Angular slice's `contract` speaks in standalone-component / signals terms; a FastAPI item's `oracle` expects Pydantic-validated request/response shapes). This guidance shapes the plan; it never adds a plan-time gate.
```

- [ ] **Step 4: Add `sme` to the binder-level populate list**

Replace:
```
- `token_manifest` only when the stack has a token system
```
with:
```
- `token_manifest` only when the stack has a token system
- `sme` — the SME pack ids matched in `plan:sme`; omit or use `[]` when none matched
```

- [ ] **Step 5: Add `sme` to the post-synthesis review checklist**

Replace:
```
- UI fields are present only on items with a UI surface.

Fix gaps in the main thread before proceeding.
```
with:
```
- UI fields are present only on items with a UI surface.
- `sme` lists the packs matched in `plan:sme` (or is absent / `[]` when none matched).

Fix gaps in the main thread before proceeding.
```

- [ ] **Step 6: Add the report line to Phase 6**

Replace:
```
- **First wave** — the items with no `depends_on`. These can start right away.
```
with:
```
- **First wave** — the items with no `depends_on`. These can start right away.
- **Experts applied** — the `sme` packs in effect for this binder (or *none*).
```

- [ ] **Step 7: Verify the insertions and regenerate**

Run:
```bash
grep -c "plan:sme" skills/karta-plan/SKILL.md          # expect 3
grep -q "SME expert matching" skills/karta-plan/SKILL.md && echo matcher-ok
grep -q "Experts applied" skills/karta-plan/SKILL.md && echo report-ok
uv run scripts/sync_codex_skills.py && uv run scripts/sync_codex_skills.py --check
```
Expected: `3`, `matcher-ok`, `report-ok`, `CODEX SKILLS PROJECTIONS: IN SYNC`.

- [ ] **Step 8: Commit**

```bash
git add skills/karta-plan/SKILL.md .agents/ plugins/
git commit -m "feat(sme): karta-plan matches packs, feeds synthesis, pins sme[]"
```

---

### Task 5: karta-build — load packs, self-check the diff, record declared overrides

All edits are to `skills/karta-build/SKILL.md`.

**Files:**
- Modify: `skills/karta-build/SKILL.md`

**Interfaces:**
- Consumes: the binder's `sme[]`; packs under `references/sme/` + `.karta/sme/`; the `KARTA-DEFER` marker convention in `references/declared-debt.md`.
- Produces: `SME_PACKS` cached in `build:gate`; packs loaded in `build:implement`; a pre-commit self-check in `build:merge` that emits `KARTA-SME-OVERRIDE(<pack>: <rule>): <reason>` markers for deliberate deviations and a per-pack result line in `build:report`.

- [ ] **Step 1: Cache `SME_PACKS` in `build:gate`**

Replace:
```
- UI annex fields, **only if present**: `COMPONENT_MAP` (`component_map`), `ICON_MAP` (`icon_map`), `TOKEN_CHANGES` (`token_changes`), `DESIGN_REFERENCE` (`design_reference`), and the binder's `design_facts.source`
```
with:
```
- UI annex fields, **only if present**: `COMPONENT_MAP` (`component_map`), `ICON_MAP` (`icon_map`), `TOKEN_CHANGES` (`token_changes`), `DESIGN_REFERENCE` (`design_reference`), and the binder's `design_facts.source`
- `SME_PACKS` — the binder's `sme` list (advisory expert pack ids), if present; resolved and loaded in `build:implement`, self-checked in `build:merge`
```

- [ ] **Step 2: Add the SME config row to build's Project configuration table**

Replace:
```
| **Repo policy** | Branch/CI/ruleset/deployment policy, only when the item touches those areas | Read root/area `AGENTS.md`, existing workflows, CI docs when remote policy is in scope. For details load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
```
with:
```
| **Repo policy** | Branch/CI/ruleset/deployment policy, only when the item touches those areas | Read root/area `AGENTS.md`, existing workflows, CI docs when remote policy is in scope. For details load [references/ci-policy.md](references/ci-policy.md) and [references/policy-yagni.md](references/policy-yagni.md) |
| **SME packs** | Advisory expert do's/don'ts to write against, with an enforceable Review checklist | Read the binder's `sme`; resolve each id against the project overlay `.karta/sme/*.md` laid over the built-in [references/sme/](references/sme/) |
```

- [ ] **Step 3: Load the packs in `build:implement` (new step 4c-ter)**

Replace:
```
**4c-bis. Build the token manifest (UI + DTCG token systems only).** When the item carries a UI surface and the project has a DTCG/tiered token system, build the token manifest before any token lookup — see **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. Skip entirely otherwise.

**4d. Implement the item** against the resolved conventions, stack-agnostically. Key rules:
```
with:
```
**4c-bis. Build the token manifest (UI + DTCG token systems only).** When the item carries a UI surface and the project has a DTCG/tiered token system, build the token manifest before any token lookup — see **[references/dtcg-tokens.md](references/dtcg-tokens.md)**. Skip entirely otherwise.

**4c-ter. Load the SME packs (when `SME_PACKS` is non-empty).** For each id in `SME_PACKS`, resolve the pack file — the project overlay `.karta/sme/<id>.md` in the worktree, else the built-in [references/sme/](references/sme/) `<id>.md`. Read each resolved pack and hold its **Do / Don't / Patterns / Review checklist** as implementation guidance for this item; in a polyglot repo apply the pack(s) matching the area this item targets. If a pinned id resolves to no file, record a one-line non-fatal note for the report (`build:report`) and continue — advisory guidance never blocks. Follow this guidance while implementing (4d) and while fixing any gate kickback.

**4d. Implement the item** against the resolved conventions, stack-agnostically. Key rules:
```

- [ ] **Step 4: Add the pre-commit self-check to `build:merge` (new step 9-sme, before 9a)**

Replace:
```
The integration tip has exactly one writer per [references/integration-branch.md](references/integration-branch.md). Steps 9a (secret scan) and 9b (commit) run in **both** modes; step 9c branches on `RUN_MODE`.

**9a. Secret scan before every commit.**
```
with:
```
The integration tip has exactly one writer per [references/integration-branch.md](references/integration-branch.md). Steps 9a (secret scan) and 9b (commit) run in **both** modes; step 9c branches on `RUN_MODE`.

**9-sme. SME self-check before commit (when `SME_PACKS` is non-empty).** For each loaded pack (4c-ter), run its **Review checklist** against the item's diff (`git diff "$integration"...HEAD`). For every checklist item, decide pass or miss. Two outcomes for a miss:

- **Fix it** — adjust the code so the checklist item passes. Preferred.
- **Declare the override** — when the deviation is deliberate and justified, leave the code and record a declared override at the deviation site, modelled on the `KARTA-DEFER` family (see [references/declared-debt.md](references/declared-debt.md)): an inline comment `KARTA-SME-OVERRIDE(<pack>: <checklist-rule>): <one-line rationale>`. A declared override is a justified crossing; the safety-auditor passes it.

Record the per-pack tally for the report (`build:report`), e.g. `SME self-check (angular): 4/4 ok` or `3/4 — 1 declared override`. This self-check **never halts the build** — it produces the markers and the report line. The judgment of declared-vs-undeclared belongs to `karta-safety-auditor` at the gate: an **undeclared** checklist violation is a VIOLATION there (kickback), so leaving a miss neither fixed nor declared will fail the boundary scan. Only Review-checklist items have teeth; Do / Don't / Patterns are advisory.

**9a. Secret scan before every commit.**
```

- [ ] **Step 5: Add the SME report bullet to `build:report`**

Replace:
```
- **Acceptance result** — which gate ran (`karta-verify` / `karta-validate` / opted out), final disposition, rounds used, any residual finding
```
with:
```
- **Acceptance result** — which gate ran (`karta-verify` / `karta-validate` / opted out), final disposition, rounds used, any residual finding
- **SME self-check** — per applied pack, the Review-checklist tally and any `KARTA-SME-OVERRIDE` declared (what, which rule, why); plus a note for any pinned `sme` id that resolved to no pack file. Omit the whole line when `SME_PACKS` is empty
```

- [ ] **Step 6: Verify the insertions and regenerate**

Run:
```bash
grep -q "4c-ter" skills/karta-build/SKILL.md && echo load-ok
grep -q "9-sme" skills/karta-build/SKILL.md && echo selfcheck-ok
grep -q "KARTA-SME-OVERRIDE" skills/karta-build/SKILL.md && echo marker-ok
uv run scripts/sync_codex_skills.py && uv run scripts/sync_codex_skills.py --check
```
Expected: `load-ok`, `selfcheck-ok`, `marker-ok`, `CODEX SKILLS PROJECTIONS: IN SYNC`.

- [ ] **Step 7: Commit**

```bash
git add skills/karta-build/SKILL.md .agents/ plugins/
git commit -m "feat(sme): karta-build loads packs, self-checks diff, records overrides"
```

---

### Task 6: Safety-auditor enforces undeclared overrides; karta-verify resolves checklists

**Files:**
- Modify: `agents/karta-safety-auditor.md` (canonical agent)
- Modify: `skills/karta-verify/SKILL.md`
- Regenerate: `.codex/agents/karta-safety-auditor.toml`, `skills/karta-verify/references/karta-safety-auditor.agent.md` (via `sync_codex_agents.py`)

**Interfaces:**
- Consumes: the binder's `sme[]`; the packs' Review checklists (resolved by karta-verify); the `KARTA-SME-OVERRIDE` markers from Task 5.
- Produces: a conditional auditor check — `PASS` when no `sme[]` or all checklist violations are declared; `VIOLATION` on any undeclared checklist violation (existing kickback + 3-attempt-then-escalate machinery).

- [ ] **Step 1: Update the auditor's `description` frontmatter**

In `agents/karta-safety-auditor.md`, replace:
```
description: Read-only boundary scan on the actual diff. Re-runs the seven smart-surfaced-review signals against the real code and flags any sensitive, destructive, or contract crossing the work item never justified; verdict PASS | VIOLATION; max 3 attempts then escalate to the human.
```
with:
```
description: Read-only boundary scan on the actual diff. Re-runs the seven smart-surfaced-review signals against the real code, plus a conditional SME-norm conformance check when the binder pins sme[], and flags any sensitive, destructive, contract, or undeclared SME-checklist crossing the work item never justified; verdict PASS | VIOLATION; max 3 attempts then escalate to the human.
```

- [ ] **Step 2: Add the conditional checklist input**

Replace:
```
3. **The diff range** — the item branch versus the integration tip. Run `git diff <range>` in the shell to see exactly what changed. You scan the diff, not the whole tree.

Read the binder JSON directly. There is no invariants registry, no resolver, no stored rule state, and no per-repo placeholder rule to fail closed on. The rule set is the seven signals below — they are always configured because they are embedded here.
```
with:
```
3. **The diff range** — the item branch versus the integration tip. Run `git diff <range>` in the shell to see exactly what changed. You scan the diff, not the whole tree.
4. **SME Review checklists (conditional).** Only when the binder's `sme[]` is non-empty, the dispatcher hands you the resolved **Review checklist** of each pinned pack (the built-in and project-local packs cannot be re-derived by you — built-ins live in the plugin, not the worktree — so they travel in your dispatch). When `sme[]` is empty or absent you receive none and skip the SME-norm check below entirely.

Read the binder JSON directly. There is no invariants registry, no resolver, no stored rule state, and no per-repo placeholder rule to fail closed on. The base rule set is the seven signals below — always configured because they are embedded here; the SME-norm check is the one conditional addition, and only when checklists were handed to you.
```

- [ ] **Step 3: Add the conditional SME-norm check after the seven signals**

Replace:
```
A leaked secret in the diff is a leaked secret in production; treat sensitive-zone findings with that seriousness.

## How to scan
```
with:
```
A leaked secret in the diff is a leaked secret in production; treat sensitive-zone findings with that seriousness.

## Conditional check — SME-norm conformance (only when checklists were handed to you)

When the dispatcher handed you SME Review checklists (input 4), judge the diff against **those checklist items only** — never the packs' Do / Don't / Patterns prose, which is advisory. For each checklist item, scan the diff for a violation. A violation is a finding **unless it is declared**: the implementer may justify a deliberate deviation with an inline `KARTA-SME-OVERRIDE(<pack>: <rule>): <rationale>` marker at the deviation site (the same declared-crossing principle as the seven signals — a declared crossing is justified). So:

- Diff violates a checklist item **and carries no matching `KARTA-SME-OVERRIDE` marker** → **VIOLATION** (undeclared override).
- Diff violates a checklist item **and a matching marker declares it** → justified; pass it.
- No checklist violation → pass.

This check uses the same verdict, cap, and escalation path as the seven signals; an undeclared SME override is just one more boundary crossing. The acceptance-reviewer does not see this — it is yours alone.

## How to scan
```

- [ ] **Step 4: Note the conditional in the rules list**

Replace:
```
- **The seven signals are the rule set.** They are embedded above; there is no per-repo placeholder rule and no fail-closed-on-unconfigured mechanism. The signals are always present.
```
with:
```
- **The seven signals are the base rule set.** They are embedded above; there is no per-repo placeholder rule and no fail-closed-on-unconfigured mechanism. The signals are always present. The SME-norm check is the one conditional addition — active only when the binder pins `sme[]` and the dispatcher handed you the checklists; it judges checklist items only, and a declared `KARTA-SME-OVERRIDE` marker passes.
```

- [ ] **Step 5: Teach karta-verify to resolve and pass the checklists**

In `skills/karta-verify/SKILL.md`, replace:
```
The agent re-runs the seven smart-surfaced-review signals (see `references/smart-surfaced-review.md`) on the actual diff and returns:
```
with:
```
**Resolve SME checklists first (only when the binder pins `sme[]`).** Read the binder's `sme` list. For each id, resolve the pack — the worktree's project overlay `.karta/sme/<id>.md` laid over this skill's built-in [references/sme/](references/sme/) `<id>.md` (project-local wins) — and extract its **Review checklist** section. Hand those checklists to the auditor in its dispatch brief. This is the one input beyond the four that travels to a gate agent, and only when `sme[]` is non-empty: a project-extensible checklist cannot be embedded in the self-contained agent, and built-in packs live in the plugin rather than the worktree, so the dispatcher resolves them. When `sme[]` is empty or absent, hand nothing and the auditor's SME-norm check no-ops.

The agent re-runs the seven smart-surfaced-review signals (see `references/smart-surfaced-review.md`) on the actual diff — plus, when handed SME checklists, the conditional SME-norm check (an undeclared `KARTA-SME-OVERRIDE` is a VIOLATION) — and returns:
```

- [ ] **Step 6: Soften the "only four inputs" lines in karta-verify to admit the scoped exception**

Replace (in Phase 0):
```
1. Confirm a fresh, thin context: only the worktree path, the binder path, the work item id, and the diff range travel to each agent. No build-session state.
```
with:
```
1. Confirm a fresh, thin context: only the worktree path, the binder path, the work item id, and the diff range travel to each agent — plus, for the safety-auditor when the binder pins `sme[]`, the resolved SME Review checklists (see `verify:boundary`). No build-session state.
```

Replace (in the Gotchas):
```
- **Fresh session per dispatch.** Each agent dispatch is a new session with no build-session context. Pass only the four inputs; nothing else travels.
```
with:
```
- **Fresh session per dispatch.** Each agent dispatch is a new session with no build-session context. Pass only the four inputs — the one exception is the safety-auditor's SME Review checklists, resolved here and passed only when the binder pins `sme[]`. No build-session state travels.
```

- [ ] **Step 7: Regenerate the agent projections, then the skill projections, and verify**

Order matters: the bundled `*.agent.md` lives inside the karta-verify skill, so agents-sync runs before skills-sync (per AGENTS.md "After you edit").
```bash
uv run scripts/sync_codex_agents.py
uv run scripts/sync_codex_skills.py
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
```
Expected: both `--check`s report IN SYNC. Confirm the generated bundle carries the new check:
```bash
grep -q "SME-norm conformance" skills/karta-verify/references/karta-safety-auditor.agent.md && echo bundle-ok
grep -q "SME-norm conformance" .codex/agents/karta-safety-auditor.toml && echo toml-ok
```
Expected: `bundle-ok`, `toml-ok`.

- [ ] **Step 8: Commit (canonical agent + verify skill + all regenerated projections)**

```bash
git add agents/karta-safety-auditor.md skills/karta-verify/ .codex/ .agents/ plugins/
git commit -m "feat(sme): safety-auditor flags undeclared overrides; karta-verify passes checklists"
```

---

### Task 7: Docs + full validation pass

**Files:**
- Modify: `AGENTS.md` (layout table)
- Modify: `README.md` (new SME section)

**Interfaces:**
- Consumes: everything above. No new code.
- Produces: contributor + user docs; a clean four-check run.

- [ ] **Step 1: Add the `_shared/sme` row to the AGENTS.md layout table**

In `AGENTS.md`, replace:
```
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
```
with:
```
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
| `skills/_shared/sme/<id>.md` | Built-in SME expert packs — canonical; copied byte-equal into karta-plan/build/verify `references/sme/` | yes |
```

- [ ] **Step 2: Add a short SME section to the README**

In `README.md`, replace:
```
## Cross-cutting
```
with:
```
## Domain experts (SME packs)

karta carries curated **SME packs** — stack-specific do's and don'ts — so planning and implementation follow each stack's norms. At plan time karta auto-matches the packs that fit the repo (from the detected stack and dependencies) and pins their ids in the binder's `sme` field; karta-build loads them to write against. Built-ins ship for `angular` and `python-fastapi`; a project adds or overrides packs by dropping `.karta/sme/<id>.md` in its repo (project-local wins on a name clash).

Each pack's **Review checklist** is the enforceable part. Before commit the build implementer self-checks its diff against it; a deliberate deviation is declared inline with a `KARTA-SME-OVERRIDE(<pack>: <rule>): <reason>` marker. The existing `karta-safety-auditor` then flags any **undeclared** checklist violation as a boundary crossing the item never justified — a kickback, escalating to you at its cap. A declared override passes and is surfaced in the run report. The acceptance gate stays SME-unaware: there is still exactly one acceptance authority, and Do / Don't / Patterns prose never blocks — only the checklist does.

## Cross-cutting
```

- [ ] **Step 3: Regenerate (README/AGENTS are not mirrored, but run the skills sync for safety) and run all four pre-commit checks**

Run:
```bash
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
uv run scripts/check_shared_copies.py --self-test
uv run scripts/validate_plugin.py --self-test
uv run skills/karta-plan/scripts/validate_binder.py --self-test
```
Expected: `IN SYNC`, `IN SYNC`, `SHARED COPIES: IN SYNC`, plugin self-test passes, validator `16/16 checks passed`.

- [ ] **Step 4: End-to-end smoke — a binder that pins `sme[]` validates**

```bash
cat > /tmp/sme-smoke.json <<'JSON'
{ "slug": "sme-smoke", "motivation": "smoke", "scope": { "included": ["x"] },
  "design_facts": { "stack": "Python/FastAPI + Angular SPA" },
  "sme": ["python-fastapi", "angular"],
  "env_contract": { "command": "uv run uvicorn app:app", "supports_isolation": true },
  "work_items": [ { "id": "wi01", "title": "Endpoint", "oracle": { "type": "unit" } } ] }
JSON
uv run skills/karta-plan/scripts/validate_binder.py --binder /tmp/sme-smoke.json
```
Expected: `VALID. 1 work items; 0 opted out of acceptance checks.`

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs(sme): document SME packs in README and AGENTS layout"
```

- [ ] **Step 6: Final review of the whole branch**

```bash
git log --oneline main..HEAD
git diff --stat main..HEAD
```
Expected: the seven feature commits (Tasks 1-7) plus the spec commits, touching only the files named in this plan and their regenerated projections.

---

## Self-Review

**Spec coverage:**
- Pack format + built-ins (spec 3a, 3b) → Task 1.
- Project overlay + byte-equal copies + nested drift check (spec 3b, 4) → Task 2.
- `sme` field, schema, validator (spec 3c, 3f, 4) → Task 3.
- Plan match + guidance + pin + report (spec 3c, 3d) → Task 4.
- Build load + self-check + declared-override markers + report (spec 3e) → Task 5.
- Safety-auditor conditional enforcement + karta-verify checklist resolution (spec 3f) → Task 6.
- Wiring/docs (spec 4) → Tasks 2-7 (regeneration folded into each touching task; docs in Task 7).
- Non-goals (spec 5): no new script (matcher/self-check are prose ✓), no new gate authority (auditor conditional, acceptance-reviewer untouched ✓), no kill switch (empty `sme[]` no-ops ✓), no per-item override (binder-level only ✓).

**Placeholder scan:** every code step shows full content; every command shows expected output. No TBD/TODO.

**Type/name consistency:** field `sme` (array of kebab-case strings); pack ids `angular`, `python-fastapi`; cache var `SME_PACKS`; phase label `plan:sme`; build steps `4c-ter` / `9-sme`; marker `KARTA-SME-OVERRIDE(<pack>: <rule>): <reason>` — used identically across Tasks 3-7. The auditor receives checklists as a conditional 4th input; karta-verify is the sole resolver/dispatcher.

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-21-sme-domain-experts.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
