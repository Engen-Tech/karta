# Watch design fidelity: how to run the comparison

Every visual check in the watch-fidelity binder compares the served Karta Watch page against one committed design reference, at one viewport, in one theme, against one fixed repo state. This page names the four constants so a second person gets the same view a first person got, every time.

## The four constants

- **Design file**: `docs/designs/karta-watch-1440x900-light.html` — a frozen, self-contained capture of the Claude Design export at 1440x900 in light theme. It opens with no network: its fonts and mascot are pointed at the copies this repo already vendors under `skills/karta-status/assets/`, not fetched. See the file's own header comment for its origin design and capture date. It is derived and can go stale against the living design — recapture it from the design source rather than hand-editing it.
- **Theme pin**: `?theme=light`. The served page defaults to dark and the design is light. A run taken without this pin compares two different themes and reports every token as drifted when nothing changed — that happened once already, producing 27 false positives.
- **Fixture**: `docs/designs/fixtures/watch-fidelity-state` — a committed repo root holding one hand-written `.karta/binders/watch-fidelity-fixture-demo.json`. Its slug matches no `karta/<slug>/*` ref anywhere in this repo, so every item in it always derives as pending: the served page always renders the same one binder, one wave, one card, regardless of whatever binder happens to be live in this repo when the check runs.
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

Compare what renders at that URL against `docs/designs/karta-watch-1440x900-light.html`, opened directly in a browser (no server needed — it is self-contained).

## Differences that are meant to stay

Read these as intended, not as defects. Each one is a place the page deliberately does something the design does not, with the reason it does it.

### The heading outline

The design's only headings are its five binder titles, and its script shows one binder section at a time — so a rendered view of the design holds exactly **one** heading, at the top level, and nothing else on it is headed at all: the map's title is a span, the next-action kicker a div, every wave header's label a span, the footer bare text.

The page heads each binder the same way the design does. What it adds is one heading naming the view — the repo whose watch this is, the name the header already prints. It needs that heading because it renders every binder a repo has at once, where the design mock renders one; several binder headlines all sitting at the top level would leave a reader no sense of what contains what.

So a rendered view of either has exactly one top-level heading. The only difference is that the page's binder titles are nested one level beneath its own, instead of being top-level themselves. If a reviewer wants literal parity — every binder headline top-level — that is the knob, and this paragraph is the reason it was not turned.

Nothing the design leaves unheaded is headed here. Those regions keep being named by the landmarks the page already gives them, which is more than the design does: the design exposes no named regions anywhere, and its own map aside carries no accessible name at all.

Where a region's name and a heading inside it would ever be the same words, the rule is that the heading text stays and the region takes its name from that heading with `aria-labelledby`, rather than holding a second copy for a reader to hear twice. No region and heading collide today, so the rule is stated and unexercised.

## What's out of scope here

This runbook and the checks built on it cover one view at one viewport in one theme. The design carries two responsive breakpoints (880px and 640px) and a dark theme that are not built or compared by this binder — recorded as a deliberate deferral, the obvious next binder, not an oversight.

The multi-repo hub landing is a different page with no counterpart in the design, so it is not compared here either. The heading rules above are about the repo view.

## Recapturing the design reference

When the living Claude Design export changes, recapture `docs/designs/karta-watch-1440x900-light.html` rather than hand-editing it: take the export's rendered light-theme markup and styles at 1440x900, point its mascot and font references at the already-vendored copies under `skills/karta-status/assets/`, and update the header comment's capture date. `uv run scripts/validate_plugin.py --self-test` fails the build if the result references an external host or an asset that doesn't resolve inside this repo.
