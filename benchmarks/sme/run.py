#!/usr/bin/env python3
"""Measure an SME pack's effect: compare an A (pack applied) vs B (pack absent) diff.

Honesty rule: report the A/B delta on a benchmark task, never a
per-repo "you saved X" number — the unbuilt version never existed in a live repo.

Usage:
  python run.py --with a.diff --without b.diff [--label "date-picker"]
  python run.py --self-test
"""
import argparse, re, sys
from pathlib import Path

# A dependency pin, matched against the line content after the leading "+":
#   JSON manifest style:      "vue": "^3.4.0"   (package.json pins; ^ or ~ optional)
#   requirements/pin style:   fastapi==0.100.0  (pip pins: ==, >=, <=, ~= then a digit)
# The package token must start the line content, so code like `if x == 1:`
# or `assert version >= 3` never counts.
_DEP_PIN = re.compile(
    r'^\s*(?:'
    r'"[^"]+":\s*"[\^~]?\d'
    r'|[A-Za-z0-9._\[\]-]+(?:==|>=|<=|~=)\d'
    r')'
)


def added_lines(diff_text: str) -> int:
    return sum(1 for ln in diff_text.splitlines()
               if ln.startswith("+") and not ln.startswith("+++"))


def new_deps(diff_text: str) -> int:
    # added lines that look like a dependency pin in a manifest hunk
    return sum(1 for ln in diff_text.splitlines()
               if ln.startswith("+") and not ln.startswith("+++")
               and _DEP_PIN.match(ln[1:]))


def _run_self_test() -> int:
    cases = [
        ("json pin counts", '+    "vue": "^3.4.0",', 1),
        ("json tilde pin counts", '+  "lodash": "~4.17.21",', 1),
        ("pip == pin counts", "+fastapi==0.100.0", 1),
        ("pip >= pin counts", "+numpy>=1.26.0", 1),
        ("pip extras pin counts", "+uvicorn[standard]~=0.23", 1),
        ("code == does not count", "+    if x == 1:", 0),
        ("code >= does not count", "+    assert version >= 3", 0),
        ("+++ header never counts", "+++ b/requirements.txt", 0),
        ("+++ header json-ish never counts", '+++ b/"x": "1.0"', 0),
    ]
    failures = 0
    for name, line, expected in cases:
        got = new_deps(line)
        ok = got == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: new_deps={got} expected={expected}")
        failures += 0 if ok else 1
    total = len(cases)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with", dest="with_", type=Path)
    ap.add_argument("--without", type=Path)
    ap.add_argument("--label", default="task")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _run_self_test()
    if not a.with_ or not a.without:
        ap.error("--with and --without are required (unless --self-test)")
    wa, wo = a.with_.read_text(), a.without.read_text()
    la, lo, da, do = added_lines(wa), added_lines(wo), new_deps(wa), new_deps(wo)
    print(f"## SME pack A/B — {a.label}")
    print(f"  added LOC   with pack: {la:>5}   without: {lo:>5}   delta: {la - lo:+}")
    print(f"  new deps    with pack: {da:>5}   without: {do:>5}   delta: {da - do:+}")
    print("  (A/B delta on this benchmark task — not a per-repo savings figure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
