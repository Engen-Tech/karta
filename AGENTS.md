# AGENTS.md — working on karta

karta is a stack-agnostic orchestration framework shipped as **both** a Claude Code plugin and a Codex CLI plugin. It plans a binder of work items, delivers it in parallel waves onto a per-binder integration branch, builds each item in an isolated git worktree, and gates each one against its own acceptance check. This file orients an agent editing karta itself; end-user usage lives in `README.md` and `docs/how-to/codex.md`.

## Layout — canonical vs generated

Some files are hand-edited (canonical); others are generated projections you must never hand-edit. Edit the canonical, then run the generator.

| Path | Role | Edit? |
|-|-|-|
| `skills/<name>/` | Skills — canonical, Claude-native | yes |
| `.agents/skills/<name>/` | Codex repo-local skill mirror — generated, byte-identical | no — run `sync_codex_skills.py` |
| `agents/<name>.md` | Gate agents — canonical (Claude registered subagents) | yes |
| `.codex/agents/<name>.toml` | Codex registered subagent — generated | no — run `sync_codex_agents.py` |
| `skills/karta-verify/references/<name>.agent.md` | Gate instructions bundled in the skill (Codex plugin-install fallback) — generated | no — run `sync_codex_agents.py` |
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
| `skills/<name>/references/<f>.md` | Per-skill copy of a `_shared` file | no — keep byte-equal |
| `.claude-plugin/` | Claude plugin + marketplace manifests | yes |
| `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Codex plugin + repo marketplace manifests | yes (keep name/version in step with `.claude-plugin/plugin.json`) |

Why a committed mirror and not a symlink: Codex does not detect symlinked skills on Windows (openai/codex#8400), so `.agents/skills/` is real directories kept in sync by the generator and guarded by the validator.

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
