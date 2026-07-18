#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for parity-mirror-sync-integrity: drill the mirror-sync machinery.

Wraps the repo's two existing checkers (scripts/check_shared_copies.py and
scripts/validate_plugin.py) and runs the vector card's three integrity drills:

  P1-exec-bit-projection        does an executable bit survive write-mode sync?
  P2-lock-degradation-survival  does an external skill survive a degraded lock entry?
  P3-external-hash-liveness     is tampered external skill content ever noticed?

SAFETY INVARIANT (the card's own): every drill runs against its OWN fresh scratch
copy (mktemp + rsync -a --exclude .git of the --target repo) and every script
invocation addresses the copy's path ($S/scripts/...) — ROOT in the sync scripts
resolves from __file__, so invoking the checkout's own copy would run the P2
rmtree drill destructively against the real repo. The probe asserts the copy path
before each invocation and refuses to run a drill when its copy setup failed —
fail-closed: a setup failure reports status "fail" with a finding, never a silent
skip. Nothing outside the scratch copies is ever mutated.

Evidence: every run writes benchmarks/parity/results/<run-date>-sync-probes.json
({probes: [{id, pass, evidence}], passed, total}, overwriting a same-date file);
scratch copy roots are normalized to the literal $S in every evidence card so a
same-date re-run on an unchanged tree is byte-identical.

Baseline: probe IDs are permanent and a previously-passing ID may never regress —
status "fail" only on regression against the last committed results file;
the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file.
When no tracked baseline exists (the first build run) no regression is possible;
the run records the seeded findings and the probe fails closed if a seeded
finding is absent from its own first baseline. The two wrapped repo checkers stay
fail-closed: either failing flips status to "fail" directly.

On stdout the probe emits the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
and exits 0 whether pass or fail (a nonzero exit means the probe itself crashed).
--self-test drives the three drills against a miniature synthetic tree embedding
the repo's real sync scripts — never the real repo — printing
[PASS]/[FAIL] lines and an N/N checks passed summary; it exits 0 only when the
summary is N/N checks passed, nonzero otherwise.

Usage:
  python3 benchmarks/probes/parity-mirror-sync-integrity.py --target <repo-root>
  python3 benchmarks/probes/parity-mirror-sync-integrity.py --self-test
"""
from __future__ import annotations
import argparse, datetime, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

PROBE_ID = "parity-mirror-sync-integrity"
DRILL_IDS = ("P1-exec-bit-projection", "P2-lock-degradation-survival",
             "P3-external-hash-liveness")
CHECKS = [
    ("shared-copy byte parity", "shared-copies", Path("scripts/check_shared_copies.py")),
    ("plugin manifest validation", "plugin-integrity", Path("scripts/validate_plugin.py")),
]
RESULTS_DIR = Path("benchmarks/parity/results")
RESULTS_GLOB = "benchmarks/parity/results/*-sync-probes.json"
CHECK_TIMEOUT_S = 55   # per wrapped checker
DRILL_TIMEOUT_S = 60   # per subprocess inside a drill
COPY_TIMEOUT_S = 90    # per rsync scratch copy
OUTPUT_TAIL = 20       # evidence keeps the last N lines of each command output
SYNTH_NAME = "bench-external-probe"
# The card's own fixture-derivation snippet: first external mirror skill name.
DERIVE_SNIPPET = (
    'import sys; sys.path.insert(0, sys.argv[1] + "/scripts"); '
    "import sync_codex_skills as m; ext = sorted(m.external_mirror_skill_names()); "
    'print(ext[0] if ext else "")'
)
SEED_SUMMARIES = {
    DRILL_IDS[0]: ("exec bit lost in projection: write-mode sync compares bytes only, "
                   "so +x on the canonical never reaches .agents/skills/ or plugins/karta/"),
    DRILL_IDS[1]: ("degraded lock entry (computedHash removed) made write-mode sync rmtree "
                   "the external skill as an orphan, then crash on the stale have-set"),
    DRILL_IDS[2]: ("tampered external SKILL.md passes sync --check and validate_plugin "
                   "silently — computedHash is never recomputed against content"),
}


def _run(cmd: list[str], cwd: Path, timeout: int = DRILL_TIMEOUT_S) -> tuple[int, str, str]:
    """Run a command; return (exit, stdout, stderr). Never raises."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=str(cwd))
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", f"did not complete: {e}"


def _tail(text: str) -> list[str]:
    return text.strip().splitlines()[-OUTPUT_TAIL:]


def _norm(obj: object, roots: list[str]) -> object:
    """Normalize scratch paths: each drill's copy root becomes the literal $S."""
    if isinstance(obj, str):
        for root in roots:
            obj = obj.replace(root, "$S")
        return obj
    if isinstance(obj, list):
        return [_norm(x, roots) for x in obj]
    if isinstance(obj, dict):
        return {k: _norm(v, roots) for k, v in obj.items()}
    return obj


def _findings_from(slug: str, output: str) -> list[dict]:
    """One finding per checker '  - <error>' line; the headline line as fallback."""
    lines = [ln[4:] for ln in output.splitlines() if ln.startswith("  - ")]
    if not lines:
        lines = [ln for ln in output.splitlines() if ln.strip()][:1] or ["no output"]
    return [{"finding_id": f"{slug}-{n}", "severity": "error", "summary": ln}
            for n, ln in enumerate(lines, 1)]


def _copy_ok(copy_root: Path, source: Path) -> bool:
    """The scratch copy is usable only when it is a distinct, complete-looking tree."""
    return (copy_root.is_dir()
            and copy_root.resolve() != source.resolve()
            and (copy_root / "scripts" / "sync_codex_skills.py").is_file())


def _fresh_copy(source: Path, copy_root: Path) -> bool:
    """mktemp + rsync -a --exclude .git of the source repo; True iff the copy is usable."""
    copy_root.mkdir(parents=True, exist_ok=True)
    code, _out, _err = _run(["rsync", "-a", "--exclude", ".git",
                             f"{source}/", f"{copy_root}/"],
                            cwd=source, timeout=COPY_TIMEOUT_S)
    return code == 0 and _copy_ok(copy_root, source)


def _copy_script(copy_root: Path, rel: str) -> Path:
    """Assert an invocation target lives inside the scratch copy (safety invariant)."""
    script = (copy_root / rel).resolve()
    if not str(script).startswith(str(copy_root.resolve()) + os.sep) or not script.is_file():
        raise RuntimeError(f"refusing script invocation outside the scratch copy: {script}")
    return script


def _derive_external(S: Path) -> tuple[str, bool]:
    """First external mirror skill name in the copy, else synthesize the bench fixture.

    The synthesized bench-external-probe lock entry keeps the P2/P3 denominator
    alive when no live external skill exists in the copied tree."""
    code, out, _err = _run([sys.executable, "-c", DERIVE_SNIPPET, str(S)], cwd=S)
    name = out.strip() if code == 0 else ""
    if name:
        return name, False
    ext_dir = S / ".agents" / "skills" / SYNTH_NAME
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "SKILL.md").write_text("# bench fixture\n")
    lock_path = S / "skills-lock.json"
    try:
        lock = json.loads(lock_path.read_text())
    except (OSError, json.JSONDecodeError):
        lock = {"version": 1, "skills": {}}
    lock.setdefault("skills", {})[SYNTH_NAME] = {
        "source": "bench/fixture", "sourceType": "github",
        "skillPath": "f/SKILL.md", "computedHash": "0" * 64,
    }
    lock_path.write_text(json.dumps(lock, indent=1) + "\n")
    return SYNTH_NAME, True


