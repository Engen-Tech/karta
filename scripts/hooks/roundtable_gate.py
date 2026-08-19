# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""House review gate: a Claude Code PreToolUse hook on the Bash tool.

Wired in .karta/../.claude/settings.json (karta repo tooling, NOT the plugin
surface), beside precommit_gate.py. Reads the PreToolUse payload JSON from
stdin and, for the karta repo's own development, requires that a multi-model
review has been recorded before the maintainer commits a plan (binder) file or
lands a delivery branch on the default branch. It is a pre-commit / pre-merge
check, directly analogous to precommit_gate.py; the review itself is produced
by the roundtable MCP tool the agent runs and filed by scripts/roundtable/run_review.py.

The gate keys on the deterministic fact "a fresh recorded review exists for
this exact content" (the staged binder blob, or the integration branch tip),
never on the review's verdict. Two detections, both by git plumbing and
command text only — never by parsing a diff, never by evaluating a
post-condition a PreToolUse hook cannot see:

  (a) BINDER-COMMIT gate: a git commit that would record a change to a
      .karta/binders/<slug>.json plan file needs a fresh record whose stored
      hash matches the binder content being committed, and the record itself
      must be in the commit.
  (b) INTEGRATION-MERGE gate: on the default branch, a git merge naming a
      karta/*/integration ref needs a fresh record for that branch tip.
  (c) LANDING gate: on the default branch, a git merge naming a
      karta/*/integration ref is blocked outright unless KARTA_LANDING_APPROVED=1
      is present. This one is NOT about a review at all. karta stops at the
      assembled integration branch by design — no PR, no push, no auto-merge —
      so the merge is a separate act, and who decides a delivery ships is
      always the human. Assembling the branch and running the floor are the
      agent's; the decision to land is not.

git cherry-pick / rebase / reset --hard / a merge --squash then a separate
commit are accepted, documented bypasses of the same class as the escape
hatch: a PreToolUse hook cannot evaluate "will this make the tip an ancestor".
The landing gate catches every `git merge` form including --squash, but shares
the cherry-pick / rebase / reset --hard limit, and shares the fail-open rule
below. Both are named rather than papered over.

Config .karta/roundtable.json gates the gates: absent or enabled:false turns
everything off; points.plan_commit / points.deliver_merge toggle each detection.
Escape hatch: KARTA_SKIP_ROUNDTABLE=1 in the command text or the environment.

The landing gate (c) is deliberately outside all of that. The config switch does
not disable it and KARTA_SKIP_ROUNDTABLE does not bypass it, because that hatch
means "the review environment is down" — which says nothing about who decides a
delivery ships. Its own variable, KARTA_LANDING_APPROVED=1, exists for the human
to type. An agent that sets it has forged an approval it was never given; a
PreToolUse hook sees only command text and cannot tell the two apart, so the
rule lives in doctrine (CLAUDE.md, AGENTS.md) and the gate makes the moment
impossible to pass through silently.
Internal errors fail OPEN (exit 0) so a broken hook never wedges the repo; a
missing or stale record is an expected result that blocks (exit 2), not an
internal error.

  python3 roundtable_gate.py < payload.json    # hook mode, exit 0/2
  python3 roundtable_gate.py --self-test        # embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root
HELPER = "scripts/roundtable/run_review.py"
CONFIG_PATH = ".karta/roundtable.json"
RECORD_DIR = ".karta/roundtable/"
BRANCH_PREFIX = "branch-"
SKIP_VAR = "KARTA_SKIP_ROUNDTABLE"
LAND_VAR = "KARTA_LANDING_APPROVED"  # gate (c); NOT covered by SKIP_VAR
INTEGRATION_GLOB = "karta/*/integration"  # the shape the merge gate matches
GIT_TIMEOUT = 30

_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")
# `git commit` / `git merge`: word-boundary match where anything between the two
# words must be option tokens (each optionally trailing one non-dash argument),
# matching precommit_gate.py's conservative detection.
_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit\b")
_MERGE_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+merge\b")
# an integration ref named anywhere in a merge command: karta/<slug>/integration
_INTEGRATION_REF_RE = re.compile(r"\bkarta/[^\s/]+/integration\b")
_BINDER_PATH_RE = re.compile(r"\.karta/binders/([^/\s]+)\.json\b")
# a leading `VAR=value ` shell assignment, so an invocation prefixed with one
# still reads as its own head for the anchored matcher below
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+")


