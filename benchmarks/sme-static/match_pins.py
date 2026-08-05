#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Replay the karta-plan SKILL stack-pack matching rule (steps 1-4) deterministically.

Implements the documented matching rule — detect_stack JSON is the only matching
input; packs enumerate overlay-over-builtin by basename; `disabled: true`
suppresses; `always: true` pins; `match` tokens pin on whole-token
case-insensitive equality against dependencies/languages; platform-native.md is
skipped — and embeds the sha256 of the SKILL.md matching-rule section text (the
hashed span is the text between the explicit `<!-- karta:matching-rule:start -->`
and `<!-- karta:matching-rule:end -->` markers, LF-normalized, leading/trailing
blank/`---` lines stripped). On a hash mismatch it exits 2: the rule prose
changed, so this implementation must be re-verified against it before the hash
constant is updated. The explicit markers replaced the earlier 'heading to next
`**`/`#` line' heuristic, which silently truncated the span whenever a `**`-lead
paragraph landed inside the matching block (the 2.25.0 pack-provenance halt).

Usage:
  python3 match_pins.py --self-test
  python3 match_pins.py --karta <karta-root> --check-rule-only
  python3 match_pins.py --karta <karta-root> --repo <consumer-root> [--mode contract|coverage]

Self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, tempfile
from pathlib import Path

RULE_HEADING = "**stack pack matching"
RULE_START_MARK = "<!-- karta:matching-rule:start"
RULE_END_MARK = "<!-- karta:matching-rule:end"
RULE_SHA256 = "e1ae5dad822ea7902dc02fc8d657f256e8073f94904a6a9bfc6658ffedc36a9b"
RULE_MISMATCH_MSG = "matching rule changed — re-verify implementation, update hash"
SKILL_MD = Path("skills") / "karta-plan" / "SKILL.md"
DETECT = Path("skills") / "karta-plan" / "scripts" / "detect_stack.py"
BUILTIN_SME = Path("skills") / "_shared" / "sme"
NOT_A_PACK = {"platform-native.md"}  # shared reference data, not a pack (karta-plan SKILL)
COVERAGE_MANIFESTS = ("package.json", "pyproject.toml", "go.mod", "Cargo.toml")
COVERAGE_GLOBS = ("requirements*.txt",)
COVERAGE_MAX_DEPTH = 3
# dirs never descended into during the coverage scan (dependency trees, VCS, envs)
COVERAGE_PRUNE = {"node_modules", "vendor", ".git", ".venv", "venv", "dist", "build",
                  "__pycache__", ".worktrees"}


# --- matching-rule hash guard --------------------------------------------------

def extract_rule_section(text: str) -> str | None:
    """The hashed span: the text between the explicit matching-rule markers
    (`RULE_START_MARK` … `RULE_END_MARK`), LF-normalized, with leading/trailing
    blank and `---` separator lines stripped. None when either marker is gone.

    Explicit markers replace the former 'heading line to the next same-level
    heading (`**…`/`#`)' heuristic, which silently truncated the span the moment a
    `**`-lead paragraph was added inside the matching block — the exact defect that
    halted the 2.25.0 pack-provenance delivery and forced a fix-forward. With
    markers, any edit inside them changes the hash transparently (a real rule
    change, re-pin and move on), and no adjacent `**`-lead paragraph outside them
    can shorten the span. The content between the markers is unchanged from the
    heuristic era, so the pinned `RULE_SHA256` is identical."""
    lines = text.replace("\r\n", "\n").split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith(RULE_START_MARK)), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines))
                if lines[j].lstrip().startswith(RULE_END_MARK)), None)
    if end is None:
        return None
    span = lines[start + 1:end]
    while span and span[-1].strip() in ("", "---"):
        span.pop()
    while span and span[0].strip() in ("", "---"):
        span.pop(0)
    return "\n".join(span) + "\n"


def rule_hash(skill_text: str) -> str | None:
    section = extract_rule_section(skill_text)
    if section is None:
        return None
    return hashlib.sha256(section.encode()).hexdigest()


