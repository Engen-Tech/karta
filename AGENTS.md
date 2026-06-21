# AGENTS.md — working on karta

karta is a stack-agnostic orchestration framework shipped as **both** a Claude Code plugin and a Codex CLI plugin. It plans a binder of work items, delivers it in parallel waves onto a per-binder integration branch, builds each item in an isolated git worktree, and gates each one against its own acceptance check. This file orients an agent editing karta itself; end-user usage lives in `README.md` and `docs/how-to/codex.md`.

## Layout — canonical vs generated

Some files are hand-edited (canonical); others are generated projections you must never hand-edit. Edit the canonical, then run the generator.

| Path | Role | Edit? |
|-|-|-|
| `skills/<name>/` | Skills — canonical, Claude-native | yes |
| `.agents/skills/<name>/` | Codex repo-local skill mirror — generated, byte-identical | no — run `sync_codex_skills.py` |
| `agents/<name>.md` | Agents — canonical (Claude registered subagents). Two read-only gates + the `karta-doc-gardner` writer | yes |
| `.codex/agents/<name>.toml` | Codex registered subagent — generated. `sandbox_mode` is derived from the agent's `tools` (Write/Edit → workspace-write; else read-only) | no — run `sync_codex_agents.py` |
| `skills/<spawn-site>/references/<name>.agent.md` | Agent instructions bundled in the agent's sole spawn-site skill (Codex plugin-install fallback) — generated. Gates → `karta-verify`; gardner → `karta-doc-gardner` (see `BUNDLE_SITE` in `sync_codex_agents.py`) | no — run `sync_codex_agents.py` |
| `plugins/karta/` | Codex marketplace install projection — generated real directory. The marketplace points here (`./plugins/karta`) because Codex CLI expects plugin entries under a child path, and real files work on Windows/macOS/Linux | no — run `sync_codex_skills.py` |
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
| `skills/_shared/sme/<id>.md` | Built-in stack packs — stack (`match`) + rule (`always: true`); canonical, copied byte-equal into karta-plan/build/verify `references/sme/` | yes |
| `skills/_shared/sme/platform-native.md` | Shared reference data the packs link to via `see_also` — not a pack | yes |
| `skills/<name>/references/<f>.md` | Per-skill copy of a `_shared` file | no — keep byte-equal |
| `.claude-plugin/` | Claude plugin + marketplace manifests | yes |
| `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Codex plugin + repo marketplace manifests | yes (keep name/version in step with `.claude-plugin/plugin.json`) |

Why committed mirrors and not symlinks: Codex does not detect symlinked skills on Windows (openai/codex#8400), so `.agents/skills/` and the marketplace install projection under `plugins/karta/` are real directories kept in sync by the generator and guarded by the validator.

## After you edit

- Edited a skill (including its `references/`, `scripts/`, or `agents/openai.yaml`): run `uv run scripts/sync_codex_skills.py`.
- Edited a gate agent (`agents/*.md`): run `uv run scripts/sync_codex_agents.py`, then `uv run scripts/sync_codex_skills.py` (the bundled `*.agent.md` lives inside karta-verify, so the mirror changes too).
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

## Two platforms, one behavior

The gate agents are read-only on every install. On Claude Code and on Codex-with-`.codex/agents/`, they run as registered subagents (`sandbox_mode = "read-only"`). On a Codex plugin install — where plugins cannot register subagents — `karta-verify` spawns a read-only subagent using the bundled `references/*.agent.md`. Keep that adaptive dispatch intact when editing `skills/karta-verify/SKILL.md`.
