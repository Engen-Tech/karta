#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate adapter: prose / schema / guard contradiction count (all four passes).

Implements benchmarks/flow/flow-spec-contradictions.md in full, so this probe declares
partial: false. It is a thin, stdlib-only adapter around the report-only runner
benchmarks/flow/check_contradictions.py: in gate mode it subprocess-runs the card-pinned

    uv run --with jsonschema benchmarks/flow/check_contradictions.py --repo <target> \
        --out <temp>

(the one sanctioned non-stdlib invocation in this binder; jsonschema resolves from the
local uv cache — uv is part of the host toolchain per design_facts), maps the runner's
findings JSON into the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}, and
exits 0 whether pass or fail. If uv or the runner cannot run, it reports status "fail"
(fail-closed).

The ~7 seeded open findings are the baseline product, not failures. Per the binder's
baseline-regression rule the adapter reports status "fail" only on regression against the
last committed results file — a NEW open finding on the INTERSECTION of probe ids with
that baseline (the card's intersection rule keeps the headline comparable as probes are
added). Per the binder's baseline-selection rule the regression baseline is the newest
git-tracked results file for this vector (git ls-files), never an untracked or same-run
file; during a gate run the adapter writes the runner's --out to a temp path and never
rewrites the committed baseline (that <version>.json is written deliberately at release
time by the builder). Adapter metrics carry {open_total, open_on_intersection,
probe_set_hash}. On the first baseline (no tracked results file) status is pass, and the
probe fails closed if any seeded finding is absent from that first baseline.

  flow-spec-contradictions.py --target <repo-root>   # gate mode (run_gate passes this)
  flow-spec-contradictions.py --self-test            # embedded fixtures

The self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary, and exits 0
only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse, importlib, json, subprocess, sys, tempfile
from pathlib import Path

# Shared-term invariants this probe is enrolled in, rendered verbatim (byte-identical to
# the binder's declared canonicals; check_shared_terms.py enforces this at merge time):
#   probe JSON contract: {"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
#   self-test report format: [PASS]/[FAIL] lines and an N/N checks passed summary
#   baseline-regression rule: status "fail" only on regression against the last committed results file
#   baseline-selection rule: the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file

VECTOR = "flow-spec-contradictions"
RESULTS_DIR = "benchmarks/results/flow-spec-contradictions"
RUNNER = "benchmarks/flow/check_contradictions.py"
IMPLEMENTED_CHECKS = ["pass1-ref-vocabulary", "pass2-hooks-events",
                      "pass3-promise-probes", "pass4-schema-validator-duals"]

# The findings the first committed baseline must contain (the card's 2026-07-17 seed
# observation). The probe fails closed when any is absent from its own first baseline.
SEEDED_FINDINGS = (
    "p1:dead-vocab:in-progress",
    "p1:written-unread:accepted",
    "p3:promise:between-waves-edit",
    "p3:promise:missing-repair-path",
    "p4:dual-disagree:shared-terms",
    "p4:promise-uncaught:ui-fields-non-ui",
    "p4:orphan-schema:doc-gardner",
)


# --- runner invocation ---------------------------------------------------------

