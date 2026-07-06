# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Dev-repo commit gate: a Claude Code PreToolUse hook on the Bash tool.

Wired in .claude/settings.json (karta repo tooling, NOT the plugin surface).
Reads the PreToolUse payload JSON from stdin; when tool_input.command contains
a `git commit` invocation it runs the repo gate suite from the repo root —
check_shared_copies, sync_codex_skills --check, sync_codex_agents --check,
validate_plugin, and validate_packs over skills/_shared/sme/ — and exits 2
with the failing gate's name plus an output tail (last ~40 lines) so the
commit is blocked with actionable feedback. All gates green, or any command
that is not a git commit, exits 0. Escape hatch for intentional partial
commits: KARTA_SKIP_GATE=1 in the command text or the environment.

Internal errors (unreadable stdin, malformed payload, unexpected exceptions)
fail OPEN — exit 0 — so a broken hook never wedges the repo. A gate that runs
and fails (or times out) is not an internal error: that blocks.

Zero dependencies (pure stdlib), so every invocation form behaves identically:
  python3 precommit_gate.py < payload.json        # hook mode, exit 0/2
  python3 precommit_gate.py --self-test           # embedded fixtures, exit 0/1
  uv run --script precommit_gate.py --self-test   # also fine — no deps
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root
GATE_TIMEOUT = 100   # seconds per gate; a hung gate is a failed gate, not a stall.
                     # 5 gates x 100s stays inside the hook's 600s timeout in
                     # .claude/settings.json — the harness must never kill this hook
                     # mid-run, because a timed-out PreToolUse hook does not block.
TAIL_LINES = 40      # cap on the captured output relayed in a deny reason
SKIP_VAR = "KARTA_SKIP_GATE"

# `git commit` detection: split chained commands conservatively on &&, ||, ;, |
# and newlines, then match a word-boundary `git ... commit` where anything
# between the two words must be option tokens, each optionally trailing one
# non-dash argument (so `git -C repo commit` and `git -c k=v commit` count but
# `git log --grep commit` does not). Any segment containing a match counts;
# false positives just run the gates, and the escape hatch covers the rest.
_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit\b")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")


def is_commit_command(command: str) -> bool:
    return any(_COMMIT_RE.search(seg) for seg in _SPLIT_RE.split(command))


def gate_specs(root: Path) -> list[tuple[str, list[str]]]:
    """The five repo gates, in the order the spec lists them. The pack gate is
    dropped (not failed) when skills/_shared/sme/ has nothing to validate —
    validate_packs errors on an empty file list, and an absent pack dir is a
    repo-shape question for the other gates, not this one."""
    py = sys.executable or "python3"
    gates = [
        ("check_shared_copies", [py, str(root / "scripts/check_shared_copies.py")]),
        ("sync_codex_skills --check", [py, str(root / "scripts/sync_codex_skills.py"), "--check"]),
        ("sync_codex_agents --check", [py, str(root / "scripts/sync_codex_agents.py"), "--check"]),
        ("validate_plugin", [py, str(root / "scripts/validate_plugin.py")]),
    ]
    # platform-native.md is shared reference data the packs point at via see_also,
    # not a pack (karta-plan skips it the same way) — validating it would fail
    # every commit on its by-design lack of frontmatter. .karta/sme/ overlay packs
    # (the kaizen dogfood surface) are validated with the same gate.
    packs = [p for p in sorted(root.glob("skills/_shared/sme/*.md"))
             if p.name != "platform-native.md"]
    packs += sorted(root.glob(".karta/sme/*.md"))
    if packs:
        gates.append(("validate_packs (packs)",
                      [py, str(root / "skills/karta-kaizen/scripts/validate_packs.py"),
                       *map(str, packs)]))
    return gates


def _tail(text: str, limit: int = TAIL_LINES) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join([f"... ({len(lines) - limit} earlier lines omitted)"] + lines[-limit:])


