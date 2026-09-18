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
commits: KARTA_SKIP_GATE=1 as a leading assignment on the commit command itself
(`KARTA_SKIP_GATE=1 git commit ...`), or in the environment. A mention anywhere
else — a commit message, a path, another command in the chain — is not the hatch.

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
import argparse, fnmatch, json, re, shlex, subprocess, sys
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
#
# The scan is deliberately QUOTE-BLIND, and the cost is real: where the gate
# suite cannot pass (Windows today), an over-detection stops being "harmlessly
# runs the gates" and becomes a hard block on a command that never touched git —
# even `grep -n "git commit" <file>`. That is annoying, and the fix for it is to
# repair the gates, NOT to teach this scan about quotes.
#
# Quote-awareness was tried and reverted. Masking quoted content rests on the
# claim that a quoted word is a string rather than an invocation, and that claim
# is false whenever the string is handed to something that runs it: `bash -c
# "..."`, `eval "..."`, `MSG="$(git commit -m x)"`, backticks, `echo "..." |
# bash`, and — on the platform this hook actually runs on — `cmd /c "..."`,
# `powershell -c "..."`, plus `ssh host "..."`, `python -c`, `node -e`, `perl
# -e`. Guarding with a blocklist of executors cannot work: the set of programs
# that accept a command string is unbounded. Detection also feeds the release
# block below, so anything that evades this regex ships a version bump un-gated.
# Over-detect instead. The deferred-execution fixtures in --self-test pin it.
_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit\b")
_SPLIT_RE = re.compile(r"&&|\|\||;|\||\n")


def is_commit_command(command: str) -> bool:
    return any(_COMMIT_RE.search(seg) for seg in _SPLIT_RE.split(command))


# --- escape hatch: an exact leading assignment, never a mention -------------------
#
# Detection above and the hatch below answer different questions, so they get
# different strictness. Detection asks "might this commit?" and over-answers on
# purpose. The hatch asks "did someone deliberately switch the gates off for this
# commit?" — and a yes skips every gate AND the release block, so it must not be
# granted by accident.
#
# It used to be `f"{SKIP_VAR}=1" in command`, a substring test over the whole
# text, which was wrong on ten of eleven shapes: a commit message mentioning the
# hatch, `KARTA_SKIP_GATE=10`, `=1x`, the token in a pathspec, trailer or
# filename, `X=KARTA_SKIP_GATE=1`, and a prefix sitting on some other command
# (`echo KARTA_SKIP_GATE=1 && git commit`) all skipped every gate, silently.
#
# Now the hatch is read the way a shell reads it: `KARTA_SKIP_GATE=1` (value
# bare, `'1'` or `"1"`, name unquoted, last assignment wins) among the leading
# assignment words of the commit command itself. Every commit invocation in the
# command must carry it. When no segment is credibly a commit — the detector
# fired on text, as in `grep "git commit" f`, or on deferred execution such as
# `bash -c "..."` — the segments the detector matched must carry it instead, so
# the prefix still escapes an over-detection. Unbalanced quoting grants nothing:
# a shell would refuse to run the command anyway. The environment route in
# decide() is unchanged, and remains the way out if this parse ever proves too
# strict. The parser has no raising path, so INV-21's fail-open stance still
# governs the hook as a whole.
_ASSIGN_WORD_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)", re.S)
_LEADING_KEYWORDS = frozenset({"(", "{", "!", "do", "then", "else", "elif", "if",
                               "while", "until", "time"})
# The set _SPLIT_RE cuts on, plus `|&` (a pipe that also carries stderr). It
# must be one token: split as `|` it leaves a stray `&` word at the head of the
# next command, which then no longer reads as a commit — so in
# `KARTA_SKIP_GATE=1 git commit -m x |& git commit -m y` only the prefixed
# commit was checked, and the unprefixed one rode through.
_SHELL_OPERATORS = ("&&", "||", "|&", ";", "|", "\n")


_Word = tuple[str, "int | None"]


_Segment = tuple[list[_Word], list[str], int, str, list[str]]


