# Use karta with Codex CLI

karta runs on Codex CLI with the same skills and gate logic it uses on Claude Code. The enforcement mechanism differs where Codex does not provide plugin hooks. This guide explains both installation modes, the fallback agents, and the boundaries you can rely on.

## Two ways to install

### As a plugin (any project)

karta is packaged as a Codex plugin published through the repo marketplace (`.agents/plugins/marketplace.json`). The marketplace points at `./plugins/karta`, a generated real-directory install projection of the canonical `.codex-plugin/plugin.json` and `skills/` tree.

1. In Codex, open the plugin browser: `/plugins`.
2. Add this repository as a marketplace source and install **karta**.
3. The skills are available immediately — including `karta-plan`, `karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`, `karta-kaizen`, `karta-plainlanguage`, `karta-doc-gardner`, `karta-status`, and `karta-debt`.

From the CLI, the equivalent commands are:

```bash
codex plugin marketplace add https://github.com/Engen-Tech/karta.git
codex plugin add karta@karta-local
```

Invoke a skill explicitly with `$karta-plan` (type `$` to mention a skill, or `@karta` to scope to the plugin), or just describe the task and let Codex match a skill by its description.

### Clone and run (repo-local)

Run `codex` from inside a karta checkout. Codex scans `.agents/skills/` from your working directory up to the repo root and discovers all the skills with no install step. The mirror is committed real directories, so this works the same on macOS, Linux, and Windows.

## The acceptance gate runs automatically

karta's behavioral gate (`karta-verify`) dispatches two read-only agents — `karta-acceptance-reviewer` and `karta-safety-auditor`. Codex plugins cannot register subagents, so karta makes the gate work everywhere without any manual setup:

| How karta reached you | Where the gate agent comes from | Read-only enforcement |
|-|-|-|
| Plugin install | instructions bundled inside the `karta-verify` skill (`references/*.agent.md`), spawned as a fresh fallback agent | instruction-enforced by the bundled agent; sandbox-enforced only when the Codex host starts that agent or session read-only |
| Repo checkout, or a project that has `.codex/agents/*.toml` | the registered `.codex/agents/karta-*.toml` subagent | sandbox-enforced (`sandbox_mode = "read-only"`) |

You never copy a fallback instruction file. On a bare plugin install, the bundled agent says not to write; a read-only Codex sandbox makes that boundary enforceable. When registered `.codex/agents/*.toml` files are present, their read-only sandbox supplies that enforcement automatically. If you want the registered form in your own project, copy `.codex/agents/karta-acceptance-reviewer.toml` and `.codex/agents/karta-safety-auditor.toml` from this repo into your project's `.codex/agents/`.

## Feature compatibility on Codex

The installed plugin passed live, feature-by-feature Codex tests for fallback gates, Kaizen, and Plannotator. Read the [compatibility result](../showcase/codex-1.19-compatibility/README.md) before relying on a security or write-confinement boundary.

- **Fallback gates:** work without repo-local registered agents by loading the agent instructions bundled with `karta-verify`. Use a read-only Codex sandbox for an enforceable no-write boundary.
- **Kaizen:** the absent and disabled switches are no-ops. Direct mode detects packs and leaves edits uncommitted. Delivery mode uses the binder's pinned packs and can land a labeled `kaizen:` commit on the supplied integration branch.
- **Plannotator:** Karta probes for the separately installed CLI. If it is absent, Karta does not mention the browser review surface. If it is present, plan annotations map only when the target field is unambiguous; Karta returns ambiguous feedback to chat and still waits for the explicit `commit` verb.
- **Hooks:** Karta's Claude Code hooks are unavailable on Codex. Exact Kaizen writer paths and similar narrow boundaries are instruction-enforced unless a separate sandbox or script enforces them.

## Notes

- **Reloading.** Codex loads skills and prompts at session start. After installing or updating, restart Codex (or start a new session) to pick up changes.
- **Windows.** The `.agents/skills/` mirror and `plugins/karta/` install projection are real directories, not symlinks — Codex does not detect symlinked skills on Windows ([openai/codex#8400](https://github.com/openai/codex/issues/8400)). Nothing extra is needed on Windows.
- **Duplicate listing.** If you both install the karta plugin and run Codex inside the karta repo, each skill can appear twice in `/skills` (Codex does not de-duplicate by name across sources). Harmless — pick either entry. To avoid it, use one source at a time.
- **Per-skill metadata.** Each skill carries an `agents/openai.yaml` (display name, short description, implicit-invocation policy) that Codex uses for presentation; it fails open if absent.

## For contributors

The Codex artifacts (`.agents/skills/`, `plugins/karta/`, `.codex/agents/*.toml`, the bundled `*.agent.md`) are **generated** from the canonical `skills/` and `agents/` trees. Never hand-edit them. After editing a skill or agent, run the generators and the validator — see [AGENTS.md](../../AGENTS.md).
