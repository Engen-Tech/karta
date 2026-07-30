#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Classify overlay packs against every historical blob of their built-in, and
audit match tokens for dead and overbroad entries.

Drift classes (no sync-commit archaeology — no sync marker exists):
  IDENTICAL             == the HEAD blob of skills/_shared/sme/<f>.md
  UPSTREAM-UNPROPAGATED == an older blob (seeded then left behind)
  LOCAL-ADDITIVE        contains every HEAD-blob line AND its match-token set is
                        a superset of the built-in's
  DIVERGENT             anything else
  The overlay is stamp-stripped (seeded_from/base_sha256 frontmatter lines
  removed) before classification; built-in blobs never carried a stamp.

Token audit (reusing the match_pins matcher semantics):
  dead token      = matches nothing in the supplied corpus (the union of
                    contract+coverage detect_stack outputs across the enrolled
                    consumer repos; the caller records the corpus)
  overbroad token = equals a detect_stack language literal in a pack whose
                    basename is not that language

Usage:
  python3 seed_drift.py --self-test
  python3 seed_drift.py [--target <karta-root>] --overlays <dir,dir,...> [--corpus <json>]

Self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary.
"""
from __future__ import annotations
import argparse, ast, json, re, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_pins  # sibling: pack parsing + corpus semantics

BUILTIN_REL = "skills/_shared/sme"
# The language names detect_stack.py can emit (see its module docstring).
LANGUAGE_LITERALS = frozenset({"python", "javascript", "node", "go", "rust", "ruby", "php"})

# Duplicated from skills/karta-plan/scripts/check_pack_provenance.py STAMP_KEYS —
# bench code never imports from skills/ (measuring-stick discipline); the
# --self-test parity check reads that file's text and fails on divergence.
_STAMP_KEYS = ("seeded_from", "base_sha256")


def _strip_stamp(text: str) -> str:
    """Remove the two provenance-stamp keys from a leading frontmatter block.

    Mirrors the runtime strip_stamp's two-key semantics (parity-checked in the
    self-test). Leading window only: line 1 must be exactly '---' after trailing
    whitespace/CR, and the next line whose stripped content is '---' closes the
    block; a later '---' in the body never closes it. No opener (including a
    BOM-prefixed file) or no closer -> identity. Body text is never touched and
    every kept byte is preserved, so the helper is the identity on unstamped
    text. The runtime's canonicalize() pipeline is deliberately NOT copied —
    bench classification is raw-text equality against historical git blobs.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return text
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return text
    kept = [l for l in lines[1:close] if l.split(":", 1)[0].strip() not in _STAMP_KEYS]
    return "\n".join([lines[0], *kept, *lines[close:]])


# --- historical blobs ----------------------------------------------------------

def historical_blobs(karta: Path, basename: str) -> list[str]:
    """All historical contents of the built-in pack, newest first (HEAD blob first)."""
    rel = f"{BUILTIN_REL}/{basename}"
    proc = subprocess.run(["git", "-C", str(karta), "rev-list", "HEAD", "--", rel],
                          capture_output=True, text=True, timeout=60)
    blobs: list[str] = []
    for sha in proc.stdout.split():
        show = subprocess.run(["git", "-C", str(karta), "show", f"{sha}:{rel}"],
                              capture_output=True, text=True, timeout=60)
        if show.returncode == 0 and show.stdout not in blobs:
            blobs.append(show.stdout)
    return blobs


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in match_pins.parse_pack(text)["tokens"]}


def classify(overlay_text: str, blobs: list[str]) -> str:
    overlay_text = _strip_stamp(overlay_text)  # overlay side only; built-ins never stamped
    if not blobs:
        return "NO-BUILTIN-HISTORY"
    head = blobs[0]
    if overlay_text == head:
        return "IDENTICAL"
    if overlay_text in blobs[1:]:
        return "UPSTREAM-UNPROPAGATED"
    # match: lines are compared at token level (the superset clause), not as text
    head_lines = {ln for ln in head.splitlines() if not ln.startswith("match:")}
    overlay_lines = set(overlay_text.splitlines())
    if head_lines <= overlay_lines and _tokens(head) <= _tokens(overlay_text):
        return "LOCAL-ADDITIVE"
    return "DIVERGENT"


