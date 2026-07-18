# Roundtable edict for karta-on-karta — design

**Date:** 2026-07-18
**Status:** design, ready to plan
**Scope:** house-only tooling for the karta repo's own development. Nothing here ships in the plugin.

## Problem

When karta builds karta, the highest-leverage review happens before code exists — at the binder — and again on the assembled integration branch before it lands on `main`. This session proved the value directly: a multi-model roundtable critique of the `bench-probe-buildout` binder caught real design defects (field-lane oracles gating on live consumer repos, a six-way serialization on one registry file, a placeholder-path trap) before a single line was built.

That review was run by hand. The maintainer wants it to be an **edict, not a suggestion**: karta's own binders and deliveries may not proceed without a recorded multi-model review. This matches karta's standing doctrine — enforced checks over skippable prose.

## Constraint that shapes everything

This is for **karta building karta, not for plugin distribution**. So it must stay out of the shipped surface: no edits to `skills/`, `agents/`, or `hooks/hooks.json` (all mirrored to `.agents/`, `.claude/skills/`, `.codex/`, `plugins/` and shipped to consumer repos). It lives only in the karta repo's non-distributed surfaces — the same places the existing dev-repo commit gate already lives:

- `scripts/hooks/precommit_gate.py` — a repo-local hook, wired through the karta repo's own `.claude/settings.json`, never part of the plugin manifest.

The roundtable edict follows that exact precedent.

## The honest enforcement boundary

Roundtable is external and nondeterministic — different models, different runs, opinions that vary. A deterministic hook therefore **cannot** gate on *"the panel returned COMMIT-READY."* What it can gate on, deterministically, is **"a fresh roundtable review of this exact artifact exists and was recorded."**

So the edict is: **you may not commit a karta binder, or land a karta integration branch on the default branch, without having run the panel and recorded its findings for that exact content.** The maintainer still reads the findings and decides what to act on. Skipping the panel is what's blocked — not disagreeing with it.

This is the same shape as the `release-version-bump-block` item built this cycle: a commit is blocked unless a matching artifact exists for the exact sha. Enforce *presence of a fresh review*; keep the nondeterministic verdict out of the hard gate.

## Where it applies

The four insertion points the maintainer named split by whether a deterministic git event exists to hang an edict on:

| Point | Git event | Treatment |
|-|-|-|
| Plan (binder) | commit staging `.karta/binders/<slug>.json` | **enforced edict** |
| Deliver (integration branch) | merge/commit landing `karta/<slug>/integration` onto the default branch | **enforced edict** |
| Verify (a built diff) | none (read-only fresh session) | helper-available |
| Standalone (ad hoc) | none (on demand) | helper-available |

Plan-commit and deliver-merge have a real commit to block. Verify and standalone have no commit or stop moment, so they get the same one-command helper without a hard gate.

## Architecture

Four pieces, all in non-distributed repo surfaces.

### 1. Config — `.karta/roundtable.json`

House switch and panel settings. Because consumer repos never carry this file, the edict is karta-on-karta by construction.

```json
{
  "enabled": true,
  "tool": "roundtable-critique",
  "providers": [],
  "min_providers": 2,
  "focus": "",
  "points": { "plan_commit": true, "deliver_merge": true }
}
```

`enabled: false` (or an absent file) disables every gate — the switch is absolute, matching the doc-gardner/kaizen opt-in pattern. `providers: []` means the panel default. `min_providers` (default 2) is what keeps "multi-model" honest: a record whose panel carries fewer than this many distinct providers is not a review (see the helper). `points` lets either edict be turned off independently.

**Validation lives where the sibling switches live.** The config is validated by `scripts/validate_plugin.py` — a new structural block alongside the existing `doc-gardner.json` and `kaizen.json` blocks — not by a separate schema file or standalone validator. `validate_plugin.py` already runs on every commit through the dev-repo commit gate, so a malformed `.karta/roundtable.json` is caught at commit exactly as a malformed doc-gardner/kaizen switch is. This is the established house pattern for an opt-in `.karta/*.json` config, and keeps the config surface to a single file.

### 2. Review record — `.karta/roundtable/<key>.json`

The artifact the hook checks. One per reviewed target.

