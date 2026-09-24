#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse guard: committed binders are read-only — live and archived.

Zero dependencies (pure stdlib). The harness invokes this on Write|Edit|NotebookEdit
with the hook payload JSON on stdin. If the target path is a binder
(`.karta/binders/*.json`, or a delivered one under `.karta/binders/archive/`) that
already exists in HEAD, the write is denied — exit 2 with the reason on stderr. The
target is judged by its lexical name, its symlink-resolved name, and — for a regular
file with more than one link — its inode, so a hard link to a committed binder is
denied like the binder itself. A
committed live binder is the plan of record karta-deliver derives run state from, so
mutating it mid-flight desynchronizes the run; an archived binder is delivered
history and is never edited. Untracked binder writes (plan drafting) pass. Any
internal error fails open (exit 0): this guard must never break an unrelated tool
call.

  guard_binder_immutability.py              # hook mode: payload on stdin, exit 0/2
  guard_binder_immutability.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, stat, subprocess, sys
from pathlib import Path

def _read_stdin_text() -> str:
    """The hook payload is UTF-8 JSON, whatever the host's locale codec is.

    sys.stdin decodes with the locale codec — cp1252 on a stock Windows
    session — which mojibakes or raises on a payload it cannot spell. Read
    the byte stream and decode explicitly; the getattr falls back for test
    doubles that carry no .buffer."""
    data = getattr(sys.stdin, "buffer", sys.stdin).read()
    return data.decode("utf-8") if isinstance(data, bytes) else data

BINDER_RE = re.compile(r"(?:^|/)\.karta/binders/(?:archive/)?[^/]+\.json$")


def _os_spelling(path: str, cwd: str) -> str:
    """Resolve dot segments and existing symlinks before classifying a target."""
    target = os.path.realpath(os.path.join(cwd or os.getcwd(), path))
    return os.path.normcase(target).replace(os.sep, "/")


def _binder_targets(path: str, cwd: str) -> set[str]:
    # Keep the lexical name too: overwriting a committed binder that is itself
    # a symlink must not become legal merely because its target is elsewhere.
    lexical = os.path.normcase(os.path.abspath(os.path.join(cwd or os.getcwd(), path)))
    return {p for p in (lexical.replace(os.sep, "/"), _os_spelling(path, cwd))
            if BINDER_RE.search(p)}


def _repo_top(start: str) -> str | None:
    top = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, encoding="utf-8")
    return top.stdout.strip() if top.returncode == 0 and top.stdout.strip() else None


