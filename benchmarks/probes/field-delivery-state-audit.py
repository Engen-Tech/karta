#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe: consumer-repo delivery-state audit (field-delivery-state-audit).

Implements benchmarks/field/field-delivery-state-audit.md in full, so the probe
declares partial: false. It maps the four-auditor composer (benchmarks/flow/run_all.py)
into the gate contract for run_gate.

Gate stance — gating = fixture, live = recorded evidence. The status is decided by
the seeded-fixture detection matrix ALONE: run_all must score 100% detection with
0 false positives on the two pinned negative cases, and the doctrine anchors must
still be present, or status is "fail". The live sweep's field findings are the
reported product, recorded as evidence and never a gate condition; a missing
consumer directory is a recorded error in live mode, not a status flip.

shared_terms this item is enrolled in, rendered verbatim (byte-identity is the
checked invariant that check_shared_terms.py enforces across enrolled items):

  probe-json-contract canonical (the stdout shape every gate probe emits):
    {"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}

  self-test-report-format canonical (what every --self-test prints):
    [PASS]/[FAIL] lines and an N/N checks passed summary

The probe exits 0 whether the status is pass or fail (a nonzero exit means the
probe itself crashed). Stdlib only; every path derives from --target.

  field-delivery-state-audit.py --target <repo>            # gate mode (run_gate)
  field-delivery-state-audit.py --fixture-only             # matrix only, no live sweep
  field-delivery-state-audit.py --consumers <p,..>         # live sweep set
  field-delivery-state-audit.py --write-snapshots [--consumers ...]  # commit snapshots
  field-delivery-state-audit.py --self-test                # embedded, [PASS]/[FAIL] N/N
"""
from __future__ import annotations
import argparse, datetime, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "flow"))
import run_all  # noqa: E402

VECTOR = "field-delivery-state-audit"
IMPLEMENTED_CHECKS = ["lint_delivery_refs", "audit_hygiene", "check_markers",
                      "audit_binder_mutations", "fixture-detection-matrix",
                      "doctrine-anchor-grep"]
RESULTS_DIR = Path("benchmarks") / "flow" / "results"


def _default_consumers(target: Path) -> list[tuple[str, Path]]:
    """The three audited repos: --target itself plus sibling parchmark and gringotts."""
    parent = target.parent
    out = [(target.name, target)]
    for name in ("parchmark", "gringotts"):
        out.append((name, parent / name))
    return out


def _build_status(target: Path) -> tuple[dict, list[dict], dict]:
    """Return (matrix, gate_findings, metrics) — status is decided here, on the
    fixture matrix and doctrine anchors alone."""
    findings: list[dict] = []
    missing_doctrine = run_all.check_doctrine(target)
    for d in missing_doctrine:
        findings.append({"finding_id": f"doctrine-missing:{d}", "severity": "high",
                         "summary": f"doctrine anchor gone — update the linter first: {d}"})
    matrix = run_all.fixture_matrix()
    for cls in matrix["missing"]:
        findings.append({"finding_id": f"fixture-miss:{cls}", "severity": "high",
                         "summary": f"fixture no longer detects {cls} — matrix incomplete"})
    for fp in matrix["false_positives"]:
        findings.append({"finding_id": f"fixture-fp:{fp['slug']}:{fp['class']}",
                         "severity": "high",
                         "summary": f"false positive on pinned negative {fp['slug']}: "
                                    f"{fp['class']}"})
    ok = matrix["ok"] and not missing_doctrine
    metrics = {"detection_rate": matrix["detection_rate"],
               "negatives_clean": matrix["negatives_clean"],
               "doctrine_anchors_present": not missing_doctrine}
    return {"ok": ok, "matrix": matrix}, findings, metrics


def _live_evidence(target: Path, consumers: list[tuple[str, Path]],
                   audit_ts_iso: str) -> tuple[list[dict], dict]:
    """Best-effort live sweep for evidence only — never flips status."""
    findings, per_repo = [], {}
    for name, repo in consumers:
        if not (repo / ".git").exists() and not repo.joinpath("HEAD").exists():
            findings.append({"finding_id": f"consumer-missing:{name}", "severity": "info",
                             "summary": f"consumer repo {name} not found at {repo} — "
                                        "live sweep skipped (evidence only)"})
            per_repo[name] = "missing"
            continue
        try:
            snap = run_all.live_snapshot(repo, name, target, audit_ts_iso)
            per_repo[name] = snap["finding_count"]
            findings.append({"finding_id": f"evidence:{name}", "severity": "info",
                             "summary": f"{name}: {snap['finding_count']} field findings "
                                        "recorded as evidence (not gated)"})
        except Exception as e:  # evidence is best-effort; never fail the gate on it
            per_repo[name] = f"error: {e}"
            findings.append({"finding_id": f"evidence-error:{name}", "severity": "info",
                             "summary": f"{name}: live sweep error (evidence only): {e}"})
    return findings, {"live_findings_per_repo": per_repo}


def _emit(status: str, findings: list[dict], metrics: dict) -> None:
    print(json.dumps({"id": VECTOR, "status": status, "partial": False,
                      "implemented_checks": IMPLEMENTED_CHECKS,
                      "findings": findings, "metrics": metrics}))


def _gate_mode(target: Path, fixture_only: bool, consumers: list[tuple[str, Path]],
               audit_ts_iso: str) -> int:
    verdict, findings, metrics = _build_status(target)
    status = "pass" if verdict["ok"] else "fail"
    if not fixture_only:
        ev_findings, ev_metrics = _live_evidence(target, consumers, audit_ts_iso)
        findings += ev_findings
        metrics.update(ev_metrics)
    _emit(status, findings, metrics)
    return 0


def _write_snapshots(target: Path, consumers: list[tuple[str, Path]],
                     audit_ts_iso: str, run_date: str) -> int:
    missing = run_all.check_doctrine(target)
    if missing:
        print(f"doctrine anchors gone — update the linter first: {missing}", file=sys.stderr)
        return 2
    out_dir = target / RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, repo in consumers:
        if not repo.exists():
            print(f"consumer {name} not found at {repo}", file=sys.stderr)
            return 2
        snap = run_all.live_snapshot(repo, name, target, audit_ts_iso)
        path = out_dir / f"{run_date}-{name}.json"
        path.write_text(json.dumps(snap, indent=1) + "\n")
        print(f"wrote {path.relative_to(target)} ({snap['finding_count']} findings)")
    return 0


def _run_self_test(target: Path) -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        suffix = f" ({detail})" if detail and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        results.append(ok)

    verdict, findings, metrics = _build_status(target)
    check("fixture matrix is perfect (100% detection, 0 FP)", verdict["ok"],
          f"missing={verdict['matrix']['missing']} "
          f"fps={[f['finding_id'] for f in findings]}")
    check("gate status is pass when the matrix is perfect and doctrine present",
          verdict["ok"] is True)

    # contract shape of the emitted payload
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _emit("pass", findings, metrics)
    payload = json.loads(buf.getvalue())
    shape_ok = (payload.get("id") == VECTOR
                and payload.get("status") in ("pass", "fail")
                and payload.get("partial") is False
                and isinstance(payload.get("implemented_checks"), list)
                and isinstance(payload.get("findings"), list)
                and isinstance(payload.get("metrics"), dict))
    check("emitted payload satisfies the gate probe JSON contract (partial false)",
          shape_ok)

    # a fixture detection miss flips status to fail (simulate a missing class)
    saved = run_all.REQUIRED_CLASSES
    try:
        run_all.REQUIRED_CLASSES = frozenset(saved | {"NEVER-SEEDED-CLASS"})
        v2, _, _ = _build_status(target)
        check("a fixture detection miss flips status to fail", v2["ok"] is False)
    finally:
        run_all.REQUIRED_CLASSES = saved

    # a missing consumer is recorded evidence, never a status flip
    ev, _ = _live_evidence(target, [("ghost-repo", target / "no-such-dir")],
                           run_all.FIXTURE_AUDIT_TS)
    check("a missing consumer is recorded as evidence, not a gate failure",
          any(f["finding_id"] == "consumer-missing:ghost-repo" for f in ev))

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=str(Path(__file__).resolve().parents[2]),
                    help="karta repo root (run_gate passes this)")
    ap.add_argument("--fixture-only", action="store_true",
                    help="run the detection matrix against the fixture, skip the live sweep")
    ap.add_argument("--consumers", default=None,
                    help="comma-separated repo paths for the live sweep")
    ap.add_argument("--write-snapshots", action="store_true",
                    help="write committed per-repo snapshots under benchmarks/flow/results/")
    ap.add_argument("--audit-timestamp", default=None,
                    help="ISO-8601 recorded audit time (default: now)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="snapshot filename date (YYYY-MM-DD)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if args.self_test:
        return _run_self_test(target)

    audit_ts_iso = args.audit_timestamp or \
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    if args.consumers:
        consumers = [(Path(p).name, Path(p).resolve())
                     for p in args.consumers.split(",") if p.strip()]
    else:
        consumers = _default_consumers(target)

    if args.write_snapshots:
        return _write_snapshots(target, consumers, audit_ts_iso, args.date)
    return _gate_mode(target, args.fixture_only, consumers, audit_ts_iso)


if __name__ == "__main__":
    sys.exit(main())