- **key** — for a binder, its slug (`<slug>.json`); for a branch, `branch-<tip-sha>.json`.
- **reviewed_hash** — for a binder, the sha256 of the **staged** binder blob (`git show :<path>`), not the working-tree file; for a branch, the integration tip sha. Keying on the staged blob is what closes the review-A-stage-B bypass: an agent cannot review one version of the binder and then commit a different, unreviewed one. Any change to the staged binder or any new commit on the branch changes the hash and invalidates the record.
- **tool**, **target_kind**, **target_ref**, **run_at**, **config_snapshot**.
- **panel** — the recorded verdicts: a list of `{provider, verdict, summary}`. A record whose panel carries fewer than `min_providers` distinct providers (default 2), or any entry missing `provider`/`verdict`, is not a review and does not satisfy the gate.

Records are committed and live under `.karta/roundtable/` — they are the audit trail that the review happened, so they must survive a clean checkout. The helper stages the record when it writes it, and the binder-commit gate requires the record to be staged in the same commit (or already in `HEAD`); a record that lives only in the working tree does not satisfy the gate. `.karta/roundtable/` must not be gitignored.

### 3. Helper — `scripts/roundtable/run_review.py`

Stdlib-only, argparse, `--self-test`, house pattern. Roundtable itself is an MCP server the agent calls, not a CLI a script can invoke — so the helper is the **record writer and checker**, and the doctrine is: the agent runs the roundtable MCP tool, then pipes the panel result to the helper.

- `--record --target <binder-path|branch-name> [--kind binder|branch]` — reads the panel JSON on stdin (the raw `roundtable-critique` output object, which the helper normalizes to the stored `{provider, verdict, summary}` list), computes the key and `reviewed_hash`, writes and `git add`s `.karta/roundtable/<key>.json`. Rejects a panel with fewer than `min_providers` distinct providers, or entries missing `provider`/`verdict`.
- `--check --target <...> [--bytes-stdin]` — exit 0 if a fresh matching record exists, non-zero otherwise. For `--kind binder`, `--bytes-stdin` compares the record's `reviewed_hash` to the sha256 of candidate bytes read from stdin instead of hashing the worktree file — this is how the hook feeds it the staged blob. This is what the hook calls.
- `--self-test` — `[PASS]/[FAIL]` lines and an `N/N checks passed` summary; covers the `--bytes-stdin` match/mismatch paths and the `min_providers` rejection.

### 4. Enforcement hook — `scripts/hooks/roundtable_gate.py`

Stdlib-only PreToolUse/Bash hook, modeled on `precommit_gate.py`, wired in `.claude/settings.json` (not `hooks/hooks.json`). Two detections, both by git plumbing — never by parsing a diff:

- **Binder-commit gate.** When the command is a `git commit` (including `--amend`) that lands a `.karta/binders/<slug>.json` change, require a fresh record for that slug whose `reviewed_hash` matches the binder content being committed, and require the record itself to be in the commit (staged or already in `HEAD`). The hook reads the binder content the same way `release-version-bump-block` reads `plugin.json`: the staged blob via `git show :<path>` for a normal commit, or the working-tree file for `git commit -a`/`-am`/pathspec forms (which stage at commit time). It feeds those bytes to the helper via `git show :<path> | run_review.py --check --kind binder --bytes-stdin`. Missing or stale → block (exit 2) with a reason naming the helper command and the escape hatch.
- **Integration-merge gate.** Fire only when the current branch is the default branch (resolved via `git symbolic-ref refs/remotes/origin/HEAD`, falling back to `main`) **and** the command is a `git merge` (including `--squash`, `--ff-only`, `--no-ff`) naming a ref matching `karta/*/integration`. Require a fresh `branch-<resolved-tip-sha>.json` record for the tip being merged. Missing or stale → block with a reason.

Both share `precommit_gate.py`'s stance: **fail-open** on any internal error (a broken hook must never wedge the repo), and an escape hatch **`KARTA_SKIP_ROUNDTABLE=1`** (command text or environment) for when the roundtable environment is down or a deliberate partial commit is needed. The hook ships a `--self-test`.

