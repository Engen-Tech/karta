#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe: guard behavior and cross-runtime enforcement matrix (Families A+B).

Implements benchmarks/flow/flow-guard-enforcement-matrix.md Families A and B;
Family C (quarterly live cells) is phase 3, so the probe declares partial: true.

Family A fabricates a scratch git repo (benchmarks/fixtures/hooked-repo/
build_fixture.sh) and runs every hooks/scripts/guard_*.py as a subprocess with
cwd = the fixture repo, feeding the committed stdin payloads from
benchmarks/fixtures/hooked-repo/payloads/ (any "cwd" value of "__FIXTURE__" is
replaced with the fixture path at run time). Each row records {expected, actual},
including the pinned known-gap rows (staged-not-committed passes; symlink alias
passes; NotebookEdit-on-pack unmatched) and the fail-open (binder immutability,
Stop-gate) vs fail-closed (auditor dispatch, writer confinement) split. The probe
never mutates the real repo: all guard invocations run inside the scratch fixture.

Family B parses hooks/hooks.json matchers against the committed
benchmarks/flow/mutation-surface.json manifest. A row with no matcher and no
waiver id is an unwaivered BYPASS finding; a guard_*.py with no manifest row
always flips status to "fail" (anti-staleness, fail-closed).

Known open rows are findings, not failures: the probe reports
status "fail" only on regression against the last committed results file
(a changed Family A row, an enforced->bypassed flip, or a new unwaivered
bypass); with no prior file it records the baseline and passes — and fails
closed if a seeded finding is absent from its own first baseline. Per the
binder's baseline-selection rule,
the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file.
Every run writes its dated evidence JSON to
benchmarks/flow/results/<run-date>-flow-guard-enforcement-matrix.json
(overwriting a same-date file).

On stdout the probe emits the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
and exits 0 whether pass or fail (a nonzero exit means the probe itself crashed).

  flow-guard-enforcement-matrix.py --target <repo-root>   # gate mode (run_gate)
  flow-guard-enforcement-matrix.py --self-test            # embedded fixtures

The self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary, and
exits 0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse, datetime, json, os, subprocess, sys, tempfile
from pathlib import Path

VECTOR = "flow-guard-enforcement-matrix"
RESULTS_DIR = Path("benchmarks") / "flow" / "results"
FIXTURE_DIR = Path("benchmarks") / "fixtures" / "hooked-repo"
GUARD_TIMEOUT_S = 30
HOOK_CHANNELS = {"write", "edit", "notebookedit", "task"}
IMPLEMENTED_CHECKS = ["family-a-guard-payload-matrix",
                      "family-b-mutation-surface-cross-check"]