def _mode(p: Path) -> str:
    return oct(p.stat().st_mode & 0o777) if p.is_file() else "missing"


def drill_p1(S: Path) -> dict:
    """chmod +x a canonical skill script; does write-mode sync project the mode?"""
    targets = sorted(
        p.relative_to(S).as_posix()
        for p in (S / "skills").rglob("*.py")
        if p.is_file()
        and "scripts" in p.relative_to(S).parts
        and "__pycache__" not in p.relative_to(S).parts
    )
    if not targets:
        print("P1-exec-bit-projection: no canonical script under skills/*/scripts/*.py "
              "— probe error, not a drill verdict", file=sys.stderr)
        raise SystemExit(2)
    rel = targets[0]
    canonical = S / rel
    canonical.chmod(canonical.stat().st_mode | 0o111)
    sync = _copy_script(S, "scripts/sync_codex_skills.py")
    chk_code, chk_out, chk_err = _run([sys.executable, str(sync), "--check"], cwd=S)
    wr_code, wr_out, wr_err = _run([sys.executable, str(sync)], cwd=S)
    sub = Path(rel).relative_to("skills")
    mirror = S / ".agents" / "skills" / sub
    install = S / "plugins" / "karta" / "skills" / sub
    ok = all(p.is_file() and (p.stat().st_mode & 0o100) for p in (mirror, install))
    return {
        "id": DRILL_IDS[0],
        "pass": bool(ok),
        "evidence": {
            "target": rel,
            "canonical_mode_after_chmod": _mode(canonical),
            "mirror_projection": {"path": str(mirror), "mode": _mode(mirror)},
            "install_projection": {"path": str(install), "mode": _mode(install)},
            "check": {"exit": chk_code, "output": _tail(chk_out + chk_err)},
            "write": {"exit": wr_code, "output": _tail(wr_out + wr_err)},
        },
    }