**Known and accepted bypasses.** A PreToolUse hook sees a command *before* it runs, so it can only match command text and inspect current git state — it cannot evaluate a post-condition like "will this make the integration tip an ancestor." So these paths are **not** gated, by design, and are documented as the same class of deliberate escape as `KARTA_SKIP_ROUNDTABLE`: landing integration content via `git cherry-pick`, `git rebase`, or `git reset --hard`; and a `git merge --squash` followed by a *separate* `git commit` that does not name the integration branch. Gating these would require post-execution inspection the hook does not have. The doctrine names them plainly rather than pretending the gate is airtight.

### 5. Doctrine — `AGENTS.md` + `docs/how-to/roundtable.md`

`AGENTS.md` states the edict, names the tool per point, and shows the run-the-panel-then-record flow and the escape hatch. `docs/how-to/roundtable.md` is the operator guide.

## Flow, end to end

**Plan.** karta-plan drafts and validates the binder → the agent runs `roundtable-critique` on the binder and pipes the result to `run_review.py --record` → the maintainer reads the findings, edits the binder if warranted (which invalidates the record, forcing a re-review) → on the `commit` verb, the binder-commit gate confirms a fresh matching record exists and allows.

**Deliver.** The wave loop assembles the integration branch → before landing it on `main`, the agent runs `roundtable-critique` on the branch diff and records it → the integration-merge gate confirms a fresh record for the tip and allows the merge.

**Verify / standalone.** The maintainer runs the helper on demand against a diff or any target; findings are recorded but nothing is gated.

## What this deliberately does not do

- It does not make a panel verdict a hard pass/fail. Nondeterministic opinion never blocks; only a missing or stale review blocks.
- It does not touch any shipped skill, agent, or plugin hook. Zero distribution surface.
- It does not gate verify or standalone. Those have no commit to hang an edict on and stay advisory by design.
- It does not add a runtime dependency to the plugin. The helper and hook are stdlib-only; roundtable is the maintainer's own MCP environment.

## Testing

- `run_review.py --self-test`: key derivation for binder vs branch, staleness detection (edited staged binder / advanced tip invalidates), `--bytes-stdin` match vs mismatch, `min_providers` and malformed-entry rejection, record staging, `--check` exit codes.
- `roundtable_gate.py --self-test`: binder-commit detection across `git commit`, `-a`/`-am`, pathspec, and `--amend` forms; merge detection scoped to the default branch and the `karta/*/integration` pattern (and the accepted-bypass commands correctly *not* firing); record-staged requirement; fresh-record allow; missing/stale block with a reason naming the fix and the escape; `KARTA_SKIP_ROUNDTABLE` skip; fail-open on internal error.
- `validate_plugin.py`: the new config-validation block rejects a malformed `.karta/roundtable.json` and passes the valid example; nothing in the plugin manifest changes. Because `precommit_gate.py` runs `validate_plugin.py` on every commit, this is what makes the config check an enforced gate rather than an unwired script.

## Resolved by review (2026-07-18 roundtable + workflow critique)

The plan draft was itself run through the edict-by-hand — a roundtable-critique panel plus a workflow of per-item critics. Both returned NEEDS-FIXES and converged on the same defects, now folded into this doc: keying binder freshness on the staged blob (was: worktree bytes — a review-A-stage-B bypass); command-pattern-only merge detection with documented bypasses (was: an uncheckable "makes tip an ancestor" post-condition); the `-a`/`--amend`/pathspec commit forms (missed by a bare `git diff --cached` check); records that must be committed, not just written; a `min_providers` floor so a single-model rubber stamp cannot satisfy a multi-model edict; and folding config validation into `validate_plugin.py` rather than a separate, unwired schema+validator. Several of these are the exact problems `release-version-bump-block` already solved, so the hook reuses that code.

## Open questions

1. Binder content hash of the staged blob — raw bytes, or a normalized form (so cosmetic whitespace doesn't force a re-review)? Default: raw staged bytes, simplest and strictest.
2. Record retention — keep every historical record under `.karta/roundtable/`, or only the latest per target? Default: append-only history (the audit trail is the point), matching the bench's honesty doctrine.
3. `min_providers` default of 2 — right floor, or should it scale with how load-bearing the target is (e.g. 3 for a delivery landing on `main`)? Default: a flat 2, revisited if panels prove noisy.
