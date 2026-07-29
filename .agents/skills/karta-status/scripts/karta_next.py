# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta-status engine: derive 'what's next' from binders + git. Zero dependencies.

  uv run --script karta_next.py                       # terminal map (auto-detect .karta/binders)
  uv run --script karta_next.py --json                # the state as JSON (Phase 2's server reads this)
  uv run --script karta_next.py --footer --binder S   # one-line run footer for a binder slug
  uv run --script karta_next.py --self-test           # embedded fixtures, exit 0/1

Order is a topo sort over `after` edges, recomputed every call — never stored. A dangling `after`
is a warning; a cross-binder cycle is an error (and order is null). Delivered binders live in
`.karta/binders/archive/` (karta-deliver's end-of-life step): they are never listed, but an
`after` naming one resolves as satisfied — a delivered predecessor is not a dangling edge.

Every real invocation also fires a fail-open, fire-and-forget `serve_status.py --ensure`
(Karta Watch hub revival), and the human renders — footer/terminal, never --json — carry one
nudge line when this repo is opted in but the hub is unreachable."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

BINDERS_DIR = Path(".karta/binders")
ARCHIVE_DIR = BINDERS_DIR / "archive"


def _topo_order(after: dict[str, list[str]]) -> list[str] | None:
    """Kahn topo sort. `after[slug]` = the slugs that must come before `slug`. Deterministic
    (slug order among ready nodes). Returns the order, or None if a cycle leaves nodes unplaced."""
    indeg = {n: 0 for n in after}
    succ: dict[str, list[str]] = {n: [] for n in after}
    for n, preds in after.items():
        for p in preds:
            if p in indeg:
                succ[p].append(n)
                indeg[n] += 1
    ready = sorted(n for n in after if indeg[n] == 0)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()
    return out if len(out) == len(after) else None


def _binder_status(item_ids: list[str], gb: dict) -> str:
    gitems = gb.get("items", {})
    if item_ids and all(gitems.get(i, {}).get("done_in_default") for i in item_ids):
        return "merged"
    if gb.get("integration_exists") or any(gitems.get(i, {}).get("done") for i in item_ids):
        return "in_flight"
    return "not_started"


def _item_status(deps: list[str], gi: dict, done_ids: set[str]) -> tuple[str, list[str]]:
    if gi.get("done"):   return "done", []
    if gi.get("failed"): return "failed", []
    if gi.get("built"):  return "built", []
    if gi.get("branch"): return "building", []
    unmet = [d for d in deps if d not in done_ids]
    return ("blocked", unmet) if unmet else ("ready", [])


def derive_state(binders: list[dict], git_facts: dict,
                 archived: frozenset[str] = frozenset()) -> dict:
    default_branch = git_facts.get("default_branch", "main")
    gfb = git_facts.get("binders", {})
    by_slug = {b["slug"]: b for b in binders}

    # cross-binder graph: resolve `after`, collect warnings, topo-sort for the order.
    # A live binder wins over an archived namesake; an `after` naming an archived-only
    # slug is a delivered predecessor — satisfied, dropped from the graph, no warning.
    slugs = set(by_slug)
    warnings: list[str] = []
    for s in sorted(slugs & archived):
        warnings.append(f"binder '{s}' reuses the slug of an archived (delivered) binder — "
                        "the delivered history is shadowed; plan new work under a fresh slug")
    after: dict[str, list[str]] = {}
    for slug, b in by_slug.items():
        resolved = []
        for ref in b.get("after", []) or []:
            if ref in slugs:
                resolved.append(ref)
            elif ref not in archived:
                warnings.append(f"binder '{slug}' has a dangling after: '{ref}' (no such binder)")
        after[slug] = resolved
    order = _topo_order(after)
    errors = [] if order is not None else ["cross-binder cycle in `after` — no run order exists"]

    out_binders = []
    status_by_slug: dict[str, str] = {}
    for slug, b in by_slug.items():
        gb = gfb.get(slug, {})
        items = b.get("work_items", [])
        item_ids = [it["id"] for it in items]
        status = _binder_status(item_ids, gb)
        status_by_slug[slug] = status

        gitems = gb.get("items", {})
        done_ids = {i for i in item_ids if gitems.get(i, {}).get("done")}
        detail, counts = [], {k: 0 for k in
                              ("done", "built", "failed", "building", "ready", "blocked")}
        for it in items:
            st, blk = _item_status(it.get("depends_on", []), gitems.get(it["id"], {}), done_ids)
            counts[st] += 1
            entry = {"id": it["id"], "status": st}
            if blk:
                entry["blocked_by"] = blk
            detail.append(entry)
        out_binders.append({
            "slug": slug, "after": after[slug], "status": status,
            "items": {"total": len(items), **counts, "detail": detail},
        })

    # is_next: a not-started binder whose every `after` predecessor is merged
    for ob in out_binders:
        ob["is_next"] = (ob["status"] == "not_started"
                         and all(status_by_slug.get(p) == "merged" for p in ob["after"]))

    order_view = order if order is not None else sorted(by_slug)
    next_action = _next_action(out_binders, order_view, warnings, errors, archived)
    return {
        "repo": {"default_branch": default_branch},
        "order": order,                      # None on cycle — derived, never stored
        "binders": _in_order(out_binders, order_view),
        "next_action": next_action,
        "warnings": sorted(set(warnings)),
        "errors": errors,
    }


def _in_order(out_binders: list[dict], order_view: list[str]) -> list[dict]:
    pos = {s: i for i, s in enumerate(order_view)}
    return sorted(out_binders, key=lambda ob: pos.get(ob["slug"], len(pos)))


# The calm end-state copy. One constant, shared by the derive and the self-test —
# the hub landing renders next_action.human verbatim, so this string is contract.
DONE_HUMAN = "all binders merged — nothing left to run"


def _next_action(out_binders: list[dict], order_view: list[str], warnings: list[str],
                 errors: list[str], archived: frozenset[str] = frozenset()) -> dict:
    by_slug = {ob["slug"]: ob for ob in out_binders}
    ordered = [by_slug[s] for s in order_view if s in by_slug]

    # 1) an in-flight binder with a failed item — fix/rerun or re-plan
    for ob in ordered:
        if ob["status"] == "in_flight" and ob["items"]["failed"]:
            return {"level": "item", "command": f"karta-deliver {ob['slug']}",
                    "human": f"{ob['slug']} has a halted item — fix and re-run, or re-plan with karta-plan"}
    # 2) an in-flight binder with work left (building/ready/blocked) — resume it
    for ob in ordered:
        if ob["status"] == "in_flight" and (ob["items"]["building"] or ob["items"]["ready"]
                                            or ob["items"]["blocked"]):
            done, total = ob["items"]["done"], ob["items"]["total"]
            return {"level": "item", "command": f"karta-deliver {ob['slug']}",
                    "human": f"resume {ob['slug']} ({done}/{total} done)"}
    # 3) no in-flight work — start the next not-started, unblocked binder
    for ob in ordered:
        if ob.get("is_next"):
            return {"level": "binder", "command": f"karta-deliver {ob['slug']}",
                    "human": f"start {ob['slug']} (its predecessors are merged)"}
    # 4) everything merged or archived (zero live binders included) on a clean
    #    derive — done. Warnings/errors keep the blocked message so a dangling
    #    edge or cycle is never papered over; an empty repo with no archive has
    #    nothing delivered, so it stays on the blocked derive too.
    if ((ordered or archived) and not warnings and not errors
            and all(ob["status"] == "merged" for ob in ordered)):
        return {"level": "done", "command": None, "human": DONE_HUMAN}
    # 5) work remains but nothing is runnable (blocked / cycle bottleneck)
    return {"level": "blocked", "command": None,
            "human": "no binder is ready to run — check the warnings/errors above"}


_GLYPH = {"merged": "✓", "in_flight": "●", "not_started": "○"}
_ITEM_GLYPH = {"done": "✓", "built": "▣", "failed": "✗", "building": "◐",
               "ready": "·", "blocked": "○"}


def render_terminal(state: dict) -> str:
    lines: list[str] = []
    for w in state["warnings"]:
        lines.append(f"  warning: {w}")
    for e in state["errors"]:
        lines.append(f"  error: {e}")
    route = "   ".join(f"{b['slug']} {_GLYPH[b['status']]}" for b in state["binders"])
    lines.append(route or "(no binders planned yet)")
    for b in state["binders"]:
        if b["status"] == "in_flight":
            it = b["items"]
            lines.append("")
            lines.append(f"{b['slug']}  (current binder)        {it['done']}/{it['total']} done")
            for d in it["detail"]:
                tail = ("  needs " + ", ".join(d["blocked_by"])) if d.get("blocked_by") else ""
                lines.append(f"   {_ITEM_GLYPH.get(d['status'], '?')} {d['id']}  {d['status']}{tail}")
    na = state["next_action"]
    lines.append("  " + "─" * 44)
    if na["command"]:
        lines.append(f"▶ next:  {na['command']}   ({na['human']})")
    else:
        lines.append(f"▶ {na['human']}")
    return "\n".join(lines)


# `built` shows as ▣ (committed, awaiting the orchestrator's merge); `building` as ◐.


def render_footer(state: dict, slug: str) -> str:
    na = state["next_action"]
    cur = next((b for b in state["binders"] if b["slug"] == slug), None)
    head = ""
    if cur:
        it = cur["items"]
        left = it["total"] - it["done"]
        head = f"{slug} {it['done']}/{it['total']}" + (f" · {left} left" if left else " · complete")
    tip = f"▶ {na['command']}" if na["command"] else f"▶ {na['human']}"
    return "  ".join(x for x in (head, tip) if x)


# ---------------------------------------------------------------------------
# Karta Watch surface (revive-integration): every real engine touch fires a
# fail-open, fire-and-forget `serve_status.py --ensure`, and the human renders
# (footer/terminal — never --json) may carry one nudge line when this repo is
# opted in but the hub is unreachable. Nothing here ever alters this script's
# own stdout shape on --json, its JSON schema, or its exit code.
# ---------------------------------------------------------------------------

_WATCH_SCRIPT = Path(__file__).resolve().parent / "serve_status.py"
# Shared terms (binder karta-watch-hub): the 'Karta Watch:' prefix and the
# 'turn off karta watch' phrase are canonical — render them byte-exactly.
WATCH_BANNER = ('Karta Watch: http://127.0.0.1:{port}/?key={token} — '
                'persistent; say "turn off karta watch" to disable.')
WATCH_NUDGE = ('Karta Watch: hub not running{reason} — revive it: '
               'uv run --script {script} --ensure')


def _fire_ensure(popen=None, os_name: str | None = None) -> None:
    """Fire-and-forget hub revival: spawn `serve_status.py --ensure` with its
    stdio on DEVNULL plus close_fds (POSIX) or the detached-process creation
    flags (Windows), so a parent capturing this script's output can never
    block on, or receive bytes from, the ensure child. Every failure is
    swallowed — the embed is fail-open and never changes this script's own
    output or exit code. The popen/os_name seams exist for the self-test."""
    try:
        if not _WATCH_SCRIPT.is_file():
            return
        kwargs: dict = {"stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                        "close_fds": True}
        if (os_name or os.name) != "posix":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        (popen or subprocess.Popen)(
            [sys.executable, str(_WATCH_SCRIPT), "--ensure"], **kwargs)
    except Exception:
        pass


def _load_watch():
    """The sibling serve_status module (watch store + probe), or None when it
    is unavailable. Imported lazily so the engine costs nothing extra at load
    and there is no import cycle (serve_status imports this module)."""
    try:
        d = str(Path(__file__).resolve().parent)
        if d not in sys.path:
            sys.path.insert(0, d)
        import serve_status
        return serve_status
    except Exception:
        return None


def _watch_state_path() -> Path:
    """The per-user watch state file, resolved WITHOUT importing serve_status
    — the cold-path gate below must stay a bare stat/read. Mirrors
    serve_status.resolve_state_dir (KARTA_WATCH_STATE_DIR override first, then
    the platform dir); serve_status's self-test pins that resolution, this
    copy only ever reads."""
    override = os.environ.get("KARTA_WATCH_STATE_DIR")
    if override:
        return Path(override) / "state.json"
    home = Path(os.environ["HOME"]) if os.environ.get("HOME") else Path.home()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else home / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_STATE_HOME")
        base = Path(xdg) if xdg else home / ".local" / "state"
    return base / "karta" / "state.json"


def _opted_in_root(cwd: str | None = None) -> str | None:
    """The cold-path gate: the repo root above `cwd` when that root is opted
    in per the per-user store, else None — decided from a stat/read of the
    small state JSON alone. A repo with no store file or no opt-in pays
    nothing beyond that read: serve_status is never imported here, and
    nothing is created on disk. Fail-open: any error is 'not opted in'."""
    try:
        state_path = _watch_state_path()
        if not state_path.is_file():
            return None
        d = os.path.abspath(os.fspath(cwd) if cwd is not None else os.getcwd())
        while not os.path.exists(os.path.join(d, ".git")):  # nearest .git, as
            parent = os.path.dirname(d)                     # find_repo_root does
            if parent == d:
                return None
            d = parent
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        repos = doc.get("repos") if isinstance(doc, dict) else None
        rec = repos.get(d) if isinstance(repos, dict) else None
        return d if isinstance(rec, dict) and rec.get("opted_in") else None
    except Exception:
        return None


def watch_line(cwd: str | None = None, *, banner: bool = False, probe=None,
               retries: int = 0, retry_delay: float = 0.25,
               sleep=time.sleep) -> str | None:
    """The one Karta Watch line for the human surfaces, or None.

    None unless the repo at `cwd` is opted in per the per-user watch store —
    decided by the cheap _opted_in_root read BEFORE the watch module loads, so
    a non-opted repo never pays the serve_status import. Opted in with the hub
    answering our token: the persistent-watch URL banner — only when `banner`
    is set (the session-start hook's surface; this script's own renders stay
    quiet while the hub is healthy). Opted in with the hub unreachable: up to
    `retries` re-probes ~retry_delay s apart first (the hook passes 2 — its
    just-fired ensure needs ~1 s to bind, and nudging about a hub that is
    coming up reads as broken), then the one-line nudge naming the --ensure
    one-liner plus any failure reason the ensure path recorded in the state
    dir. Fail-open: any error returns None. Never called on the --json path."""
    try:
        if _opted_in_root(cwd) is None:
            return None
        watch = _load_watch()
        if watch is None:
            return None
        sd = watch.ensure_state_dir()
        port = watch._hub_port(sd)
        token = watch.get_token()
        probe_fn = probe or (lambda p: watch._probe_hub(p, token))
        kind = probe_fn(port)[0]
        for _ in range(retries):
            if kind == "ours":
                break
            sleep(retry_delay)
            kind = probe_fn(port)[0]
        if kind == "ours":
            return WATCH_BANNER.format(port=port, token=token) if banner else None
        reason = ""
        try:
            doc = json.loads((sd / watch.ENSURE_FAILURE_FILENAME)
                             .read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("reason"):
                reason = f" ({doc['reason']})"
        except Exception:
            reason = ""
        return WATCH_NUDGE.format(reason=reason, script=watch._SCRIPT_PATH)
    except Exception:
        return None


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True).stdout
    except OSError:
        return ""


def _default_branch() -> str:
    head = _git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
    if head:
        return head.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _git("rev-parse", "--verify", "--quiet", cand).strip():
            return cand
    return "main"


def load_binders(binders_dir: Path = BINDERS_DIR) -> list[dict]:
    out = []
    if binders_dir.is_dir():
        for p in sorted(binders_dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def load_archived_binders(archive_dir: Path = ARCHIVE_DIR) -> list[dict]:
    """Delivered binders, moved to `.karta/binders/archive/` by karta-deliver's
    end-of-life step. Same shape as `load_binders`; consumed for `after`
    satisfaction (the engine) and the Delivered timeline phase (the watch page)."""
    out = []
    if archive_dir.is_dir():
        for p in sorted(archive_dir.glob("*.json")):
            try:
                doc = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict) and isinstance(doc.get("slug"), str):
                out.append(doc)
    return out


def gather_git_facts(binders: list[dict], default_branch: str) -> dict:
    facts = {"default_branch": default_branch, "binders": {}}
    for b in binders:
        slug = b["slug"]
        item_ids = [it["id"] for it in b.get("work_items", [])]
        refs = set(_git("for-each-ref", "--format=%(refname)",
                        f"refs/karta/{slug}/").splitlines())
        integration = bool(_git("rev-parse", "--verify", "--quiet",
                                f"karta/{slug}/integration").strip())
        items = {}
        for i in item_ids:
            base = f"refs/karta/{slug}/item-{i}"
            done = f"{base}/done" in refs
            # `merge-base --is-ancestor` answers via exit code, so call subprocess directly:
            done_in_default = done and subprocess.run(
                ["git", "merge-base", "--is-ancestor", f"{base}/done", default_branch]
            ).returncode == 0
            branch = bool(_git("rev-parse", "--verify", "--quiet",
                               f"karta/{slug}/item-{i}").strip())
            items[i] = {
                "done": done,
                "done_in_default": done_in_default,
                "built": f"{base}/built" in refs,
                "failed": f"{base}/failed" in refs,
                "branch": branch,
            }
        facts["binders"][slug] = {"integration_exists": integration, "items": items}
    return facts


def _watch_self_test_checks() -> list[tuple[str, bool]]:
    """Karta Watch surface (revive-integration): the fire-and-forget ensure
    spawn, the one nudge/banner line, and — end to end — that neither ensure
    nor the watch surface ever alters --json output or the exit code. Every
    check points KARTA_WATCH_STATE_DIR at a scratch dir: the real per-user
    store is never touched, and no real hub daemon is ever spawned (the e2e
    occupies every candidate port so the ensure child fails open)."""
    import contextlib, socket, tempfile, time
    checks: list[tuple[str, bool]] = []

    @contextlib.contextmanager
    def temp_store():
        saved = os.environ.get("KARTA_WATCH_STATE_DIR")
        with tempfile.TemporaryDirectory() as sd:
            os.environ["KARTA_WATCH_STATE_DIR"] = sd
            try:
                yield Path(sd)
            finally:
                if saved is None:
                    os.environ.pop("KARTA_WATCH_STATE_DIR", None)
                else:
                    os.environ["KARTA_WATCH_STATE_DIR"] = saved

    # -- spawn args: DEVNULL stdio + close_fds / detached creation flags --
    calls: list[tuple[list[str], dict]] = []
    _fire_ensure(popen=lambda argv, **kw: calls.append((argv, kw)),
                 os_name="posix")
    ok = bool(calls)
    if ok:
        argv, kw = calls[0]
        ok = (argv == [sys.executable, str(_WATCH_SCRIPT), "--ensure"]
              and kw.get("stdin") is subprocess.DEVNULL
              and kw.get("stdout") is subprocess.DEVNULL
              and kw.get("stderr") is subprocess.DEVNULL
              and kw.get("close_fds") is True
              and "creationflags" not in kw)
    checks.append(("ensure spawn (POSIX): --ensure argv, DEVNULL stdio, close_fds", ok))
    calls.clear()
    _fire_ensure(popen=lambda argv, **kw: calls.append((argv, kw)), os_name="nt")
    flags = calls[0][1].get("creationflags", 0) if calls else 0
    checks.append(("ensure spawn (Windows): detached creation flags + DEVNULL stdio",
                   bool(flags & 0x00000008) and bool(flags & 0x00000200)
                   and calls and calls[0][1].get("stdout") is subprocess.DEVNULL))

    def _boom(argv, **kw):
        raise OSError("spawn denied")
    try:
        _fire_ensure(popen=_boom)
        swallowed = True
    except Exception:                                          # noqa: BLE001
        swallowed = False
    checks.append(("ensure spawn failure is swallowed", swallowed))

    # -- watch_line: opt-in gate, exact banner, nudge, fail-open --
    with temp_store() as sd:
        watch = _load_watch()
        checks.append(("watch store module loads", watch is not None))
        repo = sd / "repo"
        (repo / ".git").mkdir(parents=True)
        checks.append(("not opted in -> no watch line", watch_line(str(repo)) is None))
        watch.upsert_repo(str(repo), opted_in=True)
        port = watch._hub_port(watch.ensure_state_dir())
        token = watch.get_token()
        expect = (f'Karta Watch: http://127.0.0.1:{port}/?key={token} — '
                  f'persistent; say "turn off karta watch" to disable.')
        checks.append(("opted in + reachable hub -> the exact URL banner",
                       watch_line(str(repo), banner=True,
                                  probe=lambda p: ("ours", {})) == expect))
        checks.append(("healthy hub -> the engine renders stay quiet",
                       watch_line(str(repo), probe=lambda p: ("ours", {})) is None))
        nudge = watch_line(str(repo), probe=lambda p: ("dead", None))
        checks.append(("opted in + unreachable -> nudge names the --ensure one-liner",
                       nudge is not None
                       and nudge.startswith("Karta Watch: hub not running")
                       and f"{watch._SCRIPT_PATH} --ensure" in nudge
                       and "?key=" not in nudge))
        watch._record_ensure_failure("no bindable port")
        nudge2 = watch_line(str(repo), banner=True,
                            probe=lambda p: ("foreign", None))
        checks.append(("nudge carries the recorded failure reason",
                       nudge2 is not None and "(no bindable port)" in nudge2))
        checks.append(("outside any git checkout -> no watch line",
                       watch_line(str(sd / "nowhere")) is None))

        # -- the hook's bounded retry window (a just-fired ensure needs ~1 s
        # to bind): re-probe up to `retries` times ~250 ms apart, banner on a
        # late success, nudge only after the window exhausts --
        rp_probes: list[int] = []
        rp_naps: list[float] = []
        late = iter([("dead", None), ("dead", None), ("ours", {})])
        got_late = watch_line(str(repo), banner=True,
                              probe=lambda p: rp_probes.append(p) or next(late),
                              retries=2, sleep=rp_naps.append)
        checks.append(("retry: a hub that binds during the re-probe window"
                       " still yields the banner",
                       got_late == expect and len(rp_probes) == 3
                       and rp_naps == [0.25, 0.25]))
        rp_probes.clear(); rp_naps.clear()
        got_down = watch_line(str(repo), banner=True,
                              probe=lambda p: rp_probes.append(p)
                              or ("dead", None),
                              retries=2, sleep=rp_naps.append)
        checks.append(("retry: the window is bounded — 2 re-probes, then the"
                       " nudge",
                       got_down is not None
                       and got_down.startswith("Karta Watch: hub not running")
                       and len(rp_probes) == 3 and len(rp_naps) == 2))
        rp_probes.clear(); rp_naps.clear()
        got_up = watch_line(str(repo), banner=True,
                            probe=lambda p: rp_probes.append(p) or ("ours", {}),
                            retries=2, sleep=rp_naps.append)
        checks.append(("retry: a first-probe success never sleeps",
                       got_up == expect and len(rp_probes) == 1
                       and rp_naps == []))

    # -- cold path: a repo not opted in decides from the bare state JSON and
    # never imports the watch module --
    with temp_store() as sd:
        cold_repo = sd / "cold"
        (cold_repo / ".git").mkdir(parents=True)
        code = ("import json, sys\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import karta_next\n"
                "line = karta_next.watch_line(sys.argv[2])\n"
                "print(json.dumps({'line': line,"
                " 'imported': 'serve_status' in sys.modules}))\n")
        proc = subprocess.run(
            [sys.executable, "-c", code,
             str(Path(__file__).resolve().parent), str(cold_repo)],
            capture_output=True, text=True, timeout=60)
        try:
            cold = json.loads(proc.stdout)
        except ValueError:
            cold = {}
        checks.append(("cold path: a non-opted repo never imports serve_status",
                       proc.returncode == 0 and cold.get("line") is None
                       and cold.get("imported") is False))

    # -- e2e: opted-in repo, every candidate port foreign — the detached child
    # fails open (never spawns), the footer ends on the nudge, --json is
    # untouched, both exit 0 --
    with temp_store() as sd:
        repo = sd / "repo"
        (repo / ".karta" / "binders").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / ".karta" / "binders" / "s.json").write_text(json.dumps(
            {"slug": "s", "motivation": "x", "scope": {"included": ["x"]},
             "work_items": [{"id": "a", "title": "A",
                             "oracle": {"type": "unit"}}]}))
        # realpath: the child processes key the store by their resolved cwd
        repo = Path(os.path.realpath(repo))
        watch = _load_watch()
        watch.upsert_repo(str(repo), opted_in=True)
        listeners: list = []
        base = None
        for start in range(watch.PORT_BASE, watch.PORT_BASE + watch.PORT_SPAN - 5):
            socks = []
            try:
                for off in range(5):
                    s = socket.socket()
                    s.bind(("127.0.0.1", start + off))
                    s.listen(16)
                    socks.append(s)
                listeners, base = socks, start
                break
            except OSError:
                for s in socks:
                    s.close()
        checks.append(("e2e scaffold: five consecutive loopback ports held",
                       base is not None))
        if base is not None:
            watch.record_port(base)
            me = str(Path(__file__).resolve())
            crumb = watch.ensure_state_dir() / watch.ENSURE_FAILURE_FILENAME

            def wait_crumb() -> bool:
                """Each detached ensure child ends its candidate walk by
                failing open — writing the breadcrumb. Gating on it before
                the next step keeps every candidate port held for the whole
                walk, so a child can never see a freed port and spawn a real
                daemon out of the test."""
                deadline = time.time() + 30
                while time.time() < deadline:
                    if crumb.is_file():
                        return True
                    time.sleep(0.2)
                return False

            foot = subprocess.run([sys.executable, me, "--footer", "--binder", "s"],
                                  capture_output=True, text=True, cwd=repo,
                                  timeout=120)
            ran_footer = wait_crumb()
            crumb.unlink(missing_ok=True)
            jso = subprocess.run([sys.executable, me, "--json"],
                                 capture_output=True, text=True, cwd=repo,
                                 timeout=120)
            ran_json = wait_crumb()
            flines = foot.stdout.splitlines()
            checks.append(("e2e: footer exits 0 and ends on the one nudge line",
                           foot.returncode == 0 and len(flines) == 2
                           and flines[1].startswith("Karta Watch: hub not running")
                           and "--ensure" in flines[1]))
            checks.append(("e2e: --json output and exit code are never altered",
                           jso.returncode == 0 and "Karta Watch" not in jso.stdout
                           and isinstance(json.loads(jso.stdout), dict)))
            checks.append(("e2e: both fire-and-forget ensure children ran and failed open",
                           ran_footer and ran_json))
        for s in listeners:
            s.close()
    return checks


def _run_self_test() -> int:
    new   = {"slug": "s-new",  "motivation": "x", "scope": {"included": ["x"]},
             "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}
    edit  = {"slug": "s-edit", "after": ["s-new"], "motivation": "x", "scope": {"included": ["x"]},
             "work_items": [
                 {"id": "api", "title": "api", "oracle": {"type": "unit"}},
                 {"id": "doc", "title": "doc", "depends_on": ["api"], "oracle": {"type": "unit"}}]}
    deln  = {"slug": "s-del",  "after": ["s-edit"], "motivation": "x", "scope": {"included": ["x"]},
             "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}
    binders = [new, edit, deln]

    facts = {"default_branch": "main", "binders": {
        "s-new":  {"integration_exists": False,
                   "items": {"a": {"done": True, "done_in_default": True}}},
        "s-edit": {"integration_exists": True, "items": {
            "api": {"done": True, "done_in_default": False},
            "doc": {"branch": False}}},
        "s-del":  {"integration_exists": False, "items": {"a": {}}},
    }}
    st = derive_state(binders, facts)

    checks = [
        ("order is topo-sorted", st["order"] == ["s-new", "s-edit", "s-del"]),
        ("new is merged",  st["binders"][0]["status"] == "merged"),
        ("edit is in-flight", st["binders"][1]["status"] == "in_flight"),
        ("del is not-started", st["binders"][2]["status"] == "not_started"),
        ("doc is ready (api done)", any(d["id"] == "doc" and d["status"] == "ready"
                                        for d in st["binders"][1]["items"]["detail"])),
        ("next action resumes edit", st["next_action"]["command"] == "karta-deliver s-edit"),
        ("no warnings/errors", st["warnings"] == [] and st["errors"] == []),
        ("del not yet is_next (edit unmerged)", st["binders"][2]["is_next"] is False),
    ]

    dangle = derive_state([{"slug": "z", "after": ["ghost"], "motivation": "x",
                            "scope": {"included": ["x"]},
                            "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
                          {"default_branch": "main", "binders": {"z": {"items": {"a": {}}}}})
    checks.append(("dangling after warns", len(dangle["warnings"]) == 1 and dangle["errors"] == []))

    cyc = derive_state(
        [{"slug": "ca", "after": ["cb"], "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]},
         {"slug": "cb", "after": ["ca"], "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
        {"default_branch": "main", "binders": {"ca": {"items": {"a": {}}},
                                               "cb": {"items": {"a": {}}}}})
    checks.append(("cycle -> order None + error", cyc["order"] is None and len(cyc["errors"]) == 1))

    # archived predecessors: an `after` naming a delivered (archived) binder is satisfied —
    # no warning, and the successor is next. A live binder wins over an archived namesake.
    arch = derive_state(
        [{"slug": "w", "after": ["shipped"], "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
        {"default_branch": "main", "binders": {"w": {"items": {"a": {}}}}},
        archived=frozenset({"shipped"}))
    checks.append(("after -> archived binder is satisfied (no warning, is_next)",
                   arch["warnings"] == [] and arch["binders"][0]["is_next"] is True))
    dup = derive_state(
        [{"slug": "dup", "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]},
         {"slug": "x", "after": ["dup"], "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
        {"default_branch": "main", "binders": {"dup": {"items": {"a": {}}},
                                               "x": {"items": {"a": {}}}}},
        archived=frozenset({"dup"}))
    x_row = next(ob for ob in dup["binders"] if ob["slug"] == "x")
    checks.append(("live slug wins over an archived namesake (edge kept, x waits)",
                   x_row["after"] == ["dup"] and x_row["is_next"] is False))
    checks.append(("the shadowed archived namesake draws a warning",
                   len(dup["warnings"]) == 1 and "reuses the slug" in dup["warnings"][0]))

    # the calm end state (watch-shell): all merged or archived + clean derive -> done,
    # while every genuinely blocked derive keeps the blocked copy
    done_action = {"level": "done", "command": None, "human": DONE_HUMAN}
    all_merged = derive_state(
        [{"slug": "m1", "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
        {"default_branch": "main", "binders": {
            "m1": {"items": {"a": {"done": True, "done_in_default": True}}}}})
    checks.append(("all binders merged -> done, no command, the calm copy",
                   all_merged["next_action"] == done_action))
    all_archived = derive_state([], {"default_branch": "main", "binders": {}},
                                archived=frozenset({"shipped"}))
    checks.append(("zero live binders (all archived) -> the same calm done",
                   all_archived["next_action"] == done_action))
    checks.append(("genuinely blocked (cycle) derive is unchanged",
                   cyc["next_action"] == {"level": "blocked", "command": None,
                                          "human": "no binder is ready to run — "
                                                   "check the warnings/errors above"}))
    warn_merged = derive_state(
        [{"slug": "wm", "after": ["ghost"], "motivation": "x", "scope": {"included": ["x"]},
          "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}}]}],
        {"default_branch": "main", "binders": {
            "wm": {"items": {"a": {"done": True, "done_in_default": True}}}}})
    checks.append(("all merged but a dangling-after warning -> still blocked, never done",
                   warn_merged["warnings"] != []
                   and warn_merged["next_action"]["level"] == "blocked"))
    checks.append(("no binders and no archive -> unchanged blocked derive",
                   derive_state([], {"default_branch": "main", "binders": {}})
                   ["next_action"]["level"] == "blocked"))
    done_foot = render_footer(all_merged, "m1")
    done_term = render_terminal(all_archived)
    checks.append(("footer and terminal render the done state calmly",
                   done_foot == f"m1 1/1 · complete  ▶ {DONE_HUMAN}"
                   and done_term.splitlines()[-1] == f"▶ {DONE_HUMAN}"
                   and "warning:" not in done_term and "error:" not in done_term
                   and "karta-deliver" not in done_term))

    # the renderers must not raise on a real state
    try:
        render_terminal(st); render_footer(st, "s-edit"); rendered = True
    except Exception as exc:                                   # noqa: BLE001
        rendered = False; print(f"render raised: {exc}")
    checks.append(("renderers run", rendered))

    checks.extend(_watch_self_test_checks())

    failures = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        failures += 0 if ok else 1
    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--footer", action="store_true")
    ap.add_argument("--binder", type=str)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    _fire_ensure()  # every real engine touch revives the watch hub — fail-open
    binders = load_binders()
    archived = frozenset(b["slug"] for b in load_archived_binders())
    state = derive_state(binders, gather_git_facts(binders, _default_branch()), archived)
    if args.json:
        print(json.dumps(state, indent=2))  # never altered by the watch surface
    else:
        out = (render_footer(state, args.binder or "") if args.footer
               else render_terminal(state))
        nudge = watch_line()
        print(out if nudge is None else f"{out}\n{nudge}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
