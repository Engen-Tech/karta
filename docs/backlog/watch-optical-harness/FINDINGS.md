# Measuring optical sizing on the Karta Watch page

**Filed** 2026-08-23. **Status** Ready (measured, scoped, unblocked). **Blocks on** the
`watch-font-adherence` binder landing, since there is nothing to measure until the variable face
ships.

This is the brief for `scripts/check_optical_sizing.py`. It is written as work rather than as
binder items, and the reason is the last section — it is the part worth reading if you are about
to plan this instead of building it.

## What is wrong with the page

The design asks Google Fonts for `Newsreader:opsz,wght@6..72,400;6..72,500` — a live optical-size
axis (`design/Karta Status Design Redesign/Karta Watch.dc.html:13`). karta forbids a font CDN, so
the faces were vendored same-origin, and each was pinned with `fontTools.varLib.instancer` before
subsetting. Newsreader was pinned at `opsz 18`, which is the axis default, so nothing looked
obviously broken. The page sets Newsreader at six sizes and renders five of them at the wrong
optical size.

The `watch-font-adherence` binder ships the axis. It does **not** prove the page renders at the
right optical size — that is this work.

## Why no fidelity run caught it

`docs/designs/karta-watch-1440x900-light.html` is the frozen design reference every comparison
runs against. Its header records that it dropped the export's Google Fonts link and repointed its
`@font-face` rules at the repo's own vendored files, so the capture opens with no network and
cannot drift when a font host does. Both reasons are good. The side effect is that **the design
side of every fidelity run has been rendering with the page's own flattened faces** — both sides
agreed because both sides were the same font.

The binder makes the reference say so in its own header, enforced by a floor case. It does not
make the reference an independent font witness, and nothing should assume it is one.

## Measurements

All taken during planning against real cuts built from the pinned upstream
(`google/fonts@352f6b7d9d6cc4fa9e242b931291d31b21a6dc84`, `Newsreader[opsz,wght].ttf`,
`source_sha256` verified against the manifest). Chromium via playwright-cli 0.1.15, 18-character
probe at 21px, font synthesis disabled, `opsz` held constant for the weight readings.

### The weight axis

|-|-|-|
| cut | w500 − w400 | off by |
| `wght 400..500` — shipped | 3.812 px | pinned value |
| `wght 300..600` — wider | 3.843 px | 0.031 |
| `wght 400..499` | 3.813 px | 0.000 |
| `wght 400..498` | 3.766 px | 0.047 |
| `wght 400..497` | 3.719 px | 0.094 |
| `wght 400..494` | 3.578 px | 0.234 |
| `wght 400..450` | 1.938 px | 1.875 |
| `wght` pinned | 0.000 px | 3.812 |

Narrowing increments are exact multiples of 3/64 px (0.046875), so the detection edge is a step
rather than a slope: at a 0.05 px tolerance, an integer narrowing of three weight units is caught
and one or two is not. Run-to-run spread on the planning host was 0.001 px, same host and browser.

### The optical-size axis

|-|-|-|
| cut | at `opsz 6` | at `opsz 72` |
| `opsz 6..72` — shipped | 194.406 px | 165.453 px |
| `opsz 6..71.5` | — | 165.313 px (0.141 off) |
| `opsz 6..71` | — | 165.219 px (0.234 off) |
| `opsz 17..40` | 154.141 px (40.265 off) | 156.641 px (8.812 off) |

The page sets no serif text at 6 or 72. A browser can request a coordinate the page never uses,
which is why a range unreadable at the floor is still measurable.

### The file

`fvar` is 84 bytes with both axes, 56 with `opsz` dropped, absent with both pinned. The length
pins neither which axes nor how many: `instanceSize` is `4 + 4·axisCount`, or `6 + 4·axisCount`
with the optional `postScriptNameID`, so two axes with two instances (16+40+2·14) and one axis
with six (16+20+6·8) are both 84 and both spec-legal.

