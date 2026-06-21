# stack packs — design (v2)

- Date: 2026-06-21
- Status: approved (brainstorming) — pending implementation plan
- Scope: an auto-applied domain-expertise layer for karta. karta ships curated **stack packs** — do's/don'ts that planning and implementation follow; most are matched to a repo's tech, some are always-on — auto-applies the ones that fit, pins them in the binder at plan time, and feeds them to planning and implementation so work is decomposed and written to each pack's norms. Each pack's **Review checklist** is enforceable: the implementer self-checks before commit, and the existing `karta-safety-auditor` flags *undeclared* checklist violations (a declared override with a rationale passes). Ships `angular` + `python-fastapi` (stack packs) and `minimalism` (an always-on stack pack built on a minimalism ladder); projects extend with their own in `.karta/sme/`. No new gate authority. The design also adds debt mechanics (a marker trigger grammar, a no-trigger rot flag, and an on-demand harvest), and a method to measure that a pack earns its place.

## 0. What changed in v2

v1 designed tech-matched stack packs (angular, python-fastapi) — advisory knowledge with an enforceable Review checklist. v2 adds the following:

- **Two ways a stack pack applies.** A stack pack is either **matched** to the detected stack/deps (as v1) or **always-on** (applied to every binder). The first always-on stack pack is `minimalism`, built on a decision ladder. There is **no intensity dial**.
- **A shared "use the platform" reference.** A `platform-native` reference of native/stdlib substitutions (across HTML/CSS/JS/Node/Python/DB) ships as `skills/_shared/sme/platform-native.md`, owned by the `minimalism` pack and referenced by the stack packs — the concrete, diff-checkable slice.
- **Debt-marker mechanics.** The `KARTA-SME-OVERRIDE` marker gains an optional **ceiling + upgrade-trigger** grammar; karta-build's once-surfaced debt register flags any deferral with **no trigger**; a new read-only **`karta-debt`** skill harvests both marker families on demand. None of this adds a tracked backlog.
- **Proof a pack helps.** A documented A/B method + a lean harness under `benchmarks/sme/` to measure a pack's effect (code size, cost, time, and whether the gate still passes), with an honesty rule: never claim a per-repo "you saved X" number.

The enforcement model from v1 is unchanged: only a pack's **Review checklist** has teeth, judged by the one existing boundary gate; the acceptance-reviewer stays stack-pack-unaware.

## 1. Goal and decisive constraints

karta is stack-agnostic: it makes no assumption about framework, library, or layout, and it reads only the binder and the repo at runtime. That neutrality is right for orchestration, but it leaves karta with no opinion about *how* a given stack should be written, or about cross-cutting craft like not over-building. This feature gives karta that voice: the right expert advises during planning and implementation, automatically.

Constraints (all set by the user):

- **Knowledge packs, not subagents.** An expert is curated markdown loaded inline by the existing plan/build flows — not a separately dispatched advisor session.
- **Two kinds, one mechanism.** Stack packs match by detected stack/deps; always-on stack packs apply to every binder. Both are pinned in the binder, both write against the implementer, and both are enforced identically through the Review checklist. The only difference is *selection*.
- **Advisory to write by; only the Review checklist is enforceable.** Do / Don't / Patterns shape how code is written and never block. A checklist deviation passes when declared with a rationale; an undeclared checklist violation is a `karta-safety-auditor` finding. The acceptance-reviewer stays stack-pack-unaware — one acceptance authority.
- **Checklist items must be diff-checkable.** This is the rule that lets an always-on stack pack like `minimalism` be enforced safely: only objectively-checkable items go in the Review checklist (e.g. "no new dependency where the stdlib/platform already ships it"); heuristics ("prefer the simplest thing") stay in the advisory sections. The gate never judges a vibe.
- **No intensity dial.** karta runs the always-on stack packs at one fixed level. A team that wants it stronger or softer edits the pack via the project overlay.
- **Built-in plus project overlay.** karta ships curated built-ins; a project drops its own packs in `.karta/sme/` to cover arbitrary stacks, add a house always-on stack pack, or override a built-in.
- **No tracked backlog.** The debt borrowings stay one-shot and read-only. karta still surfaces debt once and never persists, schedules, or revisits it.
- **Cross-platform, dual-runtime.** Same canonical-vs-generated discipline as the rest of karta: canonical under `skills/` / `agents/`, drift-guarded Codex projections, Claude Code + Codex on macOS/Linux/Windows.

