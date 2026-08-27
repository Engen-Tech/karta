#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for perf-fixture-cost-baseline: the deterministic half only.

Partial coverage, honestly labeled. The vector's actual measurement is a
90-minute timed headless delivery run three times per release
(benchmarks/perf/fixture-v1/runner.sh) and compared against the previous
release's committed JSON. A gate probe with a 120-second budget cannot run
that, and pretending otherwise would turn a quarterly cadence into a green
checkmark. So the timed A/B run and the cross-release comparison of step 7 are
NOT implemented here, never appear in implemented_checks, and stay a manual
cadence run.

What this probe does gate, fail-closed:

  FIXTURE INTEGRITY — benchmarks/perf/fixture-v1/ still holds its four frozen
  files, repo.tar.gz still carries .karta/binders/bench.json, and runner.sh
  still parses under `sh -n`. The fixture is the denominator of every cost
  number this vector will ever produce; it must not rot unnoticed.

  MINER CORRECTNESS — benchmarks/perf/mine_fixture.py mined over the committed
  transcript fixture at benchmarks/perf/fixtures/miner-transcript/ must match
  its pinned expectations, exactly as the sibling telemetry probe gates
  mine_sessions.py.

Usage: python3 benchmarks/probes/perf-fixture-cost-baseline.py --target <repo-root>
Prints the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
to stdout and exits 0 whether pass or fail (nonzero exit means the probe itself
crashed). --self-test validates the contract shape, a doctored-fixture fail
flip, the miner gating and its own doctored fail flip, printing [PASS]/[FAIL]
lines and an N/N checks passed summary; --self-test exits 0 only when the
summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PROBE_ID = "perf-fixture-cost-baseline"
IMPLEMENTED_CHECKS = ["fixture-v1-integrity (frozen-fixture)",
                      "mine_fixture-correctness (fixture-validated)"]
FIXTURE_V1 = Path("benchmarks") / "perf" / "fixture-v1"
FIXTURE_FILES = ("repo.tar.gz", "settings.json", "runner.sh", "README.md")
FROZEN_BINDER_MEMBER = ".karta/binders/bench.json"
MINER_TRANSCRIPT = Path("benchmarks") / "perf" / "fixtures" / "miner-transcript"


