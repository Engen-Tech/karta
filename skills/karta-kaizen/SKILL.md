---
name: karta-kaizen
model: haiku
description: >-
  Improve the project's stack packs from what its builds keep repeating. Opt-in via .karta/kaizen.json, and the switch is absolute: absent or disabled means kaizen never runs, even when invoked directly. When on, it seeds every pack the project uses into .karta/sme/ on the first enabled run, then writes pack edits as labeled kaizen: commits a human reviews — it never weakens a rule and never promotes a pack to enforcing. Phase one ships the frame; sharpening rules and suggesting new packs come in later phases. Trigger phrases: "run kaizen", "kaizen the packs", "improve the stack packs".
triggers:
  - "run kaizen"
  - "kaizen the packs"
  - "improve the stack packs"
---

`karta-kaizen` turns what a project's builds keep repeating into better stack packs. It dispatches one karta-owned agent — `karta-kaizen` — a writer confined to `.karta/sme/` and its own config area, whose every change lands as a commit a human reviews. This skill is the only place that agent is dispatched; it is the pack analog of `karta-doc-gardner`: a thin orchestrator around a single writer.

**This is phase one: the frame.** What runs today is the seed-all step and the write-commit-review loop. The behaviors that will make kaizen earn its keep — sharpening rules from repeated `KARTA-SME-OVERRIDE` markers, plain "this rule is eroding" notes, spotting gaps and suggesting new packs — are later phases and are not active yet. This skill does not pretend to them.

## Opt-in — `.karta/kaizen.json` (the switch is absolute)

Read `.karta/kaizen.json`:

- `{"enabled": true}` (optionally `"focus": "<freeform note>"`) → opted in. `karta-deliver` runs this skill after each delivery, and a direct invocation works too.
- Absent, or `{"enabled": false}` → kaizen **never runs — including when this skill is invoked directly.** This is stricter than doc-gardner on purpose: doc-gardner's switch governs only its automatic delivery path and a standalone run works regardless; kaizen has no standalone carve-out. Off means off. When the switch is off and someone invokes this skill, report that kaizen is off — and that `.karta/kaizen.json` with `"enabled": true` turns it on — then stop. Do not dispatch the agent.

`focus` is a plain nudge about what to watch (for example "watch the billing items and the auth rules"). It is not a task list. The file shape is gated by [references/kaizen-schema.json](references/kaizen-schema.json); see [references/kaizen.example.json](references/kaizen.example.json). The plugin validator schema-checks the file when a repo commits one.

## Inputs

