#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Check a committed design capture's bytes against its recorded fingerprint in
.karta/design-pins.json — one more hard gate in karta-validate's prerequisites
phase, run before the design is served.

What it proves: capture-against-pin, and capture-against-its-own recapture
date. It never reaches upstream — the living design lives behind agent-invoked
tools with no network access from here — so a pass never claims the pin still
resembles the design it was taken from; that is a human's job, and the run
prints the upstream address and the recapture triggers so the person about to
trust the comparison is reading the terms at the moment they matter.

Seven outcomes, in a deliberate ladder:
  1. bytes match the pin                                -> pass
  2. bytes disagree with the pin                         -> fail (drift)
  3. inside the repo, pin file present, no entry         -> fail
  4. recapture_after has passed                          -> fail
  5. no pin file at all                                  -> pass, notice
  6. design resolved from outside the repository         -> pass, notice
  7. malformed pin file (not an object, or an entry
     missing sha256)                                     -> fail

The check reads. It never rewrites a capture, never rewrites the pin file, and
never deletes anything — restoring a drifted pin, or writing the first one, is
a copy-paste from this script's own printed hash.

Usage:
  uv run skills/karta-validate/scripts/check_design_pins.py --design-path <path>
  uv run skills/karta-validate/scripts/check_design_pins.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

# check_design_pins.py sits in the same scripts directory as serve_design.py. The
# caller's design path is not resolved when this runs — it may legally be a
# directory — so this imports serve_design's own resolver rather than
# reimplementing its standalone/non-print preference. Kept here, in the
# prerequisites phase, rather than moved into the serve phase: this is the one
# phase that is already nothing but hard gates.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from serve_design import resolve_design_file  # noqa: E402

