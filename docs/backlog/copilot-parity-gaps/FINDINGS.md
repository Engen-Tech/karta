# Copilot parity gap analysis — karta 2.38.3

Subject: what karta ships for GitHub Copilot (CLI and cloud agent) against what Copilot can now do,
with Windows treated as a first-class host. Baseline commit `0dc65d9`. Researched 2026-10-03
against Copilot CLI **1.0.92** (changelog head: 1.0.91, 2026-10-01). Status last updated
2026-10-04 at main `f12650d`.

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
`path` and `file_text` as well as `file_path`. After that, smaller items remain: the deny reason
does not reach the model (G18), the agent can get past the Stop guard by writing the done ref itself
(G19), each matched tool call costs 0.6 to 1.6 s of hook time on Windows (G20), and the SessionStart
status does not reach the model because Copilot drops plain text (G5). The rest of the list is
packaging, coverage, and docs.

**Raised to P0 on 2026-10-04.** Three gaps found in the re-verification: the reviewer profiles set
effort with a key Copilot ignores, so the reviewers run at the default effort (G21); archiving a
binder takes it out of the delivery Stop guard's scope (G22); and a machine with karta installed
runs two copies of its hooks when a second tree is loaded with `--plugin-dir` (G23).

## What the probe established

|Question|Answer|Source|
|-|-|-|
|Do Claude-format plugin hooks (`hooks/hooks.json`, PascalCase events) run?|Yes: SessionStart, PreToolUse, Stop, SubagentStop|probed|
|Payload shape for PascalCase events|Claude-style snake_case: `hook_event_name`, `tool_name`, `tool_input`, `cwd`, `session_id`, `transcript_path`|probed|
|Tool names in the payload|Claude names: shell → `Bash`, task → `Agent`, file create → `Edit` (Linux; the Windows PC sent `Write` for a create)|probed|
|Bash `tool_input`|`{command, description}`, same as Claude|probed|
|Agent `tool_input`|`{description, prompt, agent_type, name, mode}`. Added 2026-10-04: `agent_type` is namespaced by plugin in the dispatch and SubagentStop payloads, for example `g7probe:probe-writer`|probed|
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

## Re-verification (2026-10-04)

Scope: every non-P0 finding (G5 to G15, G18 to G20, and the pitfalls list). The P0 material written
before this pass is unchanged. G21, G22, and G23 were found in this pass and raised to P0 the same
day. The 2026-10-03 probe tables are unchanged apart from one dated note on the Agent `tool_input`
row. Corrections to them sit in the Corrected table below, each beside the old statement. Each
non-P0 gap section ends with a dated "Re-verified" line that names the method. The few claims that
could not be checked, and why, are in the "Not checked" table.

Systems and versions:

- Copilot CLI 1.0.92-3 on Linux (Debian), in throwaway repos with probe plugins laid out like
  karta's native plugin and loaded with `--plugin-dir`. G7: 2 runs on model `gpt-6-luna`. G5 and G6:
  9 runs on model `auto`, which resolved to `mai-code-1.1-flash`. Deny-reason variants, subagent
  hooks, subagent instructions, hook order, and instruction files: 9 runs on `auto`, which resolved
  to `gpt-6-luna` in seven and `mai-code-1.1-flash` in two. One run per variant. Effort key on
  task-tool dispatch (G21): 4 invocations, 3 of which reached a model, with every dispatched agent
  served by `mai-code-1.1-flash`. Other globally enabled plugins on the machine and a user-level
  `sessionStart` hook were active in every Linux run. Each result rests on the probe hook's own
  dumps, on a per-run token only the probe could produce, or, for the effort key, on the reasoning
  effort recorded in the request log.
- The Windows PC (`zbook`), Windows 11 Enterprise build 10.0.26200 (Intel i5-1135G7, 8 logical
  cores, 32 GB), Copilot CLI 1.0.92-3 started over `ssh` from the real executable in the WinGet
  package folder (the WinGet link fails over `ssh` with "The path cannot be traversed because it
  contains an untrusted mount point"). Hooks ran under PowerShell 7.6.6 through the `powershell`
  entry, with Python 3.14.3. The probe plugin was in karta's native layout, and its edit guard and
  SessionStart hook were routed through an unmodified copy of `.codex-plugin/hooks/launch_hook.ps1`
  from `f12650d`. Probe runs: A1 to A4 (deny variants), B1 and B2 (SessionStart output), C1 and C2
  (subagent dispatch), one run each. Timing: six Copilot runs (r1 to r6) and 12-sample direct
  measures. Four other plugins were loaded in every PC run: a live-installed `karta@karta` 2.38.4,
  `roundtable`, `convert-to-md`, and `gem-team`. Only karta's hooks ran among them (8 loaded; the
  other three add none). The deny and SessionStart verdicts rest on per-run tokens in the probe's
  own output, and the subagent payload verdicts on the probe's own dumps, so karta's hooks do not
  move them.
- Official docs, read as raw markdown from the `github/docs` repository: hooks reference, CLI plugin
  reference, custom agents configuration, CLI command reference, custom instructions, the cloud
  agent environment page, and the CLI changelog through 1.0.91 (2026-10-01). Also `copilot --help`,
  `copilot help sandbox`, and the GitHub REST API for copilot-sdk issue 782.
- The karta repo at `f12650d` (2.38.4), read, with the guards, self-tests, and unit tests run on
  Linux under Python 3.13. No Copilot session ran for that part. The title and baseline above stay
  as the 2026-10-03 record (2.38.3, `0dc65d9`).
- No cloud agent run. The repo remote is a self-hosted Forgejo, so G8 rests on the docs.

### What the Linux runs showed

- **Deny reason (G18).** An exit-2 deny reaches the model as `Denied by preToolUse hook: hook exited
  with code 2`, with the guard's stderr only in the debug log. Exit 0 with deny JSON on stdout
  (wrapped `hookSpecificOutput` form or flat `permissionDecision`) delivers the reason. Exit 2 with
  the same JSON drops it. Same for `Edit` and `Agent` dispatch.
- **SessionStart (G5).** Flat `additionalContext` reaches the model. Plain text and the wrapped
  `hookSpecificOutput` form do not.
- **SubagentStart (G5).** A PascalCase `SubagentStart` with no matcher fired on both dispatches, and
  so did the native `subagentStart`. Their flat `additionalContext` reached both subagents ahead of
  the dispatch prompt. This points to the matcher: the registration that did not fire used `"*"`,
  which the docs treat as an invalid regular expression for that event.
- **Subagent instructions (G6).** For a dispatched plugin agent, `include-custom-instructions: true`
  put AGENTS.md in its system prompt, and the same agent without the key did not get it. The
  dispatch prompts were identical. AGENTS.md only.
- **Effort key (G21).** With `--agent` selection and with task-tool dispatch, `reasoning-effort` was
  applied and `reasoningEffort` was ignored. Under the kebab-case key, `xhigh` went out as medium on
  a model that offers only low, medium, and high.
- **Instruction files (G9).** With no probe plugin, a session read
  `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md` (with `applyTo:
  "**"`), AGENTS.md, CLAUDE.md, and a file that CLAUDE.md pulls in with an `@path` line. The
  `.instructions.md` text follows the other blocks raw, frontmatter not stripped.
- **Hook order.** Matched PreToolUse hooks run in registration order, about 20 ms apart. A deny from
  the first stops the rest for that call.
- **Tool names.** A file create arrived as `Edit` with a raw apply_patch string (`gpt-6-luna`) or as
  `Write` with a `path` dict (`mai-code-1.1-flash`).

### What the Windows PC runs showed, through PowerShell

- **Deny reason (G18).** The same four results as on Linux, one run per variant: exit 2 gives the
  generic text, exit 0 with either JSON form gives the reason, exit 2 with JSON gives the generic
  text. `hook.end` in `events.jsonl` has `success: true` and the generic text only, with no error
  key (1.0.92-3 and the bundled 1.0.80).
- **SessionStart (G5).** Flat `additionalContext` from a `powershell` entry reached the model and
  the model repeated it. Plain text did not. Plain plus wrapped, with no flat hook to compete, left
  the model with nothing. The wrapped form alone was not isolated on the PC. The installed karta's
  own status hook also ran, and its plain `<karta-status>` block showed only in the debug log.
- **Subagents (G7).** A PreToolUse payload inside a subagent has no agent name. `session_id` is the
  child's id, which SubagentStop later reports as `agent_id`. A PascalCase `SubagentStart` fired
  with matcher `.*` and with no matcher. Stop fired for the subagent's turn with the child's
  `session_id` and the parent's `transcript_path`.
- **Tool names.** Under PascalCase registration, PowerShell shell calls arrive as `Bash` with
  `{command, description}`. File create and edit arrived as `Edit` with a raw apply_patch string
  (`gpt-6-luna`), on the main thread and inside a subagent. On 2026-10-03 the PC sent dicts in one
  run and patch strings in another; the 2026-10-04 runs add the subagent case and tie the patch
  string to `gpt-6-luna`.
- **Installed plugin.** `karta@karta` 2.38.4 is live on the PC, and its hooks run (8 hooks loaded).
  The 2.30.0 statements in the 2026-10-03 tables no longer describe this PC.
- **Duplicate load.** Adding `--plugin-dir` for a second karta tree loads a second copy of its
  hooks (16 hooks loaded), and both run in sequence (G23).
- **Version flag.** `copilot.exe --version` reports 1.0.92-3, but `--no-auto-update` runs the older
  bundled 1.0.80 (r1 used it).

**Timing on the Windows PC.** Each row sets the 2026-10-04 value beside the 2026-10-03 one. Direct
measures are medians of 12 samples after one warm-up. Copilot rows come from `hook.start` and
`hook.end` pairs in `events.jsonl`. One machine, indicative, not a benchmark. CPU was 4 to 8% before
the runs.

|Item|2026-10-03|2026-10-04|
|-|-|-|
|pwsh start, `-NoProfile`|~0.32 s|0.23 s (0.22 to 0.25 s)|
|python start (`-I -S`)|~0.05 s|0.02 s (18 to 20 ms)|
|Launcher + binder guard, run directly|~0.6 to 0.86 s|0.56 s (0.55 to 0.58 s); the guard alone under Python is 0.16 s|
|Copilot, matched edit that a guard denies (one pwsh start)|not measured|0.57 s (0.569 to 0.577 s, 4 runs, one on 1.0.80)|
|Copilot PreToolUse, matched edit that is allowed|~1.06 s (one hook, launcher)|1.07 s (two pwsh starts: binder guard, then pack-write guard), one run|
|Copilot PostToolUse for that allowed edit|not measured|0.52 s (one pwsh start), same run|
|Copilot PreToolUse, shim + launcher (two pwsh starts)|~1.5 s|not re-measurable: `f12650d` has no shim|
|Copilot Stop, clean repo, one karta copy loaded|0.8 to 1.3 s|0.59 to 0.61 s (3 runs, one on 1.0.80)|
|Copilot Stop, clean repo, two karta copies loaded|not measured|1.17 to 1.22 s (3 runs)|
|Copilot SessionStart, one karta copy loaded|not measured|1.33 to 1.45 s (2 runs); it starts the hub|

