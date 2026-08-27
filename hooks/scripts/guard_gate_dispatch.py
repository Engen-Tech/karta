#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse guard: gate-reviewer dispatches carry a real, sized diff.

Zero dependencies (pure stdlib). The harness invokes this on Task|Agent with the
hook payload JSON on stdin. It recognizes a gate-reviewer dispatch by the agent
identity fields of the tool input (the same key set guard_auditor_dispatch.py
scans) naming `karta-acceptance-reviewer` or `karta-safety-auditor`. For a
recognized dispatch it requires, in the prompt: a parseable diff range
(a `<rev>..<rev>` token — karta-verify already mandates the range as a required
input, so its absence is a malformed brief, not a novel style), a diff range that
is not empty (`git diff --quiet <range>` reports changes), and a
`Diff-size: <files> files, <bytes> bytes` line whose numbers match what git
recomputes for that range. Missing range, empty range, missing Diff-size line, or
a mismatched Diff-size line all deny (exit 2, reason on stderr) — the empty-diff
refusal moves the existing empty-diff-is-BLOCKED doctrine from a post-hoc gate
verdict to a pre-dispatch refusal, saving two reviewer contexts per occurrence,
and the Diff-size line makes an oversized diff visible before anyone reads it.
Like guard_auditor_dispatch.py this guard is FAIL-CLOSED on its recognized shape:
an internal error while checking a recognized dispatch denies. Unrecognized
dispatch shapes always pass.

  guard_gate_dispatch.py              # hook mode: payload on stdin, exit 0/2
  guard_gate_dispatch.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys

AGENT_KEYS = ("subagent_type", "agent_type", "agent", "agent_name", "name")
GATE_AGENTS = ("karta-acceptance-reviewer", "karta-safety-auditor")

# Revs DO contain dots — v2.31.0, release/2.31.0 — so the rev class must allow them,
# which means the class can no longer be trusted to stop at the separator. Match the whole
# token instead and split it on the separator explicitly, longest form first so A...B is
# not read as A. .. .B. The endpoints are not validated here: an endpoint that does not
# resolve falls through to the existing unresolvable-range deny, which is where a bad rev
# belongs. Written after a review found v1.17.0..HEAD parsed as 0..HEAD and denied a
# legitimate dispatch.
# Dots are legal inside a rev but never at its end (git check-ref-format), so the class
# allows them while the final character may not be one — otherwise "range base..feature."
# swallows the sentence's full stop and the endpoint stops resolving.
_REV = r"[A-Za-z0-9_/.~^{}@-]*[A-Za-z0-9_/~^{}@-]"
RANGE_TOKEN_RE = re.compile(rf"(?<![A-Za-z0-9_/.~^-])({_REV}\.\.\.?{_REV})")
# A path, not the next English word. `worktree at /srv/wt` must not yield "at", so the
# captured token has to look like a path — absolute, explicitly relative, ~-rooted, or a
# bare dot — and anything else falls back to the payload cwd rather than denying on a
# worktree the brief never named.
WORKTREE_RE = re.compile(r"\bworktree\b\s*[:=]?\s*(\S+)", re.I)
_PATHLIKE = re.compile(r"^(\.\.?/|\.\.?$|~|/)")
# The exact string "Diff-size:" is a shared term — case-sensitive, verbatim.
DIFF_SIZE_RE = re.compile(r"Diff-size:\s*(\d+)\s*files?,\s*(\d+)\s*bytes?")

GIT_TIMEOUT = 15


def _recognized(tool_input: dict) -> bool:
    for k in AGENT_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and any(agent in v for agent in GATE_AGENTS):
            return True
    return False


def _split_range(token: str) -> tuple[str, str] | None:
    """Split a matched range token into its two endpoints, three-dot form first."""
    for sep in ("...", ".."):
        head, found, tail = token.partition(sep)
        if found and head and tail:
            return head, tail
    return None


_CLAUSE_PUNCT = ';,:)]"\''


def _unquote(tok: str) -> str:
    """Remove one matched outer quote pair; a lone quote may be part of a name."""
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