def drift_report(karta: Path, overlay_dirs: dict[str, Path]) -> list[dict]:
    """One row per overlay whose basename exists in skills/_shared/sme/."""
    builtin_dir = karta / BUILTIN_REL
    rows: list[dict] = []
    for owner, d in overlay_dirs.items():
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name in match_pins.NOT_A_PACK or not (builtin_dir / p.name).is_file():
                continue
            rows.append({"owner": owner, "pack": p.stem,
                         "class": classify(p.read_text(), historical_blobs(karta, p.name))})
    return rows


# --- token audit ---------------------------------------------------------------

def token_audit(pack_files: dict[str, list[Path]], corpus: set[str]) -> list[dict]:
    """Audit match tokens of the given packs (owner -> files) against the corpus."""
    findings: list[dict] = []
    for owner, files in sorted(pack_files.items()):
        for f in sorted(files):
            pack = match_pins.parse_pack(f.read_text())
            for tok in pack["tokens"]:
                low = tok.lower()
                if low not in corpus:
                    findings.append({"kind": "dead-token", "owner": owner,
                                     "pack": f.stem, "token": tok})
                if low in LANGUAGE_LITERALS and f.stem != low:
                    findings.append({"kind": "overbroad-token", "owner": owner,
                                     "pack": f.stem, "token": tok})
    return findings


# --- self-test -----------------------------------------------------------------

_V1 = "---\nname: demo\ndescription: v1\nmatch: [\"alpha\"]\n---\n## Do\n- old rule\n"
_V2 = "---\nname: demo\ndescription: v2\nmatch: [\"alpha\", \"beta\"]\n---\n## Do\n- new rule\n"