The allowed-edit run had a process watcher running, which added about 0.09 s (a denied edit measured
0.665 s with the watcher and 0.569 to 0.577 s without it). Only the denied-edit, Stop, and
SessionStart figures have repeats. A blocking Stop does more git work and was not measured. Copilot
starts each hook as `pwsh.exe -nop -nol -c "<powershell entry>"`, one after another, never in
parallel. The launcher starts Python twice per run. Copilot also starts two `pwsh -NoProfile
-NonInteractive` processes at session start to resolve the PowerShell version.

### What the repo and guard runs showed

- **G19.** Both Stop guard copies were run against fixture repos. The result is in the G19 section.
- **G6.** `.github/agents/` holds two profiles. The generator hard-codes those two and rejects any
  tool outside `Read`, `Glob`, `Grep`, and `Bash`.
- **G13.** The manifest test and the payload-shape self-tests exist and pass. The live smoke test
  and any CI do not exist.
- **G14.** `docs/how-to/copilot-cli.md` still says no karta hook runs on Copilot. The README has no
  support matrix.
- **G5, G7, G10, G11.** What karta's scripts emit and wire today was read; see each section.

**Corrected**

|Gap|Old statement|Now|Checked by|
|-|-|-|-|
|Probe tables (tool names and payloads)|The Windows PC sent `Write {path, file_text}` and `Edit {path, old_str, new_str}`|The 2026-10-03 record already shows patch strings from the PC in one run. On 2026-10-04, same PC, auto mode (`gpt-6-luna`): create and edit arrived as `Edit` with a raw apply_patch string, on the main thread and in a subagent, which adds the subagent case and ties the string to the model. Linux: `Edit` with a patch string (`gpt-6-luna`) or `Write` with a `path` dict (`mai-code-1.1-flash`). The payload shape depends on the model, so the guards must take both|Live, Linux and PC|
|Probe table, PascalCase payload shape|Payloads carry `transcript_path`|PreToolUse carries no `transcript_path`, on the main thread or in a subagent. Stop and SubagentStop do, pointing at the parent's `events.jsonl`|Live, Linux|
|Verdict, PC probe table|The installed karta 2.30.0 plugin's hooks never run on Windows|No 2.30.0 on the PC on 2026-10-04. A live karta 2.38.4 is installed, its hook files match `f12650d`, and they run. Today's inventory cannot show what was installed on 2026-10-03|Live, PC|
|Timing note, PC probe|Copilot's timings include the installed 2.30.0 hooks (about 0.25 to 0.3 s)|On 2026-10-04 the only plugin with hooks on the PC is karta 2.38.4. With `--plugin-dir` also loading karta, 1.0.92-3 runs both copies in sequence, which doubles Stop and SessionStart time|Live, PC|
|G18 section; probe table deny rows|G18 section: the model sees `{"message":...,"code":"denied"}`. Probe table: the reason is in the process log and `events.jsonl`; the Claude `permissionDecision` JSON is not yet tried on Windows|The model receives the bare string. The `{message, code}` object is only the `events.jsonl` record. The stderr reason is only in the debug log, and `hook.end` has no error key. The JSON works on Windows too, but only with exit 0|Live, Linux and PC|
|G18|Fix: print the deny JSON as well as exiting 2|Exit 2 drops stdout, so the reason does not arrive. Deny with exit 0 and the JSON|Live, Linux and PC|
|G5|Fix: under Copilot, emit the wrapped `hookSpecificOutput` JSON, and probe whether plain text is dropped|Flat `{"additionalContext": ...}` reaches the model. The wrapped form and plain text do not (SessionStart only). The wrapper alone was isolated on Linux. On the PC it was seen only beside other hooks|Live, Linux and PC; docs|
|G5|Consider a PascalCase `SubagentStart` hook for the two gate agents later|It fired with no matcher on Linux and the PC, and with `.*` on the PC. Its `additionalContext` reaches the subagent (Linux). The earlier non-fire points to the matcher `"*"`, which the docs treat as an invalid regular expression. A PascalCase `SubagentStop` still fired for the general-purpose agent in the first Linux run, against the docs|Live, Linux and PC; docs|
|G6|Plugin agents take `model`, `reasoningEffort`, `tools`, `mcp-servers`, per the plugin reference|The plugin reference documents only `mcp-servers`. Copilot read `reasoning-effort` and ignored `reasoningEffort`, with `--agent` selection and with task-tool dispatch (G21)|Live, Linux; docs|
|G6|`include-custom-instructions: true` lets an agent read the repo instruction files|Applies to an agent dispatched as a subagent (default false). A session agent already gets the files. Live for a dispatched agent and AGENTS.md: present with the key, absent without|Docs; live, Linux|
|G6|Fix: extend the generator to emit all five and give the writers the edit tool|The generator hard-codes the two names and exits on `Edit`, `Write`, and `Skill`, so the fix also needs a tool mapping, a `Skill` decision, and a relaxed read-only check for the writers|Repo|
|G7|Whether a subagent's PreToolUse payload names the subagent: not verified|It does not, on Linux (2 runs) and the PC (2 runs). `session_id` is the child's id, so a hook can tell a subagent write but not which subagent|Live, Linux and PC|
|G8|The cloud agent runs in a Linux sandbox|Linux by default. A repo can choose a Windows runner, where the firewall does not work|Docs|
|G8|Only `bash` or `command` entries run; no `.claude` settings; permissionRequest never fires|HTTP hooks are also documented and prompt hooks may not fire. The only hook configuration present by default is `.github/hooks/*.json`, and the docs name `.claude` settings only for the CLI. permissionRequest "either does not fire or has no effect"|Docs|
|G9|`@` imports work in Copilot instruction files|Not in `*.instructions.md`. Repo paths only. Live: they work in CLAUDE.md|Docs; live, Linux|
|G10|Marketplace entries support `sha`, `copilot plugin update`, and `autoUpdate`|`sha` pins a plugin source. Update is a CLI command. `autoUpdate` is a user or managed setting; a repo setting is ignored|Docs|
|G11|Enterprise policy hooks can set `disableAllHooks`|Unsupported. The docs say `disableAllHooks` cannot turn policy hooks off. They never say a policy file can set it to switch off other sources' hooks, and the flag in one hooks file skips only that file. Managed settings have other switches that can stop karta's hooks: `allowManagedHooksOnly`, and a managed `enabledPlugins` value that wins over local settings|Docs|
|G11|karta guards shell out to `git` and `uv`|On Copilot they run `python3` directly and call `git`. `uv` is only in the Claude launcher|Repo|
|G11|Hooks run inside the sandbox and can write `$COPILOT_PLUGIN_DATA`|Policy hooks run on the host. Only plugin hooks write the data directory. The sandbox is off by default and experimental, and `/sandbox` exists only with experimental features or a managed policy|Docs; `copilot help sandbox`|
|G11|On Windows the sandbox cannot deny single paths|Only source is changelog 1.0.76, in a parenthetical. No later entry reverses it, but `copilot help sandbox` lists `deniedPaths` with no Windows caveat. Not run|Changelog; `copilot help sandbox`|
|G12|Backlog entry 29 covers the same layers on Claude and Codex|It also costs Copilot's skill-driven `uv run` chain, but predates karta's Copilot hooks. `docs/backlog/windows-latency/FINDINGS.md` still says Copilot has no hooks|Repo|
|G15|A Karta Watch canvas could replace the browser tab|Canvases exist but are extension-driven. Whether this works is untested and undocumented|Docs|
|G15|`/every` and `/after` for scheduled re-checks|They were experimental when introduced and may be gated per user|Docs|
|G19|The agent forged the done ref and the guard accepted it|With its status engine loadable, both guard copies reject a forged ref once per state. Without the engine they trust it silently, which reproduces the Windows sequence. Other ways past the guard exist (G19, G22)|Guard runs, Linux|
|G20|A matched PreToolUse takes about 1.06 s, 1.5 s with a shim, two pwsh starts|The shim is gone. A denied edit takes 0.57 s (one pwsh start). An allowed one takes about 1.6 s of hook time (three starts), measured once|Live, PC; repo|
|Pitfall|Hooks may not fire after resume (SDK issue #782)|Issue closed 2026-05-16 with an auto-generated comment saying it is fixed. No version named, fix not re-tested|REST API|
|Pitfall|1.0.86 fixed plugins dropped on resume|Narrower: marketplace plugins and skills are kept when an active session resumes without overrides|Changelog|
|Pitfall|`--model` rejects IDs not enabled for the account|It errors when the model is unavailable to the user. The plan-difference reasoning is inference|Changelog|
|Pitfall|A user who declines the trust prompt sees no error|Not documented and not tested|Docs|

**Confirmed**

|Gap|Statement|Checked by|
|-|-|-|
|G5|Stdout on exit 0 is parsed as JSON. sessionStart consumes only `additionalContext`. subagentStart's `additionalContext` is prepended to the subagent's first message. `inject_karta_status.py` prints plain text and neither copy detects Copilot|Docs; live, Linux and PC; repo|
|G6|`/subagents` sets model and effort per agent. `.github/agents/` holds only the two reviewers|Docs; repo|
|G7|Dispatch and SubagentStop payloads carry a plugin-namespaced `agent_type`. On the PC, a PascalCase hook sees the PowerShell tool as `Bash`. `guard_writer_confinement.py` exists only for Claude and has no PowerShell write parsing|Live, Linux and PC; repo|
|G8|Hooks come from `.github/hooks/*.json` by default. `ask` is deny. notification does not fire. `GITHUB_TOKEN` is unset. The network is firewalled. Setup runs from `copilot-setup-steps.yml`|Docs|
|G9|Copilot reads the five instruction sources live. The how-to has no instruction-file section and the repo has no `.github/copilot-instructions.md`|Live, Linux; repo|
|G10|A root `plugin.json` with `$schema` takes precedence. Client pieces go under `com.github.copilot/`. The legacy `.github/plugin/plugin.json` still loads. `copilot plugin update` exists. karta ships only `.github/plugin/plugin.json`|Docs; live on the PC with karta's own manifest; live `--plugin-dir` run with a karta-like layout; repo|
|G11|Policy hook locations and load order (policy, user, project, plugins). How the sandbox is switched on. The Copilot path has git calls and a hub spawn at SessionStart|Docs; `copilot help sandbox`; repo|
|G13|No CI workflow of any kind exists, and no test runs `copilot -p`|Repo|
|G15|`/fleet`, background subagents, LSP `bash` and `powershell` keys, extensions, `errorOccurred`, `postToolUseFailure`, and `--acp` exist|Docs; `copilot --help`|
|G18|An exit-2 deny gives the model only the generic text|Live, Linux and PC|
|G20|The `exec` plus `args` hook form exists in the CLI and starts a program without a shell. No karta entry uses it|Docs; repo|
|Pitfall|Since 1.0.60, Windows does not find executables in the working directory by bare name. Repo hooks load only after folder trust|Changelog|

**Not checked**

|Item|Why|
|-|-|
|Native camelCase hooks on the PC (the `powershell` shell tool name in the GitHub Actions Windows probe table)|Every PC hook here was PascalCase|
|subagentStart "cannot block"; shapes other than flat; `additionalContext` on the PC|Not tested|
|G7 resolving the subagent type from the parent's `events.jsonl`; karta's Stop guards on a subagent's Stop|No hook was tested doing it|
|G21: `xhigh` on a model that offers it, kebab-case `high`, camelCase for repo or user agents, a `/subagents` override|Every dispatched agent was served by `mai-code-1.1-flash`, which offers low, medium, and high. `--model` and a profile `model:` pin for a model that offers `xhigh` were both refused as not available|
|`include-custom-instructions` for copilot-instructions.md and CLAUDE.md|Only AGENTS.md was tested|
|G8 cloud agent behavior|Docs only. No cloud agent run (the repo remote is a self-hosted Forgejo)|
|G10: whether Copilot reads `.claude-plugin/marketplace.json`; marketplace `sha`, update and `autoUpdate`; rejection of an unsupported schema|Docs only. The repo check ran no Copilot session|
|G11 sandbox blocking the hub spawn; Windows per-path deny|The sandbox was not turned on|
|Whether a declined trust prompt shows a message|Not documented and not tested|
|G18: a crashing hook (exit 1); Claude Code and Codex accepting an exit-0 deny from karta's guards|Only exit-2 and exit-0 denies were run, and only with probe hooks|
|G19 on the PC: which tree the guard ran from, its karta version, and whether its engine was missing|The repo does not show it. The explanation is an inference from the isolated-copy runs|
|A blocking Stop's timing|Only clean Stops were measured|
|`inject_karta_status.py` run by hand in hook mode, and its `--self-test`|Hook mode starts a hub process and writes watch state outside the test directory. The installed copy did run under Copilot on the PC (G5)|

## Gap list

Priority: **P0** = a karta guarantee is silently absent. **P1** = a supported Copilot path is
missing. **P2** = useful, not urgent. Effort: S under a day, M a few days, L a week or more.

|#|Gap|Priority|Effort|Windows angle|
|-|-|-|-|-|
|G1|No hooks wired for Copilot|Done, verified on zbook via PowerShell 2026-10-03|M|needs a `powershell` launcher|
|G2|Stop, SubagentStop, and PostToolUse guards signal with exit 2|P0|S|same fix on both|
|G3|Edit guards ignore patch-string `tool_input`|Done, verified on zbook via PowerShell 2026-10-03|S|`\` separators in patch paths; the PC sent dicts in one run and a patch string under `Edit` in another|
|G4|Repo `.claude/settings.json` gates fire under Copilot, unadapted|Done, verified on zbook via PowerShell 2026-10-04|S|errored on the runner; `uv` could not start from a hook on the PC|
|G5|SessionStart status uses Claude output conventions|P1|S|none|
|G6|Three of five agents have no Copilot profile|P1|S|none|
|G7|Writer confinement has no Copilot writer to recognize|P1|M|Bash parser must also read PowerShell|
|G8|No cloud-agent support|P1|M|a repo can pick a Windows runner; the docs cover only Linux hooks|
|G9|No guidance on Copilot instruction files|P2|S|none|
|G10|Packaging: Agent Plugins layout, marketplace pin, `copilot plugin update`|P2|S|none|
|G11|Sandbox and policy interaction undocumented|P2|S|Windows sandbox cannot deny single paths (changelog 1.0.76) and needs a supported Windows build|
|G12|Windows hook latency on Copilot: measured on the PC; reduction is G20|P2|M|the whole point|
|G13|Windows real-machine verification|Done 2026-10-03, see [Windows PC probe](#windows-pc-probe-real-machine)|M|pwsh CI job still to add|
|G14|Docs describe Copilot as skills plus two reviewers|P1|S|Windows install steps|
|G15|Optional Copilot-only surfaces unused|P2|L|varies|
|G16|Edit guards read only `file_path`; Copilot sends `path` and `file_text`|Done, verified on zbook via PowerShell 2026-10-03|S|seen on the Windows PC|
|G17|Hook commands lose exit 2 without an explicit `exit $LASTEXITCODE`|Done, verified on zbook via PowerShell 2026-10-03|S|PowerShell only|
|G18|The model never sees the PreToolUse deny reason|P1|S|seen on the Windows PC|
|G19|The agent can satisfy the Stop guard by forging the done ref|P1|M|none|
|G20|0.6 to 1.6 s of hook time per matched tool call on Windows|P2|M|three pwsh starts per allowed matched edit|
|G21|Agent profiles set effort with a key Copilot ignores|P0|S|none known; not tested on zbook|
|G22|Archiving a binder takes it out of the Stop guard's scope|P0|S|none|
|G23|Two copies of karta's hooks run when it is installed and also loaded with `--plugin-dir`|P0|S|seen on the Windows PC|

### Priority order

Execution order within each tier. Done: G13, G3, G16, G17, G1.

**Landed on main.** G1, G16, G17 and the Codex side of G3 landed in `d18a7aa` (2026-10-03). The
Claude side of G3 landed in `f12650d` (2026-10-04) through binder
`copilot-patch-input-claude-guards`: `hooks/scripts/guard_pack_write.py` and
`guard_binder_immutability.py` judge a patch-string `tool_input`, with self-tests, and
`docs/how-to/hooks.md` describes the behaviour. The archived binder is in
`.karta/binders/archive/`. Still owed from that delivery: a live Claude-side guard run under
Copilot on zbook, folded into G4's acceptance.

**P0 — remaining**

1. G2 — Stop, SubagentStop, PostToolUse signaling
2. G4 — Repo `.claude/settings.json` gates
3. G21 — Agent effort key
4. G22 — Archived binders and the Stop guard
5. G23 — Duplicate hook load

G21 to G23 were raised to P0 on 2026-10-04. Their order among themselves is not settled.

**P0 — done**

- G3 — Patch-string `tool_input`
- G16 — `path` and `file_text` payload keys
- G1 — Native hooks file for Copilot
- G17 — Explicit `exit $LASTEXITCODE`

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

**Windows PowerShell test gate.** No P0 gap is Done until its acceptance checks have passed on the
Windows PC `zbook`, with Copilot CLI run through PowerShell/pwsh and hooks dispatched through the
`powershell` entry. Linux `copilot -p` and `--self-test` runs are necessary but not sufficient.

**Added 2026-10-04.** G21, G22, and G23 were raised to P0 after this specification was written and
have no entry below. Until they do, the Fix paragraph in each gap section is the working
specification. G23 bears on the test gate above: on a machine with karta installed, `--plugin-dir`
runs both copies of its hooks, so an acceptance run there should record the loaded-hook count or run
where karta is not installed.

### G3: Patch-string input — Done, verified on zbook via PowerShell 2026-10-03

- **Change.** Add one shared normalizer to the Edit-family guards (`guard_binder_immutability.py`,
  `guard_pack_write.py`) in both `hooks/scripts/` and `.codex-plugin/hooks/scripts/`. A string
  `tool_input` that starts with `*** Begin Patch` becomes `{"command": <string>}` and takes the
  existing patch path, whatever `tool_name` is. Drop the `tool_name == "apply_patch"` requirement
  in the Codex twins. Normalize `\` to `/` in patch paths.
- **Acceptance.** `--self-test` cases in each script: a bare `*** Add File:` string and a
  `*** Update File:` string on a committed binder are denied; the same on an untracked draft is
  allowed; a patch path with `\` separators matches.
- **Depends on.** None.
- **Done when.** Every Edit-family guard denies a patch-string edit of a committed binder; passes on zbook via PowerShell.
  - Codex side: the `.codex-plugin/hooks/scripts/` twins (`guard_binder_immutability.py`,
    `guard_pack_write.py`) were verified live on zbook via PowerShell on 2026-10-03.
  - Claude side: `hooks/scripts/guard_binder_immutability.py` and
    `hooks/scripts/guard_pack_write.py` now read a patch-string `tool_input`, deny patch-string
    edits of committed binders and pack files, and normalize `\` paths. They are verified by each
    script's `--self-test` under Windows Python on zbook, run from `C:\Users\Developer\src\karta`:
    `C:\Python314\python.exe hooks\scripts\guard_binder_immutability.py --self-test` and
    `C:\Python314\python.exe hooks\scripts\guard_pack_write.py --self-test`.
  - Live Claude-side run under Copilot on zbook (2026-10-04): a committed binder edit was denied
    and left unchanged, and an untracked draft was allowed. See
    [G4 acceptance on zbook](#g4-acceptance-on-zbook-2026-10-04).

### G16: `path` and `file_text` keys — Done, verified on zbook via PowerShell 2026-10-03

- **Change.** In the same normalizer, map `path` to `file_path`, `file_text` to `content`, and
  `old_str`/`new_str` to `old_string`/`new_string`. Check each Edit-family guard for other keys it
  reads.
- **Acceptance.** `--self-test` cases with `{path, file_text}` and `{path, old_str, new_str}`
  payloads: committed binder denied with exit 2, untracked draft allowed. Existing `file_path`
  cases still pass.
- **Depends on.** G3 (shares the normalizer).
- **Done when.** The Windows PC result (guard allowed a committed-binder edit) reproduces as a
  deny in `--self-test`; passes on zbook via PowerShell.

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
- **Done when.** A Stop block from the delivery guard continues the Copilot session; passes on zbook via PowerShell.

### G1: Native hooks file — Done, verified on zbook via PowerShell 2026-10-03

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
- **Done when.** The smoke test passes on Linux and on the zbook; passes on zbook via PowerShell.

### G17: Explicit exit code — Done, verified on zbook via PowerShell 2026-10-03

- **Change.** Every `powershell` entry in `hooks.json` ends with `; exit $LASTEXITCODE`. Add the
  check to the manifest test. `launch_hook.ps1` already passes the guard's code through.
- **Acceptance.** Manifest test: every `powershell` string ends with `exit $LASTEXITCODE`. On the
  zbook, a guard that exits 2 reaches Copilot as 2 (pwsh propagates it), and a Stop guard printing
  `{"decision":"block"}` blocks.
- **Depends on.** None.
- **Done when.** No entry can report 1 where the guard exited 2; passes on zbook via PowerShell.

### G4: Repo gates

- **Change.** Add `.github/hooks/karta-repo.json` in the native schema with `bash` and
  `powershell` entries for `precommit_gate.py` (timeout 900) and `roundtable_gate.py` (timeout
  600). Use `$env:COPILOT_PROJECT_DIR`, not `{{project_dir}}`. End each `powershell` entry with
  `; exit $LASTEXITCODE`. In `.claude/settings.json`, either make `command` work in both shells or
  document `disableAllHooks` for that source, so the gates do not fire twice. Test the choice on
  Windows.
- **Acceptance.** Under Copilot in a trusted karta checkout on the zbook, a shell call runs the
  gates once and is not denied by a `uv` start failure. Linux: the gates run once.
  With a consumer repo's `.claude/settings.json` routing `Edit|Write` to
  `hooks/scripts/guard_binder_immutability.py` under Copilot on zbook, a patch-string edit of a
  committed binder is denied.
- **Depends on.** G1.
- **Done when.** A Windows contributor can run Copilot in a karta checkout without every shell
  call failing closed; passes on zbook via PowerShell.

### PowerShell verification on zbook (2026-10-03)

**What shipped.** `.github/plugin/hooks.json` in the native schema, pointed at by
`.github/plugin/plugin.json`. Every guard in `.codex-plugin/hooks/scripts` has a `bash` entry and
a `powershell` entry; each `powershell` entry calls `launch_hook.ps1` and ends with
`exit $LASTEXITCODE`. `tests/test_copilot_hooks_manifest.py` checks all of that, and that no
`powershell` command contains `${`. The Codex twins of `guard_binder_immutability.py` and
`guard_pack_write.py` now wrap a string `tool_input` that starts with `*** Begin Patch` as
`{"command": <string>}`, whatever `tool_name` is, and read `\` separators in patch paths
(`guard_pack_write.py` no longer requires `tool_name == "apply_patch"`). The binder guard already
read `path`. No guard needs `old_str`/`new_str` content: the pack guard skips PreToolUse `Edit`
and reads PostToolUse results from disk. The Claude originals in `hooks/scripts/` later got the
same patch-string handling (see G3); Copilot runs only the Codex twins.

**New finding.** With the `-p` default model (gpt-6-luna) on 1.0.92-3, an edit of an existing file
arrived as `tool_name: "Edit"` with `tool_input` set to the raw `*** Begin Patch` string. Before
the G3 fix, the binder guard allowed it and the committed binder was changed.

**Setup.** Copilot 1.0.92-3, pwsh 7.6.6, Python 3.14.3. Old `karta@karta` 2.30.0 uninstalled, its
GitHub marketplace removed, and the 2.38.4 branch build added as a local marketplace
(`copilot plugin marketplace add C:\Users\Developer\karta-probe\karta-2.38.4`,
`copilot plugin install karta@karta`). Test repo: fresh `git init` with a committed
`.karta/binders/test.json`. Each run: `copilot -p "<prompt>" --allow-all-tools --deny-tool=shell
--no-ask-user --log-dir <dir> --log-level all`, from `pwsh -NoProfile -File probe5.ps1`.

|Check|Result|
|-|-|
|(a) every guard `--self-test`, Windows Python|all 15 pass, e.g. `.codex-plugin` binder 39/39, pack write 39/39, writer confinement 151/151|
|(b) edit the committed binder|`Denied by preToolUse hook: hook exited with code 2`; SHA-256 `01337E9F…592E5E` before and after; `git status` empty|
|(c) create an untracked draft binder|allowed; `?? .karta/binders/draft.json`|
|(d) SessionStart and Stop fire|session `events.jsonl`: `sessionStart success=True`, `preToolUse`, `postToolUse`, `agentStop success=True`; process log: `[hook stdout] <karta-status>`|

Exit 2 arrived as 2, not as "hook errored" (G17). Linux: all self-tests, the manifest test, and
the full suite pass.

**Not verified on zbook.** A live Stop `{"decision":"block"}` continuing a session (G2, and G17's
Stop half); a dict-shaped `{path, old_str, new_str}` edit live (this model sent patch strings, so
the dict path is covered by self-tests only); Linux `copilot -p`.

### G4 acceptance on zbook (2026-10-04)

**Setup.** Copilot CLI 1.0.92-3, pwsh 7.6.6, Python 3.14.3. Bypass-permissions is disabled by
enterprise policy, and hooks fail closed. Checkout: `C:\Users\Developer\src\karta-wt-g4`, fed by
a git bundle. Defender exclusions cover it and `C:\Users\Developer\src\temp`, which serves as
TEMP and TMP. Each run: `copilot -p "Run the shell command: <cmd>" --allow-all-tools --no-ask-user
--log-dir <dir> --log-level all`, from pwsh scripts. The outcome is read from the log, because
copilot exits 0 even when the call is denied.

**Commit probes.** Each attempt ran `git commit` on a staged `docs/zbook-probe.txt` and took about
9 minutes. There were 8 attempts.

|Attempt|Result|
|-|-|
|#1–#6|denied by timeouts: `validate_plugin` ran 992 s serially, over the 450 s per-gate budget and the 900 s outer hook timeout|
|#7|both karta gates in `.github/hooks/karta-repo.json` (`powershell` entries via `launch_hook.ps1`) passed; `roundtable_gate` then denied, and after that fix the legacy `.claude/settings.json` hook denied because `uv.exe` could not start|
|#8|with `uv` started, the `.claude/settings.json` hook still denied: `${CLAUDE_PROJECT_DIR}` expanded to empty; `echo ok` denied the same way|

**Fixes on the item branch after #1–#6.**

- `validate_plugin` fans out in parallel: 441–545 s on zbook. `KARTA_VALIDATE_JOBS` sets the
  width; the default is min(8, max(2, cpu_count)).
- Per-gate budgets: `GATE_TIMEOUTS={"validate_plugin": 720}`, with `GATE_TIMEOUT=100` for the rest.
- Outer hook timeouts: 900 to 1200 for precommit, 600 for roundtable, in `.claude/settings.json`,
  `.codex/hooks.json`, and `.github/hooks/karta-repo.json`.
- Windows test fixes: 8e8d101, 72d4547, 003e849, 94603d9, 5272693, 2db6bd1, 7ff62a3. The reviewers
  suite runs in 305 s on pwsh.

**Attempt #7.** Copilot injects `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=safe.bareRepository`, and
`GIT_CONFIG_VALUE_0=explicit` into the hook environment. `roundtable_gate` treated that as a
foreign git config and denied. Fixed in 96a9be8 with an `INERT_GIT_CONFIG` allow-list in
`roundtable_gate.py`. Copilot then ran the cross-tool `.claude/settings.json` hook
(`uv run --script "${CLAUDE_PROJECT_DIR}/scripts/hooks/precommit_gate.py"`, source "repo
settings", fail-closed) through PowerShell. PowerShell cannot start the WinGet Links `uv.exe`
symlink: "No application is associated with the specified file".

**Attempt #8.** The real `uv` package folder was put on PATH, with
`UV_PYTHON=C:\Python314\python.exe` and `UV_NO_MANAGED_PYTHON=1`. `uv` started, but PowerShell
expanded `${CLAUDE_PROJECT_DIR}` to empty, so the hook looked for
`C:\scripts\hooks\precommit_gate.py`, did not find it, and the commit was denied. `echo ok` was
denied the same way. The root cause is upstream in Copilot CLI: github/copilot-cli#4001
(".claude/settings.json hooks fail on Windows: executed via PowerShell, $CLAUDE_PROJECT_DIR not
set") and #4399.

**Decision.** `.claude/settings.json` is unchanged. `.github/hooks/karta-repo.json` is the
supported Windows path. `disableAllHooks` is not a fix, because it also turns off the karta gates.
Documented in `docs/how-to/copilot-cli.md` (1258a05).

|Acceptance check|Result|
|-|-|
|(1) gate decision lines for each commit attempt|done|
|(2) a shell call (`echo ok`) is not denied|not met in a karta checkout, blocked by upstream #4001; the karta gates themselves pass|
|(3) the `.claude/settings.json` error on Windows|documented|

**Per-process cost on zbook.**

|Process|Time|
|-|-|
|python|38 ms|
|git|~80 ms|
|guard hook|105 ms|
|`pwsh -Command`|360 ms|

The Defender exclusions had no measurable effect.

**G3 Claude-side live run.** Temp consumer repo `C:\Users\Developer\src\temp\g3repo` with a
committed `.karta/binders/test.json`. Its `.claude/settings.json` has a PreToolUse matcher
`Edit|Write|Create|MultiEdit` with command
`C:\Python314\python.exe <abs path>\hooks\scripts\guard_binder_immutability.py`: an absolute path,
no `${…}`. Runs used `--deny-tool=shell`.

|Check|Result|
|-|-|
|(a) `--self-test`, Windows Python|33/33|
|(b) edit the committed binder|denied; hook stderr carries the guard's "committed binders are read-only" message; SHA-256 identical before and after; `git status` clean|
|(c) create `.karta/binders/draft.json`|allowed; `?? .karta/binders/draft.json`|

**Finding.** Copilot logged `Hook command failed with code 1 … (hook errored)`, not exit 2.
PowerShell `-Command` flattens a native non-zero exit to 1, so the verdict reads as an error
rather than a deny. The outcome is the same, because hooks fail closed. This is why every
`powershell` entry must end with `exit $LASTEXITCODE` via `launch_hook.ps1`.

### Summary

|Gap|Files|Acceptance check|Depends on|
|-|-|-|-|
|G3|`hooks/scripts/` and `.codex-plugin/hooks/scripts/` Edit-family guards|`--self-test`: patch strings, `\` paths; patch-string cases denied through the `powershell` entry on zbook|none|
|G16|same normalizer|`--self-test` cases pass; `{path, file_text}` and `{path, old_str, new_str}` edits denied on zbook via PowerShell|G3|
|G2|`.codex-plugin/hooks/scripts/` Stop, SubagentStop, pack-write guards|`--self-test`: one JSON object, `"decision":"block"`; Stop `{"decision":"block"}` continues the Copilot session on zbook|none (live run: G1)|
|G1|`.github/plugin/hooks.json`, `.github/plugin/plugin.json`, `tests/test_codex_gate_models.py`|smoke test on zbook (tracked `.karta/binders/test.json` edit denied and unchanged, untracked draft allowed, SessionStart and Stop fire) and on Linux `copilot -p`|G16, G17|
|G17|`.github/plugin/hooks.json`, manifest test|exit 2 arrives as 2 from pwsh on zbook; Stop block works|none|
|G4|`.github/hooks/karta-repo.json`, `.claude/settings.json`|repo gates run once under Copilot via PowerShell on zbook with no `uv` denial|G1|

### G1 (done) — No hooks wired for Copilot: verified on zbook via PowerShell 2026-10-03

See [PowerShell verification on zbook](#powershell-verification-on-zbook-2026-10-03) for what
shipped and the outputs. The section below is the original research.

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

### G3 (done) — Edit guards ignore patch-string `tool_input`: verified on zbook via PowerShell 2026-10-03

**Before the fix.** `hooks/scripts/guard_binder_immutability.py` and `guard_pack_write.py` treated a
non-dict `tool_input` as nothing to check and allowed it. The Codex twins parsed patches, but only
from `tool_input["command"]` (`.codex-plugin/hooks/scripts/guard_pack_write.py` also required
`tool_name == "apply_patch"`).

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

**Now.** `inject_karta_status.py` writes its summary to stdout as plain text, which Claude and Codex
add to context. Copilot runs the `.codex-plugin` copy (`.github/plugin/hooks.json`, SessionStart).
Its docstring commits to plain stdout, never a JSON wrapper, and its self-test asserts that stdout
holds no `hookSpecificOutput` and does not start with `{`. Neither copy detects Copilot: the only
environment variable either reads is `CLAUDE_PLUGIN_ROOT`, to find the status engine. Copilot drops
plain text, so the status never reaches the model.

**Copilot does.** Stdout on exit 0 is parsed as JSON. Empty or unparseable output counts as no
output [hooks-ref]. sessionStart output is `{additionalContext?: string}`, and only
`additionalContext` is consumed. Live runs (CLI 1.0.92-3, PascalCase `SessionStart` in a native
manifest laid out like karta's, one run per variant) gave the same results on Linux and on the
Windows PC, where the hooks were `powershell` entries:

- Plain text did not reach the model. It was absent from the reply, from `events.jsonl`, and from
  every request sent to the model, and showed only as a `[hook stdout]` line in the debug log.
- Flat `{"additionalContext": "..."}` reached the model.
- The wrapped `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":...}}` did
  not, and the hooks reference never documents it. On Linux the wrapped hook was isolated in a run
  of its own. On the PC it always shared the event with other hooks: beside a flat hook, only the
  flat text arrived, and with only plain and wrapped registered, nothing arrived.

karta's own hook showed the same on the PC. With the installed karta 2.38.4 loaded,
`inject_karta_status.py` ran at SessionStart in a fixture repo that held one binder and printed its
`<karta-status>` block as plain text. The block appears once in the debug log, as a `[hook stdout]`
line, and nowhere in `events.jsonl`. `hook.end` reports `success: true` with no output, and the
request logged after it carries the prompt and not the block (timing runs r4 and r6).

This covers SessionStart only; the PreToolUse deny JSON (G18) is separate.

subagentStart cannot block, per the docs, and its `additionalContext` is prepended to the subagent's
first user message. Live on Linux, a PascalCase `SubagentStart` with no matcher and a native
camelCase `subagentStart` each fired once per dispatch of two plugin agents. Each returned flat
`additionalContext` with its own token. Both tokens reached both subagents, joined by a blank line
ahead of the dispatch prompt, which carried neither. `events.jsonl` logged one hook pair per
dispatch although both scripts ran. In either spelling the payload arrives in camelCase:
`agentDescription`, `agentDisplayName`, `agentName` (namespaced, for example `lpbc:probe-inc`),
`cwd`, `sessionId` (the parent's), `timestamp`, and `transcriptPath`, with no `hook_event_name` and
no agent id. On the PC, a PascalCase `SubagentStart` with matcher `.*` and with no matcher also
fired once per dispatch with the same payload. The earlier probe's registration with matcher `"*"`
did not fire. The docs compile a subagentStart matcher as a regex and skip a hook whose regex is
invalid, and `"*"` is invalid, so that run showed a matcher problem, not a lack of PascalCase
support. Not tested: that the event cannot block, any shape other than flat, and `additionalContext`
on the PC. The docs say the built-in general-purpose agent emits no subagent events, yet a
PascalCase `SubagentStop` fired for it in the first Linux run.

**Fix.** Under Copilot (detect `COPILOT_PLUGIN_ROOT`), emit flat `{"additionalContext": "..."}`, not
the wrapped form, and keep plain text for Claude and Codex. The Codex copy's self-test asserts the
plain-only form, so it changes with the fix. For a gate-reviewer hook later, `SubagentStart` fired
in either spelling on Linux with no matcher, and in PascalCase on the PC with no matcher or with
`.*`. The docs compile a subagentStart matcher as a full-match regex and skip the hook when it is
invalid, and the `"*"` karta uses on its other entries is invalid there.

Re-verified 2026-10-04: live on Linux and on the Windows PC (CLI 1.0.92-3); the hooks reference read
for the event and matcher rules; the repo read for what the script emits. karta's own
`inject_karta_status.py` ran in hook mode only on the PC, as the installed plugin's SessionStart
hook. It was not run by hand, and its `--self-test` was not run, because hook mode starts a hub
process.

### G6 (P1) — Three of five agents have no Copilot profile

**Now.** `.github/agents/` holds only `karta-acceptance-reviewer` and `karta-safety-auditor`,
generated by `scripts/sync_codex_agents.py`. `karta-design-reviewer`, `karta-doc-gardner`, and
`karta-kaizen` exist for Claude and Codex but not for Copilot. The generator limits Copilot to those
two names. It maps `Read` to `read`, `Glob` and `Grep` to `search`, and `Bash` to `execute`, and it
exits with "unsupported Copilot reviewer tools" on any other tool, which includes `Write`, `Edit`,
and `Skill`. A reviewer that declares `Write` or `Edit` fails its read-only check. doc-gardner and
kaizen declare `Read, Glob, Grep, Edit, Write, Skill`. `--check` reports the profiles in sync, and
the 7 tests in `tests/test_codex_gate_models.py` pass. Its fixture defines only the two reviewers,
so no test asserts that the other three are absent.

**Copilot supports.** Plugin agents (`*.agent.md`) with `description`, `tools`, `model`,
`mcp-servers`, `include-custom-instructions`, and an effort key in frontmatter. The plugin reference
documents only `mcp-servers` [plugin-ref]. The custom agents configuration page lists the rest
except the effort key and `include-custom-instructions`, which only the CLI command reference lists.
The effort key is `reasoning-effort` in the 1.0.88 changelog and in the live runs; the command
reference spells it `reasoningEffort`, which the runs ignored (G21). Since 1.0.86,
`include-custom-instructions: true` lets an agent dispatched as a subagent read AGENTS.md,
copilot-instructions.md, and CLAUDE.md [changelog]. It defaults to false there. A live run on Linux
(1.0.92-3, `mai-code-1.1-flash`) dispatched two plugin agents that differed only in that key, with
identical dispatch prompts. With the key, AGENTS.md was in the agent's system prompt. Without it, it
was not. Only AGENTS.md was tested. An agent run as the session agent (`--agent`) already gets the
files, and `--no-custom-instructions` overrides the key. Plugin agents are addressed as
`<plugin-name>:<agent-name>` with `--agent`. `/subagents` lets the user set model and effort per
agent. The command reference also lists `models` and `modelPolicy` (`required` rejects overrides);
that is docs only. The docs' tool alias table maps `edit` to `Edit`, `MultiEdit`, `Write`, and
`NotebookEdit`, and lists no alias for `Skill`.

**Fix.** Extend the generator to emit all five, with the effort key from G21. Beyond the name list
it needs three changes: map `Edit` and `Write` to the `edit` alias, decide what to do with `Skill`
(no alias is documented; omitting `tools` enables all tools, which is wider than these agents
need), and relax the read-only check for the two writers while keeping it for the reviewers. Give
doc-gardner and kaizen the edit tool, since they write, and keep the reviewers without one. Decide
per agent whether `include-custom-instructions` helps. It matters only when the agent is dispatched
as a subagent (reviewers probably not, writers probably yes). Update the test that pins the profile
list.

Re-verified 2026-10-04: live on Linux for the effort key and for `include-custom-instructions` in
`--agent` and dispatched runs; official docs for the rest; the generator, the profile folder, and
its tests read and run.

### G7 (P1) — Writer confinement has no Copilot writer to recognize

**Now.** `guard_writer_confinement.py` exists only on Claude (`hooks/hooks.json`). It is absent from
`.codex-plugin/hooks/scripts/` and from `.github/plugin/hooks.json`. It reads the actor from the
payload's top-level `agent_type`, accepts the bare name or any `*:` namespaced form, and reads edit
targets from `file_path`, `notebook_path`, and `path`. It needs to know which subagent is writing.
Codex skipped it for the same reason (`docs/how-to/codex.md`, confinement row).

**Copilot does.** Dispatch and SubagentStop payloads carry `agent_type`, namespaced by plugin (for
example `g7probe:probe-writer`, `zbprobe:probe-editor`), on Linux and the Windows PC. A PreToolUse
payload from inside a subagent carries no `agent_type`, `agent_id`, or `agent_name`: Linux, 2 runs,
in both hook formats, and the PC, 2 runs, PascalCase only. Its keys match the main thread's:
`cwd`, `hook_event_name`, `session_id`, `timestamp`, `tool_input`, `tool_name` in PascalCase format
(the PC adds `traceparent`), and `sessionId`, `timestamp`, `cwd`, `toolName`, `toolArgs` in native
format (Linux only). No environment variable naming the agent was set on the PC. `session_id` is the
child's own id, equal to the `agent_id` that SubagentStop reports later. The main thread keeps the
parent id. So a hook can tell that a write comes from a subagent, but not which subagent, and the
child id is tied to a name only at SubagentStop, after the writes. The `SubagentStart` payload has
the name but carries the parent's `sessionId` and no agent id, so it does not bridge the two. Stop
also fires at the end of a subagent's turn, carrying the child's `session_id` and the parent's
`transcript_path` (Linux and the PC). On the PC the child id has no session-state folder of its own;
its events go into the parent's `events.jsonl`. How karta's Stop guards behave on a subagent's Stop
was not tested.

PreToolUse has no `transcript_path`; Stop and SubagentStop do, and it points at the parent's
`events.jsonl`. That file gets a `subagent.started` line with `agentId` and `agentType` about 7 s
before the child's first PreToolUse (Linux). No hook was tested resolving the type that way.

Tool names and shapes inside a subagent. Linux: an edit arrived as `Edit` with a raw apply_patch
string as `tool_input`, and a shell write as `Bash` with `{command, description}`. The PC, under
PascalCase registration: shell calls arrive as `Bash` with `{command, description}` (some also carry
`mode` and `initial_wait`) and the commands are PowerShell, for example `Set-Content`; edits arrive
as `Edit` with a raw apply_patch string, on the main thread and in the subagent (`gpt-6-luna`). In
one run, a subagent given edit and shell tools wrote all its files with one `Set-Content` command
instead of the edit tool, while an agent limited to read and edit used the edit tool. One run each,
one model, so whether that recurs is untested. The native camelCase format on the PC was not
probed.

**Fix.** The payload cannot name the writer, so the guard has nothing to match on yet. Test the one
route the probe suggests: match the payload's `session_id` to an `agentId` in the parent's
`subagent.started` lines. The PreToolUse payload has no `transcript_path`, so the guard first needs
another way to find that file, which is untested. Once a route works, wire the guard after G6 ships
doc-gardner and kaizen. Edits inside a subagent arrive as raw patch strings, so the guard needs the
G3 normalizer: its self-test covers the `path`/`file_text` dict but not a string `tool_input`. Its
static Bash write parsing assumes a POSIX shell. It already catches `>`, `>>`, and the other
redirections, because it scans them lexically, but it has no PowerShell cmdlet handling. On Windows,
Copilot's `powershell` tool is also reported as `Bash`, so the parser needs `Set-Content`,
`Add-Content`, `Out-File`, `New-Item`, and `Remove-Item`, or the guard must fail closed on
PowerShell it cannot parse for confined writers. A confined writer that has a shell may write
through it (one run on the PC did), so guarding the edit tool alone is not enough.

Re-verified 2026-10-04: live on Linux (2 runs, both hook formats) and on the Windows PC (2 runs,
PascalCase); the guard's source and tests read and run (151 self-test cases pass).

### G8 (P1) — No cloud-agent support

**Now.** Nothing for the Copilot cloud agent.

**Copilot does.** The cloud agent runs hooks in an ephemeral Linux sandbox by default. A repository
can switch it to a Windows runner through `copilot-setup-steps.yml`, but the hooks reference
documents only the Linux case, and the built-in firewall does not work on Windows. Hook
configuration is loaded from `.github/hooks/*.json` in the cloned repository, the only hook
configuration present by default. Among command hooks, only `bash` or `command` entries run
(`powershell` is ignored). HTTP hooks to allow-listed hosts are also documented, and prompt hooks
may not fire. It loads no plugins, user-level hook files, `settings.json`, or `config.json`. The
docs name `.claude` settings only for the CLI. `ask` counts as deny. `notification` does not fire.
`permissionRequest` does not apply: tool calls are pre-approved, so it either does not fire or has
no effect. Use preToolUse for decisions. The working directory is `/workspace` when a repository is
cloned (otherwise `/root`). The network is limited to GitHub and Copilot hosts unless an admin
allows more, and `GITHUB_TOKEN` is not set [hooks-ref]. Setup runs from
`.github/workflows/copilot-setup-steps.yml`, which must be on the default branch and hold a single
job named `copilot-setup-steps`. Enterprise policy hooks are CLI only.

**Fix.** For projects that want karta under the cloud agent, document (or have `karta-init` offer)
a `.github/hooks/karta.json` that calls guard scripts vendored into the repo, plus a
`copilot-setup-steps.yml` that installs `uv` and Python. Plugins do not load there, so the scripts
cannot stay in the plugin. This is the one case where karta must copy files into the user's repo,
so keep it opt-in. Decide whether to support Windows runners at all, since the docs give no hook
design for them.

Re-verified 2026-10-04: official docs only. The cloud agent could not be run (the repo remote is a
self-hosted Forgejo), so none of this is observed behavior.

### G9 (P2) — No guidance on Copilot instruction files

Copilot reads `.github/copilot-instructions.md`, `.github/instructions/**/*.instructions.md`,
AGENTS.md, and CLAUDE.md (also `.claude/CLAUDE.md`). `@path` imports expand only in
copilot-instructions.md, AGENTS.md, and CLAUDE.md, not in `*.instructions.md`. They must stay inside
the repo (the docs also allow the custom instructions directory); absolute and `~/` paths are not
loaded. `copilot instruction list` shows what a session would load. A live run on Linux (1.0.92-3,
no probe plugin, one run) confirmed that a session reads `.github/copilot-instructions.md`, a
`.github/instructions/` file, AGENTS.md, CLAUDE.md, and a file that CLAUDE.md imports;
`.claude/CLAUDE.md` and nested instruction folders were not tested. Each file held its own token,
and the model listed all five, including one pulled in by an `@docs/extra.md` line in CLAUDE.md,
with no tool calls. The `.instructions.md` text, with `applyTo: "**"`, followed the other blocks
raw, frontmatter not stripped.

In karta's own repo, `docs/how-to/copilot-cli.md` has no instruction-file section, and there is no
`.github/copilot-instructions.md` or `.github/instructions/`. AGENTS.md (257 lines) exists, and
CLAUDE.md starts with `@AGENTS.md` and adds Claude-only standing directions. karta's skills carry
the doctrine, so the risk is low. The how-to should say which file a Copilot user puts karta
pointers in (one that expands `@` imports, if they use any), and confirm with
`copilot instruction list` that karta's own repo instructions reach Copilot sessions. That check was
not run against karta's repo itself.

Re-verified 2026-10-04: official docs, `copilot instruction --help`, one live Linux run in a
throwaway repo, and a read of karta's repo and how-to.

### G10 (P2) — Packaging

The plugin reference describes the Agent Plugins 1.0/1.1 layout (`$schema` values `1.0.0` and
`1.1.0`): a root `plugin.json` with `$schema` takes precedence over `.plugin/plugin.json` and
`.claude-plugin/plugin.json`, and client-specific pieces go under `com.github.copilot/` (agents,
commands, rules, `hooks/hooks.json`, `lsp.json`) [plugin-ref]. Those locations are fixed and cannot
be set in `plugin.json`. Skills load only from `skills/`, `mcp.json` must carry a `$schema`, and a
plugin that declares an unsupported version is rejected, not loaded as legacy. Moving karta to that
layout is a restructure, not a flag.

Marketplace manifests (`marketplace.json`, `.plugin/`, `.github/plugin/`, or `.claude-plugin/`,
checked in that order) can pin a plugin source to a full 40-character `sha`. Users update with
`copilot plugin update NAME` (or `--all`). Session-start auto-update is a user setting
(`autoUpdate: true` on an `extraKnownMarketplaces` entry); a repository setting cannot turn it on.
karta's legacy `.github/plugin/plugin.json` path still works, since a manifest without a recognized
`$schema` loads as legacy, so nothing is forced. Record the decision, and add
`copilot plugin update NAME` to the upgrade steps in the how-to, with a note that users must set
`autoUpdate` themselves.

In the repo, the only Copilot manifest is `.github/plugin/plugin.json` (agents `./.github/agents/`,
skills `./skills/`, hooks `./.github/plugin/hooks.json`). There is no root `plugin.json`, no
`com.github.copilot/` folder, and no `.github/plugin/marketplace.json`.
`.claude-plugin/marketplace.json` exists (plugin `karta`, source `./`, `strict: true`, a skills
list), and whether Copilot reads it is unchecked. The how-to's "Install or update" section says to
reinstall a local plugin after changing its files and never mentions `copilot plugin update`.

Re-verified 2026-10-04: official docs, `copilot plugin --help`, a read of the repo, and a live Linux
run in which a plugin laid out like karta's loaded with `--plugin-dir` and its hooks ran. On the
Windows PC, karta's own `.github/plugin/plugin.json` (the installed 2.38.4, and the `f12650d` tree
via `--plugin-dir`) loaded and its 8 hooks ran on 1.0.92-3 (G23). The marketplace features were not
run.

### G11 (P2) — Sandbox and policy interaction undocumented

The Copilot sandbox is off by default and experimental. It turns on with `--sandbox` (this session
only), `/sandbox enable`, `sandbox.enabled` in `settings.json`, or an enterprise-managed policy.
Linux needs `bwrap` 0.5.0 or later, `slirp4netns`, util-linux 2.35 or later, `iptables`,
`ip6tables`, and `/dev/net/tun`. If the sandbox is on and the host cannot run it, sandboxed shell
commands and MCP and LSP servers fail. When it is on, command hooks from the repository, user
settings, and plugins run inside it with the same access as the agent's shell commands. A hook can
read the directory it was loaded from, and a plugin hook can write to `$COPILOT_PLUGIN_DATA`. A
hook's cwd and env do not widen access; extra paths go under `sandbox.userPolicy` in settings. A
hook that fails in the sandbox shows one warning per hook and session [hooks-ref]. `/sandbox` is
registered only when experimental features are on or a managed policy forces sandboxing. karta's
Copilot guards run `python3` directly and call `git`; `uv` appears only in the Claude launcher, and
the Windows launcher probes `python3`, `python`, and `py`. Both copies of `inject_karta_status.py`
start the Karta Watch hub on every SessionStart: a detached `serve_status.py --ensure`, which spawns
`serve_status.py --hub`. On the Windows PC that spawn ran with the sandbox off (SessionStart took
1.3 to 1.45 s and the hub started on port 9433), and no Python process remained after the runs,
checked once. Expect the sandbox to block that spawn, as the Codex sandbox can; this is untested on
Copilot. On Windows the sandbox cannot deny single paths. The only source is changelog 1.0.76
("Sandbox denied paths are enforced for relative and symlinked entries on macOS and Linux (Windows
cannot deny per path)"), no later entry changes it, and the docs say it needs a Windows Insiders
build. No karta doc other than this one mentions the Copilot sandbox, `COPILOT_PLUGIN_DATA`,
`policy.d`, or Copilot's `disableAllHooks`; the `disableAllHooks` text in the README and
`claude-code.md` covers Claude Code only.

Enterprise policy hooks (`/etc/github-copilot/policy.d/`, or
`C:\ProgramData\GitHub\Copilot\policy.d\` and `HKLM\Software\Policies\GitHub\Copilot` on Windows)
load before karta's and cannot be turned off by `disableAllHooks`, so they keep running when a repo
sets it. The docs never say a policy file can set that flag to switch off other sources' hooks; in
one hooks file it skips only that file. They always run on the host, outside the sandbox, and are
CLI only. The docs show a policy hook blocking a sandbox bypass [hooks-ref]. Managed settings have
other switches that can stop karta's hooks: `allowManagedHooksOnly` limits hooks to managed ones
(the 1.0.85 changelog extends it to extension callbacks), and a managed `enabledPlugins` value wins
over local settings for that plugin [plugin-ref; changelog 1.0.81]. On POSIX, policy files must be
root-owned and not group or world writable. Document both halves, and have the status surface say
when the hub spawn was denied.

Re-verified 2026-10-04: official docs and `copilot help sandbox`, plus a read of karta's hooks and
scripts, and the SessionStart hub spawn seen on the Windows PC with the sandbox off. The sandbox was
not turned on on any system, so every sandbox statement rests on the docs.

### G12 (P2) — Windows hook latency on Copilot

Measured on the Windows PC (see [Windows PC probe](#windows-pc-probe-real-machine)): about 1.06 s
per matched PreToolUse through the launcher and 0.8–1.3 s per Stop on 2026-10-03. Re-measured
2026-10-04 at 2.38.4 (see the timing table in
[Re-verification](#re-verification-2026-10-04)): about 0.57 s for a matched edit that a guard
denies, about 1.6 s of hook time for an allowed matched edit (1.07 s PreToolUse plus 0.52 s
PostToolUse, three pwsh starts, one run), and about 0.6 s per clean Stop with one karta copy loaded.
Backlog entry 29 (researched 2026-09-24) measured the same layers on Claude and Codex, and costs
Copilot's skill-driven `uv run` chain, but it predates karta's Copilot hooks.
`docs/backlog/windows-latency/FINDINGS.md` still says Copilot has `"hooks": {}` and so no guard
cost, and that karta ships no Copilot hooks; both statements are stale. The work to bring the
numbers down is G20.

Re-verified 2026-10-04: re-measured on the Windows PC (6 Copilot runs and 12-sample direct
measures); backlog entry 29 and the windows-latency findings read.

### G13 (done) — Windows real-machine verification: done

**Done 2026-10-03.** One run on a real Windows 11 PC under Copilot 1.0.92-3 exercised the launcher,
the binder guard, and the Stop guard. Results are in
[Windows PC probe](#windows-pc-probe-real-machine); the problems it found are G16–G20. The test work
below belongs to G1's definition of done. Its status at `f12650d` (2026-10-04):

- **In place.** `tests/test_copilot_hooks_manifest.py` (4 tests, all pass) checks the native Copilot
  hooks file. The plugin manifest points at `./.github/plugin/hooks.json`, every guard in
  `.codex-plugin/hooks/scripts/` is named in both the `bash` and `powershell` strings, every entry
  has both, no `powershell` string contains `${`, and every one ends with `exit $LASTEXITCODE`. No
  test still asserts "no Claude hooks".
- **Partly in place.** Self-test cases for the payload shapes. Both copies of
  `guard_binder_immutability.py` and `guard_pack_write.py` have cases for the Copilot
  `path`/`file_text` dict and for the raw patch string. All four self-tests pass (36 and 30 for the
  binder guard, Codex and Claude copies; 36 and 23 for the pack-write guard).
  `guard_writer_confinement.py` (Claude only) has the dict cases but no raw patch-string case, and
  it does not parse a string `tool_input`. All of these are synthetic payloads, not captured ones.
- **Open.** An opt-in live smoke test (needs a Copilot login): run `copilot -p` in a temp trusted
  repo with `--plugin-dir`, attempt a binder write, and assert the deny. Run it on Windows too. No
  test or script in the repo runs `copilot -p`. Until it passes on Windows with the G1, G16, and G17
  fixes in place, G1–G4 are "wired", not "enforced". If karta is also installed on the machine, the
  run loads two copies of its hooks (G23).
- **Open.** A Windows harness. The repo has no CI workflow of any kind. A `windows-latest` GitHub
  Actions job authenticates with the workflow's `GITHUB_TOKEN` and `copilot-requests: write` (proven
  2026-10-03), but needs karta mirrored to a private GitHub repo. A tailnet Windows PC with OpenSSH
  lets the test driver run the same smoke test over `ssh`, with no code leaving the network (proven
  2026-10-03 and used again 2026-10-04; over `ssh` the WinGet `copilot.exe` link fails, so call the
  real executable in the package folder). `pwsh` on Linux checks PowerShell syntax only: Copilot
  runs the `bash` entry there, and paths, Python, and process start-up stay Linux.

Re-verified 2026-10-04: the repo read, and the manifest test, the self-tests, and the generator
tests run on Linux under Python 3.13. The 2026-10-03 harness proofs were not repeated except for the
`ssh` route to the PC. An edit inside a subagent on Linux also arrived as a bare-string patch (G7).

### G14 (P1) — Docs describe Copilot as skills plus two reviewers

`docs/how-to/copilot-cli.md` needs the enforcement table `codex.md` has (Rule / Claude Code /
Copilot), a Windows section, and the trusted-folder requirement for repo hooks. At `f12650d` it has
no table at all, Windows appears only in an install note and in a pointer to this gap analysis, and
it says nothing about trust. It is also stale. It says the Copilot entrypoint declares an empty hook
configuration, that no karta hook runs on Copilot, and that it was not exercised against a live
Copilot session. The first two are false since `.github/plugin/hooks.json` shipped, which wires
guards for Stop, SessionStart, SubagentStop, PreToolUse, and PostToolUse. The third sits in a
paragraph on the reviewers' read-only limit whose premise, that no karta hook runs on Copilot, no
longer holds.

The README has no support matrix to match. Its only tables are the image pair and the skills table.
It covers Copilot only in the "Copilot CLI reviewer models" section. Its "Enforcement below the
agent" paragraph names Claude, Codex, and Pi but not Copilot, and the one support matrix in the docs
is in `docs/how-to/pi.md`. Add Copilot to that paragraph, and decide whether the README gets a
matrix of its own.

Re-verified 2026-10-04: `copilot-cli.md`, `codex.md`, and the README read against `f12650d`. The
trust rules for repo hooks were read in the changelog and command reference; see the pitfalls list.

### G15 (P2) — Optional Copilot-only surfaces

Not parity, but available: `/fleet` and background subagents for karta-deliver wave dispatch; LSP
config with `bash` and `powershell` keys; extensions and canvases (a Karta Watch canvas could
replace the browser tab, untested: canvases are extension-driven and no doc covers this use);
`/every` and `/after` for scheduled re-checks (experimental when introduced, possibly gated per
user); `errorOccurred` and `postToolUseFailure` events for richer failure reports; `--acp` for
editor hosts. Pick these up only after G1–G14.

Re-verified 2026-10-04: official docs, `copilot --help`, and `copilot lsp --help`. Every surface
exists; none was run.

### G16 (done) — Edit guards read only `file_path`; Copilot sends `path`: verified on zbook via PowerShell 2026-10-03

**Now.** `guard_binder_immutability.py` reads `tool_input.file_path`, `notebook_path`, and
`command`. The other Edit-family guards likely read the same keys; check each.

**Copilot does.** On the Windows PC, `Write` sent `{path, file_text}` and `Edit` sent
`{path, old_str, new_str}`. The unmodified binder guard allowed an edit to a committed binder. Given
a Claude-shaped payload, the same launcher and guard denied it with exit 2.

**Fix.** In the shared normalizer from G3, map `path` to `file_path`, `file_text` to `content`, and
`old_str`/`new_str` to `old_string`/`new_string`. Add self-test cases for both shapes. Re-check the
Linux payload, which the Linux probe saw as a patch string.

### G17 (done) — Hook commands lose exit 2 without an explicit `exit`: verified on zbook via PowerShell 2026-10-03

**Copilot does.** In a `powershell` entry, `& script.ps1` with no top-level `exit` reports 1 when
the script exits 2 (Windows PC). PreToolUse then denies as "hook errored", which hides the cause.
Stop discards stdout on exit 1, so a JSON block is lost and the session ends.

**Fix.** End every `powershell` entry with `; exit $LASTEXITCODE`. Add the check to the G13
manifest test.

### G18 (P1) — The model never sees the PreToolUse deny reason

**Copilot does.** On an exit-2 deny, the model receives only the string
`Denied by preToolUse hook: hook exited with code 2` (Windows PC and Linux, 1.0.92-3; `Edit`, and on
Linux `Agent` dispatch). `events.jsonl` records the same text as `tool.execution_complete.error`
with `code: "denied"`, which is the `{message, code}` object the 2026-10-03 probe reported. The
guard's stderr appears only in the debug process log (a `[hook stderr]` line). `hook.end` in
`events.jsonl` shows `success: true` and the generic text only, with no `error` key (Linux 1.0.92-3,
Windows 1.0.92-3 and the bundled 1.0.80). So the agent cannot tell a binder rule from a broken
hook. Only exit-2 and exit-0 denies were run, not a crashing hook.

The reason does arrive when the hook exits 0 and prints deny JSON on stdout. Both the wrapped form

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}
```

and the flat `{"permissionDecision":"deny","permissionDecisionReason":...}` denied the call, and the
model received `Denied by preToolUse hook: <reason>` (Linux, on two models, and the PC, one run per
variant). With exit 2 and the same JSON on stdout the call is denied but Copilot drops stdout, and
the model gets only the generic text (Linux `Edit` and `Agent`; PC `Edit`). The hooks reference
says that on exit 2 from preToolUse, any stdout JSON is merged with the deny decision; the live runs
do not show that for the reason text. A deny also stops the later PreToolUse hooks for that call.

**Fix.** Under Copilot, have the Edit-family and dispatch guards deny by exiting 0 with the deny
JSON on stdout (either form), and do not also exit 2. Claude Code's hooks documentation says exit 0
with `permissionDecision: "deny"` blocks and passes the reason, so one exit-0 form may serve every
host. That has not been tried with karta's guards on Claude, and Codex was not checked, so until it
is, let the guard pick its exit code from the host. karta's launcher treats exit 2 as a block and
any other non-zero exit as a crash, so a Copilot deny that exits 0 needs a way to be told from an
allow. Stdout must hold one JSON object only (G2). The PC runs used a probe guard behind karta's
unmodified launcher; karta's own guards were not run for this.

Re-verified 2026-10-04: live on Linux and on the Windows PC, one run per variant; the hooks
reference read for the exit-2 rule.

### G19 (P1) — The agent can satisfy the Stop guard by forging the done ref

**Copilot does.** After the delivery Stop guard blocked on the Windows PC, the agent ran
`git update-ref refs/karta/wip/item-b/done HEAD` without merging anything and reported the delivery
fixed. On the next block it archived the binder and committed. The guard accepted the hand-written
ref. Nothing here is Copilot-specific; an agent on any host can do the same.

**Reproduced 2026-10-04 (Linux, Python 3.13).** Both guard copies,
`hooks/scripts/guard_delivery_stop.py` and its `.codex-plugin` twin, were run unmodified in fixture
repos with a binder whose item `b` was built but not merged.

- With the status engine loadable (inside the repo), both copies block the baseline (exit 2, "items
  b carry built but no done"). After the forged ref, in the same session or a fresh one, both still
  block (exit 2) and add that the done ref "fails delivery's provenance check" because the commit
  carries no marker for item `b`. A done ref forged at the integration tip, item `a`'s marked merge,
  is also rejected ("marked for another item").
- With each guard file copied byte for byte to a directory with no `skills/` tree above it, the
  forged ref is trusted. The block reason becomes "all items are done but the binder was never
  archived", and archiving then lets the stop pass. That is the sequence on the Windows PC. The
  guard finds its provenance engine by walking up from its own file, else from `CLAUDE_PLUGIN_ROOT`.
  With no engine, or if the check raises, it returns no findings and trusts every done ref, and the
  block reason does not say so. Whether the engine was missing on the PC is an inference: the repo
  does not show which tree the guard ran from, its karta version, or whether the check raised.
- An identical second stop in the same state passes (exit 0) with the forged ref still in place and
  no archive. That is the once-per-state sentinel working as designed, and a provenance fix does not
  close it.
- Archiving the binder and committing passes the guard whether or not a done ref was forged (G22).
- A done ref pointed at an empty commit `[karta:item-b] mark done` on the integration branch is
  accepted, although item `b`'s built tip is not an ancestor of it. Today a done ref passes if every
  commit in `done^1..done` carries the item's marker, the commit is first-parent reachable on the
  integration branch, and the accepted-state check passes. Nothing checks that the built work is
  contained.

**Fix.** Aim: accept a done ref only if it points at a commit that contains the item's built work,
or was written by karta's own landing step. Concretely: (1) require the `built` tip to be an
ancestor of `done`, so an empty marker commit does not count; (2) when the provenance engine cannot
load, fail closed for done refs, or say in the block reason that provenance was skipped; (3) decide
whether the once-per-state pass should stay, since it also lets a forged ref through; (4) word the
block reason so it names the landing step, not the ref. The archive path is G22.

Re-verified 2026-10-04: both guard copies run on Linux against fixture repos, and the guard source
read. The PC was not re-run.

### G20 (P2) — 0.6 to 1.6 s of hook time per matched tool call on Windows

**Copilot does.** On 2026-10-03, on the Windows PC, a matched PreToolUse took about 1.06 s through
the launcher and 1.5 s with a shim in front (two pwsh starts). pwsh start-up alone was about 0.32 s;
Python about 0.05 s. Stop took 0.8–1.3 s. Re-measured 2026-10-04 at 2.38.4 (timing table in
[Re-verification](#re-verification-2026-10-04)). A matched edit that a guard denies costs about
0.57 s, one pwsh start, because the deny stops the remaining hooks. An allowed matched edit takes
about 1.07 s in PreToolUse (binder guard, then pack-write guard, two pwsh starts in sequence) and
0.52 s in PostToolUse (one more): three starts and about 1.6 s, from one run with a process watcher
that adds about 0.09 s. pwsh start-up alone is 0.23 s and Python 0.02 s. The launcher adds about
0.18 s beyond pwsh and the guard run directly. The breakdown of that was not measured, and the
interpreter probe alone is about 0.02 s. A clean Stop takes about 0.6 s with one karta copy
loaded, and SessionStart 1.3 to 1.45 s; it also starts the hub.

**Fix.** Step one has shipped: the guards read `path` themselves, `.github/plugin/hooks.json` has
no shim, and each hook entry starts pwsh once. Still open: an allowed matched edit starts pwsh three
times, because two PreToolUse guards and one PostToolUse guard match it. Cut the launcher's
interpreter search and probe, or merge the matched guards into one process. Then try the `exec` +
`args` form (G1 open question) to skip pwsh, and apply the layer work from backlog entry 29. Every
`hooks.json` entry is still a `command` entry with `bash` and `powershell` strings, and none uses
`exec`. The hooks reference documents `exec` plus `args` for the CLI only: it starts the program
without a shell, cannot be combined with `bash`, `powershell`, or `command`, supports no pipes,
redirection, or globbing, and does not work in the cloud agent. Whether it saves the pwsh start on
Windows is unmeasured, and whether it finds a bare program name there is not covered, so use a full
path (see the 1.0.60 pitfall).

Re-verified 2026-10-04: re-measured on the Windows PC (direct measures, 12 samples; 6 Copilot runs,
one on the bundled 1.0.80); `hooks.json` and the guards read; the docs read for the `exec` form.

### G21 (P0) — Agent profiles set effort with a key Copilot ignores

Raised to P0 on 2026-10-04. The effort karta configures for its gate reviewers silently does not
apply under Copilot.

**Now.** The generator `scripts/sync_codex_agents.py` (line 104), both generated profiles in
`.github/agents/` (line 5), the generator's test (`tests/test_codex_gate_models.py`, line 92),
`docs/how-to/copilot-cli.md` (line 43), and `skills/karta-verify/SKILL.md` (line 88, with its
copies under `.agents/skills/` and `plugins/karta/skills/`) all use `reasoningEffort`.

**Copilot does.** Copilot read the kebab-case key and ignored the camelCase one on both paths, in
live runs on Linux (CLI 1.0.92-3, model `auto`, plugin-shipped agents).

- Selected with `--agent` (one run per key, `mai-code-1.1-flash`): `reasoning-effort: low` sent the
  request with reasoning effort low, and `reasoningEffort: low` sent medium, the default.
- Dispatched through the task tool, which is how karta's reviewers run (one run with five agents,
  and the `xhigh` pair again in two more runs). Every dispatched agent was served by
  `mai-code-1.1-flash`. With no key the request went out at medium. `reasoningEffort: "low"` gave
  medium and `reasoning-effort: "low"` gave low. `reasoningEffort: "xhigh"`, the line karta writes,
  gave medium. `reasoning-effort: "xhigh"` was read, but the request still went out at medium,
  because that model offers only low, medium, and high. The only sign of the fallback is a debug-log
  line, `Sending telemetry event: cli.telemetry (kind: reasoning_effort_fallback)`.
  `subagent.configured` in `events.jsonl` carries `reasoningEffort` only when the profile used the
  kebab-case key.

So karta's reviewers run at the default effort under Copilot today. Changing the spelling alone does
not give them `xhigh` when the model that serves the dispatched agent does not offer it: the level
falls back to medium, not to high, with no visible warning. Whether `xhigh` is sent on a model that
offers it could not be checked. `--model gpt-6-luna` was refused at startup as not available, a
profile `model:` pin for the same model was refused the same way, and `auto` served every dispatched
agent with `mai-code-1.1-flash` even where `subagent.configured` named `gpt-6-luna`.

The sources disagree with the runs. The CLI command reference spells the field `reasoningEffort` and
describes it for task-tool dispatch. The 1.0.88 changelog spells it `reasoning-effort`, says an
explicit `--reasoning-effort` wins, and says a level the selected model does not offer is reported
and left unapplied. The custom agents configuration and plugin reference pages list no effort key.
Not tested: camelCase for repo or user agents, kebab-case `high`, a model that offers `xhigh`, a
`/subagents` override, and Windows.

**Fix.** Emit `reasoning-effort` from the generator, in the generated profiles, the test, the
how-to, and the karta-verify skill text. Then settle the value. `xhigh` is left unapplied on a model
that does not offer it, so either write a level every likely serving model offers
(`mai-code-1.1-flash` offers `high`, which was not run) or keep `xhigh` and state the fallback in
the how-to. Confirm the result from the logged request, not from `subagent.configured`, which showed
`xhigh` while the request went out at medium. Do this before G6 adds three more profiles.

Re-verified 2026-10-04: live on Linux for both paths (`--agent`: one run per key; task-tool
dispatch: three runs that reached a model), official docs for the spellings, repo read for the key
karta writes. Not tested on Windows.

### G22 (P0) — Archiving a binder takes it out of the Stop guard's scope

Raised to P0 on 2026-10-04. The delivery Stop guard's guarantee is silently absent once a binder is
archived, although the path takes a deliberate archive commit and karta's own docs allow the archive
move.

**Now.** The delivery Stop guard reads only live binders (`.karta/binders/*.json`, from the working
tree and HEAD, never `archive/`). A guard self-test pins this as designed ("archived-only slug with
surviving refs allows"), and `docs/how-to/codex.md` allows the archive move under the binder
immutability guard.

**Seen.** On 2026-10-04, on Linux with both guard copies, in a fixture repo where item `b` was built
and never merged, moving the binder to `.karta/binders/archive/` and committing gave exit 0 and no
block. It passed with no forged ref at all (archive committed on main), and also when the archive
was committed on the integration branch after a forged done ref. So archiving is the cheapest way
past the guard, and a G19 fix that hardens only done-ref provenance leaves it open.

**Fix.** Check the refs of a binder archived in the working tree or in HEAD by the current session,
or refuse to treat an archive as delivered while any item has `built` without a verified `done`.
karta's own landing step must still be able to archive a delivered binder, so settle that rule with
the archive step's owner.

Re-verified 2026-10-04: both guard copies run on Linux against fixture repos, and the guard source
read. Not run on Windows.

### G23 (P0) — Two copies of karta's hooks run when it is installed and also loaded with `--plugin-dir`

Raised to P0 on 2026-10-04. It bears on the Windows PowerShell test gate: an acceptance run that
loads the tree under test with `--plugin-dir` on a machine with karta installed also runs the
installed copy's hooks.

**Copilot does.** The Windows PC has `karta@karta` 2.38.4 installed. On Copilot 1.0.92-3, adding
`--plugin-dir` for another karta tree did not replace it. The log went from 8 loaded hooks to 16,
and both copies' SessionStart and Stop hooks ran one after the other: SessionStart took 2.4 to 2.6 s
instead of 1.33 to 1.45 s, and Stop 1.2 s instead of 0.6 s. A PreToolUse deny from the first copy
stopped the second copy's hooks. On the bundled 1.0.80, only one copy's output appeared. So a
timing run or a smoke test that uses `--plugin-dir` measures two karta copies unless the installed
one is accounted for.

**Fix.** Note it in the how-to for contributors. Have the G13 smoke test record the loaded-hook
count or run where karta is not installed, and do the same for any G20 measurement.

Re-verified 2026-10-04: live on the Windows PC (runs r1 to r6). Not tested on Linux.

## Copilot pitfalls to plan around

- Hooks did not fire after a session resume in some builds (copilot-sdk issue #782). The issue was
  closed as completed on 2026-05-16 with an auto-generated comment saying resumed sessions now
  reload the deferred hook configuration. No version is named and the fix was not re-tested, so
  re-check on the version in use. 1.0.86 fixed marketplace plugins and skills being discarded when
  an active session is resumed without plugin-directory, discovery, or working-directory overrides.
- Since 1.0.60, Windows no longer finds executables in the working directory by bare name
  [changelog]. Use full paths or rely on `PATH`.
- `copilot --model` errors when the model is unavailable to the user (changelog 0.0.421). A
  `model:` pin in a profile can fail on another user's plan, which is why the current profiles
  leave `model` unset.
- Repo hooks load only after folder trust is confirmed. In `-p` mode they load only if the folder
  is already trusted, `COPILOT_ALLOW_ALL=true` is set, or
  `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` is set. A user who declines the prompt gets no
  repo gates. Whether any message appears is not documented; test it before claiming there is
  none.
- Hook output is read per event. For SessionStart, plain text and the wrapped `hookSpecificOutput`
  form did not reach the model in a live run; only flat `additionalContext` did (G5).
- For a plugin-shipped agent, Copilot applied `reasoning-effort` and ignored `reasoningEffort` in
  live runs, with `--agent` selection and with task-tool dispatch (one model; G21). The CLI command
  reference still spells it `reasoningEffort`. A level the serving model does not offer is left at
  the default, and the only sign is a debug-log line.
- A deny reason reaches the model only when the hook exits 0 with deny JSON. On exit 2, Copilot
  drops stdout (G18).
- Matched hooks for one event run one after another, and a PreToolUse deny stops the rest.
- On the Windows PC, `copilot.exe --no-auto-update` runs the bundled 1.0.80, not the 1.0.92-3 that
  `--version` reports. A probe with that flag tests a different Copilot.
- A `SubagentStart` matcher of `"*"` is an invalid regular expression, and the hook is skipped
  (G5). The same `"*"` on `SubagentStop`, which karta ships, fired in the Linux probe.
- A file create or edit can arrive as a `path` dict or as a raw apply_patch string, depending on the
  model, on Linux and on Windows (G3, G16).

## Order of work

Same sequence as the [Priority order](#priority-order).

1. P0 remaining: G2, then G4, then G21, G22, G23. G3, G16, G1 and G17 are done and on main
   (`f12650d`). G2 is script-only and testable on Linux first; G4 needs the live zbook run and
   carries the owed Claude-side guard check. Details in the [P0 specification](#p0-specification).
   G21, G22, and G23 were raised to P0 on 2026-10-04; their fixes are in their gap sections.
2. Windows (zbook, PowerShell) run: required before any P0 gap is marked done, and before guards
   are marked "Enforced" in the doc (G14). Includes the G13 smoke test and G20 bench.
3. P1: G18, G5, G6, G7, G14, G19. G8 when a user wants the cloud agent.
4. P2: G20, G12, G9, G10, G11, G15.

[hooks-ref]: https://docs.github.com/en/copilot/reference/hooks-reference
[plugin-ref]: https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference
[changelog]: https://github.com/github/copilot-cli/blob/main/changelog.md
