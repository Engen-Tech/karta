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

The gate keys on deterministic facts — "a fresh recorded review exists for
this exact content" (the staged binder blob, or the integration branch tip),
and, with the `ledger` switch on, "the review rounds behind that record are
committed with it" — never on what any review said. Three detections, all by
git plumbing and command text only — never by parsing a diff, never by
evaluating a post-condition a PreToolUse hook cannot see:

  (a) BINDER-COMMIT gate: a git commit that would record a change to a
      .karta/binders/<slug>.json plan file needs a fresh record whose stored
      hash matches the binder content being committed, and the record itself
      must be in the commit. With `ledger: true` it also needs
      .karta/roundtable/<slug>.rounds.json in the commit, whose last round's
      reviewed_hash equals that same binder hash, and the record must be bound
      to that ledger's final round.
  (b) INTEGRATION-MERGE gate: on the default branch, a git merge naming a
      karta/*/integration ref needs a fresh record for that branch tip in HEAD,
      and with `ledger: true` a branch-<tip>.rounds.json in HEAD whose last
      round reviewed that tip.
  (c) LANDING gate: on the default branch, a git merge naming a
      karta/*/integration ref is blocked outright unless KARTA_LANDING_APPROVED=1
      is present. This one is NOT about a review at all. karta stops at the
      assembled integration branch by design — no PR, no push, no auto-merge —
      so the merge is a separate act, and who decides a delivery ships is
      always the human. Assembling the branch and running the floor are the
      agent's; the decision to land is not.

THE SOURCE GIT WILL COMMIT. Every gated file — binder, record, ledger, and the
config on its first enable — is read from the one source git will actually
record for that path, decided by git itself, never by token matching:
  - `-a`/`--all`, or the path named by the commit's pathspec (as `git ls-files`
    and `git ls-tree HEAD` resolve it) -> the WORKTREE bytes;
  - a pathspec commit that does not name the path -> the HEAD bytes (git keeps
    the committed version whatever the index holds);
  - a plain commit, `--amend`, or `--include` for a path it does not name ->
    the INDEX bytes.
A path missing from its selected source is absent — there is no cross-source
fallback, because a fallback is exactly how a fresh ledger the commit will not
contain gets approved. Modes whose content the hook cannot see from the
command text (`--patch`, `--interactive`, `--pathspec-from-file`) are denied by
name, never guessed.

DENY-BY-DEFAULT GRAMMAR. The hook runs before bash evaluates the command, so
it recognises exactly one shape — `[KARTA_*=1 ...] git commit|merge <options
it knows> <pathspecs it can resolve>` issued from the repository root — and
denies every other shape it cannot reproduce: a preceding or trailing command
segment, a command substitution anywhere, an unquoted expansion character, a
redirection, a relocating `git -C`/`--git-dir`/`--work-tree` or `GIT_*=`
prefix, an option outside the whitelist, a pathspec it cannot resolve
root-relatively. The cost of that posture is over-denial of unusual but valid
spellings, never under-denial. Malformed ledgers, records and configs are
denials with the defect named, never internal errors.

git cherry-pick / rebase / reset --hard, `git update-ref` / `git symbolic-ref`
moving the default branch to an integration tip, a merge that names the tip by
SHA, and `git pull` are accepted, documented bypasses of the same class as the
escape hatch: a PreToolUse hook cannot evaluate "will this make the tip an
ancestor". The ref itself is read in every spelling git accepts for a branch —
`karta/<slug>/integration`, `refs/heads/karta/<slug>/integration`,
`refs/remotes/<remote>/karta/<slug>/integration` and the remote-tracking
shorthand `<remote>/karta/<slug>/integration`.
Detection is anchored at the head of each shell segment (split outside quotes
on `&&`, `||`, `;`, `|`, a newline and a bare `&` — never the `&` of `2>&1` or
`&>`), looking through assignment prefixes, `(`/`{` openers, `!`, redirections
with their targets (`2>&1`, `>log`, `&>log` — git never sees them), and the
executing wrappers time / env / command / builtin / exec / nice / nohup /
timeout / stdbuf, matched by basename (`/usr/bin/env`) — their options
wherever GNU getopt permutes them, before or after the wrapper's own positional
or an env assignment, short clusters char by char (`-iu X`, `exec -ca spoof`),
long options by unambiguous GNU prefix (`--un X`), env's legacy `-` as `-i` —
on dequoted words, and the command word is git when its basename is git
(`/usr/bin/git`, `./git`, `~/bin/git`). Git's optional-argument options
(`-S[key]`, `--gpg-sign[=key]`, `--log[=n]`) carry their value attached only,
so a following word is the ref. `env -a NAME` only renames argv[0]: the program
env runs is still the next word, and it is read. Every gated segment is judged
on its own: a landing is approved only by the prefix on that very segment, and
the skip hatch covers only the segment it prefixes. One env spelling hides the
command from a text reader, `env -S`/`--split-string` (re-splits its string):
that FAILS CLOSED — the review gates deny the segment by the option's name, and
the landing gate denies it whenever the dequoted words mention `git merge` and
an integration ref. What the gate still cannot see, and does not pretend to:
an invocation behind `do`, `xargs`, `sh -c '...'`, `eval`, `sudo`, `su -c`,
`bash <file>`, `coproc`, a shell function or alias, a `$VAR` that expands to
`git`, a history expansion, a `$(...)` or backtick substitution (with the
config on the grammar denies any raw substitution; with it off the text is not
read), or an `env -S` string whose further quoting hides the ref itself — those
read as text, and the review gates' deny-by-default grammar catches only the
ones it is handed. The landing gate shares them. A repository hook
(.git/hooks/pre-commit) is the same channel as an editor and is not something
a text gate can close. All are named rather than papered over.

Config .karta/roundtable.json gates the gates: absent or enabled:false turns
everything off; points.plan_commit / points.deliver_merge toggle each detection;
ledger:true adds the round-ledger conditions. The switch is read from HEAD (the
committed configuration) so a same-commit flip cannot disable the check that
would catch it; only when HEAD has no config at all is it read from the source
the commit will record. Escape hatch: KARTA_SKIP_ROUNDTABLE=1 as an exact
assignment word prefixing the gated invocation, or in the environment,
evaluated before every other rule — never a substring of the command text.

The landing gate (c) is deliberately outside all of that. The config switch does
not disable it and KARTA_SKIP_ROUNDTABLE does not bypass it, because that hatch
means "the review environment is down" — which says nothing about who decides a
delivery ships. Its own variable, KARTA_LANDING_APPROVED=1, exists for the human
to type. An agent that sets it has forged an approval it was never given; a
PreToolUse hook sees only command text and cannot tell the two apart, so the
rule lives in doctrine (CLAUDE.md, AGENTS.md) and the gate makes the moment
impossible to pass through silently.
Internal errors fail OPEN (exit 0) so a broken hook never wedges the repo; a
missing, stale or malformed record / ledger / config is an expected result
that blocks (exit 2), not an internal error.

  python3 roundtable_gate.py < payload.json    # hook mode, exit 0/2
  python3 roundtable_gate.py --self-test        # embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root
HELPER = "scripts/roundtable/run_review.py"
CONFIG_PATH = ".karta/roundtable.json"
RECORD_DIR = ".karta/roundtable/"
BRANCH_PREFIX = "branch-"
LEDGER_SUFFIX = ".rounds.json"
SKIP_VAR = "KARTA_SKIP_ROUNDTABLE"
LAND_VAR = "KARTA_LANDING_APPROVED"  # gate (c); NOT covered by SKIP_VAR
INTEGRATION_GLOB = "karta/*/integration"  # the shape the merge gate matches
GIT_TIMEOUT = 30
FILE_MODES = ("100644", "100755")
# the only assignment prefixes the grammar lets through
ALLOWED_PREFIXES = (f"{SKIP_VAR}=1", f"{LAND_VAR}=1")
# the only GIT_* environment values the hook accepts: inert exact values, never
# a program git would run after the hook (an editor, a pager, an ssh command)
INERT_GIT_ENV = {"GIT_EDITOR": ("true", ":"), "GIT_PAGER": ("cat",), "GIT_TERMINAL_PROMPT": ("0",)}

# `git commit` / `git merge`: word-boundary match where anything between the two
# words must be option tokens (each optionally trailing one non-dash argument),
# matching precommit_gate.py's conservative detection. Applied to the DEQUOTED
# head of a segment, so `"git"`, `\git` and `git''` are all git.
_COMMIT_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+commit\b")
_MERGE_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+merge(?![-\w])")
# an integration ref named anywhere in a merge command: karta/<slug>/integration,
# in its short spelling or the full ones git also accepts — refs/heads/karta/...,
# refs/remotes/<remote>/karta/... and the remote-tracking shorthand
# <remote>/karta/... (`origin/karta/x/integration`, one path component before
# `karta/`, the spelling git resolves to the same refs/remotes ref). The
# shorthand over-matches a local branch that happens to be named
# `<word>/karta/<slug>/integration`; that over-denial is accepted. A raw SHA is
# not a ref spelling and stays on the accepted-bypass list.
_REF_PREFIX = r"(?:refs/heads/|refs/remotes/[^\s/]+/|[^\s/]+/)?"
_INTEGRATION_REF_RE = re.compile(r"\b" + _REF_PREFIX + r"karta/[^\s/]+/integration\b")
_INTEGRATION_REF_FULL_RE = re.compile(r"^" + _REF_PREFIX + r"karta/[^\s/]+/integration$")
# a leading `(` or `{ ` group opener: `(git commit ...)` and `{ git commit ...; }`
# are the same invocation wrapped, and the closer is trailing text
_GROUP_OPEN_RE = re.compile(r"^[({]\s*")
# a shell assignment word, on its dequoted text: NAME=value or NAME+=value
_ASSIGN_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
# an assignment word as GNU env reads it: any name without `=` (`X-Y=1`,
# `1X=1`, `a.b=1` are all set, and the command after them runs). Applied only
# to a word that is not an option (does not start with `-`) or that follows
# `--`, so env's own options are still read as options.
_ENV_ASSIGN_RE = re.compile(r"^[^=\s]+=")
_MODE_RE = re.compile(r"^[0-7]{6}$")
# executing wrappers the anchored matcher looks through, keyed on the command
# word's basename (`/usr/bin/env`, `/usr/bin/timeout` are the same programs):
# (the short option letters that take a value — attached or as the next word,
# read char by char through a cluster, so `-iu X` unsets X and `-ca spoof` is
# exec's -c plus -a's value; the long options that take a value; the long
# options that take none, both matched on any unambiguous GNU prefix; and how
# many positional words precede the command). Any other short letter is a flag;
# an unknown long option is read as a flag. An optional-argument long option
# (`--default-signal[=SIG]`) only ever carries its value attached, so it is a
# flag here.
_WRAPPERS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], int]] = {
    "time": ("of", ("--output", "--format"), ("--append", "--portability", "--verbose", "--quiet"), 0),
    "command": ("", (), (), 0), "builtin": ("", (), (), 0), "exec": ("a", (), (), 0),
    "nohup": ("", (), ("--help", "--version"), 0),
    "nice": ("n", ("--adjustment",), ("--help", "--version"), 0),
    "timeout": ("ks", ("--kill-after", "--signal"), ("--foreground", "--preserve-status", "--verbose", "--help", "--version"), 1),
    "stdbuf": ("ioe", ("--input", "--output", "--error"), ("--help", "--version"), 0),
    "env": ("uCSa", ("--unset", "--chdir", "--split-string", "--argv0"),
            ("--ignore-environment", "--null", "--debug", "--default-signal", "--ignore-signal", "--block-signal",
             "--list-signal-handling", "--help", "--version"), 0),
}
# the one env option after which the command is no longer readable from the
# text: -S re-splits its string by env's own rules. (-a/--argv0 only renames
# argv[0]; the program env runs is still the next word, so it is read through.)
_ENV_HIDING_SHORT = {"S": "-S"}
_ENV_HIDING_LONG = ("--split-string",)

# commit options the hook knows. Anything else is denied by name: a skipped
# value-bearing option would leave its argument to be read as a pathspec.
COMMIT_VALUE_OPTS = ("-m", "--message", "-F", "--file", "--author", "--date", "--trailer")
COMMIT_FLAG_OPTS = ("-a", "--all", "-i", "--include", "-o", "--only", "--amend", "--no-edit",
                    "-q", "--quiet", "-v", "--verbose", "-s", "--signoff", "--no-verify", "-n",
                    "--allow-empty", "--allow-empty-message",  # stage nothing new: a plain commit
                    "-S", "--gpg-sign", "--no-gpg-sign")
COMMIT_DENIED_MODES = ("-p", "--patch", "--interactive", "--pathspec-from-file", "--pathspec-file-nul")
MERGE_FLAG_OPTS = ("--no-ff", "--ff-only", "--no-edit", "-S", "--gpg-sign", "--no-gpg-sign")
MERGE_VALUE_OPTS = ("-m",)
# git's optional-argument options: the value is only ever ATTACHED (`-Skey`,
# `--gpg-sign=key`); a separate next word is a pathspec or the ref. So the bare
# spelling is a flag, and the attached spelling is the same flag with a value.
OPTIONAL_ATTACHED_LONG = ("--gpg-sign",)
OPTIONAL_ATTACHED_SHORT = ("-S",)


def _optional_attached(t: str) -> bool:
    """Whether `t` is an optional-argument option carrying its value attached."""
    name = t.partition("=")[0]
    return (name in OPTIONAL_ATTACHED_LONG and "=" in t) or (
        not t.startswith("--") and len(t) > 2 and t[:2] in OPTIONAL_ATTACHED_SHORT)


class Denial(Exception):
    """An expected blocking result with its reason. Raised inside decide() and
    turned into (2, message) there — never reaches hook_main's fail-open handler."""


def _segments(command: str, bare_amp: bool = False) -> list[str]:
    """Split a command into shell segments the way bash would: on `&&`, `||`,
    `;`, `|` and newlines OUTSIDE quotes, never inside them — a `;` in a merge
    message does not end the merge. A backslash-newline is a line continuation
    and is collapsed first. With bare_amp the split also happens on a bare `&`,
    so a backgrounded preceding command still exposes the git invocation at a
    segment head; every gate uses it, and because the split is quote-aware a
    `&` inside a quoted merge message never splits the ref away from the
    invocation. An unbalanced quote swallows the rest of the text (bash would
    refuse the line)."""
    text = command.replace("\\\n", "")
    out: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    in_single = in_double = False
    # whether the previous character was an UNQUOTED, UNESCAPED `>` or `<` —
    # only that is a redirection operator a following `&` / `|` belongs to;
    # `\>&`, `'>'&` and `">"|` are a literal argument and then a real operator
    prev_op = False
    while i < n:
        c = text[i]
        if in_single:
            if c == "'":
                in_single = False
        elif in_double:
            if c == '"':
                in_double = False
            elif c == "\\" and i + 1 < n:
                cur.append(c); i += 1; c = text[i]
        elif c == "\\" and i + 1 < n:
            cur.append(c); i += 1; c = text[i]
        elif c == "'":
            in_single = True
        elif c == '"':
            in_double = True
        elif c == "\n" or c == ";":
            out.append("".join(cur)); cur = []; prev_op = False; i += 1; continue
        elif c in "&|":
            nxt = text[i + 1] if i + 1 < n else ""
            if (c == "&" and (prev_op or nxt == ">")) or (c == "|" and prev_op):
                # the & of 2>&1 / >&f / <&0 / &>log, the | of >|: a redirection, not an operator
                cur.append(c); prev_op = False; i += 1; continue
            if nxt == c:
                i += 1
            elif c == "&" and not bare_amp:
                cur.append(c); prev_op = False; i += 1; continue
            out.append("".join(cur)); cur = []; prev_op = False; i += 1; continue
        else:
            cur.append(c); prev_op = c in "<>"; i += 1; continue
        cur.append(c)
        prev_op = False
        i += 1
    out.append("".join(cur))
    return out


class _Word:
    __slots__ = ("text", "unquoted", "redirect")

    def __init__(self) -> None:
        self.text = ""      # the word after quote removal
        self.unquoted = ""  # the characters that sat outside any quoting
        self.redirect = 0   # a redirection operator: 1 = its target is the next word, 2 = self-contained


_REDIRECT_RE = re.compile(r"(?:&>>?|<<<|<<|<>|<&|>&|>\||>>|<|>)")


def _loose_words(segment: str) -> list[_Word]:
    """shlex-style word split with quote removal, lenient where tokenize() is
    strict: no denials, an unbalanced quote runs to the end, and control
    characters are ordinary text. An unquoted redirection operator (`>`, `>>`,
    `<`, `2>`, `2>&1`, `&>`, `&>>`, `<&`, `>&`, `>|`, `<<`, `<>`) is its own
    word, marked, with a leading unquoted digit string (`2>`) folded into it;
    `>&1` / `<&0` / `>&-` carry their target attached and are self-contained,
    every other operator's target is the word that follows, attached or not.
    Only for the anchored DETECTION of a gated invocation; the deny-by-default
    grammar re-lexes with tokenize(), which denies every redirection."""
    words: list[_Word] = []
    cur: _Word | None = None
    i, n = 0, len(segment)
    in_single = in_double = False
    while i < n:
        c = segment[i]
        if in_single:
            if c == "'":
                in_single = False
            else:
                cur.text += c
        elif in_double:
            if c == '"':
                in_double = False
            elif c == "\\" and i + 1 < n and segment[i + 1] in '"\\$`':
                i += 1; cur.text += segment[i]
            else:
                cur.text += c
        elif c in " \t":
            if cur is not None:
                words.append(cur); cur = None
        elif c in "<>" or (c == "&" and i + 1 < n and segment[i + 1] == ">"):
            op = _REDIRECT_RE.match(segment, i).group(0)
            if cur is not None and not (cur.text.isdigit() and cur.unquoted == cur.text and c != "&"):
                words.append(cur); cur = None  # the fd digits stay with the operator
            if cur is None:
                cur = _Word()
            cur.text += op; cur.unquoted += op
            cur.redirect = 1
            i += len(op)
            if op.endswith("&"):
                j = i
                while j < n and segment[j].isdigit():
                    j += 1
                if j == i and i < n and segment[i] == "-":
                    j += 1
                if j > i:
                    cur.text += segment[i:j]; cur.unquoted += segment[i:j]
                    cur.redirect = 2
                    i = j
            words.append(cur); cur = None
            continue
        else:
            if cur is None:
                cur = _Word()
            if c == "'":
                in_single = True
            elif c == '"':
                in_double = True
            elif c == "\\" and i + 1 < n:
                i += 1; cur.text += segment[i]
            else:
                cur.text += c; cur.unquoted += c
        i += 1
    if cur is not None:
        words.append(cur)
    return words


def _strip_redirections(words: list[_Word]) -> list[_Word]:
    """The words with every redirection operator and its target dropped: git
    never sees `2>&1` or `>log`, so neither does the ref detection."""
    out: list[_Word] = []
    skip = False
    for w in words:
        if skip:
            skip = False; continue
        if w.redirect:
            skip = w.redirect == 1
            continue
        out.append(w)
    return out


def _long_option(name: str, known: tuple[str, ...]) -> str | None:
    """The long option `name` (`--` and the name, no value) spells, by exact
    match or any unambiguous GNU prefix of the wrapper's `known` list; None
    when it is unknown or ambiguous."""
    if name in known:
        return name
    hits = [k for k in known if k.startswith(name)]
    return hits[0] if len(hits) == 1 else None


