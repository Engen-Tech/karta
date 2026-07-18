#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for ownership-cost-ledger (card: benchmarks/meta/ownership-cost-ledger.md).

Full coverage, so partial is False. The probe drives
benchmarks/field/ownership_ledger.py in CHECK MODE only — it recomputes the fresh
ownership-cost row in memory and diffs it against the last committed row without
writing, so a gate run leaves benchmarks/field/results/ownership-ledger.json
byte-identical.

Gating and evidence split (the card's own): gating = ledger integrity plus the
buy-vs-build no-longer-correct-without-action check; the fresh-row cost diff itself
is recorded in the gate row's metrics as evidence for the human, never asserted.

Stance:
  status "fail"  — a corrupted ledger (rows out of order or a duplicate release) or
                   a buy-vs-build row answered no-longer-correct with neither an
                   action nor a dated waiver.
  status "pass"  — growing cost deltas are findings for the human, never a gate
                   failure.

Usage: python3 benchmarks/probes/ownership-cost-ledger.py --target <repo-root>
Prints the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
to stdout and exits 0 whether pass or fail (a nonzero exit means the probe itself
crashed). --self-test validates the clean-pass mapping, the corrupted-ledger fail
flip, the buy-vs-build-violation fail flip, and the contract shape, printing
[PASS]/[FAIL] lines and an N/N checks passed summary; --self-test exits 0 only when
the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

PROBE_ID = "ownership-cost-ledger"
IMPLEMENTED_CHECKS = [
    "machinery-counts (a)",
    "manual-release-steps (b)",
    "bench-spend (c)",
    "triage-load (d)",
    "buy-vs-build-recheck",
    "ledger-integrity",
]
# Card-internal inconsistency surfaced as a standing info finding, never applied.
CARD_ERRATUM_FINDING = {
    "finding_id": "card-results-dir-mismatch",
    "severity": "info",
    "summary": "card frontmatter results: benchmarks/meta/results/ contradicts the "
    "procedure's benchmarks/field/results/ownership-ledger.json path; the ledger "
    "follows the procedure (card left untouched, mismatch recorded).",
}


def _load_ledger_module(target: Path):
    path = target / "benchmarks" / "field" / "ownership_ledger.py"
    spec = importlib.util.spec_from_file_location("ownership_ledger", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(result: dict) -> dict:
    integrity = result["integrity_errors"]
    violations = result["buy_vs_build_violations"]
    findings: list[dict] = []
    for i, e in enumerate(integrity, 1):
        findings.append({"finding_id": f"ledger-integrity-{i}", "severity": "error",
                         "summary": e})
    for i, v in enumerate(violations, 1):
        findings.append({"finding_id": f"buy-vs-build-{i}", "severity": "error",
                         "summary": v})
    # Growing cost deltas are recorded as info findings, never gating.
    diff = result["diff"]
    if not diff.get("first_row"):
        for field in ("duplicated_bytes_delta", "script_count_delta", "bench_loc_delta"):
            val = diff.get(field)
            if isinstance(val, (int, float)) and val > 0:
                findings.append({"finding_id": f"cost-growth-{field}", "severity": "info",
                                 "summary": f"{field} grew by {val} since the last row "
                                            f"(evidence for the human, never a gate failure)"})
    findings.append(CARD_ERRATUM_FINDING)
    status = "fail" if (integrity or violations) else "pass"
    return {
        "id": PROBE_ID,
        "status": status,
        "partial": False,
        "implemented_checks": IMPLEMENTED_CHECKS,
        "findings": findings,
        "metrics": {
            "rows_total": result["rows_total"],
            "integrity_errors": len(integrity),
            "buy_vs_build_violations": len(violations),
            "fresh_row_diff": diff,
        },
    }


def _run_self_test(target: Path) -> int:
    ol = _load_ledger_module(target)
    results: list[tuple[str, bool, str]] = []

    live = ol.check(target)
    payload = _payload(live)

    shape_ok = (payload["id"] == PROBE_ID and payload["status"] in ("pass", "fail")
                and isinstance(payload["partial"], bool)
                and isinstance(payload["implemented_checks"], list)
                and isinstance(payload["findings"], list)
                and isinstance(payload["metrics"], dict))
    results.append(("payload satisfies the gate probe JSON contract", shape_ok,
                    str(payload)[:120]))
    results.append(("partial is False (full coverage)", payload["partial"] is False, ""))
    results.append(("the live committed ledger is intact and maps to status pass",
                    payload["status"] == "pass",
                    f"status={payload['status']}; findings={payload['findings']}"))
    results.append(("the fresh-row diff is recorded in metrics",
                    "fresh_row_diff" in payload["metrics"], str(payload["metrics"].keys())))

    # A corrupted (reordered) ledger flips status to fail.
    corrupt = {
        "fresh_row": live["fresh_row"], "last_row": live["last_row"],
        "rows_total": 2,
        "integrity_errors": ["row 1: date '2026-01-01' is earlier than the previous row's '2026-02-01'"],
        "buy_vs_build_violations": [],
        "diff": live["diff"],
    }
    corrupt_payload = _payload(corrupt)
    results.append(("a corrupted-ledger (out-of-order) result flips status to fail",
                    corrupt_payload["status"] == "fail"
                    and any(f["severity"] == "error" for f in corrupt_payload["findings"]),
                    str(corrupt_payload["status"])))

    # A buy-vs-build no-longer-correct-without-action violation flips status to fail.
    bvb = dict(corrupt)
    bvb["integrity_errors"] = []
    bvb["buy_vs_build_violations"] = ["buy-vs-build component 'gate-runner' is "
                                      "no-longer-correct with neither an action nor a dated waiver"]
    bvb_payload = _payload(bvb)
    results.append(("a buy-vs-build no-longer-correct-without-action violation flips "
                    "status to fail",
                    bvb_payload["status"] == "fail", str(bvb_payload["status"])))

    # A positive cost delta is an info finding, not a fail.
    grow = {"fresh_row": live["fresh_row"], "last_row": live["last_row"],
            "rows_total": 2, "integrity_errors": [], "buy_vs_build_violations": [],
            "diff": {"first_row": False, "duplicated_bytes_delta": 512,
                     "script_count_delta": 1, "bench_loc_delta": 40}}
    grow_payload = _payload(grow)
    results.append(("growing cost deltas are info findings, never a gate failure",
                    grow_payload["status"] == "pass"
                    and any(f["severity"] == "info" and "grew" in f["summary"]
                            for f in grow_payload["findings"]),
                    str(grow_payload["status"])))

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

    ol = _load_ledger_module(target)
    print(json.dumps(_payload(ol.check(target)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
