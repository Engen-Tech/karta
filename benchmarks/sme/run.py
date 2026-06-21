#!/usr/bin/env python3
"""Measure an SME pack's effect: compare an A (pack applied) vs B (pack absent) diff.

Honesty rule: report the A/B delta on a benchmark task, never a
per-repo "you saved X" number — the unbuilt version never existed in a live repo.

Usage:
  python run.py --with a.diff --without b.diff [--label "date-picker"]
"""
import argparse, re, sys
from pathlib import Path


def added_lines(diff_text: str) -> int:
    return sum(1 for ln in diff_text.splitlines()
               if ln.startswith("+") and not ln.startswith("+++"))


def new_deps(diff_text: str) -> int:
    # crude: added lines that look like a dependency pin in a manifest hunk
    return sum(1 for ln in diff_text.splitlines()
               if ln.startswith("+") and re.search(r'"\S+":\s*"\^?\d|==|>=', ln))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with", dest="with_", required=True, type=Path)
    ap.add_argument("--without", required=True, type=Path)
    ap.add_argument("--label", default="task")
    a = ap.parse_args()
    wa, wo = a.with_.read_text(), a.without.read_text()
    la, lo, da, do = added_lines(wa), added_lines(wo), new_deps(wa), new_deps(wo)
    print(f"## SME pack A/B — {a.label}")
    print(f"  added LOC   with pack: {la:>5}   without: {lo:>5}   delta: {la - lo:+}")
    print(f"  new deps    with pack: {da:>5}   without: {do:>5}   delta: {da - do:+}")
    print("  (A/B delta on this benchmark task — not a per-repo savings figure)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
