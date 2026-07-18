---
id: flow-spec-contradictions
family: flow
method: deterministic-probe
cadence: every-release
cost: S
probe: benchmarks/probes/flow-spec-contradictions.py
probe_status: implemented
results: benchmarks/flow/results/
provenance: "lens=flow; merged_from=prose-hook-contradiction-count, binder-amend-path-probe, schema-vs-validator-disagreement"
---

# Prose / schema / guard contradiction count

**Question.** How many open disagreements exist between what skill prose and published schemas promise and what the guards and validators actually do — documented paths a hook blocks, vocabulary no code reads, schemas that reject shipped artifacts, and fictional safety nets?

## Procedure

1. Run `benchmarks/flow/check_contradictions.py`, report-only (always exit 0), as:
   `uv run --with jsonschema benchmarks/flow/check_contradictions.py --repo . --out benchmarks/results/flow-spec-contradictions/<version>.json`
2. Canonical tree is pinned to `skills/` + `hooks/` + `scripts/` only (mirror copies in `.agents/` and `plugins/` are `scripts/check_shared_copies.py`'s problem, not this bench's).
3. Pass 1 — ref-vocabulary cross-check: extract full-path matches of `refs/karta/<slug>/item-<id>/(\w+)` from a FIXED file list (`skills/_shared/integration-branch.md`, `skills/karta-deliver/SKILL.md` + references/) and diff two ways against `hooks/scripts/*.py` string literals and REF_STATES: defined-nowhere-read (today: `in-progress`, integration-branch.md:52) and written-per-prose-but-absent-from-Stop-gate-REF_STATES (today: `accepted` vs guard_delivery_stop.py:41).
4. Pass 2 — hooks.json pin: validate every event name and matcher tool name against a committed whitelist (`benchmarks/flow/claude-code-events.json`). Unknown names emit status UNKNOWN-EVENT distinct from findings, so a Claude Code upgrade is bench maintenance, not a karta defect. A name within edit-distance 2 of a whitelisted one is a MISSPELLING finding (closes `scripts/validate_plugin.py`'s acceptance of typos like 'PreTooluse').
5. Pass 3 — promise probes: committed list `benchmarks/flow/promises.json`; each probe = id + file + sentence-REGEX anchor (never line numbers; an anchor miss emits ANCHOR-LOST as its own finding state, never a silent pass) + check:
   - A1: synthesized Edit/Write payloads on a HEAD-committed binder piped to `hooks/scripts/guard_binder_immutability.py` stdin, assert deny exit (payload fixtures shared with flow-guard-enforcement-matrix's `benchmarks/fixtures/hooked-repo/`).
   - A2: the between-waves-edit sentence regex in `skills/karta-deliver/SKILL.md` AND a concrete mechanism anchor named in the probe entry (file + regex that would count as the amend/re-validate mechanism); CONSISTENT only if the sentence is gone OR the mechanism anchor matches.
   - A3 (static; the sandbox replay from the draft is cut — replays belong to dark-midwave-crash-resume): assert that no anchor in `skills/karta-deliver/SKILL.md` + references matches a committed sanctioned-repair regex set; the missing-repair-path finding (fixed id, seeded from gringotts 1f548e5 / parchmark 76aadba) stays open until a repair mechanism anchor appears or re-plan-to-new-slug is documented as the sanctioned path.
6. Pass 4 — schema-vs-validator duals: vendored fixtures in `benchmarks/fixtures/schema-contradictions/` — including a FROZEN copy of gringotts browse-refinements.json (vendor it now from gringotts/.karta/binders/; never reference the live sibling checkout at run time), a minimal shared_terms binder, a backend-only item carrying component_map/icon_map/token_changes, a bogus-extra-key control, and the shipped doc-gardner example config — each validated twice: raw jsonschema against `skills/karta-plan/references/binder-schema.json` (and doc-gardner-schema.json) vs `skills/karta-plan/scripts/validate_binder.py`. Finding when the two disagree, or when both accept what the karta-plan SKILL.md 'the validator will catch it' promise (sentence-anchored, currently line 281) claims is caught.
7. Findings have content-derived stable ids; resolutions move to a resolved list carrying the resolving commit hash (append-only).
8. Output JSON carries the probe-set id list + a probe-set hash.

## Metric and comparability

Open contradiction count computed on the INTERSECTION of probe ids with the previous committed run (honest release-over-release, target monotone decrease on that intersection); total open count reported separately as informational; resolved list append-only with resolving commit hashes.

The headline stays comparable because it is computed only on the intersection of probe ids with the previous committed run — adding probes can never inflate or deflate the release-over-release number, which lands in the informational total instead. Stable content-derived finding ids, an append-only resolved list carrying resolving commit hashes, and the probe-set hash embedded in each output JSON make every run attributable to the exact probe set that produced it.

## Inputs

- hooks/hooks.json + hooks/scripts/*.py
- skills/karta-deliver + skills/_shared references (ref scheme, Gotchas)
- skills/karta-plan/references/binder-schema.json + skills/karta-plan/scripts/validate_binder.py
- benchmarks/fixtures/schema-contradictions/ (new; includes a vendored frozen copy of gringotts browse-refinements.json)
- benchmarks/flow/claude-code-events.json (new committed whitelist)
- benchmarks/flow/promises.json (new committed probe list)

## Seed observation (v2.21.0, 2026-07-17)

At least 7 open findings today, 6 verified live during critique: the between-waves binder-edit prose vs the immutability hook (field-proven by gringotts 1f548e5, with no sanctioned repair path — which is why parchmark 76aadba burned a full re-plan); the dead in-progress ref contract; the Stop-gate's ignorance of the accepted namespace (REF_STATES at guard_delivery_stop.py:41); validate_plugin accepting arbitrary hooks.json event/matcher spellings; shared_terms binders rejected by the published schema (additionalProperties:false) but accepted via validate_binder's runtime injection at validate_binder.py:133 — every modern gringotts binder is invalid per karta's own schema file; UI fields on non-UI items accepted by both validators despite plan SKILL.md:281's claimed catch; doc-gardner-schema.json executed by no one.
