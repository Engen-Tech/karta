---
name: go-htmx
description: Go + htmx server-side do's and don'ts (partials, HX-Request, redirects, htmx-config)
match: ["htmx", "htmx.org", "github.com/a-h/templ"]
---
## Do
- Vendor htmx as a static file served by the app, loaded via `<script defer>`; no CDN.
- Structure templates as base / pages / named partials; render a named fragment for htmx requests, the full page otherwise — same URL, same handler.
- Detect htmx in one helper: `r.Header.Get("HX-Request") == "true"`; branch full-page vs partial only through it.
- Redirect through one helper: htmx request → `204` + `HX-Redirect` (full reload); else normal `http.Redirect` 3xx. Prefer `HX-Redirect` over `HX-Location` — HX-Location's follow-up fetch always sends `HX-Request: true`, corrupting partial/full branching.
- Pin htmx-config (htmx 2.x keys) in the base layout meta tag: `historyRestoreAsHxRequest: false` (default true; mandatory when HX-Request gates partials), `historyCacheSize: 0`, `disableInheritance: true`, `includeIndicatorStyles: false`, explicit `responseHandling` (204 no-swap; 422 swap; `[45]..` swap into body; `...` swap), and a request `timeout`.
- Use Go 1.22+ routing (`GET /{$}`, method-prefixed patterns) and `http.FileServerFS` over embedded assets; render templates to a buffer before writing status + body.

## Don't
- Don't answer an htmx request with a bare 3xx when a redirect is intended — htmx follows it invisibly and swaps the destination body into the target.
- Don't serve a fragment to a non-htmx request: deep links, shared URLs, history-restore misses get a full page.
- Don't leave htmx 2.x's default error handling in place — it silently drops 4xx/5xx bodies (console-only).
- Don't rely on hx-* attribute inheritance; declare attributes on the element.
- Don't read `r.URL` as the user-visible URL during an htmx request; use `HX-Current-URL` with an `r.URL` fallback.
- Don't carry the config pins across an htmx major upgrade — htmx 4 drops/renames several 2.x keys and flips the error-body default; re-derive from its docs.

## Patterns
- One renderer serves both full pages and fragments; the fragment is a named template in the page's template file.
- Validation failure: `422` + the re-rendered form fragment (responseHandling swaps 422 into the target).
- Matching: vendored htmx leaves no manifest trace — auto-match needs `htmx.org` (package.json) or templ (go.mod); vendored projects copy this pack to `.karta/sme/go-htmx.md` with `always: true` (overlay wins by name). Via templ alone, rules bite only htmx diffs.

## Review checklist
- [ ] htmx.1 — Every route that can serve an htmx fragment serves a full page for the same URL when `HX-Request` is absent.
- [ ] htmx.2 — Every response whose body varies on `HX-Request` carries `Vary: HX-Request`.
- [ ] htmx.3 — No handler sends a bare 3xx on an htmx-originated request; redirects go through the HX-Redirect/3xx-fallback helper.
- [ ] htmx.4 — A diff adding or changing a branch on `HX-Request` ships with `historyRestoreAsHxRequest: false` pinned in the base layout htmx-config.
- [ ] htmx.5 — A diff introducing htmx or touching htmx-config declares explicit `responseHandling` — 4xx/5xx visible (swap into body), 422 swap into target.
- [ ] htmx.6 — An htmx `<script>` the diff adds or changes is vendored and `defer`-loaded; no CDN htmx in changed templates.
- [ ] htmx.7 — Changed templates declare hx-* attributes on the element itself — no reliance on attribute inheritance.
