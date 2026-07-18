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

Release block (version-bump gate): when the detected `git commit` would change
the plugin version in .claude-plugin/plugin.json, the commit is additionally
refused unless a green full-gate file (benchmarks/results/gate/<date>-gate.json,
never a *.partial.json — subset runs do not count) whose plugin_version equals the
new version and whose karta_sha equals this commit's parent HEAD is staged in the
same commit. Missing, red, partial-only, malformed, version- or sha-mismatched, or
unstaged gate files block with exit 2 naming the exact fix (the run_gate command,
or `git add`) plus the KARTA_SKIP_GATE=1 escape hatch. Version detection is git
plumbing only — a diff is never parsed — and version-read failures leave the block
disarmed rather than wedging a commit; internal errors still fail OPEN.

The --self-test mode prints [PASS]/[FAIL] lines and an N/N checks passed summary
(exit 0 only when the summary is N/N checks passed).

Zero dependencies (pure stdlib), so every invocation form behaves identically:
  python3 precommit_gate.py < payload.json        # hook mode, exit 0/2
  python3 precommit_gate.py --self-test           # embedded fixtures, exit 0/1
  uv run --script precommit_gate.py --self-test   # also fine — no deps
"""
from __future__ import annotations
import argparse, json, re, shlex, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root
GATE_TIMEOUT = 100   # seconds per gate; a hung gate is a failed gate, not a stall.
                     # 5 gates x 100s stays inside the hook's 600s timeout in
                     # .claude/settings.json — the harness must never kill this hook
                     # mid-run, because a timed-out PreToolUse hook does not block.
TAIL_LINES = 40      # cap on the captured output relayed in a deny reason
SKIP_VAR = "KARTA_SKIP_GATE"

# Release block: a version bump must ship with a green full-gate file for the new
# version and this commit's parent HEAD, staged into the same commit.
PLUGIN_JSON = ".claude-plugin/plugin.json"
GATE_RESULTS_REL = "benchmarks/results/gate"
RUN_GATE_CMD = "python3 benchmarks/gate/run_gate.py"

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


# --- release block: version-bump gate --------------------------------------------
#
# git plumbing is injected as `git(args) -> (exit_code, stdout)` and the filesystem
# root as `root`, so --self-test drives the whole block with a fabricated repo and a
# stubbed git — the same stubbed-runner pattern the gate suite already uses.


def _real_git(root: Path, args: list[str]) -> tuple[int, str]:
    """Run one read-only git plumbing command from `root`; never raises."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], text=True,
                              capture_output=True, timeout=GATE_TIMEOUT)
        return proc.returncode, (proc.stdout or "")
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _json_version(text: str) -> str | None:
    """The `version` field of a plugin.json blob, or None if unreadable."""
    try:
        v = json.loads(text).get("version")
        return v if isinstance(v, str) else None
    except (ValueError, TypeError, AttributeError):
        return None


def commit_reads_worktree(command: str) -> bool:
    """True when the commit records WORKING-TREE content of plugin.json rather than
    the staged blob: a `-a`/`--all` commit (including combined short flags like
    `-am`) or a commit carrying a pathspec. Falls closed toward the working tree on
    unparseable quoting — a false working-tree read is a safe over-arm, an escapable
    block; a missed `-a` would let a bump ship un-gated."""
    seg = next((s for s in _SPLIT_RE.split(command) if _COMMIT_RE.search(s)), command)
    try:
        tokens = shlex.split(seg)
    except ValueError:
        return bool(re.search(r"(?:^|\s)(?:--all|-[A-Za-z]*a[A-Za-z]*)(?:\s|$)", seg))
    if "commit" not in tokens:
        return False
    rest = tokens[tokens.index("commit") + 1:]
    value_long = {"--message", "--reuse-message", "--reedit-message", "--file",
                  "--author", "--date", "--template", "--fixup", "--squash",
                  "--cleanup", "--pathspec-from-file"}
    value_short = set("mCcFt")  # short opts that consume the next token as their value
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--":                         # everything after -- is a pathspec
            return i + 1 < len(rest)
        if tok.startswith("--"):
            if tok.split("=", 1)[0] == "--all":
                return True
            if tok in value_long:
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:  # short-flag cluster
            letters = tok[1:]
            if "a" in letters:
                return True
            i += 2 if letters[-1] in value_short else 1
            continue
        return True                              # a bare token after commit = pathspec
    return False


