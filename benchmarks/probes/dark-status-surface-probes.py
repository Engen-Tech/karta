#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for dark-status-surface-probes: do the status surfaces tell the
truth when the git state goes wrong?

Implements benchmarks/dark/dark-status-surface-probes.md in full (partial:
false): Family S (8 stranded states), Family F (4 ref forgeries), Family P (2
provenance binaries), the gate/engine ref-set agreement assertion, and the
scripted-reachability static binary. Every grading invocation fabricates every
case fresh in a mktemp scratch repo via
benchmarks/fixtures/stranded-states/make_state.sh and grades it strictly
against the committed anchors in benchmarks/fixtures/stranded-states/
expected.json — no free-form judgment. A fabricated scratch repo is never
reused across two guard_delivery_stop.py invocations (its once-per-
(session,state) sentinel in the git common dir would silence the second Stop);
the synthetic Stop payload pins a fixed session_id and the fixture repo cwd —
freshness of the repo, not the id, guarantees sentinel reset. A serve_status
import failure grades FAIL-LOUD (a probe error, never a silent skip —
fail-closed).

Stdout is the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
with the per-case stable-ID matrix in metrics; exit 0 whether pass or fail (a
nonzero exit means the probe itself crashed). Verdict rule:
status "fail" only on regression against the last committed results file
(any case ID flipping toward worse, diffed by ID, never by bare fraction);
the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file
(the tracked baseline is read from the git index, so this run's own write can
never be its own baseline). On the first run (no
tracked baseline yet) the probe fails closed unless its own matrix reproduces
the contract's seeded findings: the S1 dead-end with empty warnings, the S5
vanishing corrupt binder, 0/4 forgeries detected with the forged done ref
silencing the Stop-gate, P1 accepted-done badged PASSED, and the gate/engine
ref-set disagreement.

On every run the full matrix is written to
benchmarks/dark/results/<run-date>-status-truth.json (overwriting a same-date
file). That path deliberately follows the card frontmatter (results:
benchmarks/dark/results/) and the README family-local-evidence rule, not card
step 8's benchmarks/results/<version>-status-truth.json — recorded as a
standing finding here and as row 8 of benchmarks/meta/card-errata-2026-07-17.md.

  python3 benchmarks/probes/dark-status-surface-probes.py --target <repo-root>
  python3 benchmarks/probes/dark-status-surface-probes.py --self-test

--self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary, and
exits 0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse
import datetime
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_ID = "dark-status-surface-probes"
S_CASES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8")
F_CASES = ("F1", "F2", "F3", "F4")
P_CASES = ("P1", "P2")
ALL_CASES = S_CASES + F_CASES + P_CASES
REFSET_ID = "gate_engine_ref_agreement"
REACH_ID = "scripted-reachability-check-exists"
SESSION_ID = "karta-bench-status-truth"  # fixed on purpose: repo freshness, not the id, resets the sentinel
FIXTURES = Path("benchmarks/fixtures/stranded-states")
RESULTS_DIR = Path("benchmarks/dark/results")
RESULTS_RE = re.compile(r"benchmarks/dark/results/\d{4}-\d{2}-\d{2}-status-truth\.json")
SUB_TIMEOUT_S = 30


class ProbeError(RuntimeError):
    """The probe itself cannot grade — fail loud (nonzero exit), never a silent skip."""


def _run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                              input=stdin, timeout=SUB_TIMEOUT_S, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"subprocess did not complete: {cmd[:2]}... ({e})") from e


def _load_anchors(target: Path) -> dict:
    path = target / FIXTURES / "expected.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ProbeError(f"cannot read grading anchors {path}: {e}") from e
    cases = doc.get("cases")
    if not isinstance(cases, dict) or sorted(cases) != sorted(ALL_CASES):
        raise ProbeError(f"{path} must anchor exactly the cases {ALL_CASES}")
    return doc