# Family A: (probe_id, guard script, payload file, expected exit, gap-finding id).
# A gap id pins a known enforcement hole: the row is expected to PASS the guard
# (exit 0) even though the mutation is the guarded kind. Rows run in order — the
# two stop rows share a payload and session id deliberately (block-once probe).
FAMILY_A = (
    ("binder-guarded-write", "guard_binder_immutability.py",
     "binder-write-committed.json", 2, None,
     "a Write to a HEAD-committed binder is denied"),
    ("binder-benign-write", "guard_binder_immutability.py",
     "binder-write-benign.json", 0, None,
     "a Write to a non-binder file passes"),
    ("binder-archived-write", "guard_binder_immutability.py",
     "binder-write-archived.json", 2, None,
     "a Write to a committed archived binder is denied"),
    ("binder-symlink-alias", "guard_binder_immutability.py",
     "binder-write-symlink-alias.json", 0, "symlink-alias",
     "a write via a symlink alias dir escapes BINDER_RE — the path is matched as "
     "the tool call names it, before any resolution"),
    ("binder-staged-not-committed", "guard_binder_immutability.py",
     "binder-write-staged-only.json", 0, "staged-not-committed",
     "a binder staged but not committed passes the HEAD-only check (ls-tree HEAD "
     "misses the index)"),
    ("binder-malformed-json", "guard_binder_immutability.py",
     "malformed.json", 0, None,
     "malformed stdin fails open (exit 0) — binder guard must never break a call"),
    ("pack-invalid-write", "guard_pack_write.py",
     "pack-write-invalid.json", 2, None,
     "a Write of an invalid pack is denied with validator findings"),
    ("pack-valid-write", "guard_pack_write.py",
     "pack-write-valid.json", 0, None,
     "a Write of a validator-clean pack passes"),
    ("pack-notebookedit-invalid", "guard_pack_write.py",
     "pack-notebookedit-invalid.json", 0, "notebookedit-on-pack",
     "a NotebookEdit writing an invalid pack passes — the live PreToolUse pack "
     "matcher is Write-only and the guard reads only tool_input.file_path"),
    ("auditor-no-binder-path", "guard_auditor_dispatch.py",
     "auditor-dispatch-no-binder.json", 2, None,
     "a recognized auditor dispatch without a binder path is denied (fail-closed)"),
    ("auditor-no-checklists", "guard_auditor_dispatch.py",
     "auditor-dispatch-no-checklists.json", 2, None,
     "a recognized dispatch naming an sme-pinned binder without checklist "
     "evidence is denied"),
    ("auditor-complete", "guard_auditor_dispatch.py",
     "auditor-dispatch-complete.json", 0, None,
     "a dispatch with binder path and checklist evidence passes"),
    ("auditor-unrecognized", "guard_auditor_dispatch.py",
     "auditor-dispatch-unrecognized.json", 0, None,
     "an unrecognized dispatch shape always passes"),
    ("kaizen-in-surface", "guard_writer_confinement.py",
     "kaizen-write-in-surface.json", 0, None,
     "a kaizen write inside .karta/sme/ passes"),
    ("kaizen-out-of-surface", "guard_writer_confinement.py",
     "kaizen-write-out-of-surface.json", 2, None,
     "a kaizen write outside its surface is denied"),
    ("kaizen-unverifiable-input", "guard_writer_confinement.py",
     "kaizen-write-unverifiable.json", 2, None,
     "a recognized kaizen write with unverifiable tool_input is denied "
     "(fail-closed on the recognized shape)"),
    ("stop-dirty-delivery", "guard_delivery_stop.py",
     "stop-dirty.json", 2, None,
     "a Stop in a repo with a stranded built ref (no done/failed) is blocked, "
     "via the real refs/karta/<slug>/item-<id>/built namespace"),
    ("stop-block-once-repeat", "guard_delivery_stop.py",
     "stop-dirty.json", 0, None,
     "an identical second Stop in the same session passes (block-once sentinel)"),
    ("stop-malformed-json", "guard_delivery_stop.py",
     "malformed.json", 0, None,
     "malformed stdin fails open (exit 0) — a stray Stop trap is worse than a "
     "missed nudge"),
)

# The findings the first committed baseline must contain (the in-Claude-Code
# seeded set from the card's 2026-07-17 seed observation). The probe fails
# closed when any of these is absent from its own first baseline.
SEEDED_FINDINGS = (
    "gap:staged-not-committed",
    "gap:symlink-alias",
    "gap:notebookedit-on-pack",
    "bypass:binder-write:bash",
    "bypass:pack-write:bash",
    "bypass:pack-write:notebookedit",
    "bypass:kaizen-write:bash",
    "bypass:ref-forge:bash",
    "bypass:integration-merge:bash",
)


# --- Family A ------------------------------------------------------------------

def _build_fixture(target: Path, dest: Path) -> None:
    script = target / FIXTURE_DIR / "build_fixture.sh"
    proc = subprocess.run(["bash", str(script), str(dest)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"fixture build failed: {proc.stderr.strip()}")


def _run_guard(target: Path, fixture: Path, guard: str, payload_file: str) -> int:
    raw = (target / FIXTURE_DIR / "payloads" / payload_file).read_text()
    raw = raw.replace("__FIXTURE__", json.dumps(str(fixture))[1:-1])
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(target))
    proc = subprocess.run([sys.executable, str(target / "hooks" / "scripts" / guard)],
                          input=raw, cwd=str(fixture), env=env,
                          capture_output=True, text=True, timeout=GUARD_TIMEOUT_S)
    return proc.returncode


def _family_a(target: Path, fixture: Path) -> list[dict]:
    rows = []
    for probe_id, guard, payload, expected, gap, note in FAMILY_A:
        actual = _run_guard(target, fixture, guard, payload)
        rows.append({"probe_id": probe_id, "guard": guard, "payload": payload,
                     "expected": expected, "actual": actual, "gap": gap,
                     "note": note})
    return rows


# --- Family B ------------------------------------------------------------------

def _hooks_pairs(target: Path) -> set[tuple[str, str, str]]:
    """(tool-name-lowercased, guard-script-basename, event) triples from
    hooks/hooks.json."""
    data = json.loads((target / "hooks" / "hooks.json").read_text())
    pairs: set[tuple[str, str, str]] = set()
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups:
            tools = [t.strip().lower() for t in group.get("matcher", "").split("|")]
            for hook in group.get("hooks", []):
                name = Path(str(hook.get("command", "")).strip('"').replace("\\", "/")).name
                if not name.endswith(".py"):
                    continue
                pairs.update((tool, name, event) for tool in tools if tool)
    return pairs