## 2. Relationship to karta's "no registry, stack-agnostic" stance

A curated catalog of experts is, in a sense, the registry karta has avoided. The design resolves it on three points, and v2 holds them even with always-on stack packs added:

- **The catalog is not invariant state.** stack packs carry no run state, nothing a later stage reads back. They are static reference text — the category as the `skills/_shared/*.md` material karta already ships. The binder's `sme[]` is a resolved-at-plan fact, like `design_facts.stack`.
- **It does not narrow the orchestration.** karta's pipeline (plan → deliver → build, gated by verify/validate) is untouched. A stack pack is additive; remove every pack and karta behaves exactly as today.
- **Stack-pack specificity lives only in pack data, never in control flow.** No skill or agent grows a `case "angular"` or `case "minimalism"` branch. The matcher, the build self-check, and the auditor's conformance check are all generic — they read whatever checklist the applied packs carry. An always-on stack pack is selected by a generic "apply to every binder" flag, not a hardcoded behavior.

## 3. The model

### 3a. A stack pack

A markdown knowledge file. Frontmatter declares identity, *kind* (via selection), and match tokens; the body is the advisory content plus the enforceable checklist.

```
skills/_shared/sme/angular.md            (stack pack)
---
name: angular
description: Angular architecture do's and don'ts
match: ["@angular/core", "@angular/cli", "angular"]
see_also: ["platform-native#html-elements", "platform-native#css-capabilities"]
---
## Do … ## Don't … ## Patterns …
## Review checklist
- [ ] No `any` in changed component/service signatures.
- [ ] No date/color/range-picker dependency where an `<input type=…>` covers it.   ← from platform-native
...
```

```
skills/_shared/sme/minimalism.md         (always-on stack pack)
---
name: minimalism
description: Write the least code that works; don't over-build
always: true
see_also: ["platform-native"]
---
## The ladder (advisory) … ## Never simplify away (safety floor) …
## Review checklist
- [ ] No new third-party dependency where the stdlib/platform ships it (name the dep + the native equivalent).
- [ ] No abstraction with a single implementation/caller added speculatively.
- [ ] No new config/flag/option nothing reads.
- [ ] Non-trivial new logic (branch, loop, parser, money/security path) leaves one runnable check.
```

- `name` — the pack id (kebab-case, unique). Lands in `sme[]`; a project-local file of the same name overrides the built-in.
- `description` — one line, shown in the plan report.
- **Selection** — exactly one of:
  - `match: [tokens]` → **stack pack**. Applied when a token equals a detected dependency name or is a case-insensitive substring of the resolved stack phrase.
  - `always: true` → **always-on stack pack**. Applied to every binder, regardless of stack.
- `see_also` — optional links into the shared `platform-native` reference (no copying; see 3g).
- **Review checklist** — the enforceable subset, and the only section with teeth. Authoring rule: **every item must be objectively checkable on a diff.** Do / Don't / Patterns / ladder text stay purely advisory.

### 3b. Where packs live

- **Built-in (canonical):** `skills/_shared/sme/*.md`. Per karta's `_shared` convention, each consumed file is copied byte-equal into the consuming skills' `references/sme/` trees. v1 stack packs: `angular`, `python-fastapi`. v2 adds the `minimalism` always-on stack pack and the shared `platform-native.md` reference.
- **Project-local overlay:** `.karta/sme/*.md` in the user's repo. A new `name` adds an expert (stack or rule) for an unsupported case; a reused `name` overrides the built-in. Project-local wins on a `name` clash. This is the open-ended, fine-tune-as-you-go surface. It sits beside `.karta/binders/`.

### 3c. Selection — auto-applied at plan, pinned in the binder

karta-plan's survey already resolves the stack phrase and reads dependency manifests. After the survey, the matcher runs over the available packs (project-local overlaid on built-in, by `name`):