def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")

    with tempfile.TemporaryDirectory() as td:
        karta = Path(td)
        pack = karta / BUILTIN_REL / "demo.md"
        pack.parent.mkdir(parents=True)
        git = ["git", "-C", str(karta), "-c", "user.name=selftest",
               "-c", "user.email=selftest@local"]
        env = {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
               "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"}
        subprocess.run(["git", "-C", str(karta), "init", "-q", "-b", "main"], check=True)
        for text, msg in ((_V1, "v1"), (_V2, "v2")):
            pack.write_text(text)
            subprocess.run([*git, "add", "-A"], check=True)
            subprocess.run([*git, "commit", "-qm", msg], check=True,
                           env={**__import__("os").environ, **env})

        blobs = historical_blobs(karta, "demo.md")
        check("historical blobs enumerate newest-first", blobs == [_V2, _V1])
        check("IDENTICAL: overlay equals the HEAD blob", classify(_V2, blobs) == "IDENTICAL")
        check("UPSTREAM-UNPROPAGATED: overlay equals an older blob",
              classify(_V1, blobs) == "UPSTREAM-UNPROPAGATED")
        additive = _V2.replace("## Do\n", "## Do\n- local extra rule\n").replace(
            '"beta"]', '"beta", "gamma"]')
        check("LOCAL-ADDITIVE: every HEAD line kept + token superset",
              classify(additive, blobs) == "LOCAL-ADDITIVE")
        check("DIVERGENT: a HEAD line lost", classify(_V2.replace("- new rule\n", ""),
                                                      blobs) == "DIVERGENT")
        check("DIVERGENT: token set shrank even with lines kept",
              classify(additive.replace(', "beta"', ""), blobs) == "DIVERGENT")

        # stamped variants: the strip runs on classify's input, so every class
        # sees stripped text — and a stamp never rescues a real delta
        stamp = ("seeded_from: skills/_shared/sme/demo.md\n"
                 "base_sha256: " + "a" * 64 + "\n")

        def _stamped(text: str) -> str:
            return text.replace("---\n", "---\n" + stamp, 1)

        check("stamped HEAD copy classifies IDENTICAL",
              classify(_stamped(_V2), blobs) == "IDENTICAL")
        check("stamped older copy classifies UPSTREAM-UNPROPAGATED",
              classify(_stamped(_V1), blobs) == "UPSTREAM-UNPROPAGATED")
        check("stamped additive copy stays LOCAL-ADDITIVE",
              classify(_stamped(additive), blobs) == "LOCAL-ADDITIVE")
        check("forged stamp on a divergent copy stays DIVERGENT",
              classify(_stamped(_V2.replace("- new rule\n", "")), blobs) == "DIVERGENT")
        check("strip is the identity on unstamped text", _strip_stamp(_V2) == _V2)
        check("strip is the identity on text with no frontmatter",
              _strip_stamp("## Do\n- rule\n") == "## Do\n- rule\n")
        check("non-stamp frontmatter lines survive byte-identical",
              _strip_stamp(_stamped(_V2)) == _V2)
        one_key = _V2.replace("---\n", "---\nseeded_from: skills/_shared/sme/demo.md\n", 1)
        check("single-key partial stamp strips cleanly", _strip_stamp(one_key) == _V2)

        # drift_report plumbing over an overlay dir
        overlay = karta / "overlay"
        overlay.mkdir()
        (overlay / "demo.md").write_text(_V1)
        (overlay / "localonly.md").write_text(_V1.replace("demo", "localonly"))
        rows = drift_report(karta, {"consumerx": overlay})
        check("drift_report classifies only overlays with a built-in counterpart",
              rows == [{"owner": "consumerx", "pack": "demo",
                        "class": "UPSTREAM-UNPROPAGATED"}], repr(rows))

        # token audit
        deadpack = overlay / "webby.md"
        deadpack.write_text('---\nname: webby\ndescription: d\n'
                            'match: ["templ", "go", "reactish"]\n---\nbody\n')
        corpus = {"github.com/a-h/templ", "go", "fastapi"}
        got = token_audit({"consumerx": [deadpack]}, corpus)
        kinds = {(f["kind"], f["token"]) for f in got}
        check("dead token: templ never whole-token-equals github.com/a-h/templ",
              ("dead-token", "templ") in kinds, repr(got))
        check("dead token: unmatched invented token flagged",
              ("dead-token", "reactish") in kinds)
        check("alive token: 'go' matches the corpus language literal",
              ("dead-token", "go") not in kinds)
        check("overbroad token: language literal 'go' in a non-'go' pack",
              ("overbroad-token", "go") in kinds)
        gopack = overlay / "go.md"
        gopack.write_text('---\nname: go\ndescription: d\nmatch: ["go"]\n---\nbody\n')
        got2 = token_audit({"consumerx": [gopack]}, corpus)
        check("not overbroad when the pack basename IS the language",
              not any(f["kind"] == "overbroad-token" for f in got2), repr(got2))

    # parity: the duplicated key tuple can never silently diverge from the
    # runtime classifier — read its source as text, never import it
    runtime = (Path(__file__).resolve().parents[2] / "skills" / "karta-plan"
               / "scripts" / "check_pack_provenance.py")
    check("parity: runtime source exists", runtime.is_file(),
          f"runtime parity source missing: {runtime}")
    runtime_keys: set[str] | None = None
    if runtime.is_file():
        m = re.search(r"^\s*STAMP_KEYS\s*=\s*(\([^)]*\))", runtime.read_text(),
                      re.MULTILINE)
        if m:
            runtime_keys = set(ast.literal_eval(m.group(1)))
    check("parity: STAMP_KEYS names exactly the bench stamp-key pair",
          runtime_keys == set(_STAMP_KEYS),
          f"{runtime} STAMP_KEYS={runtime_keys!r} != "
          f"benchmarks/sme-static/seed_drift.py _STAMP_KEYS={set(_STAMP_KEYS)!r}")

    failures = results.count(False)
    total = len(results)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[2],
                    help="karta repo root (default: this script's repo)")
    ap.add_argument("--overlays", default=None,
                    help="comma-separated overlay dirs (owner inferred from path)")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="JSON file with detect_stack union {dependencies, languages}")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    karta = args.target.resolve()
    dirs = ([Path(s) for s in args.overlays.split(",") if s]
            if args.overlays else [karta / ".karta" / "sme"])
    overlay_dirs = {str(d): d for d in dirs}
    rows = drift_report(karta, overlay_dirs)
    out: dict = {"drift": rows}
    if args.corpus:
        corpus = match_pins.corpus_of(json.loads(args.corpus.read_text()))
        files = {o: [p for p in sorted(d.glob("*.md")) if p.name not in match_pins.NOT_A_PACK]
                 for o, d in overlay_dirs.items()}
        out["token_audit"] = token_audit(files, corpus)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