def _new_version(command: str, git, root: Path, worktree_mode: bool) -> str | None:
    """The plugin version the commit would record: working tree under -a/pathspec,
    else the staged blob. None when it cannot be read (block stays disarmed)."""
    if worktree_mode:
        try:
            return _json_version((root / PLUGIN_JSON).read_text())
        except OSError:
            return None
    code, out = git(["show", f":{PLUGIN_JSON}"])
    return _json_version(out) if code == 0 else None


def _staged_paths(git) -> list[str]:
    code, out = git(["diff", "--cached", "--name-only"])
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if code == 0 else []


def _release_block(command: str, git, root: Path) -> str | None:
    """A deny reason when a version bump lacks its green staged gate file, else None
    (not a bump, or the gate is present and green). Never raises on git/JSON errors —
    an undeterminable version leaves the block disarmed."""
    worktree_mode = commit_reads_worktree(command)
    code, head_blob = git(["show", f"HEAD:{PLUGIN_JSON}"])
    old = _json_version(head_blob) if code == 0 else None
    new = _new_version(command, git, root, worktree_mode)
    if old is None or new is None or old == new:
        return None  # not a version bump (or undeterminable) — block not armed

    head_sha = (git(["rev-parse", "HEAD"])[1] or "").strip()
    staged = set(_staged_paths(git))
    gate_dir = root / GATE_RESULTS_REL
    files = sorted(gate_dir.glob("*-gate.json")) if gate_dir.is_dir() else []
    has_partial = bool(list(gate_dir.glob("*-gate.partial.json"))) if gate_dir.is_dir() else False

    # Classify every full gate file; the first full green+match+staged file allows.
    seen: dict[str, str] = {}  # kind -> a relevant path for the reason
    for p in files:
        rel = str(p.relative_to(root))
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            seen.setdefault("malformed", rel)
            continue
        if not isinstance(data, dict) or data.get("only") is not None:
            has_partial = True  # a subset run masquerading as a full file
            continue
        if data.get("plugin_version") != new:
            seen.setdefault("version", rel)
            continue
        if data.get("karta_sha", "").strip() != head_sha:
            seen.setdefault("sha", rel)
            continue
        summary = data.get("summary")
        green = (isinstance(summary, dict)
                 and summary.get("fail") == 0 and summary.get("error") == 0)
        if not green:
            seen.setdefault("red", rel)
            continue
        committed = rel in staged or (worktree_mode and p.is_file())
        if committed:
            return None  # green full-gate file for this bump, staged — allow
        seen.setdefault("unstaged", rel)

    return _release_reason(new, head_sha, seen, has_partial)


def _release_reason(new: str, head_sha: str, seen: dict[str, str], has_partial: bool) -> str:
    """The most actionable deny reason for an armed-but-unsatisfied release block.
    Ordered from closest-to-done (just `git add`) to nothing-there (run the gate)."""
    short = head_sha[:9] or "HEAD"
    head = (f"Commit blocked by the release gate: this commit bumps the plugin version "
            f"to {new}, which requires a green full-gate file for this exact tree "
            f"(plugin_version {new}, karta_sha {short}, fail=0 error=0), staged into the "
            f"same commit.")
    escape = f" For an intentional bypass, prefix the command with {SKIP_VAR}=1 (documented escape hatch)."
    rerun = (f" Edit the version, run `{RUN_GATE_CMD}` on this tree (it records the current "
             f"HEAD sha), then `git add` the dated gate file and commit it with the bump.")
    if "unstaged" in seen:
        return (f"{head} A matching green gate file exists but is not staged: run "
                f"`git add {seen['unstaged']}` and commit it together with the version bump.{escape}")
    if "red" in seen:
        return (f"{head} The gate file {seen['red']} matches but is red (fail/error > 0); a red "
                f"gate blocks the release. Fix the failing vectors and re-run `{RUN_GATE_CMD}`.{escape}")
    if "sha" in seen:
        return (f"{head} A green gate file for {new} exists but its karta_sha does not match "
                f"this commit's parent HEAD {short} — the gate must run on the pre-bump tree at "
                f"this HEAD.{rerun}{escape}")
    if "version" in seen:
        return (f"{head} A full gate file exists but its plugin_version does not match {new}.{rerun}{escape}")
    if "malformed" in seen:
        return (f"{head} The gate file {seen['malformed']} is not valid JSON — an unreadable "
                f"verdict is not a green verdict.{rerun}{escape}")
    partial = (" A partial (--only) gate run was found, but subset runs do not count."
               if has_partial else "")
    return (f"{head} No committed full-gate file was found under {GATE_RESULTS_REL}/ for "
            f"{new} at HEAD {short}.{partial}{rerun}{escape}")