def _leading_command(segment: str) -> tuple[list[_Word], list[str], tuple[str, str] | None]:
    """(the assignment words that prefix the invocation, the dequoted head
    words of the segment with everything that merely wraps the invocation
    stripped, and the hiding env option with the dequoted words after `env` —
    or None):
    `(` / `{` group openers, VAR=value and VAR+=value assignments (quoted values
    and backslash-escaped spaces included), a `!` negation, redirections with
    their targets (git never sees `2>&1` or `>log`), and the executing wrappers
    in _WRAPPERS — `time`, `env` with its own assignments, `command`, `builtin`,
    `exec`, `nice -n N`, `nohup`, `timeout <dur>`, `stdbuf <opts>` — matched on
    the wrapper word's basename. A wrapper's options are read wherever GNU
    getopt permutes them — before or after its positional (`timeout 5 -s KILL
    git ...`), after an env assignment (`env F=1 -i git ...`) — short clusters
    char by char with the valued letter taking the rest of the cluster or the
    next word (`-iu X`, `-ca spoof`), long options by exact name or unambiguous
    GNU prefix (`--un X`), env's legacy `-` as `-i`, and a bare `--` ends them,
    so the command follows. Each of those runs the same git the shell would;
    none of them is a place for an invocation to hide. `env -S` is the
    exception: env itself re-splits what it runs, so the head is empty and the
    third slot names the option for the caller to deny on. The command word is
    matched on its dequoted text, so `"git"`, `\\git` and `git''` are all git,
    and by its basename, so `/usr/bin/git` is too."""
    seg = segment.strip()
    while True:
        m = _GROUP_OPEN_RE.match(seg)
        if not m:
            break
        seg = seg[m.end():]
    words = _strip_redirections(_loose_words(seg))
    assigns: list[_Word] = []
    i, n = 0, len(words)
    while i < n:
        w = words[i]
        if _ASSIGN_TOKEN_RE.match(w.text):
            assigns.append(w); i += 1; continue
        if w.text == "!":
            i += 1; continue
        name = os.path.basename(w.text) if w.text else ""
        spec = _WRAPPERS.get(name)
        if spec is None:
            break
        is_env = name == "env"
        short_valued, long_valued, long_flags, remaining = spec
        i += 1
        after_env = i
        after_dashdash = False
        while i < n:
            t = words[i].text
            if is_env and (after_dashdash or not t.startswith("-")) and _ENV_ASSIGN_RE.match(t):
                assigns.append(words[i]); i += 1; continue  # env's own assignments; the option scan resumes
            if after_dashdash:
                break
            if t == "--":
                after_dashdash = True; i += 1; continue
            if t == "-" and is_env:
                i += 1; continue  # the legacy spelling of -i
            if t.startswith("--"):
                lname, eq, _ = t.partition("=")
                long = _long_option(lname, long_valued + long_flags)
                if is_env and long in _ENV_HIDING_LONG:
                    return assigns, [], (long, " ".join(x.text for x in words[after_env:]))
                i += 1
                if long in long_valued and not eq and i < n:
                    i += 1  # the value is the next word
                continue
            if t.startswith("-") and t != "-":
                i += 1
                if name == "nice" and t[1:].isdigit():
                    continue  # nice -5: the legacy adjustment
                k = 1
                while k < len(t):
                    ch = t[k]
                    if is_env and ch in _ENV_HIDING_SHORT:
                        return assigns, [], (_ENV_HIDING_SHORT[ch], " ".join(x.text for x in words[after_env:]))
                    if ch in short_valued:
                        if k + 1 >= len(t) and i < n:
                            i += 1  # the value is the next word
                        break  # attached: the rest of the cluster is the value
                    k += 1
                continue
            if remaining > 0:
                remaining -= 1; i += 1; continue
            break
    if i < n and words[i].text != "git" and os.path.basename(words[i].text) == "git":
        words[i].text = "git"
    if words and words[-1].unquoted.endswith(")") and words[-1].text.endswith(")"):
        # the closer of a `(...)` group rides on the last word; it is not part of it
        words[-1].text = words[-1].text[:-1]
    return assigns, [w.text for w in words[i:]], None


def _head(segment: str) -> str:
    """The dequoted head of a segment as one string, for the anchored regexes."""
    return " ".join(_leading_command(segment)[1])


def _exact_flag(assigns: list[_Word], name: str) -> bool:
    """Whether the prefix carries NAME=1 as its own assignment word: the name
    and the `=` unquoted (a quoted name is a command word to bash, not an
    assignment), the value exactly `1` — bare, '1' or "1". A substring is not
    an assignment: X=NAME=1, NAME=10 and FOO="NAME=1" all read as something else."""
    return any(w.unquoted.startswith(f"{name}=") and w.text == f"{name}=1" for w in assigns)


def is_commit_command(command: str) -> bool:
    """A segment that STARTS with `git ... commit` (after any VAR=value prefix
    or executing wrapper). Anchored, not searched: a commit command quoted
    inside an echo or a grep is text, not an invocation."""
    return any(_COMMIT_RE.match(_head(seg)) for seg in _segments(command, bare_amp=True))


def is_merge_command(command: str) -> bool:
    return any(_MERGE_RE.match(_head(seg)) for seg in _segments(command, bare_amp=True))


def hidden_invocation(command: str) -> str | None:
    """The env option that hides a segment's command from the hook (`-S` or
    `--split-string`), or None. The hook cannot read what env will run after
    re-splitting its string, so such a segment is treated as a gated invocation
    and denied by name — never silently allowed."""
    for seg in _segments(command, bare_amp=True):
        _, _, hidden = _leading_command(seg)
        if hidden:
            return hidden[0]
    return None


def _gated_segments(command: str) -> list[tuple[list[_Word], list[str], tuple[str, str] | None]]:
    """Every segment that is a gated invocation — a commit, a merge, or an env
    segment whose command is hidden — as (assigns, head words, hidden)."""
    out = []
    for seg in _segments(command, bare_amp=True):
        assigns, words, hidden = _leading_command(seg)
        head = " ".join(words)
        if hidden or _COMMIT_RE.match(head) or _MERGE_RE.match(head):
            out.append((assigns, words, hidden))
    return out


def has_skip_prefix(command: str) -> bool:
    """Whether EVERY gated invocation in the command is prefixed with SKIP_VAR=1
    as an exact assignment word. Not a substring match over the whole text: a
    commit message that quotes the hatch does not invoke it, an assignment on a
    different segment is a different command's environment, and a hatch on one
    of two gated segments leaves the other one gated."""
    gated = _gated_segments(command)
    return bool(gated) and all(_exact_flag(assigns, SKIP_VAR) for assigns, _, _ in gated)


# merge options that take no value, beyond the whitelist the grammar accepts:
# what the landing gate needs in order to find the positional ref
_MERGE_NOVALUE_OPTS = frozenset((
    "--no-ff", "--ff", "--ff-only", "--no-edit", "-e", "--edit", "--squash", "--no-squash", "--commit",
    "--no-commit", "-q", "--quiet", "-v", "--verbose", "-n", "--no-verify", "--verify", "--stat", "--no-stat",
    "--log", "--no-log", "--signoff", "--no-signoff", "--progress", "--no-progress", "--autostash",
    "--no-autostash", "--allow-unrelated-histories", "--rerere-autoupdate", "--no-rerere-autoupdate",
    "--overwrite-ignore", "--no-overwrite-ignore", "--verify-signatures", "--no-verify-signatures",
    "--no-gpg-sign", "--summary", "--no-summary", "--abort", "--continue", "--quit",
    # optional-argument options: the value is only ever attached (`--gpg-sign=key`, `--log=n`),
    # so the bare spelling takes no value and the next word is the ref
    "--gpg-sign"))
_MERGE_VALUED_SHORT = ("-m", "-F", "-s", "-X")
_MERGE_VALUED_LONG = ("--message", "--file", "--strategy", "--strategy-option", "--cleanup", "--into-name")
_UNKNOWN = "?"


def _merge_positional_ref(words: list[str]) -> str | None:
    """The single positional ref the dequoted `git ... merge` head words hand
    git — what parse_merge yields — or None when there is none, or _UNKNOWN
    when an option shape the hook does not know makes the positional
    undecidable. A ref mentioned only inside a message value is not positional:
    git does not merge it."""
    try:
        i = words.index("merge") + 1
    except ValueError:
        return None
    ref: str | None = None
    after_dashdash = False
    n = len(words)
    while i < n:
        t = words[i]
        if after_dashdash or not t.startswith("-"):
            if ref is not None:
                return _UNKNOWN  # more than one positional: not a shape the gate reads
            ref = t
            i += 1
            continue
        if t == "--":
            after_dashdash = True; i += 1; continue
        name, eq, _ = t.partition("=")
        if eq and name.startswith("--"):
            i += 1; continue  # an inline long value
        if t in _MERGE_NOVALUE_OPTS:
            i += 1; continue
        if t in _MERGE_VALUED_SHORT or t in _MERGE_VALUED_LONG:
            i += 2; continue
        if t.startswith("-S") and not t.startswith("--"):
            i += 1; continue  # -S[keyid]: the key id is attached or absent
        if len(t) > 2 and not t.startswith("--") and t[:2] in _MERGE_VALUED_SHORT:
            i += 1; continue  # -mtext, -sours: the value is attached
        return _UNKNOWN
    return ref


def _integration_ref_in(words: list[str]) -> str | None:
    """The integration ref the dequoted `git merge` head words merge: the
    positional ref only, so a ref quoted inside a message never arms a gate.
    When the positional cannot be determined (an option shape the hook does not
    know) and an integration ref is mentioned anywhere, that mention is
    returned — fail closed, never silent."""
    ref = _merge_positional_ref(words)
    if ref == _UNKNOWN:
        m = _INTEGRATION_REF_RE.search(" ".join(words))
        return m.group(0) if m else None
    if ref is not None and _INTEGRATION_REF_FULL_RE.match(ref):
        return ref
    return None


def merge_invocations(command: str) -> list[tuple[str, bool]]:
    """Every segment that merges a karta/<slug>/integration ref (short,
    refs/heads / refs/remotes, or <remote>/karta/... shorthand — the last also
    matches an oddly named local branch `<word>/karta/<slug>/integration`,
    an accepted over-denial), as (the ref, whether LAND_VAR=1
    prefixes that same segment). A segment whose command env -S hides counts
    when its dequoted words mention `git merge` and an integration ref: the
    hook cannot read what env runs, so it fails closed, and only the approval
    prefix on that segment lets it through."""
    out: list[tuple[str, bool]] = []
    for seg in _segments(command, bare_amp=True):
        assigns, words, hidden = _leading_command(seg)
        if hidden:
            _, rest = hidden
            m = _INTEGRATION_REF_RE.search(rest)
            if m and _MERGE_RE.search(rest):
                out.append((m.group(0), _exact_flag(assigns, LAND_VAR)))
            continue
        if not _MERGE_RE.match(" ".join(words)):
            continue
        ref = _integration_ref_in(words)
        if ref:
            out.append((ref, _exact_flag(assigns, LAND_VAR)))
    return out


def merge_invocation(command: str) -> tuple[str, bool] | None:
    """The first landing in the command, or None — see merge_invocations, which
    decide() uses so that every landing segment answers for itself.

    Two things are deliberately narrower here than in the other two gates.

    Anchored, not searched. A merge command written inside a heredoc, echoed
    into a file, or handed to grep is text, not an invocation, and blocking it
    would stop ordinary work — editing this very file included. Anchoring at
    the head of the segment (after any VAR=value prefix or executing wrapper)
    tells the two apart. The trade is named rather than hidden: an invocation
    buried mid-segment, behind a `do` or an `xargs`, reads as text and is not
    caught — the same class as the cherry-pick / rebase / reset --hard bypasses
    already documented here. It errs toward letting real work through rather
    than toward blocking prose about a merge.

    Approval must prefix the merge itself, as an exact assignment word. The skip
    hatch only ever loosens a review requirement; this one grants authority, so
    an accidental grant is worse than an accidental block: the assignment has to
    sit in front of the invocation it approves, spelled NAME=1 and nothing else
    — not X=NAME=1, not NAME=10, not FOO="NAME=1", and not merely somewhere in
    the same command line."""
    found = merge_invocations(command)
    return found[0] if found else None


def merged_integration_ref(command: str) -> str | None:
    """The karta/<slug>/integration ref a `git merge` segment merges, or None.
    Read on the dequoted words, so `integr""ation` is the ref git sees."""
    for seg in _segments(command, bare_amp=True):
        _, words, _ = _leading_command(seg)
        if not _MERGE_RE.match(" ".join(words)):
            continue
        ref = _integration_ref_in(words)
        if ref:
            return ref
    return None


def slug_of(binder_path: str) -> str:
    return Path(binder_path).stem


# --- command grammar ------------------------------------------------------------

class Tok:
    __slots__ = ("text", "unquoted", "quoted")

    def __init__(self) -> None:
        self.text = ""      # the word as git would receive it
        self.unquoted = ""  # the characters that sat outside any quoting
        self.quoted = False

    def __repr__(self) -> str:
        return f"Tok({self.text!r})"


def tokenize(command: str) -> list[Tok]:
    """Split a command the way bash would, keeping which characters were quoted.
    The standard library's shlex splits the same words but cannot report which
    characters sat inside quotes, and the unquoted-expansion rule needs exactly
    that, so this is a small quote-tracking lexer with shlex's word rules.
    Raises Denial for anything that can execute or precede the git invocation:
    a control operator or redirection outside quotes, a command substitution
    or backtick anywhere, an unbalanced quote (the standard library's shlex
    would also refuse it; the reason is named here instead)."""
    if "$(" in command or "`" in command:
        raise Denial("command substitution or backtick in the command — it would run between "
                     "validation and the commit")
    toks: list[Tok] = []
    cur: Tok | None = None
    i, n = 0, len(command)
    in_single = in_double = False
    while i < n:
        c = command[i]
        if in_single:
            if c == "'":
                in_single = False
            else:
                cur.text += c
            i += 1
            continue
        if in_double:
            if c == '"':
                in_double = False
            elif c == "\\" and i + 1 < n and command[i + 1] in '"\\$`':
                cur.text += command[i + 1]
                i += 1
            else:
                cur.text += c
            i += 1
            continue
        if c in " \t":
            if cur is not None:
                toks.append(cur)
                cur = None
            i += 1
            continue
        if c in "\n;|&<>()":
            raise Denial(f"shell control operator or redirection {c!r} outside quotes — the command "
                         "must be exactly one git invocation with nothing before, after or around it")
        if cur is None:
            cur = Tok()
        if c == "'":
            in_single = True
            cur.quoted = True
        elif c == '"':
            in_double = True
            cur.quoted = True
        elif c == "\\" and i + 1 < n:
            cur.text += command[i + 1]
            cur.quoted = True
            i += 1
        else:
            cur.text += c
            cur.unquoted += c
        i += 1
    if in_single or in_double:
        raise Denial("command could not be parsed (unbalanced quote)")
    if cur is not None:
        toks.append(cur)
    return toks


def _check_inert(tok: Tok, role: str) -> None:
    """An unquoted expansion character is expanded by bash before git runs, so
    the token git receives is not the token the hook classified. Denied."""
    if any(ch in tok.unquoted for ch in "$*?[]{}"):
        raise Denial(f"unquoted expansion character in {role} {tok.text!r} — quote it")
    if tok.unquoted.startswith("~"):
        raise Denial(f"unquoted tilde in {role} {tok.text!r} — bash expands it before git runs")


def _check_pathspec(tok: Tok) -> None:
    _check_inert(tok, "pathspec")
    if "$" in tok.text:
        raise Denial(f"a variable in pathspec position {tok.text!r} cannot be resolved by the hook")
    if tok.text.startswith("/"):
        raise Denial(f"absolute pathspec {tok.text!r} — the hook resolves paths from the repository root")
    body = tok.text.split(")", 1)[-1] if tok.text.startswith(":(") else (tok.text[2:] if tok.text.startswith(":!") else tok.text)
    if any(part == ".." for part in body.split("/")):
        raise Denial(f"pathspec {tok.text!r} leaves the repository root")


class CommitSpec:
    def __init__(self) -> None:
        self.all = self.include = self.only = self.amend = self.no_edit = False
        self.pathspecs: list[str] = []
        self.messages: list[str] = []
        self.message_files: list[str] = []


def parse_invocation(command: str) -> tuple[list[str], list[Tok]]:
    """(assignment prefixes, the git words). Denies every prefix but the two
    KARTA_* ones and any invocation that is not a plain `git <subcommand>`."""
    toks = tokenize(command)
    prefixes: list[str] = []
    while toks and _ASSIGN_TOKEN_RE.match(toks[0].unquoted):
        prefixes.append(toks.pop(0).text)
    for p in prefixes:
        if p not in ALLOWED_PREFIXES:
            raise Denial(f"assignment prefix {p!r} is not allowed — only {SKIP_VAR}=1 and "
                         f"{LAND_VAR}=1 may prefix a gated git command")
    if not toks or os.path.basename(toks[0].text) != "git" or toks[0].quoted:
        raise Denial("the command must begin with a plain `git` invocation")
    if len(toks) < 2 or toks[1].text not in ("commit", "merge") or toks[1].quoted:
        raise Denial(f"a git option before the subcommand ({toks[1].text if len(toks) > 1 else ''!r}) "
                     "relocates the repository or the pathspec base and is denied")
    return prefixes, toks


def parse_commit(words: list[Tok]) -> CommitSpec:
    """Whitelist parse of the tokens after `git commit`."""
    spec = CommitSpec()
    i, n = 0, len(words)
    after_dashdash = False
    while i < n:
        tok = words[i]
        t = tok.text
        # option-vs-pathspec is decided on the text git receives: bash quoting is
        # invisible to git, so `"-m"` is the -m option, not a pathspec
        if after_dashdash or not t.startswith("-"):
            _check_pathspec(tok)
            spec.pathspecs.append(t)
            i += 1
            continue
        if t == "--":
            after_dashdash = True
            i += 1
            continue
        _check_inert(tok, "option")
        name, eq, inline = t.partition("=")
        if name in COMMIT_DENIED_MODES:
            raise Denial(f"commit mode {name} chooses content the hook cannot see from the command text")
        if name in COMMIT_VALUE_OPTS:
            if eq:
                value = inline
            else:
                if i + 1 >= n:
                    raise Denial(f"option {name} is missing its value")
                i += 1
                _check_inert(words[i], f"the value of {name}")
                value = words[i].text
            if name in ("-m", "--message"):
                spec.messages.append(value)
            elif name in ("-F", "--file"):
                spec.message_files.append(value)
            i += 1
            continue
        if _optional_attached(t):
            i += 1
            continue  # -Skey / --gpg-sign=key: the value rides attached, the next word is a pathspec
        if eq or t not in COMMIT_FLAG_OPTS:
            raise Denial(f"commit option {t!r} is not one the hook recognises")
        if t in ("-a", "--all"):
            spec.all = True
        elif t in ("-i", "--include"):
            spec.include = True
        elif t in ("-o", "--only"):
            spec.only = True
        elif t == "--amend":
            spec.amend = True
        elif t == "--no-edit":
            spec.no_edit = True
        i += 1
    return spec


class MergeSpec:
    def __init__(self) -> None:
        self.no_ff = self.ff_only = self.no_edit = False
        self.messages: list[str] = []
        self.ref: str | None = None


