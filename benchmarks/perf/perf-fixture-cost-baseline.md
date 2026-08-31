---
id: perf-fixture-cost-baseline
family: perf
method: ab-fixture-run
cadence: quarterly
cost: L
probe: benchmarks/probes/perf-fixture-cost-baseline.py
probe_status: partial
results: benchmarks/perf/results/
provenance: "lens: perf; merged_from: fixture-phase-cost-ab"
---

# Fixed-fixture per-phase cost baseline

**Question.** On a pinned fixture repo and a canned binder, what does one karta delivery cost per phase in tokens and wall-clock — measured identically across plugin versions so the number is a true regression signal, unconfounded by repo or stack?

## Procedure

1. One-time: create `benchmarks/perf/fixture-v1/` = {`repo.tar.gz` (frozen tiny karta-testbed-shape repo with own git history and pre-committed `.karta/binders/bench.json`: one S unit-oracle item + one M integration-oracle item, no UI), `settings.json` (permission allowlist the delivery needs), `runner.sh`, README recording the pin mechanism}. Fixture bytes are immutable; a plugin release that breaks the binder schema mints fixture-v2 and RESETS the baseline — never compare across fixture versions.
2. Per release (n=3): `RUN_ROOT=$(mktemp -d /tmp/karta-bench.XXXXXX); tar -xzf repo.tar.gz -C $RUN_ROOT` — never a session scratchpad (its path changes every session and scrambles the `~/.claude/projects` mapping).
3. Pin plugin: git worktree/clone of the karta repo at tag `v<X.Y.Z>` into `$RUN_ROOT/plugin`, registered as a local plugin source in `$RUN_ROOT/repo/.claude/settings.json` — mechanism fixed in the README, identical every quarter.
4. `cd $RUN_ROOT/repo && timeout 90m claude -p '/karta:karta-deliver bench' --output-format stream-json --permission-mode bypassPermissions > run$i.jsonl`; `/usr/bin/time -v` wraps it for wall+RSS.
5. Classify: COMPLETE iff both item tags exist on the integration branch AND the stream ends in a success result AND no AskUserQuestion/human-prompt event occurred (`skills/karta-deliver/SKILL.md:122` enforces a live human waiver channel — headless, that is a stall, not a datapoint); INCOMPLETE runs are committed with reason and excluded from aggregates.
6. Mine: compute the mangled `~/.claude/projects` dir from `$RUN_ROOT/repo`, take `.jsonl` files with mtime in the run window, run `benchmarks/perf/mine_fixture.py` (shared with perf-delivery-telemetry's `mine_sessions.py` if that ships first; else this vector builds it) with `--result run$i.jsonl` emitting per run: spawned-context count per item, per-phase turn counts, verify-retry count, merge-time re-validation count, per-phase output tokens, per-role context accounting (messages, cache read, cache creation, plain input, output — deduplicated by message id), context-tokens/item, and — apportioned from the run's own recorded dollar cost — cost-USD/item, wall minutes, resolved model-ID set, claude CLI version.
7. Compare vs the previous release's committed JSON: PRIMARY = structural counts (spawns/item, retries, re-validation passes) — expected exactly reproducible across all 3 repeats; any cross-version drift is the flagged finding, no statistics needed. SECONDARY = median output-tokens/item, median context-tokens/item, median cost-USD/item, and wall-min/item with [min,max]; flag only if the median shifts >25% AND the resolved model-ID set is unchanged; if the model set changed (opus/sonnet aliases in `agents/*.md` float across refreshes), record 'model refresh — cost delta not attributable this quarter' instead of a verdict (join against `benchmarks/epochs/epochs.json` from model-refresh-sentinel). context-tokens/item and cost-USD/item are here because cache read is roughly 90% of the billed tokens on this fixture and cache traffic is nearly all of the spend — output tokens per item do not measure what costs money.
8. Commit `benchmarks/perf/results/<date>-fixture-v1-plugin-v<X.Y.Z>.json` including INCOMPLETE runs.
9. Dropped from the draft: promptfoo as a per-vector requirement (adds nothing over `claude -p` here) and bayes_evals paired_comparisons (6 paired observations cannot support credible-interval claims; exact-match on near-deterministic counts plus a coarse threshold is the honest comparator).

## Metric and comparability

PRIMARY: structural counts (spawned contexts per item, verify retries, merge-time re-validation passes) — expected exactly reproducible across 3 repeats; any cross-version drift is the flagged finding. SECONDARY: median output-tokens/item, median context-tokens/item, median cost-USD/item, and wall-min/item with [min,max], flagged only if median shifts >25% AND resolved model-ID set unchanged; model-set change = 'model refresh — not attributable this quarter'. context-tokens/item and cost-USD/item are SECONDARY because cache read is roughly 90% of the billed tokens on this fixture and cache traffic is nearly all of the spend, so output tokens per item alone do not measure what costs money. INCOMPLETE runs committed with reason, excluded from aggregates. Never compare across fixture versions.

The number stays honest across releases because the fixture bytes are immutable and a schema-breaking release mints fixture-v2 with a baseline RESET rather than a cross-fixture comparison; the plugin pin mechanism is fixed in the README and identical every quarter; cost deltas are attributed only when the resolved model-ID set is unchanged, with model refreshes joined against `benchmarks/epochs/epochs.json` and recorded as non-attributable; and every run — including INCOMPLETE ones with their reason — is committed to `benchmarks/perf/results/` so the aggregate denominator is auditable.

## Inputs

- `benchmarks/perf/fixture-v1/` (new, frozen)
- `benchmarks/perf/mine_fixture.py` (shared lineage with `mine_sessions.py`)
- karta repo release tags (plugin pinning)
- `benchmarks/epochs/epochs.json` (model-refresh-sentinel epoch authority)
- `skills/karta-deliver/SKILL.md:122` (human waiver channel — headless stall classifier)

## Seed observation (v2.21.0, 2026-07-17)

No comparable cross-version number exists today — the first run establishes the 2.21.0 baseline. From live mining, expect roughly 3-4 spawned contexts per trivial item (builder + acceptance + safety), ~40k+ output tokens per item, and double-digit minutes wall even on a toy fixture, dominated by the opus gate pair and merge-time re-validation. Known confound pinned in-procedure: acceptance-reviewer and safety-auditor pin model by 'opus' ALIAS, so cross-quarter token/wall deltas can be model refreshes — hence the model-ID-set gate.