def _shell_segments(command: str) -> list[_Segment] | None:
    """The command's segments, split only at UNQUOTED operators. Each segment is
    (words, substitutions, process_substitution_count, operator_after,
    heredoc_bodies). operator_after is the operator that ended it ("" for the
    final segment), which says whether its output flows into the next; the
    heredoc bodies are text fed to the command on its stdin. A word is
    (text, quoted_at): the dequoted text, and
    how long that text was when the first quoting character was met (None if
    never quoted) — which is what tells `KARTA_SKIP_GATE="1"` (quoted value, an
    assignment) apart from `'KARTA_SKIP_GATE'=1` (quoted name, a plain word).

    `substitutions` holds the interior of every command substitution the shell
    would actually run in that segment — `$(...)` or backticks, bare or inside
    double quotes — and nothing that is inert: single-quoted text and an escaped
    `\\$(` are literals. A substitution is consumed whole, so an operator inside
    one does not split the segment. None when quoting or a substitution is
    unbalanced: the shell would refuse to run it."""
    segments: list[_Segment] = []
    words: list[_Word] = []
    subs: list[str] = []
    docs: list[str] = []                      # heredoc bodies fed to this segment
    # Heredocs awaiting their body, each with the docs and subs lists of the
    # command that OWNS its `<<` — not of whichever command the next newline
    # ends. In `git commit -F- <<'EOF' && git push`, the body is the commit's
    # message; handed to `git push` it made the push look like it ran text.
    pending: list[tuple[str, bool, bool, list[str], list[str]]] = []
    procs = 0
    buf: list[str] = []
    quoted_at: int | None = None
    in_word = False
    i, n = 0, len(command)

    def end_word() -> None:
        nonlocal buf, quoted_at, in_word
        if in_word:
            words.append(("".join(buf), quoted_at))
        buf, quoted_at, in_word = [], None, False

    def read_delim(k: int) -> tuple[str, bool, bool, int] | None:
        """Parse the heredoc operator at k (`<<EOF`, `<<-EOF`, `<<'EOF'`):
        (delimiter, strip_tabs, quoted, index_after), or None if malformed."""
        j = k + 2
        strip_tabs = command.startswith("-", j)
        j += strip_tabs
        while j < n and command[j] in " \t":
            j += 1
        delim: list[str] = []
        quoted = False
        while j < n and command[j] not in " \t\n;&|<>()":
            if command[j] in "'\"\\":
                quoted = True
            else:
                delim.append(command[j])
            j += 1
        return ("".join(delim), strip_tabs, quoted, j) if delim else None

    def read_bodies(k: int, waiting: list[tuple[str, bool, bool]]
                    ) -> tuple[int, list[tuple[str, bool]]] | None:
        """k is just past a newline: read each waiting heredoc's body up to its
        delimiter line. (index_after, [(body, quoted)]), or None if one never ends."""
        bodies: list[tuple[str, bool]] = []
        for delim, strip_tabs, quoted in waiting:
            lines: list[str] = []
            while True:
                nl = command.find("\n", k)
                line = command[k:] if nl < 0 else command[k:nl]
                if (line.lstrip("\t") if strip_tabs else line) == delim:
                    k = n if nl < 0 else nl + 1
                    break
                if nl < 0:
                    return None
                lines.append(line)
                k = nl + 1
            bodies.append(("\n".join(lines), quoted))
        return k, bodies

    def close_paren(j: int) -> tuple[int, str]:
        """(index just past the `)` closing the `$(` whose `(` is at j, the text
        of the substitution that can RUN), or (-1, "") if it never closes. A
        heredoc body inside it is data, so it is left out of the returned text —
        except, under an unquoted delimiter, the substitutions in that body. This
        is what lets `git commit -m "$(cat <<'EOF' … EOF)"`, the usual way an
        agent writes a commit message, carry a message that mentions a commit."""
        depth, k = 1, j + 1
        runnable: list[str] = []
        start = k
        waiting: list[tuple[str, bool, bool]] = []
        while k < n:
            ch = command[k]
            if ch == "\\":
                k += 2
                continue
            if ch in "'\"":
                e = k + 1
                while e < n and command[e] != ch:
                    e += 2 if (ch == '"' and command[e] == "\\") else 1
                if e >= n:
                    return -1, ""
                k = e + 1
                continue
            # A `#` that starts a word opens a comment running to the newline; a
            # `)` inside it closes nothing, so `$(echo a #)` does not end there.
            if ch == "#" and command[k - 1] in " \t\n(;&|":
                nl = command.find("\n", k)
                if nl < 0:
                    return -1, ""
                k = nl
                continue
            if command.startswith("<<", k) and not command.startswith("<<<", k):
                d = read_delim(k)
                if d is None:
                    return -1, ""
                waiting.append(d[:3])
                k = d[3]
                continue
            if ch == "\n" and waiting:
                runnable.append(command[start:k])
                r = read_bodies(k + 1, waiting)
                if r is None:
                    return -1, ""
                k, bodies = r
                for body, quoted in bodies:
                    if not quoted:
                        runnable.extend(_body_substitutions(body))
                waiting = []
                start = k
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    runnable.append(command[start:k])
                    return k + 1, "\n".join(runnable)
            k += 1
        return -1, ""

    def close_brace(j: int) -> int:
        """Index just past the `}` closing the `${` whose `{` is at j, or -1."""
        depth, k = 1, j + 1
        while k < n:
            ch = command[k]
            if ch == "\\":
                k += 2
                continue
            if ch in "'\"":
                e = k + 1
                while e < n and command[e] != ch:
                    e += 2 if (ch == '"' and command[e] == "\\") else 1
                if e >= n:
                    return -1
                k = e + 1
                continue
            if command.startswith("$(", k):
                e, _ = close_paren(k + 1)
                if e < 0:
                    return -1
                k = e
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return k + 1
            k += 1
        return -1

    def close_tick(j: int) -> int:
        """Index just past the backtick closing the one at j, or -1."""
        k = j + 1
        while k < n:
            if command[k] == "\\":
                k += 2
                continue
            if command[k] == "`":
                return k + 1
            k += 1
        return -1

    def substitution_at(j: int, in_dq: bool = False) -> int | None:
        """If a substitution opens at j, record it and return the index past it;
        -1 when it never closes; None when none opens here. Process substitution
        (`<(…)`, `>(…)`) exists only outside double quotes, and is also flagged
        in `procs`: text that flows into one can be run by it, as in
        `echo "git commit" | tee >(bash)`, whatever its interior says."""
        nonlocal procs
        if not in_dq and command.startswith("${", j):
            # An unquoted `${…}` is one word, and after expansion bash splits it
            # into words and may run them: `${X:-git<newline>commit -m y}` is a
            # commit. So its interior gets the same check, and nothing inside it
            # — not even a newline — splits the segment.
            e = close_brace(j + 1)
            if e >= 0:
                subs.append(command[j + 2:e - 1])
                buf.append(command[j:e])
            return e
        if command.startswith("$(", j) or (not in_dq and command[j:j + 2] in ("<(", ">(")):
            e, runnable = close_paren(j + 1)
            if e >= 0:
                subs.append(runnable)
                buf.append(command[j:e])
                procs += command[j] != "$"
            return e
        if command[j] == "`":
            e = close_tick(j)
            if e >= 0:
                subs.append(command[j + 1:e - 1])
                buf.append(command[j:e])
            return e
        return None

    while i < n:
        c = command[i]
        if c in " \t":
            end_word()
            i += 1
            continue
        # A heredoc (`<<EOF`, `<<-EOF`, `<<'EOF'`; not the `<<<` here-string) is
        # data for this command, not more commands: its body is read at the next
        # newline and kept apart. Bash expands substitutions in the body only when
        # the delimiter is unquoted, so only then can the body run a commit itself.
        if command.startswith("<<<", i):
            # A here-string is one token: taken `<` by `<`, the scan would reach
            # `<<` one character in and misread the rest as a heredoc delimiter.
            end_word()
            words.append(("<<<", None))
            i += 3
            continue
        if command.startswith("<<", i):
            end_word()
            d = read_delim(i)
            if d is None:
                return None
            pending.append((d[0], d[1], d[2], docs, subs))
            i = d[3]
            continue
        op = next((o for o in _SHELL_OPERATORS if command.startswith(o, i)), None)
        # A bare `&` ends a command too: `KARTA_SKIP_GATE=1 true & git commit`
        # backgrounds `true` and runs the commit unprefixed. It is only a
        # separator when it is not part of a redirection (`2>&1`, `&>log`).
        if (op is None and c == "&" and command[i + 1:i + 2] != ">"
                and not (i > 0 and command[i - 1] in "<>|")):
            op = "&"
        if op:
            end_word()
            i += len(op)
            if op == "\n" and pending:
                r = read_bodies(i, [p[:3] for p in pending])
                if r is None:
                    return None  # a heredoc that never ends
                i, bodies = r
                for (body, quoted), (_, _, _, owner_docs, owner_subs) in zip(bodies, pending):
                    owner_docs.append(body)
                    if not quoted:
                        owner_subs.extend(_body_substitutions(body))  # these will run
                pending = []
            segments.append((words, subs, procs, op, docs))
            words, subs, docs, procs = [], [], [], 0
            continue
        if c in "<>" and command[i + 1:i + 2] != "(":
            # A redirect is its own token even glued to a word — `"…">go.sh`,
            # `>&go.sh`, `>"go.sh"` — keeping a leading fd (`2>`) or `&` (`&>`).
            held = "".join(buf)
            fd = held if quoted_at is None and (held.isdigit() or held == "&") else ""
            if fd:
                buf, in_word = [], False
            else:
                end_word()
            j = i + 1
            if j < n and command[j] == c:
                j += 1
            if j < n and command[j] in "&|":
                j += 1
            words.append((fd + command[i:j], None))
            i = j
            continue
        in_word = True
        e = substitution_at(i)
        if e is not None:
            if e < 0:
                return None
            i = e
            continue
        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                return None
            if quoted_at is None:
                quoted_at = len("".join(buf))
            buf.append(command[i + 1:j])
            i = j + 1
        elif c == '"':
            if quoted_at is None:
                quoted_at = len("".join(buf))
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n and command[j + 1] in '"\\$`':
                    buf.append(command[j + 1])   # escaped: a literal, never a substitution
                    j += 2
                    continue
                e = substitution_at(j, in_dq=True)   # "$(...)" still executes in double quotes
                if e is not None:
                    if e < 0:
                        return None
                    j = e
                    continue
                buf.append(command[j])
                j += 1
            if j >= n:
                return None
            i = j + 1
        elif c == "\\":
            if i + 1 >= n:
                return None
            if quoted_at is None:
                quoted_at = len("".join(buf))
            buf.append(command[i + 1])
            i += 2
        else:
            buf.append(c)
            i += 1
    end_word()
    if pending:
        return None  # a heredoc whose body never arrived
    segments.append((words, subs, procs, "", docs))
    return segments