def decide(payload, env, runner, gates=None, git=None, root=None) -> tuple[int, str]:
    """(exit_code, stderr_message) for one hook invocation. Pure over its inputs
    so --self-test can drive it with fabricated payloads, a stubbed runner, a stubbed
    git, and a fabricated repo root."""
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
    root = ROOT if root is None else root
    if git is None:
        git = lambda args: _real_git(root, args)
    # The five repo gates run exactly as before, first.
    failure = run_gates(gate_specs(ROOT) if gates is None else gates, runner)
    if failure is not None:
        name, code, output = failure
        reason = (
            f"Commit blocked by the karta repo gate suite: gate '{name}' failed (exit {code}). "
            f"A `git commit` was detected, so the pre-commit gates ran from the repo root; this one "
            f"did not pass. Fix the failure shown below and commit again — or, for an intentional "
            f"partial commit, prefix the command with {SKIP_VAR}=1 (documented escape hatch).\n\n"
            f"--- {name} output (last {TAIL_LINES} lines) ---\n{_tail(output)}"
        )
        return 2, reason
    # Then the release block: a version bump needs its green staged gate file.
    block = _release_block(command, git, root)
    return (2, block) if block is not None else (0, "")


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
    # a git stub reporting no version change, so gate-suite cases stay hermetic
    no_bump = lambda args: (0, json.dumps({"version": "2.21.0"}))
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
    code, _ = decide(_payload('git commit -m "x"'), {}, green, stub_gates, git=no_bump)
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

    # --- release block: version-bump gate ---------------------------------------
    # Fabricated repos (a working-tree plugin.json + gate result files) and a stubbed
    # git drive the whole block — the stubbed-runner pattern the gate suite uses.
    import tempfile, shutil
    tmp_roots: list[str] = []

    def gate_doc(version, sha, *, fail=0, error=0, only=None):
        return {"schema_version": 1, "run_date": "2026-07-18", "karta_sha": sha,
                "plugin_version": version, "strict": False, "only": only, "vectors": [],
                "summary": {"total": 24, "pass": 2, "fail": fail, "error": error, "skipped": 22}}

    def mk_repo(*, head_ver, staged_ver=None, worktree_ver=None, head_sha="p" * 40,
                gate_files=(), staged_paths=(), partials=()):
        root = Path(tempfile.mkdtemp(prefix="pcg-rel-"))
        tmp_roots.append(str(root))
        (root / ".claude-plugin").mkdir(parents=True)
        wt = worktree_ver if worktree_ver is not None else (
            staged_ver if staged_ver is not None else head_ver)
        (root / PLUGIN_JSON).write_text(json.dumps({"version": wt}))
        gdir = root / GATE_RESULTS_REL
        gdir.mkdir(parents=True)
        for name, content in gate_files:
            (gdir / name).write_text(content if isinstance(content, str) else json.dumps(content))
        for name in partials:
            (gdir / name).write_text(json.dumps(gate_doc(head_ver, head_sha, only=["one-vector"])))
        sver = staged_ver if staged_ver is not None else head_ver

        def git(args):
            if args[:1] == ["show"]:
                return (0, json.dumps({"version": head_ver if args[1].startswith("HEAD:") else sver}))
            if args[:2] == ["rev-parse", "HEAD"]:
                return (0, head_sha)
            if args[:1] == ["diff"]:
                return (0, "\n".join(staged_paths))
            return (1, "")
        return root, git

    SHA = "a" * 40
    GATE = "2026-07-18-gate.json"
    GATE_REL = f"{GATE_RESULTS_REL}/{GATE}"

    # a commit that bumps nothing never arms the block (even with no gate file)
    root, git = mk_repo(head_ver="2.21.0")
    code, _ = decide(_payload('git commit -m "x"'), {}, green, stub_gates, git=git, root=root)
    check("no version change never arms the release block", code == 0)

    # green full-gate file matching version+sha, staged into the commit -> allow
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.22.0", SHA))],
                        staged_paths=[GATE_REL, PLUGIN_JSON])
    code, _ = decide(_payload('git commit -m "bump 2.22.0"'), {}, green, stub_gates, git=git, root=root)
    check("bump with a green matching staged gate file allows", code == 0)

    # green match but the gate file is not staged -> block naming git add
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.22.0", SHA))], staged_paths=[PLUGIN_JSON])
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("green-but-unstaged gate file blocks", code == 2)
    check("unstaged block names `git add` of the gate file", f"git add {GATE_REL}" in reason)
    check("unstaged block names the escape hatch", f"{SKIP_VAR}=1" in reason)

    # red gate file (fail>0) blocks, naming run_gate
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.22.0", SHA, fail=1))], staged_paths=[GATE_REL])
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("red gate file blocks the bump", code == 2 and RUN_GATE_CMD in reason)

    # sha mismatch blocks (a gate that ran on a different tree)
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.22.0", "b" * 40))], staged_paths=[GATE_REL])
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("sha-mismatched gate file blocks", code == 2 and "karta_sha" in reason)

    # version mismatch blocks (gate ran for a different version)
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.21.0", SHA))], staged_paths=[GATE_REL])
    code, _ = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("version-mismatched gate file blocks", code == 2)

    # partial-only (*.partial.json) does not count -> block
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        partials=["2026-07-18-gate.partial.json"])
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("partial-only gate run does not count", code == 2 and "subset runs do not count" in reason)

    # absent gate file -> block naming run_gate
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA)
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("absent gate file blocks naming run_gate", code == 2 and RUN_GATE_CMD in reason)

    # malformed gate JSON blocks (an unreadable verdict is not a green verdict)
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA,
                        gate_files=[(GATE, "{ not valid json")], staged_paths=[GATE_REL])
    code, reason = decide(_payload('git commit -m "bump"'), {}, green, stub_gates, git=git, root=root)
    check("malformed gate JSON blocks", code == 2 and "not valid JSON" in reason)

    # `git commit -am` records WORKING-TREE content: worktree bumped, staged blob not.
    # Reading the staged blob would see no bump and allow; arming proves it read the
    # working tree.
    root, git = mk_repo(head_ver="2.21.0", worktree_ver="2.22.0", staged_ver="2.21.0", head_sha=SHA)
    code, _ = decide(_payload('git commit -am "bump"'), {}, green, stub_gates, git=git, root=root)
    check("git commit -am arms the block from working-tree content", code == 2)

    # under -a, a green gate file present in the working tree counts as committed -> allow
    root, git = mk_repo(head_ver="2.21.0", worktree_ver="2.22.0", staged_ver="2.21.0", head_sha=SHA,
                        gate_files=[(GATE, gate_doc("2.22.0", SHA))], staged_paths=[])
    code, _ = decide(_payload('git commit -am "bump"'), {}, green, stub_gates, git=git, root=root)
    check("under -a a green gate file present in the tree counts as committed", code == 0)

    # a pathspec commit also records working-tree content and arms the block
    root, git = mk_repo(head_ver="2.21.0", worktree_ver="2.22.0", staged_ver="2.21.0", head_sha=SHA)
    code, _ = decide(_payload('git commit .claude-plugin/plugin.json -m "bump"'),
                     {}, green, stub_gates, git=git, root=root)
    check("pathspec commit arms the block from working-tree content", code == 2)

    # KARTA_SKIP_GATE=1 still bypasses everything, even an armed version bump
    root, git = mk_repo(head_ver="2.21.0", staged_ver="2.22.0", head_sha=SHA)
    code, _ = decide(_payload('KARTA_SKIP_GATE=1 git commit -m "bump"'), {}, must_not_run,
                     stub_gates, git=git, root=root)
    check("KARTA_SKIP_GATE=1 skips even an armed version bump", code == 0)

    # working-tree detector unit checks
    check("detector: -m is staged mode", commit_reads_worktree('git commit -m "x"') is False)
    check("detector: -am is working-tree mode", commit_reads_worktree('git commit -am "x"') is True)
    check("detector: --all is working-tree mode", commit_reads_worktree("git commit --all") is True)
    check("detector: pathspec is working-tree mode",
          commit_reads_worktree("git commit path/to/file -m x") is True)
    check("detector: --amend alone is staged mode",
          commit_reads_worktree("git commit --amend --no-edit") is False)

    for r in tmp_roots:
        shutil.rmtree(r, ignore_errors=True)

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