def _family_b(target: Path, phantom_guards: tuple[str, ...] = ()) -> dict:
    manifest = json.loads((target / "benchmarks" / "flow" / "mutation-surface.json")
                          .read_text())
    pairs = _hooks_pairs(target)
    channels = set(manifest["channels"])
    rows, errors, bypass_ids = [], [], []
    for row in manifest["rows"]:
        status, channel, guard = row["status"], row["channel"], row.get("guard")
        if channel not in channels:
            errors.append(f"row {row['id']}: channel {channel!r} is not in the "
                          "manifest's declared channel list")
        if status == "enforced":
            if channel not in HOOK_CHANNELS or not guard or not row.get("hook_event"):
                errors.append(f"row {row['id']}: 'enforced' needs a hook channel "
                              f"({', '.join(sorted(HOOK_CHANNELS))}), a guard, and "
                              "a hook_event")
                computed = "bypassed"
            else:
                computed = ("enforced"
                            if (channel, guard, row["hook_event"]) in pairs
                            else "bypassed")
        elif status.startswith("waived:"):
            computed = "waived"
        elif status == "bypass":
            computed = "bypassed"
            bypass_ids.append(row["id"])
        elif status == "n/a":
            computed = "n-a"
        else:
            errors.append(f"row {row['id']}: unknown status {status!r}")
            computed = "bypassed"
        rows.append({"id": row["id"], "mutation_class": row["mutation_class"],
                     "channel": channel, "status": status, "guard": guard,
                     "computed_state": computed, "note": row.get("note", "")})
    guards_on_disk = sorted(p.name for p in
                            (target / "hooks" / "scripts").glob("guard_*.py"))
    manifest_guards = {r["guard"] for r in rows if r["guard"]}
    missing = [g for g in [*guards_on_disk, *phantom_guards]
               if g not in manifest_guards]
    return {"rows": rows, "unwaivered_bypasses": bypass_ids,
            "manifest_errors": errors, "guards_missing_row": missing,
            "manifest_version": manifest["manifest_version"]}


# --- Baseline ------------------------------------------------------------------