- **Repo root** — the working tree whose packs kaizen improves (the integration branch's tree in a delivery).
- **Mode** — `delivery` (commit the pack edits) or `direct` (leave the working-tree edits for the caller). An explicit signal from the caller; default `direct`.
- **Binder slug** — in delivery mode, for the commit label.
- **Focus** — the optional `focus` string from `.karta/kaizen.json`, passed through to the agent.

## Resolving the kaizen agent (any runtime)

The agent is a **writer** (it edits packs), so it needs a write-capable sandbox. Resolve it the way the runtime supports:

1. **A registered `karta-kaizen` subagent exists** — dispatch it by name. This is the path on Claude Code (the plugin bundles it) and on Codex when the project carries `.codex/agents/karta-kaizen.toml` (there `sandbox_mode = "workspace-write"` is set).
2. **No registered agent by that name** (a Codex plugin install, which cannot register subagents) — spawn a fresh write-capable subagent (a normal worker, not the read-only explorer) and give it, as its complete instructions, the bundled agent file: [references/karta-kaizen.agent.md](references/karta-kaizen.agent.md). That file is the agent's own instructions and is self-contained.

## Phase 1 — Check the switch  `kaizen:switch`

Read `.karta/kaizen.json`. If it is absent, unparseable, or `enabled` is not `true`, report that kaizen is off and stop — even on a direct invocation. Otherwise read `focus` (may be absent) and continue.

## Phase 2 — Resolve the pack set  `kaizen:packs`

Resolve the pack set for this run — deterministically, by mode. "Every pack the project uses" means:

- **`Mode: delivery`** (including karta-build's single-item hatch): exactly the binder's pinned `sme[]` — the packs this delivery built and gated against. Read the list from the binder; never re-derive it by matching. A delivery seeds what it used, nothing more: a pack the stack would match but the binder never pinned is left for the run that actually pins it.
- **`Mode: direct`** (no binder in play): derive the set the way `plan:sme` does — the always-on built-ins plus every built-in whose `match` token equals (case-insensitively) a dependency name or language emitted by `python3 skills/karta-plan/scripts/detect_stack.py <repo-root>`.

Either way, resolve ids from karta's bundled pack set (the same built-ins the karta-plan, karta-build, and karta-verify skills carry), laid under the project's own `.karta/sme/*.md` — on a name clash the project's copy wins. Hand the agent the resolved list, each id with the path to its source file. The skill resolves this because the built-in packs live in the installed plugin, not necessarily in the repo.

## Phase 3 — Dispatch the writer  `kaizen:write`

Dispatch `karta-kaizen` (resolved as above) with the repo root, the resolved pack list, and the focus note. On the first enabled run the agent seeds `.karta/sme/` — every used pack copied in as a full file with a provenance stamp and a lowercase basename (lowercase enforced at seed time), existing project copies left untouched. Beyond seeding, phase one edits a pack only on a concrete instruction carried in the dispatch, and the agent never weakens a rule or promotes a pack to enforcing. It returns a terse envelope (`seeded`, `packs_changed`, `residual`, `summary`).

### The provenance stamp and the eager migrate pass  `kaizen:migrate`

Every file the agent seeds carries a **provenance stamp** in its frontmatter — `seeded_from` (the built-in it was copied from) and `base_sha256` (the canonical hash of that built-in) — so a later run can tell an untouched copy from an edited one. The stamp is diagnostic only: `validate_packs.py` checks its shape (paired keys; `base_sha256` is 64 lowercase hex) but never gates a pack's cleanliness on it.

The **first enabled run after this change performs an eager migrate pass** over `.karta/sme/`, so copies that were seeded before stamps existed get classified and brought current in one visible sweep. It classifies every existing `.karta/sme/` file with `python3 skills/karta-plan/scripts/check_pack_provenance.py`, then acts on the classifier's state — one visible logged line per action:

- **seeded cache** — stamp-stripped bytes match the current built-in: write the provenance stamp onto it (it was a faithful copy that simply predates stamping).
- **stale cache** — byte-identical to a genuine *past* built-in the shipped hash ledger records: **auto-reseed** it — replace its bytes with the current built-in plus a fresh stamp. "stale cache" is the classifier's **ledger-verified** state; the migrate pass auto-reseeds only a ledger-verified stale cache, never a copy it merely guesses is old.
- **illegal shadow** (a local delta over the shipped built-in, including an unverifiable `base_sha256`) — **left in place and reported, never overwritten.** Kaizen never destroys a local delta in the warning era; a genuinely edited copy is the human's to reconcile.
- **project pack / suppression / orphaned cache** — left as-is.

The migrate pass is **naturally idempotent**: a stamped seeded cache classifies clean on the next run, so re-runs are no-ops — there is no marker file to write or read.

## Phase 4 — Land or hand back  `kaizen:land`

**Pre-land syntax check — a hard rule in both modes.** Before staging or committing anything (delivery) and before reporting (direct), run the bundled pack validator over every changed file under `.karta/sme/`, seeded files included: `python3 skills/karta-kaizen/scripts/validate_packs.py <pack.md>...` (exit 0 clean, 1 findings). On findings, return the invalid file(s) and the validator output to the agent once to fix, then run the check again. If it still fails, do **not** land — no commit, no clean hand-back: report the validator output and return the failure as this phase's result.

- **`Mode: delivery`** — the agent's edits are in the integration branch's working tree. If `packs_changed` is non-empty, stage the changed files under `.karta/sme/` and commit them as a labeled kaizen commit on the integration branch: `kaizen: <short summary>` (for a seed run, `kaizen: seed <n> packs into .karta/sme/`), carrying the agent's `summary` in the body and any `residual` as a trailer. The human reviewing the branch reviews these commits like any other. If nothing changed, make no commit. Never push.
- **`Mode: direct`** — leave the edits in the working tree and report the envelope to the caller; the user reviews and commits. Make no commit yourself.

Fold the envelope into the caller's report. Write everything you show a person in plain language — the agent routes its human-facing output through the karta-plainlanguage skill, and so does this skill.

## Rules

- **One agent, packs only.** The kaizen agent writes inside `.karta/sme/` and `.karta/kaizen.json` — never code, tests, the binder, git refs, prose docs, or karta's built-in packs. This skill never edits anything itself — it dispatches and (in delivery mode) commits the agent's pack edits.
- **The switch is absolute.** Absent or disabled means kaizen never runs, direct invocation included. There is no standalone carve-out.
- **Never weaker.** No rule loosened or removed, no pack promoted to enforcing — changing what gates a build is the human's decision, made in review of kaizen's commits.
- **Syntax-checked before landing.** Every changed file under `.karta/sme/` must pass `skills/karta-kaizen/scripts/validate_packs.py` before Phase 4 commits (delivery) or hands back (direct); a failure lands nothing, and the validator output is the phase result. The design's promise that pack edits are syntax-checked before they land — so a bad edit can't silently break the checker that reads the pack — is now a running check, not prose.
- **Seed once, full files, stamped.** The first enabled run copies every used pack into `.karta/sme/` whole, each with its provenance stamp and a lowercase basename; a project's existing copy always wins. That same first run migrates copies seeded before stamps existed (`kaizen:migrate`): it stamps a seeded cache and auto-reseeds a ledger-verified stale cache, but leaves an illegal shadow in place, reported, never overwritten. From then on the repo owns its packs, and the built-ins cover only names the repo does not carry.
- **The migrate pass lands through the existing flow.** The stamps it writes and the stale caches it auto-reseeds are ordinary changes under `.karta/sme/` — labeled `kaizen:` commits in delivery mode, working-tree edits in direct mode — gated by the same pre-land `validate_packs` check (Phase 4). It needs no separate path and no marker file; being naturally idempotent, a second run is a no-op.
- **Labeled, revertible commits.** In delivery mode every change is a `kaizen:` commit on the integration branch — never pushed, never on a protected branch — reviewed and revertible like any commit.
- **Plain language to people, precision in packs.** Human-facing output goes through the karta-plainlanguage skill; pack content stays technical.
- **Phase-one honesty.** Sharpening, erosion notes, and new-pack suggestion are later phases. Report what ran and no more.