The subset byte count is **not** reproducible: 79,784 on fontTools 4.60.1, then 79,948 and 79,648
on two runs of 4.63.0. Gate on the budget bound, never the count.

The documented subset flags already preserve `fvar`, `gvar`, `avar`, `HVAR` and `STAT` — the
variations were lost to the `instancer` step, not the subsetter, so no new flag is needed.

### The rendered page

Measured at 1440x900 on the pinned fixture, after clicking `button.binder__header`:

|-|-|-|-|
| size | hook | text | chars |
| 21 w500 ls −0.2px | **none** (`.shell__word`) | "karta" | 5 |
| 17 w400 | **none** (`.rail__name`) | "Fixture binder for the…" | 52 |
| 20 w400 | `data-kw-item-title` | "Fixture work item" | 17 |
| 24 w400 | `data-kw-band-sentence` | "start watch-fidelity-…" | 63 |
| 25 w400 | `data-kw-wave-step-numeral` | **"1"** | **1** |
| 40 w400 ls −0.8px | `data-kw-binder-heading` | "Fixture binder…" | 52 |

- **At rest only four sizes exist** — 17, 21, 24, 40. The 20px card title and 25px wave numeral
  appear only after the click.
- **One element per tuple.** Not twenty-seven card titles — that number is from the design export
  and does not describe the fixture.
- **Two of six carry no `data-kw` hook.**
- **The wave numeral is one character.**

Confounds, measured: a probe that does not carry the element's own `letter-spacing` is off by
10.078 px at the 40px headline; one that does not carry its `weight` is off by 3.875 px at the
21px wordmark. The `opsz` signals at those sizes are 13.172 px and 0.938 px — so at 21px the
weight confound alone is four times the signal.

## Decisions the harness has to make

Each of these was surfaced by a review round, and each is a place where a plausible
implementation is wrong. They are listed as decisions rather than as requirements because the
right answer depends on what the built thing measures.

1. **Element selection needs two sets, not one.** Measuring whatever computes to `--serif` means
   an element turned to sans *leaves the set* instead of failing. Enumerating a named inventory
   means an element that *became* serif is never looked at. Both were implemented, one round
   apart, and each broke the other. Two independently built sets required equal is the closed
   form — but note it has no lower bound: both can shrink together, so an expected manifest with
   exact counts is needed on top.
2. **A synthetic probe cannot see the page's CSS.** If all three measurements are injected
   probes, `font-optical-sizing: none` on the real element passes. One of the three has to be the
   live element as the page draws it.
3. **Measure the text's advance, not the element's box.** A block element's box is its
   container's width. And the 40px heading at 52 characters **wraps** at 1440, so a single
   bounding rect is not the advance — sum the line rects, or measure unwrapped.
4. **No absolute pixel floor works.** The page carries a one-character numeral and a
   sixty-three-character sentence. At 25px the numeral's `opsz` difference is around 0.146 px,
   under the 0.3 px floor three drafts of the binder carried — a floor that fails a *correct*
   build. Any bar must be per element and scaled to its own text.
5. **Per-element text does not prove glyph coverage.** A missing character falls back in all
   three measurements equally, so equality still holds. Coverage stays what
   `serve_status.py:115` says it is — a build-step guarantee no oracle checks. Closing it needs a
   `cmap` read, which is available here because this script sits outside the hermetic floor.
6. **A baseline the run can regenerate cannot fail.** If the expectation is re-recorded from the
   changed behaviour, both move together. Baselines must be committed artifacts, re-recorded only
   as a deliberate act that shows as a diff.
7. **Non-discriminating elements must fail, not be reported.** An element whose correct and
   flattened baselines do not separate has no evidence behind it. Reporting it while passing the
   run is how a gate acquires elements it cannot fail on.
8. **Tolerances must be pinned, not derived loosely.** "Within the recorded spread" is vacuous if
   the spread came from one sample, and permissive if it came from a noisy run. Pin the sample
   count, the spread statistic, and the margin formula.
