# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Mirror the canonical skills into Codex real-directory projections.

Codex discovers repo-local skills under `.agents/skills/<name>/SKILL.md`. karta keeps
its canonical skills at `skills/<name>/` (Claude-native). Symlinks are unreliable for
this on Windows (openai/codex#8400), so the mirror is committed real directories kept
byte-identical to the source by this generator and guarded by validate_plugin.py.
Externally managed, cross-runtime skills declared in `skills-lock.json` may share the
`.agents/skills/` root; they are preserved but never copied into karta's plugin.

Codex marketplace installs also expect marketplace entries to point at a child plugin
directory (`./plugins/<name>`). The `plugins/karta/` install projection is generated
from the same canonical files and uses real directories for the same cross-platform
reason.

`skills/_shared/` has no SKILL.md and is not a skill; it is never mirrored (its files
are already copied into each skill's own `references/`).

Projections carry the canonical file's executable bit: write mode projects it onto
both mirrors and `--check` reports a projection whose bit drifted.

External skill integrity: a lock entry's `computedHash` is the sha256 of the external
skill's SKILL.md bytes as synced locally. Write mode recomputes and stores it
(re-baseline); `--check` and scripts/validate_plugin.py recompute it and fail, naming
the skill, on mismatch. A degraded lock state — an entry missing fields, or the entry
absent entirely while the skill's directory exists — is recompute-needed, surfaced as
a `--check` failure with a recompute instruction, and NEVER destructive: write mode
repairs what it can and always leaves the external skill's files in place. Only a
deliberate removal — a canonical skill deleted from `skills/` while its generated
projections linger — is cleaned up.

Usage:
  uv run scripts/sync_codex_skills.py            # write the Codex projections
  uv run scripts/sync_codex_skills.py --check     # report drift, exit 0/1 (no writes)
  uv run scripts/sync_codex_skills.py --self-test # drills on a synthetic tree
"""
from __future__ import annotations
import argparse, hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
MIRROR = ROOT / ".agents" / "skills"
CODEX_PLUGIN = ROOT / ".codex-plugin"
INSTALL_PLUGIN = ROOT / "plugins" / "karta"
INSTALL_SKILLS = INSTALL_PLUGIN / "skills"
INSTALL_CODEX_PLUGIN = INSTALL_PLUGIN / ".codex-plugin"
SKILLS_LOCK = ROOT / "skills-lock.json"
LOCK_FIELDS = ("source", "sourceType", "skillPath", "computedHash")
RECOMPUTE_HINT = "recompute-needed: run uv run scripts/sync_codex_skills.py"


def _is_artifact(p: Path) -> bool:
    """Build artifacts that must never be mirrored or compared — running the skills'
    scripts leaves `__pycache__/*.pyc` in their dirs (gitignored), which would
    otherwise drift between canonical and mirror and trip --check."""
    return "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo")


def skill_dirs() -> list[Path]:
    return sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))


def _lock_skills(lockfile: Path = SKILLS_LOCK) -> dict[str, dict]:
    """Every named lock entry, complete or degraded; {} on an absent/malformed lock."""
    if not lockfile.is_file():
        return {}
    try:
        data = json.loads(lockfile.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    skills = data.get("skills")
    if not isinstance(skills, dict):
        return {}
    return {name: meta for name, meta in skills.items()
            if isinstance(name, str) and name and isinstance(meta, dict)}


def _entry_complete(meta: dict) -> bool:
    return all(isinstance(meta.get(field), str) and meta[field] for field in LOCK_FIELDS)


def locked_external_skill_names(lockfile: Path = SKILLS_LOCK) -> set[str]:
    """Return complete external skill entries from the cross-runtime installer lock."""
    return {name for name, meta in _lock_skills(lockfile).items() if _entry_complete(meta)}


def external_mirror_skill_names() -> set[str]:
    """Locked external skills that do not collide with karta's canonical skills."""
    canonical = {path.name for path in skill_dirs()}
    return locked_external_skill_names() - canonical


def protected_external_names() -> set[str]:
    """External skill names shielded from orphan cleanup: every lock entry (complete
    or degraded) plus any mirror-only dir absent from the install projection (an
    external whose lock entry was lost — externals are never projected to
    plugins/karta/). A canonical skill deleted from skills/ leaves generated dirs in
    BOTH projection roots, so those stay cleanable (deliberate removal)."""
    canonical = {p.name for p in skill_dirs()}
    mirror_dirs = {p.name for p in MIRROR.iterdir() if p.is_dir()} if MIRROR.exists() else set()
    return (set(_lock_skills()) | (mirror_dirs - install_projection_skill_names())) - canonical


def _external_skill_hash(name: str) -> str | None:
    p = MIRROR / name / "SKILL.md"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def external_integrity_problems() -> list[str]:
    """Recompute each external skill's SKILL.md content hash against skills-lock.json.

    Shared by --check and scripts/validate_plugin.py so the two surfaces can never
    disagree. Reports, naming the skill: a content-hash mismatch (tampered/modified),
    a degraded lock entry, a locked-but-missing skill, and a skill directory with no
    lock entry. Read-only — repair is write mode's job."""
    problems: list[str] = []
    canonical = {p.name for p in skill_dirs()}
    entries = {n: m for n, m in _lock_skills().items() if n not in canonical}
    for name, meta in sorted(entries.items()):
        local = _external_skill_hash(name)
        if not _entry_complete(meta):
            missing = [f for f in LOCK_FIELDS if not (isinstance(meta.get(f), str) and meta[f])]
            problems.append(f"skills-lock.json entry '{name}' is degraded "
                            f"(missing {', '.join(missing)}) — {RECOMPUTE_HINT}")
        elif local is None:
            problems.append(f"external skill '{name}' is locked but "
                            f".agents/skills/{name}/SKILL.md is missing — "
                            "reinstall it or remove the lock entry")
        elif local != meta["computedHash"]:
            problems.append(f"external skill '{name}' content hash mismatch: "
                            f".agents/skills/{name}/SKILL.md no longer matches "
                            "skills-lock.json computedHash (tampered or modified)")
    for name in sorted(protected_external_names() - set(entries)):
        problems.append(f"external skill '{name}' has no skills-lock.json entry — "
                        f"files kept; {RECOMPUTE_HINT}")
    return problems


def _rebaseline_lock() -> int:
    """Write-mode re-baseline: store the sha256 of each external skill's SKILL.md
    bytes as synced locally into its lock entry's computedHash. A degraded entry is
    repaired in place (recompute-needed, never destructive). Returns entries updated."""
    if not SKILLS_LOCK.is_file():
        return 0
    try:
        data = json.loads(SKILLS_LOCK.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    skills = data.get("skills")
    if not isinstance(skills, dict):
        return 0
    canonical = {p.name for p in skill_dirs()}
    updated = 0
    for name, meta in skills.items():
        if not isinstance(name, str) or name in canonical or not isinstance(meta, dict):
            continue
        local = _external_skill_hash(name)
        if local and meta.get("computedHash") != local:
            meta["computedHash"] = local
            updated += 1
    if updated:
        SKILLS_LOCK.write_text(json.dumps(data, indent=2) + "\n")
    return updated


def _is_external_mirror_path(path: Path, external: set[str]) -> bool:
    try:
        parts = path.relative_to(MIRROR).parts
    except ValueError:
        return False
    return bool(parts) and parts[0] in external


def expected() -> tuple[dict[Path, tuple[bytes, int]], set[str]]:
    """Map each mirror file path to its expected (bytes, exec bits); plus skill names."""
    files: dict[Path, tuple[bytes, int]] = {}
    names: set[str] = set()
    for sd in skill_dirs():
        names.add(sd.name)
        for f in sd.rglob("*"):
            if f.is_file() and not _is_artifact(f):
                files[MIRROR / sd.name / f.relative_to(sd)] = (
                    f.read_bytes(), f.stat().st_mode & 0o111)
    return files, names


def expected_install_projection() -> dict[Path, tuple[bytes, int]]:
    """Map each marketplace install projection file to its expected (bytes, exec bits)."""
    files: dict[Path, tuple[bytes, int]] = {}
    for f in CODEX_PLUGIN.rglob("*"):
        if f.is_file():
            files[INSTALL_CODEX_PLUGIN / f.relative_to(CODEX_PLUGIN)] = (
                f.read_bytes(), f.stat().st_mode & 0o111)
    for sd in skill_dirs():
        for f in sd.rglob("*"):
            if f.is_file() and not _is_artifact(f):
                files[INSTALL_SKILLS / sd.name / f.relative_to(sd)] = (
                    f.read_bytes(), f.stat().st_mode & 0o111)
    return files


def mirror_files() -> list[Path]:
    if not MIRROR.exists():
        return []
    external = protected_external_names()
    return [
        p
        for p in MIRROR.rglob("*")
        if p.is_file() and not _is_artifact(p) and not _is_external_mirror_path(p, external)
    ]


def mirror_skill_names() -> set[str]:
    if not MIRROR.exists():
        return set()
    return {p.name for p in MIRROR.iterdir() if p.is_dir()} - protected_external_names()


def install_projection_files() -> list[Path]:
    roots = [INSTALL_CODEX_PLUGIN, INSTALL_SKILLS]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(p for p in root.rglob("*") if p.is_file() and not _is_artifact(p))
    return files


def install_projection_skill_names() -> set[str]:
    return {p.name for p in INSTALL_SKILLS.iterdir() if p.is_dir()} if INSTALL_SKILLS.exists() else set()


def projection_drift(want: dict[Path, tuple[bytes, int]], have: set[Path], label: str) -> list[str]:
    problems: list[str] = []
    for p, (content, exec_bits) in sorted(want.items()):
        if not p.exists():
            problems.append(f"{p.relative_to(ROOT)} missing from {label}")
        elif p.read_bytes() != content:
            problems.append(f"{p.relative_to(ROOT)} differs from canonical")
        elif (p.stat().st_mode & 0o111) != exec_bits:
            problems.append(f"{p.relative_to(ROOT)} executable bit differs from canonical")
    for p in sorted(have - set(want)):
        problems.append(f"{p.relative_to(ROOT)} orphaned (no canonical source)")
    return problems


def _write_projection(want: dict[Path, tuple[bytes, int]]) -> int:
    """Bring each projection file to canonical bytes and exec bits; return files touched."""
    wrote = 0
    for p, (content, exec_bits) in sorted(want.items()):
        changed = False
        if not p.exists() or p.read_bytes() != content:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            changed = True
        mode = p.stat().st_mode
        if (mode & 0o111) != exec_bits:
            p.chmod((mode & ~0o111) | exec_bits)
            changed = True
        wrote += changed
    return wrote


def _selftest_tree(root: Path) -> None:
    """A miniature repo shape running this script's copy — never the real repo."""
    for name in ("demo-a", "demo-b"):
        d = root / "skills" / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: fixture\n---\nfixture\n")
        (d / "scripts" / "tool.py").write_text("print('fixture')\n")
    (root / "scripts").mkdir()
    shutil.copy(Path(__file__).resolve(), root / "scripts" / "sync_codex_skills.py")
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text("{}\n")
    ext = root / ".agents" / "skills" / "ext-demo"
    ext.mkdir(parents=True)
    (ext / "SKILL.md").write_text("# external fixture\n")
    (root / "skills-lock.json").write_text(json.dumps({"version": 1, "skills": {
        "ext-demo": {"source": "bench/fixture", "sourceType": "github",
                     "skillPath": "e/SKILL.md", "computedHash": "0" * 64}}}, indent=2) + "\n")


def _sync(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "sync_codex_skills.py"), *args],
        capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


def self_test() -> int:
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        # Lock parsing and mirror-path filtering (pure functions, temp lockfiles).
        checks.append(("absent lockfile resolves no external skills",
                       locked_external_skill_names(base / "missing.json") == set()))
        malformed = base / "malformed.json"
        malformed.write_text("{")
        checks.append(("malformed lockfile resolves no external skills",
                       locked_external_skill_names(malformed) == set()))
        valid = base / "skills-lock.json"
        valid.write_text(json.dumps({"skills": {
            "external": {field: "value" for field in LOCK_FIELDS},
            "incomplete": {"source": "value"}}}))
        checks.append(("only complete lock entries resolve as external skills",
                       locked_external_skill_names(valid) == {"external"}))
        checks.append(("external mirror path recognized",
                       _is_external_mirror_path(MIRROR / "external" / "SKILL.md", {"external"})))
        checks.append(("managed mirror path not treated as external",
                       not _is_external_mirror_path(MIRROR / "karta-plan" / "SKILL.md", {"external"})))

        # Full drills against a synthetic tree (subprocess on this script's copy).
        root = base / "tree"
        _selftest_tree(root)
        ext_hash = hashlib.sha256(b"# external fixture\n").hexdigest()
        skill_md = root / ".agents" / "skills" / "ext-demo" / "SKILL.md"

        code, _out = _sync(root)
        lock = json.loads((root / "skills-lock.json").read_text())
        checks.append(("write mode re-baselines computedHash to the local content sha256",
                       code == 0 and lock["skills"]["ext-demo"]["computedHash"] == ext_hash))
        code, out = _sync(root, "--check")
        checks.append(("recompute over the re-baselined lock passes on the untouched tree",
                       code == 0 and "IN SYNC" in out))

        tool = root / "skills" / "demo-a" / "scripts" / "tool.py"
        tool.chmod(tool.stat().st_mode | 0o111)
        code, out = _sync(root, "--check")
        checks.append(("--check flags a projection missing the canonical executable bit",
                       code != 0 and "executable bit" in out))
        code, _out = _sync(root)
        projections = (root / ".agents" / "skills" / "demo-a" / "scripts" / "tool.py",
                       root / "plugins" / "karta" / "skills" / "demo-a" / "scripts" / "tool.py")
        checks.append(("canonical +x projects the executable bit onto both projections",
                       code == 0 and all(p.stat().st_mode & 0o100 for p in projections)))

        skill_md.write_bytes(skill_md.read_bytes() + b"x")
        code, out = _sync(root, "--check")
        checks.append(("tampered external SKILL.md fails --check naming the skill",
                       code != 0 and "ext-demo" in out))
        skill_md.write_text("# external fixture\n")

        lock = json.loads((root / "skills-lock.json").read_text())
        lock["skills"]["ext-demo"].pop("computedHash")
        (root / "skills-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
        code, out = _sync(root, "--check")
        checks.append(("degraded lock entry fails --check with a recompute instruction",
                       code != 0 and "ext-demo" in out and "recompute" in out))
        code, _out = _sync(root)
        lock = json.loads((root / "skills-lock.json").read_text())
        checks.append(("degraded lock entry survives write mode and is repaired",
                       code == 0 and skill_md.is_file()
                       and lock["skills"]["ext-demo"].get("computedHash") == ext_hash))

        (root / "skills-lock.json").write_text(
            json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")
        code, _out = _sync(root)
        checks.append(("missing lock entry keeps the external skill's files",
                       code == 0 and skill_md.is_file()))
        code, out = _sync(root, "--check")
        checks.append(("missing lock entry fails --check with a recompute instruction",
                       code != 0 and "ext-demo" in out and "recompute" in out))

        shutil.rmtree(root / "skills" / "demo-b")
        code, _out = _sync(root)
        checks.append(("deliberate canonical removal cleans both projections",
                       code == 0
                       and not (root / ".agents" / "skills" / "demo-b").exists()
                       and not (root / "plugins" / "karta" / "skills" / "demo-b").exists()
                       and skill_md.is_file()))

    passed = sum(ok for _name, ok in checks)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--self-test", action="store_true", help="drills on a synthetic tree")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    want, names = expected()
    install_want = expected_install_projection()
    if not names:
        raise SystemExit("no skills found under skills/*/SKILL.md")
    have = set(mirror_files())
    install_have = set(install_projection_files())
    orphan_dirs = sorted(mirror_skill_names() - names)
    install_orphan_dirs = sorted(install_projection_skill_names() - names)

    if args.check:
        problems = projection_drift(want, have, "mirror")
        for name in orphan_dirs:
            problems.append(f".agents/skills/{name} orphaned (no skills/{name})")
        problems.extend(projection_drift(install_want, install_have, "install projection"))
        for name in install_orphan_dirs:
            problems.append(f"plugins/karta/skills/{name} orphaned (no skills/{name})")
        problems.extend(external_integrity_problems())
        if problems:
            print("CODEX SKILLS PROJECTIONS: DRIFT")
            for m in problems:
                print(f"  - {m} — run: uv run scripts/sync_codex_skills.py")
            return 1
        print("CODEX SKILLS PROJECTIONS: IN SYNC")
        return 0

    # write mode — external skills are never removed here: orphan cleanup only ever
    # sees canonical projections (protected_external_names shields externals).
    for name in orphan_dirs:
        shutil.rmtree(MIRROR / name)
        print(f"removed orphan .agents/skills/{name}")
    for p in sorted(have - set(want)):
        if p.exists():  # not already gone with an orphan dir above (stale have-set)
            p.unlink()
            print(f"removed {p.relative_to(ROOT)}")
    for name in install_orphan_dirs:
        shutil.rmtree(INSTALL_SKILLS / name)
        print(f"removed orphan plugins/karta/skills/{name}")
    for p in sorted(install_have - set(install_want)):
        if p.exists():
            p.unlink()
            print(f"removed {p.relative_to(ROOT)}")
    wrote = _write_projection(want)
    install_wrote = _write_projection(install_want)
    rebaselined = _rebaseline_lock()
    if rebaselined:
        print(f"recomputed computedHash for {rebaselined} external skill(s) in skills-lock.json")
    for name in sorted(protected_external_names() - set(_lock_skills())):
        print(f"kept .agents/skills/{name} — no skills-lock.json entry "
              "(recompute-needed, never removed)")
    print(
        "codex projections in sync "
        f"({len(want)} mirror files, {wrote} written/updated; "
        f"{len(install_want)} install files, {install_wrote} written/updated)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
