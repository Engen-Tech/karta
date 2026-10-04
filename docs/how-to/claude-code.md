# Use karta with Claude Code

karta is a Claude Code plugin: its skills and five agents, installed from the repo marketplace. This guide covers installing it, what you get, and how the acceptance gate runs.

## Install

karta is packaged as a Claude Code plugin (the `.claude-plugin/` manifests). The GitHub repo is public, so the marketplace install needs no auth — but the code is proprietary, not open source; use is governed by the [License](../../LICENSE).

```bash
/plugin marketplace add https://github.com/Engen-Tech/karta.git
/plugin install karta@karta
```

This registers all karta skills under the `karta:` namespace:

- the pipeline skills — `karta-plan`, `karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`;
- `karta-plainlanguage`, the bundled writing standard;
- the opt-in writers `karta-doc-gardner` and `karta-kaizen`;
- `karta-debt`, on-demand debt-marker harvest;
- `karta-status`, the read-only run-status view.

It also registers five agents: the three read-only gates (`karta-acceptance-reviewer`, `karta-safety-auditor`, `karta-design-reviewer`) and the two writers (`karta-doc-gardner`, `karta-kaizen`). Plugin and skill names are stable since 1.0 with the `karta-` prefix.

## Invoke a skill

Invoke a skill explicitly by its namespaced name — `karta:karta-plan` — or just describe the task and let Claude Code match a skill by its description. A normal run is `karta-plan` to synthesize a binder, then `karta-deliver` to build it; reach for `karta-build` on its own for a single item.

## The acceptance gate runs automatically

karta's behavioral gate (`karta-verify`) dispatches two read-only agents — `karta-acceptance-reviewer` and `karta-safety-auditor`. On Claude Code the plugin registers them as subagents, so the gate runs with no setup: `karta-verify` dispatches each by name against the diff and drives any kickback to `karta-build`. The reviewers get no edit tool, and `guard_writer_confinement.py` denies edit tools from all three gate reviewers and holds their shell to a fail-closed allowlist of read-only commands. A reviewer cannot run project code or the project's type-check; `karta-verify` runs the type-check through the build skill's runner and hands the acceptance reviewer its record. This is a pre-operation hook, not a sandbox: git may still run programs named by the repository's own configuration. Retry counts live in an attempt ledger under the Git directory, so resuming a session does not reset them. The visual gate (`karta-validate`) compares a running view against its design prototype in a fresh read-only session. You copy nothing and configure nothing.

## Notes

- **Reloading.** Claude Code loads plugins at session start. After installing or updating, restart Claude Code (or start a new session) to pick up changes.
- **Updating.** Re-add or update from `/plugin` to pull a newer version from the marketplace.
- **Requirements.** Every install needs `git` 2.23 or newer, [`uv`](https://docs.astral.sh/uv/) on `PATH`, and Python 3.11 or newer. `karta-validate` also needs [`playwright-cli`](https://playwright.dev) and Chromium — see the [README](../../README.md#before-you-install) for the full list.
- **Hooks must be able to start.** If a hook cannot start — `uv` missing, say — Claude Code treats it as a non-blocking error and the tool call proceeds unguarded. A `disableAllHooks` setting or managed `allowManagedHooksOnly` policy also stops plugin hooks. Check launchers with `python3 scripts/install_smoke.py`; it proves they start, not that every guard blocks correctly.

## For contributors

The canonical sources are the `skills/` and `agents/` trees. The Codex projections are generated from them; after editing a skill or agent, run the generators and the validator — see [AGENTS.md](../../AGENTS.md).
