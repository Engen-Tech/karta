# Hooks: rules the agent cannot skip

karta's most important rules live in its skills, as instructions. Instructions can be skipped — an agent under context pressure sometimes does. Hooks close that gap for the rules that matter most: they are small scripts the harness itself runs around tool calls, deterministically, before the agent's judgment enters the picture. A hook that says no ends the tool call with a reason; the agent cannot talk its way past it.

Hooks are a backstop, not a replacement. Every skill still states its rules in full, so a runtime without hook support behaves the same by doctrine.

## What is enforced, and where

| Rule | Runtime | What happens |
|-|-|-|
| Committed binders are read-only | Claude Code (plugin hook) | Any `Write`, `Edit`, or `NotebookEdit` to a `.karta/binders/*.json` that exists in `HEAD` is blocked — including delivered binders under `.karta/binders/archive/`. Untracked binders — plan drafts — pass. |
| Pack edits must validate | Claude Code (plugin hook) | A `Write` of a `.md` file under `.karta/sme/` is checked against the pack validator before it lands and blocked with the findings. After an `Edit` or `Write`, the file on disk is checked again; a failure comes back as feedback the agent must fix. |
| Safety-auditor dispatch is complete | Claude Code (plugin hook) | Dispatching `karta-safety-auditor` without a binder path — or, when the binder pins packs, without the resolved rule checklists — is blocked, naming the pinned ids. |
| The kaizen writer stays inside its surface | Claude Code (plugin hook) | A kaizen `Write`, `Edit`, or `NotebookEdit` is blocked unless the target is under `.karta/sme/` or is exactly `.karta/kaizen.json`; the denial restates kaizen's own rule. Main-thread writes and every other agent pass untouched. |
| You see your binders at session start | Claude Code (plugin hook) | At session start: one short line per binder in `.karta/binders/` (slug, item count, pinned packs). Delivered binders — archived to `.karta/binders/archive/` — are excluded. About ten lines at most; silent when there are none. Informs only — never blocks. |
| A delivery may not end dirty | Claude Code (plugin hook) | Built-but-unmerged items (a `built` ref with no `done`) or a complete-but-unarchived binder block the session's stop once, with the fix named in the reason. A second stop in the same state passes — a nudge, not a wall. |
| Commits in this repo pass the gate suite | Claude Code (this repo's project settings) | A `git commit` in a karta checkout first runs the repo's sync and validation gates; a failing gate blocks the commit with its output. Not shipped in the plugin. |
| karta never pushes | Codex CLI (this repo's `.codex/rules/karta.rules`) | `git push` asks you first; a flag-first `git push --force` (or `git push -f`) is forbidden outright. A force flag buried later in the command (`git push origin main --force`) still lands on the ask-first rule — prefix rules match from the start of the command. Copy the file into your own project's `.codex/rules/` for the same protection there. |

The first six ship in the plugin: install karta and they are active in every project you use it in. The last two live in this repository and protect karta's own development.

## How a hook decides

Each hook is a stdlib-only Python script under `hooks/scripts/`, registered in `hooks/hooks.json` at the plugin root. The harness hands it the tool call as JSON on stdin. Exit 0 allows the call; exit 2 blocks it — or, on a check that runs after the fact, returns corrective feedback — with a one-paragraph reason on stderr.

If a script hits an internal error, it allows the call: enforcement must never break normal work. The two exceptions are the guards whose whole point is to fail closed — `guard_auditor_dispatch.py`, which blocks a dispatch it recognizes unless the required evidence is present, and `guard_writer_confinement.py`, which blocks a kaizen write it recognizes unless the target is inside kaizen's surface. Any shape they do not recognize passes. Every script has a `--self-test`, and `validate_plugin.py` checks the manifest, the scripts, and their self-tests.

## The prompts you will see, once

- **Plugin hooks** come with the plugin. Installing karta is the consent; there is no separate prompt per hook.
- **Project settings hooks** — the commit gate in this repo — need your approval. The first time Claude Code finds hooks in a project's `.claude/settings.json`, and again whenever they change, it asks you to review them before they run. Until you approve, they don't run.
- **Codex rules** load only once you mark the project trusted. Codex asks when you first open it.

## Override or disable a plugin hook

Plugin hooks sit at the lowest layer of Claude Code's settings precedence. Anything above them — your user settings (`~/.claude/settings.json`), a project's `.claude/settings.json`, or its `.claude/settings.local.json` — can override or disable one for that scope. Disabling the karta plugin removes them all. The skills keep stating the same rules either way, so turning a hook off weakens the enforcement, not the doctrine.

## The commit gate and its escape hatch (contributors)

The commit gate exists because every pack and skill in this repo has generated mirror copies, and nothing else checks them at commit time. On `git commit` it runs `check_shared_copies.py`, `sync_codex_skills.py --check`, `sync_codex_agents.py --check`, `validate_plugin.py`, and the pack validator over `skills/_shared/sme/`, and blocks the commit if any gate fails.

Sometimes a partial commit is the point — say, committing a canonical skill edit before regenerating the mirrors. For that one command, set the escape hatch:

```bash
KARTA_SKIP_GATE=1 git commit -m "wip: canonical edit, mirrors follow"
```

It skips the gate for that command and nothing else. It has no effect on the plugin hooks or the Codex rules.

## Why Claude Code and Codex differ (for now)

The asymmetry is deliberate. Claude Code gets hook enforcement now. Codex keeps three layers of its own — the skill doctrine, its OS-level sandbox, and the execpolicy rules — until its hooks feature stabilizes upstream. kaizen's writer confinement follows the same split: on Codex it stays doctrine plus the OS sandbox, because Codex cannot register plugin subagents and its hooks surface is not yet stable. The hook scripts are written runtime-agnostic (JSON on stdin, exit 2 blocks, reason on stderr) precisely so the same scripts — `guard_writer_confinement.py` included — can back a Codex hooks manifest in a later phase, with no rewrite. The delivery Stop-gate follows the same split: Codex has no Stop surface upstream, so its delivery-end doctrine stays skill prose there — and `guard_delivery_stop.py` is written runtime-agnostic like the others, so backing a future Codex Stop surface with it would need no rewrite. Until then, Codex behavior does not regress: everything the hooks enforce is still stated in the skills.

Design and rationale: the [phase 1 spec](../specs/2026-07-06-hooks-phase1-design.md).
