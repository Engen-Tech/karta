---
id: parity-doc-truth-ledger
family: parity
method: drift-diff
cadence: every-release
cost: S
probe: benchmarks/probes/parity-doc-truth-ledger.py
probe_status: implemented
results: benchmarks/parity/results/
provenance: "lens: parity; merged_from: doc-inventory-parity, doc-claim-verification-ledger, phase-label-registry-completeness"
---

# Doc truth ledger — inventory, claims, links, phase labels

**Question.** Do the numeric/inventory claims, enforcement promises, relative links, and phase-label registry in karta's user-facing docs match the repo's machine truth?

## Procedure

1. One runner, `benchmarks/probes/doc_truth.py` (python3 stdlib only, no LLM), emitting `benchmarks/findings/doc-truth-<version>.json` with PER-LANE counts and a nonzero exit on any non-allowlisted violation.
2. Compute truth vars: `N_agents` = count `agents/*.md` (today 4); `N_skills` = dirs under `skills/` minus `_shared` (today 10); writer split = frontmatter tools containing `Write|Edit`; version = `.claude-plugin/plugin.json`.
3. Lane B (count claims) MUST NOT compare regex hits to a single truth value — that false-positives on locally true sentences (`claude-code.md:29` "dispatches two read-only agents" and `README:87` "the two gate agents" are both correct while `claude-code.md:3`/`:21` "three agents" are wrong). Instead: every regex hit for `(word-number|\d+) (agents|skills|gate agents)` in `README.md`, `docs/how-to/*.md`, `.claude-plugin/marketplace.json` must be bound to a `benchmarks/claims.yaml` entry `{id, file, line-regex, expect}` where `expect` is an expression over truth vars OR a literal (gate-dispatch claims expect literal 2).
4. Lane B verdicts: unbound hit = `unadjudicated-claim` violation; bound hit failing `expect` = violation; entry whose `line-regex` no longer matches = `stale-entry` violation.
5. Lane C: `Karta \d+\.\d+` pins with major < current major, excluding `docs/showcase|docs/releases|docs/specs` (archival allowlist).
6. Lane D: every `skills/` dir (minus `_shared`) must appear in `README.md` or `docs/how-to/*.md` (today only karta-status fails; karta-debt IS discoverable at `README:113` — seed corrected).
7. Lane E ledger seeds: `DG-SCHEMA` (`grep -rl --include='*.py' doc-gardner-schema.json`, expect >=1, today 0 — a doc promises a schema gate no code executes), `KAIZEN-USERREPO` (any hook/skill invoking config validation with a consumer-repo root; today none — `scripts/validate_plugin.py` hard-anchors ROOT to the karta repo, so the kaizen how-to's validator promise misleads about user repos), `HOOKS-MATCHER-PARITY` (each `docs/how-to/hooks.md` table row's claimed tool surface diffed against the actual `hooks/hooks.json` matcher set).
8. Lane F: `os.path.exists` on every relative `[text](path)` link in `README.md` + `docs/how-to/*.md` (skip http/anchors) — a surface validate_plugin's link checker never covers.
9. Lane G: parse the Roots table (`docs/conventions/phase-labels.md:23`) into `{root: doc-path}`; scan `skills/**/*.md` + the two labeled reference docs for trailing two-space `^[a-z]+:[a-z]+$` label tokens and inline colon-path cross-references; assert every used root registered, the table's doc-path is where the labels actually live, and each (doc,leaf) pair is unique.
10. COMPARISON RULE: the metric is the per-lane vector, never a scalar sum; gate = no lane regresses vs the last committed findings JSON ("monotonically down" is dropped — new docs legitimately add claims).
11. `claims.yaml` and the ledger are add-only; retiring an entry requires a `retired: reason` field, and retired counts are reported in the JSON so silencing shows in the diff.
12. Promote lanes into the repo precommit gate only after two consecutive green releases; post-promotion the bench still runs the runner and records the zero.

## Metric and comparability

Per-lane violation vector {B count-claims (incl. unadjudicated + stale-entry), C version-pins, D undiscoverable-skills, E unverified ledger claims, F broken links, G label violations (unregistered roots + wrong-doc-path rows + duplicate leaves)} — gate = no lane regresses vs the last committed findings JSON; add-only ledger with visible retirement counts; never a scalar sum.

The metric stays honest across releases because it is never collapsed to a scalar: each lane is compared against the last committed findings JSON, so a regression in one lane cannot hide behind improvement in another. Truth denominators are recomputed from the repo each run (`agents/*.md`, `skills/` dirs, `plugin.json`), not frozen prose. `claims.yaml` and the ledger are add-only, and retirement requires an explicit `retired: reason` with retired counts reported in the JSON — silencing an entry is visible in the diff rather than an invisible deletion.

## Inputs

- README.md + docs/how-to/*.md
- .claude-plugin/marketplace.json + .claude-plugin/plugin.json
- skills/ + agents/ dir listings and frontmatter
- benchmarks/claims.yaml (new, committed, add-only)
- docs/conventions/phase-labels.md
- hooks/hooks.json + scripts/validate_plugin.py

## Seed observation (v2.21.0, 2026-07-17)

~10 violations today (seed corrected from ~12: karta-debt IS discoverable at README:113 and claude-code.md:19), all real install-path drift: 'three agents' x2 (claude-code.md:3,:21; truth 4), 'two read-only gate agents' x2 (marketplace.json:9,:15 — omitting the two writer agents that edit the user's repo, the trust-critical omission), codex.md:39,:41 pinned 'Karta 1.19' on a 2.21.0 plugin, karta-status with zero README/how-to mentions, the unresolved 'The five skills'/'The two agents' README headings vs 10 skills/4 agents, DG-SCHEMA and KAIZEN-USERREPO both failing, and 2 unregistered phase-label roots (docgardner, kaizen — 7 labels invisible to the convention's own index). Known false-positive traps pinned: claude-code.md:29 and README:87 are locally TRUE and must be bound to literal-2 expects.
