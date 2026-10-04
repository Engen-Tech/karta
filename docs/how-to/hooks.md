# Hooks: rules the agent cannot skip

karta's most important rules live in its skills, as instructions. Instructions can be skipped — an agent under context pressure sometimes does. Hooks close that gap for the rules that matter most: they are small scripts the harness itself runs around tool calls, deterministically, before the agent's judgment enters the picture. A hook that says no ends the tool call with a reason; the agent cannot talk its way past it.

Hooks are a backstop, not a replacement. Every skill still states its rules in full, so a runtime without hook support behaves the same by doctrine.

## What is enforced, and where

| Rule | Runtime | What happens |
|-|-|-|
| Committed binders are read-only | Claude Code (plugin hook) | Any `Write`, `Edit`, `MultiEdit`, or `NotebookEdit` to a committed `.karta/binders/*.json` is blocked, including resolved `./` paths, directory symlink aliases, hard links, and archived binders. When the edit arrives as a raw patch string (`*** Begin Patch`, as Copilot sends it), every path the patch touches is checked, move targets included, and `\` separators count as `/`. Shell writes are not routed to this guard. A wrong plan is replaced by a successor binder; see [binder-repair.md](binder-repair.md). |
| Pack edits must validate | Claude Code (plugin hook) | A `Write` of a `.md` file under `.karta/sme/` is checked before it lands. After an `Edit`, `MultiEdit`, or `Write`, the file on disk is checked again. A raw patch string (`*** Begin Patch`) is checked file by file: an added file as a `Write` of its content, an updated or moved file as an `Edit`; `\` separators count as `/`. |
| Safety-auditor dispatch is complete | Claude Code (plugin hook) | Dispatching `karta-safety-auditor` without a binder path — or, when the binder pins packs, without the resolved rule checklists — is blocked, naming the pinned ids. |
| Gate dispatches carry a real, sized diff | Claude Code (plugin hook) | Dispatching `karta-acceptance-reviewer` or `karta-safety-auditor` with no diff range, an empty diff, a missing `Diff-size: <files> files, <bytes> bytes` line, or one that doesn't match what git recomputes is blocked before either reviewer context spins up. |
| The confined writers stay inside their surfaces | Claude Code (plugin hook) | A `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, or `Bash` call from a confined writer is checked against that writer's surface. Shell syntax and newlines before a command do not hide it; ambiguous write targets fail closed. |
| The gate reviewers write nothing | Claude Code (plugin hook) | Edit tools from the three gate reviewers are blocked. Their `Bash` is held to a fail-closed allowlist of read-only tools and read-only git subcommands; compound syntax, expansion, wrappers, write options, programs named by path, and unlisted programs are blocked. This is a pre-operation hook, not a sandbox: git may still run programs named by repository configuration. |
| A vanished build item is called out | Claude Code (plugin hook) | When a karta-typed subagent stops while a live binder holds an item that produced nothing — no completion trace at all — the stop is turned back once with the stranded ids, so the final report names them and the orchestrator re-derives its frontier. A nudge, not a wall; unrelated subagents pass untouched. |
| You see your binders at session start | Claude Code (plugin hook) | At session start: one short line per binder in `.karta/binders/` (slug, item count, pinned packs). Delivered binders — archived to `.karta/binders/archive/` — are excluded. About ten lines at most; silent when there are none. Informs only — never blocks. The same hook also fires a fail-open Karta Watch hub revive and, for an opted-in repo, appends one line with the watch URL or a revive nudge — see [karta-watch.md](karta-watch.md). |
| A delivery may not end dirty | Claude Code (plugin hook) | Built items with no valid `done`, unfinished accepts, `done` refs that fail delivery provenance, or a complete-but-unarchived binder block the stop once. The identical second stop passes — a nudge, not a wall. |
| Commits in this repo pass the gate suite | Claude Code (this repo's project settings) | A `git commit` in a karta checkout first runs the repo's sync and validation gates; a failing gate blocks the commit with its output. Not shipped in the plugin. |
| karta never pushes | Codex CLI (this repo's `.codex/rules/karta.rules`) | `git push` asks you first; a flag-first `git push --force` (or `git push -f`) is forbidden outright. A force flag buried later in the command (`git push origin main --force`) still lands on the ask-first rule — prefix rules match from the start of the command. Copy the file into your own project's `.codex/rules/` for the same protection there. |

The first nine ship in the plugin: install karta and they are active in every project you use it in. The last two live in this repository and protect karta's own development. Seven of the plugin rules also ship Codex-side twins — see the runtime parity table in [the Codex how-to](codex.md).

### The pack-write guard checks form, not ownership

The pack-write guard (`guard_pack_write.py`) enforces well-formedness only: a pack file written under `.karta/sme/` must pass the pack validator. It has nothing to say about whose copy it is or whether it matches the shipped built-in. Ownership is settled at plan time, and it is informational — karta classifies every pack copy and reports a local fork with its one consequence, that the copy no longer receives upstream pack updates. Nothing is blocked over it; your edits always win. The guard also sees only tool calls in the session it runs in, so a `git pull`, a merge, an outside editor, or a sync tool can drop a pack file into `.karta/sme/` without triggering it. That is fine: plan-time classification reads the files as they are, however they arrived.

## How a hook decides

Each hook is a stdlib-only Python script under `hooks/scripts/`, registered in `hooks/hooks.json` at the plugin root. The harness hands it the tool call as JSON on stdin. Exit 0 allows the call; exit 2 blocks it — or, on a check that runs after the fact, returns corrective feedback — with a one-paragraph reason on stderr.

If a script hits an internal error, it allows the call: enforcement must never break normal work. The three exceptions are the guards whose whole point is to fail closed — `guard_auditor_dispatch.py`, `guard_gate_dispatch.py`, and `guard_writer_confinement.py`. The last blocks confined-writer calls outside the resolved surface and gate-reviewer calls outside the read-only allowlist. Any shape they do not recognize passes. Every script has a `--self-test`, and `validate_plugin.py` checks the manifest, scripts, and self-tests.

## The prompts you will see, once

- **Plugin hooks on Claude Code** come with the plugin. Installing karta is the consent; there is no separate prompt per hook. They run only if Claude Code can start them; a missing `uv` is a non-blocking hook error, so the tool call proceeds unguarded. Check launchers with `python3 scripts/install_smoke.py`.
- **Bundled Codex hooks** are not auto-trusted: Codex skips a plugin's hooks until you review and trust each hook definition. The Codex-side rows in [the parity table](codex.md) are live only after that review.
- **Project settings hooks** — the commit gate in this repo — need your approval. The first time Claude Code finds hooks in a project's `.claude/settings.json`, and again whenever they change, it asks you to review them before they run. Until you approve, they don't run.
- **Codex rules** load only once you mark the project trusted. Codex asks when you first open it.

## Override or disable a plugin hook

Plugin hooks sit at the lowest layer of Claude Code's settings precedence. Anything above them — your user settings (`~/.claude/settings.json`), a project's `.claude/settings.json`, or its `.claude/settings.local.json` — can override or disable one for that scope. Disabling the karta plugin removes them all. The skills keep stating the same rules either way, so turning a hook off weakens the enforcement, not the doctrine.

## The commit gate and its escape hatch (contributors)

The commit gate exists because every pack and skill in this repo has generated mirror copies, and nothing else checks them at commit time. On `git commit` it runs `check_shared_copies.py`, `sync_codex_skills.py --check`, `sync_codex_agents.py --check`, `validate_plugin.py`, and the pack validator over `skills/_shared/sme/`, and blocks the commit if any gate fails.

The same hook carries a release block. A version bump must include a green full-gate result read from the bytes Git will commit, with the new plugin version and a source fingerprint matching the source being committed. It must cover `benchmarks/gate/release-required.json`: every required vector present and PASS, with partial coverage only where the inventory records a reason. The hook validates the result structure instead of trusting its summary. Missing, red, malformed, mismatched, uncovered, or unstaged evidence blocks with the exact fix named.

Sometimes a partial commit is the point — say, committing a canonical skill edit before regenerating the mirrors. For that one command, set the escape hatch:

```bash
KARTA_SKIP_GATE=1 git commit -m "wip: canonical edit, mirrors follow"
```

It skips the gate for that command and nothing else. It has no effect on the plugin hooks or the Codex rules.

On Copilot CLI the gate runs from a separate manifest; see [the Copilot CLI guide](copilot-cli.md#scope-and-limits).

The hatch counts only as a leading assignment on the commit command itself — `KARTA_SKIP_GATE=1 git commit …`, optionally after other `NAME=value` prefixes or an earlier `&&` step. Mentioning it anywhere else does nothing: in the commit message, in a path, as another variable's value, or on a different command in the chain (`echo KARTA_SKIP_GATE=1 && git commit`). Only the value `1` counts; `KARTA_SKIP_GATE=10` does not. Setting it in the hook's environment still works too.

The gate deliberately errs toward firing, so it also trips on commands that merely mention a commit, such as `grep -n "git commit" notes.md`. The same prefix on that command escapes it: `KARTA_SKIP_GATE=1 grep -n "git commit" notes.md`. Any later command that the text reaches, through a pipe or a heredoc or a file an earlier command wrote it to, needs the prefix too, because it could run it. Plain filters are the exception, since they can't run what they read: `head`, `tail`, `grep`, `wc`, `sort` and similar need no prefix. So `KARTA_SKIP_GATE=1 grep -n "git commit" notes.md | tail -5` escapes, but `KARTA_SKIP_GATE=1 echo "git commit …" | bash` does not. Every commit the line runs needs its own prefix, including one handed to `sh -c` or `bash -c`. Later commands that don't touch that text, such as `&& git push`, need nothing. Nothing escapes a command that runs a commit through `$(…)` or backticks. That commit runs before, and outside, whatever command carries the prefix.

## Where Claude Code and Codex still differ

Codex gets bundled twins of seven guards. Writer confinement and reviewer write denial remain outside the Codex hook surface because `PreToolUse` does not say which subagent made the call. Registered reviewers use Codex's read-only sandbox unless live session permissions replace it; a plugin fallback has only its instructions. Closing that gap needs subagent identity in Codex's tool-call payload, not another parser.

Design and rationale: the [phase 1 spec](../specs/2026-07-06-hooks-phase1-design.md).
