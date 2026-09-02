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
import argparse, fnmatch, json, os, subprocess, sys, time
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
                 archived: frozenset[str] = frozenset(),
                 surface_on_default: dict[str, bool | None] | None = None) -> dict:
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
        row = {
            "slug": slug, "after": after[slug], "status": status,
            "items": {"total": len(items), **counts, "detail": detail},
        }
        # Finding 21: a not_started binder whose declared surface already exists
        # on the default branch was likely delivered by another hand. Flag it
        # (advisory only — never a state change) so the next action can say so.
        if (status == "not_started" and surface_on_default
                and surface_on_default.get(slug) is True):
            row["surface_on_default"] = True
        out_binders.append(row)

    # is_next: a not-started binder whose every `after` predecessor is merged
    for ob in out_binders:
        ob["is_next"] = (ob["status"] == "not_started"
                         and all(status_by_slug.get(p) == "merged" for p in ob["after"]))

    order_view = order if order is not None else sorted(by_slug)
    next_action = _next_action(out_binders, order_view, warnings, errors,
                               archived, default_branch)
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
                 errors: list[str], archived: frozenset[str] = frozenset(),
                 default_branch: str = "main") -> dict:
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
    # 3) no in-flight work — start the next not-started, unblocked binder.
    #    Exception (finding 21): if that binder's declared surface already sits
    #    on the default branch, re-delivering can only whiff on no-change — point
    #    at reviewing and archiving instead, never a re-delivery loop.
    for ob in ordered:
        if ob.get("is_next"):
            if ob.get("surface_on_default"):
                slug = ob["slug"]
                return {"level": "review",
                        "command": (f"mkdir -p .karta/binders/archive && "
                                    f"git mv .karta/binders/{slug}.json "
                                    f".karta/binders/archive/"),
                        "human": (f"{slug}: its declared surface already exists on "
                                  f"{default_branch} — likely delivered outside karta; "
                                  f"review, then archive (rather than re-deliver)")}
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


def default_branch_paths(default_branch: str, runner=None) -> frozenset[str] | None:
    """Every file path tracked on the default branch, from one read-only
    `git ls-tree -r --name-only <branch>`. None when the branch is missing or
    the call fails — so a caller can tell 'no surface information' apart from
    'the surface is empty'. Read-only by construction: it inspects a branch's
    tree, never the working copy and never a ref. `runner` is the injection
    seam the self-test drives."""
    run = runner or subprocess.run
    try:
        proc = run(["git", "ls-tree", "-r", "--name-only", default_branch],
                   capture_output=True, text=True)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return frozenset(line for line in proc.stdout.splitlines() if line)


def _binder_surface_on_default(binder: dict, tree_paths: frozenset[str] | None) -> bool | None:
    """True when every file the binder's items declare in `touches` already
    exists on the default branch, False when at least one is absent, None when
    the question cannot be answered — the tree is unknown, or no item declares
    any touch (an empty surface can never be 'already delivered'). A glob touch
    is present when it matches at least one tracked path; a bare directory touch
    when any tracked path sits under it. Advisory only: the answer is a hint,
    never a state change — no ref is written and nothing is archived."""
    if tree_paths is None:
        return None
    touches: list[str] = []
    for it in binder.get("work_items", []) or []:
        for t in it.get("touches", []) or []:
            if isinstance(t, str) and t.strip():
                touches.append(t.strip())
    if not touches:
        return None
    for t in touches:
        norm = (t[2:] if t.startswith("./") else t).rstrip("/")
        if any(ch in norm for ch in "*?["):
            present = any(fnmatch.fnmatch(p, norm) for p in tree_paths)
        elif norm in tree_paths:
            present = True
        else:
            prefix = norm + "/"
            present = any(p.startswith(prefix) for p in tree_paths)
        if not present:
            return False
    return True