def drill_p2(S: Path) -> dict:
    """Delete computedHash from the external entry; does the skill survive write mode?"""
    name, synthesized = _derive_external(S)
    lock_path = S / "skills-lock.json"
    lock = json.loads(lock_path.read_text())
    lock["skills"][name].pop("computedHash", None)
    lock_path.write_text(json.dumps(lock, indent=1) + "\n")
    sync = _copy_script(S, "scripts/sync_codex_skills.py")
    wr_code, wr_out, wr_err = _run([sys.executable, str(sync)], cwd=S)
    survived = (S / ".agents" / "skills" / name).is_dir()
    return {
        "id": DRILL_IDS[1],
        "pass": bool(survived),
        "evidence": {
            "external_skill": name,
            "synthesized_fixture": synthesized,
            "degraded_field": "computedHash",
            "write": {"exit": wr_code, "output": _tail(wr_out + wr_err)},
            "external_dir_survived": survived,
        },
    }


def drill_p3(S: Path) -> dict:
    """Tamper external SKILL.md content; does --check or validate_plugin flag it by name?"""
    name, synthesized = _derive_external(S)
    with (S / ".agents" / "skills" / name / "SKILL.md").open("ab") as fh:
        fh.write(b"x")
    sync = _copy_script(S, "scripts/sync_codex_skills.py")
    chk_code, chk_out, chk_err = _run([sys.executable, str(sync), "--check"], cwd=S)
    flagged_by_check = chk_code != 0 and name in chk_out
    validator = _copy_script(S, "scripts/validate_plugin.py")
    val_code, val_out, val_err = _run([sys.executable, str(validator)], cwd=S)
    flagged_by_validator = val_code != 0 and name in (val_out + val_err)
    return {
        "id": DRILL_IDS[2],
        "pass": bool(flagged_by_check or flagged_by_validator),
        "evidence": {
            "external_skill": name,
            "synthesized_fixture": synthesized,
            "tamper": "appended 1 byte to the external skill's SKILL.md",
            "check": {"exit": chk_code, "flagged": flagged_by_check,
                      "output": _tail(chk_out + chk_err)},
            "validate_plugin": {"exit": val_code, "flagged": flagged_by_validator,
                                "output": _tail(val_out + val_err)},
        },
    }


def run_drills(source: Path) -> list[dict]:
    """Run each drill against its own fresh scratch copy; refuse on failed setup."""
    cards: list[dict] = []
    drills = ((DRILL_IDS[0], drill_p1), (DRILL_IDS[1], drill_p2), (DRILL_IDS[2], drill_p3))
    with tempfile.TemporaryDirectory(prefix="karta-sync-integrity-") as td:
        for n, (drill_id, fn) in enumerate(drills, 1):
            copy_root = Path(td) / f"p{n}"
            if not _fresh_copy(source, copy_root):
                cards.append({"id": drill_id, "pass": False,
                              "evidence": {"refused":
                                           "copy-setup-failed — drill not run (fail-closed)"}})
                continue
            roots = [str(copy_root.resolve()), str(copy_root)]
            cards.append(_norm(fn(copy_root), roots))
    return cards


