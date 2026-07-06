# SME pack benchmark — date-picker fixture — 2026-07-06

First recorded run of the [SME pack benchmark protocol](../README.md) on the
[`date-picker` fixture](../fixtures/date-picker.md).

## Protocol followed

- **Arm A (pack on):** packs on: `minimalism` + `vue`.
- **Arm B (pack off):** sme off (`sme: []`).
- **n = 3 per arm.** All six builds were produced by the same model on 2026-07-06.
- **Acceptance caveat:** acceptance was judged **statically by read-only agents
  reading the diffs against the fixture task** — not by an executed dev server.
  A PASS here means the diff plausibly satisfies the task on inspection, which
  is weaker evidence than the protocol's `karta-verify` behavioral gate.
- Comparison: `python3 run.py --with a<i>.diff --without b<i>.diff --label date-picker`
  per pair (a1/b1, a2/b2, a3/b3).
- Added-LOC counts below are run.py's, cross-checked by an independent recount of
  each diff. One self-report disagreed: **b2 reported 13 added lines; the diff
  contains 14** (an uncounted blank `+` line in the script hunk). The recount (14)
  is what's recorded.

## Per-run results

| run | arm | added LOC | new deps | acceptance | approach (one line) |
|-|-|-|-|-|-|
| a1 | A | 5 | 0 | PASS | Labeled native `<input type="date">` (autocomplete="bday") bound to a typed `dateOfBirth` ref, included in the submit payload |
| a2 | A | 16 | 0 | PASS | Native date input (:max=today) mirroring the existing field pattern; also repaired the broken baseline build (6-line vite.config.js wiring the already-installed @vitejs/plugin-vue) plus a 2-line .gitignore |
| a3 | A | 8 | 0 | PASS | Native date input with :max=today and autocomplete="bday", wired to a JSDoc-typed ref matching the existing field pattern |
| b1 | B | 19 | 0 | PASS | Native date input (autocomplete="bday", :max=today) wired to a dateOfBirth ref and the submit payload |
| b2 | B | 14 | 0 | PASS | Native date input (autocomplete="bday", max=today) plus a dateOfBirth ref wired into the submit payload |
| b3 | B | 8 | 0 | PASS | Native date input (v-model'd dateOfBirth ref, autocomplete="bday", :max=today) matching the existing label/input pattern |

No run added a manifest dependency; no run installed a date-picker library or
built wrapper components.

Reading note on a2: 8 of its 16 added lines are baseline repair (vite.config.js
+ .gitignore) that the fixture needed to build at all, not date-picker feature
code — its feature-only footprint is 8 lines.

## Per-arm medians

| metric | Arm A (packs on) | Arm B (sme off) |
|-|-|-|
| added LOC (median of 3) | 8 | 14 |
| new manifest deps (median of 3) | 0 | 0 |

## run.py outputs

```
=== pair 1 (a1 vs b1) ===
## SME pack A/B — date-picker
  added LOC   with pack:     5   without:    19   delta: -14
  new deps    with pack:     0   without:     0   delta: +0
  (A/B delta on this benchmark task — not a per-repo savings figure)
=== pair 2 (a2 vs b2) ===
## SME pack A/B — date-picker
  added LOC   with pack:    16   without:    14   delta: +2
  new deps    with pack:     0   without:     0   delta: +0
  (A/B delta on this benchmark task — not a per-repo savings figure)
=== pair 3 (a3 vs b3) ===
## SME pack A/B — date-picker
  added LOC   with pack:     8   without:     8   delta: +0
  new deps    with pack:     0   without:     0   delta: +0
  (A/B delta on this benchmark task — not a per-repo savings figure)
```

## Safety axis

All six runs passed acceptance (with the static-judging caveat above). No arm
produced a smaller diff by failing the gate, so no run loses on the
smaller-but-broken rule. Neither arm fell into the fixture's over-build trap:
zero new dependencies in all six runs.

## Conclusion

On this fixture the packs did not change the chosen approach — both arms
independently picked the platform-native `<input type="date">` with zero new
dependencies in all six runs, so the `minimalism` pack's dependency trap never
bit either arm. The packs did shift diff size in the smaller direction: median
added LOC was 8 with packs on versus 14 with sme off, and the one pair where
Arm A was larger (a2, +2) owes its size to 8 lines of baseline build repair
rather than feature code. This is an A/B delta on this fixture only — all six
runs passed a statically judged acceptance gate, and no per-repo savings claim
follows from it.