def _surface_hints(binders: list[dict], git_facts: dict, archived: frozenset[str],
                   default_branch: str) -> dict[str, bool | None] | None:
    """The read-only 'already delivered outside karta?' hints for the human
    renders, or None. Computed only when at least one binder derives
    not_started — the only state the hint can fire in — so a repo with no idle
    binder never pays the tree read. None (rather than a partial map) whenever
    the default-branch tree is unknown. Advisory: the result only colours the
    next-action copy, never the derived state (finding 21)."""
    prelim = derive_state(binders, git_facts, archived)
    if not any(b["status"] == "not_started" for b in prelim["binders"]):
        return None
    tree = default_branch_paths(default_branch)
    if tree is None:
        return None
    return {b["slug"]: _binder_surface_on_default(b, tree) for b in binders}


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


def _for_each_ref(args: list[str], runner=None) -> tuple[list[str], bool]:
    """One `git for-each-ref` subprocess, returning (lines, ok). `runner` is the
    injection seam gather_git_facts exposes: swap in a stand-in for
    subprocess.run to count calls or fail one of the three deliberately. `ok`
    is False on any failure (nonzero exit, or a spawn error) so the caller
    degrades the facts that call feeds to unknown instead of raising."""
    run = runner or subprocess.run
    try:
        proc = run(["git", "for-each-ref", *args], capture_output=True, text=True)
    except OSError:
        return [], False
    if proc.returncode != 0:
        return [], False
    return [line for line in proc.stdout.splitlines() if line], True


def gather_git_facts(binders: list[dict], default_branch: str, runner=None) -> dict:
    """Three whole-namespace ref queries answer every binder's and item's git
    facts at once, however many binders or items exist:
      1. every refs/karta/ marker leaf (done/built/failed), one for-each-ref
      2. every refs/heads/karta/ branch (integration + per-item), one for-each-ref
      3. the refs/karta/ subset reachable from default_branch — replacing the
         old per-done-item `merge-base --is-ancestor` exit-code probe; git
         peels annotated tags here exactly as merge-base does
    `runner` is the injection seam: a stand-in for subprocess.run, used to
    count calls (the call-count check) or fail one of the three on purpose
    (the resilience check). Any one query failing degrades only the facts it
    feeds — marked None (unknown), never an exception; the rest of the state
    still renders. `default_branch` not existing (missing/renamed) fails only
    query 3, so done/built/failed/branch stay known even then.

    "Unknown, never false" is a claim about query FAILURE, and there is one
    non-failure case it does not cover: with `--merged` in play git's ref-filter
    silently skips a ref that does not peel to a commit. So a `.../done` marker
    pointing at a blob or a tree appears in query 1 and is absent from query 3 —
    done True, done_in_default False, no error anywhere. That is a false rather
    than an unknown. It is not a regression: the per-item `merge-base
    --is-ancestor` form this replaced returned the same false through a nonzero
    exit. karta only ever points a marker at a commit, so no karta-written ref
    reaches it."""
    markers, markers_ok = _for_each_ref(["--format=%(refname)", "refs/karta/"], runner)
    branches, branches_ok = _for_each_ref(["--format=%(refname)", "refs/heads/karta/"], runner)
    merged, merged_ok = _for_each_ref(
        ["--format=%(refname)", f"--merged={default_branch}", "refs/karta/"], runner)
    marker_set = set(markers) if markers_ok else None
    branch_set = set(branches) if branches_ok else None
    merged_set = set(merged) if merged_ok else None

    facts = {"default_branch": default_branch, "binders": {}}
    for b in binders:
        slug = b["slug"]
        item_ids = [it["id"] for it in b.get("work_items", [])]
        integration = (None if branch_set is None else
                       f"refs/heads/karta/{slug}/integration" in branch_set)
        items = {}
        for i in item_ids:
            base = f"refs/karta/{slug}/item-{i}"
            done = None if marker_set is None else f"{base}/done" in marker_set
            if done is None:
                done_in_default = None
            elif not done:
                done_in_default = False
            elif merged_set is None:
                done_in_default = None
            else:
                done_in_default = f"{base}/done" in merged_set
            items[i] = {
                "done": done,
                "done_in_default": done_in_default,
                "built": None if marker_set is None else f"{base}/built" in marker_set,
                "failed": None if marker_set is None else f"{base}/failed" in marker_set,
                "branch": (None if branch_set is None else
                          f"refs/heads/karta/{slug}/item-{i}" in branch_set),
            }
        facts["binders"][slug] = {"integration_exists": integration, "items": items}
    return facts


