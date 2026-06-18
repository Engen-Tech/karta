# Use karta with Codex CLI

karta runs on Codex CLI with the same skills and gate agents it uses on Claude Code. This guide covers the two ways to get it, how the acceptance gate runs automatically, and the cross-platform notes.

## Two ways to install

### As a plugin (any project)

karta is packaged as a Codex plugin (`.codex-plugin/plugin.json`) published through the repo marketplace (`.agents/plugins/marketplace.json`).

1. In Codex, open the plugin browser: `/plugins`.
2. Add this repository as a marketplace source and install **karta**.
3. The six skills are available immediately — `karta-plan`, `karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`, and `karta-plainlanguage`.

Invoke a skill explicitly with `$karta-plan` (type `$` to mention a skill, or `@karta` to scope to the plugin), or just describe the task and let Codex match a skill by its description.

### Clone and run (repo-local)

Run `codex` from inside a karta checkout. Codex scans `.agents/skills/` from your working directory up to the repo root and discovers all six skills with no install step. The mirror is committed real directories, so this works the same on macOS, Linux, and Windows.

## The acceptance gate runs automatically

karta's behavioral gate (`karta-verify`) dispatches two read-only agents — `karta-acceptance-reviewer` and `karta-safety-auditor`. Codex plugins cannot register subagents, so karta makes the gate work everywhere without any manual setup:

| How karta reached you | Where the gate agent comes from | Read-only enforcement |
|-|-|-|
| Plugin install | instructions bundled inside the `karta-verify` skill (`references/*.agent.md`), spawned as a read-only subagent | behavioral (the agent's own instructions); add the sandbox by spawning an `explorer`-style read-only agent |
| Repo checkout, or a project that has `.codex/agents/*.toml` | the registered `.codex/agents/karta-*.toml` subagent | sandbox-enforced (`sandbox_mode = "read-only"`) |

You never copy a file. The only difference is the strength of read-only enforcement: behavioral on a bare plugin install, sandbox-enforced when the `.codex/agents/*.toml` are present. Both keep the gate strictly read-only; one is defense-in-depth stronger. If you want the stronger form in your own project, copy `.codex/agents/karta-acceptance-reviewer.toml` and `.codex/agents/karta-safety-auditor.toml` from this repo into your project's `.codex/agents/` — optional, not required.

## Notes

- **Reloading.** Codex loads skills and prompts at session start. After installing or updating, restart Codex (or start a new session) to pick up changes.
- **Windows.** The `.agents/skills/` mirror is real directories, not symlinks — Codex does not detect symlinked skills on Windows ([openai/codex#8400](https://github.com/openai/codex/issues/8400)). Nothing extra is needed on Windows.
- **Duplicate listing.** If you both install the karta plugin and run Codex inside the karta repo, each skill can appear twice in `/skills` (Codex does not de-duplicate by name across sources). Harmless — pick either entry. To avoid it, use one source at a time.
- **Per-skill metadata.** Each skill carries an `agents/openai.yaml` (display name, short description, implicit-invocation policy) that Codex uses for presentation; it fails open if absent.

## For contributors

The Codex artifacts (`.agents/skills/`, `.codex/agents/*.toml`, the bundled `*.agent.md`) are **generated** from the canonical `skills/` and `agents/` trees. Never hand-edit them. After editing a skill or agent, run the generators and the validator — see [AGENTS.md](../../AGENTS.md).
