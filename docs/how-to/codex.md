# Use karta with Codex CLI

karta runs on Codex CLI with the same skills and gate logic it uses on Claude Code. Where Codex's hook surface can express a karta guard, the plugin bundles a Codex version of it; where it cannot, the rule stays doctrine and this guide says so plainly. This guide explains both installation modes, the fallback agents, and the boundaries you can rely on.

## Two ways to install

### As a plugin (any project)

karta is packaged as a Codex plugin published through the repo marketplace (`.agents/plugins/marketplace.json`). The marketplace points at `./plugins/karta`, a generated real-directory install projection of the canonical `.codex-plugin/plugin.json` and `skills/` tree.

1. In Codex, open the plugin browser: `/plugins`.
2. Add this repository as a marketplace source and install **karta**.
3. The skills are available immediately — including `karta-plan`, `karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`, `karta-kaizen`, `karta-plainlanguage`, `karta-doc-gardner`, `karta-status`, and `karta-debt`.

From the CLI, the equivalent commands are:

```bash
codex plugin marketplace add https://github.com/Engen-Tech/karta.git
codex plugin add karta@karta-local
```

Invoke a skill explicitly with `$karta-plan` (type `$` to mention a skill, or `@karta` to scope to the plugin), or just describe the task and let Codex match a skill by its description.

### Clone and run (repo-local)

Run `codex` from inside a karta checkout. Codex scans `.agents/skills/` from your working directory up to the repo root and discovers all the skills with no install step. The mirror is committed real directories, so this works the same on macOS, Linux, and Windows.

## The acceptance gate runs automatically

karta's behavioral gate (`karta-verify`) dispatches two read-only agents — `karta-acceptance-reviewer` and `karta-safety-auditor`. Codex plugins cannot register subagents, so karta makes the gate work everywhere without any manual setup:

| How karta reached you | Where the gate agent comes from | Read-only enforcement |
|-|-|-|
| Plugin install | instructions bundled inside the `karta-verify` skill (`references/*.agent.md`), spawned as a fresh fallback agent | instruction-enforced by the bundled agent; sandbox-enforced only when the Codex host starts that agent or session read-only |
| Repo checkout, or a project that has `.codex/agents/*.toml` | the registered `.codex/agents/karta-*.toml` subagent | sandbox-enforced (`sandbox_mode = "read-only"`) |

You never copy a fallback instruction file. On a bare plugin install, the bundled agent says not to write; a read-only Codex sandbox makes that boundary enforceable. When registered `.codex/agents/*.toml` files are present, their read-only sandbox supplies that enforcement automatically. If you want the registered form in your own project, copy `.codex/agents/karta-acceptance-reviewer.toml` and `.codex/agents/karta-safety-auditor.toml` from this repo into your project's `.codex/agents/`.

## Feature compatibility on Codex

The installed plugin passed live, feature-by-feature Codex tests for fallback gates, Kaizen, and Plannotator. Read the [compatibility result](../showcase/codex-1.19-compatibility/README.md) before relying on a security or write-confinement boundary.