def check_rule(karta: Path) -> tuple[bool, str]:
    """(ok, detail). Not-ok covers a missing file, a missing heading, and a hash
    mismatch — all mean the implementation can no longer be trusted against the prose."""
    path = karta / SKILL_MD
    try:
        actual = rule_hash(path.read_text())
    except OSError as e:
        return False, f"{path}: unreadable ({e})"
    if actual is None:
        return False, (f"{path}: matching-rule markers ({RULE_START_MARK!r} … "
                       f"{RULE_END_MARK!r}) not found")
    if actual != RULE_SHA256:
        return False, f"expected {RULE_SHA256}, actual {actual}"
    return True, "matching-rule section hash verified"


# --- pack enumeration (SKILL step 2) -------------------------------------------

def parse_pack(text: str) -> dict:
    """Minimal frontmatter read for matching: kind (always|match|disabled|invalid)
    and match tokens. Full format validation stays validate_packs.py's job."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return {"kind": "invalid", "tokens": []}
    close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if close is None:
        return {"kind": "invalid", "tokens": []}
    fields: dict[str, str] = {}
    for ln in lines[1:close]:
        if ":" in ln:
            k, _, v = ln.partition(":")
            fields.setdefault(k.strip(), v.strip())
    if fields.get("disabled") == "true":
        return {"kind": "disabled", "tokens": []}
    if fields.get("always") == "true":
        return {"kind": "always", "tokens": []}
    if "match" in fields:
        try:
            tokens = json.loads(fields["match"])
        except json.JSONDecodeError:
            return {"kind": "invalid", "tokens": []}
        if isinstance(tokens, list) and all(isinstance(t, str) for t in tokens):
            return {"kind": "match", "tokens": tokens}
        return {"kind": "invalid", "tokens": []}
    return {"kind": "invalid", "tokens": []}


def enumerate_packs(builtin_dir: Path, overlay_dir: Path | None) -> dict[str, dict]:
    """basename -> {kind, tokens, source}; overlay wins a basename clash;
    platform-native.md is skipped (reference data, not a pack)."""
    packs: dict[str, dict] = {}
    for source, pack_dir in (("builtin", builtin_dir), ("overlay", overlay_dir)):
        if pack_dir is None or not pack_dir.is_dir():
            continue
        for p in sorted(pack_dir.glob("*.md")):
            if p.name in NOT_A_PACK:
                continue
            packs[p.stem] = parse_pack(p.read_text()) | {"source": source, "path": str(p)}
    return packs


# --- matching (SKILL steps 1, 3, 4) --------------------------------------------

def corpus_of(stack: dict) -> set[str]:
    return {s.lower() for s in stack.get("dependencies", [])} | \
           {s.lower() for s in stack.get("languages", [])}


def match_pins(packs: dict[str, dict], stack: dict) -> list[str]:
    """The expected pin set: always-on packs plus whole-token case-insensitive
    matches against detect_stack dependencies/languages. No substring matching."""
    corpus = corpus_of(stack)
    pinned = []
    for stem, pack in packs.items():
        if pack["kind"] == "always":
            pinned.append(stem)
        elif pack["kind"] == "match" and any(t.lower() in corpus for t in pack["tokens"]):
            pinned.append(stem)
    return sorted(pinned)


# --- detect_stack invocation (contract + coverage modes) -----------------------

def detect(repo: Path, karta: Path) -> dict:
    proc = subprocess.run([sys.executable, str(karta / DETECT), str(repo)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"detect_stack failed on {repo}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def coverage_dirs(repo: Path) -> list[Path]:
    """Every dir <=3 deep holding a coverage manifest. Dependency trees, VCS dirs,
    and virtualenvs are pruned — their manifests are not the repo's own stack."""
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        has = any((d / m).is_file() for m in COVERAGE_MANIFESTS) or \
              any(next(d.glob(g), None) for g in COVERAGE_GLOBS)
        if has:
            found.append(d)
        if depth >= COVERAGE_MAX_DEPTH:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError:
            return
        for c in children:
            if c.name in COVERAGE_PRUNE or c.name.startswith("."):
                continue
            walk(c, depth + 1)

    walk(repo, 0)
    return found


def coverage_detect(repo: Path, karta: Path) -> tuple[dict, list[str]]:
    """Union of detect_stack over every manifest-holding dir <=3 deep (diagnostic
    lane: labels the documented rule's monorepo blind spot, never the oracle)."""
    deps: set[str] = set()
    langs: set[str] = set()
    dirs = coverage_dirs(repo)
    for d in dirs:
        stack = detect(d, karta)
        deps.update(stack["dependencies"])
        langs.update(stack["languages"])
    rel = [str(d.relative_to(repo)) or "." for d in dirs]
    return {"dependencies": sorted(deps), "languages": sorted(langs)}, rel


