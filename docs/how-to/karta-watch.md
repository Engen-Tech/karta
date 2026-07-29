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

## Getting around

From a repo page, two controls return you to the hub landing: the small **k** mark in the
top-left corner and the **← home** button next to it. Both carry your key, so either click just
works. To jump straight to another repo without going home first, use the **also watching:**
line under the header — it lists every other opted-in repo, never the one you are on.

On the repo page itself the vocabulary stays calm: a work item waiting on a dependency shows a
steel **WAITING** chip — waiting its turn is normal flow, not an alarm.

## When the feed pauses

The repo page header carries a feed light. While the page can reach the hub it reads
**live from git — read-only**: every poll re-derives the state fresh from git, and nothing on
the page can change anything. If two polls in a row fail — the hub died, or your machine slept —
the label flips to **snapshot — feed paused**: the page keeps showing the last state it fetched,
but it is no longer updating. One missed poll never flips the label, and the first successful
poll flips it back to live. To revive a dead hub, run the ensure one-liner in "After a reboot"
below.

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
  outbound request exists whose Referer could carry your token.
- **The token rides the URL.** That means it lands in your browser history. On a shared machine,
  open the hub in a private window.
- **IPv4 only.** The bind is `127.0.0.1`, not `::1`. If your browser resolves `localhost` to
  IPv6, use `http://127.0.0.1:<port>/` directly.

The hub is read-only end to end: every card derives fresh from git on each poll, and no web route
can change anything — opt-in and opt-out exist only as the script flags above.
