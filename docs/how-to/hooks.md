# Hooks: rules the agent cannot skip

karta's most important rules live in its skills, as instructions. Instructions can be skipped — an agent under context pressure sometimes does. Hooks close that gap for the rules that matter most: they are small scripts the harness itself runs around tool calls, deterministically, before the agent's judgment enters the picture. A hook that says no ends the tool call with a reason; the agent cannot talk its way past it.

Hooks are a backstop, not a replacement. Every skill still states its rules in full, so a runtime without hook support behaves the same by doctrine.

## What is enforced, and where

| Rule | Runtime | What happens |
|-|-|-|
| Committed binders are read-only | Claude Code (plugin hook) | Any `Write`, `Edit`, or `NotebookEdit` to a `.karta/binders/*.json` that exists in `HEAD` is blocked — including delivered binders under `.karta/binders/archive/`. Untracked binders — plan drafts — pass. |
| Pack edits must validate | Claude Code (plugin hook) | Two rules run here. First, a `Write` of a `.md` file under `.karta/sme/` is checked against the pack validator before it lands and blocked with the findings. Second, a write that would fork a shipped built-in — a copy carrying a local edit over the original — is denied with a message containing `illegal shadow: a local delta over the shipped built-in`. After an `Edit` or `Write`, the file on disk is classified again; an illegal shadow comes back as feedback the agent must fix. |
| Safety-auditor dispatch is complete | Claude Code (plugin hook) | Dispatching `karta-safety-auditor` without a binder path — or, when the binder pins packs, without the resolved rule checklists — is blocked, naming the pinned ids. |
| The confined writers stay inside their surfaces | Claude Code (plugin hook) | A `Write`, `Edit`, `NotebookEdit`, or `Bash` call from a confined writer is checked against that writer's surface: kaizen may touch only `.karta/sme/` and `.karta/kaizen.json`; doc-gardner only the prose-doc surface (README, docs/, AGENTS.md, ARCHITECTURE, .gitignore). A `Bash` command is statically parsed for file writes and deletes — a clear out-of-surface target is blocked, and a command too ambiguous to pin down is blocked rather than guessed at. Main-thread calls and every other agent pass untouched. |
| A vanished build item is called out | Claude Code (plugin hook) | When a karta-typed subagent stops while a live binder holds an item that produced nothing — no completion trace at all — the stop is turned back once with the stranded ids, so the final report names them and the orchestrator re-derives its frontier. A nudge, not a wall; unrelated subagents pass untouched. |
| You see your binders at session start | Claude Code (plugin hook) | At session start: one short line per binder in `.karta/binders/` (slug, item count, pinned packs). Delivered binders — archived to `.karta/binders/archive/` — are excluded. About ten lines at most; silent when there are none. Informs only — never blocks. |
| A delivery may not end dirty | Claude Code (plugin hook) | Built-but-unmerged items (a `built` ref with no `done`) or a complete-but-unarchived binder block the session's stop once, with the fix named in the reason. A second stop in the same state passes — a nudge, not a wall. |
| Commits in this repo pass the gate suite | Claude Code (this repo's project settings) | A `git commit` in a karta checkout first runs the repo's sync and validation gates; a failing gate blocks the commit with its output. Not shipped in the plugin. |
| karta never pushes | Codex CLI (this repo's `.codex/rules/karta.rules`) | `git push` asks you first; a flag-first `git push --force` (or `git push -f`) is forbidden outright. A force flag buried later in the command (`git push origin main --force`) still lands on the ask-first rule — prefix rules match from the start of the command. Copy the file into your own project's `.codex/rules/` for the same protection there. |

The first seven ship in the plugin: install karta and they are active in every project you use it in. The last two live in this repository and protect karta's own development. Three of the plugin rules also ship Codex-side twins — see the runtime parity table in [the Codex how-to](codex.md).

### The pack-write guard is a deterrent, not the gate

The illegal-shadow half of the pack-write guard (`guard_pack_write.py`) turns a fork away as you author it, but be honest about its reach: it is a deterrent for interactive editing only, not the authoritative gate. A `git pull`, a merge, a `git checkout`, an outside editor, or a sync tool can still drop a shadow copy into `.karta/sme/` without ever triggering the hook — the guard sees only tool calls in the session it runs in. The authoritative check is at plan time, where karta classifies every pack and warns loudly on a shadow before the build proceeds (that warning becomes a halt in karta 3.0.0). The deny message never claims to be the last word — it is a fast, local nudge that catches the common case early.

## How a hook decides

Each hook is a stdlib-only Python script under `hooks/scripts/`, registered in `hooks/hooks.json` at the plugin root. The harness hands it the tool call as JSON on stdin. Exit 0 allows the call; exit 2 blocks it — or, on a check that runs after the fact, returns corrective feedback — with a one-paragraph reason on stderr.

If a script hits an internal error, it allows the call: enforcement must never break normal work. The two exceptions are the guards whose whole point is to fail closed — `guard_auditor_dispatch.py`, which blocks a dispatch it recognizes unless the required evidence is present, and `guard_writer_confinement.py`, which blocks a write from a confined writer (kaizen or doc-gardner) it recognizes unless the target is inside that writer's surface. Any shape they do not recognize passes. Every script has a `--self-test`, and `validate_plugin.py` checks the manifest, the scripts, and their self-tests.

## The prompts you will see, once

- **Plugin hooks on Claude Code** come with the plugin. Installing karta is the consent; there is no separate prompt per hook.
- **Bundled Codex hooks** are not auto-trusted: Codex skips a plugin's hooks until you review and trust each hook definition. The Codex-side rows in [the parity table](codex.md) are live only after that review.
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

## Where Claude Code and Codex still differ

Codex now gets bundled hook enforcement too: the plugin ships a Codex hooks manifest with payload-native twins of three guards — binder immutability (parsing raw `apply_patch` bodies), pack-write validation, and the delivery Stop-gate (Codex ships a Stop surface upstream). The remaining asymmetry is scoped, not wholesale, and each remaining doctrine-only rule states its real reason in [the Codex parity table](codex.md): auditor-dispatch inspection has no Codex twin yet (Codex does emit dispatch hook events; karta has not shipped an inspector for them), and writer confinement stays doctrine there because karta registers no kaizen or doc-gardner agent on Codex for a hook to recognize. Every guard is written runtime-agnostic (JSON on stdin, exit 2 blocks, reason on stderr), so closing those last gaps is wiring, not a rewrite — and Codex behavior never regresses either way: everything the hooks enforce is still stated in the skills.

Design and rationale: the [phase 1 spec](../specs/2026-07-06-hooks-phase1-design.md).
