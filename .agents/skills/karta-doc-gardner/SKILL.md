---
name: karta-doc-gardner
model: haiku
description: >-
  Automatically correct documentation drift after a delivery (or on demand). Opt-in via .karta/doc-gardner.json; when on, it rewrites prose docs (README, docs/, AGENTS.md, ARCHITECTURE) to match the current code and commits the fix to the integration branch — no report-only mode, no severity tiers, no human waive. Trigger phrases: "gardner the docs", "fix doc drift", "run doc-gardner".
triggers:
  - "gardner the docs"
  - "fix doc drift"
  - "run doc-gardner"
---

`karta-doc-gardner` keeps a repo's prose in lockstep with its code, automatically. It dispatches one karta-owned agent — `karta-doc-gardner` — which **rewrites** drifted docs to match the current code and is done. The agent holds the docs to karta's doc doctrine: facts and recorded decisions only, nothing derivable or speculative, and the whole current state captured with no gaps. There is no report-and-wait, no severity triage, no human waive, and no halt: when it runs, drift is corrected. It is **all or nothing** — opted in, every delivery corrects drift; opted out, it never runs.

This skill is the only place the gardner is dispatched. It is the doc analog of `karta-verify`: a thin orchestrator around a single agent.

## Opt-in — `.karta/doc-gardner.json`

The automatic delivery path is governed by one durable switch (the only static element; scope itself is always recomputed live — see the agent). Read `.karta/doc-gardner.json`:

- `{"enabled": true}` (optionally `"focus": "<freeform note>"`) → opted in. `karta-deliver` runs this skill unconditionally at its terminal phase; it cannot be skipped.
- Absent or `{"enabled": false}` → off. The delivery phase no-ops.

`focus` is freeform guidance that biases the gardner's attention; it is **not** a list of docs. A direct (standalone) invocation of this skill runs regardless of the switch — the switch only governs the automatic delivery path. The file shape is gated by [references/doc-gardner-schema.json](references/doc-gardner-schema.json); see [references/doc-gardner.example.json](references/doc-gardner.example.json).

## Inputs

- **Repo root** — the working tree to correct (the integration branch's tree in delivery; the repo in an ad-hoc run).
- **Diff range** — the delivery's blast radius (all items versus the binder base), e.g. `karta/<slug>/integration` vs its base. Absent in a full ad-hoc sweep.
- **Mode** — `delivery` (commit the corrections) or `ad-hoc` (leave the working-tree edits for the caller). An explicit signal from the caller; default `ad-hoc`.
- **Focus** — the optional `focus` string from `.karta/doc-gardner.json`, passed through to the agent.

## Resolving the gardner agent (any runtime)

The gardner runs as a fresh subagent that re-derives its scope by reading the repo. It is a **writer** (it edits docs), so it needs a write-capable sandbox. Resolve it the way the runtime supports:

1. **A registered `karta-doc-gardner` subagent exists** — dispatch it by name. This is the path on Claude Code (the plugin bundles it) and on Codex when the project carries `.codex/agents/karta-doc-gardner.toml` (there `sandbox_mode = "workspace-write"` is set).
2. **No registered agent by that name** (a Codex plugin install, which cannot register subagents) — spawn a fresh write-capable subagent (a normal worker, not the read-only explorer) and give it, as its complete instructions, the bundled agent file: [references/karta-doc-gardner.agent.md](references/karta-doc-gardner.agent.md). That file is the agent's own instructions and is self-contained.

Pass the agent the repo root, the diff range (or "full tree" in an ad-hoc sweep), and the focus note. The agent corrects docs in place and returns a terse envelope (`corrected_count`, `files_changed`, `residual`, `summary`).

## Phase 1 — Resolve focus  `docgardner:focus`

If `.karta/doc-gardner.json` exists, read its `focus` (may be absent). In the automatic delivery path this file is already known to be opted in (karta-deliver checked). In a standalone run, a missing file just means no focus bias.

## Phase 2 — Dispatch the gardner  `docgardner:correct`

Dispatch `karta-doc-gardner` (resolved as above) with the repo root, the diff range, and the focus note. It re-globs the live doc surface, derives the blast radius from git, runs its repo-wide pointer pass, corrects every drift it finds — including culling speculative or derivable prose and closing coverage gaps — with bounded re-verify and no human, and returns its envelope. It edits **only** doc-surface files, and writes the prose it corrects in plain language (via the bundled `karta-plainlanguage` skill).

## Phase 3 — Land or hand back  `docgardner:land`

- **`Mode: delivery`** — the gardner's edits are in the integration branch's working tree. If `corrected_count > 0`, stage the changed doc files and commit them as a single labeled commit: `docs: gardner <slug>` (carry the gardner's `summary` in the body, and any `residual` as a trailer). This is the auditable record on the integration branch — the human reviewing the branch sees exactly what the gardner changed. If `corrected_count == 0`, make no commit. Never push.
- **`Mode: ad-hoc`** — leave the corrected files in the working tree and report the envelope to the caller; the user reviews and commits. Make no commit yourself.

Fold the envelope into the caller's report. There is no human decision to surface — the corrections stand, any residual is noted, the run ends.

## Rules

- **One agent, doc-surface only.** The gardner edits prose docs to correct drift; it never touches code, tests, the binder, refs, or `.karta/`. This skill never edits anything itself — it dispatches and (in delivery mode) commits the agent's doc edits.
- **No human, no halt, no waive.** Correct, re-verify within the agent's bound, record residual, return. The delivery is never paused for a doc decision.
- **All or nothing.** No severity tier and no advisory mode. Opted in → every drift corrected; opted out → never runs.
- **Doctrine-bound.** The agent enforces minimalism and derivability: docs hold facts and decisions recorded against them, nothing derivable or speculative, and the surface captures the whole current state with no gaps. The doctrine lives in the agent; this skill only dispatches.
- **Scope is recomputed live.** The agent re-globs the doc surface and derives the blast radius every run; nothing about what to gardner is stored. New files are always in scope.
- **Labeled, revertible commit.** In delivery mode the corrections are one `docs: gardner <slug>` commit on the integration branch — never pushed, never on a protected branch — so they are reviewed and revertible like any commit.
