#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex Stop guard: a delivery may not quietly end dirty.

Codex twin of hooks/scripts/guard_delivery_stop.py (the Claude Code guard). The
detection logic is identical — it inspects repo state only, never the
transcript — for two dirty-delivery states across the live binders
(`.karta/binders/*.json` in the working tree or in HEAD, never `archive/`):

- built-unmerged: some `refs/karta/<slug>/item-<id>/built` ref (found by ref
  glob, so an orphaned ref still counts) has no matching `done` and no matching
  `failed` — the serial merge queue never finished.
- complete-unarchived: the binder has at least one work item, every item has a
  `done` ref, and the archive file `.karta/binders/archive/<slug>.json` is not
  committed anywhere it counts (HEAD, `refs/heads/karta/<slug>/integration`, or
  the default branch). On disk or staged but uncommitted does not count.

Two Codex adaptations, both in the harness contract only:

- A payload without a `hook_event_name` still counts as a Stop — this script is
  only ever wired on the Stop event, and requiring the field would silently kill
  the gate on a build that omits it. An explicit non-Stop event, or a payload
  carrying subagent identity fields, still passes untouched.
- On a finding it emits BOTH block channels: `{"decision": "block", "reason":
  ...}` on stdout (the documented Codex Stop decision) and the reason on stderr
  with exit 2 (the exit-code contract). Whichever the host honors, the result is
  the same block.

The block fires at most once per (session, state): a fingerprint of the findings
is recorded in `<git-common-dir>/karta-stop-gate.json` (shared with the Claude
twin — sessions never collide) and an identical later stop passes — a nudge, not
a wall. A standing `failed` ref is a designed resting state, never a finding.
This guard is FAIL-OPEN and corrective: any genuinely unexpected internal error
exits 0, because a stray Stop trap is strictly worse than a missed nudge. Keep
the detection semantics in step with the Claude twin — the two files are
maintained by hand, not generated.

  guard_delivery_stop.py              # hook mode: payload on stdin, exit 0/2
  guard_delivery_stop.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

SENTINEL_NAME = "karta-stop-gate.json"
SENTINEL_MAX_SESSIONS = 20
REF_STATES = ("built", "done", "failed")

BUILT_UNMERGED_MSG = (
    "binder {slug}: items {ids} carry built but no done — the serial merge queue "
    "did not finish. Re-enter karta-deliver's merge step: for each, re-validate "
    "its oracle against the current integration tip, merge FIFO, write the done "
    "ref; or tell the user plainly that the delivery is stopping mid-wave and "
    "how to resume.")
COMPLETE_UNARCHIVED_MSG = (
    "binder {slug}: all items are done but the binder was never archived. Run "
    "the end-of-life step (deliver:archive / karta-build 9c-single): git mv it "
    "to .karta/binders/archive/{slug}.json and commit on the integration branch.")


