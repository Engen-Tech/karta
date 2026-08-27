# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta gate-report checker: mechanize the report grammar the gate agents already state.

Two rules live today only as prose in agents/karta-acceptance-reviewer.md and
agents/karta-safety-auditor.md. This script turns both into a command, so the
orchestrator can check a returned report before acting on it instead of reading it:

  1. VERDICT AGREEMENT. A report carries exactly one `**Verdict:**` line, its value
     belongs to the dispatched agent's own normative verdict set, and it maps to the
     returned envelope by the table that agent file states:

       acceptance: CONFORMANT -> pass, DEVIATION -> concerns,
                   SPEC-SUSPECT -> blocked, BLOCKED -> blocked
       safety:     PASS -> pass, VIOLATION -> concerns, BLOCKED -> blocked

     A verdict outside the dispatched agent's normative set (CONFORMANT in a safety
     report, PASS in an acceptance report) is itself a finding, never a mapped row —
     it means the wrong agent's grammar came back. A divergence is a finding here,
     mirroring the agents' own "a divergence halts the pipeline" rule.

  2. STACK-PACK PROVENANCE (safety reports only). The mandatory line is present in
     its exact grammar and agrees with the binder:

       Stack-pack check: ran|skipped|blocked - pinned: [<ids>]; resolved: [<ids>]; items judged: <n>

     (with an em dash, not the hyphen shown above). The pinned id list must equal the
     binder's sme[] as a set; `skipped` is legitimate only when sme[] is empty or
     absent; pinned-but-unresolved must read `blocked`, never `skipped`.

This checker validates a REPORT against rules the agent files already state. It does
not change them, and neither agent file is edited by the item that ships this script.

Stdlib only. Invoked directly (not installed), matching the non-executable mode of
sibling scripts:

Usage:
  python3 skills/karta-verify/scripts/check_gate_report.py \\
      --agent acceptance|safety --envelope pass|concerns|blocked \\
      --report FILE --binder FILE [--item ID]
  python3 skills/karta-verify/scripts/check_gate_report.py --self-test

Exit codes: 0 = no findings, 1 = findings (or self-test failure), 2 = usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

# The normative verdict -> envelope tables, transcribed from the agent files.
VERDICT_TABLES: dict[str, dict[str, str]] = {
    "acceptance": {
        "CONFORMANT": "pass",
        "DEVIATION": "concerns",
        "SPEC-SUSPECT": "blocked",
        "BLOCKED": "blocked",
    },
    "safety": {
        "PASS": "pass",
        "VIOLATION": "concerns",
        "BLOCKED": "blocked",
    },
}

ENVELOPES = ("pass", "concerns", "blocked")

VERDICT_LINE_RE = re.compile(r"\*\*Verdict:\*\*(?P<rest>.*)")
WORK_ITEM_LINE_RE = re.compile(r"\*\*Work item id:\*\*(?P<rest>.*)")

# The provenance line's exact grammar, em dash included.
PROVENANCE_PREFIX = "Stack-pack check:"
PROVENANCE_RE = re.compile(
    r"^Stack-pack check:\s+(?P<status>ran|skipped|blocked)\s+—\s+"
    r"pinned:\s+\[(?P<pinned>[^\]]*)\];\s+"
    r"resolved:\s+\[(?P<resolved>[^\]]*)\];\s+"
    r"items judged:\s+(?P<judged>\d+)\s*$"
)


