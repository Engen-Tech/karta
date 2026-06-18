# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Mirror the canonical skills into `.agents/skills/` as real directories for Codex.

Codex discovers repo-local skills under `.agents/skills/<name>/SKILL.md`. karta keeps
its canonical skills at `skills/<name>/` (Claude-native). Symlinks are unreliable for
this on Windows (openai/codex#8400), so the mirror is committed real directories kept
byte-identical to the source by this generator and guarded by validate_plugin.py.

`skills/_shared/` has no SKILL.md and is not a skill; it is never mirrored (its files
are already copied into each skill's own `references/`).

Usage:
  uv run scripts/sync_codex_skills.py            # write the mirror
  uv run scripts/sync_codex_skills.py --check     # report drift, exit 0/1 (no writes)
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MIRROR = ROOT / ".agents" / "skills"


def skill_dirs() -> list[Path]:
    return sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))


def expected() -> tuple[dict[Path, bytes], set[str]]:
    """Map each mirror file path to its expected bytes; plus the set of skill names."""
    files: dict[Path, bytes] = {}
    names: set[str] = set()
    for sd in skill_dirs():
        names.add(sd.name)
        for f in sd.rglob("*"):
            if f.is_file():
                files[MIRROR / sd.name / f.relative_to(sd)] = f.read_bytes()
    return files, names


def mirror_files() -> list[Path]:
    return [p for p in MIRROR.rglob("*") if p.is_file()] if MIRROR.exists() else []


def mirror_skill_names() -> set[str]:
    return {p.name for p in MIRROR.iterdir() if p.is_dir()} if MIRROR.exists() else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    args = ap.parse_args()

    want, names = expected()
    if not names:
        raise SystemExit("no skills found under skills/*/SKILL.md")
    have = set(mirror_files())
    orphan_dirs = sorted(mirror_skill_names() - names)

    if args.check:
        problems: list[str] = []
        for p, content in sorted(want.items()):
            if not p.exists():
                problems.append(f"{p.relative_to(ROOT)} missing from mirror")
            elif p.read_bytes() != content:
                problems.append(f"{p.relative_to(ROOT)} differs from canonical")
        for p in sorted(have - set(want)):
            problems.append(f"{p.relative_to(ROOT)} orphaned (no canonical source)")
        for name in orphan_dirs:
            problems.append(f".agents/skills/{name} orphaned (no skills/{name})")
        if problems:
            print("CODEX SKILLS MIRROR: DRIFT")
            for m in problems:
                print(f"  - {m} — run: uv run scripts/sync_codex_skills.py")
            return 1
        print("CODEX SKILLS MIRROR: IN SYNC")
        return 0

    # write mode
    import shutil
    for name in orphan_dirs:
        shutil.rmtree(MIRROR / name)
        print(f"removed orphan .agents/skills/{name}")
    for p in sorted(have - set(want)):
        p.unlink()
        print(f"removed {p.relative_to(ROOT)}")
    wrote = 0
    for p, content in sorted(want.items()):
        if not p.exists() or p.read_bytes() != content:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            wrote += 1
    print(f"mirror in sync ({len(want)} files, {wrote} written/updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
