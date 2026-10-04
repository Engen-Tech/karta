#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install smoke check for the Karta plugin: prerequisites and hook launchers.

Stdlib only, so it runs before anything else is known to work:

  python3 scripts/install_smoke.py                       # check this checkout
  python3 scripts/install_smoke.py --plugin-root <dir>   # check an installed plugin copy
  python3 scripts/install_smoke.py --json                # machine-readable report

What it checks, each as a named PASS/FAIL line:

  1. Prerequisites resolve on PATH with usable versions: git 2.23 or newer (the
     skills use `git worktree` and `git restore`), uv (every Claude Code hook
     launcher is `uv run --script`, and skill helpers run through it), python3
     3.11 or newer (every Codex launcher execs python3; helper metadata requires
     3.11), and sh (the Codex POSIX launchers are `sh -c` one-liners).
  2. Every hook command in the Claude manifest (hooks/hooks.json, or the path
     .claude-plugin/plugin.json names) and the Codex manifest (the path
     .codex-plugin/plugin.json names) points at a script that exists — including
     each Windows launcher and its target. The Codex launcher allows the tool
     call when its script is missing, so a missing file is only visible here.
  3. Every hook command, exactly as the manifest spells it, runs a benign
     payload for its event in a scratch git repository with a minimal
     environment (PATH holding only the prerequisites' directories, a fresh
     HOME) and exits 0 without a traceback.

