# Use karta with Pi

Karta ships as one Pi package. Pi loads the package-owned extension, then exposes Karta's ten skills only in a trusted project. Build, verification, and delivery enter through the fixed `karta_dispatch` tool; project files cannot replace its prompts, roles, tools, or scripts.

The Pi package is not published to npm. Install an approved Git tag or a local checkout until publication is separately approved.

## Requirements

You need:

- Pi 0.84.2 or a later tested version;
- Node.js 22.19 or newer;
- Git with worktree and `merge-tree --write-tree` support;
- `uv` for Karta's bundled Python scripts;
- the package manager and toolchain used by the project you are delivering.

Karta 3.0.0 supports native macOS and Linux. Native Windows support is deferred to a later release.

A build worker can run Bash. Its worktree separates concurrent changes, but it is not a sandbox for hostile code. Run Karta only in projects and with models you trust to execute the project's build commands.

## Install

Install a reviewed tag rather than a moving branch.

Mac or Linux, local terminal:

```sh
pi install https://github.com/Engen-Tech/karta.git@<approved-tag>
pi list
```

For package development, install the checkout directly.

Mac or Linux, local terminal from outside the Karta checkout:

```sh
pi install /absolute/path/to/karta
pi list
```

Use `-l` to record the package in the current project's `.pi/settings.json` instead of your user settings.

Mac or Linux, local terminal in the consumer repository:

```sh
pi install -l https://github.com/Engen-Tech/karta.git@<approved-tag> --approve
```

Restart Pi after installing or updating so it reloads package resources.

## Trust the project

Karta does nothing authoritative in an untrusted project:

- its skills are not exposed;
- `karta_dispatch` refuses every action;
- `karta_script` refuses bundled script execution.

Start Pi in the repository and approve it when Pi asks. For one explicitly approved invocation, use `--approve`.

Mac or Linux, local terminal in the consumer repository:

```sh
pi --approve
```

Approval covers project-local resources. It does not let a project replace Karta's package-owned roles or child-session policy.

## Run Karta

Ask for the skill in ordinary language:

- `Plan this feature with karta.`
- `Deliver binder checkout-refresh.`
- `Build item api-contract from binder checkout-refresh.`
- `Show karta status.`

When Pi has `karta_dispatch`, the build, verify, and deliver skills call it instead of reproducing the legacy orchestration in the parent session.

|Fixed action|Authority|
|-|-|
|`buildItem`|Creates or resumes one item worktree, dispatches the isolated worker, runs host checks and gates, and writes `built` or `failed` last|
|`runVerification`|Runs the package-owned acceptance and safety gates against exact Git evidence|
|`deliverBinder`|Owns dependency waves, serial integration, rollback, human acceptance, enabled companion writers, and final binder archival|
|`inspectItemState`|Reports the item frontier derived from Git|
|`describeRole`|Reports role hashes and capabilities without exposing prompt text or paths|
|`preflightGate`|Proves the selected provider and model can answer in an isolated gate runtime|

Callers provide identity, not authority. They cannot choose a prompt, model, provider hook, command, tool set, evidence path, ref, or timeout through these actions.

## Visual oracles are not accepted on Pi yet

The Pi package runs the behavioral gates but has no visual-fidelity judge. A `visual`-oracle item still passes through the safety gate, but its acceptance blocks: full build or delivery verification returns `blocked` with the reason `visual-required` and writes no merge or completion ref. This is a fail-closed stop, never a silent pass of an unchecked view. Behavioral oracles run in full.

## Provision the check environment

The Pi host reruns your floor and oracle commands in fresh, disposable worktrees that do not share a build worker's installed dependencies. Declare what those worktrees need in `.karta/environment.json`, read from the delivery's committed integration ref (never the mutable working tree):

```json
{
  "preflight": "docker info",
  "on_unavailable": "Docker is reachable only through Incus here; start it, or run this on CI which has it natively.",
  "setup": "uv sync --frozen"
}
```

All three keys are optional and independent:

- `preflight` runs first, before setup and before any floor or oracle command. It is a cheap precondition probe — is the daemon up, is the toolchain present. When it fails, the item halts as `blocked` and the message carries your `on_unavailable` text verbatim. The real floor command is never run into the wall, and the worker is not re-prompted (a missing daemon is not fixed by another implementation attempt).
- `on_unavailable` is the remediation shown on a failed preflight — the precondition your binder already knows about, said once, up front, actionably.
- `setup` provisions dependencies into a gitignored directory. It must touch only gitignored paths; a setup that mutates a tracked file is refused so nothing rides unreviewed into the merged tree.

## Enable companion writers

Doc-gardner and Kaizen are opt-in delivery phases.

Enable doc-gardner with `.karta/doc-gardner.json`:

```json
{"enabled": true}
```

Enable Kaizen with `.karta/kaizen.json`:

```json
{"enabled": true}
```

When enabled, each writer runs in a disposable worktree with read, inventory, search, write, and edit tools—but no Bash. The host validates its observed diff, checks the exact candidate, reproduces commit hooks, and creates the commit. Doc-gardner writes only documentation surfaces. Kaizen writes only `.karta/sme/` and its config and cannot remove or weaken an existing rule. The binder moves to `.karta/binders/archive/` only after both enabled phases finish.

## Supported providers

