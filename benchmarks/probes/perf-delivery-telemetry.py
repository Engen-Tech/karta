#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for perf-delivery-telemetry: modes 1 (cost) + 4 (compliance) only.

Partial coverage, honestly labeled: modes 2 (timeline) and 3 (oracle-count) are
deferred per the card's own two-pass rollout order and never appear in
implemented_checks. The probe drives benchmarks/perf/mine_sessions.py two ways:

  GATING — the committed fixture transcript at
  benchmarks/perf/fixtures/miner-transcript/ is mined and compared against the
  miner's pinned expectations; any mismatch flips status to "fail" (fail-closed
  on the miner's own correctness).

  EVIDENCE — a best-effort live pass over whatever raw transcript dirs survive
  under ~/.claude/projects/ for this repo and its sibling consumer repos
  (parchmark, gringotts). Live content is recorded, never asserted: absent or
  decayed dirs and any contained per-session error land in an
  unmeasurable/skipped count with no effect on status (transcript decay is an
  environment fact the card documents, not a probe defect). Each live pass
  writes a dated snapshot to benchmarks/perf/results/<run-date>-perf-telemetry.json.

Usage: python3 benchmarks/probes/perf-delivery-telemetry.py --target <repo-root>
Prints the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
to stdout and exits 0 whether pass or fail (nonzero exit means the probe itself
crashed). --self-test validates the fixture gating, the doctored-expectation
fail flip, live-pass error containment, and the contract shape, printing
[PASS]/[FAIL] lines and an N/N checks passed summary; --self-test exits 0 only
when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

PROBE_ID = "perf-delivery-telemetry"
IMPLEMENTED_CHECKS = ["mode-1-cost (fixture-validated)", "mode-4-compliance (fixture-validated)"]
CONSUMER_SIBLINGS = ("parchmark", "gringotts")
LIVE_SINCE_DAYS = 30
LIVE_BUDGET_S = 60.0


def _load_miner(target: Path):
    miner_path = target / "benchmarks" / "perf" / "mine_sessions.py"
    spec = importlib.util.spec_from_file_location("mine_sessions", miner_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_pass(target: Path, ms, projects_root: Path | None = None) -> dict:
    """Best-effort mining of surviving live transcript dirs. Error-contained by
    contract: every failure is counted, nothing raises, status is never touched."""
    projects_root = projects_root or Path.home() / ".claude" / "projects"
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=LIVE_SINCE_DAYS)
    budget = LIVE_BUDGET_S / max(1, 1 + len(CONSUMER_SIBLINGS))
    repos = [target] + [target.parent / name for name in CONSUMER_SIBLINGS]
    entries: list[dict] = []
    for repo in repos:
        transcript_dir = projects_root / ms.encode_project_path(repo)
        if not transcript_dir.is_dir():
            entries.append({"repo": str(repo), "transcript_dir": str(transcript_dir),
                            "present": False,
                            "note": "absent or decayed — counted unmeasurable, never asserted"})
            continue
        try:
            report = ms.mine([transcript_dir], since=since, budget_s=budget)["reports"][0]
            entries.append({"repo": str(repo), "present": True, **report})
        except Exception as e:  # error-contained: recorded, never raised
            entries.append({"repo": str(repo), "transcript_dir": str(transcript_dir),
                            "present": True, "errored": True, "error": str(e)[:200]})
    return {
        "since": since.date().isoformat(),
        "dirs_present": sum(1 for e in entries if e.get("present")),
        "dirs_absent": sum(1 for e in entries if not e.get("present")),
        "sessions_scanned": sum(e.get("sessions_scanned", 0) for e in entries),
        "deliveries_measurable": sum(e.get("deliveries_measurable", 0) for e in entries),
        "unmeasurable_or_skipped": sum(
            e.get("unmeasurable_sessions", 0) + e.get("sessions_errored", 0)
            + e.get("sessions_skipped_budget", 0) + int(bool(e.get("errored")))
            for e in entries),
        "entries": entries,
    }