What it does not prove: that a host has installed, enabled, or trusted the
plugin's hooks, that a host passes the payload shapes used here, or that any
guard blocks what it should (the guards' own --self-test modes cover that). Most
guards fail open silently on an internal error, so an exit 0 here shows the
launcher and interpreter work, not that the guard logic is sound.
Windows launchers are checked for existence only; they are not run.

Exit: 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MIN_GIT = (2, 23)
MIN_PYTHON = (3, 11)
NOT_PROVEN = [
    "that a host has installed, enabled, or trusted these hooks "
    "(Claude Code: plugin enabled, hooks not disabled; Codex: hooks reviewed and trusted in /hooks)",
    "that a host sends exactly the payload shapes used here",
    "that any guard blocks what it should (run each guard's --self-test for that); "
    "most guards also exit 0 silently on an internal error, so exit 0 here is not proof of a healthy guard",
    "Windows launchers, which are checked for existence only",
]


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def ok(self) -> bool:
        return all(c["ok"] for c in self.checks)


# --- prerequisites --------------------------------------------------------------

def _version(argv: list[str], pattern: str) -> tuple[tuple[int, ...] | None, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    text = (p.stdout + p.stderr).strip()
    m = re.search(pattern, text)
    if p.returncode or not m:
        return None, text or f"exit {p.returncode}"
    return tuple(int(x) for x in m.groups()), text.splitlines()[0]


def check_prerequisites(report: Report) -> dict[str, str]:
    """Resolve each prerequisite on PATH; returns name -> resolved path for those found."""
    found: dict[str, str] = {}

    git = shutil.which("git")
    if report.add("prerequisite git on PATH", bool(git), git or "not found"):
        ver, text = _version([git, "--version"], r"git version (\d+)\.(\d+)")
        report.add(f"prerequisite git >= {MIN_GIT[0]}.{MIN_GIT[1]}",
                   ver is not None and ver >= MIN_GIT, text)
        found["git"] = git

    uv = shutil.which("uv")
    if report.add("prerequisite uv on PATH", bool(uv),
                  uv or "not found; every Claude Code hook launcher runs `uv run --script`"):
        ver, text = _version([uv, "--version"], r"uv (\d+)\.(\d+)")
        report.add("prerequisite uv runs", ver is not None, text)
        found["uv"] = uv

    python3 = shutil.which("python3")
    if report.add("prerequisite python3 on PATH", bool(python3),
                  python3 or "not found; every Codex hook launcher execs python3"):
        ver, text = _version([python3, "-c", "import sys; print('Python %d.%d' % sys.version_info[:2])"],
                             r"Python (\d+)\.(\d+)")
        report.add(f"prerequisite python3 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
                   ver is not None and ver >= MIN_PYTHON, text)
        found["python3"] = python3

    if os.name != "nt":
        sh = shutil.which("sh")
        if report.add("prerequisite sh on PATH", bool(sh), sh or "not found; Codex POSIX launchers use sh -c"):
            found["sh"] = sh
    return found


# --- manifests ------------------------------------------------------------------

def _manifest_path(root: Path, plugin_json: str, default: str | None) -> tuple[Path | None, str]:
    """The hooks manifest a plugin manifest declares, else the host default; with a reason."""
    try:
        declared = json.loads((root / plugin_json).read_text(encoding="utf-8")).get("hooks")
    except (OSError, ValueError, AttributeError) as e:
        if default is None:
            return None, f"{plugin_json} is unreadable ({e})"
        declared = None
    if isinstance(declared, str):
        return root / declared, ""
    if default:
        return root / default, ""
    return None, f"{plugin_json} names no hooks manifest"


def _entries(manifest: dict):
    """(event, matcher, hook) for every command hook in a hooks manifest."""
    for event, groups in (manifest.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                if hook.get("type") == "command":
                    yield event, group.get("matcher", ""), hook


HOSTS = {
    # host: (plugin manifest, default hooks path, root variable, script pattern)
    "claude": (".claude-plugin/plugin.json", "hooks/hooks.json", "CLAUDE_PLUGIN_ROOT",
               r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"'\s]+)"),
    "codex": (".codex-plugin/plugin.json", None, "PLUGIN_ROOT",
              r"\$\{PLUGIN_ROOT\}/([^\"'\s;]+)"),
}
WINDOWS_TARGET = r"launch_hook\.ps1\\?\"\s+\\?\"([^\"\\]+)\\?\""
WINDOWS_LAUNCHER = r"%PLUGIN_ROOT%\\([^\"]+?launch_hook\.ps1)"


def check_manifests(root: Path, report: Report) -> list[tuple[str, str, str, dict, str]]:
    """Static checks; returns (host, event, matcher, hook, script) rows to launch."""
    launches = []
    for host, (plugin_json, default, _var, pattern) in HOSTS.items():
        path, why = _manifest_path(root, plugin_json, default)
        if not report.add(f"manifest {host} declared", path is not None, why or str(path)):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            report.add(f"manifest {host} readable", False, f"{path}: {e}")
            continue
        count = 0
        for event, matcher, hook in _entries(manifest):
            count += 1
            scripts = re.findall(pattern, hook.get("command", ""))
            label = f"manifest {host} {event} [{matcher}]"
            if not report.add(f"{label} names a bundled script", bool(scripts), hook.get("command", "")):
                continue
            for rel in scripts:
                exists = (root / rel).is_file()
                report.add(f"{label} {rel} exists", exists, "" if exists else f"missing: {root / rel}")
                if exists:
                    launches.append((host, event, matcher, hook, rel))
            windows = hook.get("commandWindows")
            if windows:
                found = re.findall(WINDOWS_LAUNCHER, windows) + re.findall(WINDOWS_TARGET, windows)
                if not report.add(f"{label} (Windows) names a bundled script", bool(found), windows):
                    continue
                for rel in dict.fromkeys(found):
                    rel = rel.replace("\\", "/")
                    exists = (root / rel).is_file()
                    report.add(f"{label} (Windows) {rel} exists", exists,
                               "" if exists else f"missing: {root / rel}")
        report.add(f"manifest {host} has hook commands", count > 0, str(path))
    return launches


# --- launches -------------------------------------------------------------------

def _tool_input(tool: str, repo: Path) -> dict:
    target = str(repo / "notes.txt")
    return {
        "Write": {"file_path": target, "content": "hello\n"},
        "Edit": {"file_path": target, "old_string": "hello", "new_string": "hello again"},
        "NotebookEdit": {"notebook_path": str(repo / "notes.ipynb"), "new_source": "print(1)"},
        "Bash": {"command": "echo hello"},
        "Task": {"subagent_type": "general-purpose", "description": "summary",
                 "prompt": "Summarize the README."},
        "Agent": {"subagent_type": "general-purpose", "description": "summary",
                  "prompt": "Summarize the README."},
        "apply_patch": {"command": "*** Begin Patch\n*** Add File: notes.txt\n+hello\n*** End Patch\n"},
        "spawn_agent": {"message": "Summarize the README.", "task_name": "summary"},
    }.get(tool, {})


def benign_payload(event: str, matcher: str, repo: Path, transcript: Path) -> dict:
    payload = {"session_id": "install-smoke", "transcript_path": str(transcript),
               "cwd": str(repo), "hook_event_name": event, "permission_mode": "default"}
    if event in ("PreToolUse", "PostToolUse"):
        tool = (matcher or "Write").split("|")[0]
        payload.update(tool_name=tool, tool_input=_tool_input(tool, repo))
        if event == "PostToolUse":
            payload["tool_response"] = {"success": True}
    elif event == "SessionStart":
        payload["source"] = "startup"
    elif event in ("Stop", "SubagentStop"):
        payload["stop_hook_active"] = False
        if event == "SubagentStop":
            payload["agent_type"] = "general-purpose"
    return payload


def _scratch_repo(base: Path, env: dict) -> Path:
    repo = base / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# scratch\n", encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=install smoke",
           "-c", "user.email=install-smoke@example.invalid"]
    for args in (["init", "-q"], ["add", "README.md"], ["commit", "-qm", "scratch"]):
        subprocess.run(git + args, env=env, check=True, capture_output=True, timeout=30)
    return repo


def _launch(command: str, payload: str, cwd: Path, env: dict, timeout: int):
    """Run one manifest command the way its host does on POSIX: through sh -c."""
    if os.name != "nt":
        return subprocess.run(["sh", "-c", command], input=payload, cwd=cwd, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    raise OSError("POSIX hook launchers are not run on Windows")


def check_launches(root: Path, launches, tools: dict[str, str], report: Report) -> None:
    if os.name == "nt":
        report.add("launch checks", True, "not exercised on Windows; launchers checked for existence only")
        return
    path = os.pathsep.join(dict.fromkeys(str(Path(p).parent) for p in tools.values()))
    with tempfile.TemporaryDirectory(prefix="gpt-install-smoke-") as td:
        base = Path(td)
        home = base / "home"
        home.mkdir()
        env = {"PATH": path, "HOME": str(home)}
        try:
            repo = _scratch_repo(base, env)
        except (OSError, subprocess.SubprocessError) as e:
            report.add("launch scratch repository", False, str(e))
            return
        transcript = base / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")
        for host, event, matcher, hook, rel in launches:
            name = f"launch {host} {event} [{matcher}] {Path(rel).name}"
            needs = "uv" if host == "claude" else "python3"
            if needs not in tools:
                report.add(name, False, f"cannot launch without {needs}")
                continue
            var = HOSTS[host][2]
            run_env = dict(env, **{var: str(root), "CLAUDE_PROJECT_DIR": str(repo)})
            payload = json.dumps(benign_payload(event, matcher, repo, transcript))
            try:
                p = _launch(hook["command"], payload, repo, run_env, int(hook.get("timeout", 60)))
            except subprocess.TimeoutExpired:
                report.add(name, False, f"timed out after {hook.get('timeout', 60)}s")
                continue
            crashed = "Traceback (most recent call last)" in p.stderr
            detail = "; ".join(p.stderr.strip().splitlines()[-3:])
            report.add(name, p.returncode == 0 and not crashed,
                       f"exit {p.returncode}" + (f": {detail}" if detail else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plugin-root", default=str(ROOT),
                    help="plugin root holding hooks/ and .codex-plugin/ (default: this checkout)")
    ap.add_argument("--json", action="store_true", help="print a JSON report instead of lines")
    args = ap.parse_args(argv)
    root = Path(args.plugin_root).resolve()

    report = Report()
    tools = check_prerequisites(report)
    launches = check_manifests(root, report)
    check_launches(root, launches, tools, report)

    if args.json:
        print(json.dumps({"plugin_root": str(root), "ok": report.ok,
                          "checks": report.checks, "not_proven": NOT_PROVEN}, indent=2))
    else:
        for c in report.checks:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']}" + (f" — {c['detail']}" if c["detail"] else ""))
        passed = sum(c["ok"] for c in report.checks)
        print(f"\n{passed}/{len(report.checks)} install checks passed")
        print("Not proven by this check: " + "; ".join(NOT_PROVEN) + ".")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
