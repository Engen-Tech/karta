# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Dev-repo commit gate: a Claude Code PreToolUse hook on the Bash tool.

Wired in .claude/settings.json (karta repo tooling, NOT the plugin surface).
Reads the PreToolUse payload JSON from stdin; when tool_input.command contains
a `git commit` invocation it runs the repo gate suite from the INVOCATION ROOT
— the tree the payload's cwd names, resolved through _worktree.py, so a commit
issued in a linked worktree is judged on that worktree (register INV-11) —
check_shared_copies, check_invariant_register, sync_codex_skills --check,
sync_codex_agents --check, validate_plugin, and validate_packs over
skills/_shared/sme/ — and exits 2 with the failing gate's name plus an output
tail (last ~40 lines) so the commit is blocked with actionable feedback. All
gates green, or any command that is not a git commit, exits 0. Escape hatch for
intentional partial commits: KARTA_SKIP_GATE=1 in the command text or the
environment.

Internal errors (unreadable stdin, malformed payload, unexpected exceptions)
fail OPEN — exit 0 — so a broken hook never wedges the repo. A gate that runs
and fails (or times out) is not an internal error: that blocks — with one named
exception, the register checker, whose crash and timeout paths are described in
DENY_CODES below.

Binder-validity step: a commit that would record a live binder under .karta/binders/
is refused when those exact bytes do not validate. Per commit shape,
the bytes are the ones git actually records
— index blobs for a plain commit, working-tree bytes for -a, for a pathspec, and for a
chained `git add` (untracked files included when the add covers them) — and only a real
finding from validate_binder.py blocks: a crash, a missing validator, or an exhausted
budget fails OPEN with a warning.

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
import argparse, json, os, posixpath, re, shlex, subprocess, sys, tempfile, time
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the sibling module below
from _worktree import resolve_invocation_root  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root

# --- the timeout budget, derived ---------------------------------------------------
#
# The harness must never kill this hook mid-run: a timed-out PreToolUse hook does not
# block, so the whole gate budget has to fit inside the hook's own timeout with room to
# spare. The margin is DERIVED from that timeout rather than restated beside it, so the
# two cannot silently disagree.
HOOK_TIMEOUT = 600   # the `timeout` this hook is wired with in .claude/settings.json,
                     # re-read 2026-09-02. A self-test case below reads that file and
                     # fails when the two drift, so this number is checked, not asserted.
HOOK_OVERHEAD = 60   # what this hook spends OUTSIDE its gates: interpreter start, the
                     # invocation-root resolver's pointer-file reads, the release block's
                     # git plumbing, payload I/O, process teardown.
KILL_MARGIN = HOOK_TIMEOUT - HOOK_OVERHEAD   # 540s of gate budget

GATE_TIMEOUT = 90    # seconds per gate; a hung gate is a failed gate, not a stall
REGISTER_GATE_TIMEOUT = 20   # the register checker parses one markdown file and stats a
                             # few dozen paths: no subprocess, no network, no git
GATE_INCOMPLETE = -1  # the runner's own code for "did not run to completion" (a timeout
                      # or a failed spawn), which is never a real gate's exit status
# Worst case, every gate burning its whole timeout: 5 x 90 (the four repo gates plus the
# pack gate) + 20 (the register checker) + BINDER_BUDGET 50 = 520 <= KILL_MARGIN 540. The
# case register-timeout-budget-under-margin sums the LIVE gate list plus the binder budget
# and asserts that against KILL_MARGIN, so adding a gate moves the sum, never the assertion.

TAIL_LINES = 40      # cap on the captured output relayed in a deny reason
SKIP_VAR = "KARTA_SKIP_GATE"

# Register checker: which exit codes DENY. Every other gate blocks on any nonzero, which
# stays the default for a gate absent from this map.
#
# check_invariant_register.py pins 0 = clean, 1 = a named verification or parse failure,
# 2 = its own internal crash. Only 1 blocks. A crash, a timeout, or a failed spawn is not
# a verdict and fails OPEN with a warning — the same asymmetry the binder step keeps, and
# INV-21's rule that an error never wedges the repo. A DRIFTED register still denies by
# name: the register is doctrine, and a broken doctrine file blocking a commit is exactly
# what a drifted mirror already does in this suite.
REGISTER_GATE = "check_invariant_register"
DENY_CODES = {REGISTER_GATE: (1,)}

# Release block: a version bump must ship with a green full-gate file for the new
# version and this commit's parent HEAD, staged into the same commit.
PLUGIN_JSON = ".claude-plugin/plugin.json"
GATE_RESULTS_REL = "benchmarks/results/gate"
RUN_GATE_CMD = "python3 benchmarks/gate/run_gate.py"

# Binder-validity step. A live binder is any path under .karta/binders/ AT ANY DEPTH
# ending .json that is not under the archive — matched by prefix and suffix, never by a
# single-star glob: `.karta/binders/*.json` cannot cross a slash, so a nested binder
# would fall through such a pattern and the branch behind it would be dead code.
BINDER_DIR = ".karta/binders/"
BINDER_ARCHIVE = ".karta/binders/archive/"
BINDER_SUFFIX = ".json"
VALIDATE_BINDER_REL = "skills/karta-plan/scripts/validate_binder.py"
BINDER_BUDGET = 50.0  # seconds, END-TO-END: enumeration, materialization and every
                      # validator run together. Checked between candidates and passed
                      # as each subprocess's timeout, so the step cannot outlive it.
# The first line of every findings report validate_binder.py prints, and the only thing
# that turns a nonzero exit into a denial here. Keep in step with FINDINGS_SENTINEL in
# skills/karta-plan/scripts/validate_binder.py — the self-test below runs the real
# validator against a real invalid binder to prove the two still agree.
BINDER_FINDINGS_SENTINEL = "KARTA-BINDER-FINDINGS/1"

# `git commit` detection: split chained commands conservatively on &&, ||, ;, |
# and newlines, then match a word-boundary `git ... commit` where anything
# between the two words must be option tokens, each optionally trailing one
# non-dash argument (so `git -C repo commit` and `git -c k=v commit` count but
# `git log --grep commit` does not). Any segment containing a match counts;
# false positives just run the gates, and the escape hatch covers the rest.
_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit\b")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")

# The same match with a tighter boundary: `\b` sits between `commit` and a following
# `-`, so `git commit-tree` and `git commit-graph write` both read as commits to the
# regex above (backlog item 10). Running the read-only gate suite for them is harmless
# over-matching; DENYING a commit for them would not be, so the binder-validity step
# — the only part of this hook that refuses on the commit's content — triggers on this
# one instead. `--amend` is inside it: an amend records bytes like any other commit.
_TRUE_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit(?![-\w])")


def is_commit_command(command: str) -> bool:
    return any(_COMMIT_RE.search(seg) for seg in _SPLIT_RE.split(command))


def is_true_commit(command: str) -> bool:
    """True for a real `git commit` subcommand — the gate suite's detection minus the
    `commit-tree` / `commit-graph` false fires it deliberately tolerates."""
    return any(_TRUE_COMMIT_RE.search(seg) for seg in _SPLIT_RE.split(command))


