# Cross-item term consistency — design

**Date:** 2026-07-08. **Status:** design, approved for planning. **Origin:** the binder end-of-life dogfood (`docs/showcase/binder-eol-dogfood/findings.md`, Finding 4).

## Problem

karta delivers a binder's work items in parallel, each in its own worktree, and gates each item's diff **in isolation** — the safety-auditor and acceptance-reviewer see one item at a time. Nothing in the pipeline compares one item against another.

The blind dogfood exposed the consequence: two independent items each introduced the same user-facing warning, worded differently. The status engine wrote `live binder '<slug>' shadows an archived (delivered) binder of the same slug`; the validator wrote `reuses an archived (delivered) slug — the delivered history is shadowed; pick a fresh slug`. Per-item gates could not see the mismatch, and the whole-repo post-wave build passed because each string is valid on its own. A third item that had a declared dependency on the validator ("quote its warning verbatim") stayed consistent — so consistency held **along a declared edge** and drifted between the two items with **no edge between them**.

karta's house pack already asks for this ("state every cross-file term identically, quote the canonical wording, never paraphrase"), but it is an advisory rule with no enforcement point.

## Goal

Give that rule a deterministic enforcement point: a binder can declare canonical strings that several items must render identically, and karta halts a delivery whose assembled result violates a declaration.

## Non-goals

- **No heuristic drift scanner.** Detecting undeclared near-duplicate strings needs a similarity threshold; the dogfood strings share only a few words, so any threshold that catches them floods false positives. A fuzzy gate erodes trust in a determinism-first system. Rejected for v1; revisit only if dogfooding shows the planner chronically fails to surface terms.
- **No code-level canonical-source rule.** "Define each string once and import it" is the right principle but assumes an import graph, which docs, IaC, and shell items do not have. The binder-level declaration is the stack-agnostic stand-in: the declared term *is* the canonical source, and byte-identity *is* the reference check.
- **No new per-item gate.** A per-item gate structurally cannot see cross-item drift. Enforcement is a whole-binder pass.

## Design

### 1. Binder field: `shared_terms`

A new optional binder-level array. Each entry:

| field | type | meaning |
|-|-|-|
| `id` | string (kebab) | unique identifier for the shared term |
| `canonical` | string | the exact substring every listed item must contain, byte-identical |
| `items` | string[] | two or more work-item ids that must each render `canonical` |

`canonical` is a **substring**, not a whole line. The drifted strings live inside interpolations (`f"binder '{slug}' …"`); declaring the stable substring (`reuses an archived (delivered) slug — …`) sidesteps interpolation syntax and keeps the check language-agnostic — it is plain substring presence in files, not parsing.

Example (what the dogfood binder should have carried):

```json
"shared_terms": [{
  "id": "shadow-warning",
  "canonical": "reuses an archived (delivered) slug — the delivered history is shadowed; pick a fresh slug",
  "items": ["archive-aware-validator", "archive-aware-status-engine", "plan-slug-freshness-doctrine"]
}]
```

The field is optional; a binder with no shared wording omits it, and everything downstream treats an absent or empty `shared_terms` as "nothing to check".

### 2. karta-plan surfaces candidates (`plan:terms`)

The synthesis subagent proposes `shared_terms` entries from two signals:

- **Explicit:** an item `contract` that says an item must quote or match another item's string verbatim (the declared-edge case, which already stayed consistent — this makes the invariant machine-checkable instead of prose).
- **Overlap:** two or more items whose contracts describe emitting the same user-facing string or message.

Surfacing is advisory: candidates appear in the plan review card, and the human confirms them into the binder. This is where the cross-item visibility exists — the planner reads every contract at once — so it is the right place to catch the overlap the dogfood missed. It is not a guarantee (see Residual limitation).

### 3. Plan-time validation (`validate_binder.py`)

`validate_binder.py` gains schema checks for `shared_terms`, in its existing deterministic, stdlib, `--self-test` form:

- `id` present, kebab-case, unique across entries.
- `canonical` present and non-empty.
- `items` has at least two entries, each resolving to a real work-item `id` (dangling id → error, mirroring the existing `depends_on` check).
- Warn when a listed item has an empty `touches` (the deliver-time check has nothing to scan for it).

These are structural checks only; they do not read item code (there is none at plan time).

### 4. The enforcement check: `check_shared_terms.py`

A new house-pattern script at `skills/karta-plan/scripts/check_shared_terms.py` (pure stdlib, argparse, embedded `--self-test` with `[PASS]`/`[FAIL]` lines and an `N/N checks passed` tally — the same shape as `validate_binder.py`, and invoked the same way by other skills). It sits beside `validate_binder.py` because both are binder-aware deterministic checks.

**Inputs:** `--binder <path>` and a repo root (default: cwd — the assembled integration worktree).

**Algorithm**, per `shared_terms` entry:

1. Gather each listed item's `touches` files that exist under the repo root.
2. If any listed item has **no** existing touched files, the item is not delivered yet → the entry is `[PENDING]`, skipped (a partial delivery must not fail on items that have not been built).
3. Otherwise, for each listed item, read its existing touched files and test whether `canonical` appears as a byte-identical substring in at least one of them. An item whose files exist but contain no match is a **violation**.
4. Any violation → `[FAIL]`, naming the entry `id`, the offending item(s), and the `canonical` string. Exit non-zero. All entries satisfied (or pending) → exit zero.

Everything is exact substring matching over file bytes — deterministic, no threshold, no language assumptions.

### 5. Deliver and build wiring

- **karta-deliver:** after the existing post-wave build/type-check (`deliver:waveloop` step 4), run `check_shared_terms.py` on the integration tip. A `[FAIL]` halts the wave on the same footing as a failed post-wave build — the wave reverts or the human fixes the wording. Pending entries (items in later waves) are silently skipped and re-evaluated when those waves land.
- **karta-build single-item hatch:** the same check runs before the single item's merge completes, for consistency with the other post-wave gate it already owns.

### 6. House pack

The house pack's advisory "state every cross-file term identically" rule gains a pointer to `shared_terms` as the mechanism that now enforces it for karta's own binders. No new per-item Review-checklist line — the enforcement is the deliver-time whole-binder check, not something a per-item auditor can see.

## Determinism

Every new check is exact-substring or schema validation — no similarity threshold, no model call, stdlib only, each with a `--self-test`. This matches karta's determinism preference (enforced checks over skippable prose or heuristics).

## Residual limitation

The check only catches **declared** terms. If karta-plan never surfaces a shared term, drift still slips through exactly as it did in the dogfood — catching undeclared semantic drift requires semantic understanding, which lives only in the planner. Two things bound it, and the design states them plainly rather than hiding them: the plan-time surfacing prompt (the active ingredient), and the ratchet effect (declared terms accumulate as a project matures). This is an inherent limit of a deterministic mechanism, not a bug to be fixed by a fuzzier one.

## Testing

- `validate_binder.py --self-test`: new cases for a well-formed `shared_terms`, a dangling item id, a single-item entry, a duplicate id, an empty canonical.
- `check_shared_terms.py --self-test`: entries where all items match (pass), one item drifts (fail), an item not yet delivered (pending), an absent `shared_terms` (pass/no-op), a canonical appearing in one of several touched files (pass).
- `validate_plugin.py` stays green (new script mirrored three ways).

## Rollout

Ships as one binder (planned with karta, delivered by karta — the dogfood continues). The field is optional and backward-compatible: existing binders without `shared_terms` are unaffected.
