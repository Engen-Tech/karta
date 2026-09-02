---
name: karta-house-vue
description: "karta's local narrowing of vue: the watch page is a vendored global-build Options-API app, so the SFC/TypeScript rules are replaced by the shape it actually ships"
match: ["vue"]
extends: vue
id_prefix: hvue
exclude_rules: ["vue.1", "vue.2", "vue.3"]
---
## Why this pack exists

The built-in `vue` pack assumes a Single-File-Component project with a build step and
TypeScript. Karta Watch is not that, and cannot become that: it is one Vue app created
with `Vue.createApp`, using the Options API, whose template is a string embedded in
`skills/karta-status/scripts/serve_status.py` and whose runtime is a single vendored
file served same-origin. There is no compiler, so there are no compiler macros; there
is no TypeScript, so there are no typed signatures.

`vue.1` (use `<script setup>`), `vue.2` (typed `defineProps`/`defineEmits`) and `vue.3`
(no `any` in signatures) are therefore excluded rather than left to fail forever. The
rest of the built-in still applies and still enforces — `vue.4` stable `:key`, `vue.5`
no prop mutation, `vue.6` teardown for listeners and timers, `vue.7` sanitized `v-html`,
`vue.8` native inputs over picker dependencies.

The `vue` built-in does not match this repo on its own: `detect_stack.py` reads package
manifests, and karta has none — every script carries inline metadata instead. This pack
is `always: true` so the guidance applies to the watch page regardless.

## Do

- Keep the app one `Vue.createApp` root with an Options API body (`data` / `computed` /
  `methods` / `mounted` / `beforeUnmount`) and a string `template`.
- Keep every browser-facing value flowing from the engine state the server hands over,
  so the page stays a mirror of git rather than a second source of truth.
- Clean up on `beforeUnmount` — the poll timer and any listener added at mount.
- Factor a CSS magnitude that other declarations depend on (a border width, a bar height, a
  radius) into one named module constant and interpolate it everywhere the stylesheet uses
  it, rather than repeating the literal. This delivery collapsed seven such literals into
  constants (`HEADLINE_PX`, `CARD_TITLE_PX`, `HEADER_CONTROL_PX`, `BAR_HEIGHT_PX`,
  `PANEL_BORDER_PX`/`PANEL_PAD_PX`, four `RADIUS_*_PX`), each removing a place the
  stylesheet and its self-test could silently drift apart (seen 2026-08-18, watch-fidelity
  delivery). Ties into hvue.4: a self-test can only assert structure over a derivation,
  never a literal, when the source is a named constant in the first place.
- When a self-test asserts a computed box metric (width, height, offset) on an element,
  check it against a rendered DOM — a real or headless browser — rather than static source
  inspection alone. A `display:inline` element silently ignores an assigned width, and that
  exact bug survived four rounds of inspection-only review before one worker measured it
  live (seen 2026-08-18, watch-fidelity delivery: `.rail__fill` computed `display:inline`,
  so its bound width measured 0px).

## Don't

- Don't reach for a build step, a bundler, or an SFC to make a change easier. The page
  ships inside a plugin as plain files; a build step would break that.
- Don't add a second front-end dependency. Vue is the one vendored file.

## Review checklist

- [ ] hvue.1 — Replaces the excluded vue.1/vue.2/vue.3: new front-end code matches the shape this page actually ships — a global-build `Vue.createApp` root, Options API, string template, no SFC, no build step, no TypeScript, no compiler macros. A change that only works under a bundler or a `.vue` file fails this rule.
- [ ] hvue.2 — Vue remains the only vendored front-end runtime, served same-origin from the assets route. No CDN reference, no second vendored JS library, and no new front-end dependency — including a DOM-morphing or reactivity helper.
- [ ] hvue.3 — Every engine value inlined into the page passes through `_inert_json`, and no state-derived string is bound with `v-html`. Complements vue.7 for the one place this page interpolates server data.
- [ ] hvue.4 — Assertions in `serve_status.py --self-test` that cover rendered output check for structure — a class, an attribute, an element relationship — not an exact CSS declaration or a literal markup string. A restyle must be able to pass without deleting coverage.
