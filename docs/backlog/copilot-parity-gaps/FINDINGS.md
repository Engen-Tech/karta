# Copilot parity gap analysis — karta 2.38.3

Subject: what karta ships for GitHub Copilot (CLI and cloud agent) against what Copilot can now do,
with Windows treated as a first-class host. Baseline commit `0dc65d9`. Researched 2026-10-03
against Copilot CLI **1.0.92** (changelog head: 1.0.91, 2026-10-01).

Method:

- Official sources, read in full: the [hooks reference][hooks-ref], the [CLI plugin
  reference][plugin-ref], and the [CLI changelog][changelog]. Found with paired exa and parallelai
  searches.
- A live probe on Linux with the real `copilot` 1.0.92 binary, a throwaway trusted repo, and a
  Claude-format plugin loaded with `--plugin-dir`. Every hook dumped its env and stdin payload to a
  file. Claims marked **(probed)** come from those dumps, not from the docs.
- Not probed: Windows. `pwsh` is not installed on the probe host. Every Windows claim below comes
  from the official docs and is marked *(docs)*. No Copilot guard can be called enforced on Windows
  until one real Windows run passes. See [G13](#g13--no-copilot-hook-tests-and-no-windows-run).

## Verdict

Copilot is the weakest of karta's four hosts. The cause is one line, not missing capability.
`.github/plugin/plugin.json` sets `"hooks": {}`, so **none of karta's eight guards run under
Copilot**. `docs/how-to/copilot-cli.md` explains that line as keeping out an "incompatible" Claude
manifest. The probe shows the manifest is now mostly compatible. Copilot runs Claude-format plugin
hooks, sends Claude-shaped snake_case payloads, maps its tools to Claude tool names, and sets
`CLAUDE_PLUGIN_ROOT`.

Three real incompatibilities stand in the way. Each one fails silently, which is the risk:

1. **Exit 2 does not block a stop.** Copilot treats exit 2 from Stop and SubagentStop as a warning.
   The Claude guards block that way, so the delivery Stop gate and the whiff advisory would print a
   warning and let the session end. The JSON `{"decision":"block"}` form does block (probed). The
   Codex twins already emit both forms.
2. **File edits can arrive as a raw patch string.** A file create came through as
   `tool_name: "Edit"` with `tool_input` set to the `*** Begin Patch` text, not a
   `{file_path, content}` object (probed, default model). The Claude guards allow any edit whose
   `tool_input` is not a dict. Binder immutability, pack validation, and writer confinement would
   all pass every such edit. The Codex twins parse patch bodies, but they read
   `tool_input["command"]`, not a bare string.
3. **The Claude `command` string breaks on Windows.** A Claude-format `command` is copied to both
   the `bash` and `powershell` fields *(docs)*. In PowerShell, `"${CLAUDE_PLUGIN_ROOT}"` names a
   PowerShell variable, not the environment variable, so it expands to an empty string. PreToolUse
   fails closed on any non-zero exit, so a broken launcher could deny every matched tool call on
   Windows.

The rest of the list is packaging, coverage, and docs.

## What the probe established

|Question|Answer|Source|
|-|-|-|
|Do Claude-format plugin hooks (`hooks/hooks.json`, PascalCase events) run?|Yes: SessionStart, PreToolUse, Stop, SubagentStop|probed|
|Payload shape for PascalCase events|Claude-style snake_case: `hook_event_name`, `tool_name`, `tool_input`, `cwd`, `session_id`, `transcript_path`|probed|
|Tool names in the payload|Claude names: shell → `Bash`, task → `Agent`, file create → `Edit`|probed|
|Bash `tool_input`|`{command, description}`, same as Claude|probed|
|Agent `tool_input`|`{description, prompt, agent_type, name, mode}`|probed|
|File create `tool_input`|Raw apply_patch **string**: `*** Begin Patch\n*** Add File: b.txt\n+x\n*** End Patch\n`|probed (default model; other models may send a dict)|
|SubagentStop payload|`agent_id`, `agent_type`, `agent_name`, `last_assistant_message`, `stop_reason`, `transcript_path`|probed|
|Stop payload|`stop_reason`, `stop_hook_active`, `transcript_path` (`~/.copilot/session-state/<id>/events.jsonl`)|probed|
|Env vars given to plugin hooks|`CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `COPILOT_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`, `COPILOT_PROJECT_DIR`|probed|
|PreToolUse exit 2|Denies: "Denied by preToolUse hook: hook exited with code 2"|probed|
|PreToolUse Claude JSON `hookSpecificOutput.permissionDecision: "deny"`|Denies, and the reason text reaches the model|probed|
|Stop exit 2|**Does not block.** Shown as a `!` warning; the session ended|probed|
|Stop JSON `{"decision":"block","reason":...}`|Blocks. The model followed the reason and continued|probed|
|Repo `.claude/settings.json` hooks|Run when the folder is trusted (`trustedFolders` in `~/.copilot/config.json`); did not run in an untrusted `/tmp` repo|probed|
|PostToolUse exit 2 stderr reaches the model|No. Exit 2 is a warning except for preToolUse, permissionRequest, and postToolUseFailure. Use `additionalContext`|docs|
|Timeouts|Fail open for every event, preToolUse included. Default 30 s|docs|
|Other non-zero exits|Fail open, except preToolUse, which fails closed|docs|

## Gap list

Priority: **P0** = a karta guarantee is silently absent. **P1** = a supported Copilot path is
missing. **P2** = useful, not urgent. Effort: S under a day, M a few days, L a week or more.

|#|Gap|Priority|Effort|Windows angle|
|-|-|-|-|-|
|G1|No hooks wired for Copilot|P0|M|needs a `powershell` launcher|
|G2|Stop, SubagentStop, and PostToolUse guards signal with exit 2|P0|S|same fix on both|
|G3|Edit guards ignore patch-string `tool_input`|P0|S|`\` separators in patch paths|
|G4|Repo `.claude/settings.json` gates fire under Copilot, unadapted|P0|S|`${CLAUDE_PROJECT_DIR}` is empty in PowerShell|
|G5|SessionStart status uses Claude output conventions|P1|S|none|
|G6|Three of five agents have no Copilot profile|P1|S|none|
|G7|Writer confinement has no Copilot writer to recognize|P1|M|Bash parser must also read PowerShell|
|G8|No cloud-agent support|P1|M|not applicable (Linux only)|
|G9|No guidance on Copilot instruction files|P2|S|none|
|G10|Packaging: Agent Plugins layout, marketplace pin, `copilot plugin update`|P2|S|none|
|G11|Sandbox and policy interaction undocumented|P2|S|Windows sandbox cannot deny single paths|
|G12|Windows hook latency not measured on Copilot|P2|M|the whole point|
|G13|No Copilot hook tests and no Windows run|P0 (blocks calling G1–G4 done)|M|pwsh CI job|
|G14|Docs describe Copilot as skills plus two reviewers|P1|S|Windows install steps|
|G15|Optional Copilot-only surfaces unused|P2|L|varies|

### G1 — No hooks wired for Copilot

**Now.** `.github/plugin/plugin.json` has `"hooks": {}`. The test
`test_copilot_plugin_selects_native_profiles_and_no_claude_hooks`
(`tests/test_codex_gate_models.py:100`) locks that in. Copilot users get no binder immutability, no
pack validation, no dispatch inspectors, no delivery Stop gate, no whiff advisory, and no status
injection.

**Copilot supports.** Plugin hooks come from `hooks.json`, `hooks/hooks.json`, or the manifest's
`hooks` field [hooks-ref]. The native schema is `{"version": 1, "hooks": {...}}`. Each entry can
carry separate `bash` and `powershell` commands, an `exec` + `args` form with no shell, `cwd`,
`env`, `timeoutSec`, and `matcher`. `command` is a fallback copied to both shells. PascalCase event
names get the Claude-compatible payload, so the existing guard logic applies. Since 1.0.12, plugin
hooks get `{{project_dir}}` and `{{plugin_data_dir}}` template variables [changelog].

**Fix.** Ship `.github/plugin/hooks.json` in the native schema and point the manifest at it. Do not
point it at the Claude `hooks/hooks.json`, because of G2, G3, and the Windows expansion problem.

- PascalCase events, so the guards keep receiving Claude-shaped payloads.
- `bash`: `sh -c` with `$PLUGIN_ROOT` and a file-exists check, the same shape as
  `.codex-plugin/hooks/hooks.json`.
- `powershell`: `& "$env:PLUGIN_ROOT\.codex-plugin\hooks\launch_hook.ps1" <guard> Plugin`. Reuse
  the Codex launcher. It passes the guard's exit code through and fails open only on launcher
  errors (verified on Windows in 2.38.2).
- Matchers in Claude tool names: `Write|Edit|NotebookEdit`, `Bash`, `Agent|Task`.
- Script source: the Codex twins in `.codex-plugin/hooks/scripts/`, not the Claude originals. They
  already emit JSON decisions and parse patch bodies. G3 covers the one input shape they miss.

**Open question.** The docs do not say whether `exec` + `args` expands `${PLUGIN_ROOT}` or the
`{{...}}` templates. If it does, one entry with no shell serves both platforms and skips PowerShell
start-up (G12). Probe it before choosing.

### G2 — Stop, SubagentStop, and PostToolUse guards signal with exit 2

**Now.** `hooks/scripts/guard_delivery_stop.py:249` and `guard_subagent_whiff.py:255` return 2
with the reason on stderr. `guard_pack_write.py` sends PostToolUse findings back the same way.

**Copilot does.** Exit 2 is a warning for every event except preToolUse, permissionRequest, and
postToolUseFailure [hooks-ref]. The probe confirmed it: an exit-2 Stop hook printed a warning and
the session ended. A Stop hook that printed `{"decision":"block","reason":"..."}` and exited 0
blocked, and the model followed the reason. Copilot stops forcing continuation after 8 blocks in a
row and sets `stop_hook_active` in the payload [hooks-ref].

**Fix.** Use the Codex twins, which emit the JSON decision and exit 2 together. For PostToolUse
pack findings, emit `hookSpecificOutput.additionalContext` so the model sees them. Stdout must hold
exactly one JSON object [hooks-ref], so check that no guard prints anything else to stdout.

### G3 — Edit guards ignore patch-string `tool_input`

**Now.** `hooks/scripts/guard_binder_immutability.py:89` and `guard_pack_write.py:97` treat a
non-dict `tool_input` as nothing to check and allow it. The Codex twins parse patches, but only
from `tool_input["command"]` (`.codex-plugin/hooks/scripts/guard_binder_immutability.py:153`,
`guard_pack_write.py:106`, which also requires `tool_name == "apply_patch"`).

**Copilot does.** With the default model, a file create arrived as `tool_name: "Edit"` and
`tool_input: "*** Begin Patch\n*** Add File: b.txt\n+x\n*** End Patch\n"` (probed). Other models
may use the `create` or `edit` tools and send dicts. The guards must accept both.

**Fix.** One normalizer shared by the Edit-family guards: if `tool_input` is a string starting with
`*** Begin Patch`, treat it as `{"command": <string>}` and take the existing patch path, whatever
the `tool_name`. Add self-test cases for the bare-string shape. Patch paths may use `\` on Windows,
so extend the path matcher to normalize separators and cover it with a test.

### G4 — Repo `.claude/settings.json` gates fire under Copilot, unadapted

**Now.** karta's own `.claude/settings.json` runs `precommit_gate.py` (timeout 900) and
`roundtable_gate.py` (timeout 600) on Bash, through
`uv run --script "${CLAUDE_PROJECT_DIR}/scripts/hooks/..."`.

**Copilot does.** It reads `.claude/settings.json` and `.claude/settings.local.json` as repo hook
sources [hooks-ref]. The probe confirmed they fire once the folder is trusted. On Linux this works
by accident: the payload is Claude-shaped and `CLAUDE_PROJECT_DIR` is set. On Windows the `command`
is copied to `powershell` *(docs)*, `${CLAUDE_PROJECT_DIR}` expands to empty, `uv` fails, and
preToolUse fails closed. A Windows contributor running Copilot in a karta checkout could have every
shell call denied.

**Fix.** Add `.github/hooks/karta-repo.json` in the native schema with `bash` and `powershell`
entries for both gates, using `{{project_dir}}` or `$env:COPILOT_PROJECT_DIR`. Then stop the
Claude file firing a second time under Copilot. Two options: make its `command` work in both
shells, or tell Copilot users to set `disableAllHooks` for that source. Pick one and test it on
Windows (G13). Also note that a hook timeout fails **open** on Copilot, unlike Claude, so a gate
that runs past its timeout lets the commit through.

### G5 — SessionStart status uses Claude output conventions

**Now.** `inject_karta_status.py` writes its summary to stdout as plain text, which Claude and
Codex add to context.

**Copilot does.** Stdout on exit 0 is parsed as JSON [hooks-ref]. sessionStart accepts
`additionalContext`. subagentStart also accepts `additionalContext` and cannot block. That could
hand a dispatched gate reviewer its binder context without relying on the dispatcher's prompt.

**Fix.** Under Copilot (detect `COPILOT_PLUGIN_ROOT`), emit
`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":...}}`. Probe whether
plain text is dropped or shown. Consider a SubagentStart hook for the two gate agents later.

### G6 — Three of five agents have no Copilot profile

**Now.** `.github/agents/` holds only `karta-acceptance-reviewer` and `karta-safety-auditor`,
generated by `scripts/sync_codex_agents.py`. `karta-design-reviewer`, `karta-doc-gardner`, and
`karta-kaizen` exist for Claude and Codex but not for Copilot.

**Copilot supports.** Plugin agents (`*.agent.md`) with `model`, `reasoningEffort`, `tools`, and
`mcp-servers` in frontmatter [plugin-ref]. Since 1.0.86, `include-custom-instructions: true` lets an
agent read AGENTS.md, copilot-instructions.md, and CLAUDE.md [changelog]. `/subagents` lets the
user set model and effort per agent.

**Fix.** Extend the generator to emit all five. Give doc-gardner and kaizen the edit tool, since
they write, and keep the reviewers without one. Decide per agent whether
`include-custom-instructions` helps (reviewers probably not, writers probably yes). Update the test
that pins the profile list.

### G7 — Writer confinement has no Copilot writer to recognize

**Now.** `guard_writer_confinement.py` exists only on Claude. It needs to know which subagent is
writing. Codex skipped it for the same reason (`docs/how-to/codex.md`, confinement row).

**Copilot does.** Dispatch and SubagentStop payloads carry `agent_type` (probed). Whether a
PreToolUse payload from inside a subagent names that subagent is **not verified**.

**Fix.** Probe that first. If it does, wire the guard once G6 ships doc-gardner and kaizen. Its
static Bash write parsing assumes a POSIX shell. On Windows, Copilot's `powershell` tool is also
reported as `Bash`, so the parser needs PowerShell write forms (`Set-Content`, `Out-File`, `>`), or
the guard must fail closed on PowerShell it cannot parse for confined writers.

### G8 — No cloud-agent support

**Now.** Nothing for the Copilot cloud agent.

**Copilot does.** The cloud agent runs in a Linux sandbox. It reads hooks only from
`.github/hooks/*.json` and runs only `bash` or `command` entries. It loads no plugins, no user
config, and no `.claude` settings. `ask` counts as deny. permissionRequest and notification never
fire. The working directory is `/workspace`, the network is firewalled, and `GITHUB_TOKEN` is not
set [hooks-ref]. Setup runs from `.github/workflows/copilot-setup-steps.yml`.

**Fix.** For projects that want karta under the cloud agent, document (or have `karta-init` offer)
a `.github/hooks/karta.json` that calls guard scripts vendored into the repo, plus a
`copilot-setup-steps.yml` that installs `uv` and Python. Plugins do not load there, so the scripts
cannot stay in the plugin. This is the one case where karta must copy files into the user's repo,
so keep it opt-in.

### G9 — No guidance on Copilot instruction files

Copilot reads `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`,
AGENTS.md, and CLAUDE.md with `@` imports. karta's skills carry the doctrine, so the risk is low.
The how-to should say which file a Copilot user puts karta pointers in, and confirm karta's own repo
instructions reach Copilot sessions.

### G10 — Packaging

The plugin reference describes the Agent Plugins 1.0/1.1 layout: a root `plugin.json` with
`$schema` takes precedence, and client-specific pieces go under `com.github.copilot/` (agents,
commands, rules, `hooks/hooks.json`, `lsp.json`) [plugin-ref]. Marketplace entries in
`.github/plugin/marketplace.json` or `.claude-plugin/marketplace.json` support `sha` pinning,
`copilot plugin update`, and `autoUpdate`. karta's legacy `.github/plugin/plugin.json` path still
works, so nothing is forced. Record the decision, and add `copilot plugin update` to the upgrade
steps in the how-to.

### G11 — Sandbox and policy interaction undocumented

Copilot runs hooks inside its sandbox when one is on. Hooks can read their own directory and write
to `$COPILOT_PLUGIN_DATA` [hooks-ref]. karta guards shell out to `git` and `uv`, and
`inject_karta_status.py` starts the Karta Watch hub with `serve_status.py --ensure`. Expect the
sandbox to block that spawn, as the Codex sandbox can. On Windows the sandbox cannot deny single
paths. Enterprise policy hooks (`/etc/github-copilot/policy.d/`, or
`C:\ProgramData\GitHub\Copilot\policy.d\` and `HKLM\Software\Policies\GitHub\Copilot` on Windows)
load before karta's and can set `disableAllHooks`. Document both, and have the status surface say
when the hub spawn was denied.

### G12 — Windows hook latency not measured on Copilot

Backlog entry 29 measured `uv run` and PowerShell launcher cost on Claude and Codex. Copilot adds
the same cost on every matched tool call. Run the existing bench script against Copilot on Windows
once G1 lands. If `exec` + `args` works (G1 open question), it skips PowerShell start-up and should
be the Windows default.

### G13 — No Copilot hook tests and no Windows run

- Replace the "no Claude hooks" assertion with one that checks the native Copilot hooks file: every
  guard is listed, every entry has both `bash` and `powershell`, and no `powershell` string
  contains `${`.
- Add self-test cases for the payload shapes in the probe table, especially the bare-string patch.
- Add an opt-in live smoke test (needs a Copilot login): run `copilot -p` in a temp trusted repo
  with `--plugin-dir`, attempt a binder write, and assert the deny. Run it on a Windows runner with
  `pwsh` too. Until that Windows run passes, G1–G4 are "wired", not "enforced".

### G14 — Docs describe Copilot as skills plus two reviewers

`docs/how-to/copilot-cli.md` needs the enforcement table `codex.md` has (Rule / Claude Code /
Copilot), a Windows section, and the trusted-folder requirement for repo hooks. The README support
matrix should match.

### G15 — Optional Copilot-only surfaces

Not parity, but available: `/fleet` and background subagents for karta-deliver wave dispatch; LSP
config with `bash` and `powershell` keys; extensions and canvases (a Karta Watch canvas could
replace the browser tab); `/every` and `/after` for scheduled re-checks; `errorOccurred` and
`postToolUseFailure` events for richer failure reports; `--acp` for editor hosts. Pick these up
only after G1–G14.

## Copilot pitfalls to plan around

- Hooks may not fire after a session resume in some SDK builds (copilot-sdk issue #782). 1.0.86
  fixed plugins being dropped on resume; re-check on the version in use.
- Since 1.0.60, Windows no longer finds executables in the working directory by bare name
  [changelog]. Use full paths or rely on `PATH`.
- `copilot --model` rejects model IDs not enabled for the account. A `model:` pin in a profile can
  fail on another user's plan, which is why the current profiles leave `model` unset.
- Repo hooks need a trusted folder. A user who declines the trust prompt gets no repo gates and
  sees no error.

## Order of work

1. G3 and G2 in the scripts. Small, and testable on Linux.
2. G1 native hooks file, then the G13 unit tests.
3. G4 repo gates file.
4. One Windows run (G13 live test, G12 bench). Only then mark guards "Enforced" in the doc (G14).
5. G5, G6, G7.
6. G8 when a user wants the cloud agent; G9–G11 docs; G15 optional.

[hooks-ref]: https://docs.github.com/en/copilot/reference/hooks-reference
[plugin-ref]: https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference
[changelog]: https://github.com/github/copilot-cli/blob/main/changelog.md
