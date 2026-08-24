# Karta Watch: the persistent hub

Karta Watch is the live browser page that shows where your karta runs stand. By default it is
ephemeral — it dies with the session that started it. Opt a repo in, and a small per-user **hub**
keeps one stable local page alive across sessions: a landing page with one live card per opted-in
repo (status chip, binder counts, next action), each card linking into that repo's full watch
page. Ordinary karta activity revives the hub automatically — you never manage a service.

## Opt a repo in

Tell your agent **"opt this repo into karta watch"** — or run the flag yourself from the repo:

```bash
uv run --script skills/karta-status/scripts/serve_status.py --opt-in
```

That flips one per-repo flag in your own per-user state file (never inside the repo). Repos you
have not opted in never appear on the hub, and their watch behavior is unchanged.

## One stable URL

The hub binds a per-user port derived from your identity (base 8765, stepping to the next
candidate if the port is taken) and requires a per-user token on every request. The URL shape is
always:

```
http://127.0.0.1:<port>/?key=<token>
```

You never have to remember it: in every opted-in repo, session start prints this banner with the
working URL — byte for byte:

```
Karta Watch: http://127.0.0.1:8842/?key=wJ0aPqTt3XanY2vN8dKfLw — persistent; say "turn off karta watch" to disable.
```

(Your port and key will differ; the shape will not.)

## Reading the landing

Each opted-in repo gets one card, and the chip word on the card tells you what that repo needs
from you:

- **NOW** — a delivery is in flight. Work is happening in this repo right now.
- **NEXT** — work is planned and ready to run once someone picks it up.
- **CLEAR** — every binder is merged. Nothing left to run.

Cards sort by that word — every NOW card above every NEXT card above every CLEAR card — so the
top of the page is always the repo that needs you soonest. Each card is one link: click anywhere
on it to open that repo's watch page. (Two rarer words cover broken cards: **WEDGED** when the
status engine failed in that repo, and **UNAVAILABLE** when the repo path no longer exists.
The old chip word for planned work is retired; that chip now reads NEXT — the same word the
repo page's phase rail uses.)

Each card also shows when the repo last saw activity — **active today**, or **active N days
ago** — read from its newest commit. The stamp is simply absent if git can't answer in time.

The landing is set in the same colours, type and motion as the repo pages it links to, so moving
between them doesn't feel like changing product. A repo with work in flight pulses; a broken one
flashes its alarm colour. If you have asked your system for reduced motion, the two don't answer
the same way. The in-flight pulse goes still — it holds a resting ring instead of pulsing outward.
The alarm keeps moving: it softens into the same slow breathing instead of a hard on/off blink,
because a status page that stops signalling a real problem reads as fixed. It refreshes the same
way too — see below.

## Getting around

From a repo page, two controls return you to the hub landing: the small **k** mark in the
top-left corner and the **← home** button next to it. Both carry your key, so either click just
works. To jump straight to another repo without going home first, use the **also watching:**
line under the header — it lists every other opted-in repo, never the one you are on.

On the repo page itself the vocabulary stays calm: a work item waiting on a dependency shows a
steel **WAITING** chip — waiting its turn is normal flow, not an alarm.

## Choosing when the page refreshes

Every refresh derives the state fresh from git, and on a large repo that is real work — up to
613 ms of it. So the page asks slowly, tells you when it will next ask, and lets you stop it
asking at all. Three controls sit in the header, next to **show delivered**:

- **The reading.** With automatic refresh on it counts down — `next in 27s` — to the next
  refresh. The countdown is a local clock; watching it costs nothing.
- **The refresh button.** One refresh, right now. It works in either mode, and on a tab you
  have left in the background — you asked for it, so you get it. With automatic refresh on it
  also restarts the countdown.
- **auto refresh.** The switch itself. On by default; your choice is remembered per browser, so
  a reload keeps it.

