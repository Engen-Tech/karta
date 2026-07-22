# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Classify every local stack-pack copy against the shipped built-in it shadows.

Each `.karta/sme/*.md` file in a consumer repo is a copy of — or an original beside —
karta's built-in packs. This script reports which honest state each copy is in, so an
out-of-date copy can be refreshed and a genuinely edited copy is flagged loudly instead
of silently overriding the shipped rules. This release warns and deters; nothing halts.

States (the exact user-facing strings, one per pack):
  seeded cache   stamp-stripped canonical bytes are identical to the current built-in.
  stale cache    differs from the current built-in but is byte-identical to a genuine
                 PAST built-in (its canonical hash appears in the shipped hash ledger);
                 resolution is auto-reseed at the next kaizen pass.
  suppression    frontmatter `disabled: true`; the body is free commentary, not compared.
  project pack   the casefolded basename collides with no built-in — a project's own pack.
  illegal shadow a same-basename copy carrying a genuine local delta over the built-in.
  orphaned cache the stamp's `seeded_from` names a built-in that no longer exists after
                 rename-alias resolution.

The provenance stamp (frontmatter `seeded_from` + `base_sha256`) is DIAGNOSTIC-ONLY: the
byte comparison against the shipped built-ins (current one, plus the ledger of past ones)
is the sole cleanliness signal. A forged or missing stamp changes only which finding
message is emitted, never the state verdict — see `--self-test`, which proves this
invariance explicitly. That is the forged-stamp guard: a fabricated stamp can never get a
real local delta treated as clean (and so, in the kaizen item, silently overwritten).