def _fabricate(target: Path, case: str) -> tuple[Path, Path]:
    """Fresh scratch repo for one case. Returns (scratch_root, repo)."""
    scratch = Path(tempfile.mkdtemp(prefix=f"karta-bench-{case}-"))
    repo = scratch / "repo"
    p = _run(["bash", str(target / FIXTURES / "make_state.sh"), case, str(repo)])
    if p.returncode != 0:
        shutil.rmtree(scratch, ignore_errors=True)
        raise ProbeError(f"make_state.sh {case} failed (exit {p.returncode}): "
                         f"{(p.stderr or p.stdout).strip()[-300:]}")
    return scratch, repo


def _karta_next(target: Path, repo: Path) -> tuple[dict | None, bool]:
    """(state, crashed) from karta_next --json run inside the fixture repo."""
    p = _run([sys.executable,
              str(target / "skills/karta-status/scripts/karta_next.py"), "--json"],
             cwd=repo)
    if p.returncode != 0:
        return None, True
    try:
        return json.loads(p.stdout), False
    except json.JSONDecodeError:
        return None, True


def _stop_gate_exit(target: Path, repo: Path) -> int:
    payload = json.dumps({"hook_event_name": "Stop", "session_id": SESSION_ID,
                          "cwd": str(repo), "stop_hook_active": False})
    p = _run([sys.executable,
              str(target / "hooks/scripts/guard_delivery_stop.py")],
             cwd=repo, stdin=payload)
    return p.returncode


