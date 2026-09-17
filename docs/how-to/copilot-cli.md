# Acceptance and safety reviewers in Copilot CLI

The deliverable supplies native Copilot CLI profiles for `karta-acceptance-reviewer`
and `karta-safety-auditor`. Both work with Claude or GPT: they use Copilot's
configured per-reviewer model, or inherit the resolved session model when there
is no override. Both request `xhigh` reasoning effort. Neither Claude's `opus` alias
nor the Codex GPT model pin selects a model on Copilot.

## Install or update

Install from the repository root, which contains the Copilot entrypoint at
`.github/plugin/plugin.json`:

```sh
copilot plugin install Engen-Tech/karta
```

To test a local checkout, including an unreleased fix:

```sh
copilot plugin install ./karta
```

Run that command from the checkout's parent directory. On Windows, use the path
to your local checkout. Reinstall a local plugin after changing its files, then
start a new Copilot session: the CLI caches installed components. Do not install
`plugins/karta/`, which is the Codex projection.

Use `/agent` to confirm that both reviewers are loaded and `/subagents` to inspect
their model settings. Project and personal profiles can shadow plugin profiles
with the same name. If either reviewer is unexpectedly locked to GPT or shows
the bare Claude alias `opus`, inspect the profile's source and update that local
override or use the generated profile from this checkout. [GitHub documents plugin caching and installation](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-creating)
and [profile precedence](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference).

## What selects the model

`scripts/sync_codex_agents.py` generates `.github/agents/*.agent.md` from each
canonical reviewer's prompt, tools, and effort. Copilot profiles deliberately
omit `model`, `models`, and `modelPolicy`; they contain only this effort setting:

```yaml
reasoningEffort: "xhigh"
```

Copilot's per-reviewer settings take precedence over session inheritance. Thus a
Claude session can use Claude reviewers and a GPT session can use GPT reviewers;
you can also select a different model for either reviewer through `/subagents`.
An Auto session uses its resolved model. The plugin does not force a provider or
translate the Claude `opus` alias into a mandatory GPT model.

`karta-verify` dispatches the named custom reviewers with fresh review briefs.
It supplies a model override only when you explicitly choose one. The Haiku
metadata on the thin dispatcher does not choose the reviewers' models. If the
host cannot launch a fresh reviewer or honor your explicit setting, verification
reports that limitation; it does not block merely because the session uses
Claude instead of GPT, or GPT instead of Claude.
See [GitHub's model and effort reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference#custom-agent-frontmatter-fields).

## Scope and limits

This integration supplies the shared skills and these two native reviewers.
It does not certify full Copilot parity with the Claude, Codex, or Pi integrations.
The Copilot entrypoint declares an empty hook configuration so the CLI does not
load the incompatible Claude hook manifest by convention.

The profiles retain read, search, and shell tools for inspecting the actual diff.
They expose no editing tool, and their prompts forbid writes. Shell access is
still controlled by Copilot's host permissions; these profiles are not OS-enforced
read-only sandboxes. The existing acceptance assertions, safety checks, and retry
limits remain in the canonical reviewer prompts.

## Contributors

Edit `agents/karta-acceptance-reviewer.md` or `agents/karta-safety-auditor.md`, then
run `uv run scripts/sync_codex_agents.py` and `uv run scripts/sync_codex_skills.py`.
The first generator also derives the Copilot plugin version from the Claude
manifest. Its `--check` mode and the plugin validator detect projection drift.
Run `python3 tests/test_codex_gate_models.py` for model propagation regressions.