# ---------------------------------------------------------------------------
# gather_git_facts self-test support (batch-git-facts). `_reference_git_facts`
# is the ORIGINAL per-binder + per-item walker gather_git_facts replaced —
# kept here, production-dead, purely as the equivalence oracle: one
# for-each-ref per binder, one merge-base --is-ancestor exit-code probe per
# done item. The fixture builders below create real git repos so the oracle
# and the batched form answer the same actual git state.
# ---------------------------------------------------------------------------

def _reference_git_facts(binders: list[dict], default_branch: str) -> dict:
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


def _calls_stay_constant(counts: list[int]) -> bool:
    """The derivation's cost invariant: git calls stay constant as binder count
    grows. `counts` is the git-call count observed at each of several binder
    counts, in increasing order; true only when every one is the same. This is
    strictly stronger than pinning one number on one fixture — it survives the
    batched implementation being replaced by a different call-count-flat one.
    The self-test drives it both ways: the batched derivation must satisfy it,
    and a deliberately per-binder derivation must fail it, which is what makes
    the check itself known to work rather than merely never-seen-to-fail."""
    return len(counts) > 1 and len(set(counts)) == 1


def _git_facts_self_test_checks() -> list[tuple[str, bool]]:
    """batch-git-facts: equivalence across ref topologies, a constant git-call
    count (default-branch resolution included), and graceful degradation on a
    failing/erroring git call. Every check runs against a real git repo built
    with plain git — no mocked ref data — so both the oracle and the batched
    form answer the same actual state."""
    import contextlib, tempfile

    checks: list[tuple[str, bool]] = []

    @contextlib.contextmanager
    def _in_dir(path: Path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def _setup(args: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, check=True, **kw)

    def _mk_repo(path: Path) -> str:
        path.mkdir(parents=True, exist_ok=True)
        _setup(["init", "-q", "-b", "main", "."], path)
        _setup(["config", "user.email", "t@example.com"], path)
        _setup(["config", "user.name", "t"], path)
        (path / "f").write_text("c1")
        _setup(["add", "f"], path)
        _setup(["commit", "-q", "-m", "c1"], path)
        return _setup(["rev-parse", "HEAD"], path).stdout.strip()

    def _wi(iid: str) -> dict:
        return {"id": iid, "title": iid, "oracle": {"type": "unit"}}

    def _binder(slug: str, item_ids: list[str]) -> dict:
        return {"slug": slug, "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [_wi(i) for i in item_ids]}

    def _topology(path: Path, sha: str, n_binders: int, n_items: int,
                 with_integration) -> list[dict]:
        """with_integration: True/False forces it for every binder; None
        alternates per binder index. Items cycle through every presence
        combination (done / built / failed / branch-only / nothing)."""
        binders, updates = [], []
        for bi in range(n_binders):
            slug = f"b{bi}"
            item_ids = [f"i{ii}" for ii in range(n_items)]
            for ii, iid in enumerate(item_ids):
                base = f"refs/karta/{slug}/item-{iid}"
                pat = (bi + ii) % 5
                if pat == 0:
                    updates.append(f"update {base}/done {sha}")
                elif pat == 1:
                    updates.append(f"update {base}/built {sha}")
                elif pat == 2:
                    updates.append(f"update {base}/failed {sha}")
                elif pat == 3:
                    updates.append(f"update refs/heads/karta/{slug}/item-{iid} {sha}")
            wi = with_integration if with_integration is not None else (bi % 2 == 0)
            if wi:
                updates.append(f"update refs/heads/karta/{slug}/integration {sha}")
            binders.append(_binder(slug, item_ids))
        if updates:
            _setup(["update-ref", "--stdin"], path, input="\n".join(updates) + "\n")
        return binders

    def _equivalence(name: str, path: Path, binders: list[dict],
                     default_branch: str = "main") -> None:
        with _in_dir(path):
            ref = _reference_git_facts(binders, default_branch)
            new = gather_git_facts(binders, default_branch)
        checks.append((f"equivalence ({name}): batched == per-item reference walker",
                       ref == new))

    # -- six ref topologies: empty, single, typical (5x10), wide (20x10),
    # no integration branch, with integration branch --
    with tempfile.TemporaryDirectory() as sd:
        root = Path(sd)
        empty = root / "empty"; _mk_repo(empty)
        _equivalence("empty", empty, [])

        single = root / "single"; sha = _mk_repo(single)
        b = _topology(single, sha, 1, 1, True)
        _equivalence("single", single, b)

        typical = root / "typical"; sha = _mk_repo(typical)
        b = _topology(typical, sha, 5, 10, None)
        _equivalence("typical (5x10)", typical, b)

        wide = root / "wide"; sha = _mk_repo(wide)
        b = _topology(wide, sha, 20, 10, None)
        _equivalence("wide (20x10)", wide, b)

        no_int = root / "no-integration"; sha = _mk_repo(no_int)
        b = _topology(no_int, sha, 1, 3, False)
        _equivalence("no integration branch", no_int, b)

        with_int = root / "with-integration"; sha = _mk_repo(with_int)
        b = _topology(with_int, sha, 1, 3, True)
        _equivalence("with integration branch", with_int, b)

        # -- real, multi-commit history with a done ref NOT merged into default --
        hist = root / "history"; sha1 = _mk_repo(hist)
        (hist / "f2").write_text("c2")
        _setup(["add", "f2"], hist); _setup(["commit", "-q", "-m", "c2"], hist)
        sha2 = _setup(["rev-parse", "HEAD"], hist).stdout.strip()
        _setup(["checkout", "-q", "-b", "side", sha1], hist)
        (hist / "f3").write_text("c3")
        _setup(["add", "f3"], hist); _setup(["commit", "-q", "-m", "c3"], hist)
        sha3 = _setup(["rev-parse", "HEAD"], hist).stdout.strip()
        _setup(["checkout", "-q", "main"], hist)
        _setup(["update-ref", "refs/karta/hb/item-merged/done", sha2], hist)
        _setup(["update-ref", "refs/karta/hb/item-unmerged/done", sha3], hist)
        hb = [_binder("hb", ["merged", "unmerged"])]
        with _in_dir(hist):
            ref = _reference_git_facts(hb, "main")
            new = gather_git_facts(hb, "main")
        checks.append(("equivalence (real history, an unmerged done ref): "
                       "both forms agree on done_in_default for every item",
                       ref["binders"]["hb"]["items"]["merged"]["done_in_default"] is True
                       and ref == new
                       and new["binders"]["hb"]["items"]["merged"]["done_in_default"] is True
                       and new["binders"]["hb"]["items"]["unmerged"]["done_in_default"] is False))

        # -- annotated tags: one whose target is merged, one whose target is not.
        # Pins git's tag-peeling behaviour, verified directly on git 2.47.3:
        # for-each-ref --merged peels exactly as merge-base --is-ancestor does. --
        tags = root / "tags"; sha1 = _mk_repo(tags)
        (tags / "f2").write_text("c2")
        _setup(["add", "f2"], tags); _setup(["commit", "-q", "-m", "c2"], tags)
        _setup(["tag", "-a", "-m", "merged", "tm", sha1], tags)
        _setup(["checkout", "-q", "-b", "side", sha1], tags)
        (tags / "f3").write_text("c3")
        _setup(["add", "f3"], tags); _setup(["commit", "-q", "-m", "c3"], tags)
        sha3 = _setup(["rev-parse", "HEAD"], tags).stdout.strip()
        _setup(["tag", "-a", "-m", "not merged", "tnm", sha3], tags)
        _setup(["checkout", "-q", "main"], tags)
        _setup(["update-ref", "refs/karta/tb/item-merged/done", "refs/tags/tm"], tags)
        _setup(["update-ref", "refs/karta/tb/item-notmerged/done", "refs/tags/tnm"], tags)
        tb = [_binder("tb", ["merged", "notmerged"])]
        with _in_dir(tags):
            ref = _reference_git_facts(tb, "main")
            new = gather_git_facts(tb, "main")
        checks.append(("equivalence (annotated tags, one merged target one not): "
                       "both forms agree, peeling the tag exactly as merge-base does",
                       ref == new
                       and new["binders"]["tb"]["items"]["merged"]["done_in_default"] is True
                       and new["binders"]["tb"]["items"]["notmerged"]["done_in_default"] is False))

        # -- call count: a whole state derivation (default-branch resolution
        # included, counted at the subprocess boundary — the real
        # subprocess.run — rather than inside one helper) issues the same
        # fixed number of git subprocesses at 1, 5, 10 and 20 binders --
        cc = root / "callcount"; _mk_repo(cc)
        orig_run = subprocess.run
        binder_counts = (1, 5, 10, 20)
        totals = []
        with _in_dir(cc):
            for n in binder_counts:
                b = [_binder(f"cc{i}", ["a"]) for i in range(n)]
                seen = [0]

                def counting(*a, __seen=seen, **kw):
                    __seen[0] += 1
                    return orig_run(*a, **kw)

                subprocess.run = counting
                try:
                    db = _default_branch()
                    gather_git_facts(b, db)
                finally:
                    subprocess.run = orig_run
                totals.append(seen[0])
        checks.append(("git calls stay constant as binder count grows — whole "
                       "derivation at 1/5/10/20 binders, default-branch "
                       "resolution included, counted at the subprocess boundary",
                       _calls_stay_constant(totals)))

        # -- the same invariant one level down, through gather_git_facts's own
        # runner seam — plus the negative control that proves the check can
        # actually fail. _per_binder_facts is the pre-batch shape (one
        # for-each-ref per binder); the identical harness must report growth
        # for it, or the invariant check above would be untested machinery. --
        def _per_binder_facts(binders: list[dict], default_branch: str,
                              runner=None) -> dict:
            """Deliberately per-binder: one for-each-ref per binder, so its call
            count grows with binder count. The negative control only."""
            for b in binders:
                _for_each_ref(["--format=%(refname)",
                               f"refs/karta/{b['slug']}/"], runner)
            return {}

        def _counts_for(derivation) -> list[int]:
            """Git calls `derivation` issues at each of binder_counts, observed
            through the injectable counting runner both derivations accept."""
            observed = []
            for n in binder_counts:
                b = [_binder(f"cc{i}", ["a"]) for i in range(n)]
                calls = []
                derivation(b, "main", runner=lambda *a, __c=calls, **kw: (
                    __c.append(1), orig_run(*a, **kw))[1])
                observed.append(len(calls))
            return observed

        inv = root / "invariant"; _mk_repo(inv)
        with _in_dir(inv):
            batched_counts = _counts_for(gather_git_facts)
            per_binder_counts = _counts_for(_per_binder_facts)
        checks.append(("git calls stay constant as binder count grows — "
                       "gather_git_facts at 1/5/10/20 binders through its own "
                       "runner seam",
                       _calls_stay_constant(batched_counts)))
        checks.append(("gather_git_facts issues exactly 3 git calls, at every "
                       "binder count",
                       batched_counts == [3, 3, 3, 3]))
        checks.append(("negative control: the same invariant check FAILS on a "
                       "deliberately per-binder derivation, so it is known to "
                       "detect the regression it guards",
                       per_binder_counts == [1, 5, 10, 20]
                       and not _calls_stay_constant(per_binder_counts)))

        # -- failure injection: one batched call failing degrades only the
        # facts it feeds, marked None — the rest of the page still renders --
        fi = root / "failinj"; sha = _mk_repo(fi)
        _setup(["update-ref", "refs/karta/fi/item-a/done", sha], fi)
        fib = [_binder("fi", ["a"])]
        with _in_dir(fi):
            n = [0]

            def fail_third(*a, __n=n, **kw):
                __n[0] += 1
                if __n[0] == 3:
                    return subprocess.CompletedProcess(a[0], returncode=128,
                                                        stdout="", stderr="injected")
                return orig_run(*a, **kw)

            facts = gather_git_facts(fib, "main", runner=fail_third)
            item = facts["binders"]["fi"]["items"]["a"]
            checks.append(("failure injection: unaffected facts (done/built/failed/"
                           "branch) still populate when only the merged-set call fails",
                           item["done"] is True and item["built"] is False
                           and item["failed"] is False and item["branch"] is False))
            checks.append(("failure injection: the affected fact (done_in_default) "
                           "degrades to None, never raises",
                           item["done_in_default"] is None))

            def fail_all(*a, **kw):
                return subprocess.CompletedProcess(a[0], returncode=1,
                                                    stdout="", stderr="injected")

            facts_all = gather_git_facts(fib, "main", runner=fail_all)
            item_all = facts_all["binders"]["fi"]["items"]["a"]
            checks.append(("failure injection: every batched call failing yields "
                           "all-unknown facts for the item, never raises",
                           item_all["done"] is None and item_all["built"] is None
                           and item_all["failed"] is None and item_all["branch"] is None
                           and item_all["done_in_default"] is None
                           and facts_all["binders"]["fi"]["integration_exists"] is None))

            state = derive_state(fib, facts_all)
            try:
                term = render_terminal(state)
                foot = render_footer(state, "fi")
                rendered_ok = bool(term) and bool(foot)
            except Exception:                                     # noqa: BLE001
                rendered_ok = False
            checks.append(("failure injection: a state carrying unknown facts still "
                           "renders a page (terminal + footer), not blank or raising",
                           rendered_ok))

            def boom(*a, **kw):
                raise OSError("git not found")

            facts_boom = gather_git_facts(fib, "main", runner=boom)
            item_boom = facts_boom["binders"]["fi"]["items"]["a"]
            checks.append(("failure injection: a spawn OSError degrades to unknown "
                           "facts too, never raises",
                           item_boom["done"] is None and item_boom["done_in_default"] is None))

        # -- a refs/karta/ entry pointing at a non-commit object (a blob) does
        # not break the derivation --
        blob = root / "blob"; sha = _mk_repo(blob)
        blob_oid = _setup(["hash-object", "-w", "--stdin"], blob,
                          input="not a commit").stdout.strip()
        _setup(["update-ref", "refs/karta/bl/item-a/done", blob_oid], blob)
        blb = [_binder("bl", ["a"])]
        with _in_dir(blob):
            try:
                facts = gather_git_facts(blb, "main")
                ok = True
            except Exception:                                     # noqa: BLE001
                ok = False
        checks.append(("a refs/karta/ entry on a non-commit object (a blob) does "
                       "not break the derivation",
                       ok and facts["binders"]["bl"]["items"]["a"]["done"] is True))

        # -- empty repository / unborn HEAD: no commits, no refs, no raise --
        emptyrepo = root / "emptyrepo"
        emptyrepo.mkdir(parents=True, exist_ok=True)
        _setup(["init", "-q", "-b", "main", "."], emptyrepo)
        eb = [_binder("e", ["a"])]
        with _in_dir(emptyrepo):
            try:
                db = _default_branch()
                facts = gather_git_facts(eb, db)
                ok = True
            except Exception:                                     # noqa: BLE001
                ok = False
        checks.append(("empty repository / unborn HEAD: default-branch resolution "
                       "and gather_git_facts both survive, no raise",
                       ok and facts["binders"]["e"]["items"]["a"]["done"] is False
                       and facts["binders"]["e"]["items"]["a"]["done_in_default"] is False))

        # -- detached HEAD does not break the derivation --
        detached = root / "detached"; sha = _mk_repo(detached)
        _setup(["checkout", "-q", "--detach", sha], detached)
        db_ = [_binder("d", ["a"])]
        with _in_dir(detached):
            try:
                facts = gather_git_facts(db_, "main")
                ok = True
            except Exception:                                     # noqa: BLE001
                ok = False
        checks.append(("detached HEAD does not break the derivation",
                       ok and facts["binders"]["d"]["items"]["a"]["done"] is False))

        # -- a missing or renamed default branch: query 3 fails, but query 1/2
        # data (done/built/failed/branch) stays known; only done_in_default
        # for a done item degrades to unknown --
        missing = root / "missingdefault"; sha = _mk_repo(missing)
        _setup(["update-ref", "refs/karta/md/item-a/done", sha], missing)
        mdb = [_binder("md", ["a"])]
        with _in_dir(missing):
            try:
                facts = gather_git_facts(mdb, "trunk-does-not-exist")
                ok = True
            except Exception:                                     # noqa: BLE001
                ok = False
            item = facts["binders"]["md"]["items"]["a"] if ok else {}
        checks.append(("missing/renamed default branch: no raise, done stays known, "
                       "only done_in_default degrades to unknown",
                       ok and item.get("done") is True
                       and item.get("done_in_default") is None))

    return checks


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

    # surface-on-default hint (finding 21): a not_started binder whose declared
    # touches all already exist on the default branch is flagged, and the next
    # action points at archiving rather than a re-delivery that can only whiff.
    surf_binder = {"slug": "landed-elsewhere", "motivation": "x",
                   "scope": {"included": ["x"]},
                   "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"},
                                   "touches": ["app/models.py", "app/views/*.py"]}]}
    present = frozenset({"app/models.py", "app/views/list.py"})
    absent = frozenset({"app/models.py"})
    checks.append(("surface present: every touch on default -> True",
                   _binder_surface_on_default(surf_binder, present) is True))
    checks.append(("surface absent: a glob with no match -> False",
                   _binder_surface_on_default(surf_binder, absent) is False))
    checks.append(("surface unknown when the tree is None",
                   _binder_surface_on_default(surf_binder, None) is None))
    checks.append(("no declared touches -> unknown (never claim delivered)",
                   _binder_surface_on_default(
                       {"slug": "z", "work_items": [{"id": "a"}]}, present) is None))
    checks.append(("bare directory touch present when a file sits under it",
                   _binder_surface_on_default(
                       {"work_items": [{"touches": ["app/views"]}]}, present) is True))
    surf_facts = {"default_branch": "main",
                  "binders": {"landed-elsewhere": {"items": {"a": {}}}}}
    surf_state = derive_state([surf_binder], surf_facts,
                              surface_on_default={"landed-elsewhere": True})
    surf_row = surf_state["binders"][0]
    checks.append(("not_started binder is flagged surface_on_default and stays is_next",
                   surf_row["status"] == "not_started"
                   and surf_row.get("surface_on_default") is True
                   and surf_row["is_next"] is True))
    surf_na = surf_state["next_action"]
    checks.append(("next action redirects to archive, never re-deliver",
                   "karta-deliver" not in (surf_na["command"] or "")
                   and "archive" in surf_na["command"]
                   and "already exists on main" in surf_na["human"]
                   and surf_na["level"] == "review"))
    plain_state = derive_state([surf_binder], surf_facts)
    checks.append(("no surface signal -> unchanged 'start' recommendation",
                   plain_state["next_action"]["command"] == "karta-deliver landed-elsewhere"
                   and plain_state["binders"][0].get("surface_on_default") is None))
    # default_branch_paths against a real repo: lists tracked files, None on a
    # missing branch — the read-only tree query the hint is built on.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _r = Path(_td)
        _mk = lambda *a: subprocess.run(["git", *a], cwd=str(_r),
                                        capture_output=True, text=True, check=True)
        _mk("init", "-q", "-b", "main", ".")
        _mk("config", "user.email", "t@example.com")
        _mk("config", "user.name", "t")
        (_r / "app").mkdir()
        (_r / "app" / "models.py").write_text("x")
        (_r / "app" / "list.py").write_text("y")
        _mk("add", "-A"); _mk("commit", "-q", "-m", "c")
        _old = os.getcwd(); os.chdir(_r)
        try:
            tracked = default_branch_paths("main")
            missing = default_branch_paths("no-such-branch")
        finally:
            os.chdir(_old)
    checks.append(("default_branch_paths lists the branch's tracked files",
                   tracked is not None and "app/models.py" in tracked
                   and "app/list.py" in tracked))
    checks.append(("default_branch_paths returns None for a missing branch",
                   missing is None))

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

    checks.extend(_git_facts_self_test_checks())
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
    default_branch = _default_branch()
    git_facts = gather_git_facts(binders, default_branch)
    surface = _surface_hints(binders, git_facts, archived, default_branch)
    state = derive_state(binders, git_facts, archived, surface_on_default=surface)
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
