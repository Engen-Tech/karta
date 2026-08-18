# Watch design fidelity: how to run the comparison

Every visual check in the watch-fidelity binder compares the served Karta Watch page against one committed design reference, at one viewport, in one theme, against one fixed repo state. This page names the four constants so a second person gets the same view a first person got, every time.

## The four constants

- **Design file**: `docs/designs/karta-watch-1440x900-light.html` — a frozen, self-contained capture of the Claude Design export at 1440x900 in light theme. It opens with no network: its fonts and mascot are pointed at the copies this repo already vendors under `skills/karta-status/assets/`, not fetched. See the file's own header comment for its origin design and capture date. It is derived and can go stale against the living design — recapture it from the design source rather than hand-editing it.
- **Theme pin**: `?theme=light`. The served page defaults to dark and the design is light. A run taken without this pin compares two different themes and reports every token as drifted when nothing changed — that happened once already, producing 27 false positives.
- **Fixture**: `docs/designs/fixtures/watch-fidelity-state` — a committed repo root holding one hand-written `.karta/binders/watch-fidelity-fixture-demo.json`. Its slug matches no `karta/<slug>/*` ref anywhere in this repo, so every item in it always derives as pending: the shape the page derives — one binder, one wave, one card — is fixed regardless of whatever binder happens to be live in this repo when the check runs. Fixed is not the same as painted: that binder derives as `next`, and the page opens only the current binder's panel at rest, so the wave and the card are behind one click. See [The command](#the-command) for where the click belongs.
- **Viewport**: 1440x900, matching the design capture.

## The command

Serve the page rooted at the fixture, on a loopback port, with the theme pinned:

```
uv run --script skills/karta-status/scripts/serve_status.py --root docs/designs/fixtures/watch-fidelity-state --port 8765
```

Then open `http://127.0.0.1:8765/?theme=light` at a 1440x900 viewport.

`--root` is the knob that points the page at the fixture — `main()` chdirs there so `.karta/binders` and git both resolve against it. `KARTA_WATCH_STATE_DIR` is not the knob to reach for here: it overrides the multi-repo hub's own store directory (`state.json`, `state.lock`, a token file), not the binder/wave state the page renders. Setting it is still worth doing so a run never touches the real per-user store:

```
KARTA_WATCH_STATE_DIR=$(mktemp -d) uv run --script skills/karta-status/scripts/serve_status.py --root docs/designs/fixtures/watch-fidelity-state --port 8765
```

Compare what renders at that URL against `docs/designs/karta-watch-1440x900-light.html`. **Open the design file directly** — a `file://` path in a browser, or a `file://` `--design-url` if you are driving `capture_view.py`. It is self-contained and needs no server. Do not put it behind `karta-validate`'s `serve_design.py`: that script roots at the design file's own parent, and this capture points at `../../skills/karta-status/assets/`, so all eight font faces and the mascot 404 and the page silently falls back to different faces with no error anywhere.

**Two clicks are part of the run.** The fixture binder derives as `next`, and the page opens only the current binder's panel at rest — so the view at rest is the binder head alone. Take the head-level readings there, then:

1. click the binder header to open the panel, and take the work-item card readings — the card's corner, its title, its state label, its description;
2. expand one card's disclosure, and take the detail-panel readings.

Both clicks are protocol, not improvisation. `design-fidelity-gate`'s own assertion 8 names the work-item cards and the per-card disclosure panels, so a run that never clicks cannot satisfy the item it belongs to.

## Differences that are meant to stay

Read these as intended, not as defects. Each one is a place the page deliberately does something the design does not, with the reason it does it.

