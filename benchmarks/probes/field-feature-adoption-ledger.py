#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for field-feature-adoption-ledger: adapter over audit_adoption.py.

Runs benchmarks/field/audit_adoption.py against the --target repo (consumer
repos default to the sibling parchmark/gringotts checkouts of the target's
parent) and maps the per-repo attribution tables into the gate probe JSON
contract {"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
on stdout, exit 0 whether pass or fail (a nonzero exit means the probe itself
crashed).

Stance: a manifest-gate violation (audit exit 2) or a missing enrolled consumer
directory in live mode (audit exit 3) reports status "fail" — fail-closed on
probe integrity. AVAILABLE-BUT-UNUSED ids are findings — a prune-or-fix signal,
never a gate failure.

Usage:
  python3 benchmarks/probes/field-feature-adoption-ledger.py --target <repo-root>
  python3 benchmarks/probes/field-feature-adoption-ledger.py --self-test

--self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary; it
exits 0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path

PROBE_ID = "field-feature-adoption-ledger"
AUDIT_REL = Path("benchmarks") / "field" / "audit_adoption.py"
AUDIT_TIMEOUT_S = 100
IMPLEMENTED_CHECKS = [
    "manifest-gate (entry or observable:false sentinel per release)",
    "activity-event attribution (three pinned commit classes)",
    "first-use ledger (immutable committed first_use carry-forward)",
    "classification (ADOPTED / NOT-EXERCISED-SINCE-SHIP / AVAILABLE-BUT-UNUSED / "
    "CURRENT-STATE-ONLY)",
    "adoption-lag (median at n>=5, pre-release-dogfood excluded)",
]


def build_payload(audit_rc: int, tables: list[dict], detail: str) -> dict:
    """Map an audit outcome to the gate probe contract. Pure — used by self-test."""
    findings: list[dict] = []
    metrics: dict = {"repos_audited": [t["repo"] for t in tables]}
    if audit_rc == 0:
        status = "pass"
        for t in tables:
            repo = t["repo"]
            counts = {"ADOPTED": 0, "NOT-EXERCISED-SINCE-SHIP": 0,
                      "AVAILABLE-BUT-UNUSED": 0, "CURRENT-STATE-ONLY": 0}
            for row in t["features"]:
                counts[row["status"]] += 1
            for fid in t["headline"]["available_but_unused"]:
                findings.append({
                    "finding_id": f"abu-{repo}-{fid}", "severity": "warning",
                    "summary": f"{repo}: {fid} AVAILABLE-BUT-UNUSED "
                               f"(activity after ship, detection never fired)"})
            metrics[f"{repo}_available_but_unused"] = t["headline"]["count"]
            metrics[f"{repo}_denominator"] = t["headline"]["denominator"]
            metrics[f"{repo}_adopted"] = counts["ADOPTED"]
            metrics[f"{repo}_not_exercised"] = counts["NOT-EXERCISED-SINCE-SHIP"]
            metrics[f"{repo}_current_state_only"] = counts["CURRENT-STATE-ONLY"]
            median = t.get("adoption_lag_median_days")
            metrics[f"{repo}_median_lag_days"] = "n/a" if median is None else median
    else:
        status = "fail"
        kind = {2: "manifest-gate-violation", 3: "consumer-or-manifest-missing"}.get(
            audit_rc, "audit-crashed")
        findings.append({"finding_id": kind, "severity": "error",
                         "summary": f"audit_adoption.py exit {audit_rc}: {detail}"})
    return {"id": PROBE_ID, "status": status, "partial": False,
            "implemented_checks": IMPLEMENTED_CHECKS, "findings": findings,
            "metrics": metrics}


def run_live(target: Path) -> dict:
    audit = target / AUDIT_REL
    try:
        proc = subprocess.run([sys.executable, str(audit), "--target", str(target)],
                              capture_output=True, text=True, timeout=AUDIT_TIMEOUT_S,
                              cwd=str(target))
    except (OSError, subprocess.TimeoutExpired) as e:
        return build_payload(1, [], f"{audit} did not complete ({e})")
    tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
    if proc.returncode != 0:
        return build_payload(proc.returncode, [], tail)
    tables = []
    for line in proc.stdout.splitlines():
        if line.startswith("RESULTS "):
            try:
                tables.append(json.loads((target / line[len("RESULTS "):]).read_text()))
            except (OSError, json.JSONDecodeError) as e:
                return build_payload(1, [], f"unreadable results file: {e}")
    if not tables:
        return build_payload(1, [], "audit exited 0 but announced no RESULTS files")
    return build_payload(0, tables, "")


def self_test(target: Path) -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    proc = subprocess.run(
        [sys.executable, str(target / AUDIT_REL), "--self-test"],
        capture_output=True, text=True, timeout=300)
    check("audit_adoption.py --self-test passes", proc.returncode == 0,
          "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:]))

    p = build_payload(2, [], "release 9.9.9 lacks entry and sentinel")
    check("a manifest-gate violation flips status to fail",
          p["status"] == "fail"
          and p["findings"][0]["finding_id"] == "manifest-gate-violation",
          json.dumps(p["findings"]))
    p = build_payload(3, [], "enrolled consumer directory missing: /x")
    check("a missing enrolled consumer directory flips status to fail",
          p["status"] == "fail", json.dumps(p["findings"]))

    fake = {"repo": "fixrepo",
            "features": [
                {"id": "f1", "status": "ADOPTED"},
                {"id": "f2", "status": "AVAILABLE-BUT-UNUSED"},
                {"id": "f3", "status": "NOT-EXERCISED-SINCE-SHIP"},
                {"id": "f4", "status": "CURRENT-STATE-ONLY"}],
            "headline": {"available_but_unused": ["f2"], "count": 1, "denominator": 4},
            "adoption_lag_median_days": None}
    p = build_payload(0, [fake], "")
    check("alarm ids land as findings under status pass with the denominator in metrics",
          p["status"] == "pass"
          and [f["finding_id"] for f in p["findings"]] == ["abu-fixrepo-f2"]
          and p["metrics"]["fixrepo_denominator"] == 4
          and p["metrics"]["fixrepo_available_but_unused"] == 1,
          json.dumps(p))
    check("payload carries exactly the gate contract keys, partial false",
          set(p) == {"id", "status", "partial", "implemented_checks", "findings",
                     "metrics"} and p["partial"] is False and p["id"] == PROBE_ID,
          str(sorted(p)))

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {detail}"))
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    target = args.target.resolve()
    if args.self_test:
        return self_test(target)
    print(json.dumps(run_live(target), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