def gate_specs(root: Path) -> list[tuple[str, list[str], int]]:
    """The repo gates as (name, argv, timeout), in the order the spec lists them, with
    every script path rooted at `root` — the invocation root, so a commit issued in a
    linked worktree is judged by that worktree's own gates. The pack gate is dropped (not
    failed) when skills/_shared/sme/ has nothing to validate — validate_packs errors on an
    empty file list, and an absent pack dir is a repo-shape question for the other gates,
    not this one."""
    py = sys.executable or "python3"
    gates = [
        ("check_shared_copies", [py, str(root / "scripts/check_shared_copies.py")],
         GATE_TIMEOUT),
        # Beside check_shared_copies on purpose: that one holds shared prose byte-equal
        # across locations, this one holds the invariant register's carriers in place at
        # phrase grain. Same job, one grain apart.
        (REGISTER_GATE, [py, str(root / "scripts/check_invariant_register.py"),
                         "--root", str(root)], REGISTER_GATE_TIMEOUT),
        ("sync_codex_skills --check", [py, str(root / "scripts/sync_codex_skills.py"), "--check"],
         GATE_TIMEOUT),
        ("sync_codex_agents --check", [py, str(root / "scripts/sync_codex_agents.py"), "--check"],
         GATE_TIMEOUT),
        ("validate_plugin", [py, str(root / "scripts/validate_plugin.py")], GATE_TIMEOUT),
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
                       *map(str, packs)], GATE_TIMEOUT))
    return gates


def _tail(text: str, limit: int = TAIL_LINES) -> str:
    lines = text.strip().splitlines()
    if len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join([f"... ({len(lines) - limit} earlier lines omitted)"] + lines[-limit:])


