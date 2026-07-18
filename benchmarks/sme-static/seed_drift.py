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
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import match_pins  # sibling: pack parsing + corpus semantics

BUILTIN_REL = "skills/_shared/sme"
# The language names detect_stack.py can emit (see its module docstring).
LANGUAGE_LITERALS = frozenset({"python", "javascript", "node", "go", "rust", "ruby", "php"})


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
