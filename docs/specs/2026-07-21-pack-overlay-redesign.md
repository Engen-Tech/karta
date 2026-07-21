# Pack provenance and the end of silent shadowing — design (post-roundtable v2)

Status: roundtabled 2026-07-21 (5-provider adversarial panel: deepseek, glm-5p2, kimi, minimax,
qwen — all substantive; convergent findings folded in below). Research base: exa deep-research
cross-ecosystem survey (kustomize, Nix/flake.lock, ESLint extends, Homebrew taps, chezmoi,
Go vendoring, SAP fork-metadata, three-way-merge doctrine). Supersedes the v1 draft.

## Problem

`.karta/sme/<name>.md` shadows the built-in `<name>` entirely — whole-file, overlay-wins, silent.
Proven failure classes (gringotts, 2026-07-21, twice in one day): stale shadows silently supplying
plan-time rules; local improvements hoarded until hand-promotion (htmx.8); match-token divergence.

## The model: four legal states, one comparison, stamp never trusted

All comparisons use **canonicalized bytes** (NFC, LF, BOM stripped, trailing whitespace trimmed —
panel-convergent: raw-byte comparison false-halts on CRLF/BOM/formatter churn) of the local file
minus its provenance block, against the canonicalized shipped built-in resolved by **casefolded
basename** (panel-convergent: APFS/NTFS case-insensitivity breaks case-sensitive resolution;
lowercase basenames enforced at seed time). **The comparison is the sole enforcement signal; the
stamp is diagnostic only** — a forged or missing stamp changes the halt message, never the verdict
(panel invariant: any code path gating on the stamp is a bug).

| State | Definition | Resolution |
|-|-|-|
| Seeded cache | canonical-identical to a shipped built-in | built-in is used; copy is a read surface + Codex parity |
| Stale cache | differs from current built-in, but identical to the **stamped base** version — no local delta | **auto-reseed, no halt** (panel-convergent correction: pure staleness is not a fork; only a true local delta earns a halt). Eager at upgrade/migration, lazy fallback at plan time, always a visible logged line |
| Suppression | frontmatter `disabled: true`; body beyond frontmatter is free commentary, never compared (panel: preserve human rationale, no silent truncation) | pack not pinned; re-enabling re-enters classification |
| Project pack | basename (casefolded) collides with no built-in | first-class local pack; may `extends` a built-in |

Anything else is an **illegal shadow**: a same-basename file carrying a genuine local delta.
That — and only that — halts `plan:sme`, **per pack, not per plan** (panel: one divergent pack
must not brick 29 clean ones), with three verbs:

- `reseed` — discard the local delta, restore the cache;
- `promote` — the delta goes upstream into the built-in (the htmx.8 flow, now named; realistic in
  the house setting where consumers and karta share an owner — third parties default to rename);
- `rename` — move the delta to a project pack, reseed the base.

Bounded escape hatch (panel-convergent: incident-flow needs forward motion): a per-pack,
bench-logged override (`KARTA_PACK_DRIFT_OK=<basename>`) that lets ONE plan proceed on the stale
rules while recording the debt — audited deferral, not skippable prose. Orphaned caches (built-in
removed/renamed upstream — panel: the missing fourth transition) get their own two verbs at the
same halt: `adopt` (become a project pack) or `drop`.

## Project packs and `extends`

- `extends: <builtin>` appends the project pack's checklist items to the built-in's; match tokens
  are additive (union) for firing the combined checklist.
- `exclude_rules: [<rule-ids>]` — the panel's unanimous gap: per-rule subtraction, declarative and
  reviewable, so one bad-fit rule never forces whole-pack suppression or a fork. Excluded ids are
  reported at plan time (visible, like retired ledger entries).
- `id_prefix` required and unique per repo; dangling `extends` (built-in renamed) resolves through
  a rename-alias table shipped for one minor release.

## Enforcement, stated honestly (panel-convergent: the v1 hook claim was overclaimed)

| Layer | Coverage | Honest scope |
|-|-|-|
| `plan:sme` via `check_pack_provenance.py` (ships with karta-plan, stdlib) | all divergence incl. git-sourced | **the authoritative gate** |
| `guard_pack_write` (+ its new Bash-internals coverage) | interactive authoring in Claude/Codex sessions | deterrent — git pull/merge/checkout, external editors, sync tools bypass it by construction |
| kaizen | delivery-time reconciliation | the Renovate of packs: reseeds/renames land as reviewed `kaizen:` commits; never leaves an illegal shadow behind |
| bench (`sme-pack-static-suite`) | karta-side regression | DIVERGENT class goes to zero post-migration and stays there |

## Rollout: two-phase, breaking change named as such (panel-convergent)

- **Phase 1 (minor)** — additive: canonicalization, stamping (eager `migrate-packs` pass — no lazy
  stamping window), cache/stale/orphan classification, `extends` + `exclude_rules`, auto-reseed of
  stale caches, and the illegal-shadow condition as a loud bench-logged WARNING. Overlay-wins still
  functions, deprecated. The warning period populates the bench with real divergence data.
- **Phase 2 (major, karta 3.0.0)** — the halt becomes the default; overlay-wins removed from the
  docs and the code. Removing documented behavior is a semver major — the v1 "one minor release"
  framing was wrong.
- Consumer sweeps (gringotts, parchmark) happen in phase 1: reseed the stale caches (deltas are
  already upstream), stamp everything, zero project packs needed today.

## Considered and rejected

- **Per-repo pack.lock pinning the built-in set** (kimi): real determinism gain, but karta's whole
  surface is ambient-plugin-versioned — pinning packs alone buys little and adds a lockfile to
  every consumer. Revisit only if version-skew halts show up in the field despite auto-reseed.
- **Patch/delta overlay language** (kustomize-style): over-engineered for ~3.5KB markdown packs;
  `extends` + `exclude_rules` covers the composition need with two frontmatter keys.
- **Signing `check_pack_provenance.py`** (minimax): the plugin supply chain is the trust boundary
  for every shipped script equally; pack-check signing alone is theater.

## Adjacent defects surfaced during this exercise (separate fixes, same feedback batch)

1. `precommit_gate.py` is cwd-blind: it gated a commit in a *different repo* (gringotts) and ran
   karta's gates there. It must scope to commits whose repo root is the karta repo.
2. Binder amendment has no sanctioned verb: amending a committed-but-undelivered binder required
   retract-commit → edit → re-commit around the immutability guard. Either document that flow as
   the amendment path or give karta-plan an explicit `amend` mode that performs it.
