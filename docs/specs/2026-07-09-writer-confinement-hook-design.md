# Writer confinement hook — design

**Date:** 2026-07-09
**Status:** approved design, not yet built
**Scope:** hooks phase 2 — one new plugin hook that confines the kaizen writer to its declared surface

## The problem

kaizen is karta's stack-pack writer. Its own doctrine says its writable surface is exactly two things: `.karta/sme/` (the project's packs) and `.karta/kaizen.json` (its opt-in switch). But nothing enforces that. The agent definition (`agents/karta-kaizen.md`) restricts *which tools* kaizen gets (`Read, Glob, Grep, Edit, Write, Skill`) — not *where* those tools may write. The confinement lives only in the agent's instructions, and instructions can be skipped.

That matters more here than anywhere else in karta: the packs kaizen edits carry the Review checklists the safety-auditor judges every future build against. A stray write to that surface — or from kaizen to any other file — has outsized blast radius, and no downstream gate catches it. `precommit_gate.py` checks mirror parity and plugin validity, not "did a writer stay in its lane." The only backstop today is a human noticing a stray file in the diff.

This is precisely the gap hooks phase 1 exists to close (see `docs/how-to/hooks.md`): a rule that matters most, moved below the agent's judgment. Phase 1 shipped four plugin hooks — three guards plus a session-start status injector. This is the fourth guard, fifth hook.

**Out of scope, deliberately:** doc-gardner. Its surface (README, `docs/`, `AGENTS.md`, `ARCHITECTURE`, plus salvage moves out of `superpowers/`) is open-ended, so any allowlist carries real false-deny risk that could halt a legitimate doc pass — for lower stakes. It stays doctrine-confined for now. The guard's writer table makes adding it later a one-row change.

## The mechanism it stands on (verified)

The design keys on one Claude Code capability, verified three independent ways on 2026-07-09 (v2.1.205): the official hooks doc, a grep of the installed binary, and an empirical probe (a nested run with a payload-dumping hook, capturing a real subagent `Write`).

- A PreToolUse payload for a tool call made **inside a subagent** carries **top-level** `agent_id` and `agent_type` fields. They sit beside `session_id`/`cwd`/`permission_mode` — not inside `tool_input`.
- A **main-thread** tool call's payload has no agent fields at all (absent, not null). Presence of `agent_type` cleanly distinguishes subagent from main thread.
- For **plugin-shipped** subagents, `agent_type` is the plugin-scoped identifier — `karta:karta-kaizen` — while a repo-checkout registration reports the bare frontmatter name `karta-kaizen`. A guard must accept both.
- The docs carry no minimum-version note for these fields; the empirical probe pins them working at v2.1.205.

Captured payload from the probe (subagent `Write`, trimmed):

```json
{"hook_event_name": "PreToolUse", "tool_name": "Write",
 "agent_type": "general-purpose", "agent_id": "a0707dc66a5cecf56",
 "session_id": "…", "cwd": "…", "permission_mode": "…",
 "tool_input": {"file_path": "…/sub.txt", "content": "…"}}
```

The same probe's main-thread `Write` payload had no `agent_type`/`agent_id` keys.

## What gets built

Two files change; nothing else.

### 1. `hooks/scripts/guard_writer_confinement.py` (new)