1. **Always-on stack packs** (`always: true`) are applied to every binder, unconditionally.
2. **Stack packs** (`match`) are applied when their tokens hit the detected deps or stack phrase.

The applied pack ids — stack and rule together — become the binder field `sme`:

```jsonc
{ "design_facts": { "stack": "Python/FastAPI + Angular SPA" },
  "sme": ["minimalism", "python-fastapi", "angular"] }   // an always-on stack pack + two stack packs
```

`sme` stays an optional array of strings. A repo with no stack match still gets the always-on stack packs (e.g. `["minimalism"]`). A project that wants no always-on stack pack overrides it with an empty/no-op `.karta/sme/minimalism.md`.

### 3d. Plan integration

After selection, karta-plan loads the applied packs and threads their guidance into Phase 2 (`plan:synthesize`): the synthesis brief gains a **domain-guidance** section carrying the applied packs' do's/don'ts and the minimalism ladder, so decomposition, `contract`s, and `oracle`s respect each pack's norms (an Angular slice's contract in standalone-component terms; a FastAPI oracle expecting Pydantic shapes; items kept lean per the ladder). The applied ids are written into the binder at Phase 5 (`plan:emit`). The Phase 6 report gains one line: **Experts applied: minimalism (rule), angular, python-fastapi** (or *none*).

### 3e. Build integration

karta-build reads the pinned `sme[]` in Phase 1 (`build:gate`), resolves each pack (project-local → built-in), and loads it **before Phase 4 (`build:implement`)**. The implementer writes against the loaded guidance — the stack packs' patterns and the minimalism ladder — and applies the pack(s) relevant to the area it targets. A pinned pack that can't be resolved at build time produces a **non-fatal note** and the run continues.

**Self-check before commit.** Before committing (Phase 9 `build:merge`), the implementer runs each loaded pack's **Review checklist** against the diff and records the per-pack tally in the report. A deliberate deviation is a **declared override** at the deviation site — an inline marker modelled on karta's `KARTA-DEFER` family:

```
KARTA-SME-OVERRIDE(<pack>: <rule>): <rationale> [ceiling: <limit>; upgrade: <trigger>]
```

`ceiling`/`upgrade` are **optional** — name them when the shortcut is knowingly temporary (where it breaks, and what forces a revisit). A permanent justified exception needs only the rationale. Example:

```js
// KARTA-SME-OVERRIDE(minimalism: no-new-dep): need RFC-5322 edge cases the stdlib misses;
//   ceiling: bundle size if it grows; upgrade: drop when the platform ships a validator
```

The self-check never halts the build. The judgment of declared-vs-undeclared is the gate's (3f).

### 3f. The safety gate enforces undeclared overrides

`karta-safety-auditor` already scans the diff for "crossings the work item never justified" (`PASS | VIOLATION`, 3-attempt cap, human escalation). stack-pack enforcement is **one conditional check** there — active only when `sme[]` is non-empty.

- **What it judges:** the applied packs' **Review checklist** items against the diff — nothing from Do / Don't / Patterns / ladder. A checklist violation the diff declares with a `KARTA-SME-OVERRIDE` marker is a justified crossing → contributes to `PASS`. An **undeclared** checklist violation → `VIOLATION` → kickback; unresolved at the cap → escalate. The implementer clears it by *fixing the code* or *declaring the override* — never by suppressing the check.
- **How the packs reach the auditor:** `karta-verify` is the sole dispatcher (karta-build runs its acceptance check through karta-verify). karta-verify resolves `sme[]` against its own `references/sme/` (built-in) overlaid by the worktree's `.karta/sme/` (project-local) and includes the resolved Review checklists in the auditor's dispatch — a deliberate, `sme[]`-scoped exception to the "only the four inputs travel" rule (a project-extensible checklist cannot be embedded in the self-contained agent).
- **The acceptance-reviewer stays stack-pack-unaware.** One acceptance authority; the boundary gate gains one conditional check.
- `validate_binder.py` validates `sme` as an optional array of strings (schema only); it does not require packs to resolve.

### 3g. The minimalism always-on stack pack and the shared platform-native reference

- **`skills/_shared/sme/minimalism.md`** (always-on stack pack, `always: true`):
  - *Advisory:* the ladder — does this need to exist (YAGNI) → stdlib → native platform feature → already-installed dependency → one line → the minimum that works; no unrequested abstractions; deletion over addition; shortest working diff.
  - *Safety floor ("never simplify away"):* validation at trust boundaries, error handling that prevents data loss, security, accessibility, anything explicitly requested, hardware calibration knobs. This guards the pack against advising away safety.
  - *Review checklist (enforceable, concrete):* the four diff-checkable items in 3a.
- **`skills/_shared/sme/platform-native.md`** — a `platform-native` reference of native/stdlib substitutions (HTML elements, CSS, JS/browser, Node stdlib, Python stdlib, DB). It is *reference data*, not a pack (no `name`/`match`/`always`): the `minimalism` pack and the stack packs link to the relevant slice via `see_also`, so the concrete substitutions live **once** and the packs point at them (reference, don't copy — the same anti-staleness rule karta's `_shared` convention already follows). A stack pack's Review checklist may name a specific high-value substitution as a concrete item (Angular: native form inputs; python-fastapi: stdlib over thin wrappers).

### 3h. Debt markers, the rot flag, and the on-demand harvest

- **Marker grammar (borrow).** `KARTA-SME-OVERRIDE(<pack>: <rule>): <rationale> [ceiling: <limit>; upgrade: <trigger>]` (3e). The optional `ceiling`/`upgrade` is recommended for `KARTA-DEFER` too (its existing `follow-up:` is the trigger). Some overrides are permanent exceptions, so the trigger is never required on an override.
- **No-trigger rot flag (borrow).** karta-build's once-surfaced **debt register** (Phase 10 report) flags any `KARTA-DEFER` marker missing its `follow-up:` trigger as `no-trigger` — the deferral that silently rots. Ends with `<N> markers, <M> no-trigger`. This is a read of what's already scanned; no new state. The grep one-liner (`grep -rnE 'KARTA-DEFER|KARTA-SME-OVERRIDE'`) is documented for ad-hoc use.
- **On-demand harvest skill (borrow).** A new read-only skill **`karta-debt`**: on request, grep both marker families repo-wide, group by file, flag `no-trigger` rows, and print a one-shot ledger — `<file>:<line>, <what>. ceiling: <…>. upgrade: <…>.` ending `<N> markers, <M> no-trigger`. It **writes nothing and tracks nothing** — a report, not a backlog, consistent with karta's once-surfaced/no-backlog rule. It is the consolidated, repo-wide companion to the build report's per-run register.

### 3i. Proving a pack helps (measurement)

A lean A/B method + harness under `benchmarks/sme/`:

- **Method:** for a fixed task and a target pack, run the build twice — pack applied vs pack absent — and compare: lines of code in the diff, tokens/cost/time when the host exposes them, and **whether the acceptance gate still passes** (the safety axis — a pack must not cut correctness to cut code). Report medians over a small `n`.
- **Honesty rule:** the harness reports the **A/B delta on benchmark tasks**, never a per-repo "you saved X here" number — the unbuilt version was never written, so there is no live baseline to subtract from.
- **Shape:** `benchmarks/sme/README.md` (method + honesty rule), a `fixtures/` dir of 2–3 small tasks, and a runner that drives the build both ways and tabulates the result. Full push-button automation depends on a headless build path; V1 ships the method + a semi-automated runner. This is a **validation tool, not a runtime feature** — it can land after the packs.

## 4. Wiring (canonical-vs-generated discipline)

- New canonical content: `skills/_shared/sme/{angular,python-fastapi}.md` (stack, v1), `skills/_shared/sme/minimalism.md` (rule), `skills/_shared/sme/platform-native.md` (shared reference). Each pack carries a concrete Review checklist.
- Per-consumer byte-equal copies under `references/sme/` for the three pack consumers: `karta-plan` (match + guidance), `karta-build` (implement + self-check), `karta-verify` (resolve checklists for the auditor). `check_shared_copies.py` is generalized to compare nested `_shared` subdirs (path-relative keying), backward-compatible with the flat copies.
- `sync_codex_skills.py` regenerates the `.agents/skills/` mirror and the `plugins/karta/` projection (it already recurses); `validate_plugin.py` covers them.
- `agents/karta-safety-auditor.md` gains the conditional **stack-pack conformance** check (`sme[]`-gated; declared vs undeclared); `sync_codex_agents.py` regenerates its `.codex/agents/*.toml` and the bundled `references/karta-safety-auditor.agent.md`.
- New skill **`karta-debt`**: `skills/karta-debt/SKILL.md`, registered in `.claude-plugin/marketplace.json` and the Codex manifests, mirrored by the sync scripts. Read-only.
- SKILL.md edits: `karta-plan` (matcher after `plan:survey` incl. rule-pack always-apply, domain-guidance, pin, report); `karta-build` (read `sme[]`, load packs, self-check + override markers, report tally, no-trigger rot flag in the debt register); `karta-verify` (resolve `sme[]`, pass checklists into the auditor dispatch).
- `binder-reference.md` + `binder-schema.json` + `validate_binder.py`: the optional `sme` string array.
- New: `benchmarks/sme/` (README + fixtures + runner).
- `AGENTS.md` (layout rows for `skills/_shared/sme/`, the new skill, the auditor change) and `README.md` (stack-packs section covering both pack kinds, the minimalism pack, `karta-debt`, and the measurement method).

## 5. Non-goals (YAGNI)

- No advisor subagent sessions — packs are inline context for the writer and a resolved checklist for the auditor.
- **No new gate authority** — the acceptance-reviewer stays stack-pack-unaware; enforcement rides the existing safety-auditor as one conditional check, scoped to the Review checklist, and any deviation is passable by declaring a rationale.
- **No style-nit blocking** — only *undeclared* violations of concrete *checklist* rules block; advisory prose and the ladder never block. Checklist items must be diff-checkable by construction.
- **No intensity dial** — a team re-calibrates by editing the pack via overlay.
- **No tracked/persisted backlog** — the rot flag and `karta-debt` are one-shot and read-only; karta still never persists, schedules, or revisits debt.
- No kill switch beyond the overlay — no stack match still applies always-on stack packs; a project silences an always-on stack pack by overriding it with a no-op overlay file.
- No per-item `sme[]` override — packs apply by detected stack/area or always.
- No pack versioning/registry beyond the flat `sme/` directory; no automated authoring of project-local packs.
- Measurement is a validation tool, not a runtime feature; full push-button automation is out of V1 scope.

## 6. Build sequence (outline for the plan)

1. Author the built-in packs — stack (`angular`, `python-fastapi`) with concrete Review checklists, the `minimalism` always-on stack pack, and the shared `platform-native.md` reference. Generalize `check_shared_copies.py` for nested subdirs; add the byte-equal copies into plan/build/verify.
2. Add the `sme` field to `binder-schema.json`, `binder-reference.md`, and the `validate_binder.py` self-test.
3. karta-plan: matcher after `plan:survey` (always-on stack packs apply to every binder; matched stack packs by token), domain-guidance in synthesis, pin in `plan:emit`, report line.
4. karta-build: read `sme[]`, resolve + load packs before implement, self-check + `KARTA-SME-OVERRIDE` markers (with optional ceiling/upgrade) before commit, report tally + the no-trigger rot flag in the debt register.
5. karta-safety-auditor: the conditional stack-pack conformance check; karta-verify resolves + passes the checklists.
6. New `karta-debt` skill: read-only repo-wide harvest of both marker families, grouped, rot-flagged, writes nothing; register it in the manifests.
7. Regenerate Codex projections (`sync_codex_agents.py`, then `sync_codex_skills.py`); update `AGENTS.md` and `README.md`.
8. `benchmarks/sme/`: the A/B method (README + honesty rule), fixtures, and a semi-automated runner.
9. Run the four pre-commit checks clean (`validate_plugin.py --self-test`, `check_shared_copies.py --self-test`, both `--check` syncs).

## 7. Open questions

None blocking. Naming: `sme` is the binder/dir token (settled in v1); an always-on stack pack is selected by `always: true`. The measurement harness's degree of automation is bounded by whether a headless build path exists — V1 ships the method + a semi-automated runner and can deepen later.
