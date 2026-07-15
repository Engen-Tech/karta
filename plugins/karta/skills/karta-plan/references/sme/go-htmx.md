---
name: go-htmx
description: Go + htmx server-side do's and don'ts (partials, HX-Request, redirects, htmx-config)
match: ["htmx", "htmx.org", "github.com/a-h/templ"]
---
## Do
- Vendor htmx as a static file served by the app and load it with `<script defer>`; no CDN.
- Structure templates as base / pages / partials (fragments); give every template an explicit name; render a named fragment for htmx requests and the full page otherwise — same URL, same handler.
- Detect htmx in one helper: `r.Header.Get("HX-Request") == "true"`; branch full-page vs partial only through it.
- Redirect through one helper: htmx request → `204` + `HX-Redirect` (full reload); otherwise normal `http.Redirect` 3xx. Prefer `HX-Redirect` over `HX-Location` — HX-Location's follow-up fetch always sends `HX-Request: true` (not disableable), which corrupts partial/full branching.
- Pin htmx-config in the base layout meta tag: `historyRestoreAsHxRequest: false` (mandatory when HX-Request gates partials — default is true), `historyCacheSize: 0`, `disableInheritance: true`, `includeIndicatorStyles: false`, explicit `responseHandling` (204 no-swap; 422 swap; `[45]..` swap into body; `...` swap), and a request `timeout`.
- Use Go 1.22+ routing (`GET /{$}`, method-prefixed patterns) and `http.FileServerFS` over embedded assets; execute templates into a buffer before writing status + body.

## Don't
- Don't answer an htmx request with a bare 3xx when a redirect is intended — the browser follows it invisibly and htmx swaps the destination body into the target.
- Don't serve a fragment to a non-htmx request: deep links, shared URLs, and history-restore misses must get a full page.
- Don't leave htmx's default error handling in place — default `responseHandling` silently drops 4xx/5xx bodies (console-only).
- Don't rely on hx-* attribute inheritance; declare attributes on the element (inheritance is disabled by config here and off by default in htmx 4).
- Don't read `r.URL` as the user-visible URL during an htmx request; use `HX-Current-URL` with an `r.URL` fallback.

## Patterns
- One renderer serves both full pages and fragments; the fragment is a named template inside the page's template file.
- Validation failure: `422` + the re-rendered form fragment (responseHandling swaps 422 into the target).

## Review checklist
- [ ] htmx.1 — Every route that can serve an htmx fragment serves a full page for the same URL when `HX-Request` is absent.
- [ ] htmx.2 — Every response whose body varies on `HX-Request` carries `Vary: HX-Request`.
- [ ] htmx.3 — No handler sends a bare 3xx on an htmx-originated request; redirects go through the HX-Redirect/3xx-fallback helper.
- [ ] htmx.4 — If any handler branches on `HX-Request`, the base layout pins `historyRestoreAsHxRequest: false` in htmx-config.
- [ ] htmx.5 — htmx-config declares explicit `responseHandling` making 4xx/5xx visible (swap into body) and 422 swap into the target.
- [ ] htmx.6 — htmx is vendored and loaded with `defer`; no CDN `<script>` in changed templates.
- [ ] htmx.7 — Changed templates declare hx-* attributes on the element itself — no reliance on attribute inheritance.