def _find_baseline(target: Path, current_name: str) -> tuple[str | None, dict | None]:
    """Newest git-tracked results file for this vector, excluding the file this
    run writes. Returns (relpath, parsed) — (relpath, None) means tracked but
    unreadable, which the caller fails closed on."""
    proc = subprocess.run(["git", "-C", str(target), "ls-files",
                           str(RESULTS_DIR) + "/"],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None, None
    names = [ln for ln in proc.stdout.splitlines()
             if ln.endswith(f"-{VECTOR}.json") and Path(ln).name != current_name]
    if not names:
        return None, None
    newest = sorted(names)[-1]
    try:
        return newest, json.loads((target / newest).read_text())
    except (OSError, json.JSONDecodeError):
        return newest, None


def _regressions(fam_a: list[dict], famb: dict, baseline: dict) -> list[dict]:
    out = []
    base_a = {r.get("probe_id"): r for r in baseline.get("family_a", [])}
    for row in fam_a:
        b = base_a.get(row["probe_id"])
        if b and (b.get("expected"), b.get("actual")) != (row["expected"], row["actual"]):
            out.append({"kind": "family-a-changed", "id": row["probe_id"],
                        "summary": f"family-a row {row['probe_id']} changed: "
                                   f"exit {b.get('actual')} -> {row['actual']}"})
    base_rows = {r.get("id"): r for r in baseline.get("family_b", {}).get("rows", [])}
    for row in famb["rows"]:
        b = base_rows.get(row["id"])
        if b and b.get("computed_state") == "enforced" \
                and row["computed_state"] == "bypassed":
            out.append({"kind": "flip", "id": row["id"],
                        "summary": f"cell {row['id']} flipped enforced -> bypassed"})
    base_bypass = set(baseline.get("family_b", {}).get("unwaivered_bypasses", []))
    for new in sorted(set(famb["unwaivered_bypasses"]) - base_bypass):
        out.append({"kind": "new-bypass", "id": new,
                    "summary": f"new unwaivered bypass {new}"})
    return out


# --- Assembly ------------------------------------------------------------------

def _assemble(fam_a: list[dict], famb: dict, baseline_name: str | None,
              baseline: dict | None) -> dict:
    """Reduce both families plus the baseline into evidence (results-file body)."""
    findings, mismatches = [], 0
    for row in fam_a:
        if row["actual"] != row["expected"]:
            mismatches += 1
            findings.append({"finding_id": f"mismatch:{row['probe_id']}",
                             "severity": "high",
                             "summary": f"{row['probe_id']}: expected exit "
                                        f"{row['expected']}, got {row['actual']} — "
                                        "pinned expectation no longer holds"})
        elif row["gap"]:
            findings.append({"finding_id": f"gap:{row['gap']}",
                             "severity": "known-gap", "summary": row["note"]})
    manifest_notes = {r["id"]: r["note"] for r in famb["rows"]}
    for row_id in famb["unwaivered_bypasses"]:
        findings.append({"finding_id": f"bypass:{row_id}", "severity": "known-gap",
                         "summary": f"unwaivered bypass {row_id}: "
                                    f"{manifest_notes.get(row_id, '')}"})
    for guard in famb["guards_missing_row"]:
        findings.append({"finding_id": f"manifest-missing-guard:{guard}",
                         "severity": "high",
                         "summary": f"{guard} has no mutation-surface.json row — "
                                    "the manifest went stale (fail-closed)"})
    for err in famb["manifest_errors"]:
        findings.append({"finding_id": "manifest-error", "severity": "high",
                         "summary": err})
    regressions = _regressions(fam_a, famb, baseline) if baseline else []
    findings += [{"finding_id": f"regression:{r['kind']}:{r['id']}",
                  "severity": "high", "summary": r["summary"]} for r in regressions]
    if baseline_name and baseline is None:
        findings.append({"finding_id": "baseline-unreadable", "severity": "high",
                         "summary": f"tracked baseline {baseline_name} is not "
                                    "readable JSON — cannot prove no regression"})
    if famb["guards_missing_row"] or famb["manifest_errors"] \
            or (baseline_name and baseline is None):
        status = "fail"
    elif baseline is not None:
        status = "fail" if regressions else "pass"
    else:
        # first baseline: fail closed unless every seeded finding is present
        present = {f["finding_id"] for f in findings}
        missing_seed = [s for s in SEEDED_FINDINGS if s not in present]
        findings += [{"finding_id": f"seed-missing:{s}", "severity": "high",
                      "summary": f"seeded finding {s} absent from the first "
                                 "baseline (fail-closed)"} for s in missing_seed]
        status = "fail" if missing_seed else "pass"
    metrics = {"unwaivered_bypass_count": len(famb["unwaivered_bypasses"]),
               "enforced_to_bypassed_flips":
                   sum(1 for r in regressions if r["kind"] == "flip"),
               "family_a_mismatch_count": mismatches,
               "manifest_version": famb["manifest_version"],
               "baseline": baseline_name or "none"}
    return {"schema_version": 1, "vector": VECTOR,
            "manifest_version": famb["manifest_version"],
            "baseline": baseline_name,
            "family_a": fam_a,
            "family_b": {"rows": famb["rows"],
                         "unwaivered_bypasses": famb["unwaivered_bypasses"]},
            "findings": findings, "metrics": metrics, "status": status}


def _contract(evidence: dict) -> dict:
    return {"id": VECTOR, "status": evidence["status"], "partial": True,
            "implemented_checks": IMPLEMENTED_CHECKS,
            "findings": evidence["findings"], "metrics": evidence["metrics"]}


def _gate_mode(target: Path) -> int:
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "hooked-repo"
        _build_fixture(target, fixture)
        fam_a = _family_a(target, fixture)
    famb = _family_b(target)
    run_date = datetime.date.today().isoformat()
    current_name = f"{run_date}-{VECTOR}.json"
    baseline_name, baseline = _find_baseline(target, current_name)
    evidence = _assemble(fam_a, famb, baseline_name, baseline)
    results_dir = target / RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / current_name).write_text(
        json.dumps(evidence, indent=1) + "\n")
    print(json.dumps(_contract(evidence)))
    return 0


# --- Self-test -----------------------------------------------------------------

def _fixture_state_ok(fixture: Path) -> tuple[bool, str]:
    def git(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(fixture), *a],
                              capture_output=True, text=True)
    checks = [
        ("committed binder in HEAD",
         git("cat-file", "-e", "HEAD:.karta/binders/hooked.json").returncode == 0),
        ("archived binder in HEAD",
         git("cat-file", "-e", "HEAD:.karta/binders/archive/delivered.json").returncode == 0),
        ("sme pack in HEAD",
         git("cat-file", "-e", "HEAD:.karta/sme/minimalism.md").returncode == 0),
        ("staged-only binder in index",
         ".karta/binders/staged-only.json" in git("ls-files").stdout),
        ("staged-only binder NOT in HEAD",
         git("cat-file", "-e", "HEAD:.karta/binders/staged-only.json").returncode != 0),
        ("symlink alias present", (fixture / "plans").is_symlink()),
        ("built ref standing",
         git("show-ref", "--verify", "--quiet",
             "refs/karta/hooked/item-beta/built").returncode == 0),
        ("no done ref",
         git("show-ref", "--verify", "--quiet",
             "refs/karta/hooked/item-beta/done").returncode != 0),
        ("no failed ref",
         git("show-ref", "--verify", "--quiet",
             "refs/karta/hooked/item-beta/failed").returncode != 0),
    ]
    bad = [name for name, ok in checks if not ok]
    return not bad, "; ".join(bad)