def _segments(command: str) -> list[str]:
    return _SPLIT_RE.split(command)


def is_commit_command(command: str) -> bool:
    return any(_COMMIT_RE.search(seg) for seg in _segments(command))


def is_merge_command(command: str) -> bool:
    return any(_MERGE_RE.search(seg) for seg in _segments(command))


def _leading_command(segment: str) -> str:
    """A shell segment with any leading VAR=value assignments stripped, so an
    invocation prefixed with environment settings still reads as its own head."""
    seg = segment
    while True:
        m = _ENV_ASSIGN_RE.match(seg)
        if not m:
            return seg
        seg = seg[m.end():]


def merge_invocation(command: str) -> tuple[str, bool] | None:
    """For a merge of a karta/<slug>/integration ref that a segment actually
    STARTS with: (the ref, whether LAND_VAR=1 prefixes that same invocation).

    Two things are deliberately narrower here than in the other two gates.

    Anchored, not searched. The other gates search anywhere in a segment, which
    is safe while they are inert but not for a gate that always fires: a merge
    command written inside a heredoc, echoed into a file, or handed to grep is
    text, not an invocation, and blocking it would stop ordinary work — editing
    this very file included. Anchoring at the head of the segment (after any
    VAR=value prefix) tells the two apart. The trade is named rather than
    hidden: an invocation buried mid-segment, behind a `do` or an `xargs`, reads
    as text and is not caught — the same class as the cherry-pick / rebase /
    reset --hard bypasses already documented here. It errs toward letting real
    work through rather than toward blocking prose about a merge.

    Approval must prefix the merge itself. The skip hatch is matched against the
    whole command, which is fine for a hatch that only ever loosens a review
    requirement. This one grants authority, so an accidental grant is worse than
    an accidental block: the assignment has to sit in front of the invocation it
    approves, not merely appear somewhere in the same command line."""
    for seg in _segments(command):
        stripped = seg.strip()
        head = _leading_command(stripped)
        if not _MERGE_RE.match(head):
            continue
        ref = merged_integration_ref(head)
        if ref:
            prefix = stripped[:len(stripped) - len(head)]
            return ref, f"{LAND_VAR}=1" in prefix
    return None


def commit_reads_worktree(command: str) -> bool:
    """True when the recorded content is the working tree rather than the index:
    a `-a`/`-am`/`--all` or `--amend` commit, or a pathspec commit naming a
    binder path. Otherwise the plain `git commit` records the staged index."""
    for seg in _segments(command):
        if not _COMMIT_RE.search(seg):
            continue
        toks = seg.split()
        for t in toks:
            if t in ("-a", "--all", "--amend") or (t.startswith("-") and not t.startswith("--")
                                                   and "a" in t[1:] and all(c.isalpha() for c in t[1:])):
                return True
        # pathspec: a binder path appears as a bare token in the commit segment
        if _BINDER_PATH_RE.search(seg):
            return True
    return False


def merged_integration_ref(command: str) -> str | None:
    """The karta/<slug>/integration ref named in a `git merge` segment, or None."""
    for seg in _segments(command):
        if not _MERGE_RE.search(seg):
            continue
        m = _INTEGRATION_REF_RE.search(seg)
        if m:
            return m.group(0)
    return None


def slug_of(binder_path: str) -> str:
    return Path(binder_path).stem


# --- git / helper seams (injected so --self-test needs no real repo) ----------