def _extract_worktree(text: str, cwd: str) -> str:
    for m in WORKTREE_RE.finditer(text):
        # Every character scrubbed here — quotes, `;,:)]` — is legal in a POSIX name, so
        # `worktree /srv/wt/release;` may mean the directory `release;`, and a greedy strip
        # would resolve its sibling `release` instead: the guard judging a tree the brief
        # never named, which is the one outcome it must not produce. So the token is
        # peeled one step at a time — a matched outer quote pair, else one trailing
        # clause character — and the FIRST form that EXISTS wins, whatever it is. A file
        # or a dangling symlink at that name stops the peel too: `git -C` then fails and
        # the guard denies, instead of peeling past it to a sibling directory. When no
        # form exists, the longest path-like form the loop evaluated is handed to git so
        # the denial names what was written; nothing is ever returned that the loop did
        # not check. A trailing DOT is never peeled: `..` is path syntax, so `/srv/wt/...`
        # minus its dots is a real different directory, and a brief that ends its path
        # with a full stop is denied instead. A lone quote may be part of a name and
        # stays; a token that never becomes path-like is prose, and the next mention is
        # tried.
        tok = m.group(1)
        first_pathlike = None
        while tok:
            cand = _unquote(tok)
            if cand and _PATHLIKE.search(cand):
                if os.path.lexists(os.path.join(cwd, cand)):
                    return cand
                if first_pathlike is None:
                    first_pathlike = cand
            if cand != tok:
                tok = cand
            elif tok[-1] in _CLAUSE_PUNCT:
                tok = tok[:-1]
            else:
                break
        if first_pathlike is not None:
            return first_pathlike
    return cwd


