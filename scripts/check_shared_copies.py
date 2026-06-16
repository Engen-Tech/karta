# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Assert each skills/*/references/<f> copied from skills/_shared/<f> matches its source.

Usage: uv run scripts/check_shared_copies.py --self-test
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "skills" / "_shared"


def check() -> list[str]:
    errors: list[str] = []
    shared = {p.name: p.read_text() for p in SHARED.glob("*.md")}
    for ref in (ROOT / "skills").glob("*/references/*.md"):
        if ref.parent.parent.name == "_shared":
            continue
        if ref.name in shared and ref.read_text() != shared[ref.name]:
            errors.append(f"{ref.relative_to(ROOT)} drifted from skills/_shared/{ref.name}")
    return errors


def main() -> int:
    argparse.ArgumentParser().parse_known_args()
    errors = check()
    if errors:
        print("SHARED COPIES: DRIFT")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("SHARED COPIES: IN SYNC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
