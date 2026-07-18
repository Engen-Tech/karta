# AGENTS.md — working on karta

karta is a stack-agnostic orchestration framework shipped as **both** a Claude Code plugin and a Codex CLI plugin. It plans a binder of work items, delivers it in parallel waves onto a per-binder integration branch, builds each item in an isolated git worktree, and gates each one against its own acceptance check. This file orients an agent editing karta itself; end-user usage lives in `README.md` and `docs/how-to/codex.md`.

## Layout — canonical vs generated

Some files are hand-edited (canonical); others are generated projections you must never hand-edit. Edit the canonical, then run the generator.

| Path | Role | Edit? |
|-|-|-|
| `skills/<name>/` | Skills — canonical, Claude-native | yes |
| `.agents/skills/<name>/` | Codex repo-local skill mirror — generated, byte-identical | no — run `sync_codex_skills.py` |
| `agents/<name>.md` | Agents — canonical (Claude registered subagents). Two read-only gates + two writers: `karta-doc-gardner` (docs) and `karta-kaizen` (stack packs) | yes |
| `.codex/agents/<name>.toml` | Codex registered subagent — generated. `sandbox_mode` is derived from the agent's `tools` (Write/Edit → workspace-write; else read-only) | no — run `sync_codex_agents.py` |
| `skills/<spawn-site>/references/<name>.agent.md` | Agent instructions bundled in the agent's sole spawn-site skill (Codex plugin-install fallback) — generated. Gates → `karta-verify`; gardner → `karta-doc-gardner`; kaizen → `karta-kaizen` (see `BUNDLE_SITE` in `sync_codex_agents.py`) | no — run `sync_codex_agents.py` |
| `plugins/karta/` | Codex marketplace install projection — generated real directory. The marketplace points here (`./plugins/karta`) because Codex CLI expects plugin entries under a child path, and real files work on Windows/macOS/Linux | no — run `sync_codex_skills.py` |
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
| `skills/_shared/sme/<id>.md` | Built-in stack packs — stack (`match`) + rule (`always: true`); canonical, copied byte-equal into karta-plan/build/verify `references/sme/` | yes |
| `skills/_shared/sme/platform-native.md` | Shared reference data the packs link to via `see_also` — not a pack | yes |
| `skills/<name>/references/<f>.md` | Per-skill copy of a `_shared` file | no — keep byte-equal |
| `.claude-plugin/` | Claude plugin + marketplace manifests | yes |
| `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Codex plugin + repo marketplace manifests | yes (keep name/version in step with `.claude-plugin/plugin.json`) |

Why committed mirrors and not symlinks: Codex does not detect symlinked skills on Windows (openai/codex#8400), so `.agents/skills/` and the marketplace install projection under `plugins/karta/` are real directories kept in sync by the generator and guarded by the validator.

Externally managed cross-runtime skills are the exception to `.agents/skills/` ownership. A skill with a complete entry in `skills-lock.json` may be committed under `.agents/skills/` alongside its `.claude/skills/` and `.pi/skills/` copies. The generator preserves these locked external skills, does not compare them with `skills/`, and never ships them in `plugins/karta/`.

## After you edit

- Edited a skill (including its `references/`, `scripts/`, or `agents/openai.yaml`): run `uv run scripts/sync_codex_skills.py`.
- Edited an agent (`agents/*.md`): run `uv run scripts/sync_codex_agents.py`, then `uv run scripts/sync_codex_skills.py` (the bundled `*.agent.md` lives inside the agent's spawn-site skill, so that mirror changes too).
- Edited a `skills/_shared/*.md`: copy it into each consuming skill's `references/` (keep them byte-equal), then run the skills mirror.

## Before you commit

All four must be clean:

```
uv run scripts/validate_plugin.py --self-test
uv run scripts/check_shared_copies.py --self-test
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
```

The validator also runs the two `--check` paths itself, so a green `validate_plugin.py` already implies the projections are in sync; the explicit `--check` calls are here for a faster signal while iterating.

## Roundtable edict (house-only)

karta's own binders and deliveries may not proceed without a recorded multi-model review. This is a house rule for the karta repo building itself — consumer repos never carry it. It matches karta's standing doctrine: enforced checks over skippable prose.

The gate is deterministic. It enforces one fact — *a fresh recorded review of this exact content exists* — never the panel's verdict. The review is nondeterministic (different models, different runs), so disagreeing with the findings never blocks; skipping the review is what blocks. You still read the findings and decide what to act on.

### The four points

| Point | Git event | Treatment |
|-|-|-|
| Plan (binder) | commit staging `.karta/binders/<slug>.json` | enforced edict |
| Deliver (integration branch) | `git merge` landing a `karta/*/integration` branch on the default branch | enforced edict |
| Verify (a built diff) | none | helper-available (advisory) |
| Standalone (ad hoc) | none | helper-available (advisory) |

Plan-commit and deliver-merge have a real commit to block, so they are gated. Verify and standalone have no commit or stop moment to hang an edict on, so they get the same one-command helper with no hard gate.

### Run the panel, then record it

The tool per point is configured in `.karta/roundtable.json` (default `roundtable-critique`). A script cannot run roundtable — it is an MCP tool the agent calls. So the flow is two steps:

1. Run the roundtable panel on the target (the staged binder, or the integration-branch diff).
2. Pipe the panel result to the recorder: `... | python3 scripts/roundtable/run_review.py --record --target <slug-or-branch>`.

The gate then confirms the record with `run_review.py --check`. The `min_providers` floor keeps "multi-model" honest: a panel with fewer than `min_providers` distinct providers is not a review, and the recorder refuses to file it.

### Rules the gate enforces

- **Records must be committed.** The recorder stages the record under `.karta/roundtable/`, and the binder-commit gate requires it to be in the same commit (staged, or already in `HEAD`). A record that lives only in the working tree does not satisfy the gate — `.karta/roundtable/` is the committed audit trail.
- **Binder freshness keys on the staged blob.** The binder gate hashes the *staged* bytes (`git show :<path>`), not the working-tree file. Review one version of the binder and stage a different one, and the gate re-arms — you must re-review what you are actually committing.
- **The merge gate is narrow.** It fires only for a `git merge` naming a `karta/*/integration` branch while you are on the default branch. Nothing else trips it.

### Accepted bypasses

A PreToolUse hook sees a command before it runs, so it can only match command text and read current git state — it cannot judge a post-condition like "will this make the integration tip an ancestor." So these paths are **not** gated, by design, and are the same class of deliberate escape as the hatch below: landing integration content via `git cherry-pick`, `git rebase`, or `git reset --hard`; and a `git merge --squash` followed by a separate `git commit`. The doctrine names them plainly rather than pretending the gate is airtight.

### Escape hatch

When the roundtable environment is down, or you need a deliberate partial commit, set `KARTA_SKIP_ROUNDTABLE=1` — in the command text or the environment — and the gate allows the command. The hook also fails open on any internal error: a broken hook never wedges the repo.

Full operator guide: [docs/how-to/roundtable.md](docs/how-to/roundtable.md).

## Kaizen dogfood policy (this repo)

Kaizen is enabled here (`.karta/kaizen.json`) under a scoped policy, because this repo authors the built-in packs:

- `.karta/sme/minimalism.md` is a **managed shadow** of `skills/_shared/sme/minimalism.md` and must stay byte-identical (`validate_plugin.py` enforces this). When kaizen — or anyone — edits the shadow, the change is either discarded or promoted upstream into the canonical pack (then re-copied); it never drifts. Editing the canonical pack means re-copying it to the shadow in the same change.
- `.karta/sme/karta-house-skill-authoring.md` is this repo's own non-coding pack (reserved `karta-house-*` namespace, so it can never collide with a built-in). It is the pack kaizen is expected to actually evolve; its edits are reviewed like any `kaizen:` commit.
- Never seed further built-ins here; deliveries pin what their binders pin.

## Two platforms, one behavior

The gate agents are read-only on every install. On Claude Code and on Codex-with-`.codex/agents/`, they run as registered subagents (`sandbox_mode = "read-only"`). On a Codex plugin install — where plugins cannot register subagents — `karta-verify` spawns a read-only subagent using the bundled `references/*.agent.md`. Keep that adaptive dispatch intact when editing `skills/karta-verify/SKILL.md`.