def _subprocess_runner(name: str, argv: list[str], timeout: int, cwd) -> tuple[int, str]:
    """Run one gate from `cwd` under its own timeout; stdout+stderr interleaved. A run
    that did not finish — timed out, or never started — reports GATE_INCOMPLETE rather
    than a made-up exit status, because a gate that did not complete has no verdict and
    DENY_CODES has to be able to tell the difference."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd or ROOT), text=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return GATE_INCOMPLETE, f"{out}\n[gate timed out after {timeout}s]"
    except OSError as e:
        return GATE_INCOMPLETE, f"[gate could not be started: {e}]"


def run_gates(gates, runner, cwd=None) -> tuple[tuple[str, int, str] | None, list[str]]:
    """(first failing (name, exit_code, output) or None, warnings) — every gate run from
    `cwd` under its own timeout.

    RECORDED DECISION (2026-09-02, registers INV-5 and INV-21). Rooting the suite at the
    invocation root means the resolved tree can be one that does not carry these scripts
    at all — a worktree on a branch older than a gate, most plainly. A gate whose script
    is not there never ran, so it returned no verdict, and this suite fails OPEN for it
    with a warning rather than blocking every commit in such a tree. That is a deliberate
    narrowing of what blocks, chosen over the alternative and recorded here rather than
    left to be inferred."""
    warnings: list[str] = []
    for name, argv, timeout in gates:
        script = argv[1] if len(argv) > 1 else ""
        if script.endswith(".py") and not Path(script).is_file():
            warnings.append(f"precommit_gate: the '{name}' gate did not run and is allowing "
                            f"this commit: {script} is not in the tree this commit runs in")
            continue
        code, output = runner(name, argv, timeout, cwd)
        if code == 0:
            continue
        deny = DENY_CODES.get(name)
        if deny is not None and code not in deny:
            warnings.append(f"precommit_gate: the '{name}' gate did not produce a verdict "
                            f"({_gate_status(code)}) and is allowing this commit:\n"
                            f"{_tail(output)}")
            continue
        return (name, code, output), warnings
    return None, warnings


def _gate_status(code: int) -> str:
    return "did not run to completion" if code == GATE_INCOMPLETE else f"exit {code}"


# --- binder-validity step ---------------------------------------------------------
#
# WHAT IT ENFORCES. A commit that records a live binder must record a VALID one. The
# review gate already binds a binder commit to a fresh review of the exact staged bytes
# (roundtable_gate.py); nothing checked that those bytes are a well-formed plan, so a
# hand-edited invalid binder could be committed and only fail later, in whichever skill
# read it next. Register INV-7.
#
# WHAT BYTES. Per commit shape, exactly what git records for that shape and no more:
#   plain commit          index blobs of the staged live binders (`git show :<path>`)
#   pathspec / --only     working-tree bytes of the live binders the pathspecs cover;
#                         a staged-but-uncovered binder is NOT a candidate, because
#                         this commit does not record it
#   -a / --all            staged blobs, plus tracked-modified live binders from the
#                         working tree, with working-tree bytes winning on overlap
#   chained `git add`     additionally the live binders the add's arguments cover,
#                         working-tree bytes, UNTRACKED files included — except under
#                         -u/--update, which stages no untracked file
#   --include / -i        the index PLUS the named paths: the union of the first and
#                         second rows, working-tree bytes winning
#   --dry-run             records nothing, so the step does not run at all
# Pathspecs and add arguments are resolved against the PAYLOAD CWD — joined and
# normalized to repo-relative first — so a spec typed in a subdirectory matches the
# binder it actually names. A deletion is the sole exemption in every enumeration:
# a D status, or a covered path absent from the working tree, records no bytes to read.
# Statuses are classified by first letter (R100 -> R) and the DESTINATION of a rename
# or copy is what gets validated.
#
# CRASH IS NOT A VERDICT. The step denies only when the validator exits nonzero AND its
# output carries BINDER_FINDINGS_SENTINEL. A traceback, a missing validator, a spawn
# failure, an unreadable blob or an exhausted budget fail OPEN with a warning — the same
# asymmetry the rest of this hook keeps: errors open, denials named.
#
# NAMED RESIDUAL BOUNDS (stated here and in INV-7's register entry, so the claim never
# outruns the code): a `git -C <path>` relocation is skipped rather than rescoped
# (backlog item 22); a commit that does not head its own shell segment is not seen, per
# the anchoring rule in _git_verb below; a compound command that mutates the tree
# between this read and the commit is not seen; non-deterministic pathspecs (globs,
# magic prefixes, --pathspec-from-file) and `-f/--force` adds of ignored paths are not
# covered;
# cross-binder checks are skipped by --no-cross-binder, because a materialized binder's
# neighbours are not its set; and a commit that arrives by merge, rebase or cherry-pick
# fires no commit subcommand at all — the same accepted-bypass family AGENTS.md names
# for the review gates. KARTA_SKIP_GATE=1 bypasses this step exactly as it bypasses the
# gate suite. A path with no recorded change contributes nothing: its bytes are already
# what HEAD holds, so validating it would judge a commit for something it did not touch.

_GIT_GLOBAL_VALUE = {"-C", "-c", "--exec-path", "--git-dir", "--work-tree",
                     "--namespace", "--super-prefix", "--config-env"}
_GIT_GLOBAL_FLAG = {"-p", "--paginate", "-P", "--no-pager", "--bare", "--no-replace-objects",
                    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                    "--icase-pathspecs", "--no-optional-locks", "--html-path",
                    "--man-path", "--info-path", "--list-cmds"}
_GIT_RELOCATING = {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
_OPERATORS = {";", "&&", "||", "|", "&"}
# commit options that consume the next token as their value
_COMMIT_VALUE_LONG = {"--message", "--reuse-message", "--reedit-message", "--file",
                      "--author", "--date", "--template", "--fixup", "--squash",
                      "--cleanup", "--trailer"}
_COMMIT_VALUE_SHORT = set("mCcFt")


class _Shape:
    """What one `git commit` invocation records, as far as this step needs to know."""
    def __init__(self):
        self.all = self.include = self.dry_run = self.opaque = False
        self.pathspecs: list[str] = []


class _Add:
    """What one chained `git add` invocation stages. `specs` are repo-relative; the
    empty string means the whole repository (`-A` with no pathspec)."""
    def __init__(self):
        self.specs: list[str] = []
        self.untracked = True   # -u/--update stages no untracked file
        self.inert = False      # --dry-run, interactive, or otherwise unknowable


def _clean(path: str) -> str:
    """A git-reported path as a normalized repo-relative posix path."""
    p = posixpath.normpath(path.strip().replace("\\", "/"))
    return "" if p == "." else p.lstrip("/")


def _considered(path: str) -> bool:
    """A path this step looks at at all: under .karta/binders/, any depth, ending .json."""
    return path.startswith(BINDER_DIR) and path.endswith(BINDER_SUFFIX)


def _token_segments(command: str) -> list[list[str]] | None:
    """The command as shell-word segments — one per line, split again on operators — or
    None when the quoting cannot be parsed. Splitting AFTER tokenizing is what lets a
    commit message contain `;` or `&&` without a segment boundary landing inside it;
    tokenizing line by line is what keeps a newline a boundary, since shlex would
    otherwise read one as ordinary whitespace and merge two commands into one. A message
    that spans lines fails the per-line parse, and the whole command is tokenized instead."""
    try:
        lines = [shlex.split(ln) for ln in command.split("\n")]
    except ValueError:
        try:
            lines = [shlex.split(command)]
        except ValueError:
            return None
    segs: list[list[str]] = []
    for toks in lines:
        cur: list[str] = []
        for t in toks:
            if t in _OPERATORS:
                segs.append(cur)
                cur = []
            else:
                cur.append(t)
        segs.append(cur)
    return [s for s in segs if s]


def _git_verb(tokens: list[str], verb: str) -> tuple[bool, list[str]] | None:
    """(relocated, words after `git <globals> <verb>`) when this segment IS that git
    invocation, else None.

    ANCHORED, NOT SEARCHED: git must head its own segment, after any `VAR=value` prefix,
    the same rule the landing gate in roundtable_gate.py uses and for the same reason —
    this match can end in a denial, so a `git commit` that is merely quoted, echoed or
    grepped must never reach it. The cost is under-coverage, which is the safe direction:
    a commit behind `sudo`, `time` or `xargs` is simply not seen. Globals are a closed
    set too, so `git log --grep commit` is not a commit."""
    i = 0
    while i < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i]):
        i += 1
    if i >= len(tokens) or PurePosixPath(tokens[i]).name != "git":
        return None
    j, relocated = i + 1, False
    while j < len(tokens) and tokens[j].startswith("-"):
        name = tokens[j].split("=", 1)[0]
        if name in _GIT_RELOCATING:
            relocated = True
        if name in _GIT_GLOBAL_VALUE:
            j += 1 if "=" in tokens[j] else 2
        elif name in _GIT_GLOBAL_FLAG:
            j += 1
        else:
            break
    if j < len(tokens) and tokens[j] == verb:
        return relocated, tokens[j + 1:]
    return None


def _resolve_spec(arg: str, prefix: str, root: Path) -> str | None:
    """One pathspec or add argument as a repo-relative path, or None when it is not a
    deterministic containment (magic prefix, glob) or points outside the repository."""
    if not arg or arg.startswith(":") or any(c in arg for c in "*?["):
        return None
    if posixpath.isabs(arg):
        try:
            rel = os.path.relpath(os.path.realpath(arg), os.path.realpath(str(root)))
        except (OSError, ValueError):
            return None
        arg, prefix = rel.replace("\\", "/"), ""
    joined = posixpath.normpath(posixpath.join(prefix, arg))
    if joined == ".":
        return ""
    return None if joined == ".." or joined.startswith("../") else joined


def _parse_commit_shape(words: list[str], prefix: str, root: Path) -> _Shape:
    shape = _Shape()
    i = 0
    while i < len(words):
        tok = words[i]
        if tok == "--":
            for arg in words[i + 1:]:
                spec = _resolve_spec(arg, prefix, root)
                if spec is None:
                    shape.opaque = True
                else:
                    shape.pathspecs.append(spec)
            return shape
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name == "--all":
                shape.all = True
            elif name == "--include":
                shape.include = True
            elif name == "--dry-run":
                shape.dry_run = True
            elif name in ("--pathspec-from-file", "--patch", "--interactive"):
                shape.opaque = True  # what it records is not decidable from the command
            elif name == "--only":
                pass  # the pathspec shape already: only the named paths are recorded
            if name in _COMMIT_VALUE_LONG and "=" not in tok:
                i += 2
                continue
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            letters = tok[1:]
            if "a" in letters:
                shape.all = True
            if "i" in letters:
                shape.include = True
            if "p" in letters:
                shape.opaque = True
            i += 2 if letters[-1] in _COMMIT_VALUE_SHORT else 1
            continue
        spec = _resolve_spec(tok, prefix, root)
        if spec is None:
            # A glob or magic pathspec on the COMMIT decides the shape, so it cannot be
            # quietly dropped: with it gone the invocation would read as a plain commit
            # and validate the whole index, which this commit does not record. Skipping
            # the step is the only safe reading. (An add's globs are different — see
            # _parse_add: there a dropped spec can only narrow, never re-shape.)
            shape.opaque = True
        else:
            shape.pathspecs.append(spec)
        i += 1
    return shape


def _parse_add(words: list[str], prefix: str, root: Path) -> _Add:
    add = _Add()
    broad = False
    i = 0
    while i < len(words):
        tok = words[i]
        if tok == "--":
            for arg in words[i + 1:]:
                spec = _resolve_spec(arg, prefix, root)
                if spec is None:
                    add.inert = True
                else:
                    add.specs.append(spec)
            break
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in ("--all", "--no-ignore-removal"):
                broad = True
            elif name == "--update":
                add.untracked = False
                broad = True
            elif name in ("--dry-run", "--patch", "--interactive", "--edit",
                          "--pathspec-from-file"):
                add.inert = True
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            letters = tok[1:]
            if "A" in letters:
                broad = True
            if "u" in letters:
                add.untracked = False
                broad = True
            if any(c in letters for c in "npie"):
                add.inert = True
            i += 1
            continue
        spec = _resolve_spec(tok, prefix, root)
        if spec is None:
            # Not expanded: what this glob would ADDITIONALLY stage is a named residual.
            # The deterministic specs beside it still count — dropping the whole add
            # would lose coverage this command certainly has.
            pass
        else:
            add.specs.append(spec)
        i += 1
    if broad and not add.specs:
        add.specs.append("")  # -A / -u with no pathspec: the whole repository
    return add


def commit_plan(command: str, prefix: str, root: Path) -> tuple[_Shape, list[_Add]] | None:
    """The commit shape and the `git add` invocations chained ahead of it in the same
    command, or None when the step must not run (no true commit, unparseable quoting,
    a relocating `git -C`, a dry run, or an opaque shape)."""
    segs = _token_segments(command)
    if segs is None:
        return None
    idx = next((i for i, s in enumerate(segs) if _git_verb(s, "commit") is not None), None)
    if idx is None:
        return None
    relocated, words = _git_verb(segs[idx], "commit")  # type: ignore[misc]
    if relocated:
        return None  # named residual: `git -C` is denied elsewhere, never rescoped here
    shape = _parse_commit_shape(words, prefix, root)
    if shape.dry_run or shape.opaque:
        return None
    adds = []
    for seg in segs[:idx]:
        found = _git_verb(seg, "add")
        if found is None or found[0]:
            continue
        add = _parse_add(found[1], prefix, root)
        if not add.inert and add.specs:
            adds.append(add)
    return shape, adds


def _name_status(result: tuple[int, str]) -> list[tuple[str, str]]:
    """(status-letter, path) pairs from `--name-status -z`. A rename or copy carries
    two paths and the DESTINATION is the one this commit records; score suffixes
    (R100) are dropped, so classification is by first letter alone."""
    code, out = result
    if code != 0 or not out:
        return []
    fields = out.split("\0")
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(fields):
        head = fields[i].strip()
        if not head:
            i += 1
            continue
        status = head[0]
        if status in ("R", "C"):
            if i + 2 < len(fields):
                pairs.append((status, _clean(fields[i + 2])))
            i += 3
        else:
            if i + 1 < len(fields):
                pairs.append((status, _clean(fields[i + 1])))
            i += 2
    return pairs


def _nul_paths(result: tuple[int, str]) -> list[str]:
    code, out = result
    if code != 0 or not out:
        return []
    return [_clean(p) for p in out.split("\0") if p.strip()]


def _covers(specs: list[str], path: str) -> bool:
    """Exact path or directory prefix; the empty spec is the whole repository."""
    return any(s == "" or path == s or path.startswith(s + "/") for s in specs)


def binder_candidates(command: str, cwd, root: Path, git) -> tuple[list[tuple[str, str]],
                                                                  list[tuple[str, str]]]:
    """(candidates, exempted) for this command — an INSPECTABLE pair, so a test can
    assert an archived or deleted binder was CONSIDERED and then excused, rather than
    merely not denied (which any bug that skips the path would also produce).

    candidates: (repo-relative path, 'index' | 'worktree') — the bytes git records.
    exempted:   (repo-relative path, why it records no bytes worth validating)."""
    prefix = _cwd_prefix(root, cwd)
    plan = commit_plan(command, prefix, root)
    if plan is None:
        return [], []
    shape, adds = plan

    staged = _name_status(git(["diff", "--cached", "--name-status", "-z"]))
    need_worktree = shape.all or shape.pathspecs or adds
    worktree = _name_status(git(["diff", "--name-status", "-z"])) if need_worktree else []
    want_untracked = any(a.untracked for a in adds)
    untracked = (_nul_paths(git(["ls-files", "--others", "--exclude-standard", "-z",
                                 "--", BINDER_DIR.rstrip("/")]))
                 if want_untracked else [])

    staged_map = {p: s for s, p in staged}
    wt_map = {p: s for s, p in worktree}
    universe = set(staged_map) | set(wt_map) | set(untracked)

    def wt_entries(paths, untracked_ok: bool = True) -> list[tuple[str, str]]:
        """(status, path) as the WORKING TREE has them: a tracked modification's own
        status, '?' for an untracked file, else 'M' for a path that is simply present."""
        out = []
        for p in sorted(paths):
            if p in wt_map:
                out.append((wt_map[p], p))
            elif p in untracked:
                if untracked_ok:
                    out.append(("?", p))
            else:
                out.append(("M", p))
        return out

    entries: list[tuple[str, str, str]] = []
    if shape.pathspecs and not shape.include:
        # git records the NAMED paths' working-tree state and leaves the rest of the
        # index where it is, so a staged binder the pathspecs miss is not in this commit.
        covered = {p for p in universe if _covers(shape.pathspecs, p)}
        entries += [(s, p, "worktree") for s, p in wt_entries(covered)]
    else:
        entries += [(s, p, "index") for s, p in staged]
        if shape.all:
            entries += [(s, p, "worktree") for s, p in worktree]
        for add in adds:
            covered = {p for p in universe if _covers(add.specs, p)}
            entries += [(s, p, "worktree")
                        for s, p in wt_entries(covered, untracked_ok=add.untracked)]
        if shape.include:
            covered = {p for p in universe if _covers(shape.pathspecs, p)}
            entries += [(s, p, "worktree") for s, p in wt_entries(covered)]

    exempt: dict[str, str] = {}
    index_src: set[str] = set()
    worktree_src: set[str] = set()
    for status, path, source in entries:
        if not _considered(path):
            continue
        if status == "D":
            exempt.setdefault(path, "deleted by this commit — a deletion records no bytes")
            continue
        if path.startswith(BINDER_ARCHIVE):
            exempt.setdefault(path, "an archived (delivered) binder, not a live plan")
            continue
        (worktree_src if source == "worktree" else index_src).add(path)

    # A worktree-source path that is gone from the tree is the same deletion, seen from
    # the filesystem instead of a status letter — an exemption, never a missing-file error.
    for path in sorted(worktree_src):
        p = root / path
        if not (p.is_symlink() or p.exists()):
            exempt.setdefault(path, "absent from the working tree — this commit records "
                                    "its deletion")
            worktree_src.discard(path)
    for path in sorted(exempt):
        index_src.discard(path)
        worktree_src.discard(path)

    candidates = ([(p, "worktree") for p in sorted(worktree_src)]
                  + [(p, "index") for p in sorted(index_src - worktree_src)])
    return sorted(candidates), sorted(exempt.items())


def _cwd_prefix(root: Path, cwd) -> str:
    """The payload cwd as a repo-relative prefix ('' at the root, and '' as the
    conservative answer when it names nothing inside this tree — root-relative is what
    every specless shape already assumes)."""
    if not isinstance(cwd, str) or not cwd:
        return ""
    try:
        rel = os.path.relpath(os.path.realpath(cwd), os.path.realpath(str(root)))
    except (OSError, ValueError):
        return ""
    rel = rel.replace("\\", "/")
    return "" if rel == "." or rel.startswith("..") else rel


def _read_worktree(root: Path, rel: str) -> bytes | None:
    p = root / rel
    try:
        if p.is_symlink():
            # git records the LINK TEXT as the blob for a symlinked path, never the
            # target's contents — so the link text is what gets validated.
            return os.readlink(p).encode("utf-8", "surrogateescape")
        return p.read_bytes()
    except OSError:
        return None


def _real_validate(root: Path, binder: Path, timeout: float) -> tuple[int | None, str]:
    """(exit_code, output), or (None, why) when the validator could not be run to
    completion — which is never a finding."""
    py = sys.executable or "python3"
    argv = [py, VALIDATE_BINDER_REL, "--binder", str(binder), "--no-cross-binder"]
    try:
        proc = subprocess.run(argv, cwd=str(root), text=True, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired:
        return None, f"the validator did not finish inside the remaining {timeout:.0f}s budget"
    except OSError as e:
        return None, f"the validator could not be started ({e})"


def _binder_deny(path: str, tail: str) -> str:
    return (f"Commit blocked by the karta binder-validity step: the binder this commit would "
            f"record at {path} does not validate. A commit is where a plan becomes the plan of "
            f"record, so an invalid binder is refused here rather than found later by whichever "
            f"skill reads it next. Fix the findings below and commit again — or, for an "
            f"intentional bypass, prefix the command with {SKIP_VAR}=1 (documented escape hatch)."
            f"\n\n--- validate_binder.py on {path} (last {TAIL_LINES} lines) ---\n{tail}")


def _binder_warn(detail: str) -> str:
    return (f"precommit_gate: the binder-validity step did not run to completion and is "
            f"allowing this commit: {detail}")


def binder_block(command: str, cwd, root: Path, git, validate=None,
                 clock=None) -> tuple[str | None, str | None]:
    """(deny_reason, warning). A deny reason only for a real validator finding on bytes
    that were successfully materialized; everything else that goes wrong is a warning
    beside an allowed commit."""
    validate = validate or _real_validate
    clock = clock or time.monotonic
    start = clock()
    candidates, _ = binder_candidates(command, cwd, root, git)
    if not candidates:
        return None, None  # no binder in this commit: the step is skipped, not passed

    warnings: list[str] = []
    for n, (path, source) in enumerate(candidates):
        remaining = BINDER_BUDGET - (clock() - start)
        if remaining <= 0:
            left = [p for p, _ in candidates[n:]]
            return None, _binder_warn(
                f"its {BINDER_BUDGET:.0f}s end-to-end budget was exhausted with "
                f"{len(left)} binder(s) unvalidated: {', '.join(left)}")
        if source == "worktree":
            data = _read_worktree(root, path)
        else:
            code, out = git(["show", f":{path}"])
            data = out.encode("utf-8", "surrogateescape") if code == 0 else None
        if data is None:
            warnings.append(f"the {source} bytes of {path} could not be read")
            continue
        with tempfile.TemporaryDirectory(prefix="karta-binder-") as td:
            # One fresh directory per candidate, so two binders with the same basename
            # in different directories cannot collide; `where` maps what the validator
            # can see (the temp path) back to the path in the user's tree.
            materialized = Path(td) / PurePosixPath(path).name
            try:
                materialized.write_bytes(data)
            except OSError as e:
                warnings.append(f"{path} could not be materialized for validation ({e})")
                continue
            where = {str(materialized): path}
            code, out = validate(root, materialized, remaining)
        if code is None:
            warnings.append(f"{path} was not validated: {out}")
            continue
        if code != 0 and BINDER_FINDINGS_SENTINEL not in out:
            # Nonzero without the findings sentinel is a crash, not a verdict.
            warnings.append(f"the validator exited {code} on {path} without a findings "
                            f"report, so its output is not a verdict")
            continue
        if code != 0:
            for tmp_path, repo_path in where.items():
                out = out.replace(tmp_path, repo_path)
            return _binder_deny(path, _tail(out)), None
    return None, (_binder_warn("; ".join(warnings)) if warnings else None)


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


def decide(payload, env, runner, gates=None, git=None, root=None,
           validate=None, resolve_root=None, clock=None) -> tuple[int, str]:
    """(exit_code, stderr_message) for one hook invocation. Pure over its inputs
    so --self-test can drive it with fabricated payloads, a stubbed runner, a stubbed
    git, a stubbed validator, a stubbed clock and a fabricated repo root."""
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
    injected_git = git is not None
    if git is None:
        git = lambda args: _real_git(root, args)
    # Where this invocation's tree is. Resolved ONCE, at the top, and used by everything
    # below it that reads repository state: the gate suite's script paths and working
    # directory, and the binder step's index, working tree and file bytes. Every one of
    # those has to come from where the commit happens (INV-11), which is the tree the
    # payload's cwd names, not the one this script file sits in. The release block keeps
    # the root it was given: rescoping that one is the same fix for a different gate and
    # belongs to its own change.
    top = (resolve_root or resolve_invocation_root)(payload.get("cwd"))
    gate_root = root if top is None else Path(top)
    warnings: list[str] = []
    # The repo gates run first, from that root.
    failure, gate_warnings = run_gates(gate_specs(gate_root) if gates is None else gates,
                                       runner, gate_root)
    warnings += gate_warnings
    if failure is not None:
        name, code, output = failure
        reason = (
            f"Commit blocked by the karta repo gate suite: gate '{name}' failed "
            f"({_gate_status(code)}). A `git commit` was detected, so the pre-commit gates ran "
            f"from {gate_root}; this one did not pass. Fix the failure shown below and commit "
            f"again — or, for an intentional partial commit, prefix the command with "
            f"{SKIP_VAR}=1 (documented escape hatch).\n\n"
            f"--- {name} output (last {TAIL_LINES} lines) ---\n{_tail(output)}"
        )
        return 2, reason
    # Then the binder-validity step, on the same resolved tree.
    if is_true_commit(command):
        bgit = (git if injected_git or str(gate_root) == str(root)
                else (lambda args: _real_git(gate_root, args)))
        deny, warn = binder_block(command, payload.get("cwd"), gate_root, bgit,
                                  validate=validate, clock=clock)
        if deny is not None:
            return 2, deny
        if warn:
            warnings.append(warn)
    # Then the release block: a version bump needs its green staged gate file.
    block = _release_block(command, git, root)
    return (2, block) if block is not None else (0, "\n".join(warnings))


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

    green = lambda name, argv, *_: (0, f"{name}: OK")
    # a git stub reporting no version change, so gate-suite cases stay hermetic
    no_bump = lambda args: (0, json.dumps({"version": "2.21.0"}))
    calls: list[str] = []

    def failing(name, argv, *_):
        calls.append(name)
        if name == "sync_codex_skills --check":
            return 1, "\n".join(f"L{i:03d} drift detail" for i in range(1, 101))
        return 0, "OK"

    def must_not_run(name, argv, *_):
        raise AssertionError("gate runner invoked for a non-commit command")

    stub_gates = [("check_shared_copies", [], GATE_TIMEOUT),
                  (REGISTER_GATE, [], REGISTER_GATE_TIMEOUT),
                  ("sync_codex_skills --check", [], GATE_TIMEOUT),
                  ("sync_codex_agents --check", [], GATE_TIMEOUT),
                  ("validate_plugin", [], GATE_TIMEOUT),
                  ("validate_packs (packs)", [], GATE_TIMEOUT)]

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
          calls == ["check_shared_copies", REGISTER_GATE, "sync_codex_skills --check"],
          f"calls={calls}")

    # fail-open paths
    code, _ = hook_main("this is not json", {}, must_not_run)
    check("malformed payload fails open", code == 0)
    code, _ = hook_main("[1, 2, 3]", {}, must_not_run)
    check("non-object payload fails open", code == 0)

    def exploding(name, argv, *_):
        raise RuntimeError("boom")
    code, _ = hook_main(json.dumps(_payload("git commit -m x")), {}, exploding)
    check("runner exception fails open", code == 0)

    # real gate list has the expected shape (no gates executed)
    specs = gate_specs(ROOT)
    names = [n for n, _, _ in specs]
    check("gate_specs lists the spec's gates in order",
          names[:5] == ["check_shared_copies", REGISTER_GATE, "sync_codex_skills --check",
                        "sync_codex_agents --check", "validate_plugin"], f"names={names}")
    pack_argv = next((argv for n, argv, _ in specs if n.startswith("validate_packs")), [])
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

    # --- binder-validity step ----------------------------------------------------
    # Fabricated repos again: real files on disk for the working-tree half, a stubbed
    # git for the index half, and the REAL validator for every case whose verdict is
    # the point — so these cases exercise the whole chain (enumerate -> read ->
    # materialize -> validate -> report), not a mock of it.
    BAD = {"slug": "demo", "title": "T", "summary": "S", "scope": {"included": ["x"]},
           "work_items": [{"id": "a", "title": "A", "summary": "s",
                           "oracle": {"type": "unit"}}]}          # no `motivation`
    GOOD = dict(BAD, motivation="why this binder exists")
    BAD_J, GOOD_J = json.dumps(BAD), json.dumps(GOOD)
    B1, B2 = ".karta/binders/one.json", ".karta/binders/two.json"

    def zjoin(entries):
        fields = [f for e in entries for f in e]
        return "\0".join(fields) + ("\0" if fields else "")

    def mk_binder_repo(*, staged=(), worktree=(), untracked=(), blobs=None, files=None,
                       blob_fail=()):
        root = Path(tempfile.mkdtemp(prefix="pcg-binder-"))
        tmp_roots.append(str(root))
        for rel, body in (files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body if isinstance(body, str) else json.dumps(body))

        def git(args):
            if args[:3] == ["diff", "--cached", "--name-status"]:
                return (0, zjoin(staged))
            if args[:2] == ["diff", "--name-status"]:
                return (0, zjoin(worktree))
            if args[:2] == ["ls-files", "--others"]:
                return (0, zjoin([(p,) for p in untracked]))
            if args[:1] == ["show"] and args[1].startswith(":"):
                path = args[1][1:]
                if path in blob_fail:
                    return (1, "")
                body = (blobs or {}).get(path)
                return (0, body) if body is not None else (1, "")
            if args[:1] == ["show"]:                      # HEAD:plugin.json, release block
                return (0, json.dumps({"version": "2.21.0"}))
            if args[:2] == ["rev-parse", "HEAD"]:
                return (0, "p" * 40)
            return (1, "")
        return root, git

    # the real validator, invoked exactly as the step invokes it, from THIS checkout
    def real_validate(_root, binder, timeout):
        return _real_validate(ROOT, binder, timeout)

    def crash_validate(_root, binder, timeout):
        return 1, "Traceback (most recent call last):\n  File \"x\", line 1\nRuntimeError: boom"

    def never_validate(_root, binder, timeout):
        raise AssertionError("the binder-validity step ran when it must not have")

    def run(command, *, root, git, cwd="/tmp", validate=real_validate, **kw):
        """One decide() with the gate suite green and the release block disarmed, so the
        only thing that can move the exit code is the binder-validity step."""
        return decide({**_payload(command), "cwd": cwd}, {}, green, stub_gates,
                      git=git, root=root, validate=validate, **kw)

    # (1) a plain commit records the INDEX blob of every staged live binder
    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J})
    code, reason = run('git commit -m "plan"', root=root, git=git)
    check("binder-invalid-staged-denied: a plain commit staging a binder whose index blob "
          "omits the schema-required `motivation` is refused, naming the repo-relative path "
          "and the validator's own finding",
          code == 2 and B1 in reason and "missing required property 'motivation'" in reason,
          f"code={code} reason={reason[-200:]}")
    check("the denial reports the repo path, never the temp basename it validated",
          code == 2 and "/karta-binder-" not in reason and "binder-validity step" in reason)

    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: GOOD_J})
    code, msg = run('git commit -m "plan"', root=root, git=git)
    check("binder-valid-staged-passes: the same shape with a valid staged binder allows the "
          "commit and warns about nothing", code == 0 and msg == "", f"code={code} msg={msg}")

    # (2) archive and deletion: CONSIDERED, then excused — asserted through the pair, so a
    # bug that never looked at the path could not pass these by producing no denial
    root, git = mk_binder_repo(staged=[("M", ".karta/binders/archive/old.json"), ("M", B1)],
                               blobs={".karta/binders/archive/old.json": BAD_J, B1: GOOD_J})
    cands, exempted = binder_candidates('git commit -m "x"', "/tmp", root, git)
    code, _ = run('git commit -m "x"', root=root, git=git)
    check("binder-archive-exempt: an invalid binder under .karta/binders/archive/ is "
          "considered and then exempted as delivered history, while the live binder beside "
          "it stays a candidate",
          cands == [(B1, "index")] and len(exempted) == 1
          and exempted[0][0] == ".karta/binders/archive/old.json"
          and "archived" in exempted[0][1] and code == 0,
          f"cands={cands} exempted={exempted} code={code}")

    root, git = mk_binder_repo(staged=[("D", B2), ("M", B1)], blobs={B1: GOOD_J})
    cands, exempted = binder_candidates('git commit -m "x"', "/tmp", root, git)
    check("binder-delete-exempt: a staged deletion is considered and exempted — a deleted "
          "binder records no bytes to read — and is never a missing-blob error",
          cands == [(B1, "index")] and exempted and exempted[0][0] == B2
          and "deletion" in exempted[0][1], f"cands={cands} exempted={exempted}")

    # (3) -a records the WORKING TREE for tracked modifications; those bytes win
    root, git = mk_binder_repo(staged=[("M", B1)], worktree=[("M", B1)],
                               blobs={B1: GOOD_J}, files={B1: BAD_J})
    code, reason = run('git commit -am "plan"', root=root, git=git)
    check("binder-staging-shape-worktree-bytes: under -a the working-tree bytes win over a "
          "valid staged blob, so the invalid file on disk — which is what -a stages — denies",
          code == 2 and B1 in reason, f"code={code}")

    root, git = mk_binder_repo(staged=[("M", B1)], worktree=[("D", B1)], blobs={B1: BAD_J})
    cands, exempted = binder_candidates('git commit -am "x"', "/tmp", root, git)
    code, _ = run('git commit -am "x"', root=root, git=git)
    check("binder-staging-shape-delete-exempt: under -a a worktree deletion exempts the path "
          "even though its staged blob is invalid — the commit records the deletion, not bytes",
          cands == [] and exempted and exempted[0][0] == B1 and code == 0,
          f"cands={cands} exempted={exempted} code={code}")

    # (4) a pathspec commit records the NAMED paths' working-tree state, and nothing else
    root, git = mk_binder_repo(staged=[("M", B1)], worktree=[("M", B1)],
                               blobs={B1: GOOD_J}, files={B1: BAD_J})
    code, reason = run(f'git commit {B1} -m "plan"', root=root, git=git)
    check("binder-pathspec-worktree-bytes: a pathspec commit validates the working-tree bytes "
          "of the path it names, not the staged blob git leaves behind",
          code == 2 and B1 in reason, f"code={code}")

    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J},
                               files={"docs/note.md": "hi"})
    cands, _ = binder_candidates('git commit docs/note.md -m "x"', "/tmp", root, git)
    code, _ = run('git commit docs/note.md -m "x"', root=root, git=git)
    check("binder-pathspec-scope-restricted: an invalid binder that is staged but NOT covered "
          "by the pathspec is no candidate and denies nothing — this commit does not record it",
          cands == [] and code == 0, f"cands={cands} code={code}")

    # (5) a chained `git add` stages what the commit then records
    root, git = mk_binder_repo(worktree=[("M", B1)], files={B1: BAD_J})
    code, reason = run(f'git add {B1} && git commit -m "plan"', root=root, git=git)
    check("binder-chained-add-worktree-bytes: `git add <binder> && git commit` validates the "
          "working-tree bytes the add stages, with nothing in the index beforehand",
          code == 2 and B1 in reason, f"code={code}")

    # A second, ordinary add rides along so the untracked list is really enumerated —
    # otherwise "-u stages no untracked file" would be proven by the enumeration never
    # having run, which is a different fact and would survive the rule being wrong.
    root, git = mk_binder_repo(worktree=[("M", B1)], untracked=[B2],
                               files={B1: BAD_J, B2: BAD_J, "docs/note.md": "hi"})
    chained = 'git add -u && git add docs/note.md && git commit -m "x"'
    cands, _ = binder_candidates(chained, "/tmp", root, git)
    code, reason = run(chained, root=root, git=git)
    check("binder-chained-add-update-denied: `git add -u` covers tracked-modified binders "
          "exactly as -a does and denies on one, while the untracked binder the same command "
          "enumerates stays out — -u stages no untracked file",
          code == 2 and B1 in reason and B2 not in reason and cands == [(B1, "worktree")],
          f"code={code} cands={cands}")

    for broad in ("git add -A", "git add ."):
        root, git = mk_binder_repo(untracked=[B2], files={B2: BAD_J})
        code, reason = run(f'{broad} && git commit -m "plan"', root=root, git=git)
        check(f"binder-untracked-broad-add-denied: an invalid UNTRACKED binder that `{broad}` "
              f"would stage is enumerated and denied — a brand-new binder is the common case, "
              f"and it appears in no diff", code == 2 and B2 in reason, f"code={code}")

    root, git = mk_binder_repo(untracked=[B2], files={B2: BAD_J})
    cands, _ = binder_candidates('git commit -m "x"', "/tmp", root, git)
    code, _ = run('git commit -m "x"', root=root, git=git, validate=never_validate)
    check("binder-untracked-not-staged-passes: the same invalid untracked binder with no add "
          "covering it is not in the commit, so the step never even runs the validator",
          cands == [] and code == 0, f"cands={cands} code={code}")

    # (6) a rename or copy records its DESTINATION
    root, git = mk_binder_repo(staged=[("C100", B1, B2)], blobs={B1: GOOD_J, B2: BAD_J})
    code, reason = run('git commit -m "copy the plan"', root=root, git=git)
    check("binder-copy-destination-validated: a C-status entry validates the destination path — "
          "the bytes this commit adds — with the score suffix stripped from the status",
          code == 2 and B2 in reason, f"code={code} reason={reason[-160:]}")

    # (7) malformed JSON is a verdict, not a crash — the validator says so and the step relays it
    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: "{ not json at all"})
    code, reason = run('git commit -m "plan"', root=root, git=git)
    check("binder-malformed-json-denied: unparseable binder bytes come back as a normal "
          "finding carrying the sentinel, so the step denies instead of failing open",
          code == 2 and "not valid JSON" in reason, f"code={code} reason={reason[-160:]}")

    # (8) the fail-open bounds: nothing but a real finding on real bytes ever denies
    root, git = mk_binder_repo(staged=[("M", B1)], blob_fail=(B1,))
    code, msg = run('git commit -m "plan"', root=root, git=git, validate=never_validate)
    check("binder-blob-read-fail-open: an unreadable index blob allows the commit with a "
          "warning naming the path, and never reaches the validator",
          code == 0 and B1 in msg and "could not be read" in msg, f"code={code} msg={msg}")

    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J})
    code, msg = run('git commit -m "plan"', root=root, git=git, validate=crash_validate)
    crash_out = crash_validate(root, Path("x"), 1)[1]
    check("binder-validator-crash-fail-open: a validator that exits nonzero with a traceback "
          "and no findings sentinel is a crash, not a verdict — the commit is allowed with a "
          "warning, on bytes a real run would have denied",
          code == 0 and "not a verdict" in msg
          and BINDER_FINDINGS_SENTINEL not in crash_out, f"code={code} msg={msg}")

    # the same fail-open on an exhausted budget, with the clock stubbed past it
    root, git = mk_binder_repo(staged=[("M", B1), ("M", B2)], blobs={B1: BAD_J, B2: BAD_J})
    ticks = iter([0.0, BINDER_BUDGET + 1, BINDER_BUDGET + 2])
    code, msg = run('git commit -m "plan"', root=root, git=git, validate=never_validate,
                    clock=lambda: next(ticks))
    check("an exhausted end-to-end budget fails open, naming the budget and every binder it "
          "did not get to",
          code == 0 and "50s" in msg and B1 in msg and B2 in msg, f"code={code} msg={msg}")

    # (9) the step reads the tree the payload's cwd names, not this script's checkout
    root_a, git_a = mk_binder_repo(worktree=[("M", B1)], files={B1: GOOD_J})
    root_b, _ = mk_binder_repo(files={B1: BAD_J})
    cmd = f'git commit {B1} -m "plan"'
    code_b, reason_b = decide({**_payload(cmd), "cwd": str(root_b)}, {}, green, stub_gates,
                              git=git_a, root=root_a, validate=real_validate,
                              resolve_root=lambda cwd: root_b)
    code_a, _ = decide({**_payload(cmd), "cwd": "/tmp"}, {}, green, stub_gates, git=git_a,
                       root=root_a, validate=real_validate, resolve_root=lambda cwd: None)
    check("binder-step-resolved-root: with the same command and the same git, the bytes come "
          "from the worktree the payload cwd resolves to — invalid there denies, and the "
          "unresolved control reading the given root allows",
          code_b == 2 and B1 in reason_b and code_a == 0, f"b={code_b} a={code_a}")

    # a pathspec typed in a subdirectory names the binder it actually names
    root, git = mk_binder_repo(worktree=[("M", B1)], files={B1: BAD_J})
    code, reason = decide({**_payload('git commit binders/one.json -m "plan"'),
                           "cwd": str(root / ".karta")}, {}, green, stub_gates, git=git,
                          root=root, validate=real_validate, resolve_root=lambda cwd: root)
    check("a pathspec is resolved against the payload cwd before coverage matching",
          code == 2 and B1 in reason, f"code={code}")

    # (10) the trigger boundary, both directions
    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J})
    code, _ = run("git commit-tree HEAD^{tree} -m x", root=root, git=git,
                  validate=never_validate)
    check("binder-commit-tree-not-gated: `git commit-tree` is plumbing that the gate suite's "
          "looser detection still matches, and the binder step deliberately does not — an "
          "invalid staged binder denies nothing here",
          code == 0 and is_commit_command("git commit-tree HEAD^{tree} -m x")
          and not is_true_commit("git commit-tree HEAD^{tree} -m x"), f"code={code}")

    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J})
    code, _ = decide(_payload(f'{SKIP_VAR}=1 git commit -m "plan"'), {}, must_not_run,
                     stub_gates, git=git, root=root, validate=never_validate)
    check("binder-skip-hatch-bypass: KARTA_SKIP_GATE=1 bypasses the binder step exactly as it "
          "bypasses the gate suite — one documented hatch, not two", code == 0, f"code={code}")

    check("true-commit detection: --amend is in, commit-graph and commit-tree are out",
          is_true_commit("git commit --amend --no-edit")
          and not is_true_commit("git commit-graph write")
          and not is_true_commit("git commit-tree $TREE")
          and is_true_commit("make lint && git commit -m x"))

    # shapes that record nothing, or nothing decidable, run no step at all
    root, git = mk_binder_repo(staged=[("M", B1)], blobs={B1: BAD_J})
    dry, _ = binder_candidates('git commit --dry-run -m "x"', "/tmp", root, git)
    relocated, _ = binder_candidates('git -C /elsewhere commit -m "x"', "/tmp", root, git)
    quoted, _ = binder_candidates('git commit -m "fix; and && more"', "/tmp", root, git)
    check("--dry-run records nothing and `git -C` is a named residual, while a commit message "
          "containing shell operators is still parsed as one commit",
          dry == [] and relocated == [] and quoted == [(B1, "index")],
          f"dry={dry} relocated={relocated} quoted={quoted}")

    # a glob decides a COMMIT's shape, so an unexpandable one skips the step; on an ADD
    # it can only narrow, so the deterministic spec beside it still counts
    root_g, git_g = mk_binder_repo(staged=[("M", B1)], worktree=[("M", B1)],
                                   blobs={B1: BAD_J}, files={B1: BAD_J})
    glob_commit, _ = binder_candidates('git commit "*.json" -m "x"', "/tmp", root_g, git_g)
    glob_add, _ = binder_candidates(f'git add "docs/*.md" {B1} && git commit -m "x"',
                                    "/tmp", root_g, git_g)
    check("a non-deterministic pathspec on the commit skips the step (dropping it would "
          "re-shape the commit into one that records the whole index), while on an add the "
          "deterministic spec beside it still counts",
          glob_commit == [] and glob_add == [(B1, "worktree")],
          f"commit={glob_commit} add={glob_add}")

    # anchoring: a denial may never be triggered by a commit that is merely mentioned.
    # Each pair is the same words in text position and in command position.
    echoed, _ = binder_candidates("echo git commit", "/tmp", root, git)
    grepped, _ = binder_candidates('grep -r "git commit" docs', "/tmp", root, git)
    newline, _ = binder_candidates('make lint\ngit commit -m "x"', "/tmp", root, git)
    chained_ok, _ = binder_candidates('make lint && git commit -m "x"', "/tmp", root, git)
    check("a git commit that does not head its own segment is text, not a command — while the "
          "same invocation after a newline or an && still counts",
          echoed == [] and grepped == [] and newline == [(B1, "index")]
          and chained_ok == [(B1, "index")],
          f"echoed={echoed} grepped={grepped} newline={newline} chained={chained_ok}")

    # live-binder matching is prefix+suffix at any depth. The negative control is the
    # pattern this step deliberately does not use: a path-aware single-star glob cannot
    # cross a slash, so `.karta/binders/*.json` leaves a nested binder unmatched and
    # everything behind such a test would be dead code for it.
    nested = ".karta/binders/team/deep.json"
    check("a live binder is matched by prefix and suffix at any depth, which the single-star "
          "glob it avoids would miss",
          _considered(nested) and not PurePosixPath(nested).match(".karta/binders/*.json")
          and PurePosixPath(".karta/binders/one.json").match(".karta/binders/*.json")
          and _considered(".karta/binders/archive/x.json")
          and not _considered(".karta/binders/notes.txt")
          and not _considered("docs/binders/x.json"))

    # the two files agree on the sentinel, proven by running the real validator
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "one.json"
        p.write_text(BAD_J)
        vcode, vout = _real_validate(ROOT, p, 60)
        pg = Path(td) / "good.json"
        pg.write_text(GOOD_J)
        gcode, gout = _real_validate(ROOT, pg, 60)
    check("the sentinel this step keys on is exactly what validate_binder.py prints for a "
          "finding, and never for a clean run",
          vcode == 1 and BINDER_FINDINGS_SENTINEL in vout
          and gcode == 0 and BINDER_FINDINGS_SENTINEL not in gout,
          f"invalid={vcode} valid={gcode}")

    # --- the register-checker gate, its root, and the budget it fits in -------------
    GATE_SCRIPTS = ["scripts/check_shared_copies.py", "scripts/check_invariant_register.py",
                    "scripts/sync_codex_skills.py", "scripts/sync_codex_agents.py",
                    "scripts/validate_plugin.py"]

    def mk_gate_root(*, omit=()):
        """A tree carrying the gate scripts as empty files — nothing here executes them;
        the recording runner below stands in for every run."""
        r = Path(tempfile.mkdtemp(prefix="pcg-root-"))
        tmp_roots.append(str(r))
        for rel in GATE_SCRIPTS:
            if rel in omit:
                continue
            p = r / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("")
        return r

    ran: list[tuple[str, str, str, int]] = []

    def recorder(name, argv, timeout, cwd):
        ran.append((name, argv[1], str(cwd), timeout))
        return 0, "OK"

    seen_root = mk_gate_root()
    code, _ = decide({**_payload('git commit -m "x"'), "cwd": "/anywhere"}, {}, recorder,
                     git=no_bump, root=ROOT, resolve_root=lambda cwd: seen_root)
    check("precommit-gates-run-at-resolved-root: every gate's script path and subprocess cwd "
          "come from the invocation root the payload resolves to, not from the checkout this "
          "hook's own file sits in — so a commit issued in a linked worktree is judged by that "
          "worktree's own gates",
          code == 0 and len(ran) == 5
          and all(script.startswith(str(seen_root) + os.sep) and cwd == str(seen_root)
                  for _n, script, cwd, _t in ran)
          and not any(str(ROOT) in script for _n, script, _c, _t in ran),
          f"code={code} ran={ran[:2]}")

    # the negative control for the same posture: a resolved tree missing one gate script
    lame_root = mk_gate_root(omit=["scripts/sync_codex_agents.py"])
    ran.clear()
    code, msg = decide({**_payload('git commit -m "x"'), "cwd": "/anywhere"}, {}, recorder,
                       git=no_bump, root=ROOT, resolve_root=lambda cwd: lame_root)
    check("a resolved root that does not carry a gate script fails that gate's spawn and fails "
          "open with a warning naming it, while every gate that IS there still runs — the "
          "recorded decision in run_gates, and the control for the case above",
          code == 0 and "sync_codex_agents.py" in msg
          and [n for n, _s, _c, _t in ran] == ["check_shared_copies", REGISTER_GATE,
                                               "sync_codex_skills --check", "validate_plugin"],
          f"code={code} msg={msg} ran={[n for n, *_ in ran]}")

    def register_exit(status):
        def runner(name, argv, timeout, cwd):
            return (status, "register checker output") if name == REGISTER_GATE else (0, "OK")
        return runner

    code_crash, msg_crash = decide(_payload('git commit -m "x"'), {}, register_exit(2),
                                   stub_gates, git=no_bump)
    code_deny, reason_deny = decide(_payload('git commit -m "x"'), {}, register_exit(1),
                                    stub_gates, git=no_bump)
    check("register-crash-fail-open: the register checker exiting 2 — its own pinned internal "
          "crash — allows the commit with a warning, while exit 1, its named verification or "
          "parse failure, denies. A crash never blocks anyone; a drifted register always does",
          code_crash == 0 and REGISTER_GATE in msg_crash and "exit 2" in msg_crash
          and code_deny == 2 and REGISTER_GATE in reason_deny,
          f"crash={code_crash}/{msg_crash} deny={code_deny}")

    def incomplete(which):
        def runner(name, argv, timeout, cwd):
            if name != which:
                return 0, "OK"
            return GATE_INCOMPLETE, f"partial output\n[gate timed out after {timeout}s]"
        return runner

    code_to, msg_to = decide(_payload('git commit -m "x"'), {}, incomplete(REGISTER_GATE),
                             stub_gates, git=no_bump)
    code_other, reason_other = decide(_payload('git commit -m "x"'), {},
                                      incomplete("validate_plugin"), stub_gates, git=no_bump)
    check("register-timeout-fail-open: a register-checker run that never finished is no verdict "
          "and allows the commit with a warning quoting its own timeout — while the same "
          "incomplete run on any other gate still blocks, the suite's unchanged posture",
          code_to == 0 and f"timed out after {REGISTER_GATE_TIMEOUT}s" in msg_to
          and code_other == 2 and "did not run to completion" in reason_other,
          f"to={code_to}/{msg_to} other={code_other}")

    live = gate_specs(ROOT)
    budget = sum(t for _n, _a, t in live) + BINDER_BUDGET
    # The control is this suite's own history: at the 100s per-gate timeout this hook
    # carried before the register checker joined it, the same list overruns the margin.
    # So the assertion has teeth — it is not satisfied by any arrangement of numbers.
    overrun = sum(100 if t == GATE_TIMEOUT else t for _n, _a, t in live) + BINDER_BUDGET
    check("register-timeout-budget-under-margin: the LIVE gate list's timeouts plus the binder "
          "step's end-to-end budget fit inside the kill margin DERIVED from the hook's configured "
          "timeout, never a hardcoded twin of it — so adding a gate moves the sum, not this "
          "assertion; and the pre-change 100s per-gate timeout overruns that same margin",
          budget <= KILL_MARGIN and KILL_MARGIN == HOOK_TIMEOUT - HOOK_OVERHEAD
          and overrun > KILL_MARGIN,
          f"{budget}s of gates ({len(live)}) vs a {KILL_MARGIN}s margin; at 100s: {overrun}s")

    try:
        wired = json.loads((ROOT / ".claude/settings.json").read_text())
        entries = [h for group in wired["hooks"]["PreToolUse"] for h in group["hooks"]
                   if "precommit_gate.py" in h.get("command", "")]
        configured = entries[0]["timeout"]
    except (OSError, ValueError, KeyError, IndexError):
        configured = None
    check("HOOK_TIMEOUT is the timeout this hook is actually wired with in "
          ".claude/settings.json, read from that file rather than asserted beside it",
          configured == HOOK_TIMEOUT, f"settings.json says {configured}")

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
    code, message = hook_main(sys.stdin.read(), os.environ, _subprocess_runner)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