- **Fallback gates:** work without repo-local registered agents by loading the agent instructions bundled with `karta-verify`. Use a read-only Codex sandbox for an enforceable no-write boundary.
- **Kaizen:** the absent and disabled switches are no-ops. Direct mode detects packs and leaves edits uncommitted. Delivery mode uses the binder's pinned packs and can land a labeled `kaizen:` commit on the supplied integration branch.
- **Plannotator:** Karta probes for the separately installed CLI. If it is absent, Karta does not mention the browser review surface. If it is present, plan annotations map only when the target field is unambiguous; Karta returns ambiguous feedback to chat and still waits for the explicit `commit` verb.
- **Hooks:** Codex now has a hooks surface, and karta ships Codex versions of the guards that surface can express — see [What karta enforces on Codex](#what-karta-enforces-on-codex) below. The compatibility showcase predates that wiring; its "hooks unavailable" rows describe the older surface it certified, not today's plugin.

## What karta enforces on Codex

Codex has a hooks surface — small scripts the harness runs around tool calls, the same idea as Claude Code hooks. karta bundles a Codex hooks manifest (`.codex-plugin/hooks/hooks.json`, declared by the `hooks` entry in the plugin manifest) with guards rewritten for Codex's payload shape: Codex file edits arrive as one `apply_patch` call carrying the raw patch body, not a file path, so the Codex guards parse the patch itself. The bundled guards cover the write rules (`guard_binder_immutability.py`, `guard_pack_write.py`), the delivery Stop gate (`guard_delivery_stop.py`), the whiff advisory (`guard_subagent_whiff.py`), the session-start status injection (`inject_karta_status.py`), and the fail-closed dispatch inspectors (`guard_auditor_dispatch.py`, `guard_gate_dispatch.py`). Each guard passes anything it does not recognize, and each manifest launcher fails open when the plugin root does not resolve — enforcement must never break normal work. The dispatch inspectors are the one deliberate exception once a dispatch *is* recognized: an internal error mid-check denies with a corrective reason rather than waving the dispatch through.

This table is the honest parity story. "Enforced" means a hook the harness runs; "doctrine" means the skill states the rule and the agent is expected to follow it, with nothing below the agent to catch a slip.

| Rule | Claude Code | Codex |
|-|-|-|
| Committed binders are read-only | Enforced (plugin hook) | Enforced (bundled Codex hook). Any patch that adds over, rewrites, deletes, or renames a binder committed in `HEAD` is denied — including archived binders. The one sanctioned mutation is the end-of-life archive move: a hunk-free rename of a live binder to `.karta/binders/archive/<same name>.json` (or `git mv`). Untracked drafts pass. |
| Pack edits must validate | Enforced (plugin hook) | Enforced (bundled Codex hook). A patch that adds a `.karta/sme/*.md` pack carries its full content, so it is validated before it lands; after any other pack edit the file on disk is re-checked and failures come back as feedback the agent must fix. |
| Safety-auditor dispatch is complete | Enforced, fail-closed (plugin hook) | Enforced (bundled Codex hook). `guard_auditor_dispatch.py` runs on agent dispatch (the `spawn_agent` tool, alias `Agent`) and fails closed: a recognized `karta-safety-auditor` dispatch must name its binder (`.karta/binders/<slug>.json`) and, when that binder pins packs, carry each pack's resolved checklist — a dispatch missing its binder or its checklists is denied on both documented channels before the reviewer context spins up. Recognition is scoped to the payload's identity fields: `agent_type`, or a karta-bearing task name in either spelling (hyphens or underscores); a bare plugin install's generic fallback dispatch that names neither is outside the enforced claim. Live only after the per-user trust review. |
| Gate dispatches carry a real, sized diff | Enforced, fail-closed (plugin hook) | Enforced (bundled Codex hook). `guard_gate_dispatch.py` fails closed on the same recognition scope — an identity field naming the gate (`agent_type`, or a karta-bearing task name in either spelling); the generic fallback dispatch that names neither is outside the enforced claim. A recognized dispatch must carry a parseable, non-empty diff range and a `Diff-size` line whose numbers match what git recomputes for that range — a dispatch with an empty diff range, or a missing or mismatched Diff-size line, is denied before the reviewers spin up. Live only after the per-user trust review. |
| The confined writers stay inside their surfaces | Enforced, fail-closed (plugin hook, incl. static `Bash` write parsing) | Doctrine. Codex hook payloads can carry subagent identity, but karta registers no kaizen or doc-gardner agent on Codex — there is no writer for a confinement hook to recognize. A read-only Codex sandbox for the session, or the registered read-only gate agents, narrows what instruction-following has to carry. |
| You see your binders at session start | Informational (plugin hook) | Enforced (bundled Codex hook). `inject_karta_status.py` runs on SessionStart and injects a short fenced karta status summary — your live binders, a line each — as session context. It informs, never blocks, and it fails open: any internal error exits 0, so a status hint never surfaces as a session error. |
| A delivery may not end dirty | Enforced (plugin Stop hook) | Enforced (bundled Codex Stop hook). Same detection — built-but-unmerged items or a complete-but-unarchived binder block the stop once per state, then the identical stop passes. The guard emits both block forms Codex documents (the JSON `block` decision and exit 2 with the reason), so whichever your build honors, the result is the same. |
| A build item that produced nothing is named, not lost | Enforced (plugin SubagentStop hook). When a karta-typed subagent stops while a whiffed item's residue stands, the advisory blocks that stop once, naming the stranded items so the orchestrating session re-derives the frontier. | Enforced (bundled Codex hook). `guard_subagent_whiff.py`, the SubagentStop twin, detects the same residue from git facts alone — a live-binder item with no completion ref whose branch equals its base and shows no in-flight work — and blocks the stop once with the items named; the identical stop then passes, because the harness re-invokes the guard with `stop_hook_active` set. An advisory nudge, never a denial of normal work. |
| Karta Watch hub auto-revives | SessionStart hook + script-embedded ensure | SessionStart hook + script-embedded ensure. The bundled `inject_karta_status.py` SessionStart hook now fires the same fail-open `serve_status.py --ensure` once you trust it; the script-embedded ensure remains the path that always runs — the status engine fires it on every karta touch, no hooks required. A Codex sandbox can still deny the detached spawn; the ensure then fails open (karta work is never blocked) and the status surface nudges you to run `serve_status.py --ensure` from a terminal yourself. See [karta-watch.md](karta-watch.md). |
| karta never pushes | — | Enforced (this repo's `.codex/rules/karta.rules` execpolicy, unchanged): `git push` asks first, flag-first force pushes are forbidden. |
| Commits in this repo pass the gate suite | Enforced (this repo's project settings) | Enforced (this repo's `.codex/hooks.json`): a `git commit` runs the same gate suite through a shell-payload adapter (`scripts/hooks/codex_precommit_gate.py`) that normalizes Codex's shell payloads into the shape the canonical gate accepts. Repo checkout only — not shipped in the plugin. |

### Before you rely on it

- **Hooks are stable and on by default.** Codex hooks are stable and enabled by default since 0.124.0 (release note: "Hooks are now stable"), and plugin-shipped hooks are enabled since 0.131.0. The off switch is the config line `[features] hooks = false` — if a guard seems inert, check that line before probing anything else.
- **Hooks are a guardrail, not a boundary.** Codex emits hook events for its local function tools (shell, `unified_exec`, `apply_patch`, agent dispatch, MCP calls); hosted tools do not fire hooks, and a raw shell redirection that writes a binder still bypasses the patch-parsing write guards — the same shell gap Claude Code's binder guard has. For a hard boundary, use a read-only Codex sandbox or OS-level permissions.
- **Enforcement is opt-in per user — bundled hooks included.** Codex does not auto-trust a plugin's hooks: it skips them until you review and trust each hook definition through `/hooks`. Trust is hash-based — plugin and project hooks enforce only after that review, and an edited hook drops back to untrusted until re-reviewed — so every "Enforced (bundled Codex hook)" row above is live only after it. Repo-local hooks (`.codex/hooks.json`) additionally need the repo marked trusted. Until then, doctrine is all there is.
- **A coarser OS-enforced option exists.** Codex filesystem permission profiles can mark `.karta/binders` read-only at the sandbox level. karta does not ship one: a profile cannot tell a committed binder from a new plan draft, so it would block planning too. If your project never drafts binders on Codex, it is a legitimate extra layer.

## Notes

- **Reloading.** Codex loads skills and prompts at session start. After installing or updating, restart Codex (or start a new session) to pick up changes.
- **Windows.** The `.agents/skills/` mirror and `plugins/karta/` install projection are real directories, not symlinks — Codex does not detect symlinked skills on Windows ([openai/codex#8400](https://github.com/openai/codex/issues/8400)). Nothing extra is needed on Windows.
- **Duplicate listing.** If you both install the karta plugin and run Codex inside the karta repo, each skill can appear twice in `/skills` (Codex does not de-duplicate by name across sources). Harmless — pick either entry. To avoid it, use one source at a time.
- **Per-skill metadata.** Each skill carries an `agents/openai.yaml` (display name, short description, implicit-invocation policy) that Codex uses for presentation; it fails open if absent.

## For contributors

The Codex artifacts (`.agents/skills/`, `plugins/karta/`, `.codex/agents/*.toml`, the bundled `*.agent.md`) are **generated** from the canonical `skills/` and `agents/` trees. Never hand-edit them. After editing a skill or agent, run the generators and the validator — see [AGENTS.md](../../AGENTS.md).

The Codex guard scripts under `.codex-plugin/hooks/scripts/` are the exception: they are canonical, hand-maintained twins of the Claude guards in `hooks/scripts/` — same rule, different payload shape. Edit them in place, keep the rule semantics in step with the Claude twin, and run each script's `--self-test`. `sync_codex_skills.py` copies `.codex-plugin/**` verbatim into the `plugins/karta/` install projection, so a guard edit still needs a generator run before commit.