def _subprocess_runner(name: str, argv: list[str]) -> tuple[int, str]:
    """Run one gate from the repo root; stdout+stderr interleaved."""
    try:
        proc = subprocess.run(argv, cwd=ROOT, text=True, timeout=GATE_TIMEOUT,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 1, f"{out}\n[gate timed out after {GATE_TIMEOUT}s]"


def run_gates(gates, runner) -> tuple[str, int, str] | None:
    """First failing (name, exit_code, output) or None when all gates are green."""
    for name, argv in gates:
        code, output = runner(name, argv)
        if code != 0:
            return name, code, output
    return None


def decide(payload, env, runner, gates=None) -> tuple[int, str]:
    """(exit_code, stderr_message) for one hook invocation. Pure over its inputs
    so --self-test can drive it with fabricated payloads and a stubbed runner."""
    if not isinstance(payload, dict):
        return 0, ""
    tool_input = payload.get("tool_input")
    if payload.get("tool_name", "Bash") != "Bash" or not isinstance(tool_input, dict):
        return 0, ""
    command = tool_input.get("command")
    if not isinstance(command, str) or not is_commit_command(command):
        return 0, ""
    if f"{SKIP_VAR}=1" in command or env.get(SKIP_VAR) == "1":
        return 0, ""
    failure = run_gates(gate_specs(ROOT) if gates is None else gates, runner)
    if failure is None:
        return 0, ""
    name, code, output = failure
    reason = (
        f"Commit blocked by the karta repo gate suite: gate '{name}' failed (exit {code}). "
        f"A `git commit` was detected, so the pre-commit gates ran from the repo root; this one "
        f"did not pass. Fix the failure shown below and commit again — or, for an intentional "
        f"partial commit, prefix the command with {SKIP_VAR}=1 (documented escape hatch).\n\n"
        f"--- {name} output (last {TAIL_LINES} lines) ---\n{_tail(output)}"
    )
    return 2, reason


def hook_main(stdin_text: str, env, runner) -> tuple[int, str]:
    """Parse the payload and decide; any internal error fails open (exit 0)."""
    try:
        payload = json.loads(stdin_text)
    except (ValueError, TypeError):
        return 0, ""
    try:
        return decide(payload, env, runner)
    except Exception as e:  # fail-open: a broken hook must never wedge the repo
        print(f"precommit_gate: internal error, failing open: {e}", file=sys.stderr)
        return 0, ""


# --- self-test ----------------------------------------------------------------

def _payload(command: str, tool: str = "Bash") -> dict:
    return {"session_id": "t", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp",
            "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}}


def _run_self_test() -> int:
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    # detection: word-boundary parse over conservatively split segments
    detect = [
        ('git commit -m "x"', True),
        ("git commit --amend --no-edit", True),
        ("make lint && git commit -m x", True),
        ("cd sub; git commit", True),
        ("git -C /repo commit -m x", True),
        ('echo "run git commit later"', True),   # contains counts, by design
        ("git status", False),
        ("echo git committed", False),           # no boundary after 'commit'
        ('git log | grep "commit"', False),      # split on | isolates the words
        ("git log --grep commit", False),        # non-option token between the words
        ("ls -la", False),
    ]
    for cmd, want in detect:
        check(f"detect {cmd!r} -> {want}", is_commit_command(cmd) == want)

    green = lambda name, argv: (0, f"{name}: OK")
    calls: list[str] = []

    def failing(name, argv):
        calls.append(name)
        if name == "sync_codex_skills --check":
            return 1, "\n".join(f"L{i:03d} drift detail" for i in range(1, 101))
        return 0, "OK"

    def must_not_run(name, argv):
        raise AssertionError("gate runner invoked for a non-commit command")

    stub_gates = [("check_shared_copies", []), ("sync_codex_skills --check", []),
                  ("sync_codex_agents --check", []), ("validate_plugin", []),
                  ("validate_packs (packs)", [])]

    # allow paths
    code, _ = decide(_payload("ls -la"), {}, must_not_run, stub_gates)
    check("non-commit command allows without running gates", code == 0)
    code, _ = decide(_payload("rm -rf build", tool="Write"), {}, must_not_run, stub_gates)
    check("non-Bash tool allows", code == 0)
    code, _ = decide(_payload('git commit -m "x"'), {}, green, stub_gates)
    check("commit with all gates green allows", code == 0)
    code, _ = decide(_payload('KARTA_SKIP_GATE=1 git commit -m "x"'), {}, must_not_run, stub_gates)
    check("KARTA_SKIP_GATE=1 in command text skips the gates", code == 0)
    code, _ = decide(_payload('git commit -m "x"'), {"KARTA_SKIP_GATE": "1"}, must_not_run, stub_gates)
    check("KARTA_SKIP_GATE=1 in the environment skips the gates", code == 0)

    # deny path: failing gate blocks, names itself, caps output, fails fast
    calls.clear()
    code, reason = decide(_payload("make lint && git commit -m x"), {}, failing, stub_gates)
    check("failing gate blocks with exit 2", code == 2)
    check("deny reason names the failing gate", "sync_codex_skills --check" in reason)
    check("deny reason keeps the output tail", "L100 drift detail" in reason)
    check("deny reason drops early lines beyond the cap", "L001" not in reason and "omitted" in reason)
    check("deny reason mentions the escape hatch", "KARTA_SKIP_GATE=1" in reason)
    check("gates fail fast (later gates not run)",
          calls == ["check_shared_copies", "sync_codex_skills --check"], f"calls={calls}")

    # fail-open paths
    code, _ = hook_main("this is not json", {}, must_not_run)
    check("malformed payload fails open", code == 0)
    code, _ = hook_main("[1, 2, 3]", {}, must_not_run)
    check("non-object payload fails open", code == 0)

    def exploding(name, argv):
        raise RuntimeError("boom")
    code, _ = hook_main(json.dumps(_payload("git commit -m x")), {}, exploding)
    check("runner exception fails open", code == 0)

    # real gate list has the expected shape (no gates executed)
    specs = gate_specs(ROOT)
    names = [n for n, _ in specs]
    check("gate_specs lists the spec's gates in order",
          names[:4] == ["check_shared_copies", "sync_codex_skills --check",
                        "sync_codex_agents --check", "validate_plugin"], f"names={names}")
    pack_argv = next((argv for n, argv in specs if n.startswith("validate_packs")), [])
    check("pack gate skips platform-native.md (reference data, not a pack)",
          not any(a.endswith("platform-native.md") for a in pack_argv))

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    import os
    code, message = hook_main(sys.stdin.read(), os.environ, _subprocess_runner)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