def _state_meta(target: Path) -> dict:
    """The pinned serve_status._STATE_META import probe. Import failure is FAIL-LOUD."""
    p = _run([sys.executable, "-c",
              "import json, serve_status; print(json.dumps(serve_status._STATE_META))"],
             cwd=target / "skills/karta-status/scripts")
    if p.returncode != 0:
        raise ProbeError("serve_status import probe failed — grading FAIL-LOUD, "
                         f"never a silent skip: {p.stderr.strip()[-300:]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"serve_status import probe emitted bad JSON: {e}") from e


def _item_state_key(state: dict | None, item_id: str) -> str | None:
    if not state:
        return None
    for binder in state.get("binders", []):
        if binder.get("slug") == "wip":
            for d in binder.get("items", {}).get("detail", []):
                if d.get("id") == item_id:
                    return d.get("status")
    return None


def _provenance_reconstructible(repo: Path) -> bool:
    """P2: waiver reconstructible from refs + merge trailers alone."""
    refs = _run(["git", "for-each-ref", "--format=%(refname) %(objectname)",
                 "refs/karta/wip/"], cwd=repo).stdout.splitlines()
    by_name = dict(line.split(" ", 1) for line in refs if " " in line)
    accepted = by_name.get("refs/karta/wip/item-a/accepted")
    done = by_name.get("refs/karta/wip/item-a/done")
    if not accepted or not done:
        return False
    trailers = _run(["git", "log", "-1", "--format=%(trailers)", done],
                    cwd=repo).stdout
    waived = re.search(r"^Karta-Accepted:\s*\S+", trailers, re.M)
    why = re.search(r"^Karta-Accept-Reason:\s*\S+", trailers, re.M)
    return bool(waived and why)


def grade_case(target: Path, case: str, anchor: dict) -> dict:
    """Fabricate the case fresh and grade it strictly against its committed anchor."""
    scratch, repo = _fabricate(target, case)
    try:
        state, crashed = _karta_next(target, repo)
        na = (state or {}).get("next_action") or {}
        command_nonnull = na.get("command") is not None
        warnings = (state or {}).get("warnings") or []
        rx = re.compile(anchor["warning_regex"], re.IGNORECASE)
        warning_matched = any(rx.search(w) for w in warnings if isinstance(w, str))
        stop_exit = _stop_gate_exit(target, repo)

        badge_word = None
        anchor_word_ok = True
        pin = anchor.get("serve_status")
        if pin:
            meta = _state_meta(target)
            anchor_word_ok = (meta.get(pin["symbol"], {}).get("word") == pin["word"])
            if case == "P1":
                key = _item_state_key(state, "a")
                badge_word = meta.get(key, {}).get("word") if key else None

        cell: dict = {
            "engine_crash": crashed,
            "command_nonnull": command_nonnull,
            "warnings_count": len(warnings),
            "warning_matched": warning_matched,
            "stop_gate_exit": stop_exit,
        }
        if case in S_CASES:
            cell["actionable"] = command_nonnull or warning_matched
            cell["good"] = cell["actionable"]
        elif case in F_CASES:
            cell["detected"] = warning_matched or stop_exit == 2
            cell["good"] = cell["detected"]
        elif case == "P1":
            key = _item_state_key(state, "a")
            cell["state_key"] = key
            cell["badge_word"] = badge_word
            distinct = key is not None and key != "done"
            cell["good"] = distinct and badge_word is not None and badge_word != "PASSED"
        else:  # P2
            cell["reconstructible"] = _provenance_reconstructible(repo)
            cell["good"] = cell["reconstructible"]

        cell["anchor_match"] = (
            command_nonnull == anchor["expect_command_nonnull"]
            and stop_exit == anchor["stop_gate_exit"]
            and anchor_word_ok)
        return cell
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _refset_cell(target: Path) -> dict:
    guard_src = (target / "hooks/scripts/guard_delivery_stop.py").read_text()
    m = re.search(r"REF_STATES\s*=\s*\(([^)]*)\)", guard_src)
    if not m:
        raise ProbeError("cannot parse REF_STATES from guard_delivery_stop.py")
    gate_set = set(re.findall(r"\"([a-z-]+)\"|'([a-z-]+)'", m.group(1)))
    gate_states = {a or b for a, b in gate_set}
    doctrine = (target / "skills/_shared/integration-branch.md").read_text()
    engine_states = set(re.findall(r"refs/karta/<slug>/item-<id>/([a-z][a-z-]*)", doctrine))
    if not gate_states or not engine_states:
        raise ProbeError("empty ref-state set parsed — refusing to grade agreement")
    return {"good": gate_states == engine_states,
            "gate_ref_states": sorted(gate_states),
            "engine_ref_states": sorted(engine_states),
            "missing_from_gate": sorted(engine_states - gate_states),
            "extra_in_gate": sorted(gate_states - engine_states)}


def _reachability_cell(target: Path) -> dict:
    """Static binary: grep -rEn 'merge-base|--first-parent|rev-list' skills/karta-deliver/ hooks/scripts/"""
    rx = re.compile(r"merge-base|--first-parent|rev-list")
    hits: list[str] = []
    for root in (target / "skills/karta-deliver", target / "hooks/scripts"):
        for f in sorted(root.rglob("*")) if root.is_dir() else []:
            if f.is_file():
                try:
                    if rx.search(f.read_text(errors="replace")):
                        hits.append(str(f.relative_to(target)))
                except OSError:
                    continue
    return {"good": bool(hits), "exists": bool(hits), "hits": hits}


def build_matrix(target: Path, anchors: dict) -> dict:
    matrix = {case: grade_case(target, case, anchors["cases"][case])
              for case in ALL_CASES}
    matrix[REFSET_ID] = _refset_cell(target)
    matrix[REACH_ID] = _reachability_cell(target)
    return matrix


def seeded_findings(matrix: dict) -> tuple[list[dict], list[str]]:
    """The contract's named seeded reds observed in this matrix, plus the ids of
    any named seed that is absent (the first-run fail-closed trigger)."""
    findings: list[dict] = []
    missing: list[str] = []

    s1 = matrix["S1"]
    if (not s1["good"]) and (not s1["command_nonnull"]) and s1["warnings_count"] == 0:
        findings.append({"finding_id": "seed-S1-dead-end", "severity": "warn",
                         "summary": "S1 built-unmerged mid-wave: karta_next renders the "
                                    "dead-end fallback (null command, empty warnings) in the "
                                    "precise state the Stop-gate exists to catch"})
    else:
        missing.append("seed-S1-dead-end")

    s5 = matrix["S5"]
    if s5["engine_crash"] and not s5["good"]:
        findings.append({"finding_id": "seed-S5-corrupt-binder-vanishes", "severity": "warn",
                         "summary": "S5 corrupt/non-dict binder: karta_next crashes instead of "
                                    "naming the corrupt binder — the state vanishes from status"})
    else:
        missing.append("seed-S5-corrupt-binder-vanishes")

    detected = sum(1 for c in F_CASES if matrix[c]["good"])
    if detected == 0 and matrix["F1"]["stop_gate_exit"] == 0:
        findings.append({"finding_id": "seed-F-forgeries-undetected", "severity": "warn",
                         "summary": "0/4 forgeries detected: the forged done ref actively "
                                    "silences the Stop-gate's built-without-done check "
                                    "(F1 stop_gate_exit 0) and no surface warns"})
    else:
        missing.append("seed-F-forgeries-undetected")

    p1 = matrix["P1"]
    if (not p1["good"]) and p1.get("badge_word") == "PASSED":
        findings.append({"finding_id": "seed-P1-accepted-badged-PASSED", "severity": "warn",
                         "summary": "P1 accepted-done: karta_next surfaces plain 'done' and "
                                    "serve_status._STATE_META badges it PASSED — the waiver is "
                                    "invisible on the status surfaces"})
    else:
        missing.append("seed-P1-accepted-badged-PASSED")

    refset = matrix[REFSET_ID]
    if not refset["good"]:
        findings.append({"finding_id": "seed-refset-disagreement", "severity": "warn",
                         "summary": "guard_delivery_stop.py REF_STATES disagrees with the ref "
                                    "names the engine writes per integration-branch.md "
                                    f"(missing from gate: {', '.join(refset['missing_from_gate'])})"})
    else:
        missing.append("seed-refset-disagreement")

    findings.append({"finding_id": "card-step8-results-path-errata", "severity": "info",
                     "summary": "results are written to benchmarks/dark/results/<date>-status-truth.json "
                                "per the card frontmatter and the README family-local-evidence rule, "
                                "deviating from card step 8's benchmarks/results/<version>-status-truth.json "
                                "— card text left untouched; recorded as row 8 of "
                                "benchmarks/meta/card-errata-2026-07-17.md"})
    return findings, missing


def anchor_mismatch_findings(matrix: dict) -> list[dict]:
    out = []
    for case in ALL_CASES:
        if not matrix[case]["anchor_match"]:
            out.append({"finding_id": f"anchor-mismatch-{case}", "severity": "error",
                        "summary": f"{case}: observed behavior deviates from the committed "
                                   "expected.json anchor — a surface changed without its anchor "
                                   "edit (anchors may only change in the same commit as the "
                                   "surface change they track)"})
    return out


def compare_matrices(baseline_matrix: dict, current_matrix: dict) -> list[str]:
    """Diff-by-ID, toward-worse only: a case ID whose baseline good=true is now
    false or missing is a regression. New IDs never dilute or inflate anything."""
    regressions = []
    for case_id, base_cell in sorted(baseline_matrix.items()):
        if not isinstance(base_cell, dict) or not base_cell.get("good"):
            continue
        cur = current_matrix.get(case_id)
        if not isinstance(cur, dict) or not cur.get("good"):
            regressions.append(case_id)
    return regressions


def find_baseline(target: Path) -> tuple[str | None, dict | None]:
    """Newest git-tracked results file for this vector, read from the git index —
    never an untracked file, never this run's own working-tree write."""
    ls = _run(["git", "-C", str(target), "ls-files", "--",
               str(RESULTS_DIR) + "/"]).stdout.split()
    tracked = sorted(p for p in ls if RESULTS_RE.fullmatch(p))
    if not tracked:
        return None, None
    path = tracked[-1]
    blob = _run(["git", "-C", str(target), "show", f":{path}"])
    if blob.returncode != 0:
        raise ProbeError(f"tracked baseline {path} unreadable from the git index")
    try:
        doc = json.loads(blob.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"tracked baseline {path} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("case_matrix"), dict):
        raise ProbeError(f"tracked baseline {path} lacks a case_matrix object")
    return path, doc


def decide_status(matrix: dict, baseline_doc: dict | None,
                  seed_missing: list[str]) -> tuple[str, list[str]]:
    """(status, regressions). With a committed baseline: fail only on regression.
    Without one (first run): fail closed unless every named seeded finding is
    present in the probe's own first baseline."""
    if baseline_doc is not None:
        regressions = compare_matrices(baseline_doc["case_matrix"], matrix)
        return ("fail" if regressions else "pass"), regressions
    return ("fail" if seed_missing else "pass"), []


def results_payload(matrix: dict, findings: list[dict], anchors: dict,
                    baseline_doc: dict | None, run_date: str) -> dict:
    anchor_diff: list[str] = []
    if baseline_doc is not None and baseline_doc.get("anchors") != anchors:
        anchor_diff = list(difflib.unified_diff(
            json.dumps(baseline_doc.get("anchors"), indent=2, sort_keys=True).splitlines(),
            json.dumps(anchors, indent=2, sort_keys=True).splitlines(),
            fromfile="anchors@baseline", tofile="anchors@run", lineterm=""))
    return {"schema_version": 1, "vector": PROBE_ID, "run_date": run_date,
            "case_matrix": matrix, "findings": findings,
            "anchors": anchors, "anchor_diff": anchor_diff}


def run_probe(target: Path) -> int:
    anchors_doc = _load_anchors(target)
    anchors = anchors_doc["cases"]
    matrix = build_matrix(target, anchors_doc)
    run_findings, seed_missing = seeded_findings(matrix)
    run_findings += anchor_mismatch_findings(matrix)
    baseline_path, baseline_doc = find_baseline(target)
    status, regressions = decide_status(matrix, baseline_doc, seed_missing)

    run_date = datetime.date.today().isoformat()
    results_dir = target / RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / f"{run_date}-status-truth.json"
    results_file.write_text(json.dumps(
        results_payload(matrix, run_findings, anchors, baseline_doc, run_date),
        indent=2, sort_keys=True) + "\n")

    findings = list(run_findings)
    findings += [{"finding_id": f"regression-{c}", "severity": "error",
                  "summary": f"case {c} regressed toward worse versus the committed "
                             f"baseline {baseline_path}"} for c in regressions]
    findings += [{"finding_id": f"seed-missing-{sid}", "severity": "error",
                  "summary": f"first-run baseline lacks contract-named seeded finding "
                             f"{sid} — failing closed (fixture factory or surface drift)"}
                 for sid in seed_missing] if baseline_doc is None else []

    print(json.dumps({
        "id": PROBE_ID,
        "status": status,
        "partial": False,
        "implemented_checks": [
            "family-S-stranded-actionability(8)",
            "family-F-forgery-detection(4)",
            "family-P-provenance(2)",
            "gate-engine-ref-set-agreement",
            "scripted-reachability-static-binary",
            "anchor-conformance",
            "baseline-regression-diff-by-id",
        ],
        "findings": findings,
        "metrics": {
            "matrix": matrix,
            "s_actionable": f"{sum(1 for c in S_CASES if matrix[c]['good'])}/8",
            "f_detected": f"{sum(1 for c in F_CASES if matrix[c]['good'])}/4",
            "baseline_file": baseline_path,
            "regressions": regressions,
            "results_file": str(RESULTS_DIR / f"{run_date}-status-truth.json"),
        },
    }, indent=2))
    return 0


def _run_self_test() -> int:
    target = Path(__file__).resolve().parent.parent.parent
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    anchors_ok = True
    try:
        doc = _load_anchors(target)
        for case, a in doc["cases"].items():
            re.compile(a["warning_regex"])
            assert isinstance(a["expect_command_nonnull"], bool)
            assert a["stop_gate_exit"] in (0, 2)
    except (ProbeError, KeyError, AssertionError, re.error):
        anchors_ok = False
    check("expected.json anchors all 14 cases with valid fields", anchors_ok)

    try:
        s1, r1 = _fabricate(target, "S1")
        h1 = _run(["git", "rev-parse", "HEAD"], cwd=r1).stdout.strip()
        shutil.rmtree(s1, ignore_errors=True)
        s2, r2 = _fabricate(target, "S1")
        h2 = _run(["git", "rev-parse", "HEAD"], cwd=r2).stdout.strip()
        shutil.rmtree(s2, ignore_errors=True)
        check("make_state.sh fabricates S1 with byte-stable shas", bool(h1) and h1 == h2)
    except ProbeError as e:
        check(f"make_state.sh fabricates S1 with byte-stable shas ({e})", False)

    try:
        cell = grade_case(target, "S1", doc["cases"]["S1"])
        check("S1 grades not-actionable with Stop-gate exit 2 and anchor match",
              cell["good"] is False and cell["stop_gate_exit"] == 2
              and cell["anchor_match"] is True and cell["warnings_count"] == 0)
    except ProbeError as e:
        check(f"S1 grades not-actionable with Stop-gate exit 2 and anchor match ({e})", False)

    try:
        cell = grade_case(target, "P2", doc["cases"]["P2"])
        check("P2 accepted-done waiver reconstructible from refs + trailers",
              cell["good"] is True and cell["anchor_match"] is True)
    except ProbeError as e:
        check(f"P2 accepted-done waiver reconstructible from refs + trailers ({e})", False)

    base = {c: {"good": g} for c, g in
            [("S1", False), ("S3", True), ("S4", True), ("F1", False),
             ("P2", True), (REFSET_ID, False)]}
    same = {c: dict(cell) for c, cell in base.items()}
    check("identical matrix vs baseline stays pass",
          compare_matrices(base, same) == []
          and decide_status(same, {"case_matrix": base}, [])[0] == "pass")

    worse = {c: dict(cell) for c, cell in base.items()}
    worse["S3"]["good"] = False  # one case ID flipped toward worse
    st, regs = decide_status(worse, {"case_matrix": base}, [])
    check("a case ID flipped toward worse flips status to fail",
          st == "fail" and regs == ["S3"])

    gone = {c: dict(cell) for c, cell in base.items() if c != "P2"}
    check("a case ID missing from the current matrix counts as a regression",
          compare_matrices(base, gone) == ["P2"])

    grown = {c: dict(cell) for c, cell in base.items()}
    grown["S9"] = {"good": False}  # new IDs get new IDs; they never dilute the diff
    check("a new case ID alone is never a regression",
          compare_matrices(base, grown) == [])

    try:
        refset = _refset_cell(target)
        check("REF_STATES and the engine ref-set both parse (done present in each)",
              "done" in refset["gate_ref_states"] and "done" in refset["engine_ref_states"])
    except ProbeError as e:
        check(f"REF_STATES and the engine ref-set both parse ({e})", False)

    st, _ = decide_status(worse, None, ["seed-S1-dead-end"])
    check("first run fails closed when a contract-named seeded finding is absent",
          st == "fail")

    total, failures = len(results), results.count(False)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root to measure (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded checks; exit 0 only on N/N checks passed")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        return run_probe(args.target.resolve())
    except ProbeError as e:
        print(f"{PROBE_ID}: PROBE ERROR — {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