A PreToolUse guard in the house pattern of the three shipped guards: pure stdlib, argparse, `--self-test` with `[PASS]/[FAIL]` lines and an `N/N checks passed` summary, hook payload JSON on stdin, exit 0 allows, exit 2 blocks with a one-paragraph reason on stderr. The PEP 723 header is the four-line block the other hook scripts carry:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
```

**Decision logic, in order:**

1. **Who is writing?** Read top-level `agent_type` from the payload. Absent → main thread → exit 0. Present but not a confined writer → exit 0.
2. **Is this a confined writer?** Match against the writers table. One entry ships:

   | Writer | Recognized as | Allowed surface |
   |-|-|-|
   | kaizen | `agent_type` equals `karta-kaizen`, or ends with `:karta-kaizen` (any namespace prefix) | paths under a `.karta/sme/` segment, and the exact file `.karta/kaizen.json` |

   The namespace-tolerant match is required, not decorative: a plugin install reports `karta:karta-kaizen`, a checkout reports `karta-kaizen`. This is the same family as `guard_auditor_dispatch.py`'s recognition (identity fields, tolerant of namespacing) but deliberately stricter: the auditor guard uses substring containment, which would also match a hypothetical `karta-kaizen-v2`; this guard matches the exact bare name or an exact `*:`-namespaced form, nothing wider.
3. **Collect the write targets.** Check `tool_input.file_path` and `tool_input.notebook_path` — both keys, regardless of `tool_name`, the same loop idiom `guard_binder_immutability.py` uses. **Every path present must be inside the allowed surface.** For a recognized writer, a call with *no* extractable path — `tool_input` missing, not a dict, or carrying neither key, or a path that isn't a string — is **denied**: an unverifiable kaizen write is treated as out of bounds, not waved through (such a call would fail tool validation anyway, so the deny costs nothing).
4. **Is the path allowed?** Normalize each path with `os.path.normpath`, then replace `os.sep` with `/` so the match is separator-stable on Windows (on POSIX this is a no-op). Normalization collapses `..` traversal like `.karta/sme/../../src/x.py` before matching. Then match:
   - allowed: the normalized path matches `(?:^|/)\.karta/sme/` — i.e. a `.karta/sme/` **segment** with its trailing slash; `.karta/sme-extras/` does not match, and the bare directory path `.karta/sme` (no trailing file) does not match and is denied
   - allowed: the normalized path matches `(?:^|/)\.karta/kaizen\.json$` — end-anchored, so `.karta/kaizen.json.bak` does not match (same end-anchoring as `guard_binder_immutability.py`'s `BINDER_RE`)
   - anything else: denied

   The match is case-sensitive. On a case-insensitive filesystem a write to `.KARTA/SME/` would be denied — a false deny, which is the safe direction; not worth special-casing.

   Segment anchoring (rather than anchoring on `payload.cwd`) is deliberate and required: kaizen writes inside the integration-branch worktree, whose absolute path is a sibling of the checkout — a cwd-anchored check would false-deny legitimate pack edits. This mirrors `guard_binder_immutability.py`'s `(?:^|/)\.karta/binders/…` idiom. The allow-list consequence of that choice is stated under Honest limits.
5. **Deny with doctrine.** Exit 2; stderr names the writer, the offending path, and restates the rule the agent already carries: kaizen's writable surface is exactly two things — `.karta/sme/` and `.karta/kaizen.json`. The subagent sees the correction and can adapt; the write never lands.

**Fail posture: fail-closed on the recognized shape.** An internal error while evaluating a call whose `agent_type` matches a confined writer denies with a generic reason. Unrecognized shapes — unparseable payload, missing `agent_type`, unknown writer — always pass. Strict for the agent the guard exists to confine, invisible to everyone else. This copies `guard_auditor_dispatch.py`'s posture, including the guarded re-check of recognition inside the exception handler. Note the asymmetry with that guard's "tool_input not a dict → pass" behavior: there, recognition itself lives inside `tool_input`, so a malformed `tool_input` is an unrecognized shape; here, recognition is top-level, so a malformed `tool_input` on a recognized writer is an unverifiable write and is denied (step 3).

**Self-test fixtures (minimum set):**

| Case | Expect |
|-|-|
| kaizen (bare `karta-kaizen`) writes `.karta/sme/python.md` | pass |
| kaizen (`karta:karta-kaizen`) writes `.karta/sme/python.md` | pass |
| kaizen writes `/abs/worktree/.karta/sme/minimalism.md` | pass (segment anchor) |
| kaizen writes `src/x/.karta/sme/y.md` | pass (accepted residual — see Honest limits) |
| kaizen writes `.karta/kaizen.json` | pass |
| kaizen writes `.karta/kaizen.json.bak` | deny (end-anchored match) |
| kaizen writes `.karta/sme-extras/x.md` | deny (segment boundary) |
| kaizen writes bare `.karta/sme` | deny (no trailing segment) |
| kaizen writes `skills/karta-plan/SKILL.md` | deny, reason names path + surface |
| kaizen writes `.karta/binders/x.json` | deny (confinement, independent of immutability guard) |
| kaizen writes `.karta/sme/../../src/app.py` | deny (normalized before match) |
| kaizen `NotebookEdit` with `notebook_path` outside surface | deny |
| kaizen write with `tool_input` not a dict | deny (unverifiable — fail-closed) |
| kaizen write with non-string `file_path` (e.g. `42`) | deny (unverifiable; also exercises the error path) |
| `agent_type` like `karta-kaizen-v2` writes anywhere | pass (not an exact match) |
| main-thread write (no `agent_type`), any path | pass |
| unknown agent (`Explore`) writes anywhere | pass |
| doc-gardner (`karta:karta-doc-gardner`) writes `docs/x.md` | pass (not confined in this cut) |
| payload not JSON / not a dict | pass (unrecognized shape) |
| prose mentioning kaizen in `tool_input` but no `agent_type` | pass (identity comes from the payload field, never from text) |

### 2. `hooks/hooks.json` (one new entry)

Append to the existing `PreToolUse` list:

```json
{
  "matcher": "Write|Edit|NotebookEdit",
  "hooks": [
    {
      "type": "command",
      "command": "\"${CLAUDE_PLUGIN_ROOT}/hooks/scripts/guard_writer_confinement.py\"",
      "timeout": 30
    }
  ]
}
```

Same matcher and shape as the immutability guard's entry. All PreToolUse hooks whose matcher covers a call run; any exit 2 blocks — no ordering dependency between guards.

## What we get without building it

- **Validator coverage is automatic.** `validate_plugin.py` already parses `hooks.json`, errors on any script under `hooks/scripts/` that `hooks.json` does not reference ("it would never run"), and executes every hook script's `--self-test` (`scripts/validate_plugin.py:268–315`). The two changed files land inside the existing gate suite, including the repo's pre-commit gate (which runs `validate_plugin.py`).
- **No mirror work.** The Claude Code plugin root is the repo root — the installed plugin ships `hooks/` as-is. The three-way mirror discipline (`sync_codex_skills.py` / `sync_codex_agents.py`) covers skills and agents for Codex only; hooks are Claude-Code-only by design.

## How the guards compose

Each guard answers one question; none knows the others exist:

| Guard | Question | Registered on |
|-|-|-|
| `guard_writer_confinement.py` (new) | may **this agent** write **here**? | PreToolUse `Write\|Edit\|NotebookEdit` |
| `guard_binder_immutability.py` | is this file **frozen history**? | PreToolUse `Write\|Edit\|NotebookEdit` |
| `guard_pack_write.py` | is the content a **valid pack**? | PreToolUse `Write`; PostToolUse `Edit\|Write` (corrective feedback after the fact) |

A kaizen pack `Write` passes confinement (right surface) and must still pass pack validation (right content) before it lands; a kaizen pack `Edit` is confinement-checked pre-write and pack-validated post-write. A kaizen write to a committed binder is denied twice over. Known gap, unchanged by this design: a `NotebookEdit` into `.karta/sme/` is confinement- and immutability-checked but never pack-validated — irrelevant today (packs are `.md`, kaizen carries no `NotebookEdit`), noted so the composition table stays honest.

## Enforcement asymmetry (stated, not papered over)

On Codex, kaizen's confinement remains doctrine + OS sandbox: Codex cannot register subagents from a plugin install, and its hooks surface is not yet stable — the same accepted asymmetry as hooks phase 1 (user decision 2026-07-06). The guard script is runtime-agnostic (stdin JSON in, exit code out), so wiring it into a future Codex hooks surface needs no rewrite. `docs/how-to/hooks.md` gets the explicit note.

## Honest limits

- **This fences the writer it names, not writes in general.** kaizen's tool grant is `Write`/`Edit` (no shell, no `NotebookEdit`), so this hook closes its entire mutation channel; the matcher covers `NotebookEdit` anyway as defense against future tool-grant drift. An agent that *does* carry Bash could still `echo >` anywhere — out of scope; this is per-writer confinement, not a global write-fence.
- **Segment anchoring widens the allowed surface as well as the protected one.** Because the rule matches `.karta/sme/` anywhere in the path, kaizen may write into a *nested* pack directory — `src/x/.karta/sme/y.md` passes (pinned by fixture). Accepted residual: the guard cannot know the repo root from the payload, kaizen legitimately writes into sibling worktrees, and a nested `.karta/sme/` is still a pack surface whose content the pack guard validates and a human reviews. The deny-list twin of this idiom in `guard_binder_immutability.py` has the same property in the safe direction (it protects nested binder dirs too).
- **Symlink escape is theoretically possible, practically not:** a pre-existing symlink under `.karta/sme/` pointing elsewhere would pass the path check. kaizen cannot create symlinks (no shell), and the packs directory is human-reviewed; accepted residual.
- **The field contract is Anthropic's, not ours.** If a future Claude Code release renamed or dropped `agent_type`, the guard would degrade to pass-everything (unrecognized shape) — enforcement lost, work unbroken, doctrine still stated in the agent. The self-test pins our parsing, not the harness's emission; the release checklist should include one live kaizen-write smoke test.

## Documentation changes

- `docs/how-to/hooks.md` — one new row in the "What is enforced, and where" table (writer confinement / Claude Code plugin hook / what happens); the Codex-asymmetry sentence; **and** update the "How a hook decides" paragraph whose current text says the auditor-dispatch guard is "the one exception" that fails closed — with this guard there are two, and the sentence must name both.
- `README.md` — extend the "Enforcement below the agent" paragraph's rule list with the confinement clause.
- `agents/karta-kaizen.md` — one sentence in "Where you may write — and where never": on Claude Code, this boundary is also hook-enforced. (Mirrors regenerate via `sync_codex_agents.py`.)

## Delivery

Built as a karta binder (single item or small set) per the repo's dogfood-first pattern; release as part of the next minor (1.19.0) with a checklist entry that includes the live smoke test: dispatch kaizen against a scratch repo, observe one in-surface write pass and one out-of-surface write blocked with the doctrine message.
