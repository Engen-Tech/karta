---
id: sme-pack-static-suite
family: sme
method: deterministic-probe
cadence: every-release
cost: S
probe: benchmarks/probes/sme-pack-static-suite.py
probe_status: implemented
results: benchmarks/sme/results/
provenance: "lens=sme; merged_from=pin-derivation-fidelity, pack-integrity-sweep, seed-drift-against-upstream"
---

# SME pack, mirror, and pin static integrity suite

**Question.** Do all pack surfaces validate and stay in sync — built-ins, the 3 skill mirrors, consumer overlays — and does every binder's pinned pack set equal what the documented matching rule computes from detect_stack, with no dead/overbroad match tokens and no unpropagated upstream fixes?

## Procedure

1. Layout: `benchmarks/sme-static/` holds `run.sh`, `match_pins.py`, `cross_prefix.py`, `seed_drift.py`, `consumers.json` (enrolled repo paths; each run records the SHA probed), `exceptions.json` (skip-list with one-line justification per entry), and `findings/<date>-<version>.json`.
2. PROBE 1 — pack_integrity:
   1. Run `python3 skills/karta-kaizen/scripts/validate_packs.py skills/_shared/sme/*.md` with `platform-native.md` on the committed `exceptions.json` skip-list — the karta-plan SKILL sme section already declares it "shared reference data, not a pack"; encoding the skip (vs adding frontmatter) avoids changing shipped prompt text. Target 0 findings after exceptions.
   2. Run `uv run scripts/check_shared_copies.py` — this already asserts all 3 `references/sme` mirrors byte-equal `_shared`; do NOT re-validate mirror files individually (identical bytes, pure noise).
   3. Run `validate_packs.py` over each `consumers.json` repo's `.karta/sme/*.md`; size warnings counted separately from failures.
   4. Run `cross_prefix.py` (~30 lines, committed): prefix->basename map over `_shared` + all overlays; fail on a prefix claimed by two basenames or an overlay using a PREFIXES entry registered to a different pack.
3. PROBE 2 — `match_pins.py`, committed ONCE, never re-derived from prose: implements the karta-plan SKILL sme matching steps 1-4 (overlay-over-builtin by basename, disabled suppresses, `always:true` pins, whole-token case-insensitive equality vs detect_stack dependencies/languages, platform-native skipped); embeds sha256 of the SKILL.md section text and exits 2 "matching rule changed — re-verify implementation, update hash" on mismatch; `--self-test` fixtures include the templ-vs-`github.com/a-h/templ` non-match.
4. Per consumer, run match_pins in two modes: contract mode = `python3 skills/karta-plan/scripts/detect_stack.py <root>`; coverage mode = union of detect_stack over every dir <=3 deep holding `package.json`/`pyproject.toml`/`go.mod`/`requirements*.txt`/`Cargo.toml` — diagnostic only (labels the documented rule's monorepo blind spot), never the conformance oracle. The coverage-mode diagnostic MUST keep emitting the unmatched-ecosystem list (stacks with no pack — e.g. JVM/Swift/ML manifests detect_stack does not cover), because that list is the surviving carrier of the owner's "fillable gaps" question.
5. For each binder under `.karta/binders/` + `archive/` that HAS an `sme` key, emit `{binder, era, pinned, expected_contract, expected_coverage, underivable_pins, missing_pins}`; era = plugin version in force at the binder's commit date (karta release tags). Headline conformance % computed only over current-era binders, older eras in a frozen backfill table, sme-less pre-feature binders counted excluded not failing.
6. PROBE 3 — `seed_drift.py`, no sync-commit archaeology (no sync marker exists): for each overlay whose basename exists in `skills/_shared/sme/`, compare overlay content against every historical blob of the built-in (`git -C <karta> rev-list HEAD -- skills/_shared/sme/<f>.md`; `git show <sha>:<path>`). Classify IDENTICAL (== HEAD blob), UPSTREAM-UNPROPAGATED (== older blob), LOCAL-ADDITIVE (contains every HEAD-blob line and match-token set is a superset of the built-in's), DIVERGENT (else).
7. Token audit, reusing the match_pins matcher: dead token = matches nothing in the union of contract+coverage detect_stack outputs across all `consumers.json` repos at their recorded SHAs (corpus recorded in findings JSON); overbroad token = equals a detect_stack language literal in a pack whose basename is not that language.
8. OUTPUT: one findings JSON per run — probe-card rows `{probe, outcome, file, remediation}` plus `{plugin_version, consumer_shas, pack_list, binder_era_counts}`; committed.

## Metric and comparability

Zero-target surfaces: validator failures after exceptions, mirror drift, prefix collisions, DIVERGENT overlays, dead tokens. Plus current-era pin-conformance % and counted-not-gated diagnostics (size warnings, coverage-mode gaps incl. the unmatched-ecosystem list, UPSTREAM-UNPROPAGATED). Release-over-release diff compares same-era rows only.

The metric stays honest across releases because comparisons are era-partitioned: conformance % is computed only over current-era binders, older eras sit in a frozen backfill table, and release-over-release diffs compare same-era rows only. Sme-less pre-feature binders are excluded, not counted as failures, so the denominator's meaning never shifts. Each run records consumer SHAs and the dead-token corpus in the committed findings JSON, exceptions live in a committed skip-list with per-entry justification, and match_pins.py is committed once with an embedded sha256 of the SKILL.md rule text — a rule change forces an explicit exit-2 re-verification instead of a silent drift of the oracle.

## Inputs

- skills/karta-kaizen/scripts/validate_packs.py
- scripts/check_shared_copies.py
- skills/_shared/sme/*.md + git history (5984580, 4fbd308)
- skills/{karta-plan,karta-build,karta-verify}/references/sme/ mirrors
- skills/karta-plan/scripts/detect_stack.py
- parchmark and gringotts .karta/sme/ + .karta/binders/**
- benchmarks/flow/seed-map-2026-07-17.json plan gaps ("No script computes the pinned pack set", "Kaizen seed freezes packs")

## Seed observation (v2.21.0, 2026-07-17)

Fails on three fronts today (all verified live): platform-native.md INVALID (no frontmatter) in the canonical dir and all three mirrors, plus 2 size warnings on gringotts overlays (go-htmx 5028B, go-naming 3576B); all 12 parchmark pinned binders carry 2 pins underivable from the scripted contract input (root detect_stack returns empty on the monorepo — the planner LLM improvised the set, exactly the mapped "No script computes the pinned pack set" gap) and miss the python pack coverage-mode matching mandates; gringotts go-htmx is DIVERGENT after d95bf72's incomplete refresh (lost built-in frontmatter; dead token 'templ' that can never whole-token-equal 'github.com/a-h/templ'; overbroad token 'go'). Minimalism — the only byte-guarded pack — is the only one that stayed synced. The archived sme-less binder note-edit-save-workflow.json is the denominator trap the era partitioning absorbs.