def _write_snapshot(target: Path, live: dict) -> str:
    """Dated live-evidence snapshot: content recorded, never asserted."""
    run_date = datetime.date.today().isoformat()
    out = target / "benchmarks" / "perf" / "results" / f"{run_date}-perf-telemetry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "schema_version": 1,
        "vector": PROBE_ID,
        "run_date": run_date,
        "generated_by": "benchmarks/probes/perf-delivery-telemetry.py (gate live pass)",
        "note": "live telemetry evidence over surviving raw transcripts — recorded, "
                "never asserted; the fixture run alone gates",
        "live": live,
    }, indent=2) + "\n")
    return str(out.relative_to(target))


def _payload(fixture_checks: list[tuple[str, bool, str]], live: dict | None,
             snapshot: str | None) -> dict:
    failed = [(name, detail) for name, ok, detail in fixture_checks if not ok]
    findings = [{"finding_id": f"fixture-mismatch-{i}", "severity": "error",
                 "summary": f"fixture check '{name}' failed: {detail}"}
                for i, (name, detail) in enumerate(failed, 1)]
    metrics = {
        "fixture_checks_passed": len(fixture_checks) - len(failed),
        "fixture_checks_total": len(fixture_checks),
        "live_dirs_present": live["dirs_present"] if live else 0,
        "live_dirs_absent": live["dirs_absent"] if live else 0,
        "live_sessions_scanned": live["sessions_scanned"] if live else 0,
        "live_deliveries_measurable": live["deliveries_measurable"] if live else 0,
        "live_unmeasurable_or_skipped": live["unmeasurable_or_skipped"] if live else 0,
    }
    if snapshot:
        metrics["snapshot"] = snapshot
    return {
        "id": PROBE_ID,
        "status": "fail" if failed else "pass",  # derived solely from the fixture run
        "partial": True,
        "implemented_checks": IMPLEMENTED_CHECKS,
        "findings": findings,
        "metrics": metrics,
    }


def _run_self_test(target: Path) -> int:
    ms = _load_miner(target)
    fixture = target / "benchmarks" / "perf" / "fixtures" / "miner-transcript"
    results: list[tuple[str, bool, str]] = []

    checks = ms.check_fixture(fixture)
    bad = [name for name, ok, _ in checks if not ok]
    results.append(("fixture run matches pinned expectations",
                    not bad, f"failed: {bad}"))
    results.append(("clean fixture run maps to status pass",
                    _payload(checks, None, None)["status"] == "pass", "status was not pass"))

    doctored = copy.deepcopy(ms.EXPECTED_FIXTURE)
    doctored["spawns_per_type"]["builder"] = 99
    flipped = _payload(ms.check_fixture(fixture, expected=doctored), None, None)
    results.append(("doctored fixture expectation flips status to fail",
                    flipped["status"] == "fail" and flipped["findings"],
                    f"status={flipped['status']}, findings={len(flipped['findings'])}"))

    with tempfile.TemporaryDirectory() as tmp:
        ghost = Path(tmp) / "nonexistent" / "repo"
        live = _live_pass(ghost, ms, projects_root=Path(tmp) / "no-projects")
        results.append(("live pass over absent dirs is contained (no crash, counted)",
                        live["dirs_absent"] == 3 and live["dirs_present"] == 0,
                        f"got {live['dirs_absent']} absent, {live['dirs_present']} present"))

    payload = _payload(checks, live, None)
    shape_ok = (payload["id"] == PROBE_ID and payload["status"] in ("pass", "fail")
                and isinstance(payload["partial"], bool)
                and isinstance(payload["implemented_checks"], list)
                and isinstance(payload["findings"], list)
                and isinstance(payload["metrics"], dict))
    results.append(("payload satisfies the gate probe JSON contract", shape_ok, str(payload)[:120]))
    results.append(("modes 2 and 3 absent from implemented_checks",
                    not any("timeline" in c or "oracle-count" in c or "mode-2" in c
                            or "mode-3" in c for c in payload["implemented_checks"]),
                    str(payload["implemented_checks"])))

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

    ms = _load_miner(target)
    fixture_checks = ms.check_fixture(
        target / "benchmarks" / "perf" / "fixtures" / "miner-transcript")
    live = _live_pass(target, ms)
    snapshot = _write_snapshot(target, live)
    print(json.dumps(_payload(fixture_checks, live, snapshot), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