Automatic refresh runs every **30 seconds**. Turn it off and the page stops asking: the timer is
cleared, and nothing else in the page starts a request behind your back. The countdown is then
replaced by how old what you are looking at is — `automatic refresh off · 4m old` — so switching
refreshing off never hides the fact that the page has gone stale.

**automatic refresh off is not the same as feed paused.** Off is your choice and the page is
working exactly as asked. Paused (below) means the page tried to reach the hub twice in a row and
failed. The two carry different wording and different styling on purpose, and neither is ever
shown as the other.

A hidden tab makes no automatic requests either, whatever the mode — the page picks the schedule
back up when you return to it.

**The landing follows the same choice.** It used to reload itself every ten seconds no matter
what — six times a minute of re-deriving *every* watched repo, and no way for your choice to
stop it. Now it runs on the same 30-second schedule, reads the same setting, and stops asking
when you turn automatic refresh off. It re-reads the setting every time it comes due, so
switching refresh off in a repo tab quiets the landing within half a minute. There are no
controls on the landing itself: set it once on a repo page and both follow.

One difference is worth knowing. The repo page fetches new state and redraws in place; the
landing is built by the server, so its refresh is a full page load. Expect it to behave like
one — the scroll position returns to the top.

## When the feed pauses

The repo page header carries a feed light. While the page can reach the hub it reads
**live from git — read-only**: every poll derives the state fresh from git, nothing is held
between polls, and nothing on the page can change anything. What an unchanged poll skips is the
download, not the derivation — see the fingerprint below. If two polls in a row fail — the hub
died, or your machine slept —
the label flips to **snapshot — feed paused**: the page keeps showing the last state it fetched,
but it is no longer updating. One missed poll never flips the label, and the first successful
poll flips it back to live. A hidden tab makes no requests at all, so it never flips the label
either — the page picks up where it left off when you come back. To revive a dead hub, run the
ensure one-liner in "After a reboot" below.

## The state feed's fingerprint

Both state feeds — `/state.json` in a single repo, and `/r/<slug>/state.json` on the hub — answer
with an **ETag** response header: a short fingerprint of exactly the bytes that reply carried.
Send that value back on the next request as `If-None-Match`, and if nothing has changed the server
answers **304 Not Modified** with no body instead of re-sending the whole document. A tag stops
matching the moment that repo's state changes, so a 304 never hides new work from you, and each
repo has its own tag — two repos never share one.

If you poll a feed with a script of your own, four things follow:

- **A 304 carries no body.** Keep the state you already hold — a client that expects JSON on every
  reply will find an empty response instead.
- **The token is still checked first.** On the hub a missing or wrong `?key=`, or a Host header
  outside the allowlist, is rejected before the fingerprint is considered at all: 403, and no ETag
  on the reply. A conditional request is never a way around the token.
- **HEAD never 304s.** A HEAD request always answers 200 with the ETag and no body, even when it
  carries a matching `If-None-Match` — use HEAD to read the current tag cheaply, not to poll for
  change.
- **Archived binders ride thin after the first reply.** The page's initial load carries each
  archived binder's full title, summary, and item counts. Every reply after that — a 200 with a
  new body — carries only a compact `{slug, total, done}` entry per archived binder under the
  `archived` key; a script that wants more than counts for a binder archived after load must keep
  the detail from the first reply and join it by slug.

The watch page already handles the first two for you. Each refresh it makes sends back the last tag
it received as `If-None-Match`, so an unchanged state costs a 304 with no body and the page skips
the redraw — and a 304 counts as a healthy poll, because a feed with nothing new to say is still
live. That conditional request is a request like any other, so it obeys the refresh controls above:
a hidden tab and automatic-refresh-off both stop it. A watch tab you left in the background costs
nothing until you look at it.

## Turn it off

Say **"turn off karta watch"** — the agent runs `--opt-out` for the current repo. Or run it
yourself:

```bash
uv run --script skills/karta-status/scripts/serve_status.py --opt-out
```

`--opt-out` also accepts a path or slug, so you can clear the entry of a repo you moved or
deleted from anywhere. When the last repo opts out, the hub exits on its own.

