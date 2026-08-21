# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Assert every design fact a binder records is traced to an assertion, or says why not.

A binder that records design facts keeps them at token_manifest.design_fact_table
(a list of rows; design_facts is additionalProperties:false and can never hold one).
Each row carries either a non-empty traced_by — every entry '<item-id>:<0-based
assertion index>' naming a real work item and an assertion index it has — or an
explicit, non-empty untraced_reason. A binder with no fact table passes untouched.

LIMIT: this check proves a fact NAMES an existing assertion. It never proves the
assertion depends on the fact — that is a reading, and only a reviewer can make it.

Usage:
  uv run scripts/check_fact_traces.py                 # sweep .karta/binders/*.json (non-recursive)
  uv run scripts/check_fact_traces.py <binder.json>…  # check the named binders / directories
  uv run scripts/check_fact_traces.py --self-test     # run the embedded fixtures
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINDERS = ROOT / ".karta" / "binders"
LIMIT = ("limit: proves a fact NAMES an existing assertion — never that the "
         "assertion depends on the fact")
TRACE_RE = re.compile(r"^(?P<item>[^:]+):(?P<index>\d+)$")


def check_binder(binder: object, label: str) -> tuple[list[str], list[str]]:
    """(errors, notes) for one parsed binder. Notes echo every declared untraced
    reason so an accepted gap is visible in the output, never silent."""
    errors: list[str] = []
    notes: list[str] = []
    if not isinstance(binder, dict):
        return [f"{label}: binder must be a JSON object"], notes
    manifest = binder.get("token_manifest")
    if not isinstance(manifest, dict) or "design_fact_table" not in manifest:
        return errors, notes                     # records no facts — nothing to trace
    table = manifest["design_fact_table"]
    if not isinstance(table, list):
        return [f"{label}: token_manifest.design_fact_table must be a list of fact rows"], notes
    items: dict[str, int] = {}
    for it in binder.get("work_items") or []:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            oracle = it.get("oracle") if isinstance(it.get("oracle"), dict) else {}
            assertions = oracle.get("assertions")
            items[it["id"]] = len(assertions) if isinstance(assertions, list) else 0
    seen: set[str] = set()
    for i, row in enumerate(table):
        if not isinstance(row, dict):
            errors.append(f"{label}: design_fact_table[{i}] must be an object")
            continue
        fid = row.get("id") if isinstance(row.get("id"), str) and row.get("id") else f"row {i}"
        if fid in seen:
            errors.append(f"{label}: fact '{fid}' is recorded twice — ids must be unique")
        seen.add(fid)
        traces = row.get("traced_by", [])
        reason = row.get("untraced_reason")
        if not isinstance(traces, list) or not all(isinstance(t, str) for t in traces):
            errors.append(f"{label}: fact '{fid}': traced_by must be a list of "
                          f"'<item-id>:<assertion index>' strings")
            continue
        if traces and reason is not None:
            errors.append(f"{label}: fact '{fid}' carries both a trace and an untraced_reason — "
                          f"a fact is traced or it is untraced, not both")
        if not traces:
            if isinstance(reason, str) and reason.strip():
                notes.append(f"{label}: fact '{fid}' untraced — {reason.strip()}")
            else:
                errors.append(f"{label}: fact '{fid}' is untraced — no traced_by entry and no "
                              f"untraced_reason (trace it as '<item-id>:<assertion index>', "
                              f"or say why no assertion depends on it)")
            continue
        for t in traces:
            m = TRACE_RE.match(t)
            if not m:
                errors.append(f"{label}: fact '{fid}': trace '{t}' is not "
                              f"'<item-id>:<0-based assertion index>'")
                continue
            item, index = m["item"], int(m["index"])
            if item not in items:
                errors.append(f"{label}: fact '{fid}': trace '{t}' names work item '{item}', "
                              f"which this binder does not have")
            elif index >= items[item]:
                have = (f"{items[item]} (valid: 0..{items[item] - 1})" if items[item]
                        else "no assertions")
                errors.append(f"{label}: fact '{fid}': trace '{t}' names assertion {index}, "
                              f"but '{item}' has {have}")
    return errors, notes


def check_path(path: Path) -> tuple[list[str], list[str]]:
    """A file is checked; a directory is swept non-recursively (*.json) — an archived
    binder is frozen by the immutability guard, so a trace rule there could never be
    satisfied and is not applied."""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    errors: list[str] = []
    notes: list[str] = []
    for f in files:
        try:
            label = str(f.relative_to(ROOT))
        except ValueError:
            label = str(f)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errors.append(f"{label}: could not read binder JSON ({e})")
            continue
        e, n = check_binder(data, label)
        errors.extend(e)
        notes.extend(n)
    return errors, notes