def _body_substitutions(text: str) -> list[str]:
    """The interiors of the `$(…)` and backtick substitutions in an unquoted
    heredoc body — the only parts of it bash runs. A backslash escapes the next
    character; quotes in a heredoc body are literal, so they are not tracked.
    An unclosed substitution returns its whole remainder, so it is still checked."""
    found: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith("$(", i):
            # Quotes are literal in the body itself but NOT inside a substitution
            # in it: `$(printf ')' ; git commit)` does not end at the quoted `)`.
            depth, k = 1, i + 2
            while k < n and depth:
                ch = text[k]
                if ch == "\\":
                    k += 2
                    continue
                if ch in "'\"":
                    # Inside double quotes a backslash escapes the next char, so
                    # `"a\"b)"` does not end at the escaped quote; single quotes
                    # take everything literally.
                    e = k + 1
                    while e < n and text[e] != ch:
                        e += 2 if (ch == '"' and text[e] == "\\") else 1
                    k = n if e >= n else e + 1
                    continue
                depth += {"(": 1, ")": -1}.get(ch, 0)
                k += 1
            found.append(text[i + 2:k - 1] if depth == 0 else text[i + 2:])
            i = k
            continue
        if text[i] == "`":
            k = text.find("`", i + 1)
            found.append(text[i + 1:k] if k >= 0 else text[i + 1:])
            i = n if k < 0 else k + 1
            continue
        i += 1
    return found


def _split_leading(words: list[tuple[str, int | None]]) -> tuple[str | None, list[str]]:
    """(the hatch value set by this segment's leading assignments, the command
    words after them). The hatch value is None when the segment never assigns
    it; the last assignment wins, as it does in a shell."""
    ws = list(words)
    # Grouping and reserved words that can precede a command's assignments:
    # `do KARTA_SKIP_GATE=1 git commit` in a loop, `then …`, `time -p …`, `! …`.
    while ws and ws[0][1] is None and ws[0][0] in _LEADING_KEYWORDS:
        was_time = ws.pop(0)[0] == "time"
        if was_time and ws and ws[0] == ("-p", None):
            ws.pop(0)
    if ws and ws[0][0].startswith("(") and ws[0][1] != 0:
        text, q = ws[0]
        ws[0] = (text[1:], None if q is None else q - 1)
    value: str | None = None
    for _ in range(2):  # the leading assignments, then any after a bare `env`
        while ws:
            text, q = ws[0]
            m = _ASSIGN_WORD_RE.fullmatch(text)
            if not m or (q is not None and q <= m.end(2)):
                break  # not an assignment, or its NAME (or the `=`) was quoted
            if m.group(1) == SKIP_VAR:
                value = None if m.group(2) else m.group(3)
            ws.pop(0)
        # `env KARTA_SKIP_GATE=1 git commit` hands git the variable just as the
        # bare prefix does. Only a bare `env` is read: its options (`-i`, `-u
        # NAME`, `-S`) change the environment in ways this parse does not model,
        # so they fall through to a deny — the gates run, never a false skip.
        if ws and ws[0][1] is None and ws[0][0].replace("\\", "/").rsplit("/", 1)[-1] in ("env", "env.exe"):
            ws.pop(0)
            if ws and ws[0][0] == "--":   # end of env's options (quoted or not); changes nothing
                ws.pop(0)
            elif ws and ws[0][0].startswith("-"):
                # `-i`, `-u NAME`, `-S`: env rewrites the environment before git
                # sees it, so a hatch set BEFORE env proves nothing either —
                # `KARTA_SKIP_GATE=1 env -u KARTA_SKIP_GATE git commit` runs bare.
                return None, [text for text, _ in ws]
            continue
        break
    return value, [text for text, _ in ws]


_GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                             "--config-env", "--super-prefix"})


def _is_git_commit(words: list[str]) -> bool:
    """Whether these command words are credibly a commit: the program is git by
    basename, and its subcommand — found word by word, past git's global options
    and their values — is `commit`. Word by word, not by joining the words and
    matching a regex: `git -c user.name="foo bar" commit` has a value with a
    space in it, which a joined string mistakes for two words."""
    if not words:
        return False
    program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if program not in ("git", "git.exe"):
        return False
    k = 1
    while k < len(words):
        if words[k] in _GIT_VALUE_OPTS:
            k += 2
        elif words[k].startswith("-"):
            k += 1
        else:
            return words[k] == "commit"
    return False


def hatch_prefixed(command: str) -> bool:
    """True only when the escape hatch is set as an exact leading assignment on
    every commit invocation in `command` — never on the strength of a mention."""
    segments = _shell_segments(command)
    if segments is None:
        return False
    # A commit inside a substitution runs before, and outside, whatever command
    # the prefix sits on — `KARTA_SKIP_GATE=1 git commit -m x $(git commit -m y)`
    # runs the inner commit with no hatch in its environment. No prefix anywhere
    # can cover it, so the whole command runs the gates. A substitution that runs
    # something else (`-m "$(cat msg.txt)"`) is fine.
    if any(_COMMIT_RE.search(sub) for _, subs, _, _, _ in segments for sub in subs):
        return False
    parsed = [(_split_leading(ws), ws, procs, op, docs)
              for ws, _, procs, op, docs in segments if ws]

    def mentions(ws: list[_Word], docs: list[str] = ()) -> bool:
        return bool(_COMMIT_RE.search(" ".join([*(text for text, _ in ws), *docs])))

    # Nothing before the first command that mentions a commit can run one the
    # detector sees, so the walk starts there. From there on, one rule for every
    # command, checked against real bash (see the negative controls): it needs
    # the prefix if it is a commit, if its own text mentions one and it could
    # run that text, or if such text flows into it — through a pipe, or through
    # a file an earlier command wrote it to — and it could run what it reads.
    # A command that could not run text is an inert filter; see _INERT_FILTERS.
    # Unrelated later commands (`&& git log`, `&& git push`) are left alone.
    first = next((k for k, ((_, rest), ws, _, _, docs) in enumerate(parsed)
                  if mentions(ws, docs) or _is_git_commit(rest)), None)
    if first is None:
        return False
    tail = parsed[first:]
    if any(procs for _, _, procs, _, _ in tail):
        return False  # a process substitution is somewhere text can be run
    carried = downstream = written_anywhere = seen = False
    tainted: set[str] = set()   # variables holding text that mentions a commit
    written: set[str] = set()   # basenames of files that commit text was sent to

    def reads(text: str) -> bool:
        return bool(tainted) and bool(re.search(
            r"\$\{?(?:" + "|".join(map(re.escape, tainted)) + r")\b", text))

    # The line's own variable assignments — `x=go.sh`, `f=out.txt`, and a `for`
    # loop's list — so a `$x` can be resolved to what it names rather than
    # suspected of naming anything. A variable the line never sets comes from the
    # environment; no one typing this line put a file name in it on purpose.
    assigned: dict[str, str] = {}
    for (_, r), ws2, _, _, _ in parsed:
        for t, _ in ws2:
            m = _ASSIGN_WORD_RE.fullmatch(t)
            if m:
                assigned[m.group(1)] = m.group(3)
        if len(r) >= 3 and r[0] == "for" and r[2] == "in":
            assigned[r[1]] = " ".join(r[3:])

    def expand(text: str) -> str:
        return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: assigned.get(m.group(1), ""), text)

    def base(name: str) -> str:
        return name.replace("\\", "/").rsplit("/", 1)[-1]

    def names_written(ws: list[_Word]) -> bool:
        # A later command is held to a written file only if it could name it —
        # `bash go.sh`, `source ./go.sh`, `sh < go.sh` — never `&& git push`.
        # It could name it literally; through a glob that matches it (`bash
        # *.sh`); through a variable the line set to it (`x=go.sh; bash "$x"`);
        # or through a substitution, whose output cannot be known (`bash $(ls)`).
        # An environment variable (`--out "$OUT"`) names nothing written here.
        if not written:
            return False
        for text, _ in ws:
            if "$(" in text or "`" in text:
                return True
            for piece in expand(text).split():
                b = base(piece)
                for name in written:
                    if re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", piece):
                        return True
                    if any(ch in b for ch in "*?[") and fnmatch.fnmatchcase(name, b):
                        return True
        return False

    for (value, rest), ws, _, op, docs in tail:
        # A heredoc body is text flowing INTO the command, like a pipe, so it is
        # judged by whether the command could run it — `bash <<EOF` could,
        # `git commit -F - <<EOF` is a commit and needs the prefix anyway. A
        # variable carries text the same way: `x='git commit …'; eval "$x"`.
        credible = _is_git_commit(rest)
        # A command that names a written file reads its text too, and passes it
        # on: `cat go.sh > run.sh` taints run.sh, `cat go.sh | bash` feeds bash.
        carries_text = (mentions(ws) or credible or downstream or mentions([], docs)
                        or any(reads(text) for text, _ in ws) or names_written(ws))
        if not rest:  # assignments only: remember which names now hold commit text
            for text, _ in ws:
                m = _ASSIGN_WORD_RE.fullmatch(text)
                if m and (_COMMIT_RE.search(m.group(3)) or reads(m.group(3))):
                    tainted.add(m.group(1))   # `y=$x` copies the taint along
        inert = _is_inert_filter(rest)
        if value == "1":
            carried = True
        elif credible:
            return False
        elif not inert and (carries_text or written_anywhere):
            return False
        # Side effects are tracked whether or not the command carries the prefix:
        # a prefix grants THIS command, never whatever it leaves behind.
        #  - Commit text sent to a named file: `echo "git commit" > go.sh` — and
        #    a compound's closing word carrying the redirect for its whole body:
        #    `{ echo "git commit"; } > go.sh`, `…; fi > go.sh`. A later command
        #    that names the file is then held to it.
        #  - A prefixed command that could run the text may have written it to
        #    a file this parse cannot name: `KARTA_SKIP_GATE=1 bash -c 'echo … >
        #    go.sh'`. Then every later command that could run text is held.
        closer = bool(rest) and rest[0] in _COMPOUND_CLOSERS
        if not credible and (carries_text or (closer and seen)):
            files: set[str] = set()
            for target in _written_files(ws, rest):
                resolved = expand(target)
                # A target this line cannot resolve — an unset `$OUT`, a
                # substitution, a glob, a descriptor — is a real file this parse
                # cannot name: `… > "$OUT" && bash out.txt`.
                if (not resolved or "$(" in target or "`" in target
                        or any(ch in resolved for ch in "$*?[")):
                    written_anywhere = True
                else:
                    files.add(base(resolved))
            for name in files:
                written.add(name)
                # Any OTHER command in the line that names this file may have made
                # it an alias, before the write or after: `ln -s go.sh link; … >
                # link; bash go.sh`. Its other file-like arguments are held too.
                pattern = r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])"
                for (_, other_rest), other_ws, _, _, _ in parsed:
                    if other_ws is not ws and any(re.search(pattern, expand(t)) for t, _ in other_ws):
                        written |= {base(expand(a)) for a in other_rest[1:]
                                    if a and not a.startswith("-")} - {""}
        if value == "1" and carries_text and not credible and not inert:
            written_anywhere = True
        seen = seen or carries_text
        downstream = carries_text and op in ("|", "|&")
    return carried


