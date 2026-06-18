# Codex CLI compatibility — design

- Date: 2026-06-18
- Status: approved (brainstorming) — pending implementation plan
- Scope: make every karta artifact a valid, correctly-interpreted Codex CLI artifact, distributable as a plugin **and** discoverable in a bare clone, on macOS/Linux/Windows, with zero Claude Code regressions.

## 1. Goal and constraints

karta today is a Claude Code plugin: skills under `skills/`, two read-only gate agents under `agents/`, a Claude plugin manifest under `.claude-plugin/`. A partial Codex layer already exists (`.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`). This work completes and corrects the Codex layer.

Constraints (all confirmed with the user):

- **Dual-platform, no Claude regressions.** `.claude-plugin/`, `skills/`, and `agents/*.md` keep working exactly as today. Codex artifacts are added alongside.
- **Both distribution modes.** Installable as a Codex plugin via the repo marketplace, *and* auto-discovered when someone clones the repo and runs `codex` in it.
- **Cross-platform: macOS, Linux, Windows.** This rules out symlinks for skill discovery (see §3).
- **Full compliance + polish.** Agents→TOML, repo-local discovery, per-skill `agents/openai.yaml`, repo `AGENTS.md`, an extended validator that guards the Codex artifacts, and Codex docs.
- **One source of truth per artifact.** Every Codex projection is generated from a canonical file and guarded against drift, matching the repo's existing `check_shared_copies.py` idiom.

## 2. How Codex interprets these artifacts (verified)

From the OpenAI Codex docs and source (developers.openai.com/codex/{skills,subagents,plugins,plugins/build,mcp}; `openai/codex` `core-skills/src/loader.rs`):

- **Skills.** A skill is a directory with `SKILL.md` whose YAML frontmatter must include `name` and `description`. karta's SKILL.md files already satisfy this. Codex loads name+description+path first (progressive disclosure) and the full body on selection. Activation is explicit (`$skill-name`, `/skills`) or implicit (description match).
- **Repo-local skill discovery.** Codex scans `.agents/skills/` in every directory from the cwd up to the repo root. Skills live at `.agents/skills/<name>/SKILL.md`. (karta keeps canonical skills at `skills/`, so a bare clone finds nothing without a mirror.)
- **Per-skill metadata (optional).** `<skill-dir>/agents/openai.yaml` sets `interface` (display_name, short_description, icons, brand_color, default_prompt), `policy.allow_implicit_invocation`, and `dependencies.tools`. The loader fails open on a missing or malformed file.
- **Subagents.** Custom agents are **TOML** files at `.codex/agents/<name>.toml` (project) or `~/.codex/agents/` (personal). Required keys: `name`, `description`, `developer_instructions`. Optional: `model`, `model_reasoning_effort`, `sandbox_mode`, `mcp_servers`, `skills.config`, `nickname_candidates`. A read-only gate is expressed as `sandbox_mode = "read-only"`. Codex also ships built-in read-only agents (`explorer`). **Plugins cannot bundle subagents** — the plugin manifest points only at skills/mcp/apps/hooks. So a *registered* Codex agent must be project-scoped TOML, which does not travel in a plugin install. The skill bundle is the only plugin component that auto-travels on **both** runtimes, so karta ships the gate-agent instructions *inside the dispatching skill* (`karta-verify`) and dispatches adaptively — this makes the gate run automatically on a Codex plugin install with no manual step (§4b, §4d, §6).
- **Plugin manifest.** `.codex-plugin/plugin.json` with `name`/`version`/`description`, optional metadata, and component pointers (`skills`, `mcpServers`, `apps`, `hooks`) plus an `interface` block. karta's existing file matches this schema.
- **Repo marketplace.** `$REPO/.agents/plugins/marketplace.json` with top-level `name`, `interface.displayName`, and a `plugins[]` array whose entries carry `name`, `source` (`{source:"local", path:"./"}`), `policy` (`installation`, `authentication`), and `category`. karta's existing file matches this schema.
- **AGENTS.md.** Project instructions Codex reads from repo root down to cwd. Used here to orient contributors working *on* karta.

## 3. The cross-platform decision: no symlinks, committed real-dir mirror