def _worktrees(top: str) -> list[str]:
    """`top` plus every worktree of its repository (`git worktree list --porcelain`)."""
    try:
        out = subprocess.run(["git", "-C", top, "worktree", "list", "--porcelain"],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return [top]
    if out.returncode != 0:
        return [top]
    lines = out.stdout.decode("utf-8", errors="surrogateescape").splitlines()
    return [top, *(line[len("worktree "):] for line in lines if line.startswith("worktree "))]


def _hardlinked_binder(path: str, cwd: str) -> bool:
    """Is `path` a hard link to a binder committed in HEAD?

    realpath collapses symlinks, never hard links: a second name for the binder's
    inode classifies as an ordinary file. So when the resolved target is a regular
    file with more than one link, compare its (st_dev, st_ino) with every binder
    HEAD tracks — live and archived — in every worktree of the repository of the
    working directory and of the target, and apply the binder rule on a match. A
    linked worktree holds its own copy of each tracked file, so a hard link made in
    one worktree to another worktree's binder matches only in that other worktree."""
    target = os.path.realpath(os.path.join(cwd or os.getcwd(), path))
    try:
        st = os.stat(target)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_nlink < 2:
        return False
    tops = {t for t in (_repo_top(os.path.dirname(target)), _repo_top(cwd or os.getcwd())) if t}
    tops = {os.path.realpath(w) for t in tops for w in _worktrees(t)}
    for top in sorted(tops):
        listing = subprocess.run(["git", "-C", top, "ls-tree", "-r", "-z", "--name-only", "HEAD"],
                                 capture_output=True, timeout=10)
        if listing.returncode != 0:
            continue
        for rel in listing.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
            if not rel or not BINDER_RE.search(rel):
                continue
            try:
                other = os.stat(os.path.join(top, rel))
            except OSError:
                continue
            if (other.st_dev, other.st_ino) == (st.st_dev, st.st_ino):
                return True
    return False


def _committed_binder(path: str, cwd: str, tracked) -> bool:
    return (any(tracked(p, cwd) for p in _binder_targets(path, cwd))
            or _hardlinked_binder(path, cwd))


def _target_path(tool_input: dict) -> str | None:
    for key in ("file_path", "notebook_path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _tracked_in_head(path: str, cwd: str) -> bool:
    """Is `path` (as the tool call names it) a blob in HEAD of its repo?"""
    abs_path = Path(os.path.abspath(os.path.join(cwd, path)))
    base = str(abs_path.parent) if abs_path.parent.is_dir() else cwd
    top = subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, encoding="utf-8")
    if top.returncode != 0:
        return False  # not a repo (or no git): nothing is committed here
    toplevel = Path(top.stdout.strip()).resolve()
    rel = os.path.relpath(abs_path, toplevel)
    if rel.startswith(".."):
        return False  # outside the repo the tool call runs in
    out = subprocess.run(["git", "-C", str(toplevel), "ls-tree", "HEAD", "--", rel],
                         capture_output=True, text=True, encoding="utf-8")
    return out.returncode == 0 and bool(out.stdout.strip())


def decide(payload: dict, tracked=_tracked_in_head) -> tuple[int, str]:
    """Return (exit_code, stderr_reason). `tracked` is injectable for the self-test."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    target = _target_path(tool_input)
    cwd = payload.get("cwd") or os.getcwd()
    if not target:
        return 0, ""
    if not _committed_binder(target, cwd, tracked):
        return 0, ""  # untracked draft — plan-time binder writing is allowed
    return 2, (
        f"karta: committed binders are read-only. '{target}' already exists in HEAD, and a "
        "committed binder is the plan of record — karta-deliver derives the whole run's state "
        "from it plus its git refs, so mutating it mid-flight desynchronizes the run and its "
        "resume story; an archived binder (.karta/binders/archive/) is delivered history and "
        "is never edited. To change the plan, re-plan with karta-plan (which writes a fresh "
        "binder file) or draft a new, not-yet-committed binder; writes to untracked binder "
        "drafts are not blocked. If nothing has been delivered from this binder and the plan "
        "is simply wrong — a retroactive review rejected it, say — withdraw it: commit the "
        "deletion of this file, then commit the corrected plan (the same slug is fine) with "
        "its review record. Never rewrite history to edit a binder in place.")


def _run_self_test() -> int:
    tracked = lambda path, cwd: True     # noqa: E731
    untracked = lambda path, cwd: False  # noqa: E731

    def pre(tool: str, **ti) -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": tool,
                "cwd": "/tmp", "tool_input": ti}

    cases = [
        ("non-binder write passes",
         pre("Write", file_path="src/app.py", content="x"), tracked, 0),
        ("tracked binder write denied",
         pre("Write", file_path=".karta/binders/checkout.json", content="{}"), tracked, 2),
        ("tracked binder edit denied (absolute path)",
         pre("Edit", file_path="/repo/.karta/binders/checkout.json",
             old_string="a", new_string="b"), tracked, 2),
        ("tracked binder notebook edit denied",
         pre("NotebookEdit", notebook_path=".karta/binders/checkout.json"), tracked, 2),
        ("untracked binder draft passes",
         pre("Write", file_path=".karta/binders/new-plan.json", content="{}"), untracked, 0),
        ("non-json under binders passes",
         pre("Write", file_path=".karta/binders/notes.md", content="x"), tracked, 0),
        ("binder-like path elsewhere passes",
         pre("Write", file_path="docs/karta/binders-history.json", content="x"), tracked, 0),
        ("nested binder dir path still matches",
         pre("Write", file_path="sub/.karta/binders/x.json", content="{}"), tracked, 2),
        ("tracked archived binder write denied",
         pre("Write", file_path=".karta/binders/archive/done.json", content="{}"), tracked, 2),
        ("untracked archive draft passes",
         pre("Write", file_path=".karta/binders/archive/new.json", content="{}"), untracked, 0),
        ("deeper subdir under binders passes (only archive/ is a binder home)",
         pre("Write", file_path=".karta/binders/archive/nested/x.json", content="{}"), tracked, 0),
        ("no target path passes", pre("Write", content="x"), tracked, 0),
        ("tool_input not a dict passes",
         {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": "junk"},
         tracked, 0),
    ]
    if os.name == "nt":
        # The spellings Windows resolves to the protected file while a literal
        # match waves them through. 8.3 short names (KARTA~1) are covered by the
        # same realpath call but need the component to exist, so they have no
        # portable fixture here — the probe above is the evidence.
        cases += [
            ("Windows: case-variant spelling reaches the same binder — denied",
             pre("Write", file_path=".Karta/Binders/checkout.json", content="{}"),
             tracked, 2),
            ("Windows: a trailing dot is stripped by the OS, not by the pattern — denied",
             pre("Write", file_path=".karta/binders/checkout.json.", content="{}"),
             tracked, 2),
            ("Windows: a trailing space likewise — denied",
             pre("Write", file_path=".karta/binders/checkout.json ", content="{}"),
             tracked, 2),
        ]
    failures = 0
    for name, payload, probe, want in cases:
        code, reason = decide(payload, tracked=probe)
        ok = code == want and (want == 0) == (reason == "")
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
        failures += 0 if ok else 1

    # real git roundtrip: a committed binder denies, a fresh draft next to it passes
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)

        def git(*a: str) -> None:
            subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, encoding="utf-8")

        git("init", "-q")
        (repo / ".karta" / "binders").mkdir(parents=True)
        (repo / ".karta" / "binders" / "committed.json").write_text("{}\n", encoding="utf-8")
        git("add", ".")
        git("-c", "user.email=karta@test", "-c", "user.name=karta", "commit", "-q", "-m", "seed")
        (repo / ".karta" / "binders" / "draft.json").write_text("{}\n", encoding="utf-8")
        # A linked worktree holds its own copies: a hard link made there to the main
        # checkout's binder must still meet the binder rule; one to a non-binder not.
        (repo / "notes.txt").write_text("x\n", encoding="utf-8")
        git("add", "notes.txt")
        git("-c", "user.email=karta@test", "-c", "user.name=karta", "commit", "-q", "-m", "notes")
        wt = Path(td) / "wt"
        git("worktree", "add", "-q", str(wt), "HEAD")
        linked = True
        try:
            os.link(repo / ".karta" / "binders" / "committed.json", wt / "alias.json")
            os.link(repo / "notes.txt", wt / "notes-alias.txt")
        except OSError:
            linked = False  # no hard links on this filesystem: nothing to alias

        git_cases = [
            ("git: committed binder denied across path aliases",
             ".karta/binders/committed.json", 2),
            ("git: untracked draft passes", ".karta/binders/draft.json", 0),
        ]
        git_cases = [(n, rel, want, repo) for n, rel, want in git_cases]
        if linked:
            git_cases += [
                ("git: hard link in a linked worktree to the main checkout's binder denied",
                 "alias.json", 2, wt),
                ("git: hard link in a linked worktree to a non-binder passes",
                 "notes-alias.txt", 0, wt),
            ]
        for name, rel, want, where in git_cases:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                       "cwd": str(where), "tool_input": {"file_path": rel, "content": "{}"}}
            code, _ = decide(payload)
            ok = code == want
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
            failures += 0 if ok else 1

    # The denial has to name the withdrawal path, or a rejected plan tempts the
    # reader into a history rewrite instead.
    _, reason = decide(pre("Write", file_path=".karta/binders/checkout.json", content="{}"),
                       tracked=tracked)
    msg_ok = "withdraw it" in reason and "commit the deletion" in reason
    print(f"[{'PASS' if msg_ok else 'FAIL'}] denial names the withdrawal path")
    failures += 0 if msg_ok else 1

    total = len(cases) + len(git_cases) + 1
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        payload = json.loads(_read_stdin_text())
        code, reason = decide(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001
        return 0  # fail open: a guard-internal error must never break the tool call
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