def _self_test() -> int:
    """Embedded fixtures: every rule has a passing case and a case that fails it."""
    def binder(table, items=None):
        if items is None:
            items = {"view": 3, "gate": 1}
        b = {"slug": "fx", "work_items": [
            {"id": k, "oracle": {"assertions": [f"a{i}" for i in range(n)]}} for k, n in items.items()]}
        if table is not None:
            b["token_manifest"] = {"design_fact_table": table}
        return b

    def fact(fid, traces=None, reason=None):
        row = {"id": fid, "claim": "…", "traced_by": traces if traces is not None else []}
        if reason is not None:
            row["untraced_reason"] = reason
        return row

    # (name, binder, expected error substrings (empty → must pass), expected note substrings)
    cases = [
        ("every fact traced to a real item and index passes",
         binder([fact("a", ["view:0"]), fact("b", ["view:2", "gate:0"])]), [], []),
        ("one untraced fact fails, and the failure names it",
         binder([fact("a", ["view:0"]), fact("needs-a-home")]), ["fact 'needs-a-home' is untraced"], []),
        ("a trace naming a work item the binder does not have fails",
         binder([fact("a", ["ghost:0"])]), ["names work item 'ghost'"], []),
        ("a trace naming an assertion index the item does not have fails",
         binder([fact("a", ["view:3"])]), ["names assertion 3", "has 3"], []),
        ("a trace into an item with no assertions fails",
         binder([fact("a", ["bare:0"])], {"bare": 0}), ["has no assertions"], []),
        ("a trace that is not '<item-id>:<index>' fails",
         binder([fact("a", ["view"]), fact("b", ["view:x"])]),
         ["trace 'view' is not", "trace 'view:x' is not"], []),
        ("an explicit untraced reason passes and is echoed",
         binder([fact("a", [], "already true on the page")]), [],
         ["fact 'a' untraced — already true on the page"]),
        ("an empty untraced reason is no reason",
         binder([fact("a", [], "   ")]), ["fact 'a' is untraced"], []),
        ("a fact carrying both a trace and a reason fails",
         binder([fact("a", ["view:0"], "why")]), ["both a trace and an untraced_reason"], []),
        ("a duplicated fact id fails",
         binder([fact("a", ["view:0"]), fact("a", ["view:1"])]), ["recorded twice"], []),
        ("traced_by that is not a list of strings fails",
         binder([fact("a", "view:0")]), ["traced_by must be a list"], []),
        ("a fact row that is not an object fails",
         binder(["view:0"]), ["design_fact_table[0] must be an object"], []),
        ("a fact table that is not a list fails",
         {"slug": "fx", "work_items": [], "token_manifest": {"design_fact_table": {}}},
         ["must be a list"], []),
        ("a binder with no fact table passes untouched",
         binder(None), [], []),
        ("a binder with a token_manifest but no fact table passes untouched",
         {"slug": "fx", "work_items": [], "token_manifest": {"mechanism": "css vars"}}, [], []),
        ("a binder that is not an object fails",
         ["not", "a", "binder"], ["must be a JSON object"], []),
    ]
    failures = 0
    for name, data, want_err, want_note in cases:
        errors, notes = check_binder(data, "fx.json")
        ok = (bool(errors) == bool(want_err)
              and all(any(w in e for e in errors) for w in want_err)
              and all(any(w in n for n in notes) for w in want_note))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errors!r} / {notes!r}"))
        failures += 0 if ok else 1

    # A real binder from this repo that records no facts passes untouched. Archived
    # binders are frozen, so one is always there to stand in; a live one is preferred.
    real = next((p for d in (BINDERS, BINDERS / "archive") if d.is_dir()
                 for p in sorted(d.glob("*.json"))
                 if "design_fact_table" not in
                 (json.loads(p.read_text(encoding="utf-8")).get("token_manifest") or {})), None)
    if real is None:
        ok, detail = False, "no binder in this repo records no facts"
    else:
        errors, notes = check_path(real)
        ok, detail = (errors == [] and notes == []), f"{real.relative_to(ROOT)} — got {errors!r}"
    print(f"[{'PASS' if ok else 'FAIL'}] a real binder recording no facts passes untouched"
          + (f" ({real.relative_to(ROOT)})" if ok else f" — {detail}"))
    failures += 0 if ok else 1

    # The limit is stated where a reader meets the check: usage text and every run's output.
    ok = LIMIT in " ".join(_parser().format_help().split())
    print(f"[{'PASS' if ok else 'FAIL'}] the check's limit is stated in its usage text")
    failures += 0 if ok else 1

    print(f"self-test: {len(cases) + 2 - failures}/{len(cases) + 2} cases passed")
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Check that every recorded design fact is traced to an assertion "
                    "('<item-id>:<0-based assertion index>') or carries an untraced_reason. "
                    + LIMIT + ".")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="binder files or directories (a directory is swept non-recursively); "
                         "default: .karta/binders")
    ap.add_argument("--self-test", action="store_true", help="run the embedded fixtures")
    return ap


def main() -> int:
    args = _parser().parse_args()
    if args.self_test:
        return _self_test()
    errors: list[str] = []
    notes: list[str] = []
    for p in args.paths or [BINDERS]:
        e, n = check_path(p)
        errors.extend(e)
        notes.extend(n)
    for n in notes:
        print(f"  ~ {n}")
    if errors:
        print("FACT TRACES: FAIL")
        for e in errors:
            print(f"  - {e}")
    else:
        print("FACT TRACES: PASS")
    print(f"  ({LIMIT})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
