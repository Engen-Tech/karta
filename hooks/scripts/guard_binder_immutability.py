#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse guard: committed binders are read-only.

Zero dependencies (pure stdlib). The harness invokes this on Write|Edit|NotebookEdit
with the hook payload JSON on stdin. If the target path is a binder
(`.karta/binders/*.json`) that already exists in HEAD, the write is denied — exit 2
with the reason on stderr. A committed binder is the plan of record karta-deliver
derives run state from, so mutating it mid-flight desynchronizes the run. Untracked
binder writes (plan drafting) pass. Any internal error fails open (exit 0): this
guard must never break an unrelated tool call.

  guard_binder_immutability.py              # hook mode: payload on stdin, exit 0/2
  guard_binder_immutability.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

BINDER_RE = re.compile(r"(?:^|/)\.karta/binders/[^/]+\.json$")


def _target_path(tool_input: dict) -> str | None:
    for key in ("file_path", "notebook_path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _tracked_in_head(path: str, cwd: str) -> bool:
    """Is `path` (as the tool call names it) a blob in HEAD of its repo?"""
    abs_path = Path(path) if os.path.isabs(path) else Path(cwd) / path
    base = str(abs_path.parent) if abs_path.parent.is_dir() else cwd
    top = subprocess.run(["git", "-C", base, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if top.returncode != 0:
        return False  # not a repo (or no git): nothing is committed here
    toplevel = top.stdout.strip()
    rel = os.path.relpath(abs_path, toplevel)
    if rel.startswith(".."):
        return False  # outside the repo the tool call runs in
    out = subprocess.run(["git", "-C", toplevel, "ls-tree", "HEAD", "--", rel],
                         capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def decide(payload: dict, tracked=_tracked_in_head) -> tuple[int, str]:
    """Return (exit_code, stderr_reason). `tracked` is injectable for the self-test."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    target = _target_path(tool_input)
    if not target or not BINDER_RE.search(target.replace("\\", "/")):
        return 0, ""
    cwd = payload.get("cwd") or os.getcwd()
    if not tracked(target, cwd):
        return 0, ""  # untracked draft — plan-time binder writing is allowed
    return 2, (
        f"karta: committed binders are read-only. '{target}' already exists in HEAD, and a "
        "committed binder is the plan of record — karta-deliver derives the whole run's state "
        "from it plus its git refs, so mutating it mid-flight desynchronizes the run and its "
        "resume story. To change the plan, re-plan with karta-plan (which writes a fresh binder "
        "file) or draft a new, not-yet-committed binder; writes to untracked binder drafts are "
        "not blocked.")


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
        ("no target path passes", pre("Write", content="x"), tracked, 0),
        ("tool_input not a dict passes",
         {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": "junk"},
         tracked, 0),
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
            ("git: committed binder denied", ".karta/binders/committed.json", 2),
            ("git: untracked draft passes", ".karta/binders/draft.json", 0),
        ]
        for name, rel, want in git_cases:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                       "cwd": str(repo), "tool_input": {"file_path": rel, "content": "{}"}}
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