def _run_runner(target: Path) -> tuple[dict | None, str]:
    """Run the uv/jsonschema runner into a temp file; return (evidence, error)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "findings.json"
        cmd = ["uv", "run", "--with", "jsonschema", str(target / RUNNER),
               "--repo", str(target), "--out", str(out)]
        try:
            proc = subprocess.run(cmd, cwd=str(target), capture_output=True,
                                  text=True, timeout=110)
        except FileNotFoundError:
            return None, "uv not found on PATH (host toolchain missing)"
        except subprocess.TimeoutExpired:
            return None, "runner timed out"
        if proc.returncode != 0:
            tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
            return None, f"runner exit {proc.returncode}: {tail}"
        try:
            return json.loads(out.read_text()), ""
        except (OSError, json.JSONDecodeError) as e:
            return None, f"runner produced no readable findings JSON ({e})"


# --- baseline / regression -----------------------------------------------------

def _find_baseline(target: Path) -> tuple[str | None, dict | None]:
    """Newest git-tracked results file for this vector. Returns (relpath, parsed) —
    (relpath, None) means tracked but unreadable, which the caller fails closed on."""
    proc = subprocess.run(["git", "-C", str(target), "ls-files", RESULTS_DIR + "/"],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None, None
    names = [ln for ln in proc.stdout.splitlines() if ln.endswith(".json")]
    if not names:
        return None, None
    newest = sorted(names)[-1]
    try:
        return newest, json.loads((target / newest).read_text())
    except (OSError, json.JSONDecodeError):
        return newest, None


def _regressions(current: dict, baseline: dict) -> list[dict]:
    """New open findings whose probe id is in the intersection of probe sets."""
    base_ids = {f["finding_id"] for f in baseline.get("findings", [])}
    intersection = set(current.get("probe_set", [])) & set(baseline.get("probe_set", []))
    return [f for f in current.get("findings", [])
            if f["finding_id"] not in base_ids and f.get("probe_id") in intersection]


def _open_on_intersection(current: dict, baseline: dict | None) -> int:
    if baseline is None:
        return len(current.get("findings", []))
    intersection = set(current.get("probe_set", [])) & set(baseline.get("probe_set", []))
    return sum(1 for f in current.get("findings", [])
               if f.get("probe_id") in intersection)


def assemble(current: dict, baseline_name: str | None,
             baseline: dict | None) -> dict:
    """Map a runner-evidence dict into the gate probe JSON contract."""
    findings = [{"finding_id": f["finding_id"], "severity": f.get("severity", "?"),
                 "summary": f.get("summary", "")} for f in current.get("findings", [])]
    if baseline_name and baseline is None:
        findings.append({"finding_id": "baseline-unreadable", "severity": "high",
                         "summary": f"tracked baseline {baseline_name} is not readable "
                                    "JSON — cannot prove no regression"})
        status = "fail"
    elif baseline is not None:
        regs = _regressions(current, baseline)
        findings += [{"finding_id": f"regression:{f['finding_id']}", "severity": "high",
                      "summary": f"NEW open contradiction on the probe-id intersection "
                                 f"with {baseline_name}: {f.get('summary', '')}"}
                     for f in regs]
        status = "fail" if regs else "pass"
    else:
        present = {f["finding_id"] for f in current.get("findings", [])}
        missing = [s for s in SEEDED_FINDINGS if s not in present]
        findings += [{"finding_id": f"seed-missing:{s}", "severity": "high",
                      "summary": f"seeded finding {s} absent from the first baseline "
                                 "(fail-closed)"} for s in missing]
        status = "fail" if missing else "pass"
    metrics = {"open_total": len(current.get("findings", [])),
               "open_on_intersection": _open_on_intersection(current, baseline),
               "probe_set_hash": current.get("probe_set_hash", ""),
               "unknown_events": len(current.get("unknown_events", [])),
               "baseline": baseline_name or "none"}
    return {"id": VECTOR, "status": status, "partial": False,
            "implemented_checks": IMPLEMENTED_CHECKS,
            "findings": findings, "metrics": metrics}


def _fail_closed(detail: str) -> dict:
    return {"id": VECTOR, "status": "fail", "partial": False,
            "implemented_checks": IMPLEMENTED_CHECKS,
            "findings": [{"finding_id": "runner-unavailable", "severity": "high",
                          "summary": f"cannot run the contradiction runner: {detail} — "
                                     "fail-closed"}],
            "metrics": {"open_total": 0, "open_on_intersection": 0,
                        "probe_set_hash": "", "unknown_events": 0, "baseline": "none"}}


def gate_mode(target: Path) -> int:
    current, err = _run_runner(target)
    if current is None:
        print(json.dumps(_fail_closed(err)))
        return 0
    baseline_name, baseline = _find_baseline(target)
    print(json.dumps(assemble(current, baseline_name, baseline)))
    return 0


# --- self-test -----------------------------------------------------------------

def _load_runner(target: Path):
    sys.path.insert(0, str(target / "benchmarks" / "flow"))
    return importlib.import_module("check_contradictions")


def run_self_test(target: Path) -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        suffix = f" ({detail})" if detail and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        results.append(ok)

    cc = _load_runner(target)
    whitelist = json.loads((target / "benchmarks/flow/claude-code-events.json").read_text())

    # Pass 2 — MISSPELLING and UNKNOWN-EVENT on fixture hooks.json objects.
    typo = {"hooks": {"PreTooluse": [{"matcher": "Write", "hooks": []}]}}
    f_typo, u_typo = cc.check_hooks_events(typo, whitelist)
    check("pass 2: a typo'd event name yields a MISSPELLING finding",
          any(f["finding_id"].startswith("p2:misspelling:event:PreTooluse")
              for f in f_typo) and not u_typo)

    unknown = {"hooks": {"TotallyFakeEvent": [{"matcher": "Write", "hooks": []}]}}
    f_unk, u_unk = cc.check_hooks_events(unknown, whitelist)
    check("pass 2: an unrecognized name yields UNKNOWN-EVENT distinct from findings",
          not f_unk and any(u["name"] == "TotallyFakeEvent" for u in u_unk))

    clean = json.loads((target / "hooks/hooks.json").read_text())
    f_clean, u_clean = cc.check_hooks_events(clean, whitelist)
    check("pass 2: the live hooks.json is clean (zero findings, zero unknown)",
          not f_clean and not u_clean)

    # Pass 3 — ANCHOR-LOST when a promise anchor regex no longer matches its file.
    lost = {"id": "AX", "check": "guard-deny",
            "anchor_file": "hooks/scripts/guard_binder_immutability.py",
            "anchor_regex": "zzz-this-anchor-never-matches-zzz",
            "guard": "hooks/scripts/guard_binder_immutability.py",
            "payload_fixture": "benchmarks/fixtures/hooked-repo/payloads/binder-write-committed.json",
            "expected_exit": 2, "summary": ""}
    res_lost = cc.check_promise(target, lost, None)
    check("pass 3: a stale promise anchor emits ANCHOR-LOST as its own finding",
          res_lost["state"] == "ANCHOR-LOST" and res_lost["finding"] is not None)

    # Pass 3 A1 — consumes the hooked-repo payload fixtures and asserts the deny exit.
    promises = json.loads((target / "benchmarks/flow/promises.json").read_text())
    a1 = next(p for p in promises["promises"] if p["id"] == "A1")
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "hooked-repo"
        cc.build_hooked_fixture(target, fixture)
        payload = (target / a1["payload_fixture"]).read_text()
        deny_exit = cc._run_guard_stdin(target, a1["guard"], payload, fixture)
        res_a1 = cc.check_promise(target, a1, fixture)
    check("pass 3 A1: the immutability guard denies the committed-binder payload (exit 2)",
          deny_exit == 2 and res_a1["state"] == "CONSISTENT")

    # Pass 3 A2/A3 — the live seeded promise contradictions reproduce.
    a2 = cc.check_promise(target, next(p for p in promises["promises"] if p["id"] == "A2"), None)
    a3 = cc.check_promise(target, next(p for p in promises["promises"] if p["id"] == "A3"), None)
    check("pass 3: A2 between-waves-edit and A3 missing-repair-path are open",
          a2["state"] == "OPEN" and a2["finding"]["finding_id"] == "p3:promise:between-waves-edit"
          and a3["state"] == "OPEN" and a3["finding"]["finding_id"] == "p3:promise:missing-repair-path")

    # Pass 1 — the live seeded ref-vocabulary contradictions reproduce.
    p1 = {f["finding_id"] for f in cc.pass1(target)}
    check("pass 1: dead `in-progress` vocab and `accepted`-missing-from-REF_STATES reproduce",
          {"p1:dead-vocab:in-progress", "p1:written-unread:accepted"} <= p1)

    # Adapter baseline regression — identical stays pass, a synthetic new finding fails.
    synthetic = {"probe_set": ["pass1-ref-vocabulary", "A2", "A3", "p4:shared-terms",
                               "p4:ui-fields", "p4:doc-gardner"],
                 "probe_set_hash": "x",
                 "findings": [{"finding_id": s, "probe_id": "pass1-ref-vocabulary"}
                              for s in SEEDED_FINDINGS]}
    same = assemble(synthetic, "prior.json", synthetic)
    check("adapter: an identical baseline stays pass", same["status"] == "pass")

    worse = json.loads(json.dumps(synthetic))
    worse["findings"].append({"finding_id": "p1:dead-vocab:brand-new",
                              "probe_id": "pass1-ref-vocabulary"})
    reg = assemble(worse, "prior.json", synthetic)
    check("adapter: a new open finding on the intersection flips status to fail",
          reg["status"] == "fail"
          and any(f["finding_id"].startswith("regression:") for f in reg["findings"]))

    # First-baseline fail-closed — a missing seeded finding fails the first baseline.
    short = json.loads(json.dumps(synthetic))
    short["findings"] = [f for f in short["findings"]
                         if f["finding_id"] != "p4:orphan-schema:doc-gardner"]
    first = assemble(short, None, None)
    check("adapter: a missing seeded finding fails the first baseline closed",
          first["status"] == "fail")

    # First-baseline with every seed present passes.
    full_first = assemble(synthetic, None, None)
    check("adapter: the first baseline with every seeded finding present passes",
          full_first["status"] == "pass")

    # Gate contract shape (partial false, required keys, metrics vector).
    payload = full_first
    shape_ok = (payload.get("id") == VECTOR and payload.get("status") in ("pass", "fail")
                and payload.get("partial") is False
                and isinstance(payload.get("implemented_checks"), list)
                and isinstance(payload.get("findings"), list)
                and {"open_total", "open_on_intersection", "probe_set_hash"}
                <= set(payload.get("metrics", {})))
    check("gate payload satisfies the probe JSON contract (partial false, full metrics)",
          shape_ok)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="flow-spec-contradictions gate adapter")
    ap.add_argument("--target", default=str(Path(__file__).resolve().parents[2]),
                    help="repo root to probe (run_gate passes this)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    target = Path(args.target).resolve()
    if args.self_test:
        return run_self_test(target)
    return gate_mode(target)


if __name__ == "__main__":
    sys.exit(main())
