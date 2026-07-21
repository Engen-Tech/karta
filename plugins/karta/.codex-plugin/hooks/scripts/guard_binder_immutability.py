#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex PreToolUse guard: committed binders are read-only — live and archived.

Codex twin of hooks/scripts/guard_binder_immutability.py (the Claude Code guard).
Same rule, different payload shape: Codex file edits arrive as `apply_patch` calls
whose `tool_input.command` is the RAW PATCH BODY (`*** Add File:` / `*** Update
File:` / `*** Delete File:` / `*** Move to:` directives), never a `file_path`.
This guard parses the patch body, resolves each touched path against the payload
`cwd`, and denies (exit 2, reason on stderr) any operation that would rewrite a
binder (`.karta/binders/*.json`, or a delivered one under
`.karta/binders/archive/`) that already exists in HEAD:

- Update File on a committed binder — denied, with one sanctioned exception: a
  hunk-free move (`*** Move to:`) of a live binder to its own archive path
  (`.karta/binders/archive/<same name>.json`), which is the end-of-life step.
- Add File over a path tracked in HEAD — denied (a re-add is an overwrite).
- Delete File of a committed binder — denied; archive it with `git mv` or a
  hunk-free patch move instead.
- Untracked binder writes (plan drafting) pass, as on Claude Code.

A Claude-shaped payload (`tool_input.file_path`/`notebook_path`) is also handled,
in case a Codex build maps Write/Edit natively. Any internal error or unknown
payload shape fails open (exit 0): this guard must never break an unrelated tool
call. Keep the rule semantics in step with the Claude twin — the two files are
maintained by hand, not generated.

  guard_binder_immutability.py              # hook mode: payload on stdin, exit 0/2
  guard_binder_immutability.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

BINDER_RE = re.compile(r"(?:^|/)\.karta/binders/(?:archive/)?[^/]+\.json$")
DIRECTIVE_RE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


def parse_patch_ops(text: str) -> list[dict]:
    """apply_patch body -> [{op, path, move_to, changed}]. `changed` is True when
    the op carries content hunks (+/- lines); a pure rename has none. Content
    lines are +/- prefixed, so a file whose text contains `*** Update File:` can
    never spoof a directive."""
    ops: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = DIRECTIVE_RE.match(line)
        if m:
            kind, path = m.group(1), m.group(2).strip()
            if kind == "Move to":
                if cur is not None:
                    cur["move_to"] = path
            else:
                cur = {"op": kind, "path": path, "move_to": None, "changed": False}
                ops.append(cur)
            continue
        if line.startswith("*** "):
            continue  # Begin Patch / End Patch / End of File markers
        if cur is not None and line.startswith(("+", "-")):
            cur["changed"] = True
    return ops


