#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for quality-verify-integrity-drills: Layer 0 static tripwires only.

Thin adapter, partial: true — Families E (empty-diff agent paths) and K
(kickback caps) need headless claude -p and are phase 3. It subprocess-runs
benchmarks/verify-drills/static_check.py against --target and maps the three
tripwires into the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}.
Exits 0 whether pass or fail (a nonzero exit means the probe itself crashed);
a static_check crash or malformed output is itself status "fail" (fail-closed).

The baseline stance lives in static_check: status "fail" only on regression against the last committed results file,
where the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file.

Self-test (--self-test) checks the contract mapping over embedded fixture
payloads, printing [PASS]/[FAIL] lines and an N/N checks passed summary and
exits 0 only when the summary is N/N checks passed.

Usage: python3 benchmarks/probes/quality-verify-integrity-drills.py --target <repo-root>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROBE_ID = "quality-verify-integrity-drills"
STATIC_CHECK = Path("benchmarks/verify-drills/static_check.py")
CHECK_TIMEOUT_S = 100

IMPLEMENTED_CHECKS = [
    "layer0-tripwire-a-safety-auditor-blocked-empty-diff-clause",
    "layer0-tripwire-b-acceptance-reviewer-empty-diff-precondition",
    "layer0-tripwire-c-store-no-loop-state-persisted-attempt-pairing",
]


def to_contract(raw: dict) -> dict:
    """Map a static_check results payload into the gate probe contract."""
    tripwires = raw.get("tripwires", {}) if isinstance(raw, dict) else {}
    findings = raw.get("findings", []) if isinstance(raw, dict) else []
    status = raw.get("status") if isinstance(raw, dict) else None
    return {
        "id": PROBE_ID,
        "status": status if status in ("pass", "fail") else "fail",
        "partial": True,
        "implemented_checks": list(IMPLEMENTED_CHECKS),
        "findings": [
            {"finding_id": str(f.get("finding_id", "unknown")),
             "severity": str(f.get("severity", "error")),
             "summary": str(f.get("summary", ""))}
            for f in findings if isinstance(f, dict)
        ],
        "metrics": {
            "tripwires_passed": sum(1 for v in tripwires.values() if v == "pass"),
            "tripwires_total": 3,
            "tripwire_states": [f"{k}={v}" for k, v in sorted(tripwires.items())],
            "baseline": raw.get("baseline_used") or "none",
        },
    }


def fail_closed(summary: str) -> dict:
    """Contract payload for a static_check that could not run: status fail."""
    return {
        "id": PROBE_ID,
        "status": "fail",
        "partial": True,
        "implemented_checks": list(IMPLEMENTED_CHECKS),
        "findings": [{"finding_id": "static-check-not-run", "severity": "error",
                      "summary": summary}],
        "metrics": {"tripwires_passed": 0, "tripwires_total": 3,
                    "tripwire_states": [], "baseline": "none"},
    }


def _contract_ok(payload: dict) -> bool:
    """Mirror run_gate.py's contract validation."""
    return (isinstance(payload, dict)
            and payload.get("id") == PROBE_ID
            and payload.get("status") in ("pass", "fail")
            and isinstance(payload.get("partial"), bool)
            and isinstance(payload.get("implemented_checks"), list)
            and isinstance(payload.get("findings"), list)
            and isinstance(payload.get("metrics"), dict))


FIX_PASS = {
    "status": "pass",
    "tripwires": {"a": "known-open", "b": "pass", "c": "known-open"},
    "findings": [{"finding_id": "tripwire-a-blocked-empty-diff-clause-missing",
                  "severity": "known-open", "summary": "known gap"}],
    "baseline_used": "benchmarks/quality/results/2026-07-18-verify-drills.json",
}
FIX_FAIL = {
    "status": "fail",
    "tripwires": {"a": "known-open", "b": "fail", "c": "known-open"},
    "findings": [{"finding_id": "tripwire-b-acceptance-reviewer-precondition-lost",
                  "severity": "error", "summary": "protection regressed"}],
    "baseline_used": None,
}


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))

    p = to_contract(FIX_PASS)
    check("pass-shaped payload maps to status pass", p["status"] == "pass")
    check("mapped payload satisfies run_gate's contract", _contract_ok(p))
    check("partial is always true (Layer 0 only)", p["partial"] is True)
    check("implemented_checks is exactly the three Layer 0 tripwires",
          p["implemented_checks"] == IMPLEMENTED_CHECKS)
    check("metrics carry tripwire tally and baseline provenance",
          p["metrics"]["tripwires_passed"] == 1
          and p["metrics"]["tripwires_total"] == 3
          and p["metrics"]["baseline"].endswith("-verify-drills.json"))

    f = to_contract(FIX_FAIL)
    check("fail-shaped payload maps to status fail with findings carried",
          f["status"] == "fail" and _contract_ok(f)
          and f["findings"][0]["finding_id"]
          == "tripwire-b-acceptance-reviewer-precondition-lost")
    check("a payload with no usable status maps to fail (defensive)",
          to_contract({})["status"] == "fail")

    fc = fail_closed("static_check.py did not run")
    check("static_check crash path is fail-closed and contract-valid",
          fc["status"] == "fail" and _contract_ok(fc))
    try:
        json.dumps(p), json.dumps(f), json.dumps(fc)
        serializable = True
    except (TypeError, ValueError):
        serializable = False
    check("every mapped payload is JSON-serializable", serializable)

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="quality-verify-integrity-drills gate probe")
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded mapping checks and exit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    target = args.target.resolve()
    script = target / STATIC_CHECK
    if not script.is_file():
        print(json.dumps(fail_closed(f"{STATIC_CHECK} not found under {target}"),
                         indent=2))
        return 0
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--target", str(target)],
            capture_output=True, text=True, timeout=CHECK_TIMEOUT_S,
            cwd=str(target))
    except (OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps(fail_closed(f"{STATIC_CHECK} did not complete ({e})"),
                         indent=2))
        return 0
    if proc.returncode != 0:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
        print(json.dumps(fail_closed(
            f"{STATIC_CHECK} crashed (exit {proc.returncode}: {tail})"), indent=2))
        return 0
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        print(json.dumps(fail_closed(f"{STATIC_CHECK} emitted bad JSON ({e})"),
                         indent=2))
        return 0
    print(json.dumps(to_contract(raw), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
