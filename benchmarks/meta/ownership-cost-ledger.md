---
id: ownership-cost-ledger
family: meta
method: drift-diff
cadence: every-release
cost: S
probe: benchmarks/probes/ownership-cost-ledger.py
probe_status: implemented
results: benchmarks/meta/results/
provenance: "lens=completeness; merged_from=[]"
---

# Ownership cost ledger and standing buy-vs-build re-check

**Question.** What does it cost to KEEP karta and its bench — mirrored/duplicated bytes and sync scripts, manual release steps, bench run wall-clock and token spend per cycle, findings requiring human triage — is the trend growing faster than the value, and (standing rule) has any bench component been overtaken by an existing tool that should replace it?

## Procedure

1. Per release, run one stdlib script `benchmarks/field/ownership_ledger.py`; it emits a ledger row committed to `benchmarks/field/results/ownership-ledger.json` (append-only).
2. Row part (a) — machinery counts: sync/projection scripts, byte-identical mirrored copies and total duplicated bytes (`scripts/check_shared_copies.py` scope + the 3 skill mirrors + codex projections under `.agents/`), hook scripts, bench runner scripts and their LOC.
3. Row part (b) — process counts: manual steps in the release flow not enforced by release-gate-blocking-smoke.
4. Row part (c) — bench spend: sum wall-clock and token cost from the gate artifact plus quarterly-arm results JSONs.
5. Row part (d) — triage load: bench findings opened vs resolved vs waived this cycle across all vector results.
6. Buy-vs-build re-check: for each hand-rolled bench component (gate runner, transcript miner, fixture harness, stats), write one row citing the committed evaltool verdict (`benchmarks/research/evaltool-2026-07-17.json` — the committed copy of the session buy-vs-build verdicts) and a yes/no "still correct?" with a one-line reason.
7. Start by resolving the recorded OPEN CONTRADICTION: the evaltool verdicts say buy promptfoo as chassis and vendor bayes_evals, while the sharpened vectors dropped both from their per-vector procedures (promptfoo not installed; bayes machinery cut as false precision at n<=3). The chassis reconciliation in the spec (promptfoo for Tier-1/2 orchestration only; bayes at k>=5 only) is the first row's proposed answer.
8. Each "no longer correct" row must carry an action or a dated waiver.
9. Rows append; the diff vs the previous row is the deliverable.

## Metric and comparability

Per-release ledger row diff: duplicated-bytes delta, script-count delta, bench wall-clock/token delta, unresolved-triage count, and count of buy-vs-build rows answered "no longer correct" (each must carry an action or a dated waiver).

The ledger is append-only, so every release's row stays on record and each cycle's deliverable is the diff against the previous row — trends cannot be rewritten, only extended. Buy-vs-build rows are anchored to the committed evaltool verdicts file, so the "still correct?" answer is always judged against the same recorded evidence, and every "no longer correct" answer must carry an action or a dated waiver rather than silently lapsing.

## Inputs

- `scripts/sync_codex_skills.py` + `scripts/check_shared_copies.py` scope
- `hooks/scripts/` + `benchmarks/**` runner scripts
- `benchmarks/gate/results/` + quarterly-arm results JSONs (spend numbers)
- all vector findings/results (triage load)
- `benchmarks/research/evaltool-2026-07-17.json` (committed copy of the session buy-vs-build verdicts)

## Seed observation (v2.21.0, 2026-07-17)

The owner's doctrine created a lot of machinery — 6 hooks, 3 mirror trees, byte-copies in 4-5 skill dirs, sync scripts, and now a 22-vector bench — and nothing measures whether upkeep is compounding. The standing buy-vs-build rule currently has evidence (the evaltool verdicts) but no executor: those verdicts already contradict the shipped vector procedures and nobody is assigned to notice. Without this ledger the bench itself becomes the next unbounded maintenance sink, invisible until a release cycle stalls on it.
