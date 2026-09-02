# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Verify the invariant register's carriers — the file, the phrase, the script.

docs/conventions/invariants.md names each invariant's carriers as a file plus a short
distinctive phrase quoted from the woven sentence itself, so that rewording an invariant's
prose is a two-file diff a reviewer can see. Until this script existed nothing checked
that pairing: the exposure showed up the day the register landed, when a sibling delivery
rewrote a carrier paragraph while the register was in flight and only a lucky merge
conflict surfaced it (backlog item 21, register INV-20).

WHAT IT PROVES, and what it does not. For every entry: the carrier file exists, every
quoted phrase is present in the carrier file that segment names, and every enforcement
script named in the Carriers bullet exists. That is PRESENCE. Whether a claim's WORDING
still matches what its enforcement delivers is not decidable by grep and stays with the
reviewer of the branch the change arrives on.

IT READS THE WORKING TREE of --root, like every other gate in the commit suite. Bytes a
partial staging would commit can differ from what was checked; that bound is named here,
in the register's own entry, and in the hook's doc comment rather than implied closed.

EXIT CODES, pinned — the hook in scripts/hooks/precommit_gate.py denies ONLY on 1:
  0  every entry passes
  1  a verification or parse failure, each named with its entry id and line number.
     A --root whose tree carries no docs/conventions/invariants.md is one of these:
     a missing register is a named failure, never a silent pass and never a crash.
  2  an internal crash. A crash never blocks anyone (INV-21) — but a malformed or
     drifted register denying by name IS the design: the register is doctrine, and a
     broken doctrine file blocking a commit is what a drifted mirror already does in
     this suite.

THE GRAMMAR, pinned — and therefore the register's authoring constraint. A shape this
parser does not accept is a named parse failure rather than a guess, so extending what
the register may say means extending this grammar in the same diff.

  Entry heading    A line beginning '### INV-'. It must read exactly
                   '### INV-<digits> · <title>'; a near miss (a hyphen for the middot,
                   a non-numeric id) is a parse failure, so near misses fail loudly
                   instead of vanishing from the entry list. A duplicate id is a
                   parse failure too.
  Continuations    An indented line following a bullet is joined to that bullet with a
                   single space before anything is parsed.
  Status bullet    The first bullet whose bold token (**...**) is exactly one of
                   'enforced', 'partial', 'prose', 'prose by design', matched
                   longest-first. Within a bullet the FIRST matching bold token wins, so
                   a status note that later bolds another status word for contrast still
                   grades by the word it opened with. An entry with no status bullet is a
                   parse failure.
  Carriers bullet  The bullet beginning 'Carriers:'. Its value splits into segments on
                   '; ', but ONLY where the split point sits outside double quotes and
                   outside parentheses — a semicolon inside a quoted phrase or a
                   parenthetical is part of the text, not a boundary. An entry with no
                   Carriers bullet is a parse failure.
  Segment          Exactly one carrier file token, plus zero or more quoted phrases.
                   Zero or two-plus carrier tokens is a parse failure. A script-pattern
                   token counts as the segment's carrier when it is the only file-shaped
                   token in the segment — INV-20's own carrier is exactly that shape.
                   Where an .md (or non-script .py) token stands beside a script token,
                   the former is the carrier and the latter is an enforcement reference.
  'none yet'       A Carriers value beginning 'none yet' names no file. That is a
                   structural pass for a 'prose' or 'prose by design' entry, and a named
                   VERIFICATION failure for an 'enforced' or 'partial' one — an enforced
                   claim with no carrier is the drift this checker exists to catch.

  Tokens           Repo-relative path-shaped tokens ending .md or .py, recognized inside
                   backticks or bare. A leading slash or a '..' segment is a parse
                   failure. Script tokens additionally match
                   (scripts|skills|benchmarks)/…/<name>.py.
  Extraction order Quoted phrases are extracted FIRST and masked; file and script tokens
                   are collected only from unmasked text, so a path inside a quoted
                   phrase is never a token. Backtick and markdown-link stripping applies
                   ONLY to token extraction — a quoted phrase is taken verbatim between
                   its quotes, backticks included.
  Phrase matching  Whitespace-normalized on both sides (every run of whitespace,
                   newlines included, collapses to one space) and case-sensitive.

