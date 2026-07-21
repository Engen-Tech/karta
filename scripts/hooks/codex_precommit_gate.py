#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex shell-payload adapter for the dev-repo commit gate.

Wired in .codex/hooks.json (karta repo tooling, NOT the plugin surface). Codex
names its shell tool `shell` (or `unified_exec`) and may carry the command as an
argv list; precommit_gate.py — the canonical commit gate — accepts only
`tool_name: "Bash"` with a string command and fails open on anything else. This
adapter closes that gap without forking the gate logic: it reads the Codex
PreToolUse payload from stdin, normalizes it to the canonical Bash shape
(tool_name -> "Bash"; argv list -> one shell-quoted string; unified_exec `input`
-> `command`), and delegates to precommit_gate.py in this same directory, passing
the exit code and stderr straight through — so a failing gate still blocks the
commit with the same reason text.

Anything unrecognized — a non-shell tool, a malformed payload, a missing
precommit_gate.py — fails OPEN (exit 0): a broken adapter must never wedge the
repo. The KARTA_SKIP_GATE=1 escape hatch is the delegate's and works unchanged.

  codex_precommit_gate.py              # hook mode: payload on stdin, exit 0/2
  codex_precommit_gate.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, shlex, subprocess, sys
from pathlib import Path

SHELL_TOOLS = ("shell", "Bash", "bash", "unified_exec", "local_shell")
DELEGATE = Path(__file__).resolve().with_name("precommit_gate.py")


def normalize(payload: object) -> dict | None:
    """Codex shell payload -> canonical Bash payload, or None for not-ours."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") not in SHELL_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if command is None and payload.get("tool_name") == "unified_exec":
        command = tool_input.get("input")
    if isinstance(command, list):
        if not command or not all(isinstance(x, str) for x in command):
            return None
        command = shlex.join(command)
    if not isinstance(command, str) or not command.strip():
        return None
    out = dict(payload)
    out["tool_name"] = "Bash"
    out["tool_input"] = {**tool_input, "command": command}
    out.setdefault("hook_event_name", "PreToolUse")
    return out


def delegate(normalized: dict, delegate_path: Path = DELEGATE) -> int:
    """Run the canonical gate on the normalized payload; stderr passes through so
    a deny reason reaches the harness. Missing delegate fails open."""
    if not delegate_path.is_file():
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, str(delegate_path)],
            input=json.dumps(normalized), text=True, timeout=590)
    except (OSError, subprocess.TimeoutExpired):
        return 0  # fail open: adapter trouble must never wedge the repo
    return 2 if proc.returncode == 2 else 0


def _run_self_test() -> int:
    import tempfile
    checks: list[tuple[str, bool]] = []

    def shell(command: object, tool: str = "shell") -> dict:
        return {"hook_event_name": "PreToolUse", "tool_name": tool,
                "cwd": "/tmp", "tool_input": {"command": command}}

    n = normalize(shell(["git", "commit", "-m", "x"]))
    checks.append(("argv list becomes Bash + one quoted string",
                   n is not None and n["tool_name"] == "Bash"
                   and n["tool_input"]["command"] == "git commit -m x"))
    n = normalize(shell(["bash", "-lc", "git commit -m 'y z'"]))
    checks.append(("wrapped bash -lc command keeps the commit text findable",
                   n is not None and "git commit" in n["tool_input"]["command"]))
    n = normalize(shell("git commit -m x", tool="Bash"))
    checks.append(("string command passes through unchanged",
                   n is not None and n["tool_input"]["command"] == "git commit -m x"))
    ue = {"hook_event_name": "PreToolUse", "tool_name": "unified_exec",
          "cwd": "/tmp", "tool_input": {"input": "git commit -m x"}}
    n = normalize(ue)
    checks.append(("unified_exec input field maps to command",
                   n is not None and n["tool_input"]["command"] == "git commit -m x"))
    checks.append(("non-shell tool is not ours",
                   normalize(shell("git commit", tool="apply_patch")) is None))
    checks.append(("tool_input not a dict is not ours", normalize(
        {"tool_name": "shell", "tool_input": "junk"}) is None))
    checks.append(("non-string argv items are not ours",
                   normalize(shell(["git", 42])) is None))
    checks.append(("empty command is not ours", normalize(shell("")) is None))
    checks.append(("non-dict payload is not ours", normalize(["junk"]) is None))

    # Delegation: a stub delegate proves shape, exit-code propagation, and the
    # missing-delegate fail-open — without running the real repo gates.
    with tempfile.TemporaryDirectory() as td:
        stub = Path(td) / "stub_gate.py"
        stub.write_text(
            "import json, sys\n"
            "p = json.load(sys.stdin)\n"
            "assert p['tool_name'] == 'Bash'\n"
            "assert isinstance(p['tool_input']['command'], str)\n"
            "sys.exit(2 if 'git commit' in p['tool_input']['command'] else 0)\n")
        n = normalize(shell(["git", "commit", "-m", "x"]))
        checks.append(("stub delegate sees Bash shape and its exit 2 propagates",
                       delegate(n, stub) == 2))
        n = normalize(shell("ls -la"))
        checks.append(("stub delegate exit 0 propagates", delegate(n, stub) == 0))
        checks.append(("missing delegate fails open",
                       delegate(n, Path(td) / "ghost.py") == 0))
        crash = Path(td) / "crash_gate.py"
        crash.write_text("import sys\nsys.exit(7)\n")
        checks.append(("delegate exit codes other than 2 map to allow",
                       delegate(n, crash) == 0))

    failures = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        failures += 0 if ok else 1
    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0  # fail open: a broken adapter must never wedge the repo
    normalized = normalize(payload)
    if normalized is None:
        return 0
    return delegate(normalized)


if __name__ == "__main__":
    sys.exit(main())
