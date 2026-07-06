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
    shared = {p.relative_to(SHARED).as_posix(): p.read_text()
              for p in SHARED.rglob("*.md")}
    for skill_dir in sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir()):
        if skill_dir.name == "_shared":
            continue
        refs = skill_dir / "references"
        if not refs.is_dir():
            continue
        for ref in refs.rglob("*.md"):
            rel = ref.relative_to(refs).as_posix()
            if rel in shared and ref.read_text() != shared[rel]:
                errors.append(f"{ref.relative_to(ROOT)} drifted from skills/_shared/{rel}")
        # A references dir that mirrors a shared subdir (e.g. sme/) must carry
        # every file in it — a new shared file must land in all mirrors.
        for sub in (p for p in SHARED.iterdir() if p.is_dir()):
            if not (refs / sub.name).is_dir():
                continue
            for rel in (r for r in shared if r.startswith(f"{sub.name}/")):
                if not (refs / rel).is_file():
                    errors.append(f"{(refs / rel).relative_to(ROOT)} missing (skills/_shared/{rel} not copied)")
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