PIN_FILE_NAME = ".karta/design-pins.json"
DRIFT_MESSAGE = "design capture does not match its pin in .karta/design-pins.json"
_ENTRY_KEYS = {"sha256", "source", "captured_on", "recapture_triggers", "recapture_after"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(design_path: Path, pin_file: Path, repo_root: Path) -> tuple[int, list[str]]:
    """Run the enforcement ladder for one design path against one pin file,
    rooted at repo_root. Returns (exit_code, lines-to-print). Every outcome is
    a return, never an exception — the caller only prints and exits."""
    repo_root = repo_root.expanduser().resolve()
    try:
        resolved = resolve_design_file(Path(design_path))
    except SystemExit as e:
        return 1, [str(e)]

    try:
        inside = resolved.is_relative_to(repo_root)
    except ValueError:
        inside = False

    if not inside:
        return 0, [f"NOTICE: {resolved} is outside the repository ({repo_root}); "
                   f"it cannot be pinned and was not verified."]

    rel = resolved.relative_to(repo_root).as_posix()

    if not pin_file.exists():
        digest = _sha256(resolved)
        return 0, [f"NOTICE: no {PIN_FILE_NAME} found; {rel} was not verified "
                   f"(sha256={digest})."]

    try:
        raw = json.loads(pin_file.read_text())
    except (OSError, ValueError) as e:
        return 1, [f"malformed pin file {PIN_FILE_NAME}: invalid JSON ({e})"]

    if not isinstance(raw, dict):
        return 1, [f"malformed pin file {PIN_FILE_NAME}: top level must be an object "
                   f"(design path -> pin record)"]

    for key, val in raw.items():
        if not isinstance(val, dict) or not isinstance(val.get("sha256"), str) or not val.get("sha256"):
            return 1, [f"malformed pin file {PIN_FILE_NAME}: entry for '{key}' is missing 'sha256'"]

    entry = raw.get(rel)
    if entry is None:
        digest = _sha256(resolved)
        return 1, [f"{rel} has no pin in {PIN_FILE_NAME} (sha256={digest}) — this "
                   f"repository pins its design captures, and this one has no entry."]

    recapture_after = entry.get("recapture_after")
    if recapture_after:
        try:
            deadline = date.fromisoformat(str(recapture_after))
        except ValueError:
            return 1, [f"malformed pin file {PIN_FILE_NAME}: entry for '{rel}' has an "
                       f"invalid recapture_after date '{recapture_after}'"]
        if date.today() > deadline:
            return 1, [f"{rel} pin has expired: recapture_after {recapture_after} has "
                       f"passed — recapture the design before trusting this comparison."]

    digest = _sha256(resolved)
    pinned = entry["sha256"]
    if digest != pinned:
        return 1, [f"{rel}: {DRIFT_MESSAGE}",
                   f"  pinned sha256={pinned}",
                   f"  actual sha256={digest}"]

    lines = [f"PASS: {rel} matches its pin (sha256={digest})",
             f"  captured:   {entry.get('captured_on', '(not recorded)')}",
             f"  source:     {entry.get('source', '(not recorded)')}"]
    triggers = entry.get("recapture_triggers") or []
    lines.append("  recapture_triggers: "
                 + (", ".join(str(t) for t in triggers) if triggers else "(none recorded)"))
    return 0, lines


def _self_test() -> int:
    """Each of the seven ladder outcomes, plus the two directory-path cases, gets
    its own fixture built in a temporary directory at run time and committed
    nowhere — the passing and failing fixtures differ only in the byte or the
    date that should decide the outcome."""
    total = 0
    failures = 0

    def record(name: str, ok: bool, detail: str = "") -> None:
        nonlocal total, failures
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {detail}"))
        total += 1
        failures += 0 if ok else 1

    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        good_bytes = b"<!doctype html><title>ok</title>"
        good_hash = hashlib.sha256(good_bytes).hexdigest()
        pin_file = root / ".karta" / "design-pins.json"
        pin_file.parent.mkdir(parents=True)

        # 1. bytes match the pin -> pass, printing capture date, source, triggers.
        design = root / "design.html"
        design.write_bytes(good_bytes)
        pin_file.write_text(json.dumps({"design.html": {
            "sha256": good_hash, "source": "claude-design://x", "captured_on": "2026-01-01",
            "recapture_triggers": ["the export changes"]}}))
        code, lines = evaluate(design, pin_file, root)
        blob = "\n".join(lines)
        ok = (code == 0 and "PASS" in blob and "2026-01-01" in blob
              and "claude-design://x" in blob and "the export changes" in blob)
        record("a capture whose bytes match its pin passes, printing its capture date, "
               "upstream address, and recapture triggers", ok, f"{code=} {lines=}")

        # 2. bytes disagree with the pin -> fail with the exact drift clause + both hashes.
        drifted = root / "drifted.html"
        drifted.write_text("<!doctype html><title>different</title>")
        pin_file.write_text(json.dumps({"drifted.html": {"sha256": good_hash}}))
        code, lines = evaluate(drifted, pin_file, root)
        blob = "\n".join(lines)
        ok = code == 1 and DRIFT_MESSAGE in blob and "pinned sha256=" in blob and "actual sha256=" in blob
        record("a capture whose bytes differ from its pin fails with the runbook's exact "
               "clause and prints both hashes", ok, f"{code=} {lines=}")

        # 3. inside the repo, pin file present, no entry of its own -> fail, naming the
        #    path and printing its hash (one of the three hash-printing outcomes).
        unpinned = root / "unpinned.html"
        unpinned.write_bytes(good_bytes)
        pin_file.write_text(json.dumps({"design.html": {"sha256": good_hash}}))
        code, lines = evaluate(unpinned, pin_file, root)
        blob = "\n".join(lines)
        ok = code == 1 and "unpinned.html" in blob and "no pin" in blob and "sha256=" in blob
        record("a design inside the repo with a pin file present but no entry of its own "
               "fails, naming the path and printing its hash", ok, f"{code=} {lines=}")

        # 4. recapture_after passed -> fail naming the date; the same entry with no
        #    recapture_after key passes, so the rung is opt-in.
        expired = root / "expired.html"
        expired.write_bytes(good_bytes)
        pin_file.write_text(json.dumps({"expired.html": {"sha256": good_hash,
                                                          "recapture_after": "2000-01-01"}}))
        code_a, lines_a = evaluate(expired, pin_file, root)
        ok_a = code_a == 1 and "recapture_after" in "\n".join(lines_a) and "2000-01-01" in "\n".join(lines_a)
        pin_file.write_text(json.dumps({"expired.html": {"sha256": good_hash}}))
        code_b, lines_b = evaluate(expired, pin_file, root)
        ok_b = code_b == 0
        record("a pin whose recapture_after date has passed fails naming that date, and "
               "the same entry with no recapture_after key passes", ok_a and ok_b,
               f"{code_a=} {lines_a=} {code_b=} {lines_b=}")

        # 5. no pin file at all -> pass, notice naming the design, printing its hash
        #    (the third hash-printing outcome).
        no_pinfile_design = root / "no-pinfile.html"
        no_pinfile_design.write_bytes(good_bytes)
        missing_pin_file = root / "does-not-exist.json"
        code, lines = evaluate(no_pinfile_design, missing_pin_file, root)
        blob = "\n".join(lines)
        ok = code == 0 and "no-pinfile.html" in blob and "sha256=" in blob
        record("a repository with no pin file at all passes and prints a notice naming "
               "the design it did not verify and its hash", ok, f"{code=} {lines=}")

        # 6. design resolved from outside the repository -> pass with a notice, no
        #    fingerprint it could ever have had.
        with tempfile.TemporaryDirectory() as outside_td:
            outside_design = Path(outside_td) / "outside.html"
            outside_design.write_bytes(good_bytes)
            code, lines = evaluate(outside_design, pin_file, root)
            ok = code == 0 and "outside the repository" in "\n".join(lines) and "cannot be pinned" in "\n".join(lines)
            record("a design resolved from outside the repository passes with a notice "
                   "saying it cannot be pinned", ok, f"{code=} {lines=}")

        # 7. malformed pin file — not an object, or an entry missing sha256 — is
        #    reported as malformed and NEVER as a matching capture.
        mal_ok = True
        mal_detail = ""
        for text in ("[1, 2, 3]", json.dumps({"design.html": {"source": "x"}})):
            pin_file.write_text(text)
            code, lines = evaluate(design, pin_file, root)
            blob = "\n".join(lines)
            if not (code == 1 and "malformed pin file" in blob and "PASS" not in blob):
                mal_ok = False
                mal_detail = f"{text=} {code=} {lines=}"
        record("a malformed pin file — not an object, or an entry missing sha256 — is "
               "reported as malformed and never as a matching capture", mal_ok, mal_detail)

        # 8/9. a directory-valued design path is resolved through serve_design's own
        # resolve_design_file before anything is hashed, so the check pins the file
        # the serve step will actually serve — one case where the chosen file is
        # pinned, one where it has drifted.
        subdir = root / "sub"
        subdir.mkdir()
        chosen = subdir / "demo.standalone.html"
        chosen.write_bytes(good_bytes)
        rel_chosen = chosen.relative_to(root).as_posix()
        pin_file.write_text(json.dumps({rel_chosen: {"sha256": good_hash}}))
        code, lines = evaluate(subdir, pin_file, root)
        ok = code == 0 and "PASS" in "\n".join(lines)
        record("a directory-valued design path resolves through resolve_design_file "
               "before hashing, and a pinned chosen file passes", ok, f"{code=} {lines=}")

        pin_file.write_text(json.dumps({rel_chosen: {"sha256": "0" * 64}}))
        code, lines = evaluate(subdir, pin_file, root)
        ok = code == 1 and DRIFT_MESSAGE in "\n".join(lines)
        record("the same directory-valued path fails when the file resolve_design_file "
               "chooses has drifted from its pin", ok, f"{code=} {lines=}")

    print(f"self-test: {total - failures}/{total} cases passed")
    if total != 9:
        print(f"FAIL: expected exactly 9 self-test cases, ran {total} "
              f"(the seven ladder outcomes, of which the malformed pin file is the "
              f"seventh, plus the two directory-path cases)")
        return 1
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Check a design capture's bytes against its recorded pin in {PIN_FILE_NAME}.")
    parser.add_argument("--design-path", help="Design HTML file or directory containing one.")
    parser.add_argument("--pin-file", help=f"Path to the pin file. Defaults to {PIN_FILE_NAME} "
                        "resolved from the repo root.")
    parser.add_argument("--repo-root", help="Repository root the pin file and the resolved "
                        "design path are checked against. Defaults to the current working "
                        "directory.")
    parser.add_argument("--self-test", action="store_true", help="Run the embedded fixtures and exit.")
    args = parser.parse_args()

    if args.self_test:
        raise SystemExit(_self_test())
    if not args.design_path:
        parser.error("--design-path is required unless --self-test is used")

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd().resolve()
    pin_file = (Path(args.pin_file).expanduser().resolve() if args.pin_file
               else repo_root / ".karta" / "design-pins.json")

    code, lines = evaluate(Path(args.design_path), pin_file, repo_root)
    for line in lines:
        print(line)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