def _id_list(raw: str) -> list[str]:
    """Parse a `[a, b]` body into its ids. An empty body is the empty list."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def check_verdict(report: str, agent: str, envelope: str) -> list[str]:
    """Rule 1 — exactly one verdict line, in this agent's normative set, mapping to
    the envelope."""
    findings: list[str] = []
    table = VERDICT_TABLES[agent]

    matches = [m for m in (VERDICT_LINE_RE.search(line) for line in report.splitlines()) if m]
    if len(matches) != 1:
        findings.append(
            f"verdict: expected exactly one '**Verdict:**' line, found {len(matches)}"
        )
        return findings

    verdict = matches[0].group("rest").strip()
    if verdict not in table:
        known = " | ".join(table)
        other = next(a for a in VERDICT_TABLES if a != agent)
        hint = (
            f" (that is the {other} agent's vocabulary — the wrong agent's grammar came back)"
            if verdict in VERDICT_TABLES[other]
            else ""
        )
        findings.append(
            f"verdict: '{verdict}' is not in the {agent} agent's normative set ({known}){hint}"
        )
        return findings

    expected = table[verdict]
    if expected != envelope:
        findings.append(
            f"verdict: report says {verdict}, which maps to envelope '{expected}', "
            f"but the envelope returned '{envelope}' — the agent file calls a divergence "
            f"a pipeline halt"
        )
    return findings


def check_provenance(report: str, pinned_in_binder: list[str]) -> list[str]:
    """Rule 2 — the mandatory stack-pack provenance line, in grammar and in agreement
    with the binder's sme[]. Safety reports only."""
    findings: list[str] = []

    candidates = [line.strip() for line in report.splitlines() if PROVENANCE_PREFIX in line]
    if not candidates:
        findings.append(
            "stack-pack: the mandatory 'Stack-pack check:' provenance line is missing "
            "(the safety agent requires it in every report)"
        )
        return findings
    if len(candidates) > 1:
        findings.append(
            f"stack-pack: expected one 'Stack-pack check:' line, found {len(candidates)}"
        )
        return findings

    m = PROVENANCE_RE.match(candidates[0])
    if not m:
        findings.append(
            "stack-pack: the provenance line does not match its required grammar "
            "'Stack-pack check: ran|skipped|blocked — pinned: [<ids>]; resolved: [<ids>]; "
            f"items judged: <n>' — found: {candidates[0]!r}"
        )
        return findings

    status = m.group("status")
    pinned = _id_list(m.group("pinned"))
    resolved = _id_list(m.group("resolved"))
    binder_set = set(pinned_in_binder)

    if set(pinned) != binder_set:
        findings.append(
            f"stack-pack: the line pins {sorted(set(pinned))} but the binder's sme[] is "
            f"{sorted(binder_set)} — the report must name the packs the binder pins"
        )

    if status == "skipped" and binder_set:
        findings.append(
            f"stack-pack: status 'skipped' is legitimate only when the binder pins no "
            f"sme[]; this binder pins {sorted(binder_set)}"
        )

    unresolved = set(pinned) - set(resolved)
    if unresolved and status != "blocked":
        findings.append(
            f"stack-pack: pinned but unresolved {sorted(unresolved)} must read 'blocked', "
            f"not {status!r}"
        )

    if status == "ran" and not pinned:
        findings.append(
            "stack-pack: status 'ran' with no pinned packs — an empty sme[] reads 'skipped'"
        )

    return findings


def check_item(report: str, item_id: str, binder: dict) -> list[str]:
    """Optional — the named item exists in the binder, and a report that states a work
    item id states this one."""
    findings: list[str] = []
    known = [str(i.get("id")) for i in binder.get("work_items", [])]
    if item_id not in known:
        findings.append(f"item: '{item_id}' is not a work item in the binder (have: {known})")

    for line in report.splitlines():
        m = WORK_ITEM_LINE_RE.search(line)
        if m:
            stated = m.group("rest").strip()
            if stated != item_id:
                findings.append(
                    f"item: the report's work item id {stated!r} is not the dispatched item "
                    f"{item_id!r}"
                )
            break
    return findings


def check_report(report: str, agent: str, envelope: str, binder: dict,
                 item_id: str | None = None) -> list[str]:
    """Run every applicable rule and return the findings, one string each."""
    findings = check_verdict(report, agent, envelope)
    if agent == "safety":
        findings += check_provenance(report, list(binder.get("sme") or []))
    if item_id is not None:
        findings += check_item(report, item_id, binder)
    return findings


