#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Layer 0 static tripwires for quality-verify-integrity-drills.

Three tripwires watch the reviewer and auditor definitions so no doc change can
quietly widen the known verification gaps (Families E and K need headless
claude -p and are phase 3 — this file is Layer 0 only):

  (a) agents/karta-safety-auditor.md's BLOCKED definition contains an
      empty-diff clause ('readable but empty' in the Verdicts section).
      FAILS today — the known gap is pinned as a recorded finding.
  (b) agents/karta-acceptance-reviewer.md keeps its empty-diff precondition:
      the 'Precondition — the diff must be non-empty' heading plus the
      'readable but **empty**' clause in the Verdicts section (matched with
      markdown emphasis stripped, like (a) — the live Verdicts bullet bolds
      the whole clause, so the inner-bold spelling only appears in the
      Precondition section). A miss is an immediate status "fail" — a shipped
      protection regressing is fail-closed.
  (c) 'store no loop state' stays paired with a persisted-attempt mechanism
      once one ships (regex 'refs/karta/\\S*attempt|persisted attempt
      (counter|ref)' in the same file). Unpaired occurrences are known-open
      findings; the string vanishing entirely is the anchor-lost loud finding.

A missing or renamed Verdicts section anchor is a loud finding (anchor gone
means the doc changed — re-anchor in the same commit), never a silent pass.

Stance: tripwire (b) failing flips status to "fail" directly; tripwires (a)
and (c) are known-open findings under status "fail" only on regression against the last committed results file
— for (a) that means the clause appearing and later vanishing. Baseline
selection: the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file
(committed content is read via git show HEAD:<path>). With no tracked baseline
the known-open set is reported as findings under status pass — and the run
fails closed if a seeded finding is absent from its own first baseline.

Live mode writes the dated results JSON to
benchmarks/quality/results/<date>-verify-drills.json on every run (overwriting
a same-date file; content is byte-deterministic — no timestamps or shas) and
prints the results plus baseline provenance to stdout. Exit 0 whether the
tripwires pass or fail; nonzero means this script itself crashed.

Self-test (--self-test) drives the tripwires over embedded fixture texts, both
passing and failing shapes, printing [PASS]/[FAIL] lines and an N/N checks passed summary
and exits 0 only when the summary is N/N checks passed.

Gate adapter: benchmarks/probes/quality-verify-integrity-drills.py maps this
output into the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}.

Usage:
  python3 benchmarks/verify-drills/static_check.py --target <repo-root>
  python3 benchmarks/verify-drills/static_check.py --self-test
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

VECTOR_ID = "quality-verify-integrity-drills"
SAFETY = "agents/karta-safety-auditor.md"
ACCEPT = "agents/karta-acceptance-reviewer.md"
RESULTS_DIR = "benchmarks/quality/results"
RESULTS_SUFFIX = "-verify-drills.json"

VERDICTS_HEADING = "## Verdicts"
PRECONDITION_HEADING = "## Precondition — the diff must be non-empty"
EMPTY_CLAUSE_BOLD = "readable but **empty**"
EMPTY_CLAUSE_PLAIN = "readable but empty"
LOOP_ANCHOR = "store no loop state"
PERSIST_RE = re.compile(r"refs/karta/\S*attempt|persisted attempt (counter|ref)")

# The first committed baseline must contain these seeded findings (plan-time
# truth, v2.21.0); the run fails closed if one is absent from its own first
# baseline.
SEEDED_FINDING_IDS = (
    "tripwire-a-blocked-empty-diff-clause-missing",
    f"tripwire-c-loop-state-unpaired:{SAFETY}",
    f"tripwire-c-loop-state-unpaired:{ACCEPT}",
)