Built-ins resolve from CLAUDE_PLUGIN_ROOT, falling back to this script's own skill root
`references/sme/` — the same idiom hooks/scripts/guard_pack_write.py uses for its
validator lookup. The hash ledger resolves the same way from `references/pack-hashes.json`.

  check_pack_provenance.py <repo-root>        classify every <repo-root>/.karta/sme/*.md
  check_pack_provenance.py --file <pack.md>   classify one file on disk
  check_pack_provenance.py --stdin --file <p> classify proposed content from stdin,
                                              taking its basename from <p> (PreToolUse:
                                              the target is not on disk yet)
  check_pack_provenance.py --self-test        run embedded fixtures, exit 0/1

Exit 0 whenever classification ran — phase 1 never gates on findings; a nonzero exit
means the script itself failed. JSON on stdout: {"packs": [...], "warnings": [...],
"aliases": {...}}.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, unicodedata
from pathlib import Path

# The canonical illegal-shadow substring — quoted byte-identically here, in the plan
# skill's stack-pack step, the write guard, and the docs (held together by the binder's
# shared_terms gate). Downstream call sites match on exactly this text.
ILLEGAL_SHADOW_SUBSTR = "illegal shadow: a local delta over the shipped built-in"

# Rename-alias table: retired built-in basename (sans .md, casefolded) -> current name.
# It lives here as data because provenance classification is what needs it (orphaned-cache
# resolution), and it is exposed in the JSON output so every other call site cites one
# truth. Phase 1 ships it empty — no built-in has been renamed yet; a rename adds an entry
# kept for one minor release, and phase 2 retires the table.
RENAME_ALIASES: dict[str, str] = {}

STAMP_KEYS = ("seeded_from", "base_sha256")
BUILTINS_REL = ("references", "sme")
LEDGER_REL = ("references", "pack-hashes.json")


# --- canonicalization ----------------------------------------------------------

def canonicalize(text: str) -> str:
    """NFC, LF line endings, BOM stripped, per-line trailing whitespace trimmed."""
    if text.startswith("﻿"):
        text = text[1:]
    text = unicodedata.normalize("NFC", text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines)


def _split_frontmatter(canon: str) -> tuple[list[str] | None, int]:
    """Return (frontmatter key lines, index of the first body line), or (None, 0)."""
    lines = canon.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], i + 1
    return None, 0


def parse_frontmatter(canon: str) -> dict[str, str]:
    fm_lines, _ = _split_frontmatter(canon)
    fm: dict[str, str] = {}
    for line in fm_lines or []:
        if ":" in line and not line[:1].isspace() and not line.startswith("-"):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
    return fm


def _is_stamp_line(line: str) -> bool:
    return line.split(":", 1)[0].strip() in STAMP_KEYS


def strip_stamp(canon: str) -> str:
    """Drop the two stamp keys from the frontmatter; everything else is byte-preserved."""
    fm_lines, body_start = _split_frontmatter(canon)
    if fm_lines is None:
        return canon
    kept = [l for l in fm_lines if not _is_stamp_line(l)]
    body = canon.split("\n")[body_start:]
    return "\n".join(["---", *kept, "---", *body])


def canonical_sha256(text: str) -> str:
    """The comparison hash: canonicalized, stamp-stripped, utf-8 sha256 hex.

    The same pipeline runs on both sides — a local copy and a shipped built-in — so the
    two hashes are comparable. Stamp-stripping a stampless built-in is a no-op."""
    return hashlib.sha256(strip_stamp(canonicalize(text)).encode("utf-8")).hexdigest()


def _clean(val: str | None) -> str | None:
    if val is None:
        return None
    val = val.strip().strip('"').strip("'").strip()
    return val or None


# --- built-in + ledger resolution ---------------------------------------------

def _resolve_under_skill_root(*rel: str) -> Path | None:
    """CLAUDE_PLUGIN_ROOT/skills/karta-plan/<rel>, else this script's own skill root."""
    candidates: list[Path] = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        candidates.append(Path(env) / "skills" / "karta-plan" / Path(*rel))
    candidates.append(Path(__file__).resolve().parent.parent / Path(*rel))
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def load_builtins() -> tuple[dict[str, dict[str, str]], list[str]]:
    """Map casefolded stem -> {file, hash} for every shipped built-in pack."""
    warnings: list[str] = []
    bdir = _resolve_under_skill_root(*BUILTINS_REL)
    if bdir is None or not bdir.is_dir():
        warnings.append("built-in packs directory (references/sme/) not found — every "
                        "copy classifies as project pack")
        return {}, warnings
    builtins: dict[str, dict[str, str]] = {}
    for p in sorted(bdir.glob("*.md")):
        builtins[p.stem.casefold()] = {
            "file": p.name,
            "hash": canonical_sha256(p.read_text(encoding="utf-8", errors="replace")),
        }
    return builtins, warnings


def load_ledger() -> dict[str, list[str]]:
    """basename (with .md) -> list of canonical sha256 the built-in has shipped with."""
    p = _resolve_under_skill_root(*LEDGER_REL)
    if p is None or not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def resolve_key(stem: str, builtins: dict[str, dict[str, str]],
                aliases: dict[str, str]) -> str | None:
    """Casefolded stem -> the built-in's casefolded key, directly or via a rename alias."""
    cf = stem.casefold()
    if cf in builtins:
        return cf
    aliased = aliases.get(cf)
    if aliased and aliased.casefold() in builtins:
        return aliased.casefold()
    return None


# --- classification ------------------------------------------------------------

def classify_one(text: str, filename: str, builtins: dict[str, dict[str, str]],
                 ledger: dict[str, list[str]], aliases: dict[str, str],
                 file_label: str | None = None) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    basename = Path(filename).name
    stem = Path(basename).stem

    if basename != basename.lower():
        warnings.append(f"{basename}: non-lowercase basename — resolved via casefold, but "
                        "seed packs with lowercase names")

    canon = canonicalize(text)
    fm = parse_frontmatter(canon)
    disabled = fm.get("disabled", "").strip().lower() in ("true", "yes", "on")
    seeded_from = _clean(fm.get("seeded_from"))
    base_sha256 = _clean(fm.get("base_sha256"))
    stamp = ({"seeded_from": seeded_from, "base_sha256": base_sha256}
             if (seeded_from or base_sha256) else None)
    local_hash = canonical_sha256(text)

    compare_key = resolve_key(stem, builtins, aliases)
    builtin = builtins[compare_key]["file"] if compare_key else None
    finding: str | None = None

    if disabled:
        state = "suppression"
    elif compare_key is not None:
        assert builtin is not None
        if local_hash == builtins[compare_key]["hash"]:
            state = "seeded cache"
        elif local_hash in ledger.get(builtin, []):
            state = "stale cache"
            finding = (f"stale cache: byte-identical to a past shipped {builtin} but not the "
                       "current one; resolution is auto-reseed at the next kaizen pass")
        else:
            state = "illegal shadow"
            finding = (f"{ILLEGAL_SHADOW_SUBSTR} ({builtin}) — restore the seeded copy, move "
                       "the delta to a project pack (a fresh basename with extends and "
                       "id_prefix), or take the delta upstream")
    else:
        # basename resolves to no live built-in: an orphaned cache (dead seeded_from) or a
        # project's own pack.
        origin = _clean(seeded_from.split("@", 1)[0]) if seeded_from else None
        if origin is not None and resolve_key(Path(origin).stem, builtins, aliases) is None:
            state = "orphaned cache"
            finding = (f"orphaned cache: seeded_from names '{seeded_from}', a built-in that no "
                       "longer exists after rename-alias resolution")
        else:
            state = "project pack"

    pack = {
        "file": file_label if file_label is not None else filename,
        "basename": basename,
        "state": state,
        "builtin": builtin,
        "stamp": stamp,
        "finding": finding,
    }
    return pack, warnings


def classify_repo(root: Path) -> dict:
    builtins, warnings = load_builtins()
    ledger = load_ledger()
    packs: list[dict] = []
    sme_dir = root / ".karta" / "sme"
    for p in sorted(sme_dir.glob("*.md")) if sme_dir.is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        pack, warns = classify_one(text, p.name, builtins, ledger, RENAME_ALIASES,
                                    file_label=str(p))
        packs.append(pack)
        warnings.extend(warns)
    return {"packs": packs, "warnings": warnings, "aliases": dict(RENAME_ALIASES)}


def classify_single(text: str, filename: str, file_label: str) -> dict:
    builtins, warnings = load_builtins()
    ledger = load_ledger()
    pack, warns = classify_one(text, filename, builtins, ledger, RENAME_ALIASES,
                               file_label=file_label)
    warnings.extend(warns)
    return {"packs": [pack], "warnings": warnings, "aliases": dict(RENAME_ALIASES)}


# --- self-test -----------------------------------------------------------------

_BUILTIN = (
    "---\n"
    "name: demo\n"
    "description: a fixture built-in pack\n"
    'match: ["demo"]\n'
    "---\n"
    "## Notes\n"
    "Accented café line for NFC coverage.\n"
    "## Review checklist\n"
    "- [ ] demo.1 — the shipped rule.\n"
)
_PAST_BUILTIN = _BUILTIN.replace("the shipped rule", "an older shipped rule")
_RENAMED = _BUILTIN.replace("demo", "renamed")
_GO = "---\nname: go-htmx\ndescription: fixture\nmatch: [\"go\"]\n---\n## Review checklist\n- [ ] gohtmx.1 — rule.\n"


def _with_frontmatter(body: str, **keys: str) -> str:
    """Insert extra frontmatter keys just before the closing `---` of the body."""
    lines = body.split("\n")
    close = lines.index("---", 1)
    extra = [f"{k}: {v}" for k, v in keys.items()]
    return "\n".join(lines[:close] + extra + lines[close:])


def _run_self_test() -> int:
    demo_hash = canonical_sha256(_BUILTIN)
    past_hash = canonical_sha256(_PAST_BUILTIN)
    renamed_hash = canonical_sha256(_RENAMED)
    go_hash = canonical_sha256(_GO)
    builtins = {
        "demo": {"file": "demo.md", "hash": demo_hash},
        "renamed": {"file": "renamed.md", "hash": renamed_hash},
        "go-htmx": {"file": "go-htmx.md", "hash": go_hash},
    }
    # ledger carries the current demo hash plus one genuine past hash.
    ledger = {"demo.md": [demo_hash, past_hash], "renamed.md": [renamed_hash],
              "go-htmx.md": [go_hash]}
    aliases = {"oldname": "renamed"}

    def st(text: str, filename: str) -> tuple[str, str | None]:
        pack, _w = classify_one(text, filename, builtins, ledger, aliases)
        return pack["state"], pack["finding"]

    def st_warn(text: str, filename: str) -> tuple[str, list[str]]:
        pack, w = classify_one(text, filename, builtins, ledger, aliases)
        return pack["state"], w

    # A genuine local delta (used by both the illegal-shadow and the invariance checks).
    shadow = _BUILTIN + "- [ ] demo.2 — a sneaky extra local rule.\n"
    shadow_hash = canonical_sha256(shadow)

    checks: list[tuple[str, bool]] = []

    # 1-4: canonicalization variants of the built-in all read as seeded cache.
    checks.append(("CRLF variant classifies as seeded cache",
                   st(_BUILTIN.replace("\n", "\r\n"), "demo.md")[0] == "seeded cache"))
    checks.append(("BOM variant classifies as seeded cache",
                   st("﻿" + _BUILTIN, "demo.md")[0] == "seeded cache"))
    checks.append(("NFC (decomposed) variant classifies as seeded cache",
                   st(_BUILTIN.replace("café", "café"), "demo.md")[0] == "seeded cache"))
    checks.append(("trailing-whitespace variant classifies as seeded cache",
                   st(_BUILTIN.replace("\n", "  \t\n"), "demo.md")[0] == "seeded cache"))

    # 5: casefold resolution finds the built-in AND warns on the non-lowercase basename.
    state, warns = st_warn(_GO, "GO-HTMX.md")
    checks.append(("GO-HTMX.md resolves go-htmx as seeded cache and warns on the basename",
                   state == "seeded cache" and any("non-lowercase" in w for w in warns)))

    # 6: stamp-stripped comparison — a seeded copy carrying a stamp is still seeded cache.
    stamped_seed = _with_frontmatter(_BUILTIN, seeded_from="demo", base_sha256=demo_hash)
    checks.append(("stamp-stripped comparison: a stamped seeded copy is seeded cache",
                   st(stamped_seed, "demo.md")[0] == "seeded cache"))

    # 7: one fixture per state.
    checks.append(("state fixture: suppression (disabled: true)",
                   st(_with_frontmatter(_BUILTIN, disabled="true"), "demo.md")[0] == "suppression"))
    checks.append(("state fixture: project pack (no built-in collision)",
                   st("---\nname: house\ndescription: ours\n---\nbody\n", "house.md")[0] == "project pack"))
    ill_state, ill_finding = st(shadow, "demo.md")
    checks.append(("state fixture: illegal shadow with the canonical substring",
                   ill_state == "illegal shadow"
                   and ill_finding is not None and ILLEGAL_SHADOW_SUBSTR in ill_finding))
    stale_state, stale_finding = st(
        _with_frontmatter(_PAST_BUILTIN, seeded_from="demo", base_sha256=past_hash), "demo.md")
    checks.append(("state fixture: stale cache names auto-reseed as the resolution",
                   stale_state == "stale cache"
                   and stale_finding is not None and "auto-reseed" in stale_finding))
    orph_state, orph_finding = st(
        _with_frontmatter("---\nname: ghost\ndescription: x\n---\nbody\n", seeded_from="ghost"),
        "ghost.md")
    checks.append(("state fixture: orphaned cache (dead seeded_from)",
                   orph_state == "orphaned cache"
                   and orph_finding is not None and "no longer exists" in orph_finding))

    # 8: alias-table resolution — an old basename resolves to the renamed built-in (NOT
    # orphaned), and a dead seeded_from with no alias IS orphaned. The same alias table
    # (exposed in the JSON output) is the single truth plan:sme uses to resolve a dangling
    # `extends` pointer for one minor release, so an old basename used as an extends pointer
    # resolves through resolve_key exactly as a seeded_from or a file basename does. The
    # shipped RENAME_ALIASES is empty (no built-in has been renamed yet) — this uses the
    # fixture alias {"oldname": "renamed"}, never a real rename.
    checks.append(("alias table: an old basename resolves to the renamed built-in",
                   st(_RENAMED, "oldname.md")[0] == "seeded cache"))
    checks.append(("alias table: seeded_from via alias does not false-orphan",
                   st(_with_frontmatter(_RENAMED, seeded_from="oldname"), "renamed.md")[0]
                   == "seeded cache"))
    checks.append(("alias table: an old basename used as an extends pointer resolves to the "
                   "renamed built-in",
                   resolve_key("oldname", builtins, aliases) == "renamed"))
    checks.append(("alias table: a dead name with no alias entry does not resolve",
                   resolve_key("ghost", builtins, aliases) is None))

    # 9: FORGED / MISSING STAMP VERDICT INVARIANCE (the core guard). The verdict is a pure
    # function of the stamp-stripped bytes vs the built-ins — the stamp text cannot move it.
    dirty_variants = {
        "no stamp": shadow,
        "forged base = own hash (self-certify attempt)":
            _with_frontmatter(shadow, seeded_from="demo", base_sha256=shadow_hash),
        "forged base = a real past ledger hash":
            _with_frontmatter(shadow, seeded_from="demo", base_sha256=past_hash),
    }
    checks.append(("forged/missing stamp never rescues a genuine delta from illegal shadow",
                   all(st(t, "demo.md")[0] == "illegal shadow" for t in dirty_variants.values())))
    clean_stale_variants = {
        "valid stamp": _with_frontmatter(_PAST_BUILTIN, seeded_from="demo", base_sha256=past_hash),
        "no stamp": _PAST_BUILTIN,
        "forged garbage base": _with_frontmatter(_PAST_BUILTIN, seeded_from="demo",
                                                 base_sha256="0" * 64),
    }
    checks.append(("forged/missing stamp never changes a stale-cache verdict either",
                   all(st(t, "demo.md")[0] == "stale cache" for t in clean_stale_variants.values())))

    # 10: BOTH ledger fixtures.
    checks.append(("ledger fixture: base_sha256 in the ledger but not current -> stale cache",
                   st(_with_frontmatter(_PAST_BUILTIN, seeded_from="demo", base_sha256=past_hash),
                      "demo.md")[0] == "stale cache"))
    # A genuine delta whose forged base_sha256 EQUALS its own stamp-stripped hash but is
    # absent from the ledger still falls to illegal shadow.
    checks.append(("ledger fixture: base_sha256 absent from ledger -> illegal shadow even when "
                   "it equals the local hash",
                   st(_with_frontmatter(shadow, seeded_from="demo", base_sha256=shadow_hash),
                      "demo.md")[0] == "illegal shadow"))

    failures = sum(1 for _n, ok in checks if not ok)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    total = len(checks)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


# --- entry point ---------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Classify local stack-pack copies by provenance.")
    ap.add_argument("root", nargs="?", help="repo root: classify every .karta/sme/*.md under it")
    ap.add_argument("--file", help="classify one pack file (its basename resolves the built-in)")
    ap.add_argument("--stdin", action="store_true",
                    help="read proposed content from stdin (needs --file for the basename)")
    ap.add_argument("--self-test", action="store_true", help="run embedded fixtures, exit 0/1")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()

    if args.stdin:
        if not args.file:
            print("error: --stdin needs --file <path> to supply the basename", file=sys.stderr)
            return 2
        content = sys.stdin.read()
        result = classify_single(content, Path(args.file).name, args.file)
    elif args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"error: no such file: {args.file}", file=sys.stderr)
            return 2
        result = classify_single(path.read_text(encoding="utf-8", errors="replace"),
                                 path.name, str(path))
    elif args.root:
        result = classify_repo(Path(args.root))
    else:
        ap.error("give a repo root, --file <pack.md>, --stdin --file <path>, or --self-test")
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
