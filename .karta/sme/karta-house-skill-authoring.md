---
name: karta-house-skill-authoring
description: House rules for authoring karta's own skills, agents, and doctrine prose
always: true
---
## Do
- State every cross-file term — marker grammar, mode name, phase id, report line format — identically everywhere it appears; quote the canonical wording, never paraphrase it. A binder declares such a string as a `shared_terms` entry, and `check_shared_terms.py` then enforces it — asserting byte-identity across the listed items at deliver and build time — so the identity is a checked invariant, not a wish.
- Give every new invariant an enforcement point (a validator, a gate, a hook); prose alone is a wish.
- Model new scripts on the house pattern: stdlib-only, argparse, a `--self-test` mode with [PASS]/[FAIL] lines.
- Write human-facing prose (README, docs/how-to, release notes) in plain language: lead with what the reader does, short sentences, no unexplained codenames.
- When a self-test cannot exercise real runtime behavior (a browser, a network round trip, a UI timer), say so plainly in the build report: name what was verified by direct call or by static inspection of rendered source, and what was not verified at all, rather than letting a passing self-test imply end-to-end coverage (seen 2026-08-15, watch-derivation-cost delivery).
- State a numeric bound (a byte ceiling, a call count, a timeout) together with the condition it was measured under — input size, fixture shape, machine — never as a bare number; measure it at more than one point on that axis so the bound is reproducible rather than asserted (seen 2026-08-15, watch-derivation-cost delivery).

## Don't
- Don't edit a mirror or `references/` copy by hand — edit the canonical file (`skills/_shared/`, `skills/*/SKILL.md`, `agents/*.md`) and run the sync writers.
- Don't move or rename a phase, step, or file and leave old pointers to it standing in other doctrine files.
- Don't add a new shared file without confirming `check_shared_copies.py` covers its copies.

## Patterns
- Canonical → generated: `skills/_shared/` and `agents/*.md` are sources; `.agents/`, `plugins/`, `.codex/`, and `references/` copies are outputs of the sync scripts.
- Small validators over prose promises: the `validate_binder.py` / `validate_packs.py` shape — parse strictly, fail closed, self-test.

## Review checklist
- [ ] house.1 — A diff editing a canonical `skills/_shared/`, `skills/*/SKILL.md`, or `agents/*.md` file carries its regenerated mirror copies in the same change.
- [ ] house.2 — Every new script under `scripts/` or `skills/*/scripts/` ships a runnable `--self-test` mode.
- [ ] house.3 — No doctrine pointer (phase id, step name, path) still refers to a location this same diff moved or renamed.
- [ ] house.4 — A new check that asserts an invariant (a self-test assertion, a validator rule, a regression guard) ships in the same diff with a companion negative case — a deliberately violating fixture, a mutation, or a known-bad input — proving the check can actually fail. A check never exercised against a failing case is not known to work (seen 2026-08-15, watch-derivation-cost delivery: git-call-count-invariant's per-binder-derivation negative control, lean-box-benchmark's five mutation tests, cache-derived-state's suppressed-key backstop test).
