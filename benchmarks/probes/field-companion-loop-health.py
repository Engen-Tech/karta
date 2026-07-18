#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for field-companion-loop-health: companion-loop coverage and health.

Runs benchmarks/field/audit_companions.py over each enrolled consumer repo,
writes the dated companions snapshot to benchmarks/field/results/, and maps the
rows into the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
on stdout, exiting 0 whether pass or fail (a nonzero exit means the probe
itself crashed). Stdlib-only argparse with a --self-test mode printing
[PASS]/[FAIL] lines and an N/N checks passed summary (--self-test exits 0 only
when the summary is N/N checks passed, nonzero otherwise).

Consumer repos are addressed through --consumers <path,...>, defaulting to the
sibling directories parchmark and gringotts resolved relative to the --target
repo's parent — no absolute paths in code or configuration; a missing consumer
directory is a clear error in live mode and irrelevant in self-test.

Stance: a parser import failure or a missing enrolled repo in live mode reports
status "fail" (fail-closed on probe integrity); coverage whiffs, additive-floor
violations, and correction incidents are findings — field facts feeding the
kaizen-phase-2 decision, never a gate failure.

Usage:
  python3 benchmarks/probes/field-companion-loop-health.py --target <repo-root>
  python3 benchmarks/probes/field-companion-loop-health.py --self-test