_COMPOUND_CLOSERS = frozenset({"}", "fi", "done", "esac", ")"})


_STDOUT_REDIRECT_RE = re.compile(r"(1|&)?>>?([&|])?")


def _written_files(words: list[_Word], rest: list[str]) -> set[str]:
    """The files this command sends what it prints to: the target of a stdout
    redirect token (`>`, `>>`, `1>`, `&>`, `>|`, and `>&` when a filename rather
    than a descriptor follows — `>&go.sh` writes, `>&2` does not), and tee's
    file arguments. Redirects are their own tokens (see _shell_segments), so a
    glued `"…">go.sh` or a quoted `>"go.sh"` is seen too."""
    names: set[str] = set()
    for k, (text, q) in enumerate(words):
        m = _STDOUT_REDIRECT_RE.fullmatch(text) if q is None else None
        if not m:
            continue
        target = words[k + 1][0] if k + 1 < len(words) else ""
        if m.group(2) == "&" and re.fullmatch(r"[12]|-", target):
            continue  # `>&2`, `>&1`, `>&-`: stdout, stderr, or closed — not a file
        if (m.group(2) == "&" and target.isdigit()) or re.match(r"/(dev/fd|proc/[^/]+/fd)/", target):
            # A user descriptor (`exec 3>go.sh; … >&3`) or `/dev/fd/3`: a real
            # file, but not one this parse can name. "" means "somewhere unknown".
            names.add("")
            continue
        names.add(target)
    program = rest[0].replace("\\", "/").rsplit("/", 1)[-1].lower() if rest else ""
    if program.removesuffix(".exe") == "tee":
        names.update(a for a in rest[1:] if not a.startswith("-"))
    return names


# Programs that can read text but never run it — so a later `| tail -2` needs
# no prefix of its own. This is an ALLOWLIST on purpose: a program missing from
# it costs a false deny (the gates run), never a false grant. A blocklist of
# executors was tried for detection and could not close; this one fails safe.
# Deliberately absent: sed and awk (GNU sed's `e` and awk's system() execute),
# xargs, the pagers less and more (LESSOPEN and `!` run commands, and nothing
# pages in a non-interactive shell), and every shell or interpreter.
_INERT_FILTERS = frozenset({
    "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "sort", "uniq", "cut",
    "cat", "tee", "tr", "nl", "column", "jq", "findstr",
    "echo", "printf", "true", "false",
    "}", "fi", "done", "esac", ")",   # a compound's closing word runs nothing itself
})