def _load_miner(target: Path):
    miner_path = target / "benchmarks" / "perf" / "mine_fixture.py"
    spec = importlib.util.spec_from_file_location("mine_fixture", miner_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_fixture_integrity(fixture_dir: Path) -> list[tuple[str, bool, str]]:
    """Deterministic local checks on the frozen fixture. Never runs a delivery."""
    checks: list[tuple[str, bool, str]] = []
    present = [name for name in FIXTURE_FILES if (fixture_dir / name).is_file()]
    checks.append(("fixture-v1 holds its four frozen files",
                   sorted(present) == sorted(FIXTURE_FILES),
                   f"present: {sorted(present)}"))

    tarball = fixture_dir / "repo.tar.gz"
    if tarball.is_file():
        try:
            with tarfile.open(tarball, "r:gz") as tar:
                members = tar.getnames()
            hit = [m for m in members if m.endswith(FROZEN_BINDER_MEMBER)]
            checks.append((f"repo.tar.gz carries {FROZEN_BINDER_MEMBER}",
                           bool(hit), f"{len(members)} members, no match"))
        except (tarfile.TarError, OSError) as e:
            checks.append((f"repo.tar.gz carries {FROZEN_BINDER_MEMBER}", False,
                           f"tarball unreadable: {e}"))
    else:
        checks.append((f"repo.tar.gz carries {FROZEN_BINDER_MEMBER}", False, "tarball missing"))

    runner = fixture_dir / "runner.sh"
    if runner.is_file():
        try:
            proc = subprocess.run(["sh", "-n", str(runner)], capture_output=True,
                                  text=True, timeout=30)
            checks.append(("runner.sh parses under sh -n", proc.returncode == 0,
                           (proc.stderr or proc.stdout).strip()[:200]))
        except (OSError, subprocess.SubprocessError) as e:
            checks.append(("runner.sh parses under sh -n", False, f"sh -n did not run: {e}"))
    else:
        checks.append(("runner.sh parses under sh -n", False, "runner.sh missing"))
    return checks


def _payload(checks: list[tuple[str, bool, str]], results_committed: int) -> dict:
    failed = [(name, detail) for name, ok, detail in checks if not ok]
    findings = [{"finding_id": f"fixture-cost-baseline-{i}", "severity": "error",
                 "summary": f"check '{name}' failed: {detail}"}
                for i, (name, detail) in enumerate(failed, 1)]
    return {
        "id": PROBE_ID,
        "status": "fail" if failed else "pass",
        "partial": True,
        "implemented_checks": IMPLEMENTED_CHECKS,
        "findings": findings,
        "metrics": {
            "checks_passed": len(checks) - len(failed),
            "checks_total": len(checks),
            # Zero means the quarterly cadence has never run: the harness is
            # ready, the baseline does not exist yet. Recorded, never asserted —
            # a missing manual run is not a probe failure.
            "baseline_results_committed": results_committed,
        },
    }


def _all_checks(target: Path, fixture_dir: Path | None = None) -> list[tuple[str, bool, str]]:
    mf = _load_miner(target)
    return (check_fixture_integrity(fixture_dir or target / FIXTURE_V1)
            + mf.check_fixture(target / MINER_TRANSCRIPT))


def _results_committed(target: Path) -> int:
    results = target / "benchmarks" / "perf" / "results"
    if not results.is_dir():
        return 0
    return len([p for p in results.glob("*.json") if "fixture-v1" in p.name])


def _run_self_test(target: Path) -> int:
    mf = _load_miner(target)
    results: list[tuple[str, bool, str]] = []

    checks = _all_checks(target)
    bad = [name for name, ok, _ in checks if not ok]
    results.append(("the committed fixture and miner pass every gating check",
                    not bad, f"failed: {bad}"))

    payload = _payload(checks, _results_committed(target))
    shape_ok = (payload["id"] == PROBE_ID and payload["status"] in ("pass", "fail")
                and isinstance(payload["partial"], bool)
                and isinstance(payload["implemented_checks"], list)
                and isinstance(payload["findings"], list)
                and isinstance(payload["metrics"], dict))
    results.append(("payload satisfies the gate probe JSON contract", shape_ok,
                    str(payload)[:120]))
    results.append(("a clean run maps to status pass with partial true",
                    payload["status"] == "pass" and payload["partial"] is True,
                    f"status={payload['status']}, partial={payload['partial']}"))
    results.append(("the manual timed A/B run is absent from implemented_checks",
                    not any(word in check.lower() for check in payload["implemented_checks"]
                            for word in ("timed", "a-b", "quarterly", "comparison")),
                    str(payload["implemented_checks"])))

    with tempfile.TemporaryDirectory() as tmp:
        doctored = Path(tmp) / "fixture-v1"
        shutil.copytree(target / FIXTURE_V1, doctored)
        (doctored / "README.md").unlink()
        integrity = check_fixture_integrity(doctored)
        flipped = _payload(integrity + mf.check_fixture(target / MINER_TRANSCRIPT), 0)
        results.append(("a doctored fixture (one frozen file removed) flips status to fail",
                        flipped["status"] == "fail" and len(flipped["findings"]) == 1,
                        f"status={flipped['status']}, findings={len(flipped['findings'])}"))

        broken_runner = Path(tmp) / "broken"
        shutil.copytree(target / FIXTURE_V1, broken_runner)
        (broken_runner / "runner.sh").write_text("if true; then\n")  # unterminated
        broken = _payload(check_fixture_integrity(broken_runner), 0)
        results.append(("a runner.sh that no longer parses flips status to fail",
                        broken["status"] == "fail"
                        and any("sh -n" in f["summary"] for f in broken["findings"]),
                        str(broken["findings"])[:160]))

    doctored_expected = dict(mf.EXPECTED_FIXTURE, wall_minutes=999.0)
    miner_flipped = _payload(mf.check_fixture(target / MINER_TRANSCRIPT,
                                              expected=doctored_expected), 0)
    results.append(("a doctored miner expectation flips status to fail (miner gating)",
                    miner_flipped["status"] == "fail" and miner_flipped["findings"],
                    f"status={miner_flipped['status']}"))

    failures = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f": {detail}"))
        failures += 0 if ok else 1
    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    target = args.target.resolve()

    if args.self_test:
        return _run_self_test(target)

    print(json.dumps(_payload(_all_checks(target), _results_committed(target)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