def verdicts_section(text: str) -> str | None:
    """Return the '## Verdicts' section body, or None when the anchor is gone."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == VERDICTS_HEADING:
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    return "\n".join(body)


def evaluate(safety_text: str, accept_text: str) -> tuple[dict, list[dict]]:
    """Run the three tripwires over the two doc texts.

    Returns (tripwires, findings). Each tripwire state is one of
    'pass' | 'known-open' | 'fail' | 'anchor-lost'.
    """
    tripwires: dict[str, str] = {}
    findings: list[dict] = []

    # (a) safety-auditor BLOCKED empty-diff clause, inside the Verdicts section.
    sec = verdicts_section(safety_text)
    if sec is None:
        tripwires["a"] = "anchor-lost"
        findings.append({
            "finding_id": f"anchor-lost-verdicts-section:{SAFETY}",
            "severity": "error",
            "summary": f"{SAFETY}: '{VERDICTS_HEADING}' section anchor missing or "
                       "renamed — re-anchor the tripwire in the same commit",
        })
    elif EMPTY_CLAUSE_PLAIN in sec.replace("*", ""):
        tripwires["a"] = "pass"
    else:
        tripwires["a"] = "known-open"
        findings.append({
            "finding_id": "tripwire-a-blocked-empty-diff-clause-missing",
            "severity": "known-open",
            "summary": f"{SAFETY}: BLOCKED definition lacks the empty-diff clause "
                       f"('{EMPTY_CLAUSE_PLAIN}' absent from the Verdicts section) — "
                       "a readable-but-empty diff fires no signal (known gap)",
        })

    # (b) acceptance-reviewer empty-diff precondition — fail-closed on a miss.
    missing: list[str] = []
    if not any(ln.strip() == PRECONDITION_HEADING for ln in accept_text.splitlines()):
        missing.append(f"'{PRECONDITION_HEADING}' heading")
    asec = verdicts_section(accept_text)
    if asec is None:
        missing.append(f"'{VERDICTS_HEADING}' section")
        findings.append({
            "finding_id": f"anchor-lost-verdicts-section:{ACCEPT}",
            "severity": "error",
            "summary": f"{ACCEPT}: '{VERDICTS_HEADING}' section anchor missing or "
                       "renamed — re-anchor the tripwire in the same commit",
        })
    elif EMPTY_CLAUSE_PLAIN not in asec.replace("*", ""):
        missing.append(f"'{EMPTY_CLAUSE_BOLD}' clause in the Verdicts section")
    if missing:
        tripwires["b"] = "fail"
        findings.append({
            "finding_id": "tripwire-b-acceptance-reviewer-precondition-lost",
            "severity": "error",
            "summary": f"{ACCEPT}: empty-diff protection regressed — missing "
                       + "; ".join(missing),
        })
    else:
        tripwires["b"] = "pass"

    # (c) 'store no loop state' paired with a persisted-attempt mechanism.
    present = False
    unpaired = False
    for path, text in ((SAFETY, safety_text), (ACCEPT, accept_text)):
        if LOOP_ANCHOR not in text:
            continue
        present = True
        if not PERSIST_RE.search(text):
            unpaired = True
            findings.append({
                "finding_id": f"tripwire-c-loop-state-unpaired:{path}",
                "severity": "known-open",
                "summary": f"{path}: '{LOOP_ANCHOR}' present with no "
                           "persisted-attempt mechanism in the same file — caps "
                           "are in-memory only and reset on resume (known gap)",
            })
    if not present:
        tripwires["c"] = "anchor-lost"
        findings.append({
            "finding_id": "anchor-lost-store-no-loop-state",
            "severity": "error",
            "summary": f"'{LOOP_ANCHOR}' vanished from both scanned files — the "
                       "tripwire lost its anchor; re-anchor in the same commit",
        })
    elif unpaired:
        tripwires["c"] = "known-open"
    else:
        tripwires["c"] = "pass"

    return tripwires, findings


def decide_status(tripwires: dict, findings: list[dict],
                  baseline: dict | None) -> tuple[str, list[dict]]:
    """Compute status; returns (status, regression findings to append)."""
    extra: list[dict] = []
    if tripwires.get("b") != "pass":
        return "fail", extra  # fail-closed: a shipped protection regressed
    if baseline is None:
        have = {f["finding_id"] for f in findings}
        absent = [fid for fid in SEEDED_FINDING_IDS if fid not in have]
        if absent:
            extra.append({
                "finding_id": "seeded-finding-absent",
                "severity": "error",
                "summary": "first-baseline fail-closed: seeded finding(s) absent: "
                           + ", ".join(absent),
            })
            return "fail", extra
        return "pass", extra
    base_trip = baseline.get("tripwires", {})
    base_ids = {f.get("finding_id") for f in baseline.get("findings", [])}
    for key in ("a", "c"):
        if base_trip.get(key) == "pass" and tripwires.get(key) != "pass":
            extra.append({
                "finding_id": f"regression-tripwire-{key}",
                "severity": "error",
                "summary": f"tripwire ({key}) regressed pass -> "
                           f"{tripwires.get(key)} vs the committed baseline",
            })
    for f in findings:
        if f["finding_id"] not in base_ids:
            extra.append({
                "finding_id": f"regression-new-finding:{f['finding_id']}",
                "severity": "error",
                "summary": f"new finding vs the committed baseline: {f['finding_id']}",
            })
    if extra:
        return "fail", extra
    return "pass", extra


def find_baseline(target: Path) -> tuple[str | None, dict | None, str | None]:
    """Newest git-tracked results file for this vector, read from HEAD.

    Returns (relative path or None, parsed content or None, problem or None).
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "ls-files",
             f"{RESULTS_DIR}/*{RESULTS_SUFFIX}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None, None, None  # no git => bootstrap semantics
    tracked = sorted(ln.strip() for ln in proc.stdout.splitlines() if ln.strip())
    if proc.returncode != 0 or not tracked:
        return None, None, None
    newest = tracked[-1]  # ISO date prefix: lexicographic == chronological
    try:
        show = subprocess.run(["git", "-C", str(target), "show", f"HEAD:{newest}"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return newest, None, f"baseline unreadable from HEAD ({e})"
    if show.returncode != 0:
        return None, None, None  # tracked but not committed yet: no baseline
    try:
        return newest, json.loads(show.stdout), None
    except json.JSONDecodeError as e:
        return newest, None, f"baseline unparseable ({e})"


def run_live(target: Path, run_date: str) -> dict:
    texts: dict[str, str] = {}
    findings: list[dict] = []
    for rel in (SAFETY, ACCEPT):
        try:
            texts[rel] = (target / rel).read_text(encoding="utf-8")
        except OSError as e:
            findings.append({"finding_id": f"input-unreadable:{rel}",
                             "severity": "error",
                             "summary": f"{rel} could not be read ({e})"})
    if findings:
        tripwires = {"a": "fail", "b": "fail", "c": "fail"}
        status, baseline_name = "fail", None
    else:
        tripwires, findings = evaluate(texts[SAFETY], texts[ACCEPT])
        baseline_name, baseline, problem = find_baseline(target)
        if problem:
            findings.append({"finding_id": "baseline-unreadable", "severity": "error",
                             "summary": f"{baseline_name}: {problem}"})
            status = "fail"
        else:
            status, extra = decide_status(tripwires, findings, baseline)
            findings += extra

    # Committed results content: byte-deterministic (no dates, shas, baseline
    # names) so a same-date re-run overwrites the file byte-identically.
    core = {
        "schema_version": 1,
        "vector": VECTOR_ID,
        "layer": 0,
        "status": status,
        "tripwires": tripwires,
        "findings": findings,
    }
    out = target / RESULTS_DIR / f"{run_date}{RESULTS_SUFFIX}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(core, indent=2) + "\n", encoding="utf-8")
    return {**core, "baseline_used": baseline_name, "results_file": str(out.relative_to(target))}


# ---------------------------------------------------------------------------
# Self-test fixtures: embedded doc texts, both passing and failing shapes.
# ---------------------------------------------------------------------------
S_TODAY = """# fixture safety auditor
## Verdicts
- **PASS** — nothing unjustified.
- **BLOCKED** — a required input is missing or unreadable (no binder, no readable diff).
The attempt counter is the orchestrator's; you store no loop state.
"""
S_FIXED = """# fixture safety auditor (fixed shape)
## Verdicts
- **BLOCKED** — inputs missing, or the diff is readable but **empty** (zero hunks).
The attempt counter survives in a persisted attempt counter; you store no loop state.
"""
S_NO_VERDICTS = """# fixture safety auditor (anchor gone)
## Rulings
- **BLOCKED** — a required input is missing.
you store no loop state.
"""
S_NO_LOOP = """# fixture safety auditor (no loop-state sentence)
## Verdicts
- **BLOCKED** — a required input is missing or unreadable.
"""
A_TODAY = """# fixture acceptance reviewer
## Precondition — the diff must be non-empty
Run the diff first.
## Verdicts
- **BLOCKED** — no work product, or the diff is readable but **empty** — zero changes.
The attempt counter is the orchestrator's; you store no loop state.
"""
A_LOST_CLAUSE = """# fixture acceptance reviewer (clause lost)
## Precondition — the diff must be non-empty
Run the diff first.
## Verdicts
- **BLOCKED** — no work product to judge.
you store no loop state.
"""
A_LOST_HEADING = """# fixture acceptance reviewer (heading lost)
## Verdicts
- **BLOCKED** — the diff is readable but **empty** — zero changes.
you store no loop state.
"""
A_NO_VERDICTS = """# fixture acceptance reviewer (verdicts anchor gone)
## Precondition — the diff must be non-empty
Run the diff first.
you store no loop state.
"""
A_PAIRED = """# fixture acceptance reviewer (persisted caps shipped)
## Precondition — the diff must be non-empty
Run the diff first.
## Verdicts
- **BLOCKED** — no work product, or the diff is readable but **empty** — zero changes.
Attempts persist under refs/karta/<slug>/item-<id>/attempt; you store no loop state.
"""
A_NO_LOOP = """# fixture acceptance reviewer (no loop-state sentence)
## Precondition — the diff must be non-empty
Run the diff first.
## Verdicts
- **BLOCKED** — no work product, or the diff is readable but **empty** — zero changes.
"""


def self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))

    def ids(findings: list[dict]) -> set[str]:
        return {f["finding_id"] for f in findings}

    t, f = evaluate(S_TODAY, A_TODAY)
    check("(a) today's shape is known-open", t["a"] == "known-open")
    check("(a) known-open emits its seeded finding",
          "tripwire-a-blocked-empty-diff-clause-missing" in ids(f))
    check("(b) today's shape passes", t["b"] == "pass")
    check("(c) unpaired in both files is known-open with per-file findings",
          t["c"] == "known-open"
          and f"tripwire-c-loop-state-unpaired:{SAFETY}" in ids(f)
          and f"tripwire-c-loop-state-unpaired:{ACCEPT}" in ids(f))

    t2, _ = evaluate(S_FIXED, A_TODAY)
    check("(a) bold-normalized clause in Verdicts passes", t2["a"] == "pass")

    t3, f3 = evaluate(S_NO_VERDICTS, A_TODAY)
    check("(a) Verdicts anchor gone is a loud anchor-lost finding",
          t3["a"] == "anchor-lost"
          and f"anchor-lost-verdicts-section:{SAFETY}" in ids(f3))

    t4, _ = evaluate(S_TODAY, A_LOST_CLAUSE)
    check("(b) reviewer losing the empty clause fails", t4["b"] == "fail")
    t5, _ = evaluate(S_TODAY, A_LOST_HEADING)
    check("(b) reviewer losing the precondition heading fails", t5["b"] == "fail")
    t6, f6 = evaluate(S_TODAY, A_NO_VERDICTS)
    check("(b) reviewer Verdicts anchor gone fails loudly",
          t6["b"] == "fail" and f"anchor-lost-verdicts-section:{ACCEPT}" in ids(f6))

    t7, _ = evaluate(S_NO_LOOP, A_PAIRED)
    check("(c) paired with a persisted-attempt mechanism passes", t7["c"] == "pass")
    t8, f8 = evaluate(S_NO_LOOP, A_NO_LOOP)
    check("(c) anchor vanished entirely is anchor-lost, never a silent pass",
          t8["c"] == "anchor-lost" and "anchor-lost-store-no-loop-state" in ids(f8))

    st, _ = decide_status(t, f, None)
    check("no baseline + seeded findings present -> status pass", st == "pass")

    t9, f9 = evaluate(S_FIXED, A_PAIRED)
    st9, ex9 = decide_status(t9, f9, None)
    check("no baseline + seeded finding absent -> fail-closed",
          st9 == "fail" and "seeded-finding-absent" in ids(ex9))

    base = {"tripwires": dict(t), "findings": list(f)}
    st10, _ = decide_status(t, f, base)
    check("unchanged state vs committed baseline -> pass (no regression)",
          st10 == "pass")

    base_fixed = {"tripwires": dict(t2), "findings": []}
    st11, ex11 = decide_status(t, f, base_fixed)
    check("(a) clause appearing then vanishing -> regression fail",
          st11 == "fail" and "regression-tripwire-a" in ids(ex11))

    st12, _ = decide_status(t4, [], base)
    check("(b) failing flips status to fail even against a baseline",
          st12 == "fail")

    st13, ex13 = decide_status(t3, f3, base)
    check("a new finding id vs the baseline -> regression fail",
          st13 == "fail" and any(i.startswith("regression-new-finding:")
                                 for i in ids(ex13)))

    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Layer 0 static tripwires")
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root (default: this script's repo)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="results filename date (YYYY-MM-DD, default today)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded fixture checks and exit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    print(json.dumps(run_live(args.target.resolve(), args.date), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
