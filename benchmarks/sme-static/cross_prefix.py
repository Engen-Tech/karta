#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Cross-file rule-id prefix audit over the built-in packs plus all overlays.

Builds the prefix->basename map from every checklist id (active items and
tombstones) and fails on (a) one prefix claimed by two different basenames or
(b) a pack using a PREFIXES registry entry registered to a different pack.
The registry and id grammar are imported from the canonical
skills/karta-kaizen/scripts/validate_packs.py — one registry, no copy.

Usage:
  python3 cross_prefix.py --self-test
  python3 cross_prefix.py [--target <karta-root>] [--overlays <dir,dir,...>]

Self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary.
"""
from __future__ import annotations
import argparse, importlib.util, sys, tempfile
from pathlib import Path

VALIDATOR = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"
NOT_A_PACK = {"platform-native.md"}


def _load_validator(karta: Path):
    spec = importlib.util.spec_from_file_location("validate_packs", karta / VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pack_prefixes(text: str, vp) -> set[str]:
    prefixes: set[str] = set()
    for ln in text.splitlines():
        if m := vp.ITEM_RE.match(ln):
            prefixes.add(m.group(1))
        elif m := vp.TOMBSTONE_RE.match(ln):
            prefixes.add(m.group(1))
    return prefixes


def scan(pack_files: list[Path], vp) -> list[str]:
    """Return findings (empty == clean). Same-basename files (builtin + its
    overlays) legitimately share a prefix; two different basenames may not."""
    owner_by_prefix: dict[str, dict[str, list[str]]] = {}  # prefix -> basename -> [paths]
    for f in pack_files:
        try:
            prefixes = _pack_prefixes(f.read_text(), vp)
        except OSError as e:
            return [f"{f}: unreadable ({e})"]
        for pfx in prefixes:
            owner_by_prefix.setdefault(pfx, {}).setdefault(f.stem, []).append(str(f))

    findings: list[str] = []
    registered_owner = {v: k for k, v in vp.PREFIXES.items()}
    for pfx, users in sorted(owner_by_prefix.items()):
        if len(users) > 1:
            findings.append(f"prefix collision: '{pfx}' claimed by "
                            f"{', '.join(sorted(users))}")
        reg = registered_owner.get(pfx)
        for stem in sorted(users):
            if reg and stem != reg:
                findings.append(f"foreign PREFIXES use: '{stem}' uses prefix '{pfx}' "
                                f"registered to '{reg}' ({users[stem][0]})")
    return findings


def collect(karta: Path, overlay_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for d in [karta / "skills" / "_shared" / "sme", *overlay_dirs]:
        if d.is_dir():
            files.extend(p for p in sorted(d.glob("*.md")) if p.name not in NOT_A_PACK)
    return files


# --- self-test -----------------------------------------------------------------

def _pack(name: str, ids: list[str]) -> str:
    lines = "\n".join(f"- [ ] {i} — Rule text." for i in ids)
    return f"---\nname: {name}\ndescription: d\nalways: true\n---\n## Review checklist\n{lines}\n"


def _run_self_test() -> int:
    karta = Path(__file__).resolve().parents[2]
    vp = _load_validator(karta)
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def write(rel: str, name: str, ids: list[str]) -> Path:
            p = root / rel / f"{name}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_pack(name, ids))
            return p

        clean = [write("a", "minimalism", ["min.1", "min.2"]),
                 write("b", "terraform", ["tf.1"])]
        check("clean set: no collisions, no foreign use", scan(clean, vp) == [])

        overlay_min = write("c", "minimalism", ["min.1"])
        check("builtin + overlay of the same basename share a prefix legitimately",
              scan(clean + [overlay_min], vp) == [])

        squatter = write("d", "ansible", ["tf.1"])
        got = scan(clean + [squatter], vp)
        check("prefix claimed by two basenames is a collision",
              any("prefix collision: 'tf'" in f for f in got), repr(got))

        foreign = write("e", "puppet", ["min.9"])
        got = scan(clean + [foreign], vp)
        check("overlay using a PREFIXES entry registered to a different pack fails",
              any("foreign PREFIXES use: 'puppet' uses prefix 'min'" in f for f in got),
              repr(got))

        tomb = root / "f" / "chef.md"
        tomb.parent.mkdir(parents=True)
        tomb.write_text("---\nname: chef\ndescription: d\nalways: true\n---\n"
                        "## Review checklist\n- ~~ng.1~~ retired: moved.\n- [ ] chef.1 — R.\n")
        got = scan(clean + [tomb], vp)
        check("tombstone ids count for the map too (retired ng.1 in 'chef' is foreign)",
              any("foreign PREFIXES use: 'chef' uses prefix 'ng'" in f for f in got),
              repr(got))

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
                    help="comma-separated overlay dirs (default: <target>/.karta/sme)")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    karta = args.target.resolve()
    overlays = ([Path(s) for s in args.overlays.split(",") if s]
                if args.overlays else [karta / ".karta" / "sme"])
    findings = scan(collect(karta, overlays), _load_validator(karta))
    for f in findings:
        print(f)
    print(f"{'CLEAN' if not findings else 'FINDINGS'}: {len(findings)} prefix finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
