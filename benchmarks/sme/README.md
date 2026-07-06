# SME pack benchmarks

Measure whether an SME pack earns its place: run the same fixture task **with the pack** and **without it**, then compare.

## Protocol (per fixture)

1. Pick a fixture under `fixtures/`.
2. **Arm A (pack on):** plan + build the fixture with the binder pinning the pack(s). Save the build's `git diff <integration>...HEAD` to `a.diff`.
3. **Arm B (pack off):** repeat with the binder's `sme: []`. Save to `b.diff`.
4. Compare: `python run.py --with a.diff --without b.diff --label date-picker`.
5. **Safety axis:** run the fixture's acceptance gate (`karta-verify`) on both arms and record pass/fail. A pack must not cut correctness to cut code — if Arm A is smaller but fails the gate, the pack lost.
6. Repeat for a small `n` and report medians.

## Honesty rule

Report only the **A/B delta on these fixtures**. Never print a per-repo "you saved X lines/tokens here" number: in a live repo the unbuilt version was never written, so there is no real baseline to subtract from. The only honest per-repo figure is the `karta-debt` ledger (a counted list of real markers).

## Results

- [2026-07-06 — date-picker](results/2026-07-06-date-picker.md): first recorded run (n=3 per arm, packs `minimalism` + `vue` vs `sme: []`).

## Automation note

Full push-button automation depends on a headless build path. Today the runner (`run.py`) compares two captured diffs; capturing them is the manual part of the protocol above.