def _git(repo: str | Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _repo_root(cwd: str) -> str | None:
    if not os.path.isdir(cwd):
        return None
    r = _git(cwd, "rev-parse", "--show-toplevel")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _live_slugs(root: str) -> list[str]:
    """Live binder slugs: union of working tree and HEAD, never archive/."""
    slugs: set[str] = set()
    binders = Path(root) / ".karta" / "binders"
    if binders.is_dir():
        slugs.update(f.stem for f in binders.glob("*.json"))
    r = _git(root, "ls-tree", "--name-only", "HEAD", ".karta/binders/")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            name = line.strip().rsplit("/", 1)[-1]
            if name.endswith(".json"):
                slugs.add(name[: -len(".json")])
    return sorted(slugs)


def _binder_item_ids(root: str, slug: str) -> list[str] | None:
    """Work-item ids from the live binder — working tree first, else the HEAD
    blob (a crash between the archive `git mv` and its commit removes the file
    from disk while the archival is not yet real). None = unreadable/malformed
    (fail open for this binder)."""
    live = Path(root) / ".karta" / "binders" / f"{slug}.json"
    if live.is_file():
        try:
            raw = live.read_text()
        except OSError:
            return None
    else:
        r = _git(root, "cat-file", "blob", f"HEAD:.karta/binders/{slug}.json")
        if r.returncode != 0:
            return None
        raw = r.stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    items = data.get("work_items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    ids = [it.get("id") for it in items if isinstance(it, dict)]
    if len(ids) != len(items) or not all(isinstance(i, str) for i in ids):
        return None
    return ids


def _slug_ref_states(root: str, slug: str) -> dict[str, set[str]]:
    """item-id -> set of standing states, read via plumbing (never .git/refs/
    files, which go silent once refs are packed)."""
    prefix = f"refs/karta/{slug}/item-"
    states: dict[str, set[str]] = {}
    r = _git(root, "for-each-ref", "--format=%(refname)", f"refs/karta/{slug}/")
    if r.returncode != 0:
        return states
    for ref in r.stdout.splitlines():
        if not ref.startswith(prefix):
            continue
        item, sep, state = ref[len(prefix):].rpartition("/")
        if sep and item and state in REF_STATES:
            states.setdefault(item, set()).add(state)
    return states


def _default_branch_revs(root: str) -> list[str]:
    r = _git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        name = r.stdout.strip().rsplit("/", 1)[-1]
        return [f"refs/heads/{name}", f"refs/remotes/origin/{name}"]
    for name in ("main", "master"):
        ref = f"refs/heads/{name}"
        if _git(root, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return [ref]
    return []


def _archive_committed(root: str, slug: str) -> bool:
    """Is the archive file committed anywhere it counts? A failing cat-file /
    missing branch is an expected negative feeding the finding, not an error."""
    path = f".karta/binders/archive/{slug}.json"
    revs = ["HEAD", f"refs/heads/karta/{slug}/integration", *_default_branch_revs(root)]
    return any(_git(root, "cat-file", "-e", f"{rev}:{path}").returncode == 0
               for rev in revs)


def _sentinel_path(root: str) -> Path | None:
    r = _git(root, "rev-parse", "--git-common-dir")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = Path(root) / common
    return common / SENTINEL_NAME


def _load_sentinel(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)}


def _record_nudge(path: Path, sessions: dict[str, str],
                  session_id: str, fingerprint: str) -> None:
    """Record session -> fingerprint, pruned to the newest SENTINEL_MAX_SESSIONS,
    written atomically via a temp file + os.replace."""
    sessions.pop(session_id, None)
    sessions[session_id] = fingerprint
    while len(sessions) > SENTINEL_MAX_SESSIONS:
        del sessions[next(iter(sessions))]
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=SENTINEL_NAME + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(sessions, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def block_json(reason: str) -> str:
    """The documented Codex Stop block decision, emitted on stdout beside the
    exit-2 + stderr contract."""
    return json.dumps({"decision": "block", "reason": reason})


def decide(payload: object) -> tuple[int, str]:
    """Return (exit_code, stderr_reason)."""
    if not isinstance(payload, dict):
        return 0, ""
    event = payload.get("hook_event_name")
    if event is not None and event != "Stop":
        return 0, ""  # SubagentStop or anything else is not this guard's event
    if "agent_type" in payload or "agent_id" in payload:
        return 0, ""  # subagent-shaped payload — not a main-session stop
    if payload.get("stop_hook_active"):
        return 0, ""  # harness-documented loop guard
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str):
        return 0, ""  # missing fields — fail open
    root = _repo_root(cwd)
    if root is None:
        return 0, ""  # not a git repo — nothing to check
    findings: list[tuple[str, str, tuple[str, ...]]] = []
    messages: list[str] = []
    for slug in _live_slugs(root):
        ids = _binder_item_ids(root, slug)
        if ids is None:
            continue  # unreadable/malformed binder — fail open for this slug
        states = _slug_ref_states(root, slug)
        stranded = sorted(item for item, st in states.items()
                          if "built" in st and not st & {"done", "failed"})
        if stranded:
            findings.append((slug, "built-unmerged", tuple(stranded)))
            messages.append(BUILT_UNMERGED_MSG.format(
                slug=slug, ids=", ".join(stranded)))
        if ids and all("done" in states.get(i, ()) for i in ids) \
                and not _archive_committed(root, slug):
            findings.append((slug, "complete-unarchived", tuple(sorted(ids))))
            messages.append(COMPLETE_UNARCHIVED_MSG.format(slug=slug))
    if not findings:
        return 0, ""
    fingerprint = hashlib.sha256(
        json.dumps(sorted(findings)).encode()).hexdigest()
    sentinel = _sentinel_path(root)
    if sentinel is None:
        return 0, ""  # cannot do block-once safely — fail open
    sessions = _load_sentinel(sentinel)
    if sessions.get(session_id) == fingerprint:
        return 0, ""  # already nudged for exactly this state
    _record_nudge(sentinel, sessions, session_id, fingerprint)
    return 2, (
        "karta: this session is stopping with a dirty delivery. "
        + " ".join(messages)
        + " This stop is blocked once per state — an identical stop will pass, "
          "so fix it now or stop again to defer to the resume flow.")


def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, payload: object, want: int) -> str:
        code, reason = decide(payload)
        ok = (code == want and (want == 0) == (reason == "")
              and (want == 0 or reason.startswith("karta: ")))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
        results.append(ok)
        return reason

    def flag(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    def git(repo: Path, *a: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=karta@test",
             "-c", "user.name=karta", *a], capture_output=True, text=True)

    def init_repo(td: str, name: str) -> Path:
        repo = Path(td) / name
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "README.md").write_text("seed\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "seed")
        return repo

    def write_binder(repo: Path, slug: str, ids: list[str],
                     malformed: bool = False, commit: bool = True) -> None:
        d = repo / ".karta" / "binders"
        d.mkdir(parents=True, exist_ok=True)
        body = "{not json" if malformed else json.dumps(
            {"slug": slug, "work_items": [{"id": i} for i in ids]})
        (d / f"{slug}.json").write_text(body)
        if commit:
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", f"binder {slug}")

    def set_ref(repo: Path, slug: str, item: str, state: str) -> None:
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "update-ref", f"refs/karta/{slug}/item-{item}/{state}", head)

    def stop(repo_or_dir: Path, session: str = "s1", **over: object) -> dict:
        payload: dict = {"hook_event_name": "Stop", "session_id": session,
                         "cwd": str(repo_or_dir), "stop_hook_active": False}
        payload.update(over)
        return payload

    def dirty_repo(td: str, name: str) -> Path:
        """built-unmerged: item a merged, item b built with no done/failed."""
        repo = init_repo(td, name)
        write_binder(repo, "wip", ["a", "b"])
        set_ref(repo, "wip", "a", "built")
        set_ref(repo, "wip", "a", "done")
        set_ref(repo, "wip", "b", "built")
        return repo

    def complete_repo(td: str, name: str, slug: str = "comp") -> Path:
        """all items done, nothing archived yet."""
        repo = init_repo(td, name)
        write_binder(repo, slug, ["a"])
        set_ref(repo, slug, "a", "built")
        set_ref(repo, slug, "a", "done")
        return repo

    with tempfile.TemporaryDirectory() as td:
        # 1. no live binder -> allow
        repo = init_repo(td, "clean")
        check("no live binder allows", stop(repo), 0)

        # 2. archived-only slug with surviving refs -> allow
        repo = init_repo(td, "archived")
        d = repo / ".karta" / "binders" / "archive"
        d.mkdir(parents=True)
        (d / "old.json").write_text(json.dumps(
            {"slug": "old", "work_items": [{"id": "a"}]}))
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "archive old")
        set_ref(repo, "old", "a", "built")
        check("archived-only slug with surviving refs allows", stop(repo), 0)

        # 3. in-flight binder, no built standing -> allow
        repo = init_repo(td, "inflight")
        write_binder(repo, "wip", ["a", "b"])
        set_ref(repo, "wip", "a", "done")
        check("in-flight binder with no built standing allows", stop(repo), 0)

        # 4. built-unmerged -> block naming slug + stranded items
        repo = dirty_repo(td, "built1")
        reason = check("built-unmerged blocks", stop(repo), 2)
        flag("block reason names the slug and the stranded item",
             "binder wip" in reason and "items b carry built" in reason)

        # 5. built + failed, no done -> allow (parked four-way choice)
        repo = init_repo(td, "parked")
        write_binder(repo, "wip", ["a"])
        set_ref(repo, "wip", "a", "built")
        set_ref(repo, "wip", "a", "failed")
        check("item with built and failed but no done allows (parked)",
              stop(repo), 0)

        # 6. empty work_items -> allow (vacuously complete, never a finding)
        repo = init_repo(td, "empty")
        write_binder(repo, "hollow", [])
        check("empty work_items allows", stop(repo), 0)

        # 7. complete-unarchived, integration branch exists, no archive -> block
        repo = complete_repo(td, "comp7")
        git(repo, "branch", "karta/comp/integration")
        reason = check("complete-unarchived blocks (no archive anywhere)",
                       stop(repo), 2)
        flag("block reason names the slug and the archive fix",
             "binder comp" in reason and "never archived" in reason)

        # 8. complete-unarchived with no integration branch at all -> block
        repo = complete_repo(td, "comp8")
        check("complete-unarchived blocks with no integration branch "
              "(missing branch is a negative, not an error)", stop(repo), 2)

        # 9. archive git mv'd but uncommitted -> block (live binder in HEAD)
        repo = complete_repo(td, "comp9")
        (repo / ".karta" / "binders" / "archive").mkdir()
        git(repo, "mv", ".karta/binders/comp.json",
            ".karta/binders/archive/comp.json")
        check("archive mv'd but uncommitted still blocks", stop(repo), 2)

        # 10. archive committed on the integration branch only -> allow
        repo = complete_repo(td, "comp10")
        git(repo, "checkout", "-q", "-b", "karta/comp/integration")
        (repo / ".karta" / "binders" / "archive").mkdir()
        git(repo, "mv", ".karta/binders/comp.json",
            ".karta/binders/archive/comp.json")
        git(repo, "commit", "-q", "-m", "archive comp")
        git(repo, "checkout", "-q", "main")
        check("archive committed on integration branch only allows",
              stop(repo), 0)

        # 11. complete with archive in HEAD (live copy also present) -> allow
        repo = complete_repo(td, "comp11")
        d = repo / ".karta" / "binders" / "archive"
        d.mkdir()
        (d / "comp.json").write_text(
            (repo / ".karta" / "binders" / "comp.json").read_text())
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "archive comp (copy in HEAD)")
        check("archive present in HEAD allows", stop(repo), 0)

        # 12. archive merged to default branch, stale feature checkout -> allow
        repo = complete_repo(td, "comp12")
        git(repo, "branch", "feature")
        (repo / ".karta" / "binders" / "archive").mkdir()
        git(repo, "mv", ".karta/binders/comp.json",
            ".karta/binders/archive/comp.json")
        git(repo, "commit", "-q", "-m", "archive comp on main")
        git(repo, "checkout", "-q", "feature")
        check("archive merged to default branch allows under a stale "
              "feature-branch checkout", stop(repo), 0)

        # 13. two live binders with mixed findings -> one block covering both
        repo = dirty_repo(td, "mixed")
        write_binder(repo, "comp", ["a"])
        set_ref(repo, "comp", "a", "done")
        reason = check("two live binders with mixed findings block once",
                       stop(repo), 2)
        flag("one reason covers both findings",
             "binder wip" in reason and "binder comp" in reason)
        check("second stop, same session and fingerprint covering both, allows",
              stop(repo), 0)

        # 14. payload cwd in a subdirectory -> still detects
        repo = dirty_repo(td, "subdir")
        sub = repo / "docs"
        sub.mkdir()
        check("payload cwd in a subdirectory still detects", stop(sub), 2)

        # 15. subagent-shaped payloads -> allow, even in a dirty repo
        repo = dirty_repo(td, "subagent")
        check("SubagentStop-shaped payload allows",
              stop(repo, hook_event_name="SubagentStop",
                   agent_type="karta:karta-build"), 0)
        check("Stop payload carrying subagent fields allows",
              stop(repo, agent_id="abc123"), 0)

        # 16. stop_hook_active true -> allow, even in a dirty repo
        check("stop_hook_active true allows", stop(repo, stop_hook_active=True), 0)

        # 17. Codex adaptation: no hook_event_name still counts as a Stop
        repo = dirty_repo(td, "noevent")
        payload = stop(repo, session="ne1")
        del payload["hook_event_name"]
        del payload["stop_hook_active"]
        check("payload without hook_event_name still blocks on a dirty repo",
              payload, 2)

        # 18./19./20. block-once per (session, state)
        repo = dirty_repo(td, "sessions")
        check("first stop on a dirty state blocks", stop(repo, session="s1"), 2)
        check("same session + same fingerprint allows on the second call",
              stop(repo, session="s1"), 0)
        set_ref(repo, "wip", "c", "built")  # orphaned ref still counts
        check("same session + changed fingerprint blocks again",
              stop(repo, session="s1"), 2)
        check("new session + same state blocks once", stop(repo, session="s2"), 2)
        check("new session + same state allows on its second call",
              stop(repo, session="s2"), 0)

        # 21. malformed binder JSON -> allow (fail-open)
        repo = init_repo(td, "malformed")
        write_binder(repo, "broken", [], malformed=True)
        check("malformed binder JSON allows (fail-open)", stop(repo), 0)

        # 22. non-git cwd -> allow
        plain = Path(td) / "plain"
        plain.mkdir()
        check("non-git cwd allows", stop(plain), 0)

        # 23. sentinel pruning at SENTINEL_MAX_SESSIONS sessions
        repo = dirty_repo(td, "prune")
        for n in range(1, 26):
            decide(stop(repo, session=f"p{n}"))
        sentinel = _sentinel_path(str(repo))
        sessions = _load_sentinel(sentinel) if sentinel else {}
        flag("sentinel is pruned to 20 sessions, oldest first",
             len(sessions) == SENTINEL_MAX_SESSIONS
             and "p1" not in sessions and "p5" not in sessions
             and "p6" in sessions and "p25" in sessions)

        # 24. the stdout block decision is well-formed JSON
        parsed = json.loads(block_json("fix the delivery"))
        flag("block_json emits decision=block with the reason",
             parsed == {"decision": "block", "reason": "fix the delivery"})

    total = len(results)
    failures = results.count(False)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        payload = json.load(sys.stdin)
        code, reason = decide(payload)
    except Exception:  # noqa: BLE001
        return 0  # fail open: a stray Stop trap is worse than a missed nudge
    if code == 2:
        print(block_json(reason))          # Codex Stop decision channel
        print(reason, file=sys.stderr)     # exit-code channel
    return code


if __name__ == "__main__":
    sys.exit(main())