Karta's gate children do not inherit the parent session. Each child gets an in-memory session, the exact selected model, package-owned prompt text, and two fixed read-only tools.

|Provider or credential class|Support|
|-|-|
|Stored API key|Supported; complete acceptance and safety tool roundtrips are tested|
|Environment API key|Supported when the provider declares the environment variable|
|Pi runtime key (`--api-key`)|Supported; copied into the isolated runtime without being returned in evidence|
|Declarative custom provider|Supported when its model and static request configuration can be copied without executable hooks|
|Built-in OAuth provider|Supported when the same stored OAuth credential is available to the child; tested live with `openai-codex`|
|Dynamic native provider|Rejected because it is executable ambient extension code|
|Declarative provider with `streamSimple`, `refreshModels`, or extension OAuth callbacks|Rejected because those fields execute ambient code|

Static headers in a declarative provider are copied. Parent `before_provider_request` and `before_provider_headers` handlers are not replayed. If your provider depends on those transformations, Karta stops during preflight rather than silently running a different request. Represent required static headers in the declarative provider configuration or use a supported built-in provider.

Karta requires the exact provider/model pair to exist in the isolated runtime. It never substitutes a nearby model after the parent session has selected one.

## Resume after interruption

Git is the recovery source. Pi sessions and model conversations are not delivery state.

Run the same build or delivery request again. Karta derives the next action from:

- `karta/<binder>/integration` and item branches;
- item commit markers;
- `refs/karta/<binder>/...` completion refs;
- wave base, success, and rollback tags;
- companion and archive commits.

Karta repairs only exact crash shapes it recognizes. It does not reset a dirty worktree, steal an ambiguous lock, rewrite a foreign branch, or guess through contradictory refs.

Pi cannot veto process shutdown. Before a successful action returns, Karta writes the durable Git checkpoint first. On shutdown it aborts child sessions and owned process groups, then releases its own locks. Restart Pi and rerun the same request.

## Recover a dispatch lock

Each binder lock is a directory under the repository's Git common directory:

`<git-common-dir>/karta-locks/<binder>.lock/owner.json`

A lock records the process id, process-start identity, nonce, repository, and binder. Karta never steals a lock automatically, even when the recorded process appears dead.

If Karta reports a stale lock:

1. Stop every Pi process that might still own that binder.
2. Read `owner.json` and confirm its process id and process-start identity no longer name a live process.
3. Confirm no second machine is delivering the same repository over a shared filesystem.
4. Remove only that binder's lock directory.
5. Rerun the same delivery request.

Do not remove a readable live lock to "unstick" a run. Two writers on one integration branch are not a recovery strategy; they are a small, efficient chaos generator.

## Update, roll back, and uninstall

For an unpinned Git source, update that source directly.

Mac or Linux, local terminal:

```sh
pi update https://github.com/Engen-Tech/karta.git
```

For a pinned release, install the new tag, verify it, then remove the old source. Roll back by installing the previous reviewed tag again. Pi keys removal by the original source string, so use the same string shown by `pi list`.

Mac or Linux, local terminal:

```sh
pi remove https://github.com/Engen-Tech/karta.git@<approved-tag>
pi list
```

`pi uninstall` is an alias for `pi remove`. Removing a managed Git package also removes its checkout and prunes empty cache directories. It does not delete your binders, branches, tags, refs, worktrees, or delivery commits.

## Test the package before sharing it

Every Pi package change must pass the packed-artifact smoke test before it goes to a remote.

Mac or Linux, local terminal in the Karta checkout:

```sh
npm run smoke:pi-package
```

The script runs `npm pack`, extracts the resulting artifact into a disposable directory, and loads that package into the installed `pi` executable with isolated settings. It verifies all ten skills, calls the package-owned `karta_dispatch` tool through a controlled model, removes the package, and leaves the user's real Pi settings untouched. Loading the checkout directly with `-e` does not satisfy this test.

## Current support matrix

This matrix records native runs, not architectural guesses. A container result may diagnose a problem but does not turn a missing native run green.

|Surface|Status|Evidence|
|-|-|-|
|macOS 26.5.2 arm64|Pass|Pi 0.84.2, Node 25.2.1, Apple Git 2.50.1; full suite and validators|
|Linux x86_64|Pass|Debian 13, Pi 0.84.2, Node 25.2.1, Git 2.47.3, uv 0.11.25; full suite, validators, package lifecycle, and production audit|
|Windows x86_64|Not supported in 3.0.0|Native Windows support is deferred to a later release|
|SHA-1 repositories|Pass on macOS and Linux|Native Git fixture suite|
|SHA-256 repositories|Pass on macOS and Linux|Native Git fixture suite|
|Spaces, Unicode, symlinked local install, linked worktrees|Pass on macOS and Linux|Package lifecycle and lock fixtures|
|Stored, environment, runtime-key, and declarative provider roundtrips|Pass|Controlled provider performs preflight, role tool calls, and strict verdicts|
|Stored OAuth roundtrip|Pass on macOS|Live `openai-codex/gpt-5.6-sol` preflight, tool call, and verdict|
|TUI, print, JSON, and RPC fixed-action smoke|Pass on supported platforms|A controlled provider makes Pi call `karta_dispatch`, consume its result, and finish in each mode on macOS and Linux|

The supported-platform release matrix passes. Publication remains blocked until licensing and ownership approval are explicit and `private: true` is deliberately removed.