# --- self-test -----------------------------------------------------------------

_FIXTURE_SECTION = """\
<!-- karta:matching-rule:start (pinned by benchmarks/sme-static/match_pins.py) -->
**stack pack matching (after the survey)  `plan:sme`.** Fixture rule text.

1. Run detect_stack; its JSON is the only matching input.
2. Overlay over builtin by basename; disabled suppresses; platform-native skipped.
3. always pins; match tokens equal deps/languages whole-token case-insensitively.
4. Collect always + matched into sme.
<!-- karta:matching-rule:end -->

**next same-level heading.** Not part of the span.
"""

_BUILTIN_PACKS = {
    "minimalism.md": "---\nname: minimalism\ndescription: d\nalways: true\n---\nbody\n",
    "python-fastapi.md": '---\nname: python-fastapi\ndescription: d\nmatch: ["FastAPI", "pydantic"]\n---\nbody\n',
    "go-htmx.md": '---\nname: go-htmx\ndescription: d\nmatch: ["templ", "htmx.org"]\n---\nbody\n',
    "shorttok.md": '---\nname: shorttok\ndescription: d\nmatch: ["py"]\n---\nbody\n',
    "platform-native.md": "# reference data, no frontmatter\n",
}
_OVERLAY_PACKS = {
    "minimalism.md": "---\nname: minimalism\ndescription: overlay copy\nalways: true\n---\nbody\n",
    "python-fastapi.md": '---\nname: python-fastapi\ndescription: d\nmatch: ["flask"]\n---\nbody\n',
    "shorttok.md": "---\nname: shorttok\ndescription: suppressed here\ndisabled: true\n---\nbody\n",
}


