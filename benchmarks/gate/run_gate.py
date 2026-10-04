#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Deterministic gate runner: compose the bench probes and block on their verdicts.

Loads benchmarks/bench-spec.json, runs benchmarks/probes/<vector-id>.py for every
vector that has a probe (missing probe => SKIPPED, loudly counted, never hidden),
and writes a dated results file to benchmarks/results/gate/<date>-gate.json.

Probe contract (stdout, JSON): {"id", "status": "pass"|"fail", "partial": bool,
"implemented_checks": [...], "findings": [{"finding_id","severity","summary"}...],
"metrics": {...}}. A probe crash, timeout, or malformed stdout => ERROR.

Usage:
  python3 benchmarks/gate/run_gate.py                      # all 24 vectors, date=today
  python3 benchmarks/gate/run_gate.py --date 2026-07-17    # pin the results filename
  python3 benchmarks/gate/run_gate.py --only sme-pack-static-suite,parity-mirror-sync-integrity
  python3 benchmarks/gate/run_gate.py --strict             # SKIPPED > 0 also fails
  python3 benchmarks/gate/run_gate.py --release            # incomplete release coverage also fails
  python3 benchmarks/gate/run_gate.py --consumers ../parchmark,../gringotts

Consumer-aware probes otherwise guess the consumer repos as siblings of this
checkout's parent, which is wrong whenever karta is checked out in a worktree —
three probes then fail closed on repos that exist but were looked for in the
wrong place. Pass --consumers (or export KARTA_BENCH_CONSUMERS) to say where
they actually are; the runner forwards it to every probe through the environment.

Release coverage: benchmarks/gate/release-required.json records one explicit
decision per vector (required, or not required with a written reason). A vector the
inventory marks needs_consumers is reported SKIPPED, with that reason, when no
consumers are given. Each row carries its status, its partial flag, and
known_open_count (findings a passing regression check still reports); the summary
keeps known_open apart from regression_health. The result's "coverage" block names
every required vector that is missing, not PASS, or partial without a recorded
justification. The runner validates the result's structure before writing it.

