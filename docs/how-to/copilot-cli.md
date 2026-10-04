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
The known gaps, including what blocks wiring karta's guards on Copilot and on
Windows, are listed in
[the Copilot parity gap analysis](../backlog/copilot-parity-gaps/FINDINGS.md).

In this checkout, the repo commit gates (`precommit_gate.py` and
`roundtable_gate.py`) run on Copilot from `.github/hooks/karta-repo.json`. That
manifest sets `KARTA_HOOK_SOURCE=copilot`, so the copies in `.claude/settings.json`
do nothing under Copilot and each gate runs once. On Copilot, a gate that times
out lets the command through. On Windows the `.claude/settings.json` copies fail
before they reach that check; see [Windows](#windows).

The profiles retain read, search, and shell (`execute`) tools for inspecting the
actual diff. They expose no editing tool, and their prompts forbid writes. Shell
access is still controlled by Copilot's host permissions, and no Karta
hook runs on Copilot, so a shell write by a reviewer is stopped only by its
instructions or by what you allow in Copilot's permission prompts: read-only is
instruction-level here, not an OS-enforced sandbox. This was not exercised against
a live Copilot session. The existing acceptance assertions, safety checks, and retry
limits remain in the canonical reviewer prompts; the retry counts themselves are kept
by `karta-verify`'s attempt ledger.

## Windows

These notes come from a live run on Windows 11 with PowerShell 7.6.6, Copilot
CLI 1.0.92-3, and an enterprise policy that disables bypass-permissions.

### Legacy `.claude/settings.json` hooks block every tool call

Copilot CLI also reads the hooks in `.claude/settings.json`. Its logs show them as
source "repo settings", and they fail closed. On Windows, Copilot runs each hook
`command` string through PowerShell, not bash, and it does not set
`CLAUDE_PROJECT_DIR`. The karta entries there, such as
`uv run --script "${CLAUDE_PROJECT_DIR}/scripts/hooks/precommit_gate.py"`, break
in two ways:

1. PowerShell cannot start `uv` installed with WinGet, which is a symlink at
   `%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe`. The error is
   `Program 'uv.exe' failed to run ... No application is associated with the specified file for this operation.`
   To get past it, put the real package directory
   `%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe`
   ahead of the `Links` directory on `PATH`. Also set `UV_PYTHON` and
   `UV_NO_MANAGED_PYTHON=1`: uv-managed Pythons under `AppData\Roaming` hit the
   same launch error.
2. Even when `uv` starts, PowerShell expands `${CLAUDE_PROJECT_DIR}` as an empty
   PowerShell variable. The path becomes `C:\scripts\hooks\precommit_gate.py`
   and the hook fails with `can't open file 'C:\\scripts\\hooks\\precommit_gate.py'`.

Copilot then denies every tool call, even `echo ok`, with
`Denied by preToolUse hook from "repo settings" (hook errored)`.

This is a Copilot CLI defect, tracked upstream as
[github/copilot-cli#4001](https://github.com/github/copilot-cli/issues/4001)
(hooks run through PowerShell and `$CLAUDE_PROJECT_DIR` is not set) and
[github/copilot-cli#4399](https://github.com/github/copilot-cli/issues/4399)
(shell operators in cross-tool hooks break on Windows PowerShell). Until it is
fixed, a Windows Copilot user of this repo cannot get any tool call allowed while
the `.claude/settings.json` hooks are present. Do not edit that file to work around
it: Claude Code depends on it.

The supported path on Windows is `.github/hooks/karta-repo.json`. Its entries
declare separate `bash` and `powershell` commands, and the PowerShell command goes
through `.codex-plugin/hooks/launch_hook.ps1`. Those entries pass.

Setting `disableAllHooks` in `.github/copilot/settings.json` stops the failure,
but it also disables the karta gates. It is a blunt escape hatch, not a fix.

### Commit gates and timing

Both gates pass under Copilot on Windows: `precommit_gate.py` and
`roundtable_gate.py` (the latter needs the [hook environment](#hook-environment)
allowance). A `git commit` took about 9 minutes end to end on the test laptop.
Most of that is `validate_plugin.py`, which took 441–545 s with its default
parallel pool and 992 s serially. The pool size is `min(8, max(2, cpu_count))`;
set `KARTA_VALIDATE_JOBS=<int>` to change it, or `KARTA_VALIDATE_JOBS=1` for the
serial path.

| Budget | Seconds |
|-|-|
| Each other gate in `precommit_gate.py` | 100 |
| `validate_plugin.py` | 720 |
| Outer hook timeout, `precommit_gate.py` | 1200 |
| Outer hook timeout, `roundtable_gate.py` | 600 |

The cost is per process, not per file scanned. Windows Defender exclusions made no
measurable difference. Measured startup costs:

| Process | Cost per call |
|-|-|
| `python` | 38 ms |
| `git` | about 80 ms |
| guard hook | 105 ms |
| `pwsh -Command` | about 360 ms |

## Contributors

Edit `agents/karta-acceptance-reviewer.md` or `agents/karta-safety-auditor.md`, then
run `uv run scripts/sync_codex_agents.py` and `uv run scripts/sync_codex_skills.py`.
The first generator also derives the Copilot plugin version from the Claude
manifest. Its `--check` mode and the plugin validator detect projection drift.
Run `python3 tests/test_codex_gate_models.py` for model propagation regressions.

### Hook environment

Copilot CLI runs the repo hooks in `.github/hooks/karta-repo.json` with
`GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=safe.bareRepository` and
`GIT_CONFIG_VALUE_0=explicit` in their environment. This setting only limits
which bare repositories git will open, so the commit gates allow it. They still
deny any other `GIT_CONFIG_*` injection, a malformed or missing pair, and
`GIT_CONFIG_PARAMETERS`.
