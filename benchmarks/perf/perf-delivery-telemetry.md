---
id: perf-delivery-telemetry
family: perf
method: telemetry-mining
cadence: every-release
cost: M
probe: benchmarks/probes/perf-delivery-telemetry.py
probe_status: partial
results: benchmarks/perf/results/
provenance: "lens=perf; merged_from=delivery-cost-mining, gate-serialization-tax, redundant-oracle-runs, mandated-script-invocation-compliance"
---

# Delivery telemetry mining — cost, serialization tax, redundancy, script compliance

**Question.** What does one delivered item actually cost (spawns, tokens, cache-read, wall-clock per agentType), how much wall-clock is burned at mandated serial points, how often are oracle runs provably redundant — and do sessions actually invoke the scripts the prose mandates (scan_secrets, validate_binder, check_shared_terms, karta_next)?

## Procedure

1. Invocation: `benchmarks/perf/mine_sessions.py <transcript-dir>... --since <date> --out benchmarks/perf/results/<date>-<project>-<plugin-ver>.json`. Pure JSONL parsing, no judge.
2. PRECONDITIONS (README): raw transcripts decay — cleanupPeriodDays is unset (~30d cleanup; parchmark's dir was down to 4 files by 2026-07-17, the 1.14.0 raws are gone). Set cleanupPeriodDays>=365 in `~/.claude/settings.json`, mine within 7 days of each delivery, and treat the committed dated JSONs as the durable longitudinal series — old points are NEVER re-derived from raws.
3. Mode (1) Cost: delivery window = main-session Skill tool_use with skill matching `/karta[-:]karta-deliver|karta-deliver/`, window end = session end or next deliver invocation. Per `<session>/subagents/agent-*.jsonl`: agentType+description from the sibling `.meta.json` (meta has NO usage — sum `message.usage` of assistant lines in the agent's own .jsonl: output_tokens, cache_read_input_tokens; wall = last minus first timestamp; model = mode of `message.model`). builder = agentType general-purpose AND first user message contains 'karta-build'; gates = acceptance-reviewer/safety-auditor (incl. karta:-scoped forms); map builder->item by matching binder item ids against meta description; unmapped spawns go to an explicit 'unattributed' bucket, never dropped. Emit spawns-per-type, per-item MEDIANS (output tokens, cacheR, wall min), overhead_ratio with numerator AND denominator emitted separately, models per agentType.
4. Mode (2) `--timeline`: builder/gate intervals from transcript timestamps. Merge times NOT from reflog (reflog expires ~90d and integration branches are deleted post-merge): use `git -C <consumer> log --first-parent --format='%cI %s'` matching karta's item/wave tags in merge subjects; fall back to `refs/karta/<slug>/*` only if present. gate_serial_tax = sum of min(acceptance_wall, safety_wall) per item; barrier_idle = sum of (wave-last build end minus item build end); record boolean sequencing assertion (safety_start >= acceptance_end) per item, asserted against skills/karta-deliver/SKILL.md's mandated order.
5. Mode (3) `--oracle-count`: normalize both binder oracle.command+floor commands (live + archive/ JSONs) and every Bash tool_use input: strip leading `cd X && `, strip `uv run [--script]` prefix, collapse whitespace; attribute via cwd containing the item worktree path. redundant = two consecutive matched runs, same cwd, ZERO intervening mutating tool_use (Write/Edit/NotebookEdit, or Bash matching `git apply/checkout/merge/revert`, `sed -i`, `tee`, or `>` redirection) — a `git rev-parse HEAD` pair with equal output upgrades the flag to 'confirmed'. Report median/max runs-per-item + confirmed redundant count; count unmatched test/lint-like runs separately so the denominator's meaning never shifts.
6. Mode (4) Compliance, one row per script with transcript-counted denominators: scan_secrets.py before each git commit in an item worktree with no intervening mutation (denom = commits); validate_binder.py before first worktree-creation Bash (denom = deliveries); check_shared_terms.py after each wave build, only for binders with non-empty shared_terms (denom = qualifying waves); karta_next.py --footer before session end (denom = deliver sessions). Sessions with missing/truncated subagents/ reported as an 'unmeasurable' count, never silently excluded.
7. Output JSON carries a context block: `{project, binder slug, plugin version, item count + estimate mix (S/M/L), models per agentType, claude-code version if present}`.
8. COMPARISON RULE: version-over-version diffs are computed for the SAME project only; cross-project deltas print but are labeled advisory (confounded by item difficulty). Rollout in two passes: modes (1)+(4) first — they catch the observed defects (cost curve; whether the backend-hygiene delivery skipped scan/validate); (2)+(3) second.

## Metric and comparability

Per-item cost medians (output tokens, cacheR, wall min) + overhead_ratio (numerator and denominator emitted) + gate_serial_tax and barrier_idle minutes (and % of delivery wall) + per-item sequencing boolean + median/max oracle runs-per-item + confirmed same-SHA redundant count + per-mandated-script compliance % with transcript-counted denominators + unmeasurable-session count — version-over-version same-project only; cross-project deltas labeled advisory.

The metric stays honest across releases because the longitudinal series is the committed dated JSONs, never re-derived from decaying raw transcripts, and every ratio ships its numerator and denominator separately: compliance rows carry transcript-counted denominators, unmatched oracle-like runs are counted apart so the denominator's meaning never shifts, unmapped spawns land in an explicit unattributed bucket, and unmeasurable sessions are reported as a count rather than silently excluded. Version-over-version comparisons are restricted to the same project, with cross-project deltas printed but labeled advisory (confounded by item difficulty), and each output's context block pins plugin version, models per agentType, and item estimate mix so a changed cohort cannot pass as a changed framework.

## Inputs

- ~/.claude/projects/-mnt-agent-storage-vader-src-gringotts/*.jsonl + subagents/
- ~/.claude/projects/-mnt-agent-storage-vader-src-parchmark/*.jsonl + subagents/
- ~/.claude/projects/-mnt-agent-storage-vader-src-karta/ (dogfood deliveries)
- consumer .karta/binders/*.json + archive/ (oracle.command per item, shared_terms presence)
- skills/karta-deliver/SKILL.md (mandated script invocations + gate order)

## Seed observation (v2.21.0, 2026-07-17)

gringotts 2.21.0: 12 builders / 193.4M cacheR / 148.7 min + 13 acceptance (6.3M, opus) + 10 safety (2.3M, opus) + 3 gardner + 3 kaizen; overhead_ratio ~0.60. The parchmark 1.14.0 comparison point survives only as previously-committed numbers (raws deleted) and any builder-cacheR delta against it is a cross-project confound — labeled advisory, never a regression claim. Gates ran strictly serial (21.0 min acceptance + 13.3 min safety; parallel gates would have saved up to ~13 min) and merges trailed the slowest build per the Step-3 barrier. Expect >=3 oracle-chain executions per item with an unconditional same-SHA merged-tip re-run on single-item binders. Compliance mining yields the first-ever measured secret-scan number — likely below 100%, and it will show whether the backend-hygiene delivery that dropped markers also skipped scan/validate.
