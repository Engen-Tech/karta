# Use karta with Claude Code

karta is a Claude Code plugin: its skills and three agents, installed from the repo marketplace. This guide covers installing it, what you get, and how the acceptance gate runs.

## Install

karta is packaged as a Claude Code plugin (the `.claude-plugin/` manifests). The GitHub repo is public, so the marketplace install needs no auth — but the code is proprietary, not open source; use is governed by the [License](../../LICENSE).

```bash
/plugin marketplace add https://github.com/Engen-Tech/karta.git
/plugin install karta@karta
```

This registers all karta skills under the `karta:` namespace:

- the pipeline skills — `karta-plan`, `karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`;
- `karta-plainlanguage`, the bundled writing standard;
- the opt-in `karta-doc-gardner`;
- `karta-debt`, on-demand debt-marker harvest.

It also registers three agents: the two read-only gates (`karta-acceptance-reviewer`, `karta-safety-auditor`) and the `karta-doc-gardner` writer. Plugin and skill names are stable since 1.0 with the `karta-` prefix.

## Invoke a skill

Invoke a skill explicitly by its namespaced name — `karta:karta-plan` — or just describe the task and let Claude Code match a skill by its description. A normal run is `karta-plan` to synthesize a binder, then `karta-deliver` to build it; reach for `karta-build` on its own for a single item.

## The acceptance gate runs automatically

karta's behavioral gate (`karta-verify`) dispatches two read-only agents — `karta-acceptance-reviewer` and `karta-safety-auditor`. On Claude Code the plugin registers them as subagents, so the gate runs with no setup: `karta-verify` dispatches each by name, read-only, against the diff, and drives any kickback to `karta-build`. The visual gate (`karta-validate`) compares a running view against its design prototype the same way. You copy nothing and configure nothing.

## Notes

- **Reloading.** Claude Code loads plugins at session start. After installing or updating, restart Claude Code (or start a new session) to pick up changes.
- **Updating.** Re-add or update from `/plugin` to pull a newer version from the marketplace.
- **Requirements.** Most skills need only `git` and your project's toolchain. `karta-validate` also needs [`uv`](https://docs.astral.sh/uv/), [`playwright-cli`](https://playwright.dev), and Chromium — see the [README](../../README.md#requirements) for the full per-skill list.

## For contributors

The canonical sources are the `skills/` and `agents/` trees. The Codex projections are generated from them; after editing a skill or agent, run the generators and the validator — see [AGENTS.md](../../AGENTS.md).
