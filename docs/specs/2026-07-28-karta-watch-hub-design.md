# Karta Watch hub — non-ephemeral status, design (V1)

> **Status: settled V1 design, roundtabled at brainstorm stage.** A five-provider
> roundtable-deliberate panel (DeepSeek, GLM, Kimi, MiniMax, Qwen) reviewed the approach options
> on 2026-07-28 and unanimously chose the single-hub architecture, with amendments folded in
> below. This spec is the input to a karta-plan binder; that binder gets its own recorded panel
> at commit time per the roundtable edict.

## Goal

One stable local URL per user per machine that shows every opted-in karta repo at a glance —
status chips, binder counts, the next action — with drill-down into each repo's full live
Karta Watch page. Available any time without asking an agent to start anything, bounded by two
gates:

- **Gate 1 — you ask for status at all.** Unchanged. karta-status stays read-only and on-demand;
  with no opt-in anywhere, everything behaves exactly as today (session-ephemeral server).
- **Gate 2 — ephemeral or persistent.** Persistence is an explicit per-repo opt-in, stored
  per-person, loudly visible while on, and trivially reversible.

## Founding constraints

1. **Product feature, not machine infra.** Ships in the karta plugin, works the same on
   Linux/macOS/Windows, zero-dependency stdlib Python + vendored assets, no build step, no OS
   service files (no systemd/launchd/Windows services).
2. **Off-machine access is prohibited.** Loopback bind is hardcoded. No interface knob, no
   exposure option, nothing to misconfigure. Enterprise governance is the reason; simplicity is
   the bonus.
3. **Ephemeral by default.** The persistent hub exists only after someone opts a repo in.
4. **Opt-in is per-repo in granularity, per-person in storage.** It lives in the user's own
   state file, never inside the repo — a committed opt-in would force one person's preference on
   every collaborator, which is rejected. Nothing karta-persistent is written in-repo.
5. **Opt-in gates both visibility and revival.** A repo does not appear on the hub at all until
   opted in. One flag, one meaning: "this repo is on my persistent dashboard."
6. **Read-only surface.** The hub serves GET only. No admin endpoints, no web-side mutation.
   Opt-in/opt-out are agent/CLI actions.

## The decisions (from the brainstorm + panel)