def _run_self_test() -> int:
    results: list[tuple[bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((ok, name))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")

    # rule-hash span extraction + doctored-text detection
    span = extract_rule_section(_FIXTURE_SECTION)
    check("span runs between the explicit markers, heading first, trailing --- stripped",
          span is not None and span.startswith(RULE_HEADING)
          and span.endswith("4. Collect always + matched into sme.\n")
          and "next same-level heading" not in span
          and RULE_START_MARK not in span and RULE_END_MARK not in span, repr(span))
    fixture_hash = hashlib.sha256(span.encode()).hexdigest() if span else ""
    doctored = _FIXTURE_SECTION.replace("whole-token", "substring")
    doctored_span = extract_rule_section(doctored)
    check("doctored section text changes the hash (mismatch detected)",
          doctored_span is not None
          and hashlib.sha256(doctored_span.encode()).hexdigest() != fixture_hash)
    check("CRLF input is LF-normalized before hashing",
          extract_rule_section(_FIXTURE_SECTION.replace("\n", "\r\n")) == span)
    check("missing start marker returns None (rule anchors gone is a mismatch, not a pass)",
          extract_rule_section("# other doc\n") is None)
    check("missing end marker returns None (an unterminated span is a mismatch, not a pass)",
          extract_rule_section(RULE_START_MARK + " -->\n**stack pack matching** x\n") is None)
    # hardening regressions — the exact fragility the markers eliminate:
    inside = _FIXTURE_SECTION.replace(
        "4. Collect always + matched into sme.\n",
        "4. Collect always + matched into sme.\n\n**Inside note.** A bold-lead paragraph inside the markers.\n")
    inside_span = extract_rule_section(inside)
    check("a **-lead paragraph INSIDE the markers is hashed, not silently truncated",
          inside_span is not None
          and "**Inside note.**" in inside_span
          and hashlib.sha256(inside_span.encode()).hexdigest() != fixture_hash)
    after = _FIXTURE_SECTION.replace(
        "**next same-level heading.** Not part of the span.\n",
        "**Adjacent doctrine.** Outside the markers, below the rule.\n\n"
        "**next same-level heading.** Not part of the span.\n")
    check("a **-lead paragraph AFTER the end marker never changes the span",
          extract_rule_section(after) == span)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # live exit-2 path: a karta root whose SKILL.md section text is doctored
        (root / SKILL_MD).parent.mkdir(parents=True)
        (root / SKILL_MD).write_text(_FIXTURE_SECTION)  # hash != embedded RULE_SHA256
        proc = subprocess.run([sys.executable, __file__, "--karta", str(root),
                               "--check-rule-only"], capture_output=True, text=True)
        check("doctored SKILL section text exits 2 naming the re-verify instruction",
              proc.returncode == 2 and RULE_MISMATCH_MSG in proc.stdout,
              f"rc={proc.returncode} out={proc.stdout!r}")

        # pack enumeration fixtures
        builtin = root / "builtin"
        overlay = root / "overlay"
        for d, files in ((builtin, _BUILTIN_PACKS), (overlay, _OVERLAY_PACKS)):
            d.mkdir()
            for fname, text in files.items():
                (d / fname).write_text(text)
        packs = enumerate_packs(builtin, overlay)
        check("platform-native.md is skipped (reference data, not a pack)",
              "platform-native" not in packs)
        check("overlay wins the basename clash (step 2)",
              packs["python-fastapi"]["tokens"] == ["flask"]
              and packs["python-fastapi"]["source"] == "overlay")
        check("disabled: true overlay suppresses the builtin pack",
              packs["shorttok"]["kind"] == "disabled")

        # matching fixtures (steps 1+3+4): detect_stack JSON is the only input
        stack = {"dependencies": ["github.com/a-h/templ", "pytest", "FLASK"], "languages": ["go"]}
        pinned = match_pins(packs, stack)
        check("always-on pack pins into every set (step 3)", "minimalism" in pinned)
        check("templ token does NOT whole-token-match github.com/a-h/templ",
              "go-htmx" not in match_pins({"go-htmx": packs["go-htmx"]}, stack))
        check("no substring matching: token 'py' does not match dep 'pytest'",
              "shorttok" not in match_pins(
                  {"shorttok": parse_pack(_BUILTIN_PACKS["shorttok.md"])}, stack))
        check("match is case-insensitive whole-token equality (FLASK ~ flask)",
              "python-fastapi" in pinned)
        check("collection is always + matched, sorted (step 4)",
              pinned == ["minimalism", "python-fastapi"])
        check("language entries match too (token go ~ language go)",
              match_pins({"g": {"kind": "match", "tokens": ["go"]}}, stack) == ["g"])

        # coverage scan fixtures
        (root / "repo" / "sub" / "deep").mkdir(parents=True)
        (root / "repo" / "node_modules" / "x").mkdir(parents=True)
        (root / "repo" / "package.json").write_text("{}")
        (root / "repo" / "sub" / "deep" / "go.mod").write_text("module x\n")
        (root / "repo" / "node_modules" / "x" / "package.json").write_text("{}")
        dirs = coverage_dirs(root / "repo")
        check("coverage scan finds root + nested manifest dirs, prunes node_modules",
              [str(d.relative_to(root / "repo")) or "." for d in dirs] == [".", "sub/deep"])

    failures = sum(1 for ok, _ in results if not ok)
    total = len(results)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


# --- CLI -----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--karta", type=Path, default=Path(__file__).resolve().parents[2],
                    help="karta repo root (default: this script's repo)")
    ap.add_argument("--repo", type=Path, help="consumer repo root to compute pins for")
    ap.add_argument("--mode", choices=("contract", "coverage"), default="contract")
    ap.add_argument("--check-rule-only", action="store_true",
                    help="verify the SKILL.md matching-rule hash and exit")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()

    ok, detail = check_rule(args.karta)
    if not ok:
        print(f"{RULE_MISMATCH_MSG} ({detail})")
        return 2
    if args.check_rule_only:
        print(detail)
        return 0
    if not args.repo:
        print("error: provide --repo (or --check-rule-only / --self-test)", file=sys.stderr)
        return 1

    if args.mode == "contract":
        stack, dirs = detect(args.repo, args.karta), ["."]
    else:
        stack, dirs = coverage_detect(args.repo, args.karta)
    packs = enumerate_packs(args.karta / BUILTIN_SME, args.repo / ".karta" / "sme")
    print(json.dumps({"mode": args.mode, "stack": stack, "manifest_dirs": dirs,
                      "pinned": match_pins(packs, stack)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