SCRIPT SCOPE, narrowed deliberately. Script tokens are collected from the CARRIERS bullet
only, and their existence is verified only for entries whose status is 'enforced' or
'partial'. A Carriers bullet is a claim of PRESENT enforcement; a status bullet may name
future work, which is the register's own convention for a named remainder — so a partial
entry pointing at a not-yet-built script in its status note can never brick a commit.

  python3 check_invariant_register.py               # this tree, exit 0/1/2
  python3 check_invariant_register.py --root ../wt  # another tree
  python3 check_invariant_register.py --self-test   # fabricated registers, exit 0/1
"""
from __future__ import annotations
import argparse, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> repo root
REGISTER_REL = "docs/conventions/invariants.md"

# Longest-first, so a prefix can never shadow the longer word. The comparison below is
# exact equality, which makes the ordering belt-and-braces rather than the mechanism.
STATUS_WORDS = ("prose by design", "enforced", "partial", "prose")
NONE_YET = "none yet"

_HEADING_RE = re.compile(r"^### INV-(\d+) · (.+?)\s*$")
_HEADING_PREFIX = "### INV-"
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_QUOTE_RE = re.compile(r'"([^"]*)"')
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
# Path-shaped and ending .md or .py. A leading slash and '..' segments are matched on
# purpose: they must become NAMED failures, and a token this regex refused to see would
# instead vanish and take the segment's count down with it.
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(/?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:md|py))(?![A-Za-z0-9_-])")
_SCRIPT_RE = re.compile(r"^(?:scripts|skills|benchmarks)/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py$")


def _norm(text: str) -> str:
    """Whitespace-normalized: every run of whitespace, newlines included, is one space."""
    return re.sub(r"\s+", " ", text).strip()


class _Entry:
    def __init__(self, ident: str, title: str, line: int):
        self.id, self.title, self.line = ident, title, line
        self.bullets: list[list] = []  # [text, line]


def _parse_entries(text: str) -> tuple[list[_Entry], list[str]]:
    """(entries, parse failures) for the register's own structure: headings, ids, and
    the bullets under each entry with indented continuations already joined."""
    failures: list[str] = []
    entries: list[_Entry] = []
    cur: _Entry | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith(_HEADING_PREFIX):
            m = _HEADING_RE.match(raw)
            if m is None:
                failures.append(
                    f"line {n}: parse failure — an entry heading must read "
                    f"'### INV-<digits> · <title>'; found {raw.strip()!r}")
                cur = None
                continue
            cur = _Entry(f"INV-{m.group(1)}", m.group(2), n)
            entries.append(cur)
            continue
        if raw.startswith("#"):
            cur = None
            continue
        if cur is None:
            continue
        if raw.startswith("- "):
            cur.bullets.append([raw[2:].rstrip(), n])
        elif cur.bullets and raw[:1] in (" ", "\t") and raw.strip():
            cur.bullets[-1][0] += " " + raw.strip()

    seen: dict[str, int] = {}
    for e in entries:
        if e.id in seen:
            failures.append(f"{e.id} (line {e.line}): parse failure — duplicate entry id, "
                            f"already defined at line {seen[e.id]}; ids are never reused")
        else:
            seen[e.id] = e.line
    return entries, failures


def _status_of(entry: _Entry) -> str | None:
    for text, _line in entry.bullets:
        for bold in (b.strip() for b in _BOLD_RE.findall(text)):
            for word in STATUS_WORDS:  # longest-first
                if bold == word:
                    return word
    return None


def split_segments(value: str) -> list[str]:
    """A Carriers value as its segments. '; ' is a boundary only outside double quotes
    and outside parentheses — inside either it is text the author wrote."""
    segs: list[str] = []
    cur: list[str] = []
    in_quote = False
    depth = 0
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == ";" and not in_quote and depth == 0 and value[i + 1:i + 2] == " ":
            segs.append("".join(cur))
            cur = []
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch == "(":
            depth += 1
        elif not in_quote and ch == ")":
            depth = max(0, depth - 1)
        cur.append(ch)
        i += 1
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


def segment_parts(segment: str) -> tuple[list[str], list[str]]:
    """(quoted phrases, path tokens) for one segment, in the pinned extraction order:
    phrases first, then tokens from what the phrases did not cover."""
    phrases = _QUOTE_RE.findall(segment)
    masked = _QUOTE_RE.sub(lambda m: " " * (len(m.group(0))), segment)
    # Stripping applies to token extraction only; the phrases above were taken verbatim.
    masked = _LINK_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}", masked).replace("`", " ")
    return phrases, _TOKEN_RE.findall(masked)


def _carrier_and_scripts(tokens: list[str]) -> tuple[str | None, list[str], str]:
    """(carrier, script references, why-not) for one segment's tokens."""
    scripts = [t for t in tokens if _SCRIPT_RE.match(t)]
    plain = [t for t in tokens if t not in scripts]
    if len(plain) == 1:
        return plain[0], scripts, ""
    if not plain and len(scripts) == 1:
        return scripts[0], [], ""
    found = plain or scripts
    if not tokens:
        return None, [], "no carrier file token"
    return None, [], f"{len(found)} carrier file tokens ({', '.join(found)})"