There are **three**, and the list is closed. It closed when the binder was planned; running the comparison does not add to it. A difference a run turns up that is on neither this list nor the [rule-out list](#the-five-differences-ruled-out-at-plan-time) is an open finding — it belongs in [Findings from the latest run](#findings-from-the-latest-run) as a defect, not up here as an intention. Promoting a fresh difference into this section to make a run pass is the one move this document exists to prevent.

### 1. The heading outline

The design's only headings are its five binder titles, and its script shows one binder section at a time — so a rendered view of the design holds exactly **one** heading, at the top level, and nothing else on it is headed at all: the map's title is a span, the next-action kicker a div, every wave header's label a span, the footer bare text.

The page heads each binder the same way the design does. What it adds is one heading naming the view — the repo whose watch this is, the name the header already prints. It needs that heading because it renders every binder a repo has at once, where the design mock renders one; several binder headlines all sitting at the top level would leave a reader no sense of what contains what.

So a rendered view of either has exactly one top-level heading. The only difference is that the page's binder titles are nested one level beneath its own, instead of being top-level themselves. If a reviewer wants literal parity — every binder headline top-level — that is the knob, and this paragraph is the reason it was not turned.

Nothing the design leaves unheaded is headed here. Those regions keep being named by the landmarks the page already gives them, which is more than the design does: the design exposes no named regions anywhere, and its own map aside carries no accessible name at all.

Where a region's name and a heading inside it would ever be the same words, the rule is that the heading text stays and the region takes its name from that heading with `aria-labelledby`, rather than holding a second copy for a reader to hear twice. No region and heading collide today, so the rule is stated and unexercised.

### 2. The card leads with its state; the detail stays behind a disclosure

On both sides, a work-item card leads with its state as a capitalised label, carries a compact meta line with the item's slug and its size, and keeps the check command, the touched file paths and the git ref behind a per-card disclosure.

The design is not uniform about that last part, which is where the difference lives. Its touched-path and git-ref rows read at rest on several cards — but every one of those cards is in its notifications panel, and a card in its Windows-parity panel shows neither. So the difference is against the panel that shows them: there, the design reads those rows without a click and this page keeps them behind the disclosure. Two facets of that decision are worth stating on their own.

#### The halted card opens without a click

The one card this page does open at rest is a halted one. The design draws the detail of its one halted card inline, bordered with the halt token; every other card it draws keeps its disclosure shut — 24 of its 27. This page had no card open in any state, so it now **defaults** a halted card's disclosure to open: a halt is the one thing here nobody should have to click to read.

It is a default, not a force, and the distinction is the point. The disclosure stays a real button that reports whether it is expanded. A reader who collapses a halted card sees it stay collapsed, including across a refresh that replaces the whole state object, because the default is only consulted for a card the reader has not decided about. Expanding or collapsing one card still leaves every other card where it was.

So a comparison at rest will find one halted card open on the page and one halted card open in the design, and every other card shut on both sides. Nothing to report — and note that the pinned fixture cannot show this either way, because every item in it derives as pending and no halted card appears at all. This facet is proven in the unit check of the item that added it, which is where a state-dependent claim belongs.

#### The sole card in a panel stays shut

The design draws two other detail grids inline: in two of its panels, the panel's only card shows its detail without a click. This page does not copy that, deliberately.

That inline detail is a property of the drawn mock — a panel that happens to hold one card — not a rule the export states anywhere, and every other card outside those two and the halted one keeps its disclosure shut. So the page gains no sole-card rule. A comparison run against a fixture whose panel holds one card will see the design's card open and the page's card shut; that is this decision, weighed and turned down, not an oversight.

The check type is the other thing to read carefully here. It is not in the meta line on either side: the design writes it into the disclosure button's own label, and so does this page, where the button is the whole card row.

### 3. The phase spine is gone; a slim delivery frame stays

The design's main column runs from the dark next-action band straight into the binder's own bordered panel. There is no wrapper around that panel, no phase grouping repeated inside it, and the column declares no left border at all. The panel states no width, no max-width and no margin of its own, so a card inside it starts one border and one pad in from the column's edge — one container level, and the cards get the rest of the width.

This page used to charge three. A "Delivery" panel with a 30px pad, then a phase row with a 50px gutter carrying a spine, then the binder card: four left edges stacked up before a work item began, against the design's two.

**The spine is gone.** The map down the left already groups every binder under the same four phases — Delivered, Now, Next, Later — so a spine running down the panel said it a second time, and charged every card the gutter's indent to say it. Its icon and its vertical rule are both gone, and so is the row wrapper that only existed to sit the gutter beside the content. A card now sits one box shallower than it did.

**The wrapper stays, as a frame.** It is not the same case as the spine. It carries what the design was never asked to model: which repository this watch is of, and how many binders it holds. So instead of being deleted it is held to a frame — a 1px border and a 14px pad, 15px a side, against a budget of 16. The stylesheet takes both numbers from named constants in `serve_status.py`, and the self-test reads those same two, so re-pitching the frame is one edit and drifting the sheet off it fails the build.

The budget is checked on more than the two numbers, because there are cheaper ways to steal the same width. A shorthand is read on its horizontal step, so a three-value padding's bottom cannot stand in for its left and right. A contributor written as a `var()`, `calc()`, `clamp()`, `rem` or percentage fails rather than being guessed at. And a `width`, `max-width`, offset or `transform` on the frame is refused outright — the page's column cap already exists a level up, on the shared wrapper, which is where a cap belongs.

So a comparison will find the frame as one container around a binder card that the design does not have, and **the frame is the only container this difference books.** Reported as a difference it is this decision; reported as *indent* it is a defect, because the frame is budgeted not to read as one.

If a run also finds the phase grouping — `section.panel` > `div.phase` > `div.phase__binders` > `div.binder`, three levels where the design has the frame's one — that is **not** covered here. It is open finding 1 below, and it blocks. The real count today is three containers, not one; this difference accounts for one of them. Counting them together is exactly how a run has already cleared this defect once.

What none of this settles is whether the panel stops **looking** indented and the cards visibly regain their width. The checks behind it are pure Python with no browser: they prove the arithmetic and the shape of the nesting, not the painted result. That is what the run described at the top of this file is for.

## The five differences ruled out at plan time

These five were weighed when the binder was planned and are not defects. The comparison has no way to be told about them, so whenever the served state actually exhibits one, the run will report it; when it does, it is expected, and it is not new. Two of the five will not arise against the pinned fixture at all — the fixture's next action does carry a runnable command, so both sides draw a copy control and match, and token drift is a finding of no-difference rather than a difference.

- **Five seeded binder panels against this repo's live binders.** The design draws five, four of them hidden, with its own script showing one at a time. The page renders whatever binders the served repo actually has.
- **Single-item waves.** The real run this page was built against produced waves of one item each. There is nothing to pair, so nothing pairs.
- **No copy control on the next action.** The design's next-action state has a runnable command to copy. A state with no runnable command gets no copy button.
- **Token drift.** There is none. The export defines the same 27 custom-property names in both its palette blocks, and every one matched the page exactly. If a run reports token drift, check the theme pin before believing it.
- **The lane glyph.** The design builds its lane marks from small radiused bar boxes; this page draws an SVG icon with painted legend swatches. That divergence was booked in the page's own source before this binder existed.

## What the pinned view cannot show

The fixture's slug matches no `karta/<slug>/*` ref, which is what makes it stable — and also means every item in it derives as **pending**. So nothing state-dependent is observable in this comparison: no halted card, no in-flight ring, no running strip, no part-filled progress bar. A binder that is not the current one also renders with its panel collapsed, because the page opens the current binder's panel at rest and no other, so the compared view shows the binder head and no work-item cards until something is clicked.

None of that is a finding, and no assertion in this comparison may rest on **those state-dependent treatments** — the halted card, the in-flight ring, the running strip, the part-filled progress bar. They are checked where they can be, in the unit checks of the items that added them.

This rule does **not** sweep in the work-item cards. Those are one documented click away, the run takes them there, and the gate's own assertions require them.

## Findings from the latest run

**Run of 2026-08-18**, taken exactly as described at the top of this file: the committed design file opened directly at 1440x900, the page served against the committed fixture with `?theme=light`, no network on either side. The card-level and detail-level readings below come from the two clicks the procedure describes, not from the view at rest.

**What matched.** The palette (page ground, header ground, band ground and progress track all identical), the next-action band's 16px corner and its 8px command chip and Copy button, the binder headline at 40px Newsreader with the same -0.8px tracking, the binder card's 1px edge, the work-item card's 12px corner and its 20px Newsreader title, and the card's 10px mono state label. The binder headline region matched the design's composition: a mono eyebrow above, the slug at the far right, the headline alone on its own full-width line, and no headline text inside the panel toggle.

**All three intended differences were still on the page** and still looked the way this document describes them. None had quietly reverted.

**Open findings.** These are differences the run turned up that are on neither closed list. They are recorded here as defects, not adopted as intentions.

1. **The main panel still groups binders under Now / Next / Later.** *Blocking.* The design's main column runs from the next-action band straight into the binder's own panel — head, then wave rows, then cards. This page nests the binder two boxes deeper, inside a phase group, and renders the two empty phase groups as headed rows reading "0 binders" and "— no binders" above and below the only binder there is. The design has no phase grouping anywhere in its main column.

   This is the *grouping*, not the spine. Difference 3 removed the spine and only the spine; `PHASE_DEFS`, `phases()`, `phase__head`, `phase__binders` and `data-kw-phase` all remain. It is confirmed wrong: the right-hand panel should never group by phase — it is always the detailed view of whichever binder is selected in the map. Fixing it is a change to how the panel is selected and driven, which is a binder of its own and is out of this one's scope. It is filed as a follow-up, and it is why this comparison does not pass.

   **The binder card's corner was recorded as matching, and it was not.** `.binder` declared a 16px radius and no clipping, and its last child `.bmeta` — the DEFAULT / INTEGRATION / PACKS strip — carried a solid `--surface-2` fill with square corners. Two different opaque fills, so the strip painted straight through the parent's curve and flattened it. It renders at rest, outside the collapse gate, so it was in the compared view. The design answers this at export line 458, where its own counterpart footer carries `border-radius:0 0 15px 15px` — the same value the page later derived as the panel radius less its border. The repair landed in `81f77c2`, after this binder was archived, which is why the record and the code now disagree about this one line. **The check to run next time is not "does the container declare a radius?" but "does any opaque last child sit square inside it?"**

   **This finding was nearly lost, and how is worth recording.** The first comparison pass of this run folded the phase grouping into difference 3 — it read the wrapper and the grouping as one thing, called it the panel wrapper "around the phase/binder chain", found it on the list, and returned a pass. They are not one thing. The wrapper is `section.panel`; the grouping is three `div.phase` blocks with their own heads and counts. Difference 3 names only the first. A comparison that reads them together will clear this defect every time, so check them apart: the question is not "is there a wrapper?" but "how many box levels sit between the wrapper and the binder card, and does the design have any of them?" On this page it is `section.panel` > `div.phase` > `div.phase__binders` > `div.binder`; in the design it is `section` > the binder panel, directly. That is the check to run next time.

2. **The binder summary is set much smaller and harder than the design's.** The design's summary is the panel's lede: 16.5px sans in a muted ink, held to a readable measure. The page sets it at 13px in full-strength ink across the panel's whole width. Size, colour and measure all differ, and it is the largest typographic gap in the binder head. The type-fidelity item did not take this element on — it scoped itself to the card title, card body, header controls and the map — so this is new work, not a leftover.

3. **The progress track has square ends where the design's is a pill.** The design's bar is fully rounded; the page's is a plain rectangle. The container-shape item deliberately adopted four rectangular radii and left the existing round set alone, so the bar was never in its scope, and its check now asserts that round set exactly — meaning this is a change to make deliberately, with the check moved in the same edit, not a value to nudge.

4. **Three smaller type gaps in the binder head.** The eyebrow is 9px against the design's 11px, the slug 10px against 11px, and the work-item card's description 11.5px against 13.5px. The page also gives the slug a tinted ground and a branch icon where the design leaves it as bare text.

5. **The binder card's ground is inverted against the design's.** In the design the binder panel is white on the warm page ground, so it advances. Here the delivery frame is white and the binder card inside it is warm, so the card recedes. Same two surfaces, opposite assignment.

6. **Live-page controls the mock does not model.** A toggle row sits between the headline and the summary carrying the state icon, the percentage and the caret; the header adds a countdown, a refresh button and an auto-refresh toggle; the map's legend runs to nine rows where the design's runs to seven. These are affordances a live, polling page needs and a static mock was never asked to draw. They are recorded so the next run recognises them, but they have not been weighed as intended differences and must not be read as if they had been.

## What's out of scope here

This runbook and the checks built on it cover one view at one viewport in one theme. The design carries two responsive breakpoints (880px and 640px) and a dark theme that are not built or compared by this binder — recorded as a deliberate deferral, the obvious next binder, not an oversight.

The multi-repo hub landing is a different page with no counterpart in the design, so it is not compared here either. The heading rules above are about the repo view.

## Recapturing the design reference

When the living Claude Design export changes, recapture `docs/designs/karta-watch-1440x900-light.html` rather than hand-editing it: take the export's rendered light-theme markup and styles at 1440x900, point its mascot and font references at the already-vendored copies under `skills/karta-status/assets/`, and update the header comment's capture date. `uv run scripts/validate_plugin.py --self-test` fails the build if the result references an external host or an asset that doesn't resolve inside this repo.
