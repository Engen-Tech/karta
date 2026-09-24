# Windows latency — where karta's time goes on Copilot and Codex hosts

Date: 2026-09-24
Status: findings and a ranked plan (input to backlog item #29). No code changed.
Backlog item: `docs/backlog/README.md` §29 "karta is slow on Windows hosts"

## TL;DR

**The minutes are not karta's Python. On this machine every layer karta spawns finishes in
under 1.3 s. The multi-minute stalls people see in the ChatGPT/Codex desktop app on Windows
are Codex's native sandbox re-checking ACLs and re-logging-on a sandbox user before every
command, made worse by Defender. A Go rewrite would
take 100 to 230 ms off each karta invocation and nothing off the layers that cost seconds
or minutes.**

Order of payoff: fix the environment (sandbox, Defender, git, uv) first, cut the number of
processes karta spawns second, move the guards into a resident service third. Only then
does a compiled binary become worth discussing, and even then as a thin shim.

## The question

The deliverable's hooks and skill scripts are Python launched through `uv run --script`.
On Windows, driven from PowerShell by GitHub Copilot's UI or the ChatGPT/Codex desktop app
(build 26.915.31945 at the time of asking; not the standalone Codex CLI), a shell invocation
that takes seconds on macOS or Linux takes minutes. What can karta do about it,
and would rewriting the scripts in Go make it faster?

## What karta actually spawns per event

Three hosts, three different process chains. This is the map the measurements are read
against.

| Host | Chain per hook or script call | Hooks per tool call |
|-|-|-|
| Claude Code (`hooks/hooks.json`) | shell form `uv run --script …` → bash.exe (Git Bash) → uv.exe → python.exe → N × git.exe | Write: 3 PreToolUse + 1 PostToolUse, run in parallel. Bash: 1. Task/Agent: 2. SessionStart, Stop, SubagentStop: 1 each |
| Codex (`.codex-plugin/hooks/hooks.json`, `commandWindows`) | cmd.exe `if exist` → **powershell.exe (Windows PowerShell 5.1, not pwsh)** → `launch_hook.ps1` (four `Get-Command` lookups plus a `python -I -S -c` probe process) → python.exe → N × git.exe | apply_patch: 2 PreToolUse + 1 PostToolUse. spawn_agent: 2 |
| Copilot CLI (`.github/plugin/plugin.json`) | `"hooks": {}`, so no guard cost. Every `uv run --script` line a skill asks the model to run becomes a fresh pwsh.exe → uv.exe → python.exe → N × git.exe | none |

How many script calls a skill asks for, counted from `uv run` lines in each `SKILL.md`:
deliver 10, plan 7, validate 7, build 6, status 6, kaizen 4, verify 3. Each is a full
process chain before the model's round trip even starts.

How many git subprocesses the hot scripts make (call sites, not runtime calls):
`karta_next.py` 16, `inject_karta_status.py` 6, `merge_item.py` 5, `guard_gate_dispatch.py`
4, `guard_binder_immutability.py` 3.

## Why the Codex Windows launcher is PowerShell

Recorded here because the plan below proposes retiring `launch_hook.ps1`, and the reasons
it exists have to survive its retirement. Source: commit `8e3c926` (2026-09-19) and the
header of `.codex-plugin/hooks/launch_hook.ps1`. Performance was never a criterion in that
change; correctness on a stock Windows install was.

- **The defect it fixed.** All 17 Codex command hooks launched through `sh -c`. A stock
  Windows install has no `sh`, so every fail-closed guard died at the launcher, and Codex
  reported it as `hook exited with code 1`, indistinguishable from a deliberate block.
- **cmd is still the outer layer.** Codex runs `commandWindows` through cmd, and karta's
  entries are cmd syntax: `if exist <launcher> (powershell …) else (exit /b 0)`. The choice
  was what the launcher body is written in, not whether cmd is involved.
- **Why the body is PowerShell rather than a batch file.** The launcher resolves the root,
  joins a forward-slash script path, finds a real CPython, sets `PYTHONUTF8=1`, propagates
  the guard's exit code verbatim, and fails open only on launcher-level errors. The
  interpreter search is the part batch cannot do well: the WindowsApps `python.exe` is a
  0-byte app-execution alias whether it is the Store stub that opens the Store or a real
  Store-installed Python, so the only reliable test is to run each candidate with
  `-I -S -c` and a 3.11 floor. The header rejects a one-liner because PowerShell "would have
  to be embedded in a JSON string, quoted through the manifest, and repeated once per hook
  entry". Two more PowerShell-specific fixes came out of that change's roundtable review:
  reading the working directory from `[Environment]::CurrentDirectory` because
  `Set-Location` treats `[` in a path as a wildcard, and forcing `ErrorActionPreference` to
  `Continue` so a guard writing its deny reason to stderr cannot fall into the fail-open trap.
- **Why `powershell.exe` 5.1 rather than `pwsh` 7.** Not recorded in the commit. The evident
  reason is presence: 5.1 ships with every Windows, pwsh does not. That is an inference,
  not a decision on file.
- **What it costs.** Measured below: 0.45 to 1.2 s per hook, three hooks per `apply_patch`.

What makes it revisitable is that the same commit made uv karta's declared runner on the
Claude side, and uv does interpreter discovery and `requires-python` itself, which is the
launcher's hardest job. A replacement that calls `uv run --script` straight from cmd must
still keep:

| Launcher duty | Who covers it without the launcher |
|-|-|
| resolve the root | `%PLUGIN_ROOT%` in the bundled manifest; the git-toplevel form is only needed by the repo-local `.codex/hooks.json` |
| fail open when the guard file is missing | the cmd `if exist … else (exit /b 0)` wrapper already does this |
| propagate the guard's exit code verbatim | uv exits with python's code |
| UTF-8 stdio and file reads | set `PYTHONUTF8=1` in the cmd line, or finish the work the header defers to the windows-parity binder and have each guard name its encodings |
| the validator's rules (`_check_codex_hook_windows`, `_check_command_portability`) | every command hook still needs a `commandWindows` twin naming the same guard and never via sh; a `uv run --script` twin passes because uv is the declared runner |
| uv present on PATH inside the Codex process | the Claude side already assumes this; a consumer without uv fails open at `if exist` only if the check targets uv, so decide whether a missing uv should fail open or loud |

## Measured on this machine

Windows 11 Enterprise 10.0.26200, Defender real-time protection on, repo on NTFS (not a
Dev Drive), Python 3.14 at `C:\Python314`, uv via winget, pwsh 7.6.6, Git for Windows.
Three runs each from a warm pwsh 7 session; guards fed `{}` on stdin. Milliseconds.

| Layer | min | median |
|-|-|-|
| `cmd.exe /c exit` | 13 | 30 |
| `powershell.exe -NoProfile -c exit` (5.1) | 150 | 452 |
| `pwsh -NoProfile -c exit` (7.6) | 234 | 270 |
| `git rev-parse --show-toplevel` | 35 | 46 |
| `git status --porcelain` | 49 | 66 |
| `python -c pass` | 29 | 94 |
| `python -I -S -c pass` | 29 | 44 |
| `python -c "import json,re,subprocess,pathlib,argparse,hashlib,tempfile"` (the guards' imports) | 85 | 234 |
| `uv --version` | 21 | 37 |
| `uv run --script guard_auditor_dispatch.py` (trivial, no git) | 139 | 201 |
| same guard, bare `python` | 101 | 125 |
| `uv run --script guard_writer_confinement.py` | 138 | 214 |
| `uv run --script guard_binder_immutability.py` | 122 | 218 |
| `uv run --script inject_karta_status.py` (6 git calls) | 361 | 403 |
| `uv run --script guard_delivery_stop.py` | 160 | 521 |
| Codex Windows chain: cmd → powershell.exe → launch_hook.ps1 → python guard | 453 | 1212 |
| `uv run --script karta_next.py --help` | 135 | 243 |

What the table says:

- `uv run` adds 40 to 80 ms over bare python here because interpreter discovery is cached
  and the system Python is first on PATH. On other machines it can add seconds (see uv
  below).
- The guards' standard-library imports cost more than the interpreter itself: 85 to 234 ms
  against 29 to 44 ms for a bare `-I -S` start.
- The Codex launcher is the single worst karta-owned layer. Windows PowerShell 5.1 costs
  150 to 450 ms to start, then the launcher spends four `Get-Command` lookups and a probe
  process finding an interpreter uv already knows how to find. Three hooks per
  `apply_patch` means 1.5 to 3.5 s of karta overhead per edit on Codex.
- Nothing here reaches two seconds. A user seeing minutes is seeing something above karta.

Limits of the measurement: one machine, three runs, Defender exclusions unknown (viewing
them needs admin), and neither Copilot CLI nor Codex App was timed here. The host costs
below come from published reports, not from this box.

## Why Windows is slower, with sources

**Process creation.** Assume 10 to 30 ms per `CreateProcess` on Windows against single-digit
milliseconds for fork+exec on Linux
([Szorc, "Surprisingly Slow"](https://gregoryszorc.com/blog/2021/04/06/surprisingly-slow/)).
A primitive benchmark put Windows at more than 20x slower than Linux at launching a
program ([bitsnbites](https://www.bitsnbites.eu/benchmarking-os-primitives/)). A Rust
process-spawn benchmark on Windows 11 Enterprise build 26200 with Defender on, the same
build as this machine, measured a 22.5 ms `CreateProcess` floor and 5.4 ms for a bare-name
`PATH` lookup ([ProcessKit comparison](https://github.com/ZelAnton/ProcessKit-rs/blob/main/docs/comparison.md)).
Win32 process start does extra work Unix does not: CSRSS notification, kernel32 loading,
manifest parsing, the app-compat database lookup
([Stack Overflow](https://stackoverflow.com/questions/47845/why-is-creating-a-new-process-more-expensive-on-windows-than-linux)).

**Defender.** On a normal volume the Defender minifilter scans synchronously in the
pre-operation callback, so the calling thread waits on every file open. A trusted Dev Drive
switches that to deferred asynchronous scanning
([Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5941843/dev-drive-performance-friendly-exclusions),
[Defender performance mode](https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-endpoint-antivirus-performance-mode)).
Microsoft quotes up to 30% better overall build times on Dev Drive
([Windows Developer Blog](https://blogs.windows.com/windowsdeveloper/2023/06/01/dev-drive-performance-security-and-control-for-developers/)).
One Python project measured `uv sync` 40% faster and cold start 30% faster after excluding
the repo, `.venv`, and the uv cache ([aden-hive #4428](https://github.com/adenhq/hive/issues/4428)).

**PowerShell startup.** The PowerShell team's own figure for Windows PowerShell 5.1 is 200
to 300 ms with `-NoProfile` ([PowerShell #1954](https://github.com/PowerShell/PowerShell/issues/1954));
pwsh 7 spends about 190 ms in JIT at startup even with crossgen. On a network with no
internet path, loading any signed module can wait up to 15 s on a certificate revocation
check ([Microsoft startup troubleshooting](https://learn.microsoft.com/en-us/powershell/scripting/dev-cross-plat/performance/startup-performance?view=powershell-7.6)).
That one is worth remembering on locked-down corporate laptops.

**Git Bash / MSYS.** Every external command from Git Bash costs about 100 ms because MSYS
emulates `fork`
([unix.stackexchange](https://unix.stackexchange.com/questions/786844/starting-processes-is-very-slow-in-git-bash-on-windows),
[Rufflewind](https://rufflewind.com/2014-08-23/windows-bash-slow)). Claude Code's Bash tool
on Windows was measured at 5 to 9 s per call when its shell snapshot re-sourced 85
base64-decoded functions from git-completion
([anthropics/claude-code #89580](https://github.com/anthropics/claude-code/issues/89580)).
Relevant to Claude Code on Windows, not to Copilot or Codex.

**uv interpreter discovery.** uv checks managed installs, every PATH directory, the PEP 514
registry, and Microsoft Store aliases. On a machine with Store `python.exe` stubs on PATH,
each stub took seconds to query and discovery took 25 s
([astral-sh/uv #12617](https://github.com/astral-sh/uv/issues/12617)). `UV_PYTHON` skips
the search. On a machine with no Python 3.11+ at all, the first `uv run --script` downloads
one, which behind a corporate proxy can hang until timeout.

**git on Windows.** Commands that refresh the index (`status`, `diff`, `add`, `checkout`,
`stash`, `describe --dirty`) stat the worktree with per-file calls that are expensive on
NTFS. Three settings address that, and they are not equal:

- `core.fscache` (Windows only) bulk-reads directories within one command instead of
  calling `lstat` per file: 3 to 6x on `status` in a 200k-file repo
  ([msysgit #94](https://github.com/msysgit/git/pull/94)). Git for Windows turns it on in
  the system gitconfig by default, so setting it globally is usually a no-op; check with
  `git config --show-origin core.fscache`. It is a per-command in-memory cache with no
  daemon and nothing to go stale.
- `core.untrackedCache` stores directory mtimes in the index so unchanged directories skip
  the untracked scan, about 2x on that phase
  ([GitHub blog](https://github.blog/engineering/infrastructure/improve-git-monorepo-performance-with-a-file-system-monitor/)).
  It trusts directory mtime: fine on NTFS, wrong on network shares, some FUSE mounts, and
  WSL's `/mnt/c`, where it can report stale status. `git update-index --test-untracked-cache`
  checks a volume before you trust it.
- `core.fsmonitor` starts a long-lived `git fsmonitor--daemon` per working directory and
  asks it for changes instead of scanning. GitHub measured status on Chromium-sized trees
  dropping from 17 to 85 s to under 1 s with fsmonitor plus untracked-cache. The costs: one
  daemon process per worktree, so a delivery that builds each item in its own worktree
  spawns one per item, each a CreateProcess plus a Defender scan; the first status after a
  daemon starts is as slow as before or slower; daemons outlive the shell and pile up
  ([microsoft/vscode #161088](https://github.com/microsoft/vscode/issues/161088)). Before
  git 2.34 the daemon held the worktree root open and blocked `worktree move`, rename, and
  delete ([git-for-windows #3370](https://github.com/git-for-windows/git/issues/3370),
  [#3408](https://github.com/git-for-windows/git/issues/3408)); fixed by changing directory
  to HOME at startup ([git 39664e9](https://github.com/git/git/commit/39664e93093bd9545ad4085523b122196c449508))
  and a force-shutdown when the root moves, so git 2.55 is clear, but any IDE bundling git
  2.35.1 or older misreads a boolean `core.fsmonitor` as a hook path. The daemon refuses
  network-mounted repos unless `fsmonitor.allowRemote` is set
  ([git docs](https://git-scm.com/docs/git-fsmonitor--daemon)).

None of the three speeds up karta's status script: `karta_next.py`'s git calls are
`rev-parse`, `for-each-ref`, `symbolic-ref`, `merge-base`, and `ls-tree`, all ref and
object reads that never touch the worktree. They help the build and deliver phases
(`worktree add`, merges) and the model's own `git status` and `git diff` calls. karta's
repos are small, and the fsmonitor wins in the sources start at hundreds of thousands of
files, so on a repo like karta itself the daemon saves tens of milliseconds per status
while costing a process per item worktree.

## Where the minutes come from, per host

### The ChatGPT/Codex desktop app (and Codex CLI): the native Windows sandbox

The desktop app is the case that was asked about. It bundles its own command runner (the
sandbox log names it, for example `codex-command-runner-0.151.0-alpha.7.1.exe` under the
`WindowsApps\OpenAI.Codex_26.825…` package), so a separately installed Codex CLI version
says nothing about what the app is running, and the app updates itself. Most of the reports
below are desktop reports, and the desktop path is the slower one: on the same machine a
trivial probe took 31 to 68 s through the app's nested sandbox shell and 1.3 to 2.2 s through
a direct `codex sandbox` call (#32314); another user measured 89 to 98 s through the app
against about 1 s direct (#34529). The app processes three writable roots where the CLI
processes two.

Before every sandboxed command Codex runs `codex-windows-sandbox-setup.exe`, which
re-verifies ACLs across every writable root, then launches the runner as a dedicated sandbox
user. Users on Windows 11 build 26200 measured the delay directly, with the command itself
taking well under a second:

| Report | What was measured | Workaround that worked |
|-|-|-|
| [openai/codex #31958](https://github.com/openai/codex/issues/31958) | 88 s from tool request to `powershell.exe` creation; script itself 0.077 s | Defender exclusion on the Codex install directory removed the 40 to 70 s setup time entirely |
| [#32314](https://github.com/openai/codex/issues/32314) | elevated sandbox adds 18 to 32 s per command; Desktop nested shell 31 to 68 s | `[windows] sandbox = "unelevated"` restores speed but `apply_patch` fails with split writable roots |
| [#34529](https://github.com/openai/codex/issues/34529) | 1 to 2 minutes of ACL refresh before every call on a large Unity repo | none; refresh re-runs on every invocation |
| [#34889](https://github.com/openai/codex/issues/34889) | 0.145.0 explicit-ACE repair; `%TEMP%` with 165k objects exceeds 60 s; still present in 0.147.0 | point `TMP`/`TEMP` at a clean directory; a `writable_roots` list that included the uv cache directories hung every command |
| [#41351](https://github.com/openai/codex/issues/41351) | 0.150.1 unelevated: 15.4 s in one `CreateFile` on a `\\NUL` path before every command | none yet; `danger-full-access` control ran in 122 ms |
| [#39484](https://github.com/openai/codex/issues/39484), [#39574](https://github.com/openai/codex/issues/39574) | trivial `Get-Content` stays "Working" for minutes on 0.144 and 0.145 | `[features] unified_exec = true` gave one reporter a 0 ms exec |
| [#34062](https://github.com/openai/codex/issues/34062) | the elevated runner is started with `CreateProcessWithLogonW(LOGON_WITH_PROFILE)`, loading the sandbox user's registry hive on every command; desktop 26.707 measured `cmd /c exit 0` at 31.9 to 35.9 s elevated against 0.3 s unelevated | a one-line patch (logon flags 0) removed the profile loads; not confirmed merged |
| [#33049](https://github.com/openai/codex/issues/33049) | desktop 26.707: `Get-Location` never returns; traced to stale explicit `CodexSandboxUsers` `0x1301FF` ACEs from an older install that setup tries and fails to repair every call | removing those ACEs from the drive root and user folder restored a working `elevated` sandbox |

Fixes have shipped piecemeal: coalesced setup requests in 0.145.0, hardened elevated
startup in 0.146.0, Unicode-path and ACL read-control fixes plus Windows sandbox
diagnostics in `codex doctor` in 0.150.0, compatible PowerShell selection in 0.152.0
([releases](https://github.com/openai/codex/releases),
[changelog](https://learn.chatgpt.com/docs/changelog)). The September 2026 releases start
restructuring the path itself: "Separate Windows sandbox provisioning from ACL refresh"
(#42309), a sandbox provisioning service (#42334, #42353), and a shared background
app-server daemon on Windows (#42405). The 0.147.0 retest in #34889 says the tree-size cost
was still there then. Desktop build 26.915.31945 is newer than any report found here, so
whether it carries the provisioning split is unverified; the sandbox log will say. The
official [Windows sandbox page](https://learn.chatgpt.com/docs/windows/windows-sandbox)
documents the `elevated` and `unelevated` modes and says nothing about latency.

How to confirm on a given machine: open `%USERPROFILE%\.codex\.sandbox\sandbox.*.log`. The
`spawning` line names the app package and runner version; the gap between
`setup refresh: spawning … codex-windows-sandbox-setup.exe` and `setup binary completed` is
the sandbox cost. If that gap is tens of seconds, karta is not the problem.

On top of that, karta's own Codex launcher adds 0.45 to 1.2 s per hook (measured above),
three times per `apply_patch`.

### Copilot CLI

Copilot CLI hardcodes `pwsh.exe` and requires PowerShell 7
([github/copilot-cli #1680](https://github.com/github/copilot-cli/issues/1680)). Its
`powershell` tool starts a new pwsh process per command: the hang report in
[#1434](https://github.com/github/copilot-cli/issues/1434) shows two commands as two shell
IDs with two PIDs. Whether that process loads the user's profile is not documented, so a
heavy `$PROFILE` (oh-my-posh, Terminal-Icons) is a plausible per-command tax worth
checking. karta ships no Copilot hooks, so its cost on Copilot is one pwsh start plus one
`uv run` chain per script line, roughly 0.5 to 1 s before the model round trip. Copilot
does run `.claude/settings.json` hooks it finds in a repo, through PowerShell rather than
bash ([#4001](https://github.com/github/copilot-cli/issues/4001)); karta's plugin hooks are
not in that file, so this does not touch karta today.

### Claude Code

Hooks default to shell form through bash, which on Windows is Git Bash, so every hook
pays a bash.exe start before uv and python. Claude Code runs all matching hooks in
parallel, so a Write costs roughly one guard's wall time, not four.

## What to do, in order of payoff

### Tier 0: environment, no karta change, largest wins

1. **Codex desktop app.** Read the sandbox log gap first, and note the bundled runner
   version it prints. Add the app's `WindowsApps\OpenAI.Codex_*` package directory and the
   repo to Defender exclusions. Point `TMP`/`TEMP` at a small clean directory. Keep
   `writable_roots` short and never include the uv cache. If the machine had an older Codex
   install, check the drive root and user folder for stale explicit `CodexSandboxUsers`
   ACEs (#33049). Try `[features] unified_exec = true`. Weigh
   `[windows] sandbox = "unelevated"` against the `apply_patch` split-root failure. If a
   standalone CLI is also installed, `codex doctor` reports sandbox diagnostics since 0.150.
2. **Defender.** Exclude the repo, `%LOCALAPPDATA%\uv`, and the Python install, or add
   process exclusions for `python.exe`, `uv.exe`, `git.exe`, `pwsh.exe`. Better: put repos
   and the uv cache on a Dev Drive so scanning goes asynchronous without exclusions.
3. **git.** Keep `core.fscache` (the installer default; confirm with
   `git config --show-origin core.fscache`) and set `git config --global core.untrackedCache true`,
   which is cheap and safe on NTFS. Treat `core.fsmonitor` as a per-repo opt-in for large
   worktrees, not a global default: it costs a daemon per karta item worktree for a saving
   measured in tens of milliseconds on small repos. If it is on, run
   `git fsmonitor--daemon stop` in a worktree before removing it. None of this touches
   karta's own ref-reading scripts; it helps `status`, `diff`, merges, and `worktree add`.
4. **uv.** Set `UV_PYTHON` to the interpreter path (or `uv python pin`) so discovery is
   skipped, and run `uv python install 3.12` once so a first run never downloads.
5. **PowerShell.** Install pwsh 7 (Copilot needs it anyway), keep `$PROFILE` light, and on
   an offline network check the CRL timeout.

### Tier 1: karta changes, cheap, roughly 2 to 4x on hook cost

1. **Codex `commandWindows`.** Call `uv run --script "%PLUGIN_ROOT%\…"` straight from cmd
   and drop the powershell.exe launcher. uv already resolves the interpreter and honours
   `requires-python`; the launcher's four `Get-Command` lookups and probe process repeat
   that work at 0.3 to 1 s per hook. If a launcher must stay, run it under
   `pwsh -NoProfile -NonInteractive` and cache the resolved interpreter path in
   `PLUGIN_DATA` instead of probing every time. The launcher's UTF-8 and exit-code
   contract can move into the guards themselves.
2. **Claude hooks to exec form.** `"command": "uv", "args": ["run", "--script",
   "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/x.py"]` spawns uv.exe directly with no bash.exe
   ([hooks reference](https://code.claude.com/docs/en/hooks)). Add `if` filters such as
   `Write(*/.karta/*)` or `Bash(git *)` so a guard only spawns when the tool input can
   matter; a non-matching `if` skips the process entirely.
3. **One dispatcher instead of three.** Merge the Write/Edit guards into a single script
   that reads the payload once and routes in-process. Three interpreter starts become one.
4. **Guards.** Import `subprocess`, `argparse`, `hashlib`, `tempfile` lazily (60 to 140 ms
   measured here). Return before importing anything when the payload cannot match. Batch
   git queries: one `for-each-ref` or `rev-parse` with several arguments instead of one
   process per fact. Tests exist for every guard, so this is a refactor with a net.
5. **Skills.** Fewer, fatter script calls per turn. Every `uv run` line in a SKILL.md is a
   pwsh, uv, python, and git chain on Copilot before the model answers. `karta_next.py` in
   particular is asked for five times across the status skill.

### Tier 2: go resident

Claude Code hooks support `http` and `mcp_tool` types as well as `command`; Codex supports
`command` and `mcp_tool` and launches matching hooks concurrently
([Codex hooks](https://learn.chatgpt.com/docs/hooks)). A karta MCP server bundled with the
plugin, or a local HTTP server started at SessionStart, answers PreToolUse in about a
millisecond with no process spawn at all. Copilot hooks are command-only but accept
`exec` plus `args`, which skips the shell
([Copilot hooks reference](https://github.com/github/docs/blob/main/content/copilot/reference/hooks-reference.md)).
This is the only path that removes the spawn floor rather than shaving it.

## Would Go help?

Some, and not where it hurts.

| Layer | Python today | Go binary | Note |
|-|-|-|-|
| interpreter start + stdlib imports | 100 to 230 ms | ~0 | the whole Go win |
| uv discovery | 40 to 80 ms here, seconds on bad machines | 0 | also fixed by `UV_PYTHON` |
| the binary's own `CreateProcess` with Defender | 22 ms+ | 22 ms+ | unchanged |
| git subprocesses | 40 to 60 ms each | 40 to 60 ms each | unchanged unless you read `.git` directly or link libgit2 |
| host shell spawn (bash.exe, pwsh, cmd) | 30 to 450 ms | 30 to 450 ms | unchanged |
| Codex sandbox setup | 15 to 90 s | 15 to 90 s | unchanged |

Published numbers agree. On Windows 11 a hello-world comparison measured Go 156 ms, Node
176 ms, Python 193 ms end to end, with PowerShell at 2.1 s
([claude-token-monitor benchmark commit](https://github.com/young1lin/claude-token-monitor/commit/7f27f94e4b5a585842b820512a2df87953728038)):
the spawn floor swallows the language difference. On Linux the gap is real, 2 to 13 ms for
Go against 10 to 90 ms for Python, which is why Go feels magical there and merely nice
here. A Python-to-Go CLI port on macOS saw 10x on startup and 30x on cold start
([tag-agent comparison](https://github.com/sanskarpan/tag-agent/blob/main/COMPARISON_REPORT.md)),
again with no Windows spawn tax in the way.

Costs of a port: a build matrix (Windows, macOS, Linux, x64 and arm64), shipping binaries
inside a git-distributed plugin, unsigned-exe SmartScreen and Defender friction (a fresh
unsigned `.exe` gets more scrutiny than a `.py`), losing `uv run --script` as the single
distribution story, and re-implementing 3,600 lines of guards plus about 20 skill scripts
that the Pi runtime already duplicates in TypeScript.

Verdict: a resident service written in Python beats a cold-started Go binary, because it
removes the spawn instead of trimming it. If a compiled artifact is still wanted after
Tier 2, make it a thin shim that talks to the resident service.

## Five-minute triage on a slow machine

1. Time a trivial command inside the host and outside it. `Measure-Command { cmd /c echo ok }`
   in a plain terminal should be under 50 ms. If the host takes seconds for the same thing,
   stop looking at karta.
2. Codex: read the `setup refresh` gap in `%USERPROFILE%\.codex\.sandbox\sandbox.*.log`.
3. `uv -v run --script <guard> < NUL` shows discovery time; a slow one is fixed by `UV_PYTHON`.
4. `pwsh -NoProfile -c exit` against `pwsh -c exit` shows the profile tax.
5. `New-MpPerformanceRecording` (admin) shows which processes Defender is spending time on.

## Appendix: the benchmark script

Run from pwsh 7 at the repo root. Adjust the Python path.

```powershell
$env:PYTHONUTF8 = '1'
function Bench([string]$label, [scriptblock]$block, [int]$n = 3) {
  $t = @(); for ($i = 0; $i -lt $n; $i++) {
    $sw = [Diagnostics.Stopwatch]::StartNew(); & $block *> $null; $sw.Stop(); $t += $sw.ElapsedMilliseconds }
  $s = $t | Sort-Object
  "{0,-58} min={1,6}ms  med={2,6}ms" -f $label, $s[0], $s[[int]($n/2)]
}
$py = 'C:\Python314\python.exe'
Bench 'cmd.exe /c exit'                     { cmd.exe /c exit }
Bench 'powershell.exe -NoProfile -c exit'   { powershell.exe -NoProfile -NonInteractive -Command exit }
Bench 'pwsh -NoProfile -c exit'             { pwsh -NoProfile -NonInteractive -Command exit }
Bench 'git rev-parse --show-toplevel'       { git rev-parse --show-toplevel }
Bench 'python -c pass'                      { & $py -c pass }
Bench 'python -I -S -c pass'                { & $py -I -S -c pass }
Bench 'python guard imports'                { & $py -c 'import json,re,subprocess,pathlib,argparse,hashlib,tempfile' }
Bench 'uv --version'                        { uv --version }
Bench 'uv run guard_auditor_dispatch'       { '{}' | uv run --script hooks/scripts/guard_auditor_dispatch.py }
Bench 'python guard_auditor_dispatch'       { '{}' | & $py hooks/scripts/guard_auditor_dispatch.py }
Bench 'uv run inject_karta_status'          { '{}' | uv run --script hooks/scripts/inject_karta_status.py }
Bench 'uv run guard_delivery_stop'          { '{}' | uv run --script hooks/scripts/guard_delivery_stop.py }
Bench 'Codex chain cmd->powershell->launcher->python' {
  cmd.exe /c "powershell -NoProfile -ExecutionPolicy Bypass -File .codex-plugin\hooks\launch_hook.ps1 .codex-plugin/hooks/scripts/guard_binder_immutability.py GitTop < NUL" }
Bench 'uv run karta_next --help'            { uv run --script skills/karta-status/scripts/karta_next.py --help }
```