A symlinked `.agents/skills/` is **broken on Windows** (openai/codex#8400: "skills not detected on Windows with symlink or junctions … copied as a real folder and be detected"), with a trail of related symlink-resolution bugs on macOS/Linux (#11314, #9898, #19719). Codex itself notes a real folder is detected where a symlink is not.

Therefore repo-local discovery uses a **committed real-directory mirror**:

- Canonical skills stay at `skills/<name>/` (Claude-native, untouched).
- A cross-platform `uv` generator copies each skill tree verbatim into `.agents/skills/<name>/` (real files, committed).
- The validator fails the build on any drift between `skills/<name>/` and `.agents/skills/<name>/`, and on any extra/missing mirror file.

This delivers true zero-step clone-and-run on all three OSes with zero Claude risk. The cost — duplicated skill files in the repo — is neutralized for correctness by the drift guard (the same trade the repo already accepts for `skills/_shared/` copies). The mirror is generated, never hand-edited.

Everything else in the design is plain files or `uv` scripts and is cross-platform as-is. The secret scanner is already written to avoid `grep`/`find`/WSL assumptions; the validator parses TOML with stdlib `tomllib` (Python ≥3.11).

## 4. Changes

### 4a. Repo-local skill mirror — `.agents/skills/` + generator

- New `scripts/sync_codex_skills.py` (PEP 723, stdlib-only, `uv run`). Mirrors `skills/<name>/` → `.agents/skills/<name>/` for every dir containing `SKILL.md` (excludes `skills/_shared/`, which has no SKILL.md). It removes mirror entries with no canonical source, then copies every canonical file byte-for-byte (including each skill's `references/`, `scripts/`, and `agents/openai.yaml`). Idempotent. `--check` mode reports drift without writing (used by the validator/CI).
- New committed tree `.agents/skills/<name>/…` for all six skills (karta-plan, karta-deliver, karta-build, karta-verify, karta-validate, karta-plainlanguage), produced by the generator.
- `.agents/` now holds both `plugins/` (marketplace) and `skills/` (mirror); no collision.

Note: a developer who *both* installs the karta plugin *and* runs Codex inside the karta repo may see each skill twice in selectors (Codex does not de-duplicate by name across sources). This is an accepted edge case, documented in `docs/how-to/codex.md`.

### 4b. Subagents — two projections from one canonical, both generated

Canonical stays `agents/karta-<name>.md` (frontmatter + body) — the Claude registered agent, unchanged in place. A new `scripts/sync_codex_agents.py` (PEP 723, stdlib-only, `--check` mode) emits **two** projections from it:

1. **Registered Codex agent** — `.codex/agents/karta-acceptance-reviewer.toml`, `.codex/agents/karta-safety-auditor.toml`:
   - `name` = md frontmatter `name`; `description` = md frontmatter `description`.
   - `sandbox_mode = "read-only"` (the Codex expression of the md's `tools: Read, Glob, Grep, Bash` read-only surface).
   - `developer_instructions` = the md body (everything after frontmatter), as a TOML multi-line **literal** string (`'''…'''`) so no markdown character is reinterpreted as an escape. No `model` key — inherits the parent session model (portable across Codex model names; the Claude side keeps `model: opus`).
   - Used for repo-local Codex and any project that has these TOMLs present, giving **sandbox-enforced** read-only.

2. **Skill-bundled agent instructions** — `skills/karta-verify/references/karta-acceptance-reviewer.agent.md`, `skills/karta-verify/references/karta-safety-auditor.agent.md`: the md **body** only (no frontmatter). These ride inside the `karta-verify` skill bundle, so they travel automatically on a plugin install on both runtimes. They are what makes the Codex-plugin-install gate work with no manual step (§4d). `karta-verify` is the sole spawn site for both agents (karta-build/deliver/validate delegate the gate to it); if implementation finds any other spawn site, the same two reference files are bundled there too.

Both projections are byte-checked against the canonical `agents/*.md` by the validator (§4h). The `.agents/skills/` mirror generator then propagates the bundled reference files into `.agents/skills/karta-verify/references/`.

### 4c. Agent-body wording neutralization (light, semantics-preserving)

In the canonical `agents/*.md`, replace runtime-flavored tool phrasing so both runtimes read cleanly:

- "Read it with the `Read` tool" → "read it".
- "Use `git diff <range>` (via `Bash`)" → "run `git diff <range>` in the shell".
- "run the project's type-check via `Bash`" → "run the project's type-check in the shell".
- "read-only `git` and grep via `Bash`" → "read-only `git` and text search".

These are wording-only; no instruction, gate, verdict, cap, or input changes. The Claude agents keep identical behavior; the change also improves the Claude reading.

### 4d. Adaptive dispatch in karta-verify (the automatic mechanism)

The two dispatch steps in `skills/karta-verify/SKILL.md` (acceptance, boundary) are rewritten to dispatch each gate as a fresh read-only subagent with exactly the four inputs (worktree path, binder path, work item id, diff range), resolving the agent in whatever way the current runtime supports — so the gate runs automatically everywhere karta is installed, with no manual setup:

- **Claude Code** (plugin or repo) — the plugin bundles the agent under `agents/`; dispatch the registered `karta-acceptance-reviewer` / `karta-safety-auditor` subagent by name (opus, read-only — unchanged behavior).
- **Codex with the agent registered** (repo-local, or a project that has `.codex/agents/*.toml`) — spawn the registered subagent; `sandbox_mode = "read-only"` is enforced.
- **Codex plugin install** (no registered agent) — spawn a fresh read-only subagent (e.g. Codex's built-in read-only `explorer`) and use the instructions bundled with this skill at `references/karta-<name>.agent.md`. Read-only is guaranteed behaviorally by the agent's own instructions and, when an `explorer`-style read-only agent is used, by the sandbox too.

In every branch the agent reads the binder and diff itself; only the four inputs are passed. Elsewhere, any skill/reference text that names an agent by the `agents/<name>` path form is changed to the bare `name` (both runtimes resolve it). Implementation greps `agents/karta-acceptance-reviewer|agents/karta-safety-auditor` across `skills/`, fixes all hits, then re-runs both generators.

### 4e. Per-skill `agents/openai.yaml` (polish)

One lean file per skill at `skills/<name>/agents/openai.yaml`:

```yaml
interface:
  display_name: "<skill name>"
  short_description: "<=125-char tagline>"
policy:
  allow_implicit_invocation: true
```

`display_name`/`short_description` give the Codex app a clean presentation; `allow_implicit_invocation: true` (the default, stated explicitly) keeps phrase-triggered activation. No `dependencies.tools` is declared — karta's skills use bundled `uv` scripts and git, not MCP servers, so declaring none is the honest choice. These files travel with both the plugin bundle and the `.agents/skills/` mirror.

### 4f. `AGENTS.md` at repo root

A short contributor-facing file: one-line "what karta is"; the dual-platform layout map (`skills/` canonical, `.agents/skills/` generated mirror — never hand-edit; `agents/*.md` canonical, `.codex/agents/*.toml` generated; `.claude-plugin/` vs `.codex-plugin/` + `.agents/plugins/`); and the generate-and-guard workflow (after editing a skill or agent, run `uv run scripts/sync_codex_skills.py` and `uv run scripts/sync_codex_agents.py`; before commit, run `uv run scripts/validate_plugin.py --self-test` and `uv run scripts/check_shared_copies.py --self-test`). It carries no end-user usage and no Claude-only content.

### 4g. Plugin + marketplace manifest verification

`.codex-plugin/plugin.json` and `.agents/plugins/marketplace.json` already match Codex's schemas; this work adds validator assertions (§4h) rather than rewriting them. The validator pins cross-manifest consistency so they can't silently drift from `.claude-plugin/plugin.json`.

### 4h. Validator extension — `scripts/validate_plugin.py`

Add Codex checks to the existing integrity run (still stdlib-only; uses `tomllib`):

- `.codex-plugin/plugin.json`: valid JSON; `name`/`version`/`description` present; `name` and `version` equal `.claude-plugin/plugin.json`; `skills` resolves to an existing dir; required `interface` keys present.
- `.agents/plugins/marketplace.json`: valid JSON; top-level `name` and `interface.displayName`; each `plugins[]` entry has `name`, `source.{source,path}`, `policy.{installation,authentication}`, `category`; the plugin `name` matches the manifest.
- `.agents/skills/` mirror parity: every `skills/<name>/` (with SKILL.md) has a `.agents/skills/<name>/` whose file set and bytes match exactly; no orphan mirror dirs/files; `_shared/` is not mirrored. (Implemented by calling the generator's `--check`.)
- `.codex/agents/<name>.toml`: for each `agents/<name>.md`, the TOML parses; `name`/`description` equal the md frontmatter; `developer_instructions` equals the md body; `sandbox_mode == "read-only"`.
- `skills/karta-verify/references/karta-<name>.agent.md`: present for both gate agents and byte-equal to the corresponding `agents/<name>.md` body (the skill-bundled projection that powers the Codex-plugin-install fallback).
- `skills/<name>/agents/openai.yaml`: present for every skill and declares `interface.display_name` (light line-based check; Codex itself fails open on this file).

### 4i. Docs

- README: a "Use with Codex CLI" section — install via the repo marketplace; clone-and-run (skills auto-discovered from `.agents/skills/`); the gate runs automatically (the bundled-instructions fallback, no setup); invocation (`$karta-plan` explicit, implicit by description, `@karta` for the plugin).
- New `docs/how-to/codex.md`: the fuller how-to — install paths, repo-local usage, the automatic gate and its one read-only-enforcement nuance (§6), restart-to-reload behavior, the Windows note (real dirs, not symlinks), and the duplicate-listing edge case.

## 5. Source-of-truth map (each projection drift-guarded)

| Logical artifact | Canonical | Generated projection | Generator | Guard |
|-|-|-|-|-|
| Skill content | `skills/<name>/` | `.agents/skills/<name>/` | `sync_codex_skills.py` | validator parity (byte-equal, no orphans) |
| Agent → Codex agent | `agents/<name>.md` | `.codex/agents/<name>.toml` | `sync_codex_agents.py` | validator field+body equality |
| Agent → skill bundle | `agents/<name>.md` body | `skills/karta-verify/references/<name>.agent.md` | `sync_codex_agents.py` | validator body equality |
| Plugin identity | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` | hand-maintained | validator name/version equality |

## 6. The gate runs automatically on every install (no manual step)

Codex plugins bundle skills but not subagents (platform limitation), so a Codex plugin install cannot register a named `karta-*` agent. karta closes this gap by shipping the gate-agent instructions **inside the `karta-verify` skill bundle** (§4b projection 2) and dispatching **adaptively** (§4d). Because the skill bundle travels automatically on a plugin install on both runtimes, the gate works with zero setup:

| Install | Agent source at runtime | Read-only enforcement |
|-|-|-|
| Claude Code (plugin or repo) | registered `agents/*.md`, dispatched by name | sandbox + tools allowlist (unchanged) |
| Codex repo / `.codex/agents/*.toml` present | registered `.codex/agents/*.toml` | sandbox-enforced (`sandbox_mode`) |
| Codex plugin install | bundled `references/karta-*.agent.md`, spawned as a read-only subagent | behavioral (agent instructions) + sandbox when an `explorer`-style read-only agent is used |

No user copies any file. The only cross-install difference is the strength of read-only enforcement on a Codex plugin install (behavioral, plus sandbox when an `explorer`-style agent is spawned) versus sandbox-enforced when a registered TOML is present — both keep the gate read-only; one is defense-in-depth stronger. This nuance is the one thing `docs/how-to/codex.md` notes; nothing is required of the user.

## 7. Verification

- `uv run scripts/validate_plugin.py --self-test` → PASS (now also covering all Codex artifacts).
- `uv run scripts/check_shared_copies.py --self-test` → IN SYNC.
- `uv run scripts/sync_codex_skills.py --check` and `uv run scripts/sync_codex_agents.py --check` → no drift.
- TOML files parse under `tomllib`; `openai.yaml` files declare `display_name`.
- Manual (optional, out of automated scope): in a Codex CLI checkout, `/skills` lists the six karta skills and `$karta-plan` activates one; the two `.codex/agents/*.toml` spawn as read-only subagents.

## 8. Non-goals

No CLAUDE.md changes. The gate's flow, caps, verdicts, inputs, and read-only guarantee are unchanged — only karta-verify's *agent-resolution* step becomes runtime-adaptive (§4d); the orchestration logic itself is not rewritten. No symlink-based mirror (and so no Windows symlink workaround). Models stay unpinned on the Codex agents. No new MCP servers, apps, or hooks (the automatic gate uses the skill bundle, not a hook).

## 9. File inventory

Created:

- `scripts/sync_codex_skills.py`, `scripts/sync_codex_agents.py` (the latter emits both agent projections)
- `.agents/skills/<name>/…` (mirror of all six skills)
- `.codex/agents/karta-acceptance-reviewer.toml`, `.codex/agents/karta-safety-auditor.toml`
- `skills/karta-verify/references/karta-acceptance-reviewer.agent.md`, `skills/karta-verify/references/karta-safety-auditor.agent.md` (skill-bundled agent instructions; generated)
- `skills/<name>/agents/openai.yaml` (six files)
- `AGENTS.md`
- `docs/how-to/codex.md`

Modified:

- `agents/karta-acceptance-reviewer.md`, `agents/karta-safety-auditor.md` (wording neutralization only)
- `skills/karta-verify/SKILL.md` (+ any other skill text using the `agents/<name>` path form) — dispatch phrasing
- `scripts/validate_plugin.py` (Codex checks)
- `README.md` (Codex section)

Unchanged (guaranteed): `.claude-plugin/*`, all skill orchestration logic, the binder schema and scripts, `agents/*.md` semantics.