def _diff_has_changes(worktree: str, diff_range: str) -> bool | None:
    """True = changes present, False = empty diff, None = unresolvable (deny)."""
    try:
        proc = subprocess.run(["git", "-C", worktree, "diff", "--quiet", diff_range],
                               capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    return None  # e.g. 128 — bad revision or unresolvable worktree


def _diff_stat(worktree: str, diff_range: str) -> tuple[int, int] | None:
    """Recompute (files, bytes) the way the brief's Diff-size line is supposed to."""
    try:
        names = subprocess.run(["git", "-C", worktree, "diff", "--name-only", diff_range],
                                capture_output=True, timeout=GIT_TIMEOUT)
        full = subprocess.run(["git", "-C", worktree, "diff", diff_range],
                               capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if names.returncode != 0 or full.returncode != 0:
        return None
    files = len([ln for ln in names.stdout.decode("utf-8", "replace").splitlines() if ln.strip()])
    return files, len(full.stdout)


def decide(payload: dict) -> tuple[int, str]:
    """Return (exit_code, stderr_reason)."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not _recognized(tool_input):
        return 0, ""  # unrecognized dispatch shapes always pass

    text = "\n".join(str(tool_input.get(k) or "") for k in ("prompt", "description"))
    cwd = payload.get("cwd") or os.getcwd()
    worktree = _extract_worktree(text, cwd)

    m = RANGE_TOKEN_RE.search(text)
    endpoints = _split_range(m.group(1)) if m else None
    if not endpoints:
        return 2, (
            "karta: this is a gate-reviewer dispatch (karta-acceptance-reviewer / "
            "karta-safety-auditor) and the guard fails closed — the dispatch prompt must name "
            "the diff range (a `<rev>..<rev>` token) the reviewer is meant to scan. "
            "karta-verify already mandates the range as a required input, so its absence is a "
            "malformed brief. Re-dispatch with the diff range embedded in the prompt.")

    diff_range = m.group(1)
    has_changes = _diff_has_changes(worktree, diff_range)
    if has_changes is None:
        return 2, (
            f"karta: could not resolve diff range '{diff_range}' against worktree '{worktree}' "
            "for this gate-reviewer dispatch — this guard fails closed on a dispatch it cannot "
            "verify. Re-dispatch with a diff range and worktree path that resolve together.")
    if not has_changes:
        return 2, (
            f"karta: this gate-reviewer dispatch names diff range '{diff_range}' but `git diff "
            "--quiet` reports no changes in it — dispatching two reviewer contexts on an empty "
            "diff wastes both. Two known causes: the build produced no changes (a whiff), or "
            "the work named by this range is already present on the tip. Fix the range or skip "
            "the dispatch; do not send it as-is.")

    stat = _diff_stat(worktree, diff_range)
    if stat is None:
        return 2, (
            f"karta: could not recompute the diff stat for range '{diff_range}' against "
            f"worktree '{worktree}' — this guard fails closed on a gate-reviewer dispatch it "
            "cannot verify.")
    real_files, real_bytes = stat

    size_m = DIFF_SIZE_RE.search(text)
    if not size_m:
        return 2, (
            "karta: this gate-reviewer dispatch carries no `Diff-size: <files> files, <bytes> "
            "bytes` line — every gate dispatch brief must state it so an oversized diff is "
            f"visible before anyone reads it. Recomputed from git: Diff-size: {real_files} "
            f"files, {real_bytes} bytes. Add that line to the brief and re-dispatch.")

    claimed = (int(size_m.group(1)), int(size_m.group(2)))
    if claimed != (real_files, real_bytes):
        return 2, (
            f"karta: this gate-reviewer dispatch's Diff-size line claims {claimed[0]} files, "
            f"{claimed[1]} bytes, but git recomputes Diff-size: {real_files} files, "
            f"{real_bytes} bytes — a fictional stat is a brief defect, distinct from a missing "
            "line. Fix the line to match the recomputed truth and re-dispatch.")

    return 0, ""


def _run_self_test() -> int:
    import tempfile
    from pathlib import Path

    def run(*args, cwd):
        subprocess.run(args, cwd=cwd, check=True, capture_output=True)

    with tempfile.TemporaryDirectory() as td:
        repo = str(Path(td) / "repo")
        Path(repo).mkdir()
        run("git", "init", "-q", cwd=repo)
        run("git", "config", "user.email", "t@example.com", cwd=repo)
        run("git", "config", "user.name", "t", cwd=repo)
        (Path(repo) / "a.txt").write_text("one\n")
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-q", "-m", "base", cwd=repo)
        run("git", "branch", "base", cwd=repo)
        (Path(repo) / "a.txt").write_text("one\ntwo\n")
        (Path(repo) / "b.txt").write_text("new file\n")
        run("git", "add", "-A", cwd=repo)
        run("git", "commit", "-q", "-m", "feature", cwd=repo)
        run("git", "branch", "feature", cwd=repo)
        # A dotted ref, because that is what this repo actually tags and branches. The
        # first grammar here stopped the rev class at every dot, so "v2.31.0..HEAD" was
        # read as "0..HEAD" and a legitimate dispatch was denied.
        run("git", "branch", "release/2.31.0", "feature", cwd=repo)
        # A real directory whose name genuinely ends in dots, so the case below tests what
        # its name says rather than passing for the traversal case's reason.
        (Path(repo) / "release..").mkdir()
        # A real directory named with trailing clause punctuation, carrying repo's
        # branches, beside a sibling WITHOUT them. If the guard strips the `;` it lands
        # on the sibling and the range fails to resolve — which is how round 6 caught it.
        import shutil
        twin_punct = str(Path(td) / "twin;")
        shutil.copytree(repo, twin_punct)
        twin_plain = str(Path(td) / "twin")
        Path(twin_plain).mkdir()
        run("git", "init", "-q", cwd=twin_plain)
        # A FILE named with trailing clause punctuation beside a real tree of the bare
        # name: the peel must stop on the file (it exists) and let git deny, not peel
        # past it to the tree beside it.
        trio = str(Path(td) / "trio")
        shutil.copytree(repo, trio)
        trio_file_punct = trio + ";"
        Path(trio_file_punct).write_text("not a tree\n")

        real_files, real_bytes = _diff_stat(repo, "base..feature")
        good_size_line = f"Diff-size: {real_files} files, {real_bytes} bytes"
        bad_size_line = f"Diff-size: {real_files + 9} files, {real_bytes + 999} bytes"
        missing_dir = str(Path(td) / "nope")

        def dispatch(prompt: str, subagent: str = "karta-safety-auditor",
                     worktree: str = repo) -> dict:
            return {"hook_event_name": "PreToolUse", "tool_name": "Task", "cwd": worktree,
                    "tool_input": {"subagent_type": subagent, "description": "boundary scan",
                                   "prompt": prompt}}

        cases = [
            ("unrecognized subagent passes with no diff-size line",
             dispatch("build item a", subagent="karta-build"), 0, None),
            ("mention of the gate agents in prose alone is not recognition",
             dispatch("after the build, karta-safety-auditor scans it",
                      subagent="karta-build"), 0, None),
            ("recognized acceptance-reviewer, well-formed range and Diff-size line passes",
             dispatch(f"worktree {repo}; diff range base..feature. {good_size_line}",
                      subagent="karta-acceptance-reviewer"), 0, None),
            ("recognized safety-auditor, well-formed range and Diff-size line passes",
             dispatch(f"worktree {repo}; diff range base..feature. {good_size_line}"), 0, None),
            ("a dotted ref resolves — the rev class allows dots without eating the separator",
             dispatch(f"worktree {repo}; diff range base..release/2.31.0. {good_size_line}"),
             0, None),
            ("a trailing full stop is not part of the endpoint",
             dispatch(f"worktree {repo}; diff range base..feature. {good_size_line}"), 0, None),
            ("a worktree path ending in a dot is never guessed at — it denies rather "
             "than resolving a different tree",
             dispatch(f"worktree {repo}. diff range base..feature. {good_size_line}",
                      worktree=missing_dir), 2, "could not resolve"),
            ("a traversal path keeps its dots — they are the path, not punctuation",
             dispatch(f"worktree {repo}/.git/.. diff range base..feature. "
                      f"{good_size_line}", worktree=missing_dir), 0, None),
            ("an ordinary directory whose name ends in dots is left alone",
             dispatch(f"worktree {repo}/release.. diff range base..feature. "
                      f"{good_size_line}", worktree=missing_dir), 0, None),
            ("a directory whose name ends in clause punctuation is used as written, not "
             "stripped to its sibling",
             dispatch(f"worktree {twin_punct} diff range base..feature. {good_size_line}",
                      worktree=missing_dir), 0, None),
            ("negative control: the sibling really lacks those branches, so the case "
             "above passed for the right reason",
             dispatch(f"worktree {twin_plain} diff range base..feature. {good_size_line}",
                      worktree=missing_dir), 2, "could not resolve"),
            ("clause punctuation AFTER such a directory is peeled one character at a time, "
             "stopping at the directory rather than overshooting to its sibling",
             dispatch(f"worktree {twin_punct}; diff range base..feature. {good_size_line}",
                      worktree=missing_dir), 0, None),
            ("a quoted directory of that kind followed by clause punctuation is unquoted "
             "at the right step",
             dispatch(f'worktree "{twin_punct}"; diff range base..feature. {good_size_line}',
                      worktree=missing_dir), 0, None),
            ("a quoted form of it with extra clause punctuation inside the quotes is "
             "unquoted first, then peeled to the directory",
             dispatch(f'worktree "{twin_punct};" diff range base..feature. {good_size_line}',
                      worktree=missing_dir), 0, None),
            ("an UNMATCHED leading quote is never scrubbed off to reach a sibling tree — "
             "the mention is prose and the payload cwd is used, which here does not exist",
             dispatch(f'worktree "{twin_punct} diff range base..feature. {good_size_line}',
                      worktree=missing_dir), 2, "fails closed"),
            ("a file at the longer name stops the peel and denies rather than peeling "
             "past it to the real tree beside it",
             dispatch(f"worktree {trio_file_punct} diff range base..feature. "
                      f"{good_size_line}", worktree=missing_dir), 2, "fails closed"),
            ("the word after a prose 'worktree' is not taken as a path",
             dispatch(f"reviewed in the worktree at length; diff range base..feature. "
                      f"{good_size_line}", worktree=repo), 0, None),
            ("namespaced subagent type is recognized",
             dispatch(f"worktree {repo}; diff range base..feature. {good_size_line}",
                      subagent="karta:karta-safety-auditor"), 0, None),
            ("empty range denies, naming the whiff/already-present causes",
             dispatch(f"worktree {repo}; diff range base..base. {good_size_line}"),
             2, "whiff"),
            ("missing range denies",
             dispatch(f"worktree {repo}; {good_size_line}"), 2, "diff range"),
            ("missing Diff-size line denies, printing the recomputed stat",
             dispatch(f"worktree {repo}; diff range base..feature."),
             2, f"Diff-size: {real_files} files, {real_bytes} bytes"),
            ("mismatched Diff-size numbers deny, distinct from the missing-line case",
             dispatch(f"worktree {repo}; diff range base..feature. {bad_size_line}"),
             2, "fictional stat"),
            ("unresolvable worktree/range is an internal-error fail-closed deny",
             dispatch(f"worktree {missing_dir}; diff range base..feature. {good_size_line}",
                      worktree=missing_dir),
             2, "fails closed"),
            ("tool_input not a dict passes",
             {"hook_event_name": "PreToolUse", "tool_name": "Task", "cwd": repo,
              "tool_input": "junk"}, 0, None),
        ]
        failures = 0
        for name, payload, want, needle in cases:
            code, msg = decide(payload)
            ok = code == want and (needle is None or needle in msg)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
            failures += 0 if ok else 1

    total = len(cases)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    payload: dict = {}
    try:
        raw = json.load(sys.stdin)
        if isinstance(raw, dict):
            payload = raw
    except Exception:  # noqa: BLE001
        return 0  # an unreadable payload is an unrecognized shape — pass
    try:
        code, reason = decide(payload)
    except Exception:  # noqa: BLE001
        # fail closed only on the shape this guard exists for; everything else passes
        tool_input = payload.get("tool_input")
        try:
            recognized = isinstance(tool_input, dict) and _recognized(tool_input)
        except Exception:  # noqa: BLE001
            recognized = False
        if not recognized:
            return 0
        code, reason = 2, (
            "karta: internal error while checking a gate-reviewer dispatch — this guard fails "
            "closed. Re-dispatch with a resolvable worktree path, a `<rev>..<rev>` diff range, "
            "and a `Diff-size: <files> files, <bytes> bytes` line.")
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