1. **One hub process, N repo engines.** A single detached daemon serves a landing page and
   mounts each opted-in repo's existing Karta Watch page at `/r/<slug>/`, backed by the same
   per-repo state engine used today. Rejected: per-repo daemons + index (port sprawl, N
   lifecycles, "one URL" becomes a redirect page) and static snapshots (loses the live poll that
   is the product's point).
2. **Per-user derived port, not fixed 8765.** Two users on one machine cannot share a port.
   The port derives deterministically from the user — `8765 + (uid % 1000)` on POSIX,
   `8765 + (stable hash of username % 1000)` on Windows — is recorded in the per-user state
   file, and every surfaced message prints the full working URL. A busy candidate steps to the
   next derived candidate and records what it got. Stable in practice, never hardcoded.
3. **Token required on every route.** Loopback alone does not stop other local users or a
   hostile web page reaching 127.0.0.1 through the user's own browser (DNS rebinding). The hub
   auto-generates a random token, stores it 0600 in the per-user state dir, and requires
   `?key=<token>` on every endpoint, `/identity` included. A Host-header allowlist
   (`127.0.0.1:<port>`, `localhost:<port>`) rejects rebound names. The existing `--key`
   mechanism becomes mandatory in hub mode.
4. **Version-skew handling.** `/identity` reports plugin version + PID. The revive path
   compares versions and respawns on mismatch. The hub also checks its own script file's mtime
   about once a minute and exits when the plugin updated under it; the next touch revives the
   new version. The revive path never kills a process it cannot confirm as ours via the
   token-authenticated `/identity` — a foreign process on our candidate port means we step to
   the next candidate, never a kill.
5. **Bind is the mutex.** Concurrent revivals resolve by the OS: whoever binds wins, the loser
   exits. No lock files.
6. **Honest reboot gap.** No OS services means the URL is dead after reboot until karta runs
   once. Documented plainly, plus a standalone one-liner (`serve_status.py --ensure`) that
   revives the hub from any terminal without an agent session.

## Architecture

### Per-user store

One JSON state file in the platform state dir — `$XDG_STATE_HOME/karta/` (default
`~/.local/state/karta/`) on Linux, `~/Library/Application Support/karta/` on macOS,
`%LOCALAPPDATA%\karta\` on Windows:

- `port` — the derived, recorded port.
- `token` — the hub auth token (file kept 0600).
- `repos` — map keyed by absolute repo root: `{slug, opted_in, last_seen}`. Every karta touch
  upserts `last_seen` (self-registration). `slug` is `<sanitized-basename>-<hash8-of-abspath>` — URL-safe, human-readable
  first (widened from six digest chars to eight, and pinned URL-safe, by the binder's review
  panel).

Writes are atomic (temp file + rename) because concurrent sessions upsert. The roster is state,
not config: regenerable, never committed, never inside a repo.

### Hub server

`serve_status.py --hub`, one script, one self-test (split into a separate entry point only if
the mode balloons during implementation):

- `/` — landing page: opted-in repos as cards (status chip, binder counts, next action),
  linking into each repo.
- `/r/<slug>/` and `/r/<slug>/state.json` — the existing Karta Watch page and its state feed,
  per repo. Slugs resolve only through the store; anything else is 404 (no path traversal).
- `/identity` — plugin version, PID, uptime, roster count. Token-gated like everything else.
- Threading HTTP server. Per-repo git derivation runs with subprocess timeouts and a ~5 s
  per-repo cache, so one wedged repo greys its own card instead of stalling the page.
- A small rotating log in the state dir — a detached daemon with stdio on `/dev/null` is
  otherwise undebuggable.

### Lifecycle — `ensure_hub()`

Idempotent, embedded in the scripts the skills already run (`karta_next.py`,
`serve_status.py`, the plan/deliver helpers) — code paths, not skill prose, per karta doctrine:

1. No opted-in repos → do nothing (gate 2 closed; ephemeral default untouched).
2. Probe `GET /identity?key=…` with a short timeout. Healthy + version match → done.
3. Version mismatch → kill (identity-confirmed PID only), respawn.
4. Nothing there → spawn detached: double-fork/setsid on POSIX, detached-process flags on
   Windows. Bind settles races.
5. Any failure (sandbox denial, no bindable port) → fail open with one plain line in the
   script output; the karta work itself is never blocked.

When the last repo opts out, the next state read tells the hub to exit.

### Opt-in, visibility, off switch

- Opt-in: "make karta watch persistent here" → the agent runs the flip command for that repo.
- While on, every session start (Claude) or karta script run (all platforms) in an opted-in
  repo prints: `Karta Watch: http://127.0.0.1:<port>/?key=… — persistent; say "turn off karta
  watch" to disable.`
- Opt-out flips the flag back; the page shows the disable instruction but never performs it.
- Opted-in repos whose path vanishes grey to "unavailable" — never silently pruned. Non-opted
  roster entries age out 30 days after their `last_seen`.

## Codex

Codex has a hooks surface, but it is trust-gated per user and flagged off on some builds, and
karta's session-start status hook is "Not shipped" on Codex. The Codex sandbox
(workspace-write, network off by default) can deny all three primitives the hub needs: the
state-dir write, the loopback bind, the detached spawn. So:

- **Revival is script-embedded only.** Any karta touch runs a script; the script runs
  `ensure_hub()`. No dependence on hooks or on prose an agent might skip.
- **Sandbox denial fails open** to a one-line nudge: run the `--ensure` one-liner in a
  terminal. Opt-in under sandbox may prompt for escalation — an explicit user confirmation on
  the persistence switch is acceptable, arguably desirable.
- **Visibility rides karta touches**, not session start. A bundled Codex session-start hook is
  a possible later parity upgrade, not V1.

New row for the codex.md parity table:

| Rule | Claude Code | Codex |
|-|-|-|
| Karta Watch hub auto-revives | SessionStart hook + script-embedded ensure | Script-embedded ensure only; sandbox may require the terminal one-liner |

## Errors

- No bindable candidate port → the surfaced message says so plainly; no silent absence.
- Wedged repo (git hangs) → its card greys with an error note; timeouts protect the rest.
- Stale hub after plugin update → self-exit on mtime change + version check on revive.
- Dead roster paths → grey, then prune (non-opted only) after the 30-day age-out.

## Testing

Extend `serve_status.py --self-test`, deterministic and loopback-only:

- Every hub route rejects a missing/wrong token and a disallowed Host header.
- `/identity` contract (version, PID fields).
- Slug traversal attempts 404.
- Cache TTL behavior and wedged-repo isolation (fake engine).
- `ensure_hub()` decision table: no opt-ins / healthy / version skew / dead port / foreign
  process on port (steps, never kills).
- Existing invariants unchanged.

## Rejected along the way

- **Per-repo daemons + index; static snapshots** — see decision 1.
- **Committed (in-repo) opt-in** — forces one person's preference on collaborators.
- **Fixed port 8765** — breaks multi-user machines.
- **Static HTML fallback when the hub cannot start** (panel suggestion) — resilience surface
  V1 does not need.
- **Shell-profile auto-start / "karta ping"** (panel suggestion) — the `--ensure` one-liner
  covers the gap without touching user shell config.
- **OS service files, even optional** — three platform recipes to build and support; revisit
  only if the reboot gap proves painful in practice.
- **Shipping the hub as an MCP server, bundled or separate** — MCP does not solve the actual
  problem: a stdio MCP server dies with its session (the ephemerality being eliminated), and an
  HTTP MCP server needs the same daemon lifecycle the hub already builds, plus a protocol. It
  also serves the wrong consumer — MCP is agent-facing, this feature's consumer is a human in a
  browser, and agents already have zero-config status via `karta_next.py --json`. A separate
  package would additionally break the ships-in-the-plugin requirement and add cross-package
  version skew. If cross-repo status as agent context outside karta repos ever becomes a real
  need, the shape is a thin MCP facade over the same state engine, in the same plugin — v2 at
  the earliest, and only once a consumer exists.

## Scope of change

- `skills/karta-status/scripts/serve_status.py` — hub mode, ensure/opt-in/opt-out entry
  points, self-test extensions.
- `skills/karta-status/scripts/karta_next.py` + plan/deliver helper scripts — `ensure_hub()`
  call on touch.
- `hooks/scripts/inject_karta_status.py` — ensure + URL line for opted-in repos.
- `skills/karta-status/SKILL.md` — two modes, two gates.
- `docs/how-to/codex.md` parity row; a how-to page for the hub; README touch.
- Generators + gate suite: `sync_codex_skills.py`, `validate_plugin.py`,
  `check_shared_copies.py`, `sync_codex_agents.py --check`.