9. **Metrics are environment-bound.** 0.05 px was calibrated on one host and one Chromium.
   Record the browser build and refuse to compare across engines; consider OS, locale and device
   pixel ratio too.
10. **Two repo changes fall out of the measurement.** `.shell__word` and `.rail__name` need
    hooks, and the fixture needs a second element in one tuple — without it, "measure every
    element rather than one representative" is a requirement no run can distinguish and a control
    no mutation can fail.
11. **Width cannot see a missing `gvar`, so do not let it stand in for "the variations work."**
    `gvar` carries the outline deltas; `HVAR` carries the advance-width deltas. Drop `gvar` alone
    and the letterforms freeze while the advances keep moving — so a width probe reports a
    healthy weight separation for a font whose glyphs never change shape. Measured on a
    deliberately broken cut: identical **4.828 px** width delta between weight 400 and 500 for
    both the correct and the `gvar`-less file, while ink coverage at 200px on "Hamb" moved 5,234
    sub-pixels on the correct one and **21** on the broken one. Two consequences. The binder now
    checks the delta tables' presence at the floor (they are directory entries, so no Brotli is
    needed) — that is where this belongs, not here. And any harness claim of the form "the axis
    varies" that rests on advances alone is unsound: to witness outline variation, measure ink or
    compare rendered pixels, not width.

## Two gaps the binder leaves open that this work also closes

Neither is about optical sizing, and both were found in review of the cut-down binder. They are
cheap to close here and impossible to close at the floor, so they belong in the same work.

A third gap of the same family — whether the variation *delta* tables survived the cut — turned
out to be closable at the floor after all, because `gvar`, `HVAR` and `avar` are directory
entries rather than compressed content. It moved into the binder and is **not** this work's to
close; see decision 11 for why it could never have been closed here.

**Decodability.** Every check in the binder reads filenames, manifest hashes, table-directory
metadata, CSS URLs and byte counts. None decompresses the face or loads it. Bytes with a
plausible WOFF2 header and directory entries of the recorded shape pass everything while being an
unusable font. A browser that renders the page settles it in one request; so does a `fontTools`
open. Both are outside the floor's dependency rule and inside this script's.

**Glyph coverage.** Named in the binder's own `verification_honesty` as of review round 17 — it
is disclosed there and closed here. `serve_status.py:115` records that coverage is a guarantee of the build step
and is never read back out of a woff2, because that needs a font library. Nothing checks it, and
the binder replaces a font — exactly when coverage can regress. Note the trap: measuring each
element on its own text does **not** close this, because a missing character falls back in all
three measurements equally and the equality still holds. It needs a `cmap` read against the code
points the fixture actually renders in the serif role.

## Why this is work and not a binder

An earlier draft carried this as binder items and went through thirteen review rounds without
converging. The shape of the failure is worth recording, because it is not obvious from any single
round.

Rounds 3 through 9 argued one claim down four times: the binder said the face carries certain axis
ranges, then that the axes vary, then that they cover the declared range, and finally what the
measurement actually shows. Each round I reached for a bigger check instead of a smaller claim.
**The lesson is cheap: when a reviewer says an assertion claims more than it proves, shrink the
claim first.**

Rounds 10 through 13 were a different failure. The findings stopped being overclaims and became
implementation decisions — the ten above. Rounds 11 and 12 each broke on the previous round's
fix, and the split proposed at round 13 deleted six rounds of endpoint work while drawing a
unanimous four-provider block. A prose specification of a measurement harness has no natural end,
because every detail pinned reveals another that interacts with it.

The two items that did land — the WOFF2 directory reader and the font itself — were clean for
five rounds. The difference is that they describe artifacts whose properties can be known before
building. A harness's properties cannot: they are discovered by running it against the real page.

Build it, measure what it finds, and let the numbers it produces define the gate.