def verdict(cards: list[dict], baseline: dict[str, bool] | None,
            hard_failures: int) -> tuple[str, list[dict]]:
    """Apply the baseline-regression rule; return (status, verdict findings).

    baseline maps probe id -> pass from the newest tracked results file, or None
    when no tracked baseline exists (the first build run)."""
    extra: list[dict] = []
    fail = hard_failures > 0
    by_id = {c["id"]: c for c in cards}
    if any("refused" in c.get("evidence", {}) for c in cards):
        fail = True
    if baseline is None:
        for did in DRILL_IDS:
            card = by_id.get(did)
            if card is not None and card["pass"] and "refused" not in card["evidence"]:
                fail = True
                extra.append({
                    "finding_id": f"seed-absent-{did}", "severity": "error",
                    "summary": (f"{did} passed on the first (baseline) run — the contract's "
                                "seeded finding is absent from its own first baseline "
                                "(fail-closed)")})
    else:
        for pid, passed in sorted(baseline.items()):
            if not passed:
                continue
            cur = by_id.get(pid)
            if cur is None or not cur["pass"]:
                fail = True
                extra.append({
                    "finding_id": f"regression-{pid}", "severity": "error",
                    "summary": (f"{pid} regressed: passing in the committed baseline, "
                                "failing now (a previously-passing ID may never regress)")})
    return ("fail" if fail else "pass"), extra


def run_live(target: Path) -> int:
    findings: list[dict] = []
    hard_failures = 0

    for label, slug, script in CHECKS:
        code, out, err = _run([sys.executable, str(target / script)], cwd=target,
                              timeout=CHECK_TIMEOUT_S)
        if code == -1:
            hard_failures += 1
            findings.append({"finding_id": f"{slug}-not-run", "severity": "error",
                             "summary": f"{script} did not complete ({err.strip()})"})
        elif code != 0:
            hard_failures += 1
            findings.extend(_findings_from(slug, out + err))

    baseline_name, baseline_map = "none", None
    code, out, _err = _run(["git", "-C", str(target), "ls-files", "--", RESULTS_GLOB],
                           cwd=target)
    tracked = sorted(ln.strip() for ln in out.splitlines() if ln.strip()) if code == 0 else []
    if tracked:
        baseline_name = tracked[-1]  # newest: date-prefixed names sort chronologically
        try:
            data = json.loads((target / baseline_name).read_text())
            baseline_map = {p["id"]: bool(p["pass"]) for p in data.get("probes", [])
                            if isinstance(p, dict) and "id" in p}
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            hard_failures += 1
            findings.append({"finding_id": "baseline-unreadable", "severity": "error",
                             "summary": f"tracked baseline {baseline_name} could not be "
                                        "parsed — cannot prove no regression (fail-closed)"})

    cards = run_drills(target)
    for c in cards:
        if "refused" in c["evidence"]:
            findings.append({"finding_id": f"{c['id']}-not-run", "severity": "error",
                             "summary": "scratch copy setup failed — drill refused "
                                        "(fail-closed), no script invocation ran"})
        elif not c["pass"]:
            findings.append({"finding_id": c["id"], "severity": "error",
                             "summary": SEED_SUMMARIES[c["id"]]})

    status, extra = verdict(cards, baseline_map, hard_failures)
    findings.extend(extra)

    passed = sum(1 for c in cards if c["pass"])
    results_dir = target / RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{datetime.date.today().isoformat()}-sync-probes.json"
    out_path.write_text(json.dumps(
        {"probes": cards, "passed": passed, "total": len(DRILL_IDS)}, indent=2) + "\n")

    print(json.dumps({
        "id": PROBE_ID,
        "status": status,
        "partial": False,
        "implemented_checks": [label for label, _, _ in CHECKS] + list(DRILL_IDS),
        "findings": findings,
        "metrics": {"checks_run": len(CHECKS),
                    "checks_failed": hard_failures,
                    "probes_passed": passed,
                    "probes_total": len(DRILL_IDS),
                    "baseline": baseline_name},
    }, indent=2))
    return 0