Exit: 1 if any probe FAILed or ERRORed, or measured source changed during the run
(plus, under --strict, if anything was SKIPPED; plus, under --release, if release
coverage is incomplete), else 0. 2 if the runner would write a malformed result.
"""
from __future__ import annotations
import argparse, datetime, json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from release_evidence import (INVENTORY_REL, coverage_problems, inventory_problems,
                              result_problems, working_fingerprint)
SPEC = ROOT / "benchmarks" / "bench-spec.json"
PROBES = ROOT / "benchmarks" / "probes"
RESULTS = ROOT / "benchmarks" / "results" / "gate"
# A probe may run several wrapped checkers back to back (parity-mirror-sync-integrity
# runs three drills, each able to invoke validate_plugin.py at ~79s), so the per-probe
# ceiling has to clear the sum, not one checker. Native Windows' complete parity
# probe can run two several-minute validator passes, so ten minutes was too short.
PROBE_TIMEOUT_S = 1200
# Consumer-repo locations reach the probes through the environment rather than a
# per-probe --consumers registry here: a registry would silently drift as probes
# are added, and probes that ignore consumers ignore the variable harmlessly.
# Consumer-aware probes read it as their --consumers default, so an explicit
# --consumers on a hand-run probe still wins.
CONSUMERS_ENV = "KARTA_BENCH_CONSUMERS"


def _git_sha() -> str:
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=10)
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def _plugin_version() -> str:
    try:
        return json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def _run_probe(vector_id: str, consumers: str | None = None) -> dict:
    """Run one probe and reduce it to a result row. Never raises."""
    probe = PROBES / f"{vector_id}.py"
    if not probe.is_file():
        return _skipped_row(vector_id, "no probe yet")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if consumers:
        env[CONSUMERS_ENV] = consumers
    try:
        proc = subprocess.run([sys.executable, str(probe), "--target", str(ROOT)],
                              capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
                              cwd=str(ROOT), env=env, encoding="utf-8")
    except subprocess.TimeoutExpired:
        return _error_row(vector_id, f"probe timed out after {PROBE_TIMEOUT_S}s")
    except OSError as e:
        return _error_row(vector_id, f"probe did not run ({e})")
    if proc.returncode != 0:
        tail = "; ".join(proc.stderr.strip().splitlines()[-3:])
        return _error_row(vector_id, f"probe crashed (exit {proc.returncode})" +
                          (f": {tail}" if tail else ""))
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
        return _error_row(vector_id, f"bad probe JSON (exit {proc.returncode}: {e}; {tail})")
    problem = _contract_violation(vector_id, payload)
    if problem:
        return _error_row(vector_id, f"contract violation: {problem}")
    findings = payload["findings"]
    return {"id": vector_id, "status": payload["status"].upper(),
            "partial": payload["partial"],
            "implemented_checks": payload["implemented_checks"],
            "findings_count": len(findings), "findings": findings,
            "metrics": payload["metrics"], "detail": ""}


def _contract_violation(vector_id: str, payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "stdout is not a JSON object"
    if payload.get("id") != vector_id:
        return f"probe id {payload.get('id')!r} != vector id {vector_id!r}"
    if payload.get("status") not in ("pass", "fail"):
        return f"status must be 'pass'|'fail', got {payload.get('status')!r}"
    if not isinstance(payload.get("partial"), bool):
        return "'partial' must be a bool"
    if not isinstance(payload.get("implemented_checks"), list):
        return "'implemented_checks' must be a list"
    if not isinstance(payload.get("findings"), list):
        return "'findings' must be a list"
    if not isinstance(payload.get("metrics"), dict):
        return "'metrics' must be an object"
    return None


def _load_inventory(spec_ids: list[str]) -> tuple[dict | None, list[str]]:
    """The release inventory and its problems; an unreadable one is a problem, not a crash."""
    try:
        inventory = json.loads((ROOT / INVENTORY_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"release inventory {INVENTORY_REL} is unreadable ({e})"]
    problems = inventory_problems(inventory, spec_ids)
    return (None, problems) if problems else (inventory, [])


def _skipped_row(vector_id: str, detail: str) -> dict:
    return {"id": vector_id, "status": "SKIPPED", "partial": None,
            "implemented_checks": [], "findings_count": 0, "findings": [],
            "metrics": {}, "detail": detail}


def _error_row(vector_id: str, detail: str) -> dict:
    return {"id": vector_id, "status": "ERROR", "partial": None,
            "implemented_checks": [], "findings_count": 0, "findings": [],
            "metrics": {}, "detail": detail}


def _fmt_metrics(metrics: dict) -> str:
    parts = []
    for k, v in metrics.items():
        if isinstance(v, list):
            v = ",".join(str(x) for x in v)
        parts.append(f"{k}={v}")
    return "; ".join(parts) or "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="results filename date (YYYY-MM-DD, default today)")
    ap.add_argument("--only", default=None, metavar="ID,ID",
                    help="run only these comma-separated vector ids")
    ap.add_argument("--strict", action="store_true",
                    help="also exit 1 when any vector is SKIPPED (full-coverage mode)")
    ap.add_argument("--release", action="store_true",
                    help="also exit 1 when release coverage against "
                         f"{INVENTORY_REL} is incomplete")
    ap.add_argument("--consumers", default=os.environ.get(CONSUMERS_ENV),
                    metavar="PATH,PATH",
                    help="enrolled consumer repo paths for consumer-aware probes "
                         "(default: each probe's sibling-directory guess, which is "
                         "wrong whenever this checkout is a worktree)")
    args = ap.parse_args()
    # A provided value that names no paths ("", ",", " " — e.g. an interpolation
    # of unset shell vars) must refuse loudly — never degrade into the sibling
    # guess or an empty sweep the consumer-aware probes would vacuously pass.
    # Only a genuinely unset env/flag (None) falls back to the probes' defaults.
    if args.consumers is not None and \
            not [s for s in args.consumers.split(",") if s.strip()]:
        ap.error(f"--consumers / {CONSUMERS_ENV} is set but names no paths: "
                 f"{args.consumers!r}")

    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    vector_ids = [v["id"] for v in spec["vectors"]]
    if args.only:
        wanted = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = sorted(set(wanted) - set(vector_ids))
        if unknown:
            ap.error(f"unknown vector id(s): {', '.join(unknown)}")
        vector_ids = [vid for vid in vector_ids if vid in set(wanted)]

    inventory, inventory_errors = _load_inventory([v["id"] for v in spec["vectors"]])
    decisions = {e["id"]: e for e in inventory["vectors"]} if inventory else {}

    source_before = working_fingerprint(ROOT)
    rows = []
    for vid in vector_ids:
        if decisions.get(vid, {}).get("needs_consumers") and not args.consumers:
            rows.append(_skipped_row(vid, "not assessed: needs consumer repos "
                                          f"(--consumers or {CONSUMERS_ENV})"))
        else:
            rows.append(_run_probe(vid, args.consumers))
    source_stable = source_before == working_fingerprint(ROOT)
    for r in rows:
        r["known_open_count"] = len(r["findings"]) if r["status"] == "PASS" else None
    summary = {"total": len(rows)}
    for status in ("PASS", "FAIL", "ERROR", "SKIPPED"):
        summary[status.lower()] = sum(1 for r in rows if r["status"] == status)
    summary["known_open"] = sum(r["known_open_count"] or 0 for r in rows)
    summary["regression_health"] = "red" if summary["fail"] or summary["error"] else "green"

    RESULTS.mkdir(parents=True, exist_ok=True)
    # A subset run never clobbers the full gate's dated file (append-only history).
    out = RESULTS / (f"{args.date}-gate.partial.json" if args.only else f"{args.date}-gate.json")
    doc = {
        "schema_version": 2,
        "run_date": args.date,
        "karta_sha": _git_sha(),
        "source_sha256": source_before,
        "source_stable": source_stable,
        "plugin_version": _plugin_version(),
        "strict": args.strict,
        "only": sorted(vector_ids) if args.only else None,
        "vectors": rows,
        "summary": summary,
    }
    coverage = inventory_errors or coverage_problems(doc, inventory)
    doc["coverage"] = {"inventory": INVENTORY_REL,
                       "required": sorted(v for v, e in decisions.items() if e["required"]),
                       "problems": coverage, "complete": not coverage}
    malformed = result_problems(doc)
    if malformed:
        print("run_gate: refusing to write a malformed result:\n  " + "\n  ".join(malformed),
              file=sys.stderr)
        return 2
    out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    print(f"# karta deterministic gate — {args.date} "
          f"(karta {_plugin_version()} @ {_git_sha()[:9]})")
    print()
    print("| vector | status | partial | checks | findings | metrics |")
    print("|-|-|-|-|-|-|")
    for r in rows:
        partial = "-" if r["partial"] is None else str(r["partial"]).lower()
        checks = len(r["implemented_checks"]) or "-"
        detail = f" ({r['detail']})" if r["status"] == "ERROR" else ""
        print(f"| {r['id']} | {r['status']}{detail} | {partial} "
              f"| {checks} | {r['findings_count']} | {_fmt_metrics(r['metrics'])} |")
    print()
    for r in rows:
        for f in r["findings"]:
            print(f"  [{r['id']}] {f.get('severity', '?')}: {f.get('summary', '?')}")
    print(f"Summary: pass={summary['pass']} fail={summary['fail']} "
          f"error={summary['error']} skipped={summary['skipped']} total={summary['total']}")
    print(f"*** SKIPPED: {summary['skipped']} of {summary['total']} vectors are unmeasured "
          f"(no probe yet, or no consumer repos given) — NOT passing. ***")
    print(f"Known open (reported by passing checks, not regressions): {summary['known_open']}; "
          f"regression health: {summary['regression_health']}")
    if coverage:
        print(f"Release coverage INCOMPLETE against {INVENTORY_REL}:")
        for problem in coverage:
            print(f"  - {problem}")
    else:
        print(f"Release coverage complete against {INVENTORY_REL}.")
    print(f"Results: {out.relative_to(ROOT)}")

    if not source_stable:
        print("Source changed during the run; this result cannot authorize a release.")
    if summary["fail"] or summary["error"] or not source_stable:
        return 1
    if args.strict and summary["skipped"]:
        return 1
    if args.release and coverage:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