def _tracked_in_head(path: str, cwd: str) -> bool:
    """Is `path` (as the patch names it) a blob in HEAD of its repo?"""
    abs_path = (Path(path) if os.path.isabs(path) else Path(cwd) / path).resolve()
    base = str(abs_path.parent) if abs_path.parent.is_dir() else cwd
    top = subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0:
        return False  # not a repo (or no git): nothing is committed here
    toplevel = Path(top.stdout.strip()).resolve()
    rel = os.path.relpath(abs_path, toplevel)
    if rel.startswith(".."):
        return False  # outside the repo the tool call runs in
    out = subprocess.run(["git", "-C", str(toplevel), "ls-tree", "HEAD", "--", rel],
                         capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def _archive_dst(src_norm: str) -> str | None:
    """The one sanctioned move target for a live binder: its own archive path."""
    m = re.match(r"^(?P<prefix>.*\.karta/binders/)(?P<name>[^/]+\.json)$", src_norm)
    if not m:
        return None
    return m.group("prefix") + "archive/" + m.group("name")


def _deny(path: str, what: str) -> tuple[int, str]:
    return 2, (
        f"karta: committed binders are read-only. This patch would {what} '{path}', which "
        "already exists in HEAD — a committed binder is the plan of record karta-deliver "
        "derives the whole run's state from, so mutating it mid-flight desynchronizes the "
        "run and its resume story; an archived binder (.karta/binders/archive/) is delivered "
        "history and is never edited. To change the plan, re-plan with karta-plan (which "
        "writes a fresh binder file) or draft a new, not-yet-committed binder. The one "
        "sanctioned mutation is the end-of-life archive move: `git mv` the live binder to "
        ".karta/binders/archive/<same name>.json, or use a hunk-free patch move to exactly "
        "that path.")


def decide(payload: dict, tracked=_tracked_in_head) -> tuple[int, str]:
    """Return (exit_code, stderr_reason). `tracked` is injectable for the self-test."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    cwd = payload.get("cwd") or os.getcwd()

    # Claude-shaped fallback: a direct file_path write, should a Codex build send one.
    for key in ("file_path", "notebook_path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            if BINDER_RE.search(val.replace("\\", "/")) and tracked(val, cwd):
                return _deny(val, "overwrite")
            return 0, ""

    raw = tool_input.get("command")
    if isinstance(raw, list):
        raw = "\n".join(x for x in raw if isinstance(x, str))
    if not isinstance(raw, str) or "*** " not in raw:
        return 0, ""
    for op in parse_patch_ops(raw):
        src = op["path"].replace("\\", "/")
        dst = op["move_to"].replace("\\", "/") if isinstance(op["move_to"], str) else None
        src_is_binder = bool(BINDER_RE.search(src))
        if op["op"] == "Add File":
            if src_is_binder and tracked(op["path"], cwd):
                return _deny(op["path"], "re-add (overwrite)")
        elif op["op"] == "Delete File":
            if src_is_binder and tracked(op["path"], cwd):
                return _deny(op["path"], "delete")
        elif op["op"] == "Update File":
            if src_is_binder and tracked(op["path"], cwd):
                if dst and dst == _archive_dst(src) and not op["changed"]:
                    continue  # the sanctioned end-of-life archive move
                return _deny(op["path"], "rewrite")
            if dst and BINDER_RE.search(dst) and tracked(dst, cwd):
                return _deny(dst, "move another file over")
    return 0, ""


def _run_self_test() -> int:
    tracked = lambda path, cwd: True     # noqa: E731
    untracked = lambda path, cwd: False  # noqa: E731

    def patch(*lines: str) -> dict:
        body = "\n".join(("*** Begin Patch", *lines, "*** End Patch"))
        return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                "cwd": "/tmp", "tool_input": {"command": body}}

    cases = [
        ("non-binder patch passes",
         patch("*** Update File: src/app.py", "@@", "-a", "+b"), tracked, 0),
        ("update of tracked binder denied",
         patch("*** Update File: .karta/binders/checkout.json", "@@", "-a", "+b"),
         tracked, 2),
        ("update of untracked binder draft passes",
         patch("*** Update File: .karta/binders/draft.json", "@@", "+x"), untracked, 0),
        ("add of new binder draft passes",
         patch("*** Add File: .karta/binders/new-plan.json", "+{}"), untracked, 0),
        ("re-add over tracked binder denied",
         patch("*** Add File: .karta/binders/checkout.json", "+{}"), tracked, 2),
        ("delete of tracked binder denied",
         patch("*** Delete File: .karta/binders/checkout.json"), tracked, 2),
        ("hunk-free archive move passes",
         patch("*** Update File: .karta/binders/checkout.json",
               "*** Move to: .karta/binders/archive/checkout.json"), tracked, 0),
        ("archive move with content hunks denied",
         patch("*** Update File: .karta/binders/checkout.json",
               "*** Move to: .karta/binders/archive/checkout.json", "@@", "+x"),
         tracked, 2),
        ("move of tracked binder to a non-archive path denied",
         patch("*** Update File: .karta/binders/checkout.json",
               "*** Move to: docs/checkout.json"), tracked, 2),
        ("move of tracked binder to a renamed archive path denied",
         patch("*** Update File: .karta/binders/checkout.json",
               "*** Move to: .karta/binders/archive/renamed.json"), tracked, 2),
        ("move of tracked archived binder denied (archive is history)",
         patch("*** Update File: .karta/binders/archive/done.json",
               "*** Move to: .karta/binders/done.json"), tracked, 2),
        ("move of another file over a tracked binder denied",
         patch("*** Update File: notes.json",
               "*** Move to: .karta/binders/checkout.json"), tracked, 2),
        ("update of tracked archived binder denied",
         patch("*** Update File: .karta/binders/archive/done.json", "@@", "+x"),
         tracked, 2),
        ("binder-like path elsewhere passes",
         patch("*** Update File: docs/karta/binders-history.json", "+x"), tracked, 0),
        ("non-json under binders passes",
         patch("*** Update File: .karta/binders/notes.md", "+x"), tracked, 0),
        ("deeper subdir under archive passes (only archive/ is a binder home)",
         patch("*** Update File: .karta/binders/archive/nested/x.json", "+x"),
         tracked, 0),
        ("nested binder dir path still matches",
         patch("*** Update File: sub/.karta/binders/x.json", "+x"), tracked, 2),
        ("content line spelling a directive cannot spoof one",
         patch("*** Update File: src/app.py",
               "+*** Update File: .karta/binders/checkout.json"), tracked, 0),
        ("multi-op patch: clean op then binder op denied",
         patch("*** Update File: src/app.py", "+x",
               "*** Update File: .karta/binders/checkout.json", "+y"), tracked, 2),
        ("command as list is normalized",
         {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "/tmp",
          "tool_input": {"command": ["apply_patch",
                                     "*** Begin Patch\n*** Update File: "
                                     ".karta/binders/checkout.json\n+x\n*** End Patch"]}},
         tracked, 2),
        ("Claude-shaped file_path payload on tracked binder denied",
         {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": "/tmp",
          "tool_input": {"file_path": ".karta/binders/checkout.json", "content": "{}"}},
         tracked, 2),
        ("Claude-shaped file_path payload on untracked draft passes",
         {"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": "/tmp",
          "tool_input": {"file_path": ".karta/binders/new.json", "content": "{}"}},
         untracked, 0),
        ("no command and no file_path passes",
         {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": "/tmp",
          "tool_input": {}}, tracked, 0),
        ("tool_input not a dict passes",
         {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
          "tool_input": "junk"}, tracked, 0),
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
            subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

        git("init", "-q")
        (repo / ".karta" / "binders").mkdir(parents=True)
        (repo / ".karta" / "binders" / "committed.json").write_text("{}\n")
        git("add", ".")
        git("-c", "user.email=karta@test", "-c", "user.name=karta", "commit", "-q", "-m", "seed")
        (repo / ".karta" / "binders" / "draft.json").write_text("{}\n")

        git_cases = [
            ("git: committed binder update denied",
             ".karta/binders/committed.json", 2),
            ("git: untracked draft update passes", ".karta/binders/draft.json", 0),
        ]
        for name, rel, want in git_cases:
            body = f"*** Begin Patch\n*** Update File: {rel}\n@@\n+x\n*** End Patch"
            payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                       "cwd": str(repo), "tool_input": {"command": body}}
            code, _ = decide(payload)
            ok = code == want
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
            failures += 0 if ok else 1

    total = len(cases) + 2
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
        code, reason = decide(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001
        return 0  # fail open: a guard-internal error must never break the tool call
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