def _real_git(argv: list[str], input_bytes: bytes | None = None) -> tuple[int, bytes]:
    try:
        proc = subprocess.run(["git", *argv], cwd=ROOT, timeout=GIT_TIMEOUT,
                              input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return proc.returncode, proc.stdout or b""
    except Exception:
        return 1, b""


def _real_helper(args: list[str], input_bytes: bytes | None) -> int:
    py = sys.executable or "python3"
    try:
        proc = subprocess.run([py, str(ROOT / HELPER), *args], cwd=ROOT, timeout=GIT_TIMEOUT,
                              input=input_bytes, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode
    except Exception:
        return 1


def _real_read(path: str) -> bytes | None:
    try:
        return (ROOT / path).read_bytes()
    except OSError:
        return None


def _binder_paths(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln.startswith(".karta/binders/") and ln.endswith(".json") and "/archive/" not in ln:
            out.append(ln)
    return out


def binders_to_check(command: str, git) -> list[str]:
    """The binder plan files this commit would record a change to."""
    code, cached = git(["diff", "--cached", "--name-only"])
    paths = set(_binder_paths(cached.decode(errors="replace").splitlines()) if code == 0 else [])
    if commit_reads_worktree(command):
        code2, wt = git(["diff", "--name-only"])
        if code2 == 0:
            paths |= set(_binder_paths(wt.decode(errors="replace").splitlines()))
        for m in _BINDER_PATH_RE.finditer(command):
            paths.add(f".karta/binders/{m.group(1)}.json")
    return sorted(paths)


def binder_bytes(path: str, command: str, git, read_file) -> bytes | None:
    """The content that will be committed: the working-tree file for -a/-am/
    pathspec/--amend, else the staged blob (git show :<path>)."""
    if commit_reads_worktree(command):
        return read_file(path)
    code, out = git(["show", f":{path}"])
    return out if code == 0 else None


def record_in_commit(slug: str, git) -> bool:
    """The review record must ride in the commit: staged, or already in HEAD."""
    rec = f"{RECORD_DIR}{slug}.json"
    code, cached = git(["diff", "--cached", "--name-only"])
    if code == 0 and rec in cached.decode(errors="replace").splitlines():
        return True
    code2, _ = git(["cat-file", "-e", f"HEAD:{rec}"])
    return code2 == 0


def default_branch(git) -> str:
    code, out = git(["symbolic-ref", "refs/remotes/origin/HEAD"])
    if code == 0:
        ref = out.decode(errors="replace").strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref[len("refs/remotes/origin/"):]
    return "main"


def current_branch(git) -> str:
    code, out = git(["symbolic-ref", "--short", "HEAD"])
    return out.decode(errors="replace").strip() if code == 0 else ""


def _record_cmd(slug_or_branch: str, kind: str) -> str:
    if kind == "branch":
        return (f"<run the roundtable panel, then> ... | python3 {HELPER} "
                f"--record --target {slug_or_branch} --kind branch")
    return (f"<run the roundtable panel, then> ... | python3 {HELPER} "
            f"--record --target {slug_or_branch} --kind binder")


def _deny(reason_core: str, record_cmd: str) -> str:
    return (f"{reason_core} The house roundtable edict requires a fresh recorded "
            f"review under {RECORD_DIR} before this lands. Record one with:\n  {record_cmd}\n"
            f"then retry. For an intentional skip (e.g. the review environment is down), "
            f"prefix the command with {SKIP_VAR}=1 (documented escape hatch).")


def _deny_landing(ref: str, branch: str) -> str:
    return (f"Merge blocked: landing {ref} on {branch} is the human's decision, not an agent's.\n"
            f"karta stops at the assembled integration branch by design — no PR, no push, no "
            f"auto-merge — so the merge is a separate act. Assembling the branch and running the "
            f"floor are yours; deciding it ships is not.\n"
            f"If you are an agent: do not set {LAND_VAR}. Say the branch is assembled, say the "
            f"floor result, and ask. The human runs the merge.\n"
            f"If you are the human: run the merge yourself, or prefix it with {LAND_VAR}=1.\n"
            f"Note that {SKIP_VAR} does not bypass this — that hatch is for a downed review "
            f"environment, which has nothing to do with who decides a delivery ships.")


def decide(payload, env, git, helper, config, read_file=_real_read) -> tuple[int, str]:
    """(exit_code, stderr_message). Pure over its inputs so --self-test can drive
    it with fabricated payloads and stubbed git/helper."""
    if not isinstance(payload, dict):
        return 0, ""
    tool_input = payload.get("tool_input")
    if payload.get("tool_name", "Bash") != "Bash" or not isinstance(tool_input, dict):
        return 0, ""
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0, ""

    # (c) LANDING gate. Deliberately ahead of both the skip hatch and the config
    # switch: neither one speaks to who decides a delivery ships, and that answer
    # never changes. Always the human.
    landing = merge_invocation(command)
    if landing:
        ref, approved_inline = landing
        branch = current_branch(git)
        if branch == default_branch(git) and not (approved_inline
                                                  or env.get(LAND_VAR) == "1"):
            return 2, _deny_landing(ref, branch)

    if f"{SKIP_VAR}=1" in command or env.get(SKIP_VAR) == "1":
        return 0, ""
    if not isinstance(config, dict) or not config.get("enabled"):
        return 0, ""
    points = config.get("points") if isinstance(config.get("points"), dict) else {}

    # (a) binder-commit gate
    if points.get("plan_commit") and is_commit_command(command):
        for path in binders_to_check(command, git):
            slug = slug_of(path)
            data = binder_bytes(path, command, git, read_file)
            if data is None:
                continue  # nothing readable to commit for this path
            rc = helper(["--check", "--target", slug, "--kind", "binder", "--bytes-stdin"], data)
            if rc != 0:
                return 2, _deny(
                    f"Commit blocked: plan file {path} has no fresh recorded review "
                    f"(run_review.py --check found none matching the content being committed).",
                    _record_cmd(slug, "binder"))
            if not record_in_commit(slug, git):
                return 2, _deny(
                    f"Commit blocked: the review record {RECORD_DIR}{slug}.json for {path} is "
                    f"not part of this commit (stage it so the audit trail survives checkout).",
                    _record_cmd(slug, "binder"))

    # (b) integration-merge gate
    if points.get("deliver_merge") and is_merge_command(command):
        ref = merged_integration_ref(command)
        if ref and current_branch(git) == default_branch(git):
            rc = helper(["--check", "--target", ref, "--kind", "branch"], None)
            if rc != 0:
                return 2, _deny(
                    f"Merge blocked: {ref} has no fresh recorded review for its current tip "
                    f"(expected a {RECORD_DIR}{BRANCH_PREFIX}<tip-sha>.json record).",
                    _record_cmd(ref, "branch"))

    return 0, ""


def load_config(git=None) -> dict:
    try:
        return json.loads((ROOT / CONFIG_PATH).read_text())
    except (OSError, ValueError):
        return {}


def hook_main(stdin_text: str, env, git, helper, config) -> tuple[int, str]:
    try:
        payload = json.loads(stdin_text)
    except (ValueError, TypeError):
        return 0, ""
    try:
        return decide(payload, env, git, helper, config)
    except Exception as e:  # fail-open: a broken hook must never wedge the repo
        print(f"roundtable_gate: internal error, failing open: {e}", file=sys.stderr)
        return 0, ""


# --- self-test ----------------------------------------------------------------

def _payload(command: str, tool: str = "Bash") -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"command": command}}


def _run_self_test() -> int:
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    CFG = {"enabled": True, "points": {"plan_commit": True, "deliver_merge": True}}

    # detection
    check("detect commit", is_commit_command('git commit -m x') and not is_commit_command("git status"))
    check("detect merge", is_merge_command("git merge --no-ff karta/x/integration")
          and not is_merge_command("git status"))
    check("worktree read for -a", commit_reads_worktree("git commit -am x"))
    check("worktree read for --amend", commit_reads_worktree("git commit --amend --no-edit"))
    check("worktree read for binder pathspec", commit_reads_worktree("git commit .karta/binders/x.json -m y"))
    check("staged read for plain commit", not commit_reads_worktree('git commit -m "x"'))
    check("integration ref extracted", merged_integration_ref("git merge --squash karta/foo/integration") == "karta/foo/integration")
    check("no ref for unrelated merge", merged_integration_ref("git merge feature/x") is None)

    # stub git: a staged binder x.json, its record NOT staged but in HEAD; branch tip resolvable
    def git_factory(staged, worktree=None, record_staged=False, record_in_head=True,
                    cur="main", default="main"):
        def git(argv, input_bytes=None):
            a = argv
            if a[:3] == ["diff", "--cached", "--name-only"]:
                names = list(staged)
                if record_staged:
                    names.append(f"{RECORD_DIR}x.json")
                return 0, ("\n".join(names) + "\n").encode()
            if a[:2] == ["diff", "--name-only"]:
                return 0, ("\n".join(worktree or []) + "\n").encode()
            if a[0] == "show":
                return 0, b'{"slug":"x"}'
            if a[:2] == ["cat-file", "-e"]:
                return (0, b"") if record_in_head else (1, b"")
            if a == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
                return 0, f"refs/remotes/origin/{default}\n".encode()
            if a == ["symbolic-ref", "--short", "HEAD"]:
                return 0, f"{cur}\n".encode()
            return 1, b""
        return git

    fresh = lambda args, data: 0
    stale = lambda args, data: 1

    # binder-commit gate
    g = git_factory([".karta/binders/x.json"])
    code, _ = decide(_payload('git commit -m x'), {}, g, stale, CFG)
    check("stale binder record blocks commit (exit 2)", code == 2)
    code, r = decide(_payload('git commit -m x'), {}, g, fresh, CFG)
    check("fresh record + record in HEAD allows commit", code == 0, f"code={code}")
    g2 = git_factory([".karta/binders/x.json"], record_in_head=False)
    code, r = decide(_payload('git commit -m x'), {}, g2, fresh, CFG)
    check("fresh record but record not in commit blocks", code == 2)
    check("record-not-in-commit reason mentions staging", "stage it" in r or "not part of this commit" in r)
    g3 = git_factory([".karta/binders/x.json"], record_staged=True, record_in_head=False)
    code, _ = decide(_payload('git commit -m x'), {}, g3, fresh, CFG)
    check("fresh record staged in same commit allows", code == 0)
    code, _ = decide(_payload('git commit -m x'), {}, git_factory([]), stale, CFG)
    check("commit staging no binder allows", code == 0)
    # -a form reads worktree binder (via the injected file reader, not disk)
    g4 = git_factory([], worktree=[".karta/binders/x.json"])
    read_wt = lambda p: b'{"slug":"x"}'
    code, _ = decide(_payload('git commit -am x'), {}, g4, stale, CFG, read_file=read_wt)
    check("git commit -a with stale worktree-binder record blocks", code == 2)

    # deny reason content
    code, r = decide(_payload('git commit -m x'), {}, git_factory([".karta/binders/x.json"]), stale, CFG)
    check("deny reason names record dir", RECORD_DIR in r)
    check("deny reason names the helper --record", "run_review.py --record" in r)
    check("deny reason names the escape", SKIP_VAR in r)

    # merge gate
    gm = git_factory([], cur="main", default="main")
    code, _ = decide(_payload("git merge --no-ff karta/x/integration"), {}, gm, stale, CFG)
    check("merge of integration on default branch, stale, blocks", code == 2)
    code, _ = decide(_payload(f"{LAND_VAR}=1 git merge --no-ff karta/x/integration"), {}, gm, fresh, CFG)
    check("integration merge on default, fresh record + landing approved, allows", code == 0)
    goff = git_factory([], cur="feature", default="main")
    code, _ = decide(_payload("git merge --no-ff karta/x/integration"), {}, goff, stale, CFG)
    check("same merge off the default branch allows (not gated)", code == 0)
    code, _ = decide(_payload("git merge feature/y"), {}, gm, stale, CFG)
    check("unrelated merge allows", code == 0)

    # accepted bypasses are not gated
    for cmd in ["git cherry-pick abc123", "git rebase main", "git reset --hard karta/x/integration"]:
        code, _ = decide(_payload(cmd), {}, gm, stale, CFG)
        check(f"accepted bypass not gated: {cmd}", code == 0)

    # escape + config
    code, _ = decide(_payload('KARTA_SKIP_ROUNDTABLE=1 git commit -m x'), {},
                     git_factory([".karta/binders/x.json"]), stale, CFG)
    check("KARTA_SKIP_ROUNDTABLE=1 in command skips", code == 0)
    code, _ = decide(_payload('git commit -m x'), {"KARTA_SKIP_ROUNDTABLE": "1"},
                     git_factory([".karta/binders/x.json"]), stale, CFG)
    check("KARTA_SKIP_ROUNDTABLE=1 in env skips", code == 0)
    code, _ = decide(_payload('git commit -m x'), {}, git_factory([".karta/binders/x.json"]), stale, {})
    check("absent/disabled config allows", code == 0)
    code, _ = decide(_payload('git commit -m x'), {}, git_factory([".karta/binders/x.json"]), stale,
                     {"enabled": True, "points": {"plan_commit": False, "deliver_merge": True}})
    check("plan_commit:false disables only the binder gate", code == 0)
    code, _ = decide(_payload(f"{LAND_VAR}=1 git merge --no-ff karta/x/integration"), {}, gm, stale,
                     {"enabled": True, "points": {"plan_commit": True, "deliver_merge": False}})
    check("deliver_merge:false disables only the roundtable merge gate", code == 0)

    # (c) landing gate — who decides it ships. Independent of the config switch
    # and of the roundtable skip hatch, and anchored so quoted text is not an act.
    MERGE = "git merge --ff-only karta/x/integration"
    code, r = decide(_payload(MERGE), {}, gm, fresh, CFG)
    check("landing gate blocks even with a fresh roundtable record", code == 2)
    code, _ = decide(_payload(MERGE), {}, gm, fresh, {})
    check("landing gate blocks with the roundtable config off", code == 2)
    code, _ = decide(_payload(MERGE), {}, gm, fresh,
                     {"enabled": True, "points": {"plan_commit": True, "deliver_merge": False}})
    check("landing gate blocks with deliver_merge:false", code == 2)
    code, _ = decide(_payload(f"{SKIP_VAR}=1 {MERGE}"), {}, gm, fresh, {})
    check("KARTA_SKIP_ROUNDTABLE does NOT bypass the landing gate", code == 2)
    code, _ = decide(_payload(MERGE), {SKIP_VAR: "1"}, gm, fresh, {})
    check("KARTA_SKIP_ROUNDTABLE in env does NOT bypass the landing gate", code == 2)
    code, _ = decide(_payload(f"{LAND_VAR}=1 {MERGE}"), {}, gm, fresh, {})
    check("landing approved in front of the merge allows", code == 0)
    code, _ = decide(_payload(MERGE), {LAND_VAR: "1"}, gm, fresh, {})
    check("landing approved in the env allows", code == 0)
    code, _ = decide(_payload(MERGE), {}, goff, fresh, {})
    check("landing gate does not fire off the default branch", code == 0)
    code, _ = decide(_payload("git merge --squash karta/x/integration"), {}, gm, fresh, {})
    check("landing gate catches the --squash form too", code == 2)
    code, _ = decide(_payload("git merge feature/y"), {}, gm, fresh, {})
    check("landing gate ignores a merge that is not an integration branch", code == 0)
    for cmd in ["git cherry-pick abc123", "git rebase main", "git reset --hard karta/x/integration"]:
        code, _ = decide(_payload(cmd), {}, gm, fresh, {})
        check(f"landing gate shares the accepted bypass: {cmd}", code == 0)

    # anchoring: a merge command as TEXT is not a merge being run
    for quoted in [f'echo "{MERGE}" > note.txt',
                   f"grep -n '{MERGE}' docs/how-to/roundtable.md",
                   f'python3 - <<EOF\ncmd = "{MERGE}"\nEOF']:
        code, _ = decide(_payload(quoted), {}, gm, fresh, {})
        check("quoted merge text is not an invocation: "
              + quoted.split()[0], code == 0, f"code={code}")
    code, _ = decide(_payload(f"cd /repo && {MERGE}"), {}, gm, fresh, {})
    check("a real merge after && is still caught", code == 2)

    # approval has to prefix the merge itself, not merely appear in the command
    code, _ = decide(_payload(f'echo "{LAND_VAR}=1" ; {MERGE}'), {}, gm, fresh, {})
    check("approval mentioned elsewhere in the command does not grant it", code == 2)

    code, r = decide(_payload(MERGE), {}, gm, fresh, {})
    check("landing deny names the human as the decider", "the human's decision" in r)
    check("landing deny names its own variable", LAND_VAR in r)
    check("landing deny tells an agent not to set it", f"do not set {LAND_VAR}" in r)
    check("landing deny says the skip hatch will not help", f"{SKIP_VAR} does not bypass" in r)

    # fail-open
    code, _ = hook_main("not json", {}, git_factory([]), stale, CFG)
    check("malformed payload fails open", code == 0)
    def exploding(argv, input_bytes=None): raise RuntimeError("boom")
    code, _ = hook_main(json.dumps(_payload("git commit -m x")), {}, exploding, stale, CFG)
    check("git exception fails open", code == 0)
    code, _ = decide(_payload("ls -la"), {}, git_factory([]), stale, CFG)
    check("non-command allows", code == 0)

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    code, message = hook_main(sys.stdin.read(), os.environ, _real_git, _real_helper, load_config())
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
