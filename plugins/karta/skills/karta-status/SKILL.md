---
name: karta-status
model: haiku
description: >-
  Show what's next in a karta run — where you are and the single next action, at binder and work-item level, derived fresh from git every time and never stored. Read-only. When someone wants to SEE or watch status, it opens the live Karta Watch browser page by default — ephemeral for this session unless the repo is opted into the persistent multi-repo hub; a one-shot terminal map is the headless fallback. Trigger phrases: "what's next", "karta status", "where am I in this binder", "show me the karta status", "karta watch", "turn off karta watch", "karta-status".
---

karta-status answers one question: **what do I do next?** It reads every binder in
`.karta/binders/` and the karta git refs, recomputes the state, and surfaces a "you are here" view
with one copy-pasteable next action — **by default the live Karta Watch browser page**, or a
one-shot terminal map when there's no browser. It writes nothing and changes nothing.

## How it derives state

All state is git-native and recomputed on every call — there is no stored cursor:

- **Binder order** is a topo sort over the optional cross-binder `after` edge (see
  [karta-plan's binder reference](../karta-plan/references/binder-reference.md)). The order is
  *derived*, never written anywhere. A dangling `after` is surfaced as a warning; a cycle is an
  error. An `after` naming a **delivered** binder — one karta-deliver archived to
  `.karta/binders/archive/` — resolves as satisfied, not dangling.
- **Archived binders** (`.karta/binders/archive/`) are delivered history: the terminal map and
  the session-start summary never list them; the Karta Watch page shows them under its Delivered
  phase.
- **Binder status** — `merged` (every item's `done` ref is an ancestor of the default branch),
  `in_flight` (integration branch exists, or some items merged), or `not_started`.
- **Work-item frontier** (for the in-flight binder) — `done` / `built` / `failed` / `building` /
  `ready` / `blocked`, from `depends_on` and the `refs/karta/<slug>/item-<id>/*` refs.

## Two modes, two gates

Karta Watch runs in one of two modes, and two explicit gates decide which:

1. **You ask for status at all.** Nothing serves until someone asks to see it.
2. **You opt a repo into persistence.** Without that second, per-repo opt-in, the page is
   ephemeral — it lives and dies with the session that started it, exactly today's behavior.

Ephemeral is the default and is unchanged. The persistent hub is a deliberate, per-repo choice.

## Ephemeral mode (the default) — open the live page

When someone asks for karta status, "what's next", or to **see / watch / show / look at** where
they are, **start Karta Watch, the live browser page.** That is the point of this skill, so it is
the default — not an extra someone has to ask for:

  `uv run --script skills/karta-status/scripts/serve_status.py --root <repo> --port 8765`

It is a **long-running** server (it re-derives state from git on every poll), so start it as a
**persistent/managed process** — a bare `&` or `nohup` is often reaped by the agent runtime before
it binds, so use the runtime's managed background-session mechanism — then **confirm it is serving
and hand the user the working URL**, not just the launch command: `http://127.0.0.1:8765/` (forward
the port on a remote host). The page shows the binder sequence as a card column ending at the
`★ main` integration star, the current binder's work items grouped by state (each with its oracle
and a click-to-expand assertion + command), and the next action as a copy banner; it polls
`/state.json` to stay live. `?theme=light|dark` forces a theme; `--key <token>` gates it behind
`?key=`. It is **self-contained** (vendored Vue, system fonts, no CDN, no build step) and
**zero-dependency** stdlib Python; `serve_status.py --self-test` checks its invariants.

## Persistent mode — the Karta Watch hub (per-repo opt-in)

When someone asks to keep the watch page around — "opt this repo into karta watch", "make karta
watch persistent" — flip the second gate:

  `uv run --script skills/karta-status/scripts/serve_status.py --opt-in`

From then on, one per-user **hub** serves every opted-in repo at a stable local URL, and ordinary
karta activity revives it automatically. The lifecycle flags, all on the same script:

- `--opt-in [PATH_OR_SLUG]` — opt a repo into the persistent watch (default: the current repo).
- `--opt-out [PATH_OR_SLUG]` — turn it off; accepts a path or slug so a moved or deleted repo's
  entry can be cleared from anywhere. When the user says **"turn off karta watch"**, run this.
- `--ensure` — idempotently revive the hub: no-op when healthy, detached respawn when dead or
  outdated, and it **fails open** (one plain line, exit 0) so it never blocks karta work. Karta
  fires this automatically on every plan, build, deliver, and status touch; the flag exists for
  running it by hand, e.g. after a reboot.
- `--hub` — serve the hub in the foreground (mainly for testing; `--ensure` is the normal path).

Two rules hold on every hub route — landing page, per-repo pages, state feeds, `/identity`, and
assets alike: the **token is required everywhere** (`?key=<token>`, generated per user, stored
0600 in the per-user state dir), and the **bind is hardcoded to loopback** (`127.0.0.1`, IPv4
only) with no interface option — the page is never reachable off the machine. In an opted-in
repo the session-start banner carries the full working URL with port and key.

Operator guide — opt-in, the stable URL, the off switch, the reboot gap, and the security model:
`docs/how-to/karta-watch.md` in the karta repo.

## One-shot text — when there's no browser

When the caller wants a quick textual answer, or there is no browser (CI, headless, a script), run
the engine directly instead of the page:

- `uv run --script skills/karta-status/scripts/karta_next.py` — the route + frontier + `▶ next`.
- `uv run --script skills/karta-status/scripts/karta_next.py --json` — the full state (the page consumes this).
- `uv run --script skills/karta-status/scripts/karta_next.py --footer --binder <slug>` — the one-line run-end nudge.

This skill is read-only and stack-agnostic. It never starts a build, never merges, never writes a
binder. It only tells you where you are and what is next. The one exception to "starts nothing" is
deliberate: asking for status also fires the fail-open `--ensure` above, so a hub you opted into
revives on the touches you already make.