"""
from __future__ import annotations
import argparse, datetime, json, subprocess, sys
from pathlib import Path

PROBE_ID = "field-companion-loop-health"
RUNNER = Path("benchmarks") / "field" / "audit_companions.py"
RESULTS_DIR = Path("benchmarks") / "field" / "results"
DEFAULT_CONSUMERS = ("parchmark", "gringotts")
RUNNER_TIMEOUT_S = 50
ROW_KEYS = ("repo", "head", "caveat", "deliveries", "epochs", "gardner",
            "kaizen", "config_staleness", "findings")
IMPLEMENTED_CHECKS = [
    "enable-epoch-transitions",
    "gardner-coverage-per-delivery",
    "kaizen-additive-floor (imported validate_packs grammar)",
    "correction-incidents-0-14d-right-censored",
    "spawn-utility",
    "config-staleness",
]


def _assemble(entries: list[tuple[str, dict]]) -> dict:
    """Map per-repo rows (or error stubs {"error": ...}) into the contract payload."""
    findings: list[dict] = []
    metrics: dict = {"consumers": [name for name, _ in entries]}
    status = "pass"
    seen_contradiction = False
    for name, entry in entries:
        if "error" in entry:
            status = "fail"
            findings.append({"finding_id": f"{name}-probe-integrity",
                             "severity": "error",
                             "summary": f"{name}: {entry['error']}"})
            continue
        if any(k not in entry for k in ROW_KEYS):
            status = "fail"
            missing = [k for k in ROW_KEYS if k not in entry]
            findings.append({"finding_id": f"{name}-malformed-row",
                             "severity": "error",
                             "summary": f"{name}: row missing keys {missing}"})
            continue
        for f in entry["findings"]:
            if f["finding_id"] == "card-contradiction-history-walk":
                if not seen_contradiction:
                    findings.append(f)
                    seen_contradiction = True
            else:
                findings.append(f)
        g, kz = entry["gardner"], entry["kaizen"]
        for w in g["whiffs"]:
            findings.append({"finding_id": f"{name}-gardner-whiff-{w['slug']}",
                             "severity": "warning",
                             "summary": f"{name}: delivery {w['slug']} has no "
                                        f"'docs: gardner {w['slug']}' commit "
                                        f"({w['whiff_class']})"})
        for v in kz["floor"]["fail"]:
            findings.append({"finding_id": f"{name}-floor-fail-{v['id']}",
                             "severity": "error",
                             "summary": f"{name}: {v['pack']} @ "
                                        f"{v['kaizen_sha'][:9]}: {v['summary']}"})
        for v in kz["floor"]["warn"]:
            findings.append({"finding_id": f"{name}-floor-warn-{v['id']}",
                             "severity": "warning",
                             "summary": f"{name}: {v['pack']} @ "
                                        f"{v['kaizen_sha'][:9]}: {v['summary']}"})
        for i in kz["incidents"]:
            findings.append({"finding_id": f"{name}-incident-"
                                           f"{i['correcting_sha'][:9]}",
                             "severity": "info",
                             "summary": f"{name}: correction incident "
                                        f"({i['kaizen_sha'][:9]}, "
                                        f"{i['correcting_sha'][:9]}, "
                                        f"{i['delta_minutes']} min) — "
                                        f"{i['subject']}"})
        for s in entry["config_staleness"]:
            findings.append({"finding_id": f"{name}-staleness-"
                                           f"{Path(s['config']).stem}",
                             "severity": "info",
                             "summary": f"{name}: {s['config']} is "
                                        f"{s['deliveries_since_n']} deliveries "
                                        f"since last touch; focus="
                                        f"{s['focus']!r}; last 5 deliveries: "
                                        f"{s['last_5_delivery_slugs']}"})
        su = kz["spawn_utility"]
        findings.append({"finding_id": f"{name}-spawn-utility",
                         "severity": "info",
                         "summary": f"{name}: kaizen spawn utility "
                                    f"{su['substantive_kaizen_commits_n']} "
                                    f"substantive edits over "
                                    f"{su['deliveries_in_kaizen_epoch_n']} "
                                    f"deliveries in the kaizen epoch"})
        metrics[f"{name}_gardner"] = g["fraction"]
        metrics[f"{name}_floor_fail"] = len(kz["floor"]["fail"])
        metrics[f"{name}_floor_warn"] = len(kz["floor"]["warn"])
        metrics[f"{name}_incidents"] = len(kz["incidents"])
        metrics[f"{name}_window_open"] = len(kz["window_open"])
        metrics[f"{name}_spawn_substantive"] = su["substantive_kaizen_commits_n"]
    return {"id": PROBE_ID, "status": status, "partial": False,
            "implemented_checks": IMPLEMENTED_CHECKS,
            "findings": findings, "metrics": metrics}


def _live(target: Path, consumers: list[Path]) -> dict:
    entries: list[tuple[str, dict]] = []
    for path in consumers:
        name = path.name
        if not path.is_dir():
            entries.append((name, {"error": f"missing-consumer: enrolled repo "
                                            f"directory not found at {path}"}))
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(target / RUNNER), "--repo", str(path)],
                capture_output=True, text=True, timeout=RUNNER_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as e:
            entries.append((name, {"error": f"runner did not complete ({e})"}))
            continue
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
            entries.append((name, {"error": f"runner emitted no JSON row "
                                            f"(exit {proc.returncode}: {tail})"}))
            continue
        if proc.returncode != 0 and "error" not in payload:
            payload = {"error": f"runner exit {proc.returncode}"}
        entries.append((name, payload))
    # dated companions snapshot: recorded evidence, overwrites a same-date file
    results = target / RESULTS_DIR
    results.mkdir(parents=True, exist_ok=True)
    run_date = datetime.date.today().isoformat()
    rows = [dict(entry, repo=name) if "error" in entry else entry
            for name, entry in entries]
    (results / f"{run_date}-companions.json").write_text(
        json.dumps({"run_date": run_date, "rows": rows},
                   indent=1, sort_keys=False) + "\n")
    return _assemble(entries)


# --- Self-test -----------------------------------------------------------------

def _stub_row(name: str) -> dict:
    return {
        "repo": name, "head": "0" * 40, "caveat": "stub",
        "deliveries": [{"slug": "s1", "sha": "1" * 40, "ct": 1, "via":
                        "integration-merge"}],
        "epochs": {},
        "gardner": {"fraction": "1/2", "covered_n": 1, "denominator_N": 2,
                    "per_slug": [], "excluded_pre_enable": [],
                    "whiffs": [{"slug": "s2", "whiff_class": "timing-race"}]},
        "kaizen": {"floor": {"fail": [{"kaizen_sha": "2" * 40, "pack":
                                       ".karta/sme/p.md", "id": "x.1",
                                       "summary": "active id x.1 vanished "
                                                  "without a tombstone"}],
                             "warn": [], "checked_n": 1},
                   "incidents": [{"kaizen_sha": "2" * 40,
                                  "correcting_sha": "3" * 40,
                                  "delta_minutes": 17, "subject": "sme: fix"}],
                   "closed_denominator_n": 1, "closed_denominator": ["2" * 40],
                   "window_open": [],
                   "spawn_utility": {"substantive_kaizen_commits_n": 1,
                                     "substantive_kaizen_commits": ["2" * 40],
                                     "seed_excluded": [],
                                     "deliveries_in_kaizen_epoch_n": 1,
                                     "deliveries_in_kaizen_epoch": ["s1"]}},
        "config_staleness": [{"config": ".karta/doc-gardner.json",
                              "last_touch_sha": "4" * 40, "last_touch_ct": 1,
                              "deliveries_since_n": 1,
                              "deliveries_since": ["s1"], "focus": "f",
                              "last_5_delivery_slugs": ["s1"]}],
        "findings": [{"finding_id": "card-contradiction-history-walk",
                      "severity": "info", "summary": "stub"}],
    }


def _self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    p = _assemble([("parchmark", _stub_row("parchmark")),
                   ("gringotts", _stub_row("gringotts"))])
    check("contract payload carries exactly the gate keys",
          set(p) == {"id", "status", "partial", "implemented_checks",
                     "findings", "metrics"} and p["id"] == PROBE_ID)
    check("well-formed rows map to status pass with partial false",
          p["status"] == "pass" and p["partial"] is False)
    check("whiffs, floor violations, and incidents are findings, never a "
          "gate failure",
          p["status"] == "pass"
          and any(f["finding_id"] == "parchmark-gardner-whiff-s2"
                  for f in p["findings"])
          and any(f["finding_id"] == "parchmark-floor-fail-x.1"
                  for f in p["findings"])
          and any(f["finding_id"].startswith("parchmark-incident-")
                  for f in p["findings"]))
    check("card-contradiction finding surfaced once",
          sum(1 for f in p["findings"]
              if f["finding_id"] == "card-contradiction-history-walk") == 1)
    check("metrics carry per-repo n/N gardner fractions and raw counts",
          p["metrics"]["parchmark_gardner"] == "1/2"
          and p["metrics"]["gringotts_floor_fail"] == 1
          and p["metrics"]["consumers"] == ["parchmark", "gringotts"])
    check("every finding is a {finding_id, severity, summary} object",
          all(set(f) == {"finding_id", "severity", "summary"}
              for f in p["findings"]))
    check("payload is JSON-serializable",
          bool(json.dumps(p)))

    p_import = _assemble([("parchmark",
                           {"error": "parser-import-failure: cannot import "
                                     "ITEM_RE/TOMBSTONE_RE"}),
                          ("gringotts", _stub_row("gringotts"))])
    check("a parser import failure flips status to fail",
          p_import["status"] == "fail"
          and any("parser-import-failure" in f["summary"]
                  for f in p_import["findings"]))
    p_missing = _assemble([("parchmark",
                            {"error": "missing-consumer: enrolled repo "
                                      "directory not found at ../parchmark"})])
    check("a missing enrolled repo in live mode flips status to fail",
          p_missing["status"] == "fail")
    bad = _stub_row("gringotts")
    del bad["kaizen"]
    p_bad = _assemble([("gringotts", bad)])
    check("a malformed row (missing keys) flips status to fail",
          p_bad["status"] == "fail"
          and any(f["finding_id"] == "gringotts-malformed-row"
                  for f in p_bad["findings"]))

    passed = sum(1 for _, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this probe's repo)")
    ap.add_argument("--consumers", default=None, metavar="PATH,PATH",
                    help="enrolled consumer repos (default: sibling "
                         "directories parchmark and gringotts)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    target = args.target.resolve()
    if args.consumers:
        consumers = [Path(s).resolve() for s in args.consumers.split(",") if s.strip()]
    else:
        consumers = [target.parent / name for name in DEFAULT_CONSUMERS]
    print(json.dumps(_live(target, consumers), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