def parse_merge(words: list[Tok]) -> MergeSpec:
    spec = MergeSpec()
    i, n = 0, len(words)
    while i < n:
        tok = words[i]
        t = tok.text
        _check_inert(tok, "merge argument")
        if t.startswith("-"):
            if t in MERGE_VALUE_OPTS:
                if i + 1 >= n:
                    raise Denial(f"option {t} is missing its value")
                i += 1
                _check_inert(words[i], f"the value of {t}")
                spec.messages.append(words[i].text)
            elif t == "--no-ff":
                spec.no_ff = True
            elif t == "--ff-only":
                spec.ff_only = True
            elif t == "--no-edit":
                spec.no_edit = True
            elif t in MERGE_FLAG_OPTS or _optional_attached(t):
                pass  # -S / --gpg-sign[=key]: signing, with any key id attached — the next word is the ref
            else:
                raise Denial(f"merge option {t!r} is not one the hook recognises")
        else:
            if spec.ref is not None:
                raise Denial("a merge naming more than one ref cannot be gated")
            spec.ref = t
        i += 1
    return spec


# --- git / helper seams (injected so --self-test needs no real repo) ----------

def _real_git(argv: list[str], input_bytes: bytes | None = None) -> tuple[int, bytes]:
    """(exit, stdout) — on a non-zero exit the stderr text rides in the second
    slot instead, so a caller can tell git's canonical missing-path error from
    any other failure."""
    try:
        proc = subprocess.run(["git", *argv], cwd=ROOT, timeout=GIT_TIMEOUT,
                              input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            return proc.returncode, proc.stderr or b""
        return 0, proc.stdout or b""
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


def _real_is_symlink(path: str) -> bool:
    return os.path.islink(ROOT / path)


def _lines(out: bytes) -> list[str]:
    return [ln.strip() for ln in out.decode(errors="replace").splitlines() if ln.strip()]


def _binder_paths(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        if ln.startswith(".karta/binders/") and ln.endswith(".json") and "/archive/" not in ln:
            out.append(ln)
    return out


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


# --- the commit-source resolver -----------------------------------------------

class Sources:
    """What git will commit for each path under one commit command. One resolver
    serves the binder, the record, the ledger and the first-enable config."""

    def __init__(self, spec: CommitSpec, git, read_file, is_symlink) -> None:
        self.spec, self.git, self.read_file, self.is_symlink = spec, git, read_file, is_symlink
        self.shows = 0
        code, out = git(["diff", "--cached", "--name-only"])
        self.staged = set(_lines(out)) if code == 0 else set()
        self._named: set[str] | None = None

    def named_by_pathspec(self) -> set[str]:
        """The tracked paths git matches for the commit's pathspec — consulting
        both the index and HEAD, so a path deleted from the index but known in
        HEAD is still 'named' (and then absent from the worktree, so denied)."""
        if self._named is None:
            named: set[str] = set()
            specs = self.spec.pathspecs
            if specs:
                code, out = self.git(["ls-files", "--", *specs])
                if code == 0:
                    named |= set(_lines(out))
                code, out = self.git(["ls-tree", "-r", "--name-only", "HEAD", "--", *specs])
                if code == 0:
                    named |= set(_lines(out))
            self._named = named
        return self._named

    def tracked(self, path: str) -> bool:
        if path in self.staged:
            return True
        code, out = self.git(["ls-files", "--", path])
        return code == 0 and path in _lines(out)

    def source(self, path: str) -> str:
        """'worktree' | 'index' | 'HEAD' | 'absent' — decided the way git decides it."""
        spec = self.spec
        if spec.all:
            return "worktree" if self.tracked(path) else "absent"
        if spec.pathspecs:
            if path in self.named_by_pathspec():
                return "worktree"
            return "index" if spec.include else "HEAD"
        return "index"

    def _mode_ok(self, path: str, src: str) -> None:
        if src == "index":
            code, out = self.git(["ls-files", "-s", "--", path])
        else:
            code, out = self.git(["ls-tree", "HEAD", "--", path])
        if code != 0:
            return
        for ln in _lines(out):
            fields = ln.split()
            if len(fields) >= 3 and _MODE_RE.match(fields[0]) and ln.endswith(path):
                if fields[0] == "120000":
                    raise Denial(f"gated path {path} is a symlink in the {src} — git commits the link, "
                                 "not its target's bytes")
                if fields[0] not in FILE_MODES:
                    raise Denial(f"gated path {path} is not a regular file in the {src} (mode {fields[0]})")

    def read(self, path: str) -> tuple[str, bytes | None]:
        """(source, bytes-or-None). A symlink or a non-file object is a Denial."""
        src = self.source(path)
        if src == "absent":
            return src, None
        if src == "worktree":
            if self.is_symlink(path):
                raise Denial(f"gated path {path} is a symlink — git commits the link, not its target's bytes")
            return src, self.read_file(path)
        self._mode_ok(path, src)
        self.shows += 1
        code, out = self.git(["show", f"{'HEAD:' if src == 'HEAD' else ':'}{path}"])
        return src, (out if code == 0 else None)

    def binders(self) -> list[str]:
        """The binder plan files this commit would record. A pathspec commit
        records only the paths it names (git leaves the rest of the index
        where it is), so only those are gated; with --include the staged ones
        come along too. A plain commit records the staged ones; -a also the
        ones changed only in the worktree. A binder staged or edited but not in
        the commit is not gated by it."""
        paths: set[str] = set()
        if self.spec.pathspecs:
            paths |= set(_binder_paths(sorted(self.named_by_pathspec())))
            if not self.spec.include:
                return sorted(paths)
        paths |= set(_binder_paths(sorted(self.staged)))
        if self.spec.all:
            code, out = self.git(["diff", "--name-only"])
            if code == 0:
                paths |= set(_binder_paths(_lines(out)))
        return sorted(paths)


def commit_source(path: str, command: str, git, read_file=_real_read, is_symlink=_real_is_symlink) -> str:
    """Standalone form of the resolver: 'worktree' | 'index' | 'HEAD' | 'absent'
    | 'deny:<reason>' for the path under this commit command."""
    try:
        _, words = parse_invocation(command)
        spec = parse_commit(words[2:])
        return Sources(spec, git, read_file, is_symlink).source(path)
    except Denial as d:
        return f"deny:{d}"


# --- content validation (total: every bad shape is a named denial) --------------

def _parse_json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Denial  # sentinel: not JSON


def record_shaped(data: bytes | None) -> dict | None:
    """The parsed record when it is one — an object with a non-empty string
    reviewed_hash — else None. A file at the record path that is not record-
    shaped is NO record."""
    if data is None:
        return None
    obj = _parse_json(data)
    if not isinstance(obj, dict) or not isinstance(obj.get("reviewed_hash"), str) or not obj["reviewed_hash"]:
        return None
    return obj


def validate_ledger(data: bytes, path: str) -> tuple[int, str]:
    """(round count, last round's reviewed_hash). Any other shape is a Denial
    naming the defect — never an exception the hook would fail open on."""
    obj = _parse_json(data)
    if obj is Denial:
        raise Denial(_ledger_deny(path, "is not valid JSON"))
    if not isinstance(obj, dict):
        raise Denial(_ledger_deny(path, "is not a JSON object"))
    rounds = obj.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise Denial(_ledger_deny(path, "has no non-empty `rounds` list"))
    last = rounds[-1]
    if not isinstance(last, dict):
        raise Denial(_ledger_deny(path, "has a last round that is not an object"))
    h = last.get("reviewed_hash")
    if not isinstance(h, str) or not h:
        raise Denial(_ledger_deny(path, "has a last round without a non-empty string `reviewed_hash`"))
    return len(rounds), h


def _ledger_cmd(target: str, kind: str) -> str:
    return (f"<run the roundtable panel, then> ... | python3 scripts/roundtable/run_review.py --round "
            f"--target {target} --kind {kind}   # once per round; then --record on the final one")


def _ledger_deny(path: str, defect: str) -> str:
    return (f"the round ledger {path} {defect}. Every review round is kept in the {LEDGER_SUFFIX} ledger "
            f"beside the record; append each round with run_review.py --round, then --record the final one.")


def _ledger_absent(what: str, path: str, target: str, kind: str) -> str:
    return (f"{what} blocked: no round ledger {path} is in the content being recorded — the review "
            f"rounds behind the record must be committed with it. Append each round with:\n"
            f"  {_ledger_cmd(target, kind)}\nthen rerun --record and retry. For an intentional skip "
            f"(e.g. the review environment is down), prefix the command with {SKIP_VAR}=1 (documented escape hatch).")


def _ledger_stale(what: str, path: str, target: str, kind: str) -> str:
    return (f"{what} blocked: the round ledger {path} is stale — its last round reviewed different "
            f"content than is being recorded. Review the current content and append that round with:\n"
            f"  {_ledger_cmd(target, kind)}\nthen rerun --record and retry. For an intentional skip "
            f"(e.g. the review environment is down), prefix the command with {SKIP_VAR}=1 (documented escape hatch).")


def check_record_binding(record: dict, ledger_path: str, n_rounds: int, what: str) -> None:
    """With a ledger present, the record must name that ledger and its final
    round exactly — so a round appended after --record blocks until --record
    is rerun. final_round is an exact positive integer, never a boolean."""
    fr = record.get("final_round")
    ok = (record.get("rounds_ledger") == ledger_path and isinstance(fr, int)
          and not isinstance(fr, bool) and fr > 0 and fr == n_rounds)
    if not ok:
        raise Denial(f"{what} blocked: record does not match the ledger's final round — the record must "
                     f"carry rounds_ledger {ledger_path!r} and final_round {n_rounds} ({LEDGER_SUFFIX} "
                     f"ledger has {n_rounds} round(s)). Rerun --record after the last run_review.py --round.")


# --- config ----------------------------------------------------------------------

def _parse_config(data: bytes, where: str) -> dict:
    obj = _parse_json(data)
    if obj is Denial or not isinstance(obj, dict):
        raise Denial(f"gate config unreadable: {CONFIG_PATH} in {where} is not a JSON object")
    if "ledger" in obj and not isinstance(obj["ledger"], bool):
        raise Denial(f"gate config unreadable: {CONFIG_PATH} in {where} has a non-boolean 'ledger'")
    return obj


def _unborn_head(git) -> bool:
    """Whether HEAD is genuinely unborn — a fresh repository making its first
    commit — rather than merely unreadable. Unborn means: the repository opens
    (`rev-parse --git-dir`), HEAD is a symbolic ref to a branch, and that branch
    does not resolve. Anything else (a timeout, a corrupt object store, a
    detached HEAD that cannot be read) is a Denial: an unreadable switch must
    never read as an absent one."""
    code, out = git(["rev-parse", "--git-dir"])
    if code != 0:
        raise Denial(f"gate config unreadable: git could not open the repository "
                     f"(rev-parse --git-dir exit {code}: {out.decode(errors='replace').strip()[:120]})")
    code, out = git(["symbolic-ref", "-q", "HEAD"])
    name = out.decode(errors="replace").strip() if code == 0 else ""
    if not name.startswith("refs/heads/"):
        raise Denial("gate config unreadable: HEAD could not be read and is not a symbolic ref to a branch")
    code, _ = git(["rev-parse", "--verify", "--quiet", name])
    if code == 0:
        raise Denial(f"gate config unreadable: git could not read HEAD although {name} resolves")
    return True


def head_config(git) -> dict | None:
    """The switch as HEAD committed it, or None when HEAD has no config file at
    all (git's canonical missing-path error only) or when there is no HEAD to
    read (an unborn branch: a fresh repository making its first commit — and
    only that, see _unborn_head). Any other failure, or a config that parses
    wrong, is a Denial."""
    code, _ = git(["rev-parse", "--verify", "--quiet", "HEAD"])
    if code != 0 and _unborn_head(git):
        return None
    code, out = git(["show", f"HEAD:{CONFIG_PATH}"])
    if code == 0:
        return _parse_config(out, "HEAD")
    missing = code == 128 and (b"does not exist in" in out or b"exists on disk, but not in" in out)
    if not missing:
        raise Denial(f"gate config unreadable: git could not read HEAD:{CONFIG_PATH} "
                     f"(exit {code}: {out.decode(errors='replace').strip()[:120]})")
    return None


def _config_staged(git) -> bool:
    """Whether the index carries the config at all — with no config in HEAD, a
    tracked config is by definition an addition, so `diff --cached` lists it
    whichever source (index or worktree) the commit will finally read."""
    code, out = git(["diff", "--cached", "--name-only"])
    return code == 0 and CONFIG_PATH in _lines(out)


# --- deny texts --------------------------------------------------------------------

def _record_cmd(slug_or_branch: str, kind: str) -> str:
    return (f"<run the roundtable panel, then> ... | python3 {HELPER} "
            f"--record --target {slug_or_branch} --kind {kind}")


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


# --- the decision ----------------------------------------------------------------

def _check_environment(env) -> None:
    for k in env:
        if isinstance(k, str) and k.startswith("GIT_"):
            if env.get(k) not in INERT_GIT_ENV.get(k, ()):
                raise Denial(f"{k} is set in the hook environment — a GIT_* variable relocates the "
                             "repository or names a program git runs after the hook; unset it")


def _check_cwd(payload) -> None:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise Denial("payload carries no cwd — the hook cannot resolve a pathspec without knowing "
                     "where the shell is")
    if os.path.realpath(cwd) != os.path.realpath(ROOT):
        raise Denial(f"commit must run from the repository root ({ROOT}), not {cwd} — a pathspec "
                     "issued elsewhere is invisible to the root-based lookups")


def _has_message(spec: CommitSpec, read_file) -> bool:
    if any(m for m in spec.messages):
        return True
    for f in spec.message_files:
        if f == "-":
            return True  # the message arrives on stdin; git opens no editor
        data = read_file(f)
        if data:
            return True
    return spec.amend and spec.no_edit


def _binder_gate(spec: CommitSpec, config: dict, git, helper, read_file, is_symlink,
                 sources: Sources) -> None:
    ledger_on = config.get("ledger") is True
    binders = sources.binders()
    if not binders:
        return
    if not _has_message(spec, read_file):
        raise Denial("Commit blocked: a gated commit must carry its message (-m / -F, or --amend "
                     "--no-edit) — otherwise git launches the configured editor after the hook.")
    for path in binders:
        slug = slug_of(path)
        _, data = sources.read(path)
        if data is None:
            continue  # nothing readable to commit for this path
        rec_path = f"{RECORD_DIR}{slug}.json"
        _, rec_bytes = sources.read(rec_path)  # a symlinked worktree record is denied in there
        wt_rec = read_file(rec_path)
        if wt_rec is not None and record_shaped(wt_rec) is None:
            raise Denial(_deny(
                f"Commit blocked: the file at the review record path {rec_path} is not a review "
                f"record (no string reviewed_hash), so plan file {path} has no recorded review.",
                _record_cmd(slug, "binder")))
        if rec_bytes is None:
            raise Denial(_deny(
                f"Commit blocked: the review record {rec_path} for {path} is "
                f"not part of this commit (stage it so the audit trail survives checkout).",
                _record_cmd(slug, "binder")))
        if wt_rec is not None and wt_rec != rec_bytes:
            raise Denial(_deny(
                f"Commit blocked: record source mismatch: the record git will commit is not the one "
                f"on disk ({rec_path}) — the freshness helper reads the working-tree record, so the "
                f"two must be identical.",
                _record_cmd(slug, "binder")))
        if record_shaped(rec_bytes) is None:
            raise Denial(_deny(
                f"Commit blocked: the record git will commit at {rec_path} is not a review record "
                f"(no string reviewed_hash), so plan file {path} has no recorded review.",
                _record_cmd(slug, "binder")))
        rc = helper(["--check", "--target", slug, "--kind", "binder", "--bytes-stdin"], data)
        if rc != 0:
            raise Denial(_deny(
                f"Commit blocked: plan file {path} has no fresh recorded review "
                f"(run_review.py --check found none matching the content being committed).",
                _record_cmd(slug, "binder")))
        if ledger_on:
            ledger_path = f"{RECORD_DIR}{slug}{LEDGER_SUFFIX}"
            _, ledger_bytes = sources.read(ledger_path)
            if ledger_bytes is None:
                raise Denial(_ledger_absent("Commit", ledger_path, slug, "binder"))
            n_rounds, last_hash = validate_ledger(ledger_bytes, ledger_path)
            if last_hash != hashlib.sha256(data).hexdigest():
                raise Denial(_ledger_stale("Commit", ledger_path, slug, "binder"))
            record = record_shaped(rec_bytes)
            if record is not None:
                check_record_binding(record, ledger_path, n_rounds, "Commit")


def _head_read(path: str, git) -> bytes | None:
    """A file as HEAD holds it (a merge commits nothing merely staged)."""
    code, out = git(["ls-tree", "HEAD", "--", path])
    if code == 0:
        for ln in _lines(out):
            fields = ln.split()
            if len(fields) >= 3 and _MODE_RE.match(fields[0]) and fields[0] not in FILE_MODES and ln.endswith(path):
                raise Denial(f"gated path {path} is not a regular file in HEAD (mode {fields[0]})")
    code, out = git(["show", f"HEAD:{path}"])
    return out if code == 0 else None


def _merge_gate(mspec: MergeSpec, ref: str, config: dict, git, helper, read_file) -> None:
    ledger_on = config.get("ledger") is True
    if not mspec.ff_only and not mspec.no_edit and not any(mspec.messages):
        raise Denial(f"Merge blocked: merge must carry --no-edit or a message (-m) — anything but "
                     f"--ff-only can open the configured editor after the hook. Use\n  {LAND_VAR}=1 git "
                     f"merge --no-ff --no-edit {ref}")
    code, out = git(["rev-parse", ref])
    tip = out.decode(errors="replace").strip() if code == 0 else ""
    if not tip:
        raise Denial(_deny(f"Merge blocked: {ref} could not be resolved to a tip.", _record_cmd(ref, "branch")))
    rec_path = f"{RECORD_DIR}{BRANCH_PREFIX}{tip}.json"
    ledger_path = f"{RECORD_DIR}{BRANCH_PREFIX}{tip}{LEDGER_SUFFIX}"
    rec_bytes = _head_read(rec_path, git)
    wt_rec = read_file(rec_path)
    if wt_rec is not None and record_shaped(wt_rec) is None:
        raise Denial(_deny(f"Merge blocked: the file at the review record path {rec_path} is not a "
                           f"review record.", _record_cmd(ref, "branch")))
    if rec_bytes is None and wt_rec is not None:
        raise Denial(_deny(f"Merge blocked: the review record {rec_path} is not in HEAD — a merge "
                           f"commits nothing that is merely staged; commit the record first.",
                           _record_cmd(ref, "branch")))
    record = None
    if rec_bytes is not None:
        record = record_shaped(rec_bytes)
        if record is None:
            raise Denial(_deny(f"Merge blocked: HEAD:{rec_path} is not a review record.",
                               _record_cmd(ref, "branch")))
        if record.get("reviewed_hash") != tip:
            raise Denial(_deny(f"Merge blocked: the review record HEAD:{rec_path} did not review "
                               f"the current tip {tip}.", _record_cmd(ref, "branch")))
    rc = helper(["--check", "--target", ref, "--kind", "branch"], None)
    if rc != 0:
        raise Denial(_deny(
            f"Merge blocked: {ref} has no fresh recorded review for its current tip "
            f"(expected a {RECORD_DIR}{BRANCH_PREFIX}<tip-sha>.json record).",
            _record_cmd(ref, "branch")))
    if ledger_on:
        ledger_bytes = _head_read(ledger_path, git)
        if ledger_bytes is None:
            raise Denial(_ledger_absent("Merge", ledger_path, ref, "branch"))
        n_rounds, last_hash = validate_ledger(ledger_bytes, ledger_path)
        if last_hash != tip:
            raise Denial(_ledger_stale("Merge", ledger_path, ref, "branch"))
        if record is not None:
            check_record_binding(record, ledger_path, n_rounds, "Merge")


def _first_enable_possible(command: str, env, git, read_file) -> bool:
    """With no config in HEAD, whether this commit could still record an enabled
    config. A single-segment, prefix-free commit records the index (or the
    worktree), so `diff --cached` is a complete answer. Any other shape — a
    preceding segment that may stage the config before git runs, an assignment
    prefix, a GIT_* variable in the prefix or the environment that may point git
    at an index the hook is not reading — is answered by the grammar instead,
    but only when a config exists somewhere the commit could take it from: a
    repository with the hook and no config at all never meets the grammar."""
    if _config_staged(git):
        return True
    segs = _segments(command, bare_amp=True)
    first = _loose_words(segs[0]) if segs else []
    simple = (len(segs) == 1 and bool(first) and first[0].text == "git" and first[0].unquoted == "git"
              and not any(isinstance(k, str) and k.startswith("GIT_") for k in env))
    if simple:
        return False
    return read_file(CONFIG_PATH) is not None


def decide(payload, env, git, helper, config, read_file=_real_read,
           is_symlink=_real_is_symlink) -> tuple[int, str]:
    """(exit_code, stderr_message). Pure over its inputs so --self-test can drive
    it with fabricated payloads and stubbed git/helper. `config=None` makes the
    gate resolve its own switch from HEAD (or the commit's source on first enable)."""
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
    # never changes. Always the human. Every landing segment answers for itself:
    # an approval prefix on one merge says nothing about the next one.
    landings = merge_invocations(command)
    if landings and env.get(LAND_VAR) != "1":
        branch = current_branch(git)
        if branch == default_branch(git):
            for ref, approved_inline in landings:
                if not approved_inline:
                    return 2, _deny_landing(ref, branch)

    # the skip hatch is evaluated before every other rule — as an exact leading
    # assignment on every gated invocation, or in the environment; never as text
    if has_skip_prefix(command) or env.get(SKIP_VAR) == "1":
        return 0, ""

    hidden = hidden_invocation(command)
    commit = is_commit_command(command) or hidden is not None
    merge = is_merge_command(command) and merged_integration_ref(command) is not None
    if not commit and not merge:
        return 0, ""

    def enabled(cfg) -> bool:
        return isinstance(cfg, dict) and bool(cfg.get("enabled"))

    def points_of(cfg) -> dict:
        return cfg.get("points") if isinstance(cfg.get("points"), dict) else {}

    try:
        # the switch comes first: enabled:false (or no config) turns every review
        # rule off, the grammar included. Only a first enable — no config in HEAD
        # and the config staged for this commit — has to parse the command to find
        # the config the commit will record; a consumer repo with the hook and no
        # config never meets the grammar at all.
        if config is None:
            config = head_config(git)
            if config is None and not (commit and _first_enable_possible(command, env, git, read_file)):
                return 0, ""
        if config is not None and not enabled(config):
            return 0, ""
        if hidden is not None:
            raise Denial(f"env {hidden} hides the command it runs from the hook: env re-splits that "
                         f"string by its own rules, and the hook cannot read a split string. Spell the "
                         f"invocation as separate words instead of an env {hidden} string.")
        _check_cwd(payload)
        _check_environment(env)
        _, words = parse_invocation(command)
        sub = words[1].text
        if sub == "commit":
            spec = parse_commit(words[2:])
            sources = Sources(spec, git, read_file, is_symlink)
            if config is None:
                _, data = sources.read(CONFIG_PATH)
                config = _parse_config(data, "the commit's source") if data is not None else {}
                if not enabled(config):
                    return 0, ""
            if points_of(config).get("plan_commit"):
                _binder_gate(spec, config, git, helper, read_file, is_symlink, sources)
            return 0, ""
        # merge
        if config is None:
            return 0, ""  # a merge commits nothing staged; no config in HEAD means off
        mspec = parse_merge(words[2:])
        if mspec.ref is None or not _INTEGRATION_REF_FULL_RE.match(mspec.ref):
            return 0, ""
        if points_of(config).get("deliver_merge") and current_branch(git) == default_branch(git):
            _merge_gate(mspec, mspec.ref, config, git, helper, read_file)
        return 0, ""
    except Denial as d:
        return 2, str(d)


def hook_main(stdin_text: str, env, git, helper, config=None) -> tuple[int, str]:
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

def _payload(command: str, tool: str = "Bash", cwd: str | None = None) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd or str(ROOT),
            "tool_input": {"command": command}}


def _run_self_test() -> int:
    import fnmatch
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    CFG = {"enabled": True, "points": {"plan_commit": True, "deliver_merge": True}}
    ON = {"enabled": True, "ledger": True, "points": {"plan_commit": True, "deliver_merge": True}}
    BP, RP, LP = ".karta/binders/x.json", ".karta/roundtable/x.json", ".karta/roundtable/x.rounds.json"
    B0, B1 = b'{"slug":"x","v":0}', b'{"slug":"x","v":1}'
    H0, H1 = hashlib.sha256(B0).hexdigest(), hashlib.sha256(B1).hexdigest()
    TIP = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    BR, BL = f"{RECORD_DIR}branch-{TIP}.json", f"{RECORD_DIR}branch-{TIP}.rounds.json"

    def rec(h, n=1, lp=LP):
        return json.dumps({"reviewed_hash": h, "target_ref": "x", "panel": [], "rounds_ledger": lp,
                           "final_round": n}).encode()

    def led(*hashes):
        return json.dumps({"target_ref": "x", "target_kind": "binder",
                           "rounds": [{"round": i + 1, "reviewed_hash": h} for i, h in enumerate(hashes)]}).encode()

    # detection
    check("detect commit", is_commit_command('git commit -m x') and not is_commit_command("git status"))
    check("detect merge", is_merge_command("git merge --no-ff karta/x/integration")
          and not is_merge_command("git status"))
    check("detect bare merge", is_merge_command("git merge"))
    check("merge-base is not a merge", not is_merge_command("git merge-base --is-ancestor HEAD main"))
    check("merge-tree is not a merge", not is_merge_command("git merge-tree HEAD main"))
    check("merge-file is not a merge", not is_merge_command("git merge-file mine.txt base.txt theirs.txt"))
    check("a quoted commit command is text, not an invocation",
          not is_commit_command('echo "git commit -m x" > note.txt'))
    check("integration ref extracted", merged_integration_ref("git merge --squash karta/foo/integration") == "karta/foo/integration")
    check("no ref for unrelated merge", merged_integration_ref("git merge feature/x") is None)

    # a fabricated repository: {path: {"head":bytes, "index":bytes, "work":bytes, "link":bool, "<key>_mode":str}}
    def make(tree, cur="main", default="main", tip=None, unborn=None, headfail=False, gitdirfail=False):
        def hit(p, s):
            s = s.rstrip("/")
            return s in (".", "") or s == p or p.startswith(s + "/") or fnmatch.fnmatch(p, s)

        def matched(p, specs):
            inc = [s for s in specs if not s.startswith((":!", ":(exclude)"))]
            exc = [s.split(")", 1)[-1] if s.startswith(":(") else s[2:] for s in specs if s.startswith((":!", ":(exclude)"))]
            return (not specs) or (any(hit(p, s) for s in inc) and not any(hit(p, s) for s in exc))

        calls = []

        def git(argv, input_bytes=None):
            calls.append(argv)
            if argv[:3] == ["diff", "--cached", "--name-only"]:
                return 0, "".join(p + "\n" for p, t in tree.items() if t.get("index") is not None and t.get("index") != t.get("head")).encode()
            if argv[:2] == ["diff", "--name-only"]:
                return 0, "".join(p + "\n" for p, t in tree.items() if (t.get("index") is not None or t.get("head") is not None)
                                  and t.get("work") != (t.get("index") if t.get("index") is not None else t.get("head"))).encode()
            if argv[0] in ("ls-files", "ls-tree"):
                specs = argv[argv.index("--") + 1:] if "--" in argv else []
                key = "index" if argv[0] == "ls-files" else "head"
                long = ("-s" in argv) or (argv[0] == "ls-tree" and "--name-only" not in argv)

                def line(p, t):
                    mode = t.get(key + "_mode", "100644")
                    if not long:
                        return p + "\n"
                    return (f"{mode} {'0' * 40} 0\t{p}\n") if argv[0] == "ls-files" else (f"{mode} blob {'0' * 40}\t{p}\n")
                return 0, "".join(line(p, t) for p, t in tree.items() if t.get(key) is not None and matched(p, specs)).encode()
            if argv == ["rev-parse", "--git-dir"]:
                return (128, b"fatal: not a git repository") if gitdirfail else (0, b".git\n")
            if argv == ["symbolic-ref", "-q", "HEAD"]:
                return 0, f"refs/heads/{cur}\n".encode()
            if argv[:3] == ["rev-parse", "--verify", "--quiet"]:
                if argv[3] == "HEAD":
                    return (128, b"") if unborn else (1, b"") if headfail else (0, b"0" * 40 + b"\n")
                return (1, b"") if unborn else (0, b"0" * 40 + b"\n")
            if argv[0] == "rev-parse":
                return (0, (tip + "\n").encode()) if tip and _INTEGRATION_REF_FULL_RE.match(argv[-1]) else (1, b"")
            if argv[0] == "show":
                key, _, p = argv[1].partition(":")
                key = "head" if key == "HEAD" else "index"
                ent = tree.get(p) or {}
                if ent.get("gitfail") and key == "head":
                    return 128, b"fatal: unable to read blob object"
                if unborn and key == "head":
                    return 128, unborn
                v = ent.get(key)
                if v is not None:
                    return 0, v
                return 128, (f"fatal: path '{p}' does not exist in 'HEAD'".encode() if key == "head" else b"")
            if argv == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
                return 0, f"refs/remotes/origin/{default}\n".encode()
            if argv == ["symbolic-ref", "--short", "HEAD"]:
                return 0, f"{cur}\n".encode()
            return 1, b""

        def read_file(path):
            for p, t in tree.items():
                if str(path).endswith(p):
                    return t.get("work")
            return None

        def is_symlink(path):
            for p, t in tree.items():
                if str(path).endswith(p):
                    return bool(t.get("link"))
            return False
        return git, read_file, is_symlink, calls

    seen = []

    def helper(args, data):
        seen.append(data)
        return 0 if "--bytes-stdin" not in args or data == B1 else 1

    stale = lambda args, data: 1

    def run(cmd, tree, config=CFG, env=None, cwd=None, helper_=helper, **kw):
        git, rf, isl, calls = make(tree, **kw)
        seen.clear()
        code, msg = decide(_payload(cmd, cwd=cwd), env or {}, git, helper_, config, read_file=rf, is_symlink=isl)
        return code, msg, calls

    # fixtures
    PLAIN = {BP: {"head": B0, "index": B1, "work": B1}, RP: {"head": rec(H0), "index": rec(H1), "work": rec(H1)},
             LP: {"head": led(H0), "index": led(H1), "work": led(H1)}}
    NOLEDGER = {BP: PLAIN[BP], RP: PLAIN[RP]}

    # binder-commit gate (record only)
    code, _, _ = run("git commit -m x", NOLEDGER, helper_=stale)
    check("stale binder record blocks commit (exit 2)", code == 2)
    code, msg, _ = run("git commit -m x", NOLEDGER)
    check("fresh record + record in the commit allows commit", code == 0, msg)
    NOREC = {BP: PLAIN[BP]}
    code, msg, _ = run("git commit -m x", NOREC)
    check("fresh record but record not in commit blocks", code == 2)
    check("record-not-in-commit reason mentions staging", "stage it" in msg or "not part of this commit" in msg)
    code, _, _ = run("git commit -m x", {}, helper_=stale)
    check("commit staging no binder allows", code == 0)
    WT = {BP: {"head": B0, "index": B0, "work": B1}, RP: {"head": rec(H0), "index": rec(H0), "work": rec(H1)}}
    code, _, _ = run("git commit -a -m x", WT, helper_=stale)
    check("git commit -a with stale worktree-binder record blocks", code == 2)
    code, msg, _ = run("git commit -a -m x", WT)
    check("git commit -a gates a binder modified only in the worktree on its worktree bytes",
          code == 0 and seen and seen[-1] == B1, msg)

    # deny reason content
    code, msg, _ = run("git commit -m x", NOLEDGER, helper_=stale)
    check("deny reason names record dir", RECORD_DIR in msg)
    check("deny reason names the helper --record", "run_review.py --record" in msg)
    check("deny reason names the escape", SKIP_VAR in msg)

    # ledger switch on
    code, msg, _ = run("git commit -m x", NOLEDGER, ON)
    check("ledger on: missing ledger blocks with the three remediation literals",
          code == 2 and "run_review.py --round" in msg and ".rounds.json" in msg and "round ledger" in msg, msg)
    code, msg, calls = run("git commit -m x", PLAIN, ON)
    check("ledger on: last round matching the staged blob passes", code == 0, msg)
    check("the ledger read costs at most six git show per gated commit",
          len([c for c in calls if c[0] == "show"]) <= 6, str(calls))
    ST = dict(PLAIN); ST[LP] = {"head": led(H0), "index": led(H0), "work": led(H0)}
    code, msg, _ = run("git commit -m x", ST, ON)
    check("ledger on: last round with a different hash blocks — content, not presence",
          code == 2 and "round ledger" in msg and "run_review.py --round" in msg, msg)
    code, _, _ = run("git commit -m x", NOLEDGER, CFG)
    check("ledger absent from the config: the missing ledger is allowed (consumer repos unaffected)", code == 0)
    code, _, _ = run("git commit -m x", NOLEDGER, {**CFG, "ledger": False})
    check("ledger:false: the missing ledger is allowed", code == 0)
    code, _, _ = run("KARTA_SKIP_ROUNDTABLE=1 git commit -m x", NOLEDGER, ON)
    check("KARTA_SKIP_ROUNDTABLE=1 bypasses the ledger check too — one hatch, not two", code == 0)

    # malformed ledgers are denials, never internal errors (through hook_main)
    def hm(ledger):
        t = dict(PLAIN); t[LP] = {"head": ledger, "index": ledger, "work": ledger}
        git, rf, isl, _ = make(t)
        return decide(_payload("git commit -m x"), {}, git, helper, ON, read_file=rf, is_symlink=isl)
    code, msg = hm(b"{not json")
    check("malformed ledger JSON denies instead of failing open", code == 2 and "round ledger" in msg, msg)
    code, msg = hm(json.dumps({"target_ref": "x", "rounds": []}).encode())
    check("empty rounds list denies instead of failing open", code == 2 and "round ledger" in msg, msg)
    code, msg = hm(json.dumps({"target_ref": "x", "rounds": [{"round": 1}]}).encode())
    check("last round without reviewed_hash denies instead of failing open", code == 2 and "round ledger" in msg, msg)
    for junk in (b"[]", b"5", json.dumps({"target_ref": "x"}).encode(), json.dumps({"rounds": {}}).encode(),
                 json.dumps({"rounds": [None]}).encode(), json.dumps({"rounds": [{"round": 1, "reviewed_hash": ""}]}).encode()):
        code, msg = hm(junk)
        check(f"malformed ledger shape denies: {junk[:30]!r}", code == 2 and "round ledger" in msg, msg)
    git, rf, isl, _ = make({**PLAIN, LP: {"head": b"{x", "index": b"{x", "work": b"{x"}})
    code, _ = hook_main(json.dumps(_payload("git commit -m x")), {}, git, helper, ON)
    check("a malformed ledger through hook_main exits 2, not the fail-open 0", code == 2)
    OTHER = dict(NOLEDGER); OTHER[".karta/roundtable/other.rounds.json"] = {"head": led(H1), "index": led(H1), "work": led(H1)}
    code, _, _ = run("git commit -m x", OTHER, ON)
    check("ledger is keyed on the exact record key so a differently named .rounds.json does not satisfy the gate", code == 2)

    # source matrix: HEAD, index and worktree ledgers carry three different hashes
    B2 = b'{"slug":"x","v":2}'; H2 = hashlib.sha256(B2).hexdigest()
    TRI = {BP: {"head": B0, "index": B1, "work": B1}, RP: {"head": rec(H0), "index": rec(H1), "work": rec(H1)},
           LP: {"head": led(H0), "index": led(H2), "work": led(H1)}}
    code, msg, _ = run("git commit .karta/roundtable/x.rounds.json .karta/roundtable/x.json .karta/binders/x.json -m x", TRI, ON)
    check("pathspec commit naming the ledger is judged on worktree bytes when HEAD, index and worktree ledgers differ",
          code == 0, msg)
    TRI2 = {BP: {"head": B1, "index": B1, "work": B1}, RP: {"head": rec(H1), "index": rec(H1), "work": rec(H1)},
            LP: {"head": led(H1), "index": led(H2), "work": led(H0)}}
    code, msg, _ = run("git commit .karta/binders/x.json -m x", TRI2, ON)
    check("pathspec commit naming only the binder is judged on HEAD bytes when HEAD, index and worktree ledgers differ",
          code == 0, msg)
    TRI3 = {BP: {"head": B0, "index": B1, "work": B1}, RP: {"head": rec(H0), "index": rec(H1), "work": rec(H1)},
            LP: {"head": led(H0), "index": led(H1), "work": led(H2)}}
    code, msg, _ = run("git commit -m x", TRI3, ON)
    check("plain commit is judged on index bytes when HEAD, index and worktree ledgers differ", code == 0, msg)
    code, _, _ = run("git commit -m x", {**TRI3, LP: {"head": led(H0), "index": led(H2), "work": led(H1)}}, ON)
    check("plain commit with a stale index ledger denies even though HEAD and worktree differ from it", code == 2)
    DEL = dict(PLAIN); DEL[LP] = {"head": led(H1)}
    code, _, _ = run("git commit -m x", DEL, ON)
    check("staged ledger deletion under a plain commit is absent and denied", code == 2)
    PS = {BP: {"head": B0, "index": B1, "work": B1}, RP: {"head": rec(H0), "index": rec(H1), "work": rec(H1)},
          LP: {"index": led(H1), "work": led(H1)}}
    code, _, _ = run("git commit .karta/binders/x.json .karta/roundtable/x.json -m x", PS, ON)
    check("pathspec commit without the ledger in HEAD or in the pathspec is denied", code == 2)
    code, msg, _ = run("git commit .karta/binders/x.json .karta/roundtable/x.json .karta/roundtable/x.rounds.json -m x", PS, ON)
    check("pathspec commit naming the ledger passes", code == 0, msg)
    code, msg, _ = run("git commit . -m x", {BP: {"head": B0, "index": B0, "work": B1}, RP: {"head": rec(H0), "index": rec(H0), "work": rec(H1)},
                                             LP: {"head": led(H0), "index": led(H0), "work": led(H1)}}, ON)
    check("a tracked binder changed only in the worktree and committed through `.` is gated on worktree bytes",
          code == 0 and seen[-1] == B1, msg)
    code, _, _ = run("git commit -o .karta/binders/x.json .karta/roundtable/x.rounds.json -m x",
                     {BP: {"index": B1, "work": B1}, RP: {"index": rec(H1), "work": rec(H1)}, LP: {"index": led(H1), "work": led(H1)}}, ON)
    check("a staged record excluded by --only is not in the commit and is denied", code == 2)
    A = {BP: {"head": B2, "index": B1, "work": B2}, RP: {"head": rec(H1), "index": rec(H1), "work": rec(H1)}, LP: {"head": led(H1), "index": led(H1), "work": led(H1)}}
    run("git commit --amend -m x", A, ON)
    check("plain --amend hands the helper the index binder bytes", bool(seen) and seen[-1] == B1)
    run("git commit --amend -a -m x", A, ON)
    check("--amend -a hands the helper the worktree binder bytes", bool(seen) and seen[-1] == B2)
    for cmd in ("git commit --patch -m x", "git commit -p -m x", "git commit --interactive -m x",
                "git commit --pathspec-from-file=list -m x", "git commit --pathspec-from-file list -m x"):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"a mode whose content the hook cannot see is denied: {cmd}", code == 2)
    MIS = dict(PLAIN); MIS[RP] = {"head": rec(H0), "index": rec(H0), "work": rec(H1)}
    code, msg, _ = run("git commit -m x", MIS, ON)
    check("the record git will commit must be the one on disk (record source mismatch)", code == 2 and "record source mismatch" in msg, msg)
    PRE = dict(PLAIN); PRE[RP] = {"head": rec(H0), "index": json.dumps({"reviewed_hash": H1, "target_ref": "x"}).encode(),
                                  "work": json.dumps({"reviewed_hash": H1, "target_ref": "x"}).encode()}
    code, msg, _ = run("git commit -m x", PRE, ON)
    check("a pre-ledger record without rounds_ledger/final_round is denied once a ledger exists", code == 2 and "final round" in msg, msg)
    TWO = dict(PLAIN); TWO[LP] = {"head": led(H0), "index": led(H0, H1), "work": led(H0, H1)}
    code, msg, _ = run("git commit -m x", TWO, ON)
    check("a round appended after --record blocks until --record is rerun (final_round 1 vs 2 rounds)", code == 2 and "final round" in msg, msg)
    TWO2 = dict(TWO); TWO2[RP] = {"head": rec(H0), "index": rec(H1, 2), "work": rec(H1, 2)}
    code, msg, _ = run("git commit -m x", TWO2, ON)
    check("a record whose final_round equals the ledger's round count passes", code == 0, msg)
    for bad in (rec(H1, True), rec(H1, 0), rec(H1, -1), rec(H1, 2), rec(H1, 1, ".karta/roundtable/other.rounds.json"),
                json.dumps({"reviewed_hash": H1, "target_ref": "x", "rounds_ledger": LP, "final_round": 1.0}).encode(),
                json.dumps({"reviewed_hash": H1, "target_ref": "x", "rounds_ledger": LP, "final_round": "1"}).encode()):
        t = dict(PLAIN); t[RP] = {"head": rec(H0), "index": bad, "work": bad}
        code, _, _ = run("git commit -m x", t, ON)
        check(f"record binding is exact — denied: {bad[-40:]!r}", code == 2)
    NR = dict(PLAIN); NR[RP] = {"head": rec(H0), "index": B1, "work": B1}
    code, msg, _ = run("git commit -m x", NR, ON)
    check("a file at the record path that is not record-shaped is no record", code == 2 and "record" in msg, msg)

    # grammar: deny-by-default
    for cmd in ("touch f && git commit . -m x", "touch f; git commit . -m x", "echo y | git commit . -m x",
                "touch f || git commit . -m x", "touch f & git commit . -m x", "touch f\ngit commit . -m x",
                "git commit . -m x && git commit --amend -a --no-edit", "cd .karta && git commit binders/x.json -m x"):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"a preceding or trailing command segment is denied: {cmd.split(chr(10))[0]!r}", code == 2)
    for cmd in ("git commit " + chr(36) + "(echo .karta) -m x", "git commit .karta -m " + chr(96) + "date" + chr(96),
                'git commit .karta -m "note ' + chr(36) + '(date)"'):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"a command substitution anywhere is denied: {cmd!r}", code == 2)
    for cmd in ("git -C .karta commit binders/x.json -m x", "git --git-dir=.git commit -m x", "git --git-dir .git commit -m x",
                "git --work-tree=. commit -m x", "GIT_DIR=.git git commit -m x", "GIT_WORK_TREE=.karta git commit -m x",
                "GIT_INDEX_FILE=/tmp/i git commit -m x", "'GIT_DIR=.git' git commit -m x", "FOO=bar git commit -m x",
                "KARTA_LANDING_APPROVED=1 GIT_INDEX_FILE=/tmp/i git commit -m x"):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"a relocating invocation or a foreign prefix is denied: {cmd!r}", code == 2)
    for cmd in ("git commit -am x", "git commit --amend -C HEAD", "git commit -c HEAD -m x", "git commit -t tpl -m x",
                "git commit --fixup HEAD", "git commit --zzz val -m x"):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"an option outside the whitelist is denied by name: {cmd!r}", code == 2)
    for cmd in ("git commit -m {x,.karta}", "git commit -m " + chr(36) + "VAR", "git commit -m * .karta",
                "git commit -F <(cat f) .karta", "git commit --author=" + chr(36) + "U -m x",
                "git commit .karta/{binders/x.json,roundtable/x.json} -m x", 'git commit "' + chr(36) + 'B" -m x',
                "git commit ../karta/.karta -m x", "git commit /abs/.karta -m x", "git commit ~/x/.karta -m y",
                "git commit ~+ -m x", "git commit ~ -m x", 'git commit "x -m y'):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"an unresolvable token is denied, not guessed: {cmd!r}", code == 2)
    for cmd in ("git commit .karta -m x > .karta/binders/x.json", "git commit -a -m x > .karta/roundtable/x.rounds.json",
                "git commit .karta -m x >> log", "git commit .karta -m x < in", "git commit .karta -m x 2> err",
                "git commit .karta -m x >file", "git commit .karta -m x >&f", "git commit .karta -m x <>f"):
        code, _, _ = run(cmd, PLAIN, ON)
        check(f"an unquoted redirection is denied: {cmd!r}", code == 2)
    for cmd in ('git commit .karta -m "note {a,b} ' + chr(36) + 'VAR"', "git commit --message='{a,b}' .karta",
                "git commit --author='A <a@b>' --date='now' --trailer='K: v' .karta -m x",
                "git commit -F '.karta/binders/x.json' .karta", "KARTA_LANDING_APPROVED=1 git commit .karta -m x"):
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"quoted inert values pass on a clean pathspec: {cmd!r}", code == 0, msg)
    for cmd in ("git commit .karta", "git commit --amend", "git commit -a", 'git commit -m "" .karta'):
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"a gated commit without a message source is denied: {cmd!r}", code == 2 and "message" in msg, msg)
    code, msg, _ = run("git commit --amend --no-edit", PLAIN, ON)
    check("--amend --no-edit is gated on the index and needs no message", code == 0 and seen[-1] == B1, msg)

    # quoting is invisible to git: an option is classified on its dequoted text
    qs = parse_commit(tokenize('git commit "-m" x')[2:])
    check('a fully quoted "-m" is the message option, not a pathspec', qs.messages == ["x"] and qs.pathspecs == [])
    qs = parse_commit(tokenize('git commit "-a" -m x')[2:])
    check('a fully quoted "-a" sets all', qs.all and qs.pathspecs == [])
    qs = parse_commit(tokenize('git commit "-"m x')[2:])
    check('a partly quoted "-"m is still -m', qs.messages == ["x"] and qs.pathspecs == [])
    qs = parse_commit(tokenize('git commit -m x -- "-dashed.json"')[2:])
    check("a quoted pathspec that starts with a dash after -- is a pathspec", qs.pathspecs == ["-dashed.json"] and qs.messages == ["x"])
    code, _, _ = run('git commit "-a" -m x', WT, ON, helper_=stale)
    check('git commit "-a" gates the worktree binder like -a', code == 2)
    code, _, _ = run('git commit "-m" x', PLAIN, ON, helper_=stale)
    check('git commit "-m" x is a plain commit and is gated on the index', code == 2)
    code, msg, _ = run('git commit "-m" x', PLAIN, ON)
    check('git commit "-m" x passes with a fresh record', code == 0, msg)

    # --allow-empty stages nothing new: a plain commit
    code, msg, _ = run("git commit --allow-empty -m x", {}, ON)
    check("--allow-empty on a clean tree passes", code == 0, msg)
    code, msg, _ = run("git commit --allow-empty --allow-empty-message -m x", {}, ON)
    check("--allow-empty-message is recognised", code == 0, msg)
    code, _, _ = run("git commit --allow-empty -m x", PLAIN, ON, helper_=stale)
    check("--allow-empty with a staged binder is still gated on the index", code == 2)
    code, msg, _ = run("git commit --allow-empty -m x", PLAIN, ON)
    check("--allow-empty with a staged binder and a fresh record passes", code == 0 and seen[-1] == B1, msg)

    # quoted assignment values and group openers are seen through, not bypassed
    for cmd in ('A="" git commit -m x', "A='' git commit -m x", 'FOO="a b" git commit -m x', "FOO='a b' BAR=c git commit -m x",
                "(git commit -m x)", "( git commit -m x )", "{ git commit -m x; }", "{ git commit -m x ; }",
                '(A="" git commit -m x)'):
        check(f"detected as a commit: {cmd!r}", is_commit_command(cmd))
        code, _, _ = run(cmd, PLAIN, ON, helper_=stale)
        check(f"a quoted prefix or a subshell does not bypass the review gate: {cmd!r}", code == 2)
    MI = "git merge --no-ff --no-edit karta/x/integration"
    MRECQ = {BR: {"head": rec(TIP, 1, BL)}, BL: {"head": led(TIP)}}
    for cmd in (f'FOO="a b" {MI}', f"FOO='a b' {MI}", f'A="" {MI}', f"({MI})", f"{{ {MI}; }}", f'(FOO="a b" {MI})'):
        check(f"detected as a merge: {cmd!r}", is_merge_command(cmd) and merge_invocation(cmd) == ("karta/x/integration", False))
        code, _, _ = run(cmd, MRECQ, ON, tip=TIP)
        check(f"a quoted prefix or a subshell does not bypass the landing gate: {cmd!r}", code == 2)
        code, _, _ = run(cmd, {}, CFG, env={LAND_VAR: "1"}, tip=TIP, helper_=stale)
        check(f"a quoted prefix or a subshell does not bypass the merge review gate: {cmd!r}", code == 2)
    for cmd in (f"{LAND_VAR}=1 {MI}", f"{LAND_VAR}=1 FOO='a b' {MI}", f"({LAND_VAR}=1 {MI})", f"{{ {LAND_VAR}=1 {MI}; }}"):
        check(f"the inline landing approval is still recognised: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", True))
    code, msg, _ = run(f"{LAND_VAR}=1 {MI}", MRECQ, ON, tip=TIP)
    check("the inline landing approval still lands with a fresh record", code == 0, msg)
    code, _, _ = run(f"{SKIP_VAR}=1 (git commit -m x)", PLAIN, ON, helper_=stale)
    check("the skip hatch still applies ahead of a subshell", code == 0)
    code, _, _ = run(f"{SKIP_VAR}=1 FOO='a b' git commit -m x", PLAIN, ON, helper_=stale)
    check("the skip hatch still applies ahead of a quoted prefix", code == 0)
    code, _, _ = run("KARTA_SKIP_ROUNDTABLE=1 git commit .karta", PLAIN, ON)
    check("the skip hatch is evaluated before every other rule", code == 0)
    nocwd = _payload("git commit . -m x"); del nocwd["cwd"]
    git, rf, isl, _ = make(PLAIN)
    code, msg = decide(nocwd, {}, git, helper, ON, read_file=rf, is_symlink=isl)
    check("a payload without cwd is denied", code == 2 and "cwd" in msg, msg)
    code, msg, _ = run("git commit binders/x.json -m x", PLAIN, ON, cwd=str(ROOT / ".karta"))
    check("a commit issued from a subdirectory is denied — the hook anchors to the repository root", code == 2 and "root" in msg, msg)
    for envv in ({"GIT_INDEX_FILE": "/tmp/i"}, {"GIT_CONFIG_PARAMETERS": "x"}, {"GIT_EDITOR": "touch x"},
                 {"GIT_EDITOR": "vim"}, {"GIT_PAGER": "touch x"}, {"GIT_SSH_COMMAND": "touch x"}):
        code, _, _ = run("git commit -m x", PLAIN, ON, env=envv)
        check(f"a non-inert GIT_* in the hook environment denies: {envv}", code == 2)
    code, msg, _ = run("git commit -m x", PLAIN, ON, env={"GIT_EDITOR": "true"})
    check("inert GIT_EDITOR=true in the environment does not deny", code == 0, msg)

    # symlinks and object modes
    for name in (BP, RP, LP):
        t = dict(PLAIN); t[name] = dict(PLAIN[name], link=True)
        code, msg, _ = run("git commit . -m x", t, ON)
        check(f"a symlinked gated path is denied under a pathspec commit: {name}", code == 2 and "symlink" in msg, msg)
    for name, mode in ((BP, "120000"), (RP, "160000"), (LP, "040000")):
        t = dict(PLAIN); t[name] = dict(PLAIN[name], index_mode=mode)
        code, _, _ = run("git commit -m x", t, ON)
        check(f"a non-file object mode on the index path is denied: {name} {mode}", code == 2)
    t = {BP: {"head": B1, "index": B1, "work": B1}, RP: {"head": rec(H1), "index": rec(H1), "work": rec(H1)},
         LP: {"head": led(H1), "index": led(H1), "work": led(H1), "head_mode": "120000"}}
    code, msg, _ = run("git commit .karta/binders/x.json -m x", t, ON)
    check("a committed symlink (mode 120000) on the HEAD path is denied", code == 2 and "symlink" in msg, msg)

    # config resolved by the gate itself (config=None)
    ONB = json.dumps(ON).encode(); OFFB = json.dumps({**CFG, "ledger": False}).encode()
    STALE_LP = {"head": led(H0), "index": led(H0), "work": led(H0)}

    def cfgtree(head, index, work):
        t = dict(PLAIN); t[LP] = STALE_LP
        t[CONFIG_PATH] = {k: v for k, v in (("head", head), ("index", index), ("work", work)) if v is not None}
        return t
    code, msg, _ = run("git commit -m x", cfgtree(ONB, ONB, OFFB), None)
    check("the switch is read from HEAD: HEAD ledger true, worktree false is still enforced", code == 2 and "round ledger" in msg, msg)
    code, msg, _ = run("git commit -m x", cfgtree(OFFB, OFFB, ONB), None)
    check("HEAD ledger false, worktree true is not enforced (the flip is not committed yet)", code == 0, msg)
    code, _, _ = run("git commit -m x", cfgtree(None, ONB, ONB), None)
    check("HEAD has no config, staged config true: enforced (first enable through the commit's source)", code == 2)
    code, _, _ = run("git commit . -m x", cfgtree(None, None, ONB), None)
    check("an untracked config is in no commit source: the switch is off for that commit", code == 0)
    # no config anywhere (a consumer repo with the hook installed): every gate is
    # off, the grammar included — nothing is denied by name
    NOCFG = {BP: PLAIN[BP]}
    for cmd in ("git commit -am x", "git add -A && git commit -m x", "git commit -m x", "(git commit -m x)"):
        code, msg, _ = run(cmd, NOCFG, None, cwd=str(ROOT / ".karta"))
        check(f"no config in HEAD: an unusual shape from a subdirectory is not denied: {cmd!r}", code == 0, msg)
    code, msg, _ = run("git commit -m x", NOCFG, None, env={"GIT_EDITOR": "vim"})
    check("no config in HEAD: GIT_EDITOR in the environment is not denied", code == 0, msg)
    code, _, _ = run("git commit -am x", cfgtree(None, ONB, ONB), None, cwd=str(ROOT / ".karta"))
    check("no config in HEAD but a staged enabled config: the first enable still meets the grammar", code == 2)
    code, _, _ = run("git commit -m x", cfgtree(None, ONB, ONB), None)
    check("no config in HEAD, staged config true, staged binder with a stale ledger: still gated", code == 2)
    code, msg, _ = run("git commit -m x", {**cfgtree(None, ONB, ONB), LP: PLAIN[LP]}, None)
    check("the same first enable with a fresh ledger passes", code == 0, msg)
    code, _, _ = run("git commit -am x", cfgtree(None, None, ONB), None, cwd=str(ROOT / ".karta"))
    check("no config in HEAD and none staged: -am from a subdirectory is not denied", code == 0)
    code, _, _ = run("git commit -a -m x", cfgtree(None, OFFB, ONB), None)
    check("HEAD absent, index false, worktree true under -a: the worktree config git commits is what governs", code == 2)
    code, msg, _ = run("git commit -m x", cfgtree(b"{not json", ONB, ONB), None)
    check("a HEAD config that does not parse is a denial, never fail-open", code == 2 and "config" in msg, msg)
    code, _, _ = run("git commit -m x", cfgtree(b"[]", ONB, ONB), None)
    check("a HEAD config that is not an object is a denial", code == 2)
    code, _, _ = run("git commit -m x", cfgtree(json.dumps({**CFG, "ledger": "true"}).encode(), ONB, ONB), None)
    check("a non-boolean ledger key is a denial", code == 2)
    gf = cfgtree(ONB, OFFB, OFFB); gf[CONFIG_PATH]["gitfail"] = True
    code, msg, _ = run("git commit -m x", gf, None)
    check("a git failure other than the canonical missing-path error denies rather than reading as absence",
          code == 2 and "config" in msg, msg)
    cl = cfgtree(None, ONB, ONB); cl[CONFIG_PATH]["link"] = True
    code, msg, _ = run("git commit . -m x", cl, None)
    check("a symlinked first-enable config is denied", code == 2 and "symlink" in msg, msg)
    code, msg, calls = run("git commit -m x", cfgtree(ONB, ONB, ONB), None)
    check("the read bound is seven git show when the gate resolves the config itself",
          len([c for c in calls if c[0] == "show"]) <= 7, str(calls))

    # merge gate
    E = {LAND_VAR: "1"}
    MREC = {BR: {"head": rec(TIP, 1, BL)}, BL: {"head": led(TIP)}}

    def runm(cmd, tree, config=ON, env=E, **kw):
        return run(cmd, tree, config, env=env, tip=TIP, **kw)
    code, msg, _ = runm("git merge --no-ff --no-edit karta/x/integration", MREC)
    check("branch merge gate requires branch-<tip>.rounds.json with a matching last round", code == 0, msg)
    code, msg, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: MREC[BR]})
    check("merge with no branch ledger is denied with the three remediation literals",
          code == 2 and "round ledger" in msg and "run_review.py --round" in msg and ".rounds.json" in msg, msg)
    code, msg, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: MREC[BR], BL: {"head": led("0" * 40)}})
    check("a stale HEAD branch ledger is denied and carries the three literals",
          code == 2 and "round ledger" in msg and "run_review.py --round" in msg and ".rounds.json" in msg, msg)
    code, msg, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: MREC[BR], BL: {"index": led(TIP), "work": led(TIP)}})
    check("a merge commits nothing staged: a branch ledger absent from HEAD is denied", code == 2, msg)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: {"index": rec(TIP, 1, BL), "work": rec(TIP, 1, BL)}, BL: MREC[BL]})
    check("a branch record absent from HEAD is denied even when staged", code == 2)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: {"head": rec("0" * 40, 1, BL), "work": rec(TIP, 1, BL)}, BL: MREC[BL]})
    check("a stale HEAD branch record is denied even with a fresh worktree copy", code == 2)
    code, msg, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: MREC[BR], BL: {"head": led(TIP, TIP)}})
    check("a two-round branch ledger with a final_round 1 record is denied", code == 2 and "final round" in msg, msg)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: {"head": rec(TIP, 1, ".karta/roundtable/other.rounds.json")}, BL: MREC[BL]})
    check("a merge record naming the wrong ledger path is denied", code == 2)
    code, msg, _ = runm("git merge --no-ff karta/x/integration", MREC)
    check("a message-less merge that can create a merge commit is denied", code == 2 and "no-edit" in msg, msg)
    for cmd in ("git merge --no-ff -m x karta/x/integration", "git merge --ff-only karta/x/integration"):
        code, msg, _ = runm(cmd, MREC)
        check(f"-m and --ff-only merges are gated normally: {cmd!r}", code == 0, msg)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {BR: MREC[BR]}, CFG)
    check("merge gate with the ledger off: the record alone passes", code == 0)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {}, CFG, helper_=stale)
    check("merge of integration on default branch, stale, blocks", code == 2)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", MREC, cur="feature")
    check("same merge off the default branch allows (not gated)", code == 0)
    code, _, _ = runm("git merge feature/y", {}, helper_=stale)
    check("unrelated merge allows", code == 0)
    code, _, _ = runm("git merge --squash --no-edit karta/x/integration", MREC)
    check("a merge option outside the whitelist is denied", code == 2)

    # accepted bypasses are not gated
    for cmd in ["git cherry-pick abc123", "git rebase main", "git reset --hard karta/x/integration"]:
        code, _, _ = run(cmd, {}, helper_=stale)
        check(f"accepted bypass not gated: {cmd}", code == 0)

    # escape + config
    code, _, _ = run("git commit -m x", NOLEDGER, env={"KARTA_SKIP_ROUNDTABLE": "1"}, helper_=stale)
    check("KARTA_SKIP_ROUNDTABLE=1 in env skips", code == 0)
    code, _, _ = run("git commit -m x", NOLEDGER, {}, helper_=stale)
    check("absent/disabled config allows", code == 0)
    code, _, _ = run("git commit -m x", NOLEDGER, {"enabled": True, "points": {"plan_commit": False, "deliver_merge": True}}, helper_=stale)
    check("plan_commit:false disables only the binder gate", code == 0)
    code, _, _ = runm("git merge --no-ff --no-edit karta/x/integration", {},
                      {"enabled": True, "points": {"plan_commit": True, "deliver_merge": False}}, helper_=stale)
    check("deliver_merge:false disables only the roundtable merge gate", code == 0)

    # (c) landing gate — who decides it ships. Independent of the config switch
    # and of the roundtable skip hatch, and anchored so quoted text is not an act.
    MERGE = "git merge --ff-only karta/x/integration"
    code, msg, _ = run(MERGE, MREC, ON, tip=TIP)
    check("landing gate blocks even with a fresh roundtable record", code == 2, msg)
    code, _, _ = run(MERGE, MREC, {}, tip=TIP)
    check("landing gate blocks with the roundtable config off", code == 2)
    code, _, _ = run(MERGE, MREC, {"enabled": True, "points": {"plan_commit": True, "deliver_merge": False}}, tip=TIP)
    check("landing gate blocks with deliver_merge:false", code == 2)
    code, _, _ = run(f"{SKIP_VAR}=1 {MERGE}", MREC, {}, tip=TIP)
    check("KARTA_SKIP_ROUNDTABLE does NOT bypass the landing gate", code == 2)
    code, _, _ = run(MERGE, MREC, {}, env={SKIP_VAR: "1"}, tip=TIP)
    check("KARTA_SKIP_ROUNDTABLE in env does NOT bypass the landing gate", code == 2)
    code, _, _ = run(f"{LAND_VAR}=1 {MERGE}", MREC, {}, tip=TIP)
    check("landing approved in front of the merge allows", code == 0)
    code, _, _ = run(MERGE, MREC, {}, env=E, tip=TIP)
    check("landing approved in the env allows", code == 0)
    code, _, _ = run(MERGE, MREC, {}, tip=TIP, cur="feature")
    check("landing gate does not fire off the default branch", code == 0)
    code, _, _ = run("git merge --squash karta/x/integration", MREC, {}, tip=TIP)
    check("landing gate catches the --squash form too", code == 2)
    code, _, _ = run("git merge feature/y", MREC, {}, tip=TIP)
    check("landing gate ignores a merge that is not an integration branch", code == 0)
    for cmd in ["git cherry-pick abc123", "git rebase main", "git reset --hard karta/x/integration"]:
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"landing gate shares the accepted bypass: {cmd}", code == 0)
    for quoted in [f'echo "{MERGE}" > note.txt', f"grep -n '{MERGE}' docs/how-to/roundtable.md",
                   f'python3 - <<EOF\ncmd = "{MERGE}"\nEOF']:
        code, _, _ = run(quoted, MREC, {}, tip=TIP)
        check("quoted merge text is not an invocation: " + quoted.split()[0], code == 0, f"code={code}")
    code, _, _ = run(f"cd /repo && {MERGE}", MREC, {}, tip=TIP)
    check("a real merge after && is still caught", code == 2)
    code, _, _ = run(f'echo "{LAND_VAR}=1" ; {MERGE}', MREC, {}, tip=TIP)
    check("approval mentioned elsewhere in the command does not grant it", code == 2)
    code, _, _ = run('git merge --no-ff -m "land A & B" ' + 'karta/x/integration', MREC, {}, tip=TIP)
    check("an ampersand inside the merge message does not split the ref away from the landing gate", code == 2)
    code, _, _ = run('git add -A && git commit -m x', PLAIN, {})
    check("enabled:false turns the grammar off too — the switch is absolute", code == 0)
    code, _, _ = run('git add -A && git commit -m x', cfgtree(json.dumps({**CFG, "enabled": False}).encode(), None, None), None)
    check("HEAD config enabled:false: an unusual command shape is not denied", code == 0)
    code, _, _ = run('git add -A && git commit -m x', PLAIN, ON)
    check("enabled:true: the same shape is denied by the grammar", code == 2)
    code, msg, _ = run(MERGE, MREC, {}, tip=TIP)
    check("landing deny names the human as the decider", "the human's decision" in msg)
    check("landing deny names its own variable", LAND_VAR in msg)
    check("landing deny tells an agent not to set it", f"do not set {LAND_VAR}" in msg)
    check("landing deny says the skip hatch will not help", f"{SKIP_VAR} does not bypass" in msg)

    # fail-open
    git, _, _, _ = make({})
    code, _ = hook_main("not json", {}, git, stale, CFG)
    check("malformed payload fails open", code == 0)

    def exploding(argv, input_bytes=None):
        raise RuntimeError("boom")
    code, _ = hook_main(json.dumps(_payload("git commit -m x")), {}, exploding, stale, CFG)
    check("git exception fails open", code == 0)
    code, _, _ = run("ls -la", {}, helper_=stale)
    check("non-command allows", code == 0)

    # (1) landing approval and the skip hatch are exact assignment words, not substrings
    for cmd in (f"{LAND_VAR}=1 {MERGE}", f"{LAND_VAR}='1' {MERGE}", f'{LAND_VAR}="1" {MERGE}',
                f"{LAND_VAR}=1 FOO=bar {MERGE}", f"FOO=bar {LAND_VAR}=1 {MERGE}", f"({LAND_VAR}=1 {MERGE})"):
        check(f"exact landing approval token is recognised: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", True))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"exact landing approval token lands: {cmd!r}", code == 0)
    for cmd in (f"X={LAND_VAR}=1 {MERGE}", f"{LAND_VAR}=10 {MERGE}", f'FOO="{LAND_VAR}=1" {MERGE}',
                f"FOO='{LAND_VAR}=1' {MERGE}", f"{LAND_VAR}=1x {MERGE}", f"{LAND_VAR}= {MERGE}",
                f'"{LAND_VAR}=1" {MERGE}', f"{LAND_VAR}+=1 {MERGE}", f"{LAND_VAR}=1; {MERGE}",
                f"{LAND_VAR}=1 true && {MERGE}"):
        check(f"a lookalike is not an approval: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", False), str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"a lookalike does not land: {cmd!r}", code == 2)
    for cmd in (f"{SKIP_VAR}=1 git commit -m x", f"{SKIP_VAR}='1' git commit -m x", f'{SKIP_VAR}="1" git commit -m x',
                f"FOO=bar {SKIP_VAR}=1 git commit -m x", f"({SKIP_VAR}=1 git commit -m x)"):
        check(f"exact skip token is recognised: {cmd!r}", has_skip_prefix(cmd))
        code, _, _ = run(cmd, NOLEDGER, ON, helper_=stale)
        check(f"exact skip token skips: {cmd!r}", code == 0)
    for cmd in (f'git commit -m "{SKIP_VAR}=1"', f"git commit -m '{SKIP_VAR}=1' .karta", f"X={SKIP_VAR}=1 git commit -m x",
                f"{SKIP_VAR}=10 git commit -m x", f'FOO="{SKIP_VAR}=1" git commit -m x', f"{SKIP_VAR}+=1 git commit -m x",
                f"{SKIP_VAR}=1 true; git commit -m x", f"{SKIP_VAR}=1 git add .karta && git commit -m x"):
        check(f"a lookalike skip is not the hatch: {cmd!r}", not has_skip_prefix(cmd))
        code, _, _ = run(cmd, NOLEDGER, ON, helper_=stale)
        check(f"a lookalike skip does not skip: {cmd!r}", code == 2)
    code, _, _ = run(f"{SKIP_VAR}=1 {MERGE}", MREC, {}, tip=TIP)
    check("the exact skip token still does not touch the landing gate", code == 2)

    # (2) `-F -` / `--file=-` read the message from stdin: a present message source
    for cmd in ("git commit -q -F - .karta/binders/x.json .karta/roundtable/x.json .karta/roundtable/x.rounds.json",
                "git commit --file=- .karta", "git commit --file - .karta"):
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"a stdin message file is a present message: {cmd!r}", code == 0, msg)
    code, msg, _ = run("git commit -F missing.txt .karta", PLAIN, ON)
    check("a missing message file is still no message", code == 2 and "message" in msg, msg)

    # (3) executing wrappers and command-word quoting do not hide the invocation
    WRAP = ("time ", "time -p ", "env ", "env -i ", "env -u FOO ", "env FOO=bar ", "command ", "command -p ", "builtin ",
            "exec ", "nice ", "nice -n 5 ", "nice -n5 ", "nohup ", "timeout 5 ", "timeout -k 2 5s ", "stdbuf -oL ",
            "stdbuf -o L -e L ", "! ", "FOO=a\\ b ", "FOO+=1 ", "FOO='a b' time ", "time env nice -n 5 ")
    for w in WRAP:
        check(f"wrapper does not hide a commit: {w!r}", is_commit_command(f"{w}git commit -m x"))
        check(f"wrapper does not hide a merge: {w!r}", is_merge_command(f"{w}{MI}")
              and merge_invocation(f"{w}{MI}") == ("karta/x/integration", False))
        code, _, _ = run(f"{w}git commit -m x", PLAIN, ON, helper_=stale)
        check(f"wrapped commit meets the review gate: {w!r}", code == 2)
        code, _, _ = run(f"{w}{MI}", MRECQ, ON, tip=TIP)
        check(f"wrapped merge meets the landing gate: {w!r}", code == 2)
        code, _, _ = run(f"{w}{MI}", {}, CFG, env={LAND_VAR: "1"}, tip=TIP, helper_=stale)
        check(f"wrapped merge meets the merge review gate: {w!r}", code == 2)
    for g in ('"git"', "\\git", "git''", "'git'", 'g"it"', '"git" "commit"', "git \\commit"):
        cmd = f"{g} -m x" if "commit" in g else f"{g} commit -m x"
        check(f"a quoted command word is git: {cmd!r}", is_commit_command(cmd))
        code, _, _ = run(cmd, PLAIN, ON, helper_=stale)
        check(f"a quoted command word meets the review gate: {cmd!r}", code == 2)
    for g in ('"git"', "\\git", "git''"):
        cmd = f"{g} merge --ff-only karta/x/integration"
        check(f"a quoted command word is a merge: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", False))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"a quoted command word meets the landing gate: {cmd!r}", code == 2)
    check("a backslash-newline is a continuation, not a segment break",
          is_commit_command("git \\\ncommit -m x") and merge_invocation("git merge \\\n --ff-only karta/x/integration") == ("karta/x/integration", False))
    code, _, _ = run("git \\\ncommit -m x", PLAIN, ON, helper_=stale)
    check("a continued commit meets the review gate", code == 2)
    check("a wrapper word that is not a wrapper is still text", not is_commit_command("echo time git commit -m x")
          and not is_commit_command("timeit git commit -m x") and not is_commit_command("envy git commit -m x"))

    # (4) quoting inside the ref and a control character inside a quoted message
    for cmd in ('git merge karta/x/integr""ation', "git merge --ff-only karta/x/integr'ation'", 'git merge --ff-only "karta/x/integration"',
                'git merge -m "x;y" karta/x/integration', "git merge -m 'a && b' karta/x/integration", 'git merge -m "p|q" karta/x/integration',
                'git merge -m "line1\nline2" karta/x/integration', "git merge -m 'x;y' --no-ff karta/x/integr\\ation"):
        check(f"the ref is read dequoted and the segment is split outside quotes: {cmd!r}",
              merge_invocation(cmd) == ("karta/x/integration", False) and merged_integration_ref(cmd) == "karta/x/integration", str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"landing gate holds: {cmd!r}", code == 2)
        code, _, _ = run(cmd, {}, CFG, env={LAND_VAR: "1"}, tip=TIP, helper_=stale)
        check(f"merge review gate holds: {cmd!r}", code == 2)
    code, msg, _ = run('git merge --no-ff -m "x;y" karta/x/integration', MREC, ON, env=E, tip=TIP)
    check("a quoted `;` in the merge message is not a second segment for the grammar", code == 0, msg)
    check("a quoted `;` before a merge is not a segment break", merge_invocation('echo "a;b"; git merge --ff-only karta/x/integration') == ("karta/x/integration", False))
    check("a quoted control character does not expose text as an invocation",
          not is_commit_command('echo "a; git commit -m x"') and merge_invocation("echo 'x && git merge karta/x/integration'") is None)
    code, _, _ = run('echo "a; git commit -m x"', PLAIN, ON, helper_=stale)
    check("quoted commit text behind a quoted `;` is not gated", code == 0)

    # (5) a pathspec commit records only what it names: a dirty unrelated binder is not in it
    YP, YR = ".karta/binders/y.json", ".karta/roundtable/y.json"
    DIRTY = {**PLAIN, YP: {"head": B0, "index": B0, "work": B2}, YR: {"head": rec(H0), "index": rec(H0), "work": rec(H0)}}
    code, msg, _ = run("git commit .karta/binders/x.json .karta/roundtable/x.json .karta/roundtable/x.rounds.json -m x", DIRTY, ON)
    check("a pathspec commit of binder x is not denied by a worktree-edited binder y it does not name", code == 0, msg)
    code, _, _ = run("git commit -a -m x", DIRTY, ON)
    check("-a still gates the worktree-edited binder y", code == 2)
    code, _, _ = run("git commit .karta -m x", DIRTY, ON)
    check("a pathspec that names y gates y on its worktree bytes", code == 2)

    # (6) an unborn HEAD has no config: the first commit of a fresh repository is not wedged
    for message in (b"fatal: invalid object name 'HEAD'.", b"fatal: bad object HEAD",
                    b"fatal: your current branch 'main' does not have any commits yet"):
        code, msg, _ = run("git commit -m x", {BP: {"index": B1, "work": B1}}, None, unborn=message)
        check(f"an unborn HEAD reads as no config: {message[:30]!r}", code == 0, msg)
        code, _, _ = run("git commit -m x", {BP: {"index": B1, "work": B1}, CONFIG_PATH: {"index": ONB, "work": ONB}}, None, unborn=message)
        check(f"an unborn HEAD with a staged enabled config is still a first enable: {message[:30]!r}", code == 2)
    code, msg, _ = run("git commit -m x", gf, None)
    check("a HEAD that exists but cannot be read is still a denial", code == 2 and "config" in msg, msg)

    # (7) the no-config fast path trusts the index only for a shape that records the index
    FE = cfgtree(None, None, ONB)
    for cmd in ("git add -A && git commit -m x", "git add .karta; git commit -m x", "GIT_INDEX_FILE=/tmp/alt git commit -m x",
                "FOO=bar git commit -m x", "(git commit -m x)", "env git commit -m x", "git add -A\ngit commit -m x"):
        code, _, _ = run(cmd, FE, None)
        check(f"no config in HEAD, an enabled config in the worktree: a chained or prefixed commit meets the grammar: {cmd!r}", code == 2)
    code, _, _ = run("git commit -m x", FE, None, env={"GIT_INDEX_FILE": "/tmp/alt"})
    check("no config in HEAD, worktree config, GIT_INDEX_FILE in the environment: the grammar denies it", code == 2)
    code, msg, _ = run("git commit -m x", FE, None)
    check("no config in HEAD, worktree config, a plain single commit: the index is the whole answer", code == 0, msg)
    code, msg, _ = run("git commit -m x", FE, None, env={"GIT_EDITOR": "true"})
    check("an inert GIT_* in the environment still meets the grammar and passes", code == 0, msg)
    for cmd in ("git add -A && git commit -m x", "GIT_INDEX_FILE=/tmp/alt git commit -m x", "FOO=bar git commit -m x"):
        code, msg, _ = run(cmd, NOCFG, None, env={"GIT_EDITOR": "vim"})
        check(f"no config anywhere: the same shapes are not denied (consumer repos): {cmd!r}", code == 0, msg)

    # (8) a bare `&` splits for the landing gate too; a quoted `&` never does
    for cmd in (f"true & {MI}", f"sleep 1 & {MERGE}", f"true & {LAND_VAR}=1 true & {MERGE}"):
        check(f"a backgrounded preceding command exposes the landing: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", False), str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate holds behind a bare &: {cmd!r}", code == 2)
    check("a quoted & in the message is not a split", merge_invocation('git merge --no-ff -m "A & B" karta/x/integration') == ("karta/x/integration", False))
    check("a quoted & in a single-quoted message is not a split", merge_invocation("git merge --no-ff -m 'A & B' karta/x/integration") == ("karta/x/integration", False))
    code, _, _ = run(f"true & {LAND_VAR}=1 {MERGE}", MREC, {}, tip=TIP)
    check("the approval on the landing segment behind a bare & lands", code == 0)

    # (9) wrapper options after the wrapper's positional, and `--` ending them
    for cmd in ("timeout 5 -s KILL git commit -m x", "timeout 5 -- git commit -m x", "timeout 5s -k 2 -s TERM -- git commit -m x",
                "nice -n 5 -- git commit -m x", "nice -- git commit -m x", "nohup -- git commit -m x", "stdbuf -oL -- git commit -m x",
                "timeout --signal=KILL 5 git commit -m x", "env -- git commit -m x", "env -i -- git commit -m x",
                "time -- git commit -m x", "timeout 5 -s KILL nice -n 3 -- git commit -m x"):
        check(f"wrapper options after the positional do not hide a commit: {cmd!r}", is_commit_command(cmd))
        code, _, _ = run(cmd, PLAIN, ON, helper_=stale)
        check(f"such a wrapped commit meets the review gate: {cmd!r}", code == 2)
    for w in ("timeout 5 -s KILL ", "timeout 5 -- ", "nice -n 5 -- ", "nohup -- ", "stdbuf -oL -- ", "timeout 5s -k 2 -- "):
        check(f"wrapper options after the positional do not hide a merge: {w!r}", merge_invocation(f"{w}{MI}") == ("karta/x/integration", False))
        code, _, _ = run(f"{w}{MI}", MRECQ, ON, tip=TIP)
        check(f"such a wrapped merge meets the landing gate: {w!r}", code == 2)
        code, _, _ = run(f"{w}{MI}", {}, CFG, env={LAND_VAR: "1"}, tip=TIP, helper_=stale)
        check(f"such a wrapped merge meets the merge review gate: {w!r}", code == 2)
    check("a wrapper's option value is not the command", not is_commit_command("timeout -s git commit -m x")
          and not is_commit_command("nice -n git commit -m x"))
    check("text after a wrapper that is not git is still text", not is_commit_command("timeout 5 -s KILL sleep 1")
          and not is_commit_command("timeout 5 -- echo git commit -m x"))

    # (10) env -S / env -a hide the command: fail closed, never silent
    HIDE_COMMIT = ("env -S 'git commit -m x'", 'env -S "git commit -m x"', "env --split-string='git commit -m x'",
                   "env --split-string 'git commit -m x'", "env --split='git commit -m x'", "env --s='git commit -m x'",
                   "env -iS 'git commit -m x'", "env -S'git commit -m x'", "FOO=1 env -u BAR -S 'git commit -m x'",
                   "time env -S 'git commit -m x'", "(env -S 'git commit -m x')", "env -a spoof -S 'git commit -m x'",
                   "env FOO=1 -S 'git commit -m x'", "/usr/bin/env -S 'git commit -m x'", "env - -S 'git commit -m x'")
    for cmd in HIDE_COMMIT:
        check(f"a hiding env option is reported: {cmd!r}", hidden_invocation(cmd) in ("-S", "--split-string"), str(hidden_invocation(cmd)))
        check(f"a hidden commit is not read as a plain commit: {cmd!r}", not is_commit_command(cmd))
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"a hidden command is denied by name with the config on: {cmd!r}",
              code == 2 and "env" in msg and "hides" in msg and "split string" in msg and "run git directly" not in msg.lower()
              and (" -S " in msg or "--split-string" in msg), msg)
        code, msg, _ = run(cmd, {}, ON)
        check(f"a hidden command is denied even with nothing staged: {cmd!r}", code == 2, msg)
        code, _, _ = run(cmd, PLAIN, {})
        check(f"the config switch still turns the review denial off: {cmd!r}", code == 0)
        code, _, _ = run(cmd, PLAIN, ON, env={SKIP_VAR: "1"})
        check(f"the skip hatch in the environment still applies: {cmd!r}", code == 0)
    code, _, _ = run(f"{SKIP_VAR}=1 env -S 'git commit -m x'", PLAIN, ON)
    check("the skip hatch prefixing the hidden segment applies", code == 0)
    code, msg, _ = run("env -S 'git commit -m x'", cfgtree(None, None, ONB), None)
    check("no config in HEAD, an enabled worktree config: a hidden command meets the first-enable path and is denied", code == 2, msg)
    code, msg, _ = run("env -S 'git commit -m x'", NOCFG, None)
    check("no config anywhere: a hidden command is not denied (consumer repos)", code == 0, msg)
    for cmd in ("env -i git commit -m x", "env -u FOO git commit -m x", "env -C . git commit -m x", "env --unset=FOO git commit -m x",
                "env -0 git commit -m x", "env --chdir=. git commit -m x", "env -uS git commit -m x"):
        check(f"env without a hiding option is read through: {cmd!r}", hidden_invocation(cmd) is None and is_commit_command(cmd), str(hidden_invocation(cmd)))
    check("-uS: the S is -u's value, not an option", hidden_invocation("env -uS git commit -m x") is None)
    check("the hiding scan is anchored: a quoted env -S is text", hidden_invocation("echo \"env -S 'git commit'\"") is None)
    HIDE_MERGE = ("env -S 'git merge --no-ff --no-edit karta/x/integration'", 'env -S "git merge --ff-only karta/x/integration"',
                  "env --split-string='git merge karta/x/integration'", "env -a spoof -S 'git merge --ff-only karta/x/integration'",
                  "/usr/bin/env -S 'git merge --ff-only karta/x/integration'", "env -iS 'git merge -m x karta/x/integration'")
    for cmd in HIDE_MERGE:
        check(f"a hidden merge of an integration ref is a landing: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", False), str(merge_invocation(cmd)))
        code, msg, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate denies a hidden merge with the config off: {cmd!r}", code == 2 and "human" in msg, msg)
        code, _, _ = run(cmd, MREC, ON, tip=TIP)
        check(f"the landing gate denies a hidden merge with the config on: {cmd!r}", code == 2)
        code, _, _ = run(cmd, MREC, {}, tip=TIP, cur="feature")
        check(f"off the default branch the hidden merge is not a landing: {cmd!r}", code == 0)
        check(f"the approval prefix on the hidden segment is recognised: {cmd!r}", merge_invocation(f"{LAND_VAR}=1 {cmd}") == ("karta/x/integration", True))
        code, _, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, {}, tip=TIP)
        check(f"an approved hidden merge passes the landing gate with the config off: {cmd!r}", code == 0)
        code, _, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"an approved hidden merge is still denied by the review grammar with the config on: {cmd!r}", code == 2)
    check("a hidden segment that merges no integration ref is not a landing",
          merge_invocation("env -S 'git merge feature/y'") is None and merge_invocation("env -S 'echo karta/x/integration'") is None)
    code, _, _ = run("env -S 'git merge feature/y'", MREC, {}, tip=TIP)
    check("a hidden non-landing merge with the config off is not denied", code == 0)

    # (11) the command word is git by basename
    for g in ("/usr/bin/git", "./git", "~/bin/git", "/usr/local/bin/git", "bin/git", '"/usr/bin/git"'):
        check(f"a git by path is a commit: {g!r}", is_commit_command(f"{g} commit -m x"))
        check(f"a git by path is a merge: {g!r}", merge_invocation(f"{g} merge --ff-only karta/x/integration") == ("karta/x/integration", False))
        code, _, _ = run(f"{g} commit -m x", PLAIN, ON, helper_=stale)
        check(f"a commit through git by path meets the review gate: {g!r}", code == 2)
        code, _, _ = run(f"{g} merge --ff-only karta/x/integration", MREC, {}, tip=TIP)
        check(f"a landing through git by path meets the landing gate: {g!r}", code == 2)
        code, _, _ = run(f"{g} merge --no-ff --no-edit karta/x/integration", {}, CFG, env=E, tip=TIP, helper_=stale)
        check(f"a merge through git by path meets the merge review gate: {g!r}", code == 2)
    for g in ("/usr/bin/git", "./git", "bin/git"):
        code, msg, _ = run(f"{g} commit -m x", PLAIN, ON)
        check(f"an unquoted git by path passes the grammar with a fresh record: {g!r}", code == 0, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {g} merge --no-ff --no-edit karta/x/integration", MREC, ON, tip=TIP)
        check(f"an approved merge through git by path lands with a fresh record: {g!r}", code == 0, msg)
    check("a word that merely ends in git is not git", not is_commit_command("/usr/bin/mygit commit -m x")
          and not is_commit_command("gitx commit -m x") and not is_commit_command("git.sh commit -m x"))
    check("the git directory itself is not the command", not is_commit_command("/usr/bin/git/ commit -m x"))

    # (12) approval and the skip hatch are decided per segment
    FAKE_FIRST = f"{LAND_VAR}=1 {SKIP_VAR}=1 git merge --ff-only karta/fake/integration; {MERGE}"
    check("every landing segment is reported", [r for r, _ in merge_invocations(FAKE_FIRST)] == ["karta/fake/integration", "karta/x/integration"])
    code, msg, _ = run(FAKE_FIRST, MREC, {}, tip=TIP)
    check("an approved first merge does not approve the second one", code == 2 and "karta/x/integration" in msg, msg)
    code, _, _ = run(f"{MERGE}; {LAND_VAR}=1 git merge --ff-only karta/fake/integration", MREC, {}, tip=TIP)
    check("an approved second merge does not approve the first one", code == 2)
    code, _, _ = run(f"{LAND_VAR}=1 {MERGE}; {LAND_VAR}=1 git merge --ff-only karta/y/integration", MREC, {}, tip=TIP)
    check("two landings each carrying the approval pass the landing gate", code == 0)
    code, _, _ = run(FAKE_FIRST, MREC, {}, env=E, tip=TIP)
    check("the approval in the environment covers every segment", code == 0)
    code, _, _ = run(FAKE_FIRST, MREC, {}, tip=TIP, cur="feature")
    check("off the default branch neither segment is a landing", code == 0)
    HEREDOC = f"cat <<EOF\n{LAND_VAR}=1 {MERGE}\nEOF\n{MERGE}"
    code, _, _ = run(HEREDOC, MREC, {}, tip=TIP)
    check("a fake approved merge inside a heredoc body does not approve the real merge after it", code == 2)
    code, _, _ = run(f"{MERGE}\ncat <<EOF\n{LAND_VAR}=1 {MERGE}\nEOF", MREC, {}, tip=TIP)
    check("a fake approved merge inside a heredoc body does not approve the real merge before it", code == 2)
    code, _, _ = run(f"{LAND_VAR}=1 {MERGE}\ncat <<EOF\n{MERGE}\nEOF", MREC, {}, tip=TIP)
    check("an unapproved merge line inside a heredoc body still denies (over-denial, documented)", code == 2)
    SKIP_SECOND = f"git commit -m x && {SKIP_VAR}=1 git commit -m y"
    check("the hatch on one of two commits is not the hatch for both", not has_skip_prefix(SKIP_SECOND)
          and not has_skip_prefix(f"{SKIP_VAR}=1 git commit -m x && git commit -m y"))
    check("the hatch on every commit is the hatch", has_skip_prefix(f"{SKIP_VAR}=1 git commit -m x && {SKIP_VAR}=1 git commit -m y"))
    code, _, _ = run(SKIP_SECOND, NOLEDGER, ON, helper_=stale)
    check("a skip on the second commit leaves the first one gated (the grammar denies the pair)", code == 2)
    code, _, _ = run(f"{SKIP_VAR}=1 git commit -m x && git commit -m y", NOLEDGER, ON, helper_=stale)
    check("a skip on the first commit leaves the second one gated", code == 2)
    code, _, _ = run(f"{SKIP_VAR}=1 git commit -m x; {SKIP_VAR}=1 git commit -m y", NOLEDGER, ON, helper_=stale)
    check("a skip on both commits skips", code == 0)
    code, _, _ = run(SKIP_SECOND, NOLEDGER, ON, env={SKIP_VAR: "1"}, helper_=stale)
    check("the hatch in the environment covers every segment", code == 0)
    code, _, _ = run(f"{SKIP_VAR}=1 git commit -m x && env -S 'git commit -m y'", NOLEDGER, ON, helper_=stale)
    check("a skip on a plain commit does not cover a hidden commit beside it", code == 2)

    # (13) only a genuinely unborn HEAD reads as no config
    code, msg, _ = run("git commit -m x", cfgtree(ONB, ONB, ONB), None, headfail=True)
    check("rev-parse HEAD failing while the branch resolves is a denial, not an absent config", code == 2 and "config" in msg, msg)
    code, msg, _ = run("git commit -m x", {BP: {"index": B1, "work": B1}}, None, headfail=True)
    check("the same failure with nothing to gate is still a denial — the switch could not be read", code == 2 and "config" in msg, msg)
    code, msg, _ = run("git commit -m x", {BP: {"index": B1, "work": B1}}, None, unborn=b"fatal: bad object HEAD", gitdirfail=True)
    check("a repository that does not open is a denial, never an absent config", code == 2 and "config" in msg, msg)
    code, msg, _ = run("git commit -m x", {BP: {"index": B1, "work": B1}}, None, unborn=b"fatal: bad object HEAD")
    check("a genuinely unborn HEAD still reads as no config", code == 0, msg)
    code, msg, _ = run("git commit -m x", cfgtree(ONB, ONB, ONB), None, headfail=True, cur="feature")
    check("a git failure on rev-parse HEAD fails closed on any branch", code == 2, msg)

    # (14) a pathspec commit gates only the binders it names; --include adds the staged ones
    STAGED_Y = {**PLAIN, YP: {"head": B0, "index": B2, "work": B2}, YR: {"head": rec(H0), "index": rec(H0), "work": rec(H0)}}
    X_ONLY = "git commit .karta/binders/x.json .karta/roundtable/x.json .karta/roundtable/x.rounds.json -m x"
    code, msg, _ = run(X_ONLY, STAGED_Y, ON)
    check("a pathspec commit of binder x is not denied by an unrelated staged binder y", code == 0, msg)
    git_, rf_, isl_, _ = make(STAGED_Y)
    check("binders(): a pure pathspec commit lists only the binders the pathspec names",
          Sources(parse_commit(tokenize(X_ONLY)[2:]), git_, rf_, isl_).binders() == [BP])
    check("binders(): --include lists the staged binder beside the named one",
          Sources(parse_commit(tokenize(f"git commit --include .karta/binders/x.json -m x")[2:]), git_, rf_, isl_).binders() == [BP, YP])
    check("binders(): a plain commit lists every staged binder",
          Sources(parse_commit(tokenize("git commit -m x")[2:]), git_, rf_, isl_).binders() == [BP, YP])
    check("binders(): -a lists every staged binder too",
          Sources(parse_commit(tokenize("git commit -a -m x")[2:]), git_, rf_, isl_).binders() == [BP, YP])
    code, _, _ = run("git commit -m x", STAGED_Y, ON)
    check("a plain commit is still gated on the staged binder y", code == 2)
    code, _, _ = run(f"git commit --include .karta/binders/x.json .karta/roundtable/x.json .karta/roundtable/x.rounds.json -m x", STAGED_Y, ON)
    check("--include with a pathspec still gates the staged binder y", code == 2)
    code, _, _ = run("git commit -a -m x", STAGED_Y, ON)
    check("-a still gates the staged binder y", code == 2)
    code, _, _ = run("git commit .karta -m x", STAGED_Y, ON)
    check("a pathspec that names y gates y", code == 2)
    code, _, _ = run("git commit .karta/binders/y.json -m x", STAGED_Y, ON)
    check("a pathspec commit of y alone is gated on y and denied", code == 2)

    # (15) the landing gate reads the positional ref only
    TOPIC = 'git merge -m "closes karta/x/integration" feature/topic'
    check("a ref mentioned in the message of a topic merge is not a landing", merge_invocation(TOPIC) is None and merged_integration_ref(TOPIC) is None)
    code, _, _ = run(TOPIC, MREC, {}, tip=TIP)
    check("the landing gate does not fire on a message mention", code == 0)
    code, _, _ = run(TOPIC, MREC, ON, env=E, tip=TIP, helper_=stale)
    check("the merge review gate does not fire on a message mention", code == 0)
    for cmd in ("git merge --no-ff -m 'about karta/x/integration' feature/topic", "git merge --message=karta/x/integration feature/topic",
                "git merge -mkarta/x/integration feature/topic", "git merge --no-ff --no-edit feature/topic -- ",
                "git merge -s ours -m karta/x/integration feature/topic", "git merge -X theirs feature/topic"):
        check(f"not a landing: {cmd!r}", merge_invocation(cmd) is None, str(merge_invocation(cmd)))
    for cmd in (MERGE, 'git merge --ff-only "karta/x/integration"', "git merge --ff-only 'karta/x/integration'",
                "git merge --no-ff -m 'note' -- karta/x/integration", "git merge -m x karta/x/integration",
                "git merge --no-ff --no-edit karta/x/integration", "git merge --squash karta/x/integration",
                "git merge -s ours -X theirs --no-ff -m x karta/x/integration", "git merge -S karta/x/integration",
                "git merge --gpg-sign=key karta/x/integration", "git merge karta/x/integration"):
        check(f"a positional integration ref is a landing: {cmd!r}", merge_invocation(cmd) == ("karta/x/integration", False), str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires on the positional ref: {cmd!r}", code == 2)
    for cmd in ("git merge --unknown-opt karta/x/integration", "git merge --weird feature/topic -m karta/x/integration",
                "git merge -Z karta/x/integration", "git merge feature/topic karta/x/integration"):
        check(f"an undecidable positional with an integration ref mentioned fails closed: {cmd!r}",
              merge_invocation(cmd) == ("karta/x/integration", False), str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate denies the undecidable shape: {cmd!r}", code == 2)
        code, _, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"the review grammar denies the undecidable shape too: {cmd!r}", code == 2)
    check("an undecidable shape without any integration ref is not a landing",
          merge_invocation("git merge --unknown-opt feature/topic") is None)

    # (16) git's optional-argument options carry their value attached only: the next word is the ref
    IREF = ("karta/x/integration", False)
    for cmd in ("git merge --gpg-sign karta/x/integration", "git merge --gpg-sign=KEY karta/x/integration",
                "git merge -S karta/x/integration", "git merge -SKEY karta/x/integration", "git merge --log karta/x/integration",
                "git merge --log=5 karta/x/integration", "git merge --no-ff --gpg-sign --no-edit karta/x/integration",
                "git merge -S --no-ff -m x karta/x/integration", "git merge --no-verify karta/x/integration",
                "git merge --cleanup=strip karta/x/integration", "git merge --cleanup strip karta/x/integration",
                "git merge --into-name main karta/x/integration"):
        check(f"an optional-attached option leaves the ref positional: {cmd!r}", merge_invocation(cmd) == IREF, str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires past the optional-attached option: {cmd!r}", code == 2)
    for cmd in ("git merge --gpg-sign feature/topic", "git merge -S feature/topic", "git merge -SKEY feature/topic",
                "git merge --cleanup karta/x/integration feature/topic", "git merge --into-name karta/x/integration feature/topic"):
        check(f"a topic merge past an optional-attached or valued option is not a landing: {cmd!r}", merge_invocation(cmd) is None, str(merge_invocation(cmd)))
    for cmd in ("git merge --no-ff --no-edit -S karta/x/integration", "git merge --no-ff --no-edit -SKEY karta/x/integration",
                "git merge --no-ff --no-edit --gpg-sign karta/x/integration", "git merge --gpg-sign=KEY --no-ff --no-edit karta/x/integration",
                "git merge --no-gpg-sign --no-ff --no-edit karta/x/integration"):
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"the merge grammar accepts a signed merge with a fresh record: {cmd!r}", code == 0, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP, helper_=stale)
        check(f"the merge grammar still gates a signed merge on the record: {cmd!r}", code == 2, msg)
    code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff --no-edit -S KEY karta/x/integration", MREC, ON, tip=TIP)
    check("a separate word after -S is a second ref to the grammar (git reads it so), and is denied", code == 2, msg)
    code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff --no-edit --gpg-sign=KEY karta/x/integration", MREC, ON, tip=TIP, helper_=stale)
    check("--gpg-sign=KEY is not read as a spelling that skips the review", code == 2, msg)
    for cmd in ("git commit -S -m x .karta", "git commit -SKEY -m x .karta", "git commit --gpg-sign -m x .karta",
                "git commit --gpg-sign=KEY -m x .karta", "git commit --no-gpg-sign -m x .karta", "git commit -S .karta -m x"):
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"the commit grammar accepts a signed commit with a fresh record: {cmd!r}", code == 0, msg)
        code, msg, _ = run(cmd, PLAIN, ON, helper_=stale)
        check(f"the commit grammar still gates a signed commit on the record: {cmd!r}", code == 2, msg)
    git_, rf_, isl_, _ = make(PLAIN)
    check("a separate word after commit -S is a pathspec to git, and the hook resolves it as one",
          commit_source(LP, "git commit -S KEY -m x", git_, rf_, isl_) == "HEAD" and commit_source(LP, "git commit -SKEY -m x", git_, rf_, isl_) == "index")
    for cmd in ("git commit --cleanup=strip -m x .karta", "git commit --cleanup strip -m x .karta", "git commit --log -m x .karta"):
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"an option outside the commit whitelist is still denied by name: {cmd!r}", code == 2 and "recognises" in msg, msg)

    # (17) the full-ref spellings of an integration branch are the same ref
    FULL = ("refs/heads/karta/x/integration", "refs/remotes/origin/karta/x/integration", "refs/remotes/fork-1/karta/x/integration",
            "origin/karta/x/integration", "upstream/karta/x/integration", "fork-1/karta/x/integration")
    for full in FULL:
        for cmd in (f"git merge {full}", f"git merge --no-ff {full}", f"git merge --ff-only {full}", f"git merge --no-ff --no-edit {full}",
                    f"git merge -m x {full}", f'git merge "{full}"', f"git merge --no-ff -- {full}"):
            check(f"a full-ref spelling is a landing: {cmd!r}", merge_invocation(cmd) == (full, False), str(merge_invocation(cmd)))
            check(f"a full-ref spelling is the merge gate's ref: {cmd!r}", merged_integration_ref(cmd) == full)
            code, msg, _ = run(cmd, MREC, {}, tip=TIP)
            check(f"the landing gate fires on a full-ref spelling: {cmd!r}", code == 2 and full in msg, msg)
            code, _, _ = run(cmd, MREC, {}, tip=TIP, cur="feature")
            check(f"off the default branch a full-ref spelling is not a landing: {cmd!r}", code == 0)
        code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff --no-edit {full}", MREC, ON, tip=TIP)
        check(f"an approved full-ref merge with a fresh record passes both gates: {full!r}", code == 0, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff --no-edit {full}", MREC, ON, tip=TIP, helper_=stale)
        check(f"the merge review gate fires on a full-ref spelling: {full!r}", code == 2 and "review" in msg.lower(), msg)
        code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff --no-edit {full}", {}, ON, tip=TIP)
        check(f"a full-ref merge with no record in HEAD is denied: {full!r}", code == 2, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 git merge --no-ff {full}", MREC, ON, tip=TIP)
        check(f"a full-ref merge without --no-edit or -m is denied like the short one: {full!r}", code == 2 and "--no-edit" in msg, msg)
        check(f"an undecidable shape mentioning a full-ref spelling fails closed: {full!r}",
              merge_invocation(f"git merge --unknown-opt {full}") == (full, False))
    check("a full-ref mention inside a message is not a landing",
          merge_invocation('git merge -m "refs/heads/karta/x/integration" feature/topic') is None
          and merge_invocation('git merge -m "origin/karta/x/integration" feature/topic') is None)
    check("the remote shorthand is read whole, not as its karta/ tail",
          merge_invocation("git merge origin/karta/x/integration") == ("origin/karta/x/integration", False)
          and merge_invocation("git merge --unknown-opt origin/karta/x/integration") == ("origin/karta/x/integration", False)
          and merge_invocation("git merge --unknown-opt refs/heads/karta/x/integration") == ("refs/heads/karta/x/integration", False))
    check("the shorthand also matches an oddly named local branch — the accepted over-denial",
          merge_invocation("git merge heads/karta/x/integration") == ("heads/karta/x/integration", False)
          and merge_invocation("git merge tags/karta/x/integration") == ("tags/karta/x/integration", False))
    for cmd in ("git merge --no-ff --no-edit origin/karta/x/integration", "git merge --ff-only upstream/karta/x/integration"):
        check(f"the review merge gate reads the shorthand: {cmd!r}", merged_integration_ref(cmd) == cmd.split()[-1])
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP, helper_=stale)
        check(f"the review merge gate fires on the shorthand: {cmd!r}", code == 2 and "review" in msg.lower(), msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"the review merge gate resolves the shorthand to the same tip and passes: {cmd!r}", code == 0, msg)
    for cmd in ("git merge refs/tags/karta/x/integration", "git merge refs/heads/karta/x/integration2", "git merge refs/heads/karta/integration",
                "git merge refs/remotes/karta/x/integration", "git merge origin/x/karta/x/integration", "git merge remotes/origin/karta/x/integration",
                "git merge refs/tags/v1", "git merge refs/tags/karta/x/integration", "git merge origin/karta/x/integration2",
                f"git merge {TIP}", f"git merge origin/{TIP}"):
        check(f"not an integration branch spelling (a raw SHA stays a documented bypass): {cmd!r}", merge_invocation(cmd) is None, str(merge_invocation(cmd)))
        code, _, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate does not fire on it: {cmd!r}", code == 0)

    # (18) wrapper lexing: clusters, GNU long prefixes, permuted options, legacy `-`, wrappers by path
    WRAP2 = ("env -iu X ", "env --un X ", "env --unset X ", "env --u X ", "env F=1 -i ", "env F=1 -u X G=2 ", "env - ", "env - F=1 ",
             "env -i - ", "exec -ca spoof ", "exec -c -a spoof ", "exec -cl ", "exec -a spoof ", "/usr/bin/env ", "/usr/bin/env -i ",
             "/usr/bin/timeout 5 ", "/usr/bin/timeout -k 2 5 ", "/usr/bin/nice -n 5 ", "/usr/bin/time ", "/usr/bin/time -p ",
             "/usr/bin/time -o out ", "/usr/bin/time --output=out -f fmt ", "/usr/bin/stdbuf -oL ", "/usr/bin/nohup ",
             "./env ", "timeout --kill-after=2 --sig KILL 5 ", "timeout -k2 -sKILL 5 ", "timeout -pk 2 5 ", "timeout --fore 5 ",
             "nice -5 ", "nice --adj 5 ", "nice --adjustment=5 ", "stdbuf -oL -eL ", "stdbuf -o L -i 0 ", "stdbuf --out=L ",
             "env -C . -u X ", "env -uX ", "env -Cx/ -iuX ", "env --chdir . ", "env --ch . ", "env --null ", "env -0i ",
             "env --default-signal ", "env --default-signal=INT ", "env --ignore-environment ", "env -a spoof ", "env --argv0 spoof ",
             "env --argv0=spoof ", "env --argv spoof ", "env -ia spoof ", "env -aspoof ", "env -a spoof F=1 ", "env F=1 -a spoof ",
             "env -- ", "env -i -- F=1 ", "env -u X -- ", "command -pv ", "time -p env -i ", "nohup /usr/bin/env -u X ")
    for w in WRAP2:
        check(f"the wrapper is read through to a commit: {w!r}", is_commit_command(f"{w}git commit -m x") and hidden_invocation(f"{w}git commit -m x") is None)
        check(f"the wrapper is read through to a merge: {w!r}", is_merge_command(f"{w}{MI}") and merge_invocation(f"{w}{MI}") == IREF, str(merge_invocation(f"{w}{MI}")))
        code, _, _ = run(f"{w}git commit -m x", PLAIN, ON, helper_=stale)
        check(f"the wrapped commit meets the review gate: {w!r}", code == 2)
        code, _, _ = run(f"{w}{MI}", MRECQ, ON, tip=TIP)
        check(f"the wrapped merge meets the landing gate: {w!r}", code == 2)
        code, _, _ = run(f"{w}{MI}", {}, CFG, env=E, tip=TIP, helper_=stale)
        check(f"the wrapped merge meets the merge review gate: {w!r}", code == 2)
        check(f"the approval prefix on the wrapped segment is recognised: {w!r}", merge_invocation(f"{LAND_VAR}=1 {w}{MI}") == ("karta/x/integration", True))
    for cmd in ("env -iu git commit -m x", "env --un git commit -m x", "env -u git commit -m x", "exec -a git commit -m x", "exec -ca git commit -m x",
                "timeout -k git commit -m x", "timeout -s git commit -m x", "stdbuf -o git commit -m x", "nice -n git commit -m x",
                "/usr/bin/time -o git commit -m x", "env -C git commit -m x", "env --chdir git commit -m x", "env -a git commit -m x"):
        check(f"a valued wrapper option's next word is its value, not the command: {cmd!r}", not is_commit_command(cmd))
    for cmd in ("env -a x printf ok", "env -a harmless printf ok", "env --argv0=x printf ok", "env -ia spoof true", "env - true",
                "env -iu X sh -c 'true'", "/usr/bin/env python3 -c 1", "exec -ca spoof ls", "env -a spoof echo git commit -m x",
                "/usr/bin/timeout 5 sleep 1", "env --un X printf ok", "env F=1 -i printf ok"):
        check(f"a wrapped command that is not git is harmless: {cmd!r}", not is_commit_command(cmd) and not is_merge_command(cmd)
              and hidden_invocation(cmd) is None and merge_invocation(cmd) is None)
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"and passes the hook untouched: {cmd!r}", code == 0, msg)
    check("a wrapper by path is matched on its basename, not on any path ending in the name",
          not is_commit_command("/usr/bin/envy git commit -m x") and not is_commit_command("env/ git commit -m x")
          and not is_commit_command("myenv git commit -m x"))
    check("an ambiguous GNU prefix is not a valued option (env would refuse it): the next word is still read",
          is_commit_command("env --i git commit -m x"))
    check("a legacy `-` is env's alone: to timeout it is the duration positional, to nice it is the command word",
          is_commit_command("timeout - git commit -m x") and not is_commit_command("nice - git commit -m x"))

    # (19) redirections are not words git sees
    for cmd in ("git merge 2>&1 karta/x/integration", "git merge &>log karta/x/integration", "git merge &>>log karta/x/integration",
                "git merge karta/x/integration>log", "git merge>log karta/x/integration", "git merge karta/x/integration 2>&1",
                "git merge karta/x/integration >log", "git merge karta/x/integration >>log 2>&1", "git merge karta/x/integration > log",
                "git merge karta/x/integration 2> err", "git merge --no-ff >out karta/x/integration", "git merge <in karta/x/integration",
                "git merge karta/x/integration >|log", "git merge karta/x/integration >&2", "git merge karta/x/integration 0<&3",
                "git merge karta/x/integration <<<x", "git merge karta/x/integration 3>&-", "git merge karta/x/integration <>f",
                "git merge --no-ff --no-edit karta/x/integration > /dev/null 2>&1", "git merge 2>/dev/null karta/x/integration",
                "git merge --no-ff -m x karta/x/integration&>log", "git merge --no-ff 1>log karta/x/integration"):
        check(f"a redirection does not hide the ref: {cmd!r}", merge_invocation(cmd) == IREF, str(merge_invocation(cmd)))
        check(f"a redirection does not hide the merge from the review detector: {cmd!r}", is_merge_command(cmd) and merged_integration_ref(cmd) == "karta/x/integration")
        code, msg, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires past a redirection: {cmd!r}", code == 2 and "human" in msg, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"the review grammar still denies the redirection itself: {cmd!r}", code == 2 and "redirection" in msg, msg)
        code, _, _ = run(cmd, MREC, {}, tip=TIP, cur="feature")
        check(f"off the default branch it is not a landing: {cmd!r}", code == 0)
    for cmd in ("git commit 2>&1 -m x", "git commit -m x 2>&1", "git commit -m x >log", "git commit -m x &>log", "git commit >log -m x",
                "git commit -m x 2>err >out", "git commit -m x >&2", "git commit -m x <in"):
        check(f"a redirection does not hide a commit: {cmd!r}", is_commit_command(cmd))
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"the review grammar denies the redirection on a commit: {cmd!r}", code == 2 and "redirection" in msg, msg)
    check("the & of 2>&1 does not split the segment", [r for r, _ in merge_invocations("git merge 2>&1 karta/x/integration")] == ["karta/x/integration"]
          and merge_invocation("true 2>&1 && git merge karta/x/integration") == IREF)
    check("the & of &> does not split the segment", merge_invocation("git merge &>log karta/x/integration") == IREF
          and merge_invocation("true &>log && git merge karta/x/integration") == IREF)
    check("a bare & still splits", merge_invocation("sleep 1 & git merge karta/x/integration") == IREF
          and merge_invocation("git merge karta/x/integration & true") == IREF)
    for cmd in ("true \\>& git merge karta/x/integration", "true \\>| git merge karta/x/integration", "true '>'& git merge karta/x/integration",
                "true '>'| git merge karta/x/integration", 'true ">"& git merge karta/x/integration', "true \\<& git merge karta/x/integration",
                "echo a\\>&git merge karta/x/integration", "true \\>&& git merge karta/x/integration", "true \\>|| git merge karta/x/integration"):
        check(f"an escaped or quoted redirection char is a literal; the & / | after it is an operator: {cmd!r}",
              merge_invocation(cmd) == IREF and is_merge_command(cmd), str(_segments(cmd, bare_amp=True)))
        code, msg, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires after an escaped redirection char: {cmd!r}", code == 2 and "human" in msg, msg)
    for cmd in ("true \\>& git commit -m x", "true \\>| git commit -m x", "true '>'& git commit -m x", "true '<'| git commit -m x",
                "true \\<& git commit -m x"):
        check(f"an escaped or quoted redirection char does not hide a commit: {cmd!r}", is_commit_command(cmd), str(_segments(cmd, bare_amp=True)))
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"the review gate meets the commit after an escaped redirection char: {cmd!r}", code == 2, msg)
    for cmd in ("git merge 2>&1 karta/x/integration", "git merge >&2 karta/x/integration", "git merge <&0 karta/x/integration",
                "git merge >|log karta/x/integration", "git merge &>log karta/x/integration", "git merge 2>&1 >|log karta/x/integration"):
        check(f"a real redirection is still one segment: {cmd!r}", _segments(cmd, bare_amp=True) == [cmd]
              and merge_invocation(cmd) == IREF, str(_segments(cmd, bare_amp=True)))
    check("a real redirection then a bare & still splits", _segments("true 2>&1 & git merge karta/x/integration", bare_amp=True)
          == ["true 2>&1 ", " git merge karta/x/integration"])
    check("an escaped backslash before > is not an escape of the >", _segments("true \\\\>&1 & true", bare_amp=True) == ["true \\\\>&1 ", " true"])
    check("a quoted redirection is text, not an operator", merge_invocation('git merge -m "a > b 2>&1" karta/x/integration') == IREF
          and merge_invocation("git merge -m '&>log' karta/x/integration") == IREF)
    check("a redirection target is not a ref", merge_invocation("git merge feature/topic >karta/x/integration") is None
          and merge_invocation("git merge >karta/x/integration feature/topic") is None
          and merge_invocation("git merge feature/topic 2>karta/x/integration") is None)
    check("a redirection target that is an integration ref with no positional is not a landing",
          merge_invocation("git merge >karta/x/integration") is None)
    check("the redirect lexer: fd digits fold into the operator, a non-digit word does not",
          [(w.text, w.redirect) for w in _loose_words("a 2>&1 b>c 3<&- >|d &>>e f>g")]
          == [("a", 0), ("2>&1", 2), ("b", 0), (">", 1), ("c", 0), ("3<&-", 2), (">|", 1), ("d", 0), ("&>>", 1), ("e", 0), ("f", 0), (">", 1), ("g", 0)])
    check("the redirect lexer: a quoted digit is a word, not an fd", [w.text for w in _loose_words("'2'>x")] == ["2", ">", "x"])
    check("a wrapped, redirected merge is still read", merge_invocation("env -i git merge 2>&1 karta/x/integration") == IREF
          and merge_invocation("timeout 5 git merge karta/x/integration >log") == IREF)
    check("a heredoc operator's delimiter is its target", not is_commit_command("cat <<EOF") and not is_merge_command("cat <<'EOF'"))

    # (19b) env sets any name without `=`: the command after a non-identifier assignment runs
    for a in ("X-Y=1", "1X=1", "a.b=1", "X-Y=1 1X=2 a.b=3", "-i X-Y=1", "X-Y=1 -u FOO", "-- X-Y=1", "-- -x=1", "-i -- 1X=1",
              "@=1", "X-Y=", "a.b+=1"):
        cmd = f"env {a} git merge --ff-only karta/x/integration"
        check(f"env's non-identifier assignment does not hide a landing: {cmd!r}", merge_invocation(cmd) == IREF, str(merge_invocation(cmd)))
        code, msg, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires through it: {cmd!r}", code == 2 and "human" in msg, msg)
        check(f"the review merge gate reads through it: {cmd!r}", merged_integration_ref(cmd) == "karta/x/integration")
        cmd = f"env {a} git commit -m x"
        check(f"env's non-identifier assignment does not hide a commit: {cmd!r}", is_commit_command(cmd))
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"the review gate meets the commit through it: {cmd!r}", code == 2 and "hides" not in msg, msg)
    for cmd in ("env --unset=FOO git commit -m x", "env --chdir=. git commit -m x", "env -u=FOO git commit -m x", "env --u=FOO git merge karta/x/integration"):
        check(f"an env option with = is still an option, not an assignment: {cmd!r}",
              is_commit_command(cmd) or merge_invocation(cmd) == IREF)
    check("env -S with a = word is still the hiding option", hidden_invocation("env X-Y=1 -S 'git commit -m x'") is not None)
    check("the shell-prefix assignment keeps the identifier rule",
          not is_commit_command("X-Y=1 git commit -m x") and not is_commit_command("1X=1 git commit -m x")
          and is_commit_command("X_Y=1 git commit -m x"))
    check("env assignment words are the segment's assigns",
          [w.text for w in _leading_command("env X-Y=1 1X=2 git commit -m x")[0]] == ["X-Y=1", "1X=2"])

    # (20) env -a / --argv0 renames argv[0] only: the command after it is read
    for cmd in ("env -a spoof git merge --ff-only karta/x/integration", "env --argv0=spoof git merge --ff-only karta/x/integration",
                "env --argv0 spoof git merge --ff-only karta/x/integration", "env -ia spoof git merge karta/x/integration",
                "env -aspoof git merge --no-ff --no-edit karta/x/integration", "env -a git git merge karta/x/integration"):
        check(f"env -a does not hide a landing: {cmd!r}", merge_invocation(cmd) == IREF and hidden_invocation(cmd) is None, str(merge_invocation(cmd)))
        code, msg, _ = run(cmd, MREC, {}, tip=TIP)
        check(f"the landing gate fires through env -a: {cmd!r}", code == 2 and "human" in msg, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, {}, tip=TIP)
        check(f"an approved env -a landing passes with the config off: {cmd!r}", code == 0, msg)
        code, msg, _ = run(f"{LAND_VAR}=1 {cmd}", MREC, ON, tip=TIP)
        check(f"with the config on the grammar denies the wrapper, not a hidden command: {cmd!r}", code == 2 and "hides" not in msg, msg)
    for cmd in ("env -a spoof git commit -m x", "env --argv0=spoof git commit -m x", "env -ia spoof git commit -m x", "env -aspoof git commit -m x"):
        check(f"env -a does not hide a commit: {cmd!r}", is_commit_command(cmd) and hidden_invocation(cmd) is None)
        code, msg, _ = run(cmd, PLAIN, ON)
        check(f"the review gate meets the commit and denies the wrapper by the grammar: {cmd!r}", code == 2 and "hides" not in msg, msg)
        code, _, _ = run(cmd, PLAIN, {})
        check(f"with the config off an env -a commit is not gated: {cmd!r}", code == 0)
    check("env -S still fails closed beside env -a", hidden_invocation("env -a spoof -S 'git commit -m x'") == "-S"
          and hidden_invocation("env -aS 'git commit -m x'") is None and is_commit_command("env -aS git commit -m x"))

    # the standalone resolver
    git, rf, isl, _ = make(PLAIN)
    check("commit_source: a plain commit reads the index", commit_source(LP, "git commit -m x", git, rf, isl) == "index")
    check("commit_source: a pathspec that excludes the path reads HEAD", commit_source(LP, "git commit .karta/binders/x.json -m x", git, rf, isl) == "HEAD")
    check("commit_source: -a reads the worktree", commit_source(LP, "git commit -a -m x", git, rf, isl) == "worktree")
    check("commit_source: a denied mode is reported", commit_source(LP, "git commit -p -m x", git, rf, isl).startswith("deny:"))

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    code, message = hook_main(sys.stdin.read(), os.environ, _real_git, _real_helper, None)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
