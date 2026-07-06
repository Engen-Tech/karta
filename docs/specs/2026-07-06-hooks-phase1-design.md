# Hooks, phase 1 — harness-level enforcement for karta

Date: 2026-07-06. Status: approved for build (roundtable-reviewed fix set; user accepted asymmetric runtime enforcement for this cut). Ships on the `karta/stack-pack-hardening` integration branch together with the stack-pack hardening work.

## Why

The 2026-07-06 stack-pack audit confirmed a pattern: karta's most consequential invariants live as skill prose an agent under context pressure can skip. Claude Code plugins can ship hooks — commands the harness runs deterministically around tool calls — and Codex ships OS-enforced sandboxing plus Starlark execpolicy rules (its own 2026 hooks surface is feature-gated; we adopt it in a later phase). Phase 1 moves five invariants from prose to hard gates on Claude Code, adds one Codex execpolicy quick win, and keeps skill doctrine as the cross-runtime source of truth so Codex behavior does not regress.

Asymmetry is deliberate and documented: Claude Code gets hook enforcement now; Codex keeps doctrine + `sandbox_mode` + rules until its hooks feature stabilizes. Hook scripts are written runtime-agnostic (JSON on stdin, exit 2 blocks, reason on stderr) so the same scripts back a Codex `[hooks]` manifest in phase 2.

## What ships

### 1. Plugin hook manifest — `hooks/hooks.json` (plugin root)

Registers four concerns against `${CLAUDE_PLUGIN_ROOT}/hooks/scripts/`:

| Concern | Event / matcher | Script |
|-|-|-|
| Binder immutability | PreToolUse, `Write\|Edit\|NotebookEdit` | `guard_binder_immutability.py` |
| Pack write validation | PreToolUse `Write` + PostToolUse `Edit\|Write` | `guard_pack_write.py` |
| Fail-closed auditor dispatch | PreToolUse, `Task\|Agent` | `guard_auditor_dispatch.py` |
| Session status injection | SessionStart | `inject_karta_status.py` |

Plugin hooks sit at the lowest settings precedence; a user or project can override them. That is accepted — the docs say so plainly.

### 2. Hook scripts — `hooks/scripts/*.py`

All stdlib-only, each with `--self-test` (same pattern as `validate_binder.py`). Common contract: read the hook payload JSON from stdin; exit 0 to allow; exit 2 with a one-paragraph reason on stderr to block (PreToolUse) or to surface corrective feedback (PostToolUse). Never crash the tool call: any internal error exits 0 (fail-open) EXCEPT where the script's whole purpose is fail-closed (`guard_auditor_dispatch.py`), which blocks on its recognized shape and allows everything else.

- **`guard_binder_immutability.py`** — if the target path matches `.karta/binders/*.json` and the file exists in `HEAD` (`git ls-tree HEAD -- <path>` non-empty), deny: committed binders are read-only. Untracked binder writes (plan drafting) pass.
- **`guard_pack_write.py`** — targets under `.karta/sme/`. On PreToolUse `Write`, run the plugin's pack validator (`skills/karta-kaizen/scripts/validate_packs.py`, resolved via `CLAUDE_PLUGIN_ROOT`) against the proposed content (temp file); deny with the validator findings. On PostToolUse (`Edit` or `Write`), validate the file on disk and, on failure, exit 2 so the validator output reaches the model as feedback it must fix. This is the kaizen pre-land syntax check enforced below the agent.
- **`guard_auditor_dispatch.py`** — recognizes a karta-safety-auditor dispatch (subagent type or agent name in the payload). For a recognized dispatch it requires, in the prompt: a binder path (`.karta/binders/<slug>.json`), and — when that binder's `sme[]` is non-empty — resolved checklist evidence (rule-id lines matching `^- \[ \] [a-z]+\.\d+ — ` or the id pattern `\b[a-z]+\.\d+\b` in a checklist block). Missing binder path or missing checklists ⇒ deny, naming the pinned ids. Unrecognized dispatch shapes always pass.
- **`inject_karta_status.py`** — SessionStart. If `<cwd>/.karta/binders/*.json` exists, emit at most one short line per binder (slug, item count, pinned packs); prefer the karta-status derivation script when invocable headless, else degrade to the static summary. Output must stay under ~10 lines; silence when no binders.

### 3. Dev-repo commit gate — karta repo only, not the plugin

`.claude/settings.json` (project settings, committed) gains a PreToolUse hook on `Bash`: `scripts/hooks/precommit_gate.py` detects a `git commit` invocation and runs the repo gate suite — `check_shared_copies.py`, `sync_codex_skills.py --check`, `sync_codex_agents.py --check`, `validate_plugin.py`, and `validate_packs.py` over `skills/_shared/sme/` — denying the commit with the failing gate's output tail. Escape hatch: `KARTA_SKIP_GATE=1` in the command environment, documented, for intentional partial commits. This ends the manual-only sync regime the audit confirmed (10 physical copies per pack, no CI, no hook).

### 4. Codex quick win — `.codex/rules/karta.rules`

Starlark execpolicy (project layer, loads when the project is trusted): `git push` ⇒ `prompt` (karta deliveries and kaizen never push; a human-approved push still works), `git push --force`/`-f` ⇒ `forbidden`. Verified with `codex execpolicy check` when the binary is available.

### 5. Validation + docs

- `validate_plugin.py` extends to assert: `hooks/hooks.json` parses, every referenced script exists and is executable, and each script's `--self-test` passes.
- `docs/how-to/hooks.md`: what is enforced, on which runtime, how the trust/enablement prompts appear, how to override or disable per project, the `KARTA_SKIP_GATE` escape hatch, and the asymmetry statement.
- README: one paragraph under the existing enforcement story.
- Skill doctrine is NOT weakened anywhere: hooks are belt-and-suspenders; each skill still states its invariants.

## Explicitly out of phase 1

Kaizen write-surface confinement (needs SubagentStart state plumbing — plugin subagents ignore frontmatter hooks), the delivery Stop-gate (ship warn-first later), Codex `[hooks]` manifest (feature-gated upstream), and any hook that inspects Bash command internals beyond the commit-gate's simple parse.

## Risks accepted

Plugin-hook precedence is overridable; hook scripts add a copy-sync surface (covered by the extended `validate_plugin.py`); false denies are bounded by conservative recognition rules (unrecognized shapes pass) and the documented escape hatch.
