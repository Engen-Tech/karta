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
- Windows, two probes. A GitHub-hosted `windows-latest` runner (see
  [Windows probe](#windows-probe-2026-10-03-github-actions-windows-latest)) covered hook shell
  selection, the native payload, and a repo-hook failure. A real Windows 11 PC (see
  [Windows PC probe](#windows-pc-probe-real-machine)) ran karta's own launcher and guards under
  Copilot. Where the two disagree, the PC result is the one to plan on, and each claim names the
  machine it came from. Windows claims neither probe tested come from the official docs and are
  marked *(docs)*.

## Verdict

Copilot is the weakest of karta's four hosts. The cause is one line, not missing capability.
`.github/plugin/plugin.json` sets `"hooks": {}`, so **none of karta's eight guards run under
Copilot**. `docs/how-to/copilot-cli.md` explains that line as keeping out an "incompatible" Claude
manifest. The probe shows the manifest is now mostly compatible. Copilot runs Claude-format plugin
hooks, sends Claude-shaped snake_case payloads, and sets `CLAUDE_PLUGIN_ROOT`. On a real Windows 11
PC, karta's PowerShell launcher and its guards ran under Copilot, and the binder guard denied an
edit to a committed binder once the payload key was mapped.

Three real incompatibilities stand in the way. Each one fails silently, which is the risk:

1. **Exit 2 alone does not block a stop.** Copilot treats exit 2 from Stop and SubagentStop as a
   warning (Linux probe). The Claude guards block that way, so the delivery Stop gate and the whiff
   advisory would print a warning and let the session end. The JSON `{"decision":"block"}` form
   does block, with exit 0 or exit 2 (Linux and Windows PC). The Codex twins already emit both.
2. **Edit payloads do not carry `file_path`.** On the Windows PC, Copilot sent `Write` with
   `{path, file_text}` and `Edit` with `{path, old_str, new_str}`. The guards read only
   `file_path`, `notebook_path`, and `command`, so the unmodified binder guard let the agent edit a
   committed binder. On Linux the default model sent a file create as a raw `*** Begin Patch`
   string, which the guards also allow. The guards must accept both shapes (G3, G16).
3. **PowerShell drops the exit code.** A `powershell` entry that runs `& launch_hook.ps1 ...` with
   no trailing `exit` reports 1 to Copilot when the guard exits 2 (Windows PC). PreToolUse still
   denies, as "hook errored", because it fails closed. Stop discards the JSON decision on exit 1
   and does not block. Every entry must end with `; exit $LASTEXITCODE` (G17).

An earlier version of this doc expected `"${CLAUDE_PLUGIN_ROOT}"` to expand to empty under
PowerShell. That is wrong for plugin hooks on Copilot 1.0.92-3: Copilot substitutes the text before
pwsh sees it (Windows PC). The Claude-format manifest still fails on Windows, for other reasons.
On the PC, `uv` could not start, every hook errored, and PreToolUse failed closed, denying every
matching edit. The installed karta 2.30.0 plugin's hooks never run on Windows at all: each command
is a quoted string, which pwsh prints and exits 0.

**Fix for G1.** Ship a native `.github/plugin/hooks.json` whose `powershell` entries call
`.codex-plugin/hooks/launch_hook.ps1` and end with `; exit $LASTEXITCODE`, and make the guards read
`path` and `file_text` as well as `file_path`. After that, three smaller items remain: the deny
reason does not reach the model (G18), the agent can satisfy the Stop guard by writing the done ref
itself (G19), and each matched tool call costs about 1 s on Windows (G20). The rest of the list is
packaging, coverage, and docs.

## What the probe established

|Question|Answer|Source|
|-|-|-|
|Do Claude-format plugin hooks (`hooks/hooks.json`, PascalCase events) run?|Yes: SessionStart, PreToolUse, Stop, SubagentStop|probed|
|Payload shape for PascalCase events|Claude-style snake_case: `hook_event_name`, `tool_name`, `tool_input`, `cwd`, `session_id`, `transcript_path`|probed|
|Tool names in the payload|Claude names: shell → `Bash`, task → `Agent`, file create → `Edit` (Linux; the Windows PC sent `Write` for a create)|probed|
|Bash `tool_input`|`{command, description}`, same as Claude|probed|
|Agent `tool_input`|`{description, prompt, agent_type, name, mode}`|probed|
|File create `tool_input`|Raw apply_patch **string**: `*** Begin Patch\n*** Add File: b.txt\n+x\n*** End Patch\n`|probed on Linux (default model). The Windows PC sent a dict keyed `path`; see [Windows PC probe](#windows-pc-probe-real-machine)|
|SubagentStop payload|`agent_id`, `agent_type`, `agent_name`, `last_assistant_message`, `stop_reason`, `transcript_path`|probed|
|Stop payload|`stop_reason`, `stop_hook_active`, `transcript_path` (`~/.copilot/session-state/<id>/events.jsonl`)|probed|
|Env vars given to plugin hooks|`CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT`, `PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `COPILOT_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`, `COPILOT_PROJECT_DIR`|probed|
|PreToolUse exit 2|Denies: "Denied by preToolUse hook: hook exited with code 2"|probed|
|PreToolUse Claude JSON `hookSpecificOutput.permissionDecision: "deny"`|Denies, and the reason text reaches the model|probed on Linux; not yet tried on Windows (G18)|
|Stop exit 2|**Does not block.** Shown as a `!` warning; the session ended|probed|
|Stop JSON `{"decision":"block","reason":...}`|Blocks. The model followed the reason and continued|probed|
|Repo `.claude/settings.json` hooks|Run when the folder is trusted (`trustedFolders` in `~/.copilot/config.json`); did not run in an untrusted `/tmp` repo|probed|
|PostToolUse exit 2 stderr reaches the model|No. Exit 2 is a warning except for preToolUse, permissionRequest, and postToolUseFailure. Use `additionalContext`|docs|
|Timeouts|Fail open for every event, preToolUse included. Default 30 s|docs|
|Other non-zero exits|Fail open, except preToolUse, which fails closed|docs|

### Windows probe (2026-10-03, GitHub Actions `windows-latest`)

Copilot CLI 1.0.91 on a hosted Windows runner, authenticated with the workflow's own
`GITHUB_TOKEN` (`permissions: copilot-requests: write`, user-owned private repo, no PAT). Repo
hooks loaded because the workspace was listed in `trustedFolders`.

|Question|Answer|
|-|-|
|Which entry runs from a native `.github/hooks/*.json` hook?|`powershell`, under PowerShell 7.6 Core. `bash` entries were ignored|
|Native preToolUse payload on Windows|camelCase: `sessionId`, `timestamp`, `cwd` (`D:\\a\\...`), `toolName`, `toolArgs`|
|Shell tool name on Windows|`powershell`, not `bash`/`Bash`. Guards matching only the Bash tool miss every shell call|
|Claude-format repo hook with `matcher: ""`|Whole `.claude/settings.json` rejected: "matcher cannot be empty"|
|Claude-format `python "${CLAUDE_PROJECT_DIR}/probe.py"`|Hook errored, and the shell call was denied ("hook errored", fail-closed). The runner did not show why. The PC probe found `${CLAUDE_PLUGIN_ROOT}` substituted in plugin hooks, so an empty variable is not the likely cause. Repo hooks were not tested on the PC|
|`bash` on the runner|Git Bash (`C:\Program Files\Git\bin\bash.exe`), Python 3.12 as `python`|

### Windows PC probe (real machine)

Run 2026-10-03 on a Windows 11 Enterprise PC (build 26200) over SSH, against karta `main` at
`63fc7fe`. Copilot ran as `copilot -p ... --allow-all-tools --plugin-dir <probe plugin>` in
throwaway repos.

|Item|Value|
|-|-|
|Copilot CLI|1.0.92-3 (hook env reports `COPILOT_CLI_BINARY_VERSION=1.0.80`)|
|PowerShell|7.6.6 (hooks run under pwsh 7.6.6 Core)|
|Python|3.14.3|
|git|2.56.0.windows.1|
|Model|default for `-p`|

All seven scripts in `.codex-plugin/hooks/scripts/` pass `--self-test` under Python 3.14.3.

|Question|Answer|
|-|-|
|Which shell entry runs?|Only `powershell`. `bash` entries never ran|
|Plugin root env vars|`CLAUDE_PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT`, and `PLUGIN_ROOT` all set to the plugin directory. `CLAUDE_PLUGIN_DATA`/`COPILOT_PLUGIN_DATA` and `CLAUDE_PROJECT_DIR`/`COPILOT_PROJECT_DIR` are also set|
|`env` field on a hook entry|Works|
|Hook working directory|The plugin directory, not the repo. Payload `cwd` is the repo|
|`command`-only entry|Runs under pwsh, with `"${CLAUDE_PLUGIN_ROOT}"` substituted by Copilot before pwsh sees it|
|`{{project_dir}}`, `{{plugin_data_dir}}`|Not expanded in `powershell` or `command` fields; passed through literally. Use the env vars|
|File create payload|`tool_name: "Write"`, `tool_input: {path, file_text}`|
|File edit payload|`tool_name: "Edit"`, `tool_input: {path, old_str, new_str}`. Dicts, not patch strings, and no `file_path` key|
|Binder guard through the unmodified launcher, committed binder|**Allowed**, and the file changed. The guard reads only `file_path`, `notebook_path`, and `command`, so it found no target|
|Same, with a shim that renames `path` to `file_path`, ending `; exit $LASTEXITCODE`|**Denied** with exit 2. File unchanged|
|Same shim with no explicit `exit`|Copilot saw exit 1 and still denied, as "hook errored" (fail-closed). File unchanged|
|Untracked draft binder|Allowed, as intended|
|What the model sees on a deny|Only "Denied by preToolUse hook: hook exited with code 2". The guard's reason appears only in the process log and `events.jsonl`|
|Claude-format `hooks/hooks.json` (`uv run --script "${CLAUDE_PLUGIN_ROOT}/..."`)|The path expanded, but `uv.exe` (a winget link) could not start. Every hook errored. PreToolUse failed closed and blocked every matching edit, including unrelated files. SessionStart and Stop errors were logged and did not block. Not tested under a desktop login|
|Installed karta 2.30.0 plugin|Its hooks never run. Each command is a quoted string, which pwsh prints before exiting 0, so every guard allows|
|Stop guard, `{"decision":"block"}` on stdout|Blocks with exit 0 or exit 2. The reason arrives as a new user message and the `-p` turn continues|
|Stop guard, exit 1 (launcher with no explicit `exit`)|Output discarded, no block. The session ended|
|Agent response to a Stop block|It satisfied the guard by writing `refs/karta/<binder>/item-<id>/done` with `git update-ref`, without merging anything|
|Second stop after the agent acted|Passed; the once-per-state sentinel works|

|Timing (this PC)|Value|
|-|-|
|pwsh start, `-NoProfile`|~0.32 s|
|python start (`-I -S`)|~0.05 s|
|Launcher + binder guard, run directly|~0.6–0.86 s|
|Copilot PreToolUse, matched edit, launcher|~1.06 s|
|Copilot PreToolUse, matched edit, shim + launcher (two pwsh starts)|~1.5 s|
|Copilot Stop with the delivery Stop guard|0.8–1.3 s|

Copilot's timings cover every plugin's hooks for the event, including the installed 2.30.0 hooks
(about 0.25–0.3 s on their own).

## Gap list

Priority: **P0** = a karta guarantee is silently absent. **P1** = a supported Copilot path is
missing. **P2** = useful, not urgent. Effort: S under a day, M a few days, L a week or more.

|#|Gap|Priority|Effort|Windows angle|
|-|-|-|-|-|
|G1|No hooks wired for Copilot|P0|M|needs a `powershell` launcher|
|G2|Stop, SubagentStop, and PostToolUse guards signal with exit 2|P0|S|same fix on both|
|G3|Edit guards ignore patch-string `tool_input`|P0|S|`\` separators in patch paths; the Windows PC sent dicts instead (G16)|
|G4|Repo `.claude/settings.json` gates fire under Copilot, unadapted|P0|S|errored on the runner; `uv` could not start from a hook on the PC|
|G5|SessionStart status uses Claude output conventions|P1|S|none|
|G6|Three of five agents have no Copilot profile|P1|S|none|
|G7|Writer confinement has no Copilot writer to recognize|P1|M|Bash parser must also read PowerShell|
|G8|No cloud-agent support|P1|M|not applicable (Linux only)|
|G9|No guidance on Copilot instruction files|P2|S|none|
|G10|Packaging: Agent Plugins layout, marketplace pin, `copilot plugin update`|P2|S|none|
|G11|Sandbox and policy interaction undocumented|P2|S|Windows sandbox cannot deny single paths|
|G12|Windows hook latency on Copilot: measured on the PC; reduction is G20|P2|M|the whole point|
|G13|Windows real-machine verification|Done 2026-10-03, see [Windows PC probe](#windows-pc-probe-real-machine)|M|pwsh CI job still to add|
|G14|Docs describe Copilot as skills plus two reviewers|P1|S|Windows install steps|
|G15|Optional Copilot-only surfaces unused|P2|L|varies|
|G16|Edit guards read only `file_path`; Copilot sends `path` and `file_text`|P0|S|seen on the Windows PC|
|G17|Hook commands lose exit 2 without an explicit `exit $LASTEXITCODE`|P0|S|PowerShell only|
|G18|The model never sees the PreToolUse deny reason|P1|S|seen on the Windows PC|
|G19|The agent can satisfy the Stop guard by forging the done ref|P1|M|none|
|G20|About 1 s per matched tool call on Windows|P2|M|two pwsh starts with a shim|

### Priority order

Execution order within each tier. Done: G13.

**P0**

1. G3 — Patch-string `tool_input`
2. G16 — `path` and `file_text` payload keys
3. G2 — Stop, SubagentStop, PostToolUse signaling
4. G1 — Native hooks file for Copilot
5. G17 — Explicit `exit $LASTEXITCODE`
6. G4 — Repo `.claude/settings.json` gates

**P1**

1. G18 — Deny reason reaches the model
2. G5 — SessionStart output format
3. G6 — Copilot agent profiles
4. G7 — Writer confinement
5. G14 — Copilot how-to and README
6. G19 — Stop guard done-ref hardening
7. G8 — Cloud agent

**P2**

1. G20 — Windows per-call latency
2. G12 — Windows latency measurement
3. G9 — Instruction files guidance
4. G10 — Packaging
5. G11 — Sandbox and policy docs
6. G15 — Copilot-only surfaces

## P0 specification

Six changes make karta's guards run and block under Copilot on Linux and Windows. Work them in this
order. Source for every fact below is the gap sections and the probes in this doc.

### G3: Patch-string input

- **Change.** Add one shared normalizer to the Edit-family guards (`guard_binder_immutability.py`,
  `guard_pack_write.py`) in both `hooks/scripts/` and `.codex-plugin/hooks/scripts/`. A string
  `tool_input` that starts with `*** Begin Patch` becomes `{"command": <string>}` and takes the
  existing patch path, whatever `tool_name` is. Drop the `tool_name == "apply_patch"` requirement
  in the Codex twins. Normalize `\` to `/` in patch paths.
- **Acceptance.** `--self-test` cases in each script: a bare `*** Add File:` string and a
  `*** Update File:` string on a committed binder are denied; the same on an untracked draft is
  allowed; a patch path with `\` separators matches.
- **Depends on.** None.
- **Done when.** Every Edit-family guard denies a patch-string edit of a committed binder.

### G16: `path` and `file_text` keys

- **Change.** In the same normalizer, map `path` to `file_path`, `file_text` to `content`, and
  `old_str`/`new_str` to `old_string`/`new_string`. Check each Edit-family guard for other keys it
  reads.
- **Acceptance.** `--self-test` cases with `{path, file_text}` and `{path, old_str, new_str}`
  payloads: committed binder denied with exit 2, untracked draft allowed. Existing `file_path`
  cases still pass.
- **Depends on.** G3 (shares the normalizer).
- **Done when.** The Windows PC result (guard allowed a committed-binder edit) reproduces as a
  deny in `--self-test`.

### G2: Stop, SubagentStop, PostToolUse signaling

- **Change.** `hooks.json` (G1) points at the Codex twins in `.codex-plugin/hooks/scripts/`.
  Confirm `guard_delivery_stop.py` and `guard_subagent_whiff.py` there print
  `{"decision":"block","reason":...}` and exit 2 together. Make `guard_pack_write.py` emit
  `hookSpecificOutput.additionalContext` for PostToolUse findings. Check that stdout holds one JSON
  object and nothing else. The Claude originals in `hooks/scripts/` keep exit 2, which blocks on
  Claude.
- **Acceptance.** `--self-test` cases: a blocking Stop payload yields exactly one JSON object with
  `"decision":"block"` on stdout and exit 0 or 2; a clean payload yields no block. A PostToolUse
  finding yields `additionalContext`. A test asserts stdout parses as a single JSON object.
- **Depends on.** None for the scripts; G1 for the live run.
- **Done when.** A Stop block from the delivery guard continues the Copilot session.

### G1: Native hooks file

- **Change.**
  - Add `.github/plugin/hooks.json`: `{"version": 1, "hooks": {...}}`, PascalCase events
    `SessionStart`, `PreToolUse`, `Stop`, `SubagentStop`, `PostToolUse`.
  - Matchers: `Write|Edit|NotebookEdit` for the Edit-family guards, `Bash` for shell guards,
    `Agent|Task` for dispatch guards.
  - Every guard gets a `bash` entry (`sh -c` with `$PLUGIN_ROOT` and a file-exists check, as in
    `.codex-plugin/hooks/hooks.json`) and a `powershell` entry:
    `& "$env:PLUGIN_ROOT\.codex-plugin\hooks\launch_hook.ps1" <guard> Plugin; exit $LASTEXITCODE`.
  - Set the `hooks` field in `.github/plugin/plugin.json` to that file, replacing `{}`. Do not
    point it at the Claude `hooks/hooks.json`.
  - Replace `test_copilot_plugin_selects_native_profiles_and_no_claude_hooks`
    (`tests/test_codex_gate_models.py:100`) with the manifest test under G17.
- **Acceptance.**
  - Unit test: every guard is listed, every entry has `bash` and `powershell`, no `powershell`
    string contains `${`.
  - Windows (zbook): `copilot -p --allow-all-tools --plugin-dir` in a throwaway trusted repo. An
    edit of a tracked `.karta/binders/test.json` is denied and the file is unchanged. An untracked
    draft binder is allowed. SessionStart and Stop hooks fire.
  - Linux: the same run with `copilot -p`.
- **Depends on.** G16 and G17.
- **Done when.** The smoke test passes on Linux and on the zbook.

### G17: Explicit exit code

- **Change.** Every `powershell` entry in `hooks.json` ends with `; exit $LASTEXITCODE`. Add the
  check to the manifest test. `launch_hook.ps1` already passes the guard's code through.
- **Acceptance.** Manifest test: every `powershell` string ends with `exit $LASTEXITCODE`. On the
  zbook, a guard that exits 2 reaches Copilot as 2 (pwsh propagates it), and a Stop guard printing
  `{"decision":"block"}` blocks.
- **Depends on.** None.
- **Done when.** No entry can report 1 where the guard exited 2.

### G4: Repo gates

- **Change.** Add `.github/hooks/karta-repo.json` in the native schema with `bash` and
  `powershell` entries for `precommit_gate.py` (timeout 900) and `roundtable_gate.py` (timeout
  600). Use `$env:COPILOT_PROJECT_DIR`, not `{{project_dir}}`. End each `powershell` entry with
  `; exit $LASTEXITCODE`. In `.claude/settings.json`, either make `command` work in both shells or
  document `disableAllHooks` for that source, so the gates do not fire twice. Test the choice on
  Windows.
- **Acceptance.** Under Copilot in a trusted karta checkout on the zbook, a shell call runs the
  gates once and is not denied by a `uv` start failure. Linux: the gates run once.
- **Depends on.** G1.
- **Done when.** A Windows contributor can run Copilot in a karta checkout without every shell
  call failing closed.

### Summary

|Gap|Files|Acceptance check|Depends on|
|-|-|-|-|
|G3|`hooks/scripts/` and `.codex-plugin/hooks/scripts/` Edit-family guards|`--self-test`: patch strings, `\` paths|none|
|G16|same normalizer|`--self-test`: `{path, file_text}`, `{path, old_str, new_str}`|G3|
|G2|`.codex-plugin/hooks/scripts/` Stop, SubagentStop, pack-write guards|`--self-test`: one JSON object, `"decision":"block"`|none (live run: G1)|
|G1|`.github/plugin/hooks.json`, `.github/plugin/plugin.json`, `tests/test_codex_gate_models.py`|zbook and Linux `copilot -p` smoke test|G16, G17|
|G17|`.github/plugin/hooks.json`, manifest test|exit 2 reaches Copilot as 2; Stop block works|none|
|G4|`.github/hooks/karta-repo.json`, `.claude/settings.json`|gates run once, no `uv` denial on the zbook|G1|

### G1 (P0) — No hooks wired for Copilot

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
hooks get `{{project_dir}}` and `{{plugin_data_dir}}` template variables [changelog]. The Windows
PC probe found them not expanded in `powershell` or `command` fields on 1.0.92-3, so use
`$env:COPILOT_PROJECT_DIR` and `$env:COPILOT_PLUGIN_DATA` instead.

**Fix.** Ship `.github/plugin/hooks.json` in the native schema and point the manifest at it. Do not
point it at the Claude `hooks/hooks.json`, because of G2, G3, and the Windows PC result: that
manifest's `uv run` command could not start, so every PreToolUse hook failed closed.

- PascalCase events, so the guards keep receiving Claude-shaped payloads.
- `bash`: `sh -c` with `$PLUGIN_ROOT` and a file-exists check, the same shape as
  `.codex-plugin/hooks/hooks.json`.
- `powershell`:
  `& "$env:PLUGIN_ROOT\.codex-plugin\hooks\launch_hook.ps1" <guard> Plugin; exit $LASTEXITCODE`.
  Reuse the Codex launcher. It passes the guard's exit code through and fails open only on launcher
  errors (verified on Windows in 2.38.2, and run under Copilot on the Windows PC). The trailing
  `exit` is required; without it Copilot sees 1, not 2 (G17).
- Matchers in Claude tool names: `Write|Edit|NotebookEdit`, `Bash`, `Agent|Task`.
- Script source: the Codex twins in `.codex-plugin/hooks/scripts/`, not the Claude originals. They
  already emit JSON decisions and parse patch bodies. G3 and G16 cover the input shapes they miss.

**Open question.** The docs do not say whether `exec` + `args` expands `${PLUGIN_ROOT}` or the
`{{...}}` templates. If it does, one entry with no shell serves both platforms and skips PowerShell
start-up (G20). Probe it before choosing; the Windows PC probe did not test `exec`.

### G2 (P0) — Stop, SubagentStop, and PostToolUse guards signal with exit 2

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

### G3 (P0) — Edit guards ignore patch-string `tool_input`

**Now.** `hooks/scripts/guard_binder_immutability.py:89` and `guard_pack_write.py:97` treat a
non-dict `tool_input` as nothing to check and allow it. The Codex twins parse patches, but only
from `tool_input["command"]` (`.codex-plugin/hooks/scripts/guard_binder_immutability.py:153`,
`guard_pack_write.py:106`, which also requires `tool_name == "apply_patch"`).

**Copilot does.** With the default model, a file create arrived as `tool_name: "Edit"` and
`tool_input: "*** Begin Patch\n*** Add File: b.txt\n+x\n*** End Patch\n"` (probed on Linux). On
the Windows PC, creates and edits arrived as dicts keyed `path` instead (G16). The guards must
accept both.

**Fix.** One normalizer shared by the Edit-family guards: if `tool_input` is a string starting with
`*** Begin Patch`, treat it as `{"command": <string>}` and take the existing patch path, whatever
the `tool_name`. Add self-test cases for the bare-string shape. Patch paths may use `\` on Windows,
so extend the path matcher to normalize separators and cover it with a test.

### G4 (P0) — Repo `.claude/settings.json` gates fire under Copilot, unadapted

**Now.** karta's own `.claude/settings.json` runs `precommit_gate.py` (timeout 900) and
`roundtable_gate.py` (timeout 600) on Bash, through
`uv run --script "${CLAUDE_PROJECT_DIR}/scripts/hooks/..."`.

**Copilot does.** It reads `.claude/settings.json` and `.claude/settings.local.json` as repo hook
sources [hooks-ref]. The probe confirmed they fire once the folder is trusted. On Linux this works
by accident: the payload is Claude-shaped and `CLAUDE_PROJECT_DIR` is set. On Windows the `command`
is copied to `powershell` *(docs)*. On the runner, a Claude-format repo hook errored and the shell
call was denied (fail-closed). On the Windows PC, Copilot substituted `${CLAUDE_PLUGIN_ROOT}` in
plugin hooks before pwsh ran, so an empty variable is probably not the cause there, but `uv` could
not start from a hook and every PreToolUse hook failed closed. Repo hooks were not tested on the
PC. Either way, a Windows contributor running Copilot in a karta checkout could have every shell
call denied.

**Fix.** Add `.github/hooks/karta-repo.json` in the native schema with `bash` and `powershell`
entries for both gates, using `$env:COPILOT_PROJECT_DIR` (the PC probe found `{{project_dir}}`
not expanded) and ending each `powershell` entry with `; exit $LASTEXITCODE`. Then stop the
Claude file firing a second time under Copilot. Two options: make its `command` work in both
shells, or tell Copilot users to set `disableAllHooks` for that source. Pick one and test it on
Windows (G13). Also note that a hook timeout fails **open** on Copilot, unlike Claude, so a gate
that runs past its timeout lets the commit through.

### G5 (P1) — SessionStart status uses Claude output conventions

**Now.** `inject_karta_status.py` writes its summary to stdout as plain text, which Claude and
Codex add to context.

**Copilot does.** Stdout on exit 0 is parsed as JSON [hooks-ref]. sessionStart accepts
`additionalContext`. subagentStart also accepts `additionalContext` and cannot block. That could
hand a dispatched gate reviewer its binder context without relying on the dispatcher's prompt.

**Fix.** Under Copilot (detect `COPILOT_PLUGIN_ROOT`), emit
`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":...}}`. Probe whether
plain text is dropped or shown. Consider a SubagentStart hook for the two gate agents later.

### G6 (P1) — Three of five agents have no Copilot profile

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

### G7 (P1) — Writer confinement has no Copilot writer to recognize

**Now.** `guard_writer_confinement.py` exists only on Claude. It needs to know which subagent is
writing. Codex skipped it for the same reason (`docs/how-to/codex.md`, confinement row).

**Copilot does.** Dispatch and SubagentStop payloads carry `agent_type` (probed). Whether a
PreToolUse payload from inside a subagent names that subagent is **not verified**.

**Fix.** Probe that first. If it does, wire the guard once G6 ships doc-gardner and kaizen. Its
static Bash write parsing assumes a POSIX shell. On Windows, Copilot's `powershell` tool is also
reported as `Bash`, so the parser needs PowerShell write forms (`Set-Content`, `Out-File`, `>`), or
the guard must fail closed on PowerShell it cannot parse for confined writers.

### G8 (P1) — No cloud-agent support

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

### G9 (P2) — No guidance on Copilot instruction files

Copilot reads `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md`,
AGENTS.md, and CLAUDE.md with `@` imports. karta's skills carry the doctrine, so the risk is low.
The how-to should say which file a Copilot user puts karta pointers in, and confirm karta's own repo
instructions reach Copilot sessions.

### G10 (P2) — Packaging

The plugin reference describes the Agent Plugins 1.0/1.1 layout: a root `plugin.json` with
`$schema` takes precedence, and client-specific pieces go under `com.github.copilot/` (agents,
commands, rules, `hooks/hooks.json`, `lsp.json`) [plugin-ref]. Marketplace entries in
`.github/plugin/marketplace.json` or `.claude-plugin/marketplace.json` support `sha` pinning,
`copilot plugin update`, and `autoUpdate`. karta's legacy `.github/plugin/plugin.json` path still
works, so nothing is forced. Record the decision, and add `copilot plugin update` to the upgrade
steps in the how-to.

### G11 (P2) — Sandbox and policy interaction undocumented

Copilot runs hooks inside its sandbox when one is on. Hooks can read their own directory and write
to `$COPILOT_PLUGIN_DATA` [hooks-ref]. karta guards shell out to `git` and `uv`, and
`inject_karta_status.py` starts the Karta Watch hub with `serve_status.py --ensure`. Expect the
sandbox to block that spawn, as the Codex sandbox can. On Windows the sandbox cannot deny single
paths. Enterprise policy hooks (`/etc/github-copilot/policy.d/`, or
`C:\ProgramData\GitHub\Copilot\policy.d\` and `HKLM\Software\Policies\GitHub\Copilot` on Windows)
load before karta's and can set `disableAllHooks`. Document both, and have the status surface say
when the hub spawn was denied.

### G12 (P2) — Windows hook latency on Copilot

Measured on the Windows PC (see [Windows PC probe](#windows-pc-probe-real-machine)): about 1.06 s
per matched PreToolUse through the launcher and 0.8–1.3 s per Stop. Backlog entry 29 covers the
same layers on Claude and Codex. The work to bring it down is G20.

### G13 (done) — Windows real-machine verification: done

**Done 2026-10-03.** One run on a real Windows 11 PC under Copilot 1.0.92-3 exercised the launcher,
the binder guard, and the Stop guard. Results are in
[Windows PC probe](#windows-pc-probe-real-machine); the problems it found are G16–G20. The test work
below is still open and now belongs to G1's definition of done.

- Replace the "no Claude hooks" assertion with one that checks the native Copilot hooks file: every
  guard is listed, every entry has both `bash` and `powershell`, no `powershell` string contains
  `${`, and every `powershell` string ends with `exit $LASTEXITCODE`.
- Add self-test cases for the payload shapes in the probe tables, especially the bare-string patch
  (Linux) and the `path`/`file_text` dict (Windows PC).
- Add an opt-in live smoke test (needs a Copilot login): run `copilot -p` in a temp trusted repo
  with `--plugin-dir`, attempt a binder write, and assert the deny. Run it on Windows too. Until it
  passes on Windows with the G1, G16, and G17 fixes in place, G1–G4 are "wired", not "enforced".
- Windows harness options: a `windows-latest` GitHub Actions job authenticates with the
  workflow's `GITHUB_TOKEN` and `copilot-requests: write` (proven 2026-10-03), but needs karta
  mirrored to a private GitHub repo. A tailnet Windows PC with OpenSSH lets the test driver run
  the same smoke test over `ssh`, with no code leaving the network (proven 2026-10-03). `pwsh` on
  Linux checks PowerShell syntax only: Copilot runs the `bash` entry there, and paths, Python, and
  process start-up stay Linux.

### G14 (P1) — Docs describe Copilot as skills plus two reviewers

`docs/how-to/copilot-cli.md` needs the enforcement table `codex.md` has (Rule / Claude Code /
Copilot), a Windows section, and the trusted-folder requirement for repo hooks. The README support
matrix should match.

### G15 (P2) — Optional Copilot-only surfaces

Not parity, but available: `/fleet` and background subagents for karta-deliver wave dispatch; LSP
config with `bash` and `powershell` keys; extensions and canvases (a Karta Watch canvas could
replace the browser tab); `/every` and `/after` for scheduled re-checks; `errorOccurred` and
`postToolUseFailure` events for richer failure reports; `--acp` for editor hosts. Pick these up
only after G1–G14.

### G16 (P0) — Edit guards read only `file_path`; Copilot sends `path`

**Now.** `guard_binder_immutability.py` reads `tool_input.file_path`, `notebook_path`, and
`command`. The other Edit-family guards likely read the same keys; check each.

**Copilot does.** On the Windows PC, `Write` sent `{path, file_text}` and `Edit` sent
`{path, old_str, new_str}`. The unmodified binder guard allowed an edit to a committed binder. Given
a Claude-shaped payload, the same launcher and guard denied it with exit 2.

**Fix.** In the shared normalizer from G3, map `path` to `file_path`, `file_text` to `content`, and
`old_str`/`new_str` to `old_string`/`new_string`. Add self-test cases for both shapes. Re-check the
Linux payload, which the Linux probe saw as a patch string.

### G17 (P0) — Hook commands lose exit 2 without an explicit `exit`

**Copilot does.** In a `powershell` entry, `& script.ps1` with no top-level `exit` reports 1 when
the script exits 2 (Windows PC). PreToolUse then denies as "hook errored", which hides the cause.
Stop discards stdout on exit 1, so a JSON block is lost and the session ends.

**Fix.** End every `powershell` entry with `; exit $LASTEXITCODE`. Add the check to the G13
manifest test.

### G18 (P1) — The model never sees the PreToolUse deny reason

**Copilot does.** On an exit-2 deny, the model gets only
`{"message":"Denied by preToolUse hook: hook exited with code 2","code":"denied"}` (Windows PC). The
guard's stderr appears only in the process log and in `hook.end.error` in `events.jsonl`, so the
agent cannot tell a binder rule from a broken hook. The Linux probe saw the reason reach the model
when the hook printed `hookSpecificOutput.permissionDecision: "deny"` with a reason.

**Fix.** Have the Edit-family and dispatch guards print that JSON on stdout as well as exiting 2,
and confirm on Windows that the reason reaches the model. Stdout must hold one JSON object only
(G2).

### G19 (P1) — The agent can satisfy the Stop guard by forging the done ref

**Copilot does.** After the delivery Stop guard blocked on the Windows PC, the agent ran
`git update-ref refs/karta/wip/item-b/done HEAD` without merging anything and reported the delivery
fixed. On the next block it archived the binder and committed. The guard accepted the hand-written
ref. Nothing here is Copilot-specific; an agent on any host can do the same.

**Fix.** Harden the guard: accept a done ref only if it points at a commit that contains the item's
built work, or was written by karta's own landing step. Word the block reason so it names the
landing step, not the ref.

### G20 (P2) — About 1 s per matched tool call on Windows

**Copilot does.** On the Windows PC, a matched PreToolUse took about 1.06 s through the launcher
and 1.5 s with a shim in front (two pwsh starts). pwsh start-up alone is about 0.32 s; Python is
about 0.05 s. Stop took 0.8–1.3 s.

**Fix.** Do the `path` mapping in Python (G16), not in a pwsh shim, so each hook starts pwsh once.
Then try the `exec` + `args` form (G1 open question) to skip pwsh, and apply the layer work from
backlog entry 29.

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

Same sequence as the [Priority order](#priority-order).

1. P0: G3, G16, G2, G1, G17, G4. Scripts first, testable on Linux; then the native hooks file and
   the G13 unit tests; then the repo gates. Details in the [P0 specification](#p0-specification).
2. Repeat the Windows run with the fixes in place (G13 smoke test, G20 bench). Only then mark
   guards "Enforced" in the doc (G14).
3. P1: G18, G5, G6, G7, G14, G19. G8 when a user wants the cloud agent.
4. P2: G20, G12, G9, G10, G11, G15.

[hooks-ref]: https://docs.github.com/en/copilot/reference/hooks-reference
[plugin-ref]: https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference
[changelog]: https://github.com/github/copilot-cli/blob/main/changelog.md