def _phrase_present(haystack: str, phrase: str) -> bool:
    return _norm(phrase) in haystack


def check_register(root: Path) -> list[str]:
    """Every named failure this register produces against `root`'s working tree; an
    empty list is a pass. Never raises for a missing or unreadable register — that is
    itself a named failure, because a silent pass would be the worst answer of the three."""
    register = root / REGISTER_REL
    try:
        text = register.read_text(encoding="utf-8")
    except OSError as e:
        return [f"{REGISTER_REL}: the invariant register could not be read under "
                f"--root {root} ({e}); a register that is not there cannot be verified"]

    entries, failures = _parse_entries(text)
    if not entries:
        failures.append(f"{REGISTER_REL}: parse failure — no '### INV-<digits> · <title>' "
                        f"entries were found at all")

    cache: dict[str, str | None] = {}

    def body(rel: str) -> str | None:
        if rel not in cache:
            try:
                cache[rel] = _norm((root / rel).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                cache[rel] = None
        return cache[rel]

    for entry in entries:
        where = f"{entry.id} (line {entry.line})"
        status = _status_of(entry)
        if status is None:
            failures.append(f"{where}: parse failure — no status bullet; one bullet must carry "
                            f"exactly one of **{'**, **'.join(STATUS_WORDS)}** in bold")
        carriers = next(((t, n) for t, n in entry.bullets if t.startswith("Carriers:")), None)
        if carriers is None:
            failures.append(f"{where}: parse failure — no '- Carriers:' bullet")
            continue
        value, line = carriers[0][len("Carriers:"):].strip(), carriers[1]
        where = f"{entry.id} (line {line})"

        if value.startswith(NONE_YET):
            if status in ("prose", "prose by design"):
                continue  # a stated gap, named as one — the register's own convention
            failures.append(
                f"{where}: verification failure — an entry whose status is "
                f"**{status}** carries 'none yet' for its carriers; an enforced claim "
                f"with no carrier is exactly the drift this checker exists to catch")
            continue

        for segment in split_segments(value):
            phrases, tokens = segment_parts(segment)
            unsafe = [t for t in tokens
                      if t.startswith("/") or t == ".." or t.startswith("../") or "/../" in t]
            if unsafe:
                failures.append(
                    f"{where}: parse failure — carrier token {unsafe[0]!r} is not a "
                    f"repo-relative path (no leading slash, no '..' segments)")
                continue
            carrier, scripts, why = _carrier_and_scripts(tokens)
            if carrier is None:
                failures.append(
                    f"{where}: parse failure — segment {segment!r} has {why}; each "
                    f"'; '-separated Carriers segment names exactly one file")
                continue
            content = body(carrier)
            if content is None:
                failures.append(f"{where}: verification failure — carrier file {carrier} "
                                f"does not exist under --root {root}")
                continue
            for phrase in phrases:
                if not _phrase_present(content, phrase):
                    failures.append(
                        f"{where}: verification failure — the phrase {phrase!r} is not in "
                        f"its carrier {carrier}; reword the entry or the carrier, but not "
                        f"one without the other")
            if status in ("enforced", "partial"):
                for script in scripts:
                    if not (root / script).is_file():
                        failures.append(
                            f"{where}: verification failure — the enforcement script "
                            f"{script} named in Carriers does not exist under --root {root}, "
                            f"while the entry claims status **{status}**")
    return failures


# --- self-test ----------------------------------------------------------------------

_TREE = {
    "AGENTS.md": 'A doctrine file saying "the carried phrase" out loud.\n'
                 'It also says "alpha; beta" in one breath, and a phrase that\n'
                 'wraps across a line break for good measure.\n'
                 'Case matters: Enforcement Below The Agent.\n',
    "README.md": "A second carrier.\n",
    "scripts/real_gate.py": "print('gate')\n",
}


def _entry_text(ident="INV-1", title="A rule", status="enforced",
                carriers='AGENTS.md "the carried phrase"', heading=None) -> str:
    head = heading if heading is not None else f"### {ident} · {title}"
    return (f"{head}\nThe rule in one sentence.\n"
            f"- founding · **{status}** — the honesty column.\n"
            f"- Carriers: {carriers}\n\n")


def _fixture(tmp: Path, *entries: str, files: dict | None = None,
             register: str | None = None) -> Path:
    root = tmp / f"case{len(list(tmp.iterdir()))}"
    for rel, text in {**_TREE, **(files or {})}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if register is not None or entries:
        reg = root / REGISTER_REL
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(register if register is not None
                       else "# The invariant register\n\n" + "".join(entries))
    return root


def _run_self_test() -> int:
    import tempfile
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    def joined(root: Path) -> str:
        return " || ".join(check_register(root))

    with tempfile.TemporaryDirectory(prefix="cir-") as td:
        tmp = Path(td)

        # (1) the carrier file itself is gone
        root = _fixture(tmp, _entry_text(carriers='docs/absent.md "the carried phrase"'))
        out = joined(root)
        check("register-missing-carrier-file: a carrier file that is not in the tree is a named "
              "verification failure citing the entry and the path",
              check_register(root) and "docs/absent.md" in out and "INV-1" in out
              and "does not exist" in out, out)

        # (2) the file is there and the sentence it was quoting is not — the drift this
        # whole checker exists for. Its positive control is case (4)'s clean fixture.
        root = _fixture(tmp, _entry_text(carriers='AGENTS.md "a sentence nobody wrote"'))
        out = joined(root)
        check("register-phrase-not-found: a phrase absent from an existing carrier is a named "
              "verification failure quoting the phrase and the file it was claimed to be in",
              "a sentence nobody wrote" in out and "AGENTS.md" in out, out)

        # (3) an enforced entry naming a script that does not exist
        root = _fixture(tmp, _entry_text(
            carriers='AGENTS.md "the carried phrase" (enforced by scripts/absent.py)'))
        out = joined(root)
        check("register-missing-script: an enforced entry whose Carriers bullet names a "
              "non-existent enforcement script fails by name, while the .md beside it stays "
              "the segment's carrier", "scripts/absent.py" in out and "AGENTS.md" not in out, out)

        # (4) 'none yet' — a stated gap on a prose entry, and the same words on an enforced
        # one, which is a claim with nothing behind it
        ok_root = _fixture(tmp, _entry_text(status="prose",
                                            carriers="none yet — the statement of record."),
                           )
        bad_root = _fixture(tmp, _entry_text(status="partial",
                                             carriers="none yet — the statement of record."))
        out = joined(bad_root)
        check("register-none-yet-passes: 'none yet' is a structural pass on a prose entry and a "
              "named verification failure on a partial one — the same words, graded by the claim "
              "they sit under",
              check_register(ok_root) == [] and "none yet" in out and "partial" in out,
              f"{check_register(ok_root)} || {out}")

        # (5) an entry missing a required bullet, both ways
        no_carriers = _fixture(tmp, "### INV-1 · A rule\nSentence.\n- founding · **prose** — x.\n\n")
        no_status = _fixture(tmp, "### INV-1 · A rule\nSentence.\n- Carriers: README.md\n\n")
        check("register-parse-failure-named: an entry with no Carriers bullet and an entry with "
              "no status bullet each fail as a named parse failure citing the entry id and its "
              "line number, never a guess at what was meant",
              "INV-1 (line 3): parse failure — no '- Carriers:' bullet" in check_register(no_carriers)
              and any("no status bullet" in f and "INV-1 (line 3)" in f
                      for f in check_register(no_status)),
              f"{check_register(no_carriers)} || {check_register(no_status)}")

        # (6) a heading that ALMOST matches must fail loudly rather than drop out of the
        # entry list — a silently skipped entry is an unverified one
        root = _fixture(tmp, _entry_text(heading="### INV-9 - A rule"),
                        _entry_text(heading="### INV-nine · A rule"))
        out = joined(root)
        check("register-heading-near-miss-fails: '### INV-' lines that miss the pinned shape "
              "(a hyphen for the middot, a non-numeric id) are named parse failures at their own "
              "line, not entries quietly skipped",
              "line 3" in out and "line 8" in out and out.count("parse failure") >= 2, out)

        # (7) two entries claiming one id
        root = _fixture(tmp, _entry_text(ident="INV-1"), _entry_text(ident="INV-1"))
        out = joined(root)
        check("register-duplicate-id-fails: a repeated entry id is a named parse failure pointing "
              "at both lines — ids are never renumbered or reused",
              "duplicate entry id" in out and "line 3" in out and "line 8" in out, out)

        # (8) path safety: neither shape may be read, and neither may pass silently
        root = _fixture(tmp, _entry_text(carriers='/etc/passwd.md "x"'),
                        _entry_text(ident="INV-2", carriers='../outside.md "x"'))
        out = joined(root)
        check("register-path-safety-fails: a carrier token with a leading slash or a '..' segment "
              "is a named parse failure — the checker never reads outside the root it was given",
              "/etc/passwd.md" in out and "../outside.md" in out
              and out.count("repo-relative") == 2, out)

        # (9) a segment must name exactly one file: zero and two both fail
        root = _fixture(tmp, _entry_text(carriers='AGENTS.md "the carried phrase"; a bare clause'),
                        _entry_text(ident="INV-2", carriers="AGENTS.md and README.md together"))
        out = joined(root)
        check("register-segment-token-count-fails: a Carriers segment naming no file, and one "
              "naming two, are both named parse failures quoting the offending segment",
              "no carrier file token" in out and "2 carrier file tokens" in out, out)

        # (10) the split is quote- and paren-aware. The negative control is the same
        # semicolon moved outside both, which does split and leaves a token-less segment.
        safe = _fixture(tmp, _entry_text(carriers='AGENTS.md ("alpha; beta")'))
        naive = _fixture(tmp, _entry_text(carriers='AGENTS.md alpha; beta'))
        check("register-quoted-semicolon-split-safe: '; ' inside a quoted parenthetical is text, "
              "so the segment keeps its file and its phrase — while the same words with the "
              "semicolon outside both do split, and the tail segment fails for naming no file",
              check_register(safe) == [] and "no carrier file token" in joined(naive),
              f"{check_register(safe)} || {joined(naive)}")

        # (11) case-sensitivity is deliberate: a doctrine sentence re-cased is re-worded
        root = _fixture(tmp, _entry_text(carriers='AGENTS.md "enforcement below the agent"'))
        out = joined(root)
        check("register-phrase-case-mismatch-fails: phrase matching is case-sensitive, so a "
              "carrier that says 'Enforcement Below The Agent' does not satisfy a register "
              "quoting it in lower case", "enforcement below the agent" in out, out)

        # (12) the script check is scoped to entries claiming present enforcement
        prose = _fixture(tmp, _entry_text(
            status="prose", carriers='AGENTS.md "the carried phrase" (see scripts/absent.py)'))
        partial = _fixture(tmp, _entry_text(
            status="partial", carriers='AGENTS.md "the carried phrase" (see scripts/absent.py)'))
        check("register-prose-entry-script-skipped: the identical Carriers bullet passes under "
              "**prose** and fails under **partial** — a Carriers bullet is a claim of present "
              "enforcement, and only a present claim owes an existing script",
              check_register(prose) == [] and "scripts/absent.py" in joined(partial),
              f"{check_register(prose)} || {joined(partial)}")

        # (13) whitespace normalization on both sides: the phrase wraps in the carrier file
        # and the Carriers bullet itself wraps across an indented continuation line
        wrapped = ("### INV-1 · A rule\nSentence.\n- founding · **enforced** — x.\n"
                   '- Carriers: AGENTS.md ("a phrase that wraps across a line break for\n'
                   '  good measure"); scripts/real_gate.py\n\n')
        root = _fixture(tmp, register="# reg\n\n" + wrapped)
        check("register-wrapped-phrase-matches: a phrase broken over a line in its carrier, "
              "quoted from a Carriers bullet that itself wraps onto an indented continuation "
              "line, matches — every run of whitespace collapses to one space on both sides",
              check_register(root) == [], joined(root))

        # a status note may bold another status word for contrast; the one the bullet
        # OPENED with is the entry's grade, so the qualifier cannot silently regrade it
        contrast = ('### INV-1 · A rule\nSentence.\n'
                    '- founding · **prose** — nothing fires yet; an **enforced** entry would '
                    'owe a script.\n- Carriers: AGENTS.md (see scripts/absent.py)\n\n')
        root = _fixture(tmp, register="# reg\n\n" + contrast)
        check("the first bold status word in a status bullet grades the entry, so a note that "
              "bolds another for contrast cannot regrade it: this **prose** entry skips its "
              "absent script, which reading the later **enforced** as the status would have "
              "failed on", check_register(root) == [], joined(root))

        # a --root with no register at all: named, exit-1 material, never a silent pass
        empty = _fixture(tmp, files={"AGENTS.md": "x"})
        out = joined(empty)
        check("a --root whose tree carries no register is a named failure, not a pass and not "
              "a crash", len(check_register(empty)) == 1 and REGISTER_REL in out, out)

    # The pinned exit codes, exercised through the real command line rather than asserted
    # beside it. The hook denies on 1 alone, so 2 having to mean "crash" is what lets a
    # broken checker fail open instead of wedging every commit.
    import subprocess
    me = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="cir-exit-") as td:
        tmp = Path(td)
        good = _fixture(tmp, _entry_text(status="prose", carriers="none yet."))
        bad = _fixture(tmp, _entry_text(carriers='docs/absent.md "x"'))
        crasher = tmp / "crasher.py"
        crasher.write_text(Path(me).read_text().replace(
            "def check_register(root: Path) -> list[str]:",
            "def check_register(root: Path) -> list[str]:\n    raise RuntimeError('injected')", 1))

        def run(script, root):
            return subprocess.run([sys.executable, script, "--root", str(root)],
                                  capture_output=True, text=True)
        clean, found, crash = run(me, good), run(me, bad), run(str(crasher), good)
    check("the pinned exit codes hold on the real command line — 0 clean, 1 a named finding, "
          "2 an internal crash — which is what lets the commit hook deny on 1 alone and let a "
          "crashed checker through",
          (clean.returncode, found.returncode, crash.returncode) == (0, 1, 2)
          and "docs/absent.md" in found.stdout and "internal error" in crash.stderr,
          f"{clean.returncode}/{found.returncode}/{crash.returncode} {crash.stderr[-120:]}")

    # this repository's own register, read exactly as the commit hook reads it
    real = check_register(ROOT)
    check("this repository's own invariant register passes every check",
          real == [], "\n      ".join(real))

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="verify every carrier the invariant register names")
    ap.add_argument("--root", default=".", help="the working tree to check (default: .)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    found = check_register(Path(args.root))
    for line in found:
        print(line)
    if found:
        print(f"\n{len(found)} invariant-register failure(s). Fix the entry and its carrier "
              f"together — {REGISTER_REL} names both.")
        return 1
    print(f"{REGISTER_REL}: every carrier file, quoted phrase, and enforcement script checks out.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # a crash is never a verdict: exit 2, which the hook lets through
        print(f"check_invariant_register: internal error: {exc}", file=sys.stderr)
        sys.exit(2)