## After a reboot: one honest gap

karta installs no OS services — no systemd, no launchd, no Windows service. So after a reboot the
hub is not running and **the URL is dead until karta runs once**. Any karta touch in an opted-in
repo — a plan, a build, a status ask, a new session — revives it. To revive it without touching
karta, run the ensure one-liner in any terminal:

```bash
uv run --script skills/karta-status/scripts/serve_status.py --ensure
```

(Use the path where karta is installed; the nudge line below prints the full path for you.)
`--ensure` is idempotent and silent on success: healthy hub, no-op; dead port, detached respawn;
outdated hub after a plugin update, clean replace. It never blocks your work — any failure prints
one plain line and exits 0.

## On Codex: the sandbox may block the revive

The automatic revive is embedded in scripts every platform runs, so it works on Codex with no
hooks. But a Codex sandbox can deny spawning the detached hub process. When that happens the
ensure **fails open** — your karta work is never blocked — and the status surface shows a nudge
instead of the URL banner, naming the recorded reason and the exact one-liner to run yourself
from a terminal outside the sandbox:

```
Karta Watch: hub not running (spawn denied by sandbox) — revive it: uv run --script <path-to>/serve_status.py --ensure
```

## Security model

- **Token required everywhere.** Every hub route — landing page, repo pages, state feeds,
  `/identity`, assets — rejects a missing or wrong `?key=`. Comparison is constant-time.
- **Loopback only.** The bind is hardcoded to `127.0.0.1` with no interface option. The page is
  never reachable from another machine.
- **Host-header allowlist.** Only `127.0.0.1:<port>` and `localhost:<port>` are accepted, which
  defeats DNS-rebinding tricks.
- **Fully self-contained page.** No CDN, no remote fonts, no off-site loads of any kind — so no
  outbound request exists whose Referer could carry your token. The typefaces are vendored (see
  below), not fetched from a font service.
- **The token rides the URL.** That means it lands in your browser history. On a shared machine,
  open the hub in a private window.
- **IPv4 only.** The bind is `127.0.0.1`, not `::1`. If your browser resolves `localhost` to
  IPv6, use `http://127.0.0.1:<port>/` directly.

The hub is read-only end to end: every card derives fresh from git on each poll, and no web route
can change anything — opt-in and opt-out exist only as the script flags above.

## The typefaces ship with the plugin

The page sets its text in three families — Newsreader, IBM Plex Sans and IBM Plex Mono. All three
are vendored: eight subset `.woff2` files under `skills/karta-status/assets/fonts/`, served off the
same `/assets/` route as the mascot and the Vue runtime. Nothing is fetched from a font service, so
the page works with no network at all and leaks no request to anyone. Together the eight files are
about 108 KB, and a check refuses to let them past 400 KB.

Two consequences worth knowing:

- **Coverage stops at one writing system.** Each face is cut to Basic Latin, Latin-1 Supplement and
  General Punctuation — a fixed range, not "the characters the page happens to show today", because
  the page renders your binder titles, summaries and file paths, which nobody can enumerate in
  advance. Anything outside that range — an arrow, a box-drawing character, CJK, an emoji — is drawn
  by your system font instead. That is by design, not a defect: every font stack keeps a system
  family after the vendored one, so text always renders.
- **A file:// snapshot falls back entirely.** Saved to disk and opened without the server, the font
  URLs resolve to nothing and the whole page renders in system fonts. It stays readable; it just
  does not look like the served page.

`skills/karta-status/assets/fonts/manifest.json` records what was shipped and where it came from:
per face the family, weight, file, size, sha256 and unicode range, and per family the upstream
version, the exact `google/fonts` commit it was taken from, and the subsetting flags. Each family
also ships its SIL Open Font Licence file beside the fonts. The subsetting tool (fonttools) runs at
build time only — the page itself is stdlib Python with no dependencies, so the checks compare the
manifest, the files on disk and the page's `@font-face` rules against each other rather than
claiming to read glyphs back out of the binaries.