def _is_inert_filter(words: list[str]) -> bool:
    if not words:
        return True
    program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return program.removesuffix(".exe") in _INERT_FILTERS


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
    if env.get(SKIP_VAR) == "1" or hatch_prefixed(command):
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
        # DEFERRED EXECUTION — each of these really commits, because something
        # is handed the text and runs it. The quote-blind scan catches them all
        # precisely BECAUSE it does not respect quoting. Pinned so that a future
        # attempt to make detection quote-aware fails here instead of in the
        # field: an earlier attempt silently lost every one of these, which also
        # disarms the release block, since it only arms behind detection.
        ('MSG="$(git commit -m bump)"', True),
        ('echo "$(git commit -m x)"', True),
        ('bash -c "git commit -m x"', True),
        ("sh -c 'git commit -m x'", True),
        ('eval "git commit -m x"', True),
        ('X="`git commit -m bump`"', True),
        ('echo "git commit -m x" | bash', True),
        ('cmd /c "git commit -m x"', True),
        ('powershell -c "git commit -m x"', True),
        ('ssh host "git commit -m x"', True),
        ('python -c "run(\'git commit -m x\')"', True),
    ]
    for cmd, want in detect:
        check(f"detect {cmd!r} -> {want}", is_commit_command(cmd) == want)

    # KNOWN GAPS, pinned so they cannot regress silently and cannot be mistaken
    # for a guarantee. The regex wants `git` and `commit` as adjacent unquoted
    # words, so quoting or splitting either one evades detection entirely. Every
    # line below really commits and the hook does not fire. This is the same
    # class as the bypasses AGENTS.md already names (cherry-pick, rebase,
    # reset): a hook that can only read command text cannot stop someone
    # deliberately spelling around it. Closing it means a real tokenizer — and
    # note a tokenizer would NOT close the deferred-execution cases above.
    known_gap = ['"git" commit -m x', 'git "commit" -m x', 'gi"t" commit -m x',
                 'git com"mit" -m x', "git 'commit' -m x", "git $'commit' -m x",
                 "make release", ". ./release.sh"]
    for cmd in known_gap:
        check(f"KNOWN GAP (evades detection): {cmd!r}",
              is_commit_command(cmd) is False)

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

    # The hatch is an exact leading assignment on the commit, never a mention.
    # The first block is every shape the prefix is legitimately used in.
    hatch = [
        ('KARTA_SKIP_GATE=1 git commit -F msg.txt', True),
        ('KARTA_SKIP_GATE=1 git commit -q -m "x"', True),
        ('FOO=bar KARTA_SKIP_GATE=1 git commit -m x', True),
        ('KARTA_SKIP_GATE=1 FOO=bar git commit -m x', True),
        ('make lint && KARTA_SKIP_GATE=1 git commit -m x', True),
        ('KARTA_SKIP_GATE=1 git commit -m "a && b; c | d"', True),
        ('KARTA_SKIP_GATE=1 git -C repo commit -m x', True),
        ('KARTA_SKIP_GATE="1" git commit -m x', True),
        ("KARTA_SKIP_GATE='1' git commit -m x", True),
        ("KARTA_SKIP_GATE=1'' git commit -m x", True),
        ('(KARTA_SKIP_GATE=1 git commit -m x)', True),
        ('KARTA_SKIP_GATE=1 /usr/bin/git commit -m x', True),
        ('KARTA_SKIP_GATE=1 git commit -m x 2>&1', True),
        ('KARTA_SKIP_GATE=1 git commit -m x &> log', True),
        ('KARTA_SKIP_GATE=1 git commit -m x &', True),
        ('env KARTA_SKIP_GATE=1 git commit -m x', True),
        ('KARTA_SKIP_GATE=1 env git commit -m x', True),
        # The detector over-fires on text; the prefix must still escape that,
        # or on Windows, where the gates are red, a grep would be unescapable.
        ('grep -n "git commit" f.py && KARTA_SKIP_GATE=1 git commit -F m.txt', True),
        ('KARTA_SKIP_GATE=1 grep -n "git commit" f.py', True),
        ('KARTA_SKIP_GATE=1 bash -c "git commit -m x"', True),
        # Every one of these skipped every gate under the old substring test.
        ('git commit -m "bump KARTA_SKIP_GATE=1 later"', False),
        ('git commit -m "KARTA_SKIP_GATE=1"', False),
        ('KARTA_SKIP_GATE=10 git commit -m x', False),
        ('KARTA_SKIP_GATE=1x git commit -m x', False),
        ('KARTA_SKIP_GATE=1.5 git commit -m x', False),
        ('KARTA_SKIP_GATE= git commit -m x', False),
        ('git commit KARTA_SKIP_GATE=1 -m x', False),
        ('git commit --trailer "Note: KARTA_SKIP_GATE=1" -m x', False),
        ('git commit -F KARTA_SKIP_GATE=1.txt', False),
        ('X=KARTA_SKIP_GATE=1 git commit -m x', False),
        ('echo KARTA_SKIP_GATE=1 && git commit -m x', False),
        ('KARTA_SKIP_GATE=1; git commit -m x', False),
        ('KARTA_SKIP_GATE=1 true && git commit -m x', False),
        # A bare `&` separates commands exactly as `&&` does.
        ('KARTA_SKIP_GATE=1 true & git commit -m x', False),
        ('KARTA_SKIP_GATE=1 git commit -m x |& git commit -m y', False),
        ('KARTA_SKIP_GATE=1 git commit -m x 2>&1 & git commit -m y', False),
        # A substitution runs the commit before, and outside, the prefixed command.
        ('KARTA_SKIP_GATE=1 echo $(git commit -m x)', False),
        ('KARTA_SKIP_GATE=1 echo "$(git commit -m x)"', False),
        ('KARTA_SKIP_GATE=1 echo ""$(git commit -m x)', False),
        ('KARTA_SKIP_GATE=1 echo `git commit -m x`', False),
        # ...on the credible path too: the inner commit runs first, unprefixed.
        ('KARTA_SKIP_GATE=1 git commit -m x $(git commit -m y)', False),
        ('KARTA_SKIP_GATE=1 git commit -m "$(git commit -m y)"', False),
        ('KARTA_SKIP_GATE=1 git commit -m x `git commit -m y`', False),
        ('KARTA_SKIP_GATE=1 git commit -m a && echo $(git commit -m b)', False),
        # ...but a substitution that runs something else is fine, and an escaped
        # `\$(` inside double quotes is a literal, not a substitution.
        ('KARTA_SKIP_GATE=1 git commit -m "$(cat msg.txt)"', True),
        ('KARTA_SKIP_GATE=1 git commit -m "$(printf "a; b")"', True),
        ('KARTA_SKIP_GATE=1 grep -n "\\$(git commit" f.py', True),
        # Text becomes a commit only when something later reads and runs it.
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" | bash', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" > go.sh && bash go.sh', False),
        ('git log -1; cat f | KARTA_SKIP_GATE=1 run.py --note "a git commit"', True),
        ('env -- KARTA_SKIP_GATE=1 git commit -m x', True),
        # A later filter that cannot run its input needs no prefix of its own.
        ('KARTA_SKIP_GATE=1 grep -n "git commit" f.py | head', True),
        ('cat f | KARTA_SKIP_GATE=1 run.py --note "a git commit" 2>&1 | tail -2', True),
        # ...but anything not on that allowlist does, since it might.
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" | sed -e p', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" | python3', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" | less', False),
        # Process substitution runs whatever flows into it, and its own interior.
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" | tee >(bash)', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m x" > >(bash)', False),
        ('KARTA_SKIP_GATE=1 diff <(git commit -m x) f', False),
        ('KARTA_SKIP_GATE=1 git commit -m "see <(x)"', True),
        # A heredoc is data for its command. Its body runs substitutions only
        # when the delimiter is unquoted — exactly as bash does.
        ('KARTA_SKIP_GATE=1 git commit -F - <<EOF\nmsg that mentions git commit\nEOF', True),
        ("KARTA_SKIP_GATE=1 git commit -F - <<'EOF'\nmsg $(git commit -m y)\nEOF", True),
        ('KARTA_SKIP_GATE=1 git commit -F - <<EOF\nmsg $(git commit -m y)\nEOF', False),
        ('KARTA_SKIP_GATE=1 bash <<EOF\ngit commit -m y\nEOF', True),
        ('bash <<EOF\ngit commit -m y\nEOF', False),
        ('KARTA_SKIP_GATE=1 git commit -m x <<EOF', False),   # body never arrives
        # The usual agent idiom: a heredoc message inside $(…). Its body is data,
        # so a message that mentions a commit is fine — and a `)` in the body
        # must not end the substitution early (it once did, found in real bash).
        ("KARTA_SKIP_GATE=1 git commit -m \"$(cat <<'EOF'\nfix: the git commit hatch (again)\nEOF\n)\"", True),
        ('KARTA_SKIP_GATE=1 git commit -m x && echo "$(cat <<X\n)\nX\ngit commit -m y)"', False),
        ('KARTA_SKIP_GATE=1 git commit -m x && echo "$(echo a #)\ngit commit -m y)"', False),
        ('env "--" KARTA_SKIP_GATE=1 git commit -m x', True),
        # Shell constructs, each checked against real bash.
        ('for i in 1; do KARTA_SKIP_GATE=1 git commit -m y; done', True),
        ('for i in 1; do git commit -m y; done', False),
        ('if true; then KARTA_SKIP_GATE=1 git commit -m y; fi', True),
        ('time -p KARTA_SKIP_GATE=1 git commit -m x', True),
        ('KARTA_SKIP_GATE=1 bash <<< "git commit -m y"', True),
        ('bash <<< "git commit -m y"', False),
        ("KARTA_SKIP_GATE=1 git commit -m a; x='git commit -m y'; eval \"$x\"", False),
        ("KARTA_SKIP_GATE=1 git commit -m a; x='git commit -m y'; $x", False),
        ('MSG="mentions git commit"; KARTA_SKIP_GATE=1 git commit -m "$MSG" && git push', True),
        ("trap 'git commit -m y' EXIT; KARTA_SKIP_GATE=1 git commit -m a", False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && source go.sh', False),
        # Review round 4, each confirmed as a false grant in real bash first.
        ('KARTA_SKIP_GATE=1 git commit -m a && git -c user.name="foo bar" commit -m b', False),
        ('KARTA_SKIP_GATE=1 git commit -m a && git -C "./sub dir" commit -m b', False),
        ('KARTA_SKIP_GATE=1 git -c user.name="foo bar" commit -m b', True),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y">go.sh && bash go.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" >"go.sh" && bash go.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" >& go.sh && bash go.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" >&go.sh && bash go.sh', False),
        ('KARTA_SKIP_GATE=1 git commit -m x 2>&1 >&2 && git push', True),
        ('KARTA_SKIP_GATE=1 env -u KARTA_SKIP_GATE git commit -m x', False),
        ('KARTA_SKIP_GATE=1 env -i PATH="$PATH" git commit -m x', False),
        ('KARTA_SKIP_GATE=1 git commit -m x && ${X_UNSET:-git\ncommit -m y}', False),
        ('KARTA_SKIP_GATE=1 git commit -m x; { echo "git commit -m y"; } > go.sh; bash go.sh', False),
        ('KARTA_SKIP_GATE=1 git commit -m x; if true; then echo "git commit -m y"; fi > go.sh; bash go.sh', False),
        ("KARTA_SKIP_GATE=1 bash -c 'echo \"git commit -m y\" > go.sh'; bash go.sh", False),
        ("KARTA_SKIP_GATE=1 git commit -m a; x='git commit -m y'; y=$x; eval \"$y\"", False),
        ('KARTA_SKIP_GATE=1 git commit -m "${MSG:-default}"', True),
        # Review round 5, each confirmed in real bash first.
        ("KARTA_SKIP_GATE=1 git commit -F - <<EOF\n$(printf ')' ; git commit -m y)\nEOF", False),
        ('KARTA_SKIP_GATE=1 git commit -F - <<EOF\n$(echo "a)b" ; git commit -m y)\nEOF', False),
        ('KARTA_SKIP_GATE=1 grep -n "git commit" f.py > grep.log && git push', True),
        ('KARTA_SKIP_GATE=1 grep -n "git commit" f.py | tee grep.log && git push', True),
        ("KARTA_SKIP_GATE=1 git commit -F- <<'EOF' && git push\nfix: the git commit hatch\nEOF", True),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && bash ./go.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && sh < go.sh', False),
        ('cat <<EOF | bash\ngit commit -m y\nEOF', False),
        # A written file reached under another name (each a real false grant once).
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && bash *.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && bash $(ls *.sh)', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && cat go.sh > run.sh && bash run.sh', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && cat go.sh | bash', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && for s in *.sh; do bash "$s"; done', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh; x=go.sh; bash "$x"', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && cp go.sh go.sh.bak && bash go.sh.bak', False),
        # Writes to a file the parse cannot name, and aliases made before the write.
        ('exec 3>go.sh; KARTA_SKIP_GATE=1 echo "git commit -m y" >&3; bash go.sh', False),
        ('exec 3>go.sh; KARTA_SKIP_GATE=1 echo "git commit -m y" > /dev/fd/3; bash go.sh', False),
        ('ln -s go.sh link; KARTA_SKIP_GATE=1 echo "git commit -m y" > link; bash go.sh', False),
        ('KARTA_SKIP_GATE=1 git commit -m x > /dev/null 2>&1 && git push', True),
        # Review round 7: names are resolved through the line's own assignments,
        # not suspected. An unset environment variable names nothing written here...
        ('KARTA_SKIP_GATE=1 grep -n "git commit" f > hits.txt && uv run tool.py --out "$OUT"', True),
        # ...but a variable the line set does, and so does a matching glob...
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh; x=go.sh; bash "$x"', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && bash *.sh', False),
        # ...and a write whose target cannot be resolved binds every later runner.
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > go.sh && f=out.txt; cat go.sh > $f; bash out.txt', False),
        ('KARTA_SKIP_GATE=1 echo "git commit -m y" > "$UNSET_OUT" && bash out.txt', False),
        # An escaped quote inside a heredoc-body substitution (review round 6).
        ('KARTA_SKIP_GATE=1 git commit -F- <<EOF\n$(echo "\\" )" ; git commit -m y)\nEOF', False),
        ('KARTA_SKIP_GATE=1 git commit -F - <<EOF\n$(echo "a\\"b)"; git commit -m y)\nEOF', False),
        ('cat <<EOF | KARTA_SKIP_GATE=1 bash\ngit commit -m y\nEOF', True),
        # Every commit the line can run must carry it — including one handed to a
        # shell beside a properly prefixed commit (found by running real bash).
        ("sh -c 'git commit -m y' && KARTA_SKIP_GATE=1 git commit -m x", False),
        ('KARTA_SKIP_GATE=10 bash -c "git commit -m y" && KARTA_SKIP_GATE=1 git commit -m x', False),
        ('KARTA_SKIP_GATE=1 git commit -m a && echo "git commit -m b" | bash', False),
        ('KARTA_SKIP_GATE=1 git commit -m a && echo "git commit -m b" | tee go.sh && bash go.sh', False),
        ('echo "git commit -m y" | KARTA_SKIP_GATE=1 bash', True),
        # ...while unrelated later commands stay free.
        ('KARTA_SKIP_GATE=1 git commit -m x && git log --oneline -1', True),
        ('KARTA_SKIP_GATE=1 git commit -m x && git push', True),
        ('KARTA_SKIP_GATE=1 git commit -m x > log && git push', True),
        # KNOWN false denies, pinned so they are decisions rather than surprises;
        # real bash confirms each one runs its commit WITH the hatch set. The
        # gates run; recover with the plain prefix form, or the env route.
        # (KARTA_SKIP_GATE+=1 and bash -c "KARTA_SKIP_GATE=1 …" above are two more.)
        ("KARTA_SKIP_GATE=$'1' git commit -m x", False),
        ('f(){ git commit -m y; }; KARTA_SKIP_GATE=1 f', False),   # prefix on a function call
        ('env -i KARTA_SKIP_GATE=1 git commit -m x', False),   # env options: not modelled, gates run
        ("'KARTA_SKIP_GATE'=1 git commit -m x", False),
        ('KARTA_SKIP_GATE+=1 git commit -m x', False),
        ('KARTA_SKIP_GATE=1 KARTA_SKIP_GATE=0 git commit -m x', False),
        ('bash -c "KARTA_SKIP_GATE=1 git commit -m x"', False),
        # Two commits, one prefixed: the other one is still gated.
        ('KARTA_SKIP_GATE=1 git commit -m a && git commit -m b', False),
        # Unbalanced quoting grants nothing — the shell would not run it.
        ('KARTA_SKIP_GATE=1 git commit -m "unterminated', False),
    ]
    for cmd, want in hatch:
        check(f"hatch {'granted' if want else 'NOT granted'}: {cmd!r}",
              hatch_prefixed(cmd) is want)
    # KNOWN GAPS (grants), pinned: deliberate spellings in the same class as the
    # detector's quoted-word gap. Real bash runs the second commit unprefixed.
    # Nobody types these by accident, which is the threat model; closing them
    # means modelling prompt expansion and brace expansion.
    for cmd in ("KARTA_SKIP_GATE=1 git commit -m a; x='$(git commit -m y)'; echo ${x@P}",
                "KARTA_SKIP_GATE=1 git commit -m a; git {commit,} -m y",
                "KARTA_SKIP_GATE=1 git commit -m a; $'\\x67it' commit -m y"):
        check(f"KNOWN GAP (deliberate spelling, still granted): {cmd!r}", hatch_prefixed(cmd) is True)

    # End to end, with a gate that fails: a mention must reach that gate and be
    # denied. These are the negative controls — every one returned 0 (allowed)
    # under the old `f"{SKIP_VAR}=1" in command` line.
    for cmd in ('git commit -m "bump KARTA_SKIP_GATE=1 later"',
                'KARTA_SKIP_GATE=10 git commit -m x',
                'echo KARTA_SKIP_GATE=1 && git commit -m x',
                'X=KARTA_SKIP_GATE=1 git commit -m x'):
        code, _ = decide(_payload(cmd), {}, failing, stub_gates)
        check(f"a mention of the hatch does not skip a failing gate: {cmd!r}", code == 2)
    # ...while the real prefix, in the shapes it is actually used, never reaches it.
    for cmd in ('KARTA_SKIP_GATE=1 git commit -F msg.txt',
                'make lint && KARTA_SKIP_GATE=1 git commit -m "a; b"',
                'grep -n "git commit" f.py && KARTA_SKIP_GATE=1 git commit -F m.txt'):
        code, _ = decide(_payload(cmd), {}, must_not_run, stub_gates)
        check(f"the real prefix skips without running a gate: {cmd!r}", code == 0)

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