def _run_self_test(target: Path) -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        suffix = f" ({detail})" if detail and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        results.append(ok)

    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "one"
        _build_fixture(target, fixture)
        ok, detail = _fixture_state_ok(fixture)
        check("fixture builds every documented state", ok, detail)

        again = Path(td) / "two"
        _build_fixture(target, again)
        sha = lambda p: subprocess.run(  # noqa: E731
            ["git", "-C", str(p), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        check("fixture build is deterministic (same HEAD sha twice)",
              bool(sha(fixture)) and sha(fixture) == sha(again))

        fam_a = _family_a(target, fixture)
        for row in fam_a:
            check(f"family A {row['probe_id']}: exit {row['actual']} "
                  f"(expected {row['expected']})",
                  row["actual"] == row["expected"])

    famb = _family_b(target)
    check("family B: no manifest errors and every guard has a row",
          not famb["manifest_errors"] and not famb["guards_missing_row"],
          "; ".join(famb["manifest_errors"] + famb["guards_missing_row"]))
    check("family B: every enforced row is covered by a live hooks.json matcher",
          all(r["computed_state"] == "enforced" for r in famb["rows"]
              if r["status"] == "enforced"))
    seeded_bypasses = {s[len("bypass:"):] for s in SEEDED_FINDINGS
                       if s.startswith("bypass:")}
    check("family B: seeded unwaivered bypasses are all present",
          seeded_bypasses <= set(famb["unwaivered_bypasses"]))

    ev = _assemble(fam_a, famb, None, None)
    check("first-baseline run passes with the seeded findings recorded",
          ev["status"] == "pass"
          and all(s in {f["finding_id"] for f in ev["findings"]}
                  for s in SEEDED_FINDINGS))

    famb_phantom = _family_b(target, phantom_guards=("guard_phantom.py",))
    check("anti-staleness: a guard with no manifest row flips status to fail",
          _assemble(fam_a, famb_phantom, None, None)["status"] == "fail")

    check("identical baseline stays pass",
          _assemble(fam_a, famb, "prior.json", ev)["status"] == "pass")

    doctored = json.loads(json.dumps(ev))
    doctored["family_a"][0]["actual"] = 0 if fam_a[0]["actual"] else 2
    check("regression: a changed family-a row against the baseline flips fail",
          _assemble(fam_a, famb, "prior.json", doctored)["status"] == "fail")

    doctored = json.loads(json.dumps(ev))
    doctored["family_b"]["unwaivered_bypasses"] = \
        doctored["family_b"]["unwaivered_bypasses"][1:]
    check("regression: a new unwaivered bypass against the baseline flips fail",
          _assemble(fam_a, famb, "prior.json", doctored)["status"] == "fail")

    flipped = json.loads(json.dumps(famb))
    for row in flipped["rows"]:
        if row["computed_state"] == "enforced":
            row["computed_state"] = "bypassed"
            break
    flip_ev = _assemble(fam_a, flipped, "prior.json", ev)
    check("regression: an enforced -> bypassed flip is counted and flips fail",
          flip_ev["status"] == "fail"
          and flip_ev["metrics"]["enforced_to_bypassed_flips"] == 1)

    check("seed check: a missing seeded finding fails the first baseline closed",
          _assemble([r for r in fam_a
                     if r["probe_id"] != "binder-staged-not-committed"],
                    famb, None, None)["status"] == "fail")

    payload = _contract(ev)
    shape_ok = (payload.get("id") == VECTOR
                and payload.get("status") in ("pass", "fail")
                and isinstance(payload.get("partial"), bool) and payload["partial"]
                and isinstance(payload.get("implemented_checks"), list)
                and isinstance(payload.get("findings"), list)
                and isinstance(payload.get("metrics"), dict))
    check("gate payload satisfies the probe JSON contract (partial true)", shape_ok)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=str(Path(__file__).resolve().parents[2]),
                    help="repo root to probe (run_gate passes this)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    target = Path(args.target).resolve()
    if args.self_test:
        return _run_self_test(target)
    return _gate_mode(target)


if __name__ == "__main__":
    sys.exit(main())