def _build_synthetic_tree(root: Path, with_external: bool, repo_scripts: Path) -> None:
    """A miniature repo shape embedding the real sync scripts, synced into place."""
    (root / "skills" / "demo-skill" / "scripts").mkdir(parents=True)
    (root / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: bench self-test fixture\n---\nfixture\n")
    (root / "skills" / "demo-skill" / "scripts" / "tool.py").write_text("print('fixture')\n")
    (root / "scripts").mkdir()
    for name in ("sync_codex_skills.py", "validate_plugin.py"):
        shutil.copy(repo_scripts / name, root / "scripts" / name)
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin" / "plugin.json").write_text("{}\n")
    if with_external:
        ext = root / ".agents" / "skills" / "ext-demo"
        ext.mkdir(parents=True)
        (ext / "SKILL.md").write_text("# external fixture\n")
        (root / "skills-lock.json").write_text(json.dumps({"version": 1, "skills": {
            "ext-demo": {"source": "bench/fixture", "sourceType": "github",
                         "skillPath": "e/SKILL.md", "computedHash": "0" * 64}}},
            indent=1) + "\n")
    code, out, err = _run([sys.executable, str(root / "scripts" / "sync_codex_skills.py")],
                          cwd=root)
    if code != 0:
        raise RuntimeError(f"synthetic tree setup sync failed: {out}{err}")


def self_test() -> int:
    checks: list[tuple[str, bool]] = []
    repo_scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
    with tempfile.TemporaryDirectory(prefix="karta-sync-selftest-") as td:
        base = Path(td)

        tree = base / "tree-ext"
        _build_synthetic_tree(tree, with_external=True, repo_scripts=repo_scripts)
        by_id = {c["id"]: c for c in run_drills(tree)}
        p1, p2, p3 = (by_id[d] for d in DRILL_IDS)
        checks.append(("P1 reproduces the mode-blind projection (exec bit lost)",
                       p1["pass"] is False and p1["evidence"]["check"]["exit"] == 0))
        checks.append(("P2 degraded lock destroys the external skill dir and crashes",
                       p2["pass"] is False
                       and p2["evidence"]["external_dir_survived"] is False
                       and p2["evidence"]["write"]["exit"] != 0))
        checks.append(("P2 used the live external fixture, not synthesis",
                       p2["evidence"]["synthesized_fixture"] is False
                       and p2["evidence"]["external_skill"] == "ext-demo"))
        checks.append(("P3 tampered external content stays silent (fails)",
                       p3["pass"] is False))
        blob = json.dumps([p1, p2, p3])
        checks.append(("evidence cards normalize scratch roots to $S",
                       "$S/" in blob and "karta-sync-integrity-" not in blob))

        tree0 = base / "tree-noext"
        _build_synthetic_tree(tree0, with_external=False, repo_scripts=repo_scripts)
        by_id0 = {c["id"]: c for c in run_drills(tree0)}
        p2b = by_id0[DRILL_IDS[1]]
        checks.append(("zero-external lock synthesizes bench-external-probe",
                       p2b["evidence"]["synthesized_fixture"] is True
                       and p2b["evidence"]["external_skill"] == SYNTH_NAME))
        checks.append(("synthesized fixture keeps the P2 denominator alive (drill ran, red)",
                       p2b["pass"] is False))

        bad_source = base / "not-a-repo"
        bad_source.mkdir()
        cards_bad = run_drills(bad_source)
        checks.append(("copy-setup failure refuses every drill (fail-closed)",
                       all(c["pass"] is False and "refused" in c["evidence"]
                           for c in cards_bad)))
        st_bad, _ = verdict(cards_bad, None, 0)
        checks.append(("refusal reports status fail, never a silent skip",
                       st_bad == "fail"))

        base_map = {DRILL_IDS[0]: True, DRILL_IDS[1]: False, DRILL_IDS[2]: False}
        regressed = [{"id": d, "pass": False, "evidence": {}} for d in DRILL_IDS]
        st_reg, ex_reg = verdict(regressed, base_map, 0)
        checks.append(("a previously-passing probe ID flipping false yields status fail",
                       st_reg == "fail"
                       and any(f["finding_id"] == f"regression-{DRILL_IDS[0]}"
                               for f in ex_reg)))
        unchanged = [{"id": d, "pass": d == DRILL_IDS[0], "evidence": {}} for d in DRILL_IDS]
        st_same, _ = verdict(unchanged, base_map, 0)
        checks.append(("an unchanged matrix against its baseline stays pass",
                       st_same == "pass"))
        st_seed, ex_seed = verdict(unchanged, None, 0)
        checks.append(("a seeded finding absent from the first baseline fails closed",
                       st_seed == "fail"
                       and any(f["finding_id"].startswith("seed-absent-")
                               for f in ex_seed)))

    passed = sum(1 for _name, ok in checks if ok)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="mirror-sync integrity gate probe")
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true",
                    help="drive the drills against a synthetic tree, never the real repo")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return run_live(args.target.resolve())


if __name__ == "__main__":
    sys.exit(main())