def _run_self_test() -> int:
    passed = 0
    total = 0
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total, failures
        total += 1
        if ok:
            passed += 1
        else:
            failures += 1
        suffix = f": {detail}" if detail else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")

    def report(verdict: str, provenance: str | None = None, extra: str = "") -> str:
        lines = ["## Karta Boundary Scan: t", "", f"**Verdict:** {verdict}", ""]
        if provenance is not None:
            lines.append(provenance)
        if extra:
            lines.append(extra)
        return "\n".join(lines) + "\n"

    def prov(status: str, pinned: list[str], resolved: list[str], judged: int) -> str:
        return (f"Stack-pack check: {status} — pinned: [{', '.join(pinned)}]; "
                f"resolved: [{', '.join(resolved)}]; items judged: {judged}")

    empty_binder: dict = {"work_items": [{"id": "a"}]}
    pinned_binder: dict = {"sme": ["p1", "p2"], "work_items": [{"id": "a"}]}

    # (a) a well-formed safety report with a matching envelope passes
    ok = check_report(report("PASS", prov("skipped", [], [], 0)), "safety", "pass", empty_binder)
    check("safety PASS + matching envelope -> clean", ok == [], str(ok))

    # (b) NEGATIVE CONTROL for (a): the same report against a mismatched envelope is a
    #     finding, and the finding is about the verdict mapping (not something else)
    bad = check_report(report("PASS", prov("skipped", [], [], 0)), "safety", "blocked", empty_binder)
    check("safety PASS + 'blocked' envelope -> verdict finding",
          len(bad) == 1 and bad[0].startswith("verdict:") and "maps to envelope 'pass'" in bad[0],
          str(bad))

    # (c) every normative row of both tables maps clean
    rows_ok = all(
        check_report(
            report(v, prov("skipped", [], [], 0) if agent == "safety" else None),
            agent, env, empty_binder,
        ) == []
        for agent, table in VERDICT_TABLES.items()
        for v, env in table.items()
    )
    check("every normative verdict->envelope row maps clean", rows_ok)

    # (d) NEGATIVE CONTROL: a verdict from the other agent's set is a finding about the
    #     normative set, never a mapped row
    alien = check_report(report("CONFORMANT", prov("skipped", [], [], 0)), "safety", "pass",
                         empty_binder)
    check("acceptance verdict in a safety report -> normative-set finding",
          len(alien) == 1 and "not in the safety agent's normative set" in alien[0]
          and "wrong agent's grammar" in alien[0], str(alien))
    alien2 = check_report(report("PASS"), "acceptance", "pass", empty_binder)
    check("safety verdict in an acceptance report -> normative-set finding",
          len(alien2) == 1 and "not in the acceptance agent's normative set" in alien2[0],
          str(alien2))

    # (e) zero and two verdict lines are both findings
    none_f = check_report("no verdict here\n", "acceptance", "pass", empty_binder)
    two_f = check_report("**Verdict:** DEVIATION\n**Verdict:** CONFORMANT\n", "acceptance",
                         "concerns", empty_binder)
    check("missing / duplicated verdict line -> finding",
          len(none_f) == 1 and "found 0" in none_f[0]
          and len(two_f) == 1 and "found 2" in two_f[0], f"{none_f} {two_f}")

    # (f) an acceptance report needs no provenance line; a safety report does
    acc = check_report(report("DEVIATION"), "acceptance", "concerns", empty_binder)
    saf = check_report(report("PASS"), "safety", "pass", empty_binder)
    check("provenance line required of safety reports only",
          acc == [] and len(saf) == 1 and saf[0].startswith("stack-pack:")
          and "missing" in saf[0], f"{acc} {saf}")

    # (g) the provenance grammar is exact: an ASCII hyphen for the em dash is a finding
    hyphen = ("Stack-pack check: skipped - pinned: []; resolved: []; items judged: 0")
    g = check_report(report("PASS", hyphen), "safety", "pass", empty_binder)
    check("provenance line with the wrong dash -> grammar finding",
          len(g) == 1 and "does not match its required grammar" in g[0], str(g))

    # (h) pinned ids must equal the binder's sme[] as a set (order-insensitive)
    reordered = check_report(report("PASS", prov("ran", ["p2", "p1"], ["p2", "p1"], 7)),
                             "safety", "pass", pinned_binder)
    wrong = check_report(report("PASS", prov("ran", ["p1"], ["p1"], 4)),
                         "safety", "pass", pinned_binder)
    check("pinned set must equal the binder's sme[]",
          reordered == [] and len(wrong) == 1 and "the binder's sme[] is" in wrong[0],
          f"{reordered} {wrong}")

    # (i) 'skipped' with a pinning binder, and pinned-but-unresolved not reading 'blocked'
    skipped = check_report(report("PASS", prov("skipped", ["p1", "p2"], [], 0)),
                           "safety", "pass", pinned_binder)
    unresolved_ran = check_report(report("PASS", prov("ran", ["p1", "p2"], ["p1"], 3)),
                                  "safety", "pass", pinned_binder)
    unresolved_blocked = check_report(report("BLOCKED", prov("blocked", ["p1", "p2"], ["p1"], 0)),
                                      "safety", "blocked", pinned_binder)
    check("skipped-with-pins and unresolved-not-blocked are findings; blocked is legitimate",
          any("legitimate only when" in f for f in skipped)
          and any("must read 'blocked'" in f for f in unresolved_ran)
          and unresolved_blocked == [],
          f"{skipped} {unresolved_ran} {unresolved_blocked}")

    # (j) 'ran' with no pinned packs is a finding
    ran_empty = check_report(report("PASS", prov("ran", [], [], 0)), "safety", "pass",
                             empty_binder)
    check("'ran' with an empty sme[] -> finding",
          len(ran_empty) == 1 and "reads 'skipped'" in ran_empty[0], str(ran_empty))

    # (k) --item: an unknown id, and a report naming a different item
    unknown = check_report(report("DEVIATION"), "acceptance", "concerns", empty_binder,
                           item_id="nope")
    mismatch = check_report(report("DEVIATION", None, "**Work item id:** b"), "acceptance",
                            "concerns", empty_binder, item_id="a")
    agree = check_report(report("DEVIATION", None, "**Work item id:** a"), "acceptance",
                         "concerns", empty_binder, item_id="a")
    check("--item catches an unknown id and a report naming another item",
          len(unknown) == 1 and "is not a work item" in unknown[0]
          and len(mismatch) == 1 and "is not the dispatched item" in mismatch[0]
          and agree == [], f"{unknown} {mismatch} {agree}")

    # (l) end-to-end through main(): exit 0 clean, 1 on a finding, 2 on a missing file
    tmp = Path(tempfile.mkdtemp(prefix="check_gate_report_selftest_"))
    rpt = tmp / "report.md"
    rpt.write_text(report("PASS", prov("skipped", [], [], 0)), encoding="utf-8")
    bnd = tmp / "binder.json"
    bnd.write_text(json.dumps(empty_binder), encoding="utf-8")
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        clean = main(["--agent", "safety", "--envelope", "pass", "--report", str(rpt),
                      "--binder", str(bnd)])
        finding = main(["--agent", "safety", "--envelope", "concerns", "--report", str(rpt),
                        "--binder", str(bnd)])
        usage = main(["--agent", "safety", "--envelope", "pass", "--report", str(tmp / "nope.md"),
                      "--binder", str(bnd)])
    check("main() exit codes: 0 clean / 1 findings / 2 unreadable input",
          clean == 0 and finding == 1 and usage == 2, f"{clean} {finding} {usage}")

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"self-test: {passed}/{total} cases passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_gate_report.py",
        description="Check a returned gate report against the grammar its agent file states.",
    )
    ap.add_argument("--agent", choices=sorted(VERDICT_TABLES), help="which gate agent produced the report")
    ap.add_argument("--envelope", choices=ENVELOPES, help="the verdict the return envelope carried")
    ap.add_argument("--report", type=Path, help="path to the returned report")
    ap.add_argument("--binder", type=Path, help="path to the binder JSON")
    ap.add_argument("--item", default=None, help="work item id the gate was dispatched on")
    ap.add_argument("--self-test", action="store_true", help="run embedded fixtures and exit 0/1")
    args = ap.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    missing = [n for n in ("agent", "envelope", "report", "binder") if getattr(args, n) is None]
    if missing:
        ap.error("missing required argument(s): " + ", ".join("--" + n for n in missing))

    try:
        report = args.report.read_text(encoding="utf-8")
    except OSError as e:
        print(f"check_gate_report: cannot read --report: {e}", file=sys.stderr)
        return 2
    try:
        binder = json.loads(args.binder.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"check_gate_report: cannot read --binder: {e}", file=sys.stderr)
        return 2

    findings = check_report(report, args.agent, args.envelope, binder, args.item)
    for f in findings:
        print(f)
    if findings:
        print(f"check_gate_report: {len(findings)} finding(s)")
        return 1
    print("check_gate_report: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
