#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Append-only ownership cost ledger (card: benchmarks/meta/ownership-cost-ledger.md).

Keeps a running, append-only account of what it costs to KEEP karta and its bench.
One row per release records four cost parts plus a standing buy-vs-build re-check:

  (a) machinery counts   — sync/projection scripts, byte-identical mirrored copies
                           and total duplicated bytes over the check_shared_copies
                           scope, the codex projections under .agents/, hook scripts,
                           and bench runner scripts + their LOC.
  (b) manual steps       — release-flow steps NOT enforced by the release block
                           (scripts/hooks/precommit_gate.py); the host-side
                           cleanupPeriodDays setting counts as one such manual step.
  (c) bench spend        — wall-clock and token cost summed from committed gate and
                           quarterly-arm artifacts; where no artifact carries such a
                           field, part (c) records "n/a (not yet instrumented)" per
                           honesty rule 12, NEVER 0.
  (d) triage load        — findings opened vs resolved vs waived across committed
                           per-vector results.
  buy-vs-build re-check   — one row per hand-rolled bench component citing the
                           committed evaltool verdicts
                           (benchmarks/research/evaltool-2026-07-17.json) with a
                           yes/no "still correct?" and a one-line reason; a
                           no-longer-correct answer must carry an action or a dated
                           waiver.

Two modes, pinned:
  --append   writes a new row (RELEASE TIME ONLY): computes the fresh row, refuses
             to rewrite or reorder existing rows (rows stay date-ordered and
             release-unique), then appends and writes ownership-ledger.json.
  --check    (default) recomputes the fresh row IN MEMORY and diffs it against the
             last committed row WITHOUT writing — the gate adapter and this item's
             oracle use check mode only, so a gate run leaves ownership-ledger.json
             byte-identical. Exit 0 when the ledger is intact and no buy-vs-build
             row is a no-longer-correct-without-action violation; exit 1 otherwise.

--self-test drives append / reorder-refusal / duplicate-refusal / integrity and
the buy-vs-build violation path on TEMP COPIES only (never the committed ledger),
printing [PASS]/[FAIL] lines and an N/N checks passed summary; --self-test exits 0
only when the summary is N/N checks passed, nonzero otherwise.

The gate adapter benchmarks/probes/ownership-cost-ledger.py maps the check-mode
row diff into the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # benchmarks/field/ -> repo root
LEDGER_REL = Path("benchmarks") / "field" / "results" / "ownership-ledger.json"
EVALTOOL_REL = Path("benchmarks") / "research" / "evaltool-2026-07-17.json"

SCHEMA_VERSION = 1
VECTOR = "ownership-cost-ledger"

# Card-internal inconsistency, recorded (never applied — cards are edited only to
# flip probe_status): the card frontmatter names results: benchmarks/meta/results/
# but the procedure commits the ledger to benchmarks/field/results/. The ledger
# follows the procedure path; this note is surfaced as a finding by the gate adapter.
CARD_ERRATUM = (
    "card frontmatter results: benchmarks/meta/results/ contradicts the procedure's "
    "benchmarks/field/results/ownership-ledger.json path; the ledger follows the "
    "procedure and records the mismatch as a finding (card left untouched)."
)

# The recorded resolution of the chassis contradiction (spec open_questions[0]),
# from benchmarks/results/2026-07-17-baseline.md, carried verbatim in substance as
# the first row's answer.
RESOLUTION = {
    "open_contradiction": "spec open_questions[0] — evaltool says buy promptfoo + "
    "vendor bayes_evals, while the sharpened vectors dropped both from their "
    "per-vector procedures.",
    "promptfoo": "ADOPT for the executable arms only — pinned via "
    "benchmarks/bench/package.json when that lane lands, not before; the "
    "deterministic gate lane never depends on node (standalone stdlib python "
    "probes composed by run_gate.py).",
    "bayes_evals": "reserved for k>=5 only — at n<=3, raw count tables only "
    "(credible intervals at that n are false precision).",
    "source": "benchmarks/results/2026-07-17-baseline.md",
}

# Standing buy-vs-build re-check: one row per hand-rolled bench component. Each row
# names the component, the evaltool area key its verdict lives under (the verdict
# call is read live from the committed evaltool file at compute time), the standing
# still-correct answer, and a one-line reason. A "no" answer must carry an "action"
# or a dated "waiver" {date, reason} or it is a violation.
BUY_VS_BUILD_COMPONENTS = [
    {
        "component": "gate-runner",
        "path": "benchmarks/gate/run_gate.py",
        "evaltool_key": "eval-frameworks",
        "still_correct": "yes",
        "reason": "promptfoo is adopted for the executable arms only, pinned when "
        "that lane lands; the deterministic gate lane is stdlib by design and never "
        "depends on node, so the stdlib composer stays correct.",
        "action": None,
        "waiver": None,
    },
    {
        "component": "transcript-miner",
        "path": "benchmarks/perf/mine_sessions.py",
        "evaltool_key": "stats-telemetry",
        "still_correct": "yes",
        "reason": "direct JSONL session-transcript mining is the recommended "
        "composition for local Claude Code cost/compliance telemetry; no whole-tool "
        "buy fits the modes-1+4 need.",
        "action": None,
        "waiver": None,
    },
    {
        "component": "fixture-harness",
        "path": "benchmarks/fixtures/",
        "evaltool_key": "agent-bench-harnesses",
        "still_correct": "yes",
        "reason": "no off-the-shelf harness benches an orchestration framework's "
        "internal behavior out of the box, so the stdlib fixture factories stay "
        "in-house.",
        "action": None,
        "waiver": None,
    },
    {
        "component": "stats",
        "path": "benchmarks/(bayes/small-n treatment)",
        "evaltool_key": "stats-telemetry",
        "still_correct": "yes",
        "reason": "bayes_evals is reserved for k>=5 only; at n<=3 raw count tables — "
        "vendoring its single file is the recorded plan once k>=5 executable arms "
        "exist, not before.",
        "action": None,
        "waiver": None,
    },
]

_WALL_FIELD_HINTS = ("wall_clock", "wallclock", "duration_s", "elapsed_s",
                     "latency_ms", "latency_s")
_TOKEN_FIELD_HINTS = ("tokens", "token_cost", "cost_usd")
_FINDING_KEYS = ("findings",)
_RESOLVED_KEYS = ("resolved", "resolved_findings")
_WAIVED_KEYS = ("waived", "waivers")


# ----------------------------------------------------------------- row parts

def _plugin_version(target: Path) -> str:
    try:
        return json.loads(
            (target / ".claude-plugin" / "plugin.json").read_text()
        ).get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        return "unknown"


def part_machinery(target: Path) -> dict:
    """Part (a): machinery counts over deterministic tree scopes."""
    scripts_dir = target / "scripts"
    sync_scripts = sorted(
        p.name for p in scripts_dir.glob("*.py")
        if p.stem.startswith("sync_") or p.stem == "check_shared_copies"
    )

    # check_shared_copies scope: skills/_shared/<f> mirrored into skills/*/references/<f>.
    shared_root = target / "skills" / "_shared"
    shared = {p.relative_to(shared_root).as_posix()
              for p in shared_root.rglob("*.md")} if shared_root.is_dir() else set()
    mirrored_copies = 0
    duplicated_bytes = 0
    skills_root = target / "skills"
    if skills_root.is_dir():
        for skill_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
            if skill_dir.name == "_shared":
                continue
            refs = skill_dir / "references"
            if not refs.is_dir():
                continue
            for ref in refs.rglob("*.md"):
                if ref.relative_to(refs).as_posix() in shared:
                    mirrored_copies += 1
                    duplicated_bytes += len(ref.read_bytes())

    codex_projection_files = sum(
        1 for p in (target / ".agents").rglob("*") if p.is_file()
    ) if (target / ".agents").is_dir() else 0

    hooks_dir = target / "hooks" / "scripts"
    hook_scripts = sum(1 for _ in hooks_dir.glob("*.py")) if hooks_dir.is_dir() else 0

    bench_scripts = 0
    bench_loc = 0
    bench_root = target / "benchmarks"
    if bench_root.is_dir():
        for p in sorted(bench_root.rglob("*.py")):
            bench_scripts += 1
            bench_loc += len(p.read_text(errors="replace").splitlines())

    return {
        "sync_projection_scripts": len(sync_scripts),
        "sync_projection_script_names": sync_scripts,
        "shared_source_files": len(shared),
        "mirrored_copies": mirrored_copies,
        "duplicated_bytes": duplicated_bytes,
        "codex_projection_files": codex_projection_files,
        "hook_scripts": hook_scripts,
        "bench_scripts": bench_scripts,
        "bench_loc": bench_loc,
    }


def _newest_checklist(target: Path) -> Path | None:
    rel_dir = target / "docs" / "releases"
    if not rel_dir.is_dir():
        return None
    best, best_key = None, None
    for p in rel_dir.glob("v*-checklist.md"):
        ver = p.name[1:].split("-checklist")[0]
        try:
            key = tuple(int(x) for x in ver.split("."))
        except ValueError:
            continue
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def part_manual_steps(target: Path) -> dict:
    """Part (b): documented release steps minus those the release block enforces,
    plus the host-side cleanupPeriodDays manual step.

    Countable only once the release block exists in the tree; when it is absent the
    enforced count is 0 (nothing is enforced yet)."""
    checklist = _newest_checklist(target)
    documented = 0
    checklist_name = None
    if checklist is not None:
        checklist_name = checklist.name
        documented = sum(
            1 for line in checklist.read_text().splitlines()
            if line.lstrip().startswith("- [ ]") or line.lstrip().startswith("- [x]")
        )
    # The release block enforces exactly one release step: a green full-gate file
    # matching the RC version + parent HEAD staged with the version-bump commit.
    release_block = target / "scripts" / "hooks" / "precommit_gate.py"
    enforced = 1 if release_block.is_file() else 0
    host_side = [
        "set cleanupPeriodDays>=365 in ~/.claude/settings.json (transcript "
        "retention; host configuration, not enforced by the release block)"
    ]
    manual = max(0, documented - enforced) + len(host_side)
    return {
        "documented_checklist_steps": documented,
        "checklist": checklist_name,
        "enforced_by_release_block": enforced,
        "host_side_manual_steps": host_side,
        "manual_release_steps": manual,
    }


def _walk_numbers(node: object, hints: tuple[str, ...], acc: list[float]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and any(h in k.lower() for h in hints):
                acc.append(float(v))
            _walk_numbers(v, hints, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_numbers(v, hints, acc)


def part_bench_spend(target: Path) -> dict:
    """Part (c): wall-clock/token spend from committed gate + quarterly artifacts.

    Honesty rule 12: an artifact may carry a spend FIELD that is all-zero because the
    run was never instrumented or the raws decayed (e.g. the perf snapshot's
    token counts over decayed transcripts). An all-zero total is NOT "zero spend" —
    it is "not yet instrumented", so part (c) records 'n/a (not yet instrumented)',
    never 0. A numeric sum activates only when a genuine non-zero spend value
    appears in a committed artifact."""
    artifacts: list[Path] = []
    gate_dir = target / "benchmarks" / "results" / "gate"
    if gate_dir.is_dir():
        artifacts += [p for p in sorted(gate_dir.glob("*.json"))
                      if not p.name.endswith(".partial.json")]
    bench_root = target / "benchmarks"
    if bench_root.is_dir():
        for p in sorted(bench_root.rglob("results/*.json")):
            if p.name == LEDGER_REL.name:
                continue
            artifacts.append(p)
    wall: list[float] = []
    tokens: list[float] = []
    for p in artifacts:
        try:
            doc = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        _walk_numbers(doc, _WALL_FIELD_HINTS, wall)
        _walk_numbers(doc, _TOKEN_FIELD_HINTS, tokens)

    def _spend(vals: list[float]):
        # A field that is present but sums to 0 is not instrumentation — n/a.
        return sum(vals) if any(v != 0 for v in vals) else "n/a (not yet instrumented)"

    return {
        "artifacts_scanned": len(artifacts),
        "wall_clock_fields_found": len(wall),
        "token_fields_found": len(tokens),
        "wall_clock_s": _spend(wall),
        "tokens": _spend(tokens),
    }


def part_triage(target: Path) -> dict:
    """Part (d): findings opened vs resolved vs waived across committed per-vector
    results (the ownership ledger itself and the gate roll-up dir are excluded)."""
    opened = resolved = waived = 0
    bench_root = target / "benchmarks"
    files_scanned = 0
    if bench_root.is_dir():
        for p in sorted(bench_root.rglob("results/*.json")):
            if p.name == LEDGER_REL.name:
                continue
            try:
                doc = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            files_scanned += 1
            o, r, w = _count_triage(doc)
            opened += o
            resolved += r
            waived += w
    unresolved = max(0, opened - resolved - waived)
    return {
        "results_files_scanned": files_scanned,
        "opened": opened,
        "resolved": resolved,
        "waived": waived,
        "unresolved": unresolved,
    }


def _count_triage(node: object) -> tuple[int, int, int]:
    opened = resolved = waived = 0
    if isinstance(node, dict):
        for k, v in node.items():
            kl = k.lower()
            if kl in _FINDING_KEYS and isinstance(v, list):
                opened += len(v)
            elif kl in _RESOLVED_KEYS and isinstance(v, list):
                resolved += len(v)
            elif kl in _WAIVED_KEYS and isinstance(v, list):
                waived += len(v)
            elif kl == "waived" and v is True:
                waived += 1
            o, r, w = _count_triage(v)
            opened += o
            resolved += r
            waived += w
    elif isinstance(node, list):
        for v in node:
            o, r, w = _count_triage(v)
            opened += o
            resolved += r
            waived += w
    return opened, resolved, waived


def _evaltool_calls(target: Path) -> dict[str, str]:
    """{area-key: verdict.call} from the committed evaltool verdicts file."""
    doc = json.loads((target / EVALTOOL_REL).read_text())
    return {a["key"]: a["verdict"]["call"] for a in doc["areas"] if "key" in a}


def buy_vs_build(target: Path) -> list[dict]:
    """Standing buy-vs-build rows; the verdict call is read live from the committed
    evaltool file so every row is anchored to the same recorded evidence."""
    calls = _evaltool_calls(target)
    rows = []
    for comp in BUY_VS_BUILD_COMPONENTS:
        key = comp["evaltool_key"]
        if key not in calls:
            raise ValueError(
                f"evaltool file has no area key {key!r} for component "
                f"{comp['component']!r} — re-verify {EVALTOOL_REL.as_posix()}")
        rows.append({
            "component": comp["component"],
            "path": comp["path"],
            "evaltool_key": key,
            "evaltool_verdict": calls[key],
            "cites": EVALTOOL_REL.as_posix(),
            "still_correct": comp["still_correct"],
            "reason": comp["reason"],
            "action": comp["action"],
            "waiver": comp["waiver"],
        })
    return rows


def compute_row(target: Path, release: str, date: str) -> dict:
    """The deterministic fresh row for `release`/`date` over the current tree."""
    return {
        "release": release,
        "date": date,
        "machinery": part_machinery(target),
        "manual_release_steps": part_manual_steps(target),
        "bench_spend": part_bench_spend(target),
        "triage_load": part_triage(target),
        "buy_vs_build": buy_vs_build(target),
        "resolution": RESOLUTION,
    }


# ----------------------------------------------------------------- ledger I/O

def _empty_ledger() -> dict:
    return {"schema_version": SCHEMA_VERSION, "vector": VECTOR,
            "note": "append-only ownership cost ledger — one row per release; rows "
                    "are never rewritten or reordered. See "
                    "benchmarks/meta/ownership-cost-ledger.md.",
            "card_erratum": CARD_ERRATUM, "rows": []}


def load_ledger(target: Path) -> dict:
    path = target / LEDGER_REL
    if not path.is_file():
        return _empty_ledger()
    return json.loads(path.read_text())


def _write_ledger(target: Path, ledger: dict) -> Path:
    path = target / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n")
    return path


def validate_integrity(ledger: dict) -> list[str]:
    """Rows must be date-ordered (non-decreasing) and release-unique."""
    errors: list[str] = []
    rows = ledger.get("rows", [])
    seen: set[str] = set()
    prev_date: str | None = None
    for i, row in enumerate(rows):
        rel = row.get("release")
        if rel in seen:
            errors.append(f"row {i}: duplicate release {rel!r}")
        seen.add(rel)
        date = row.get("date")
        if prev_date is not None and date is not None and date < prev_date:
            errors.append(
                f"row {i}: date {date!r} is earlier than the previous row's {prev_date!r} "
                f"(rows must be date-ordered)")
        prev_date = date if date is not None else prev_date
    return errors


def buyvsbuild_violations(row: dict) -> list[str]:
    """A buy-vs-build row answered no-longer-correct with neither an action nor a
    dated waiver is a violation."""
    violations = []
    for r in row.get("buy_vs_build", []):
        if str(r.get("still_correct", "")).lower() in ("no", "no-longer-correct", "false"):
            waiver = r.get("waiver")
            has_waiver = isinstance(waiver, dict) and waiver.get("date") and waiver.get("reason")
            if not r.get("action") and not has_waiver:
                violations.append(
                    f"buy-vs-build component {r.get('component')!r} is no-longer-correct "
                    f"with neither an action nor a dated waiver")
    return violations


def diff_row(fresh: dict, last: dict | None) -> dict:
    """Cost-field deltas of `fresh` vs `last` (the card's per-release deliverable).

    Compares substantive cost fields only — release label and date are excluded."""
    if last is None:
        return {"first_row": True, "note": "no prior row — this row is the baseline"}
    fm, lm = fresh["machinery"], last["machinery"]
    fresh_scripts = fm["sync_projection_scripts"] + fm["hook_scripts"] + fm["bench_scripts"]
    last_scripts = lm["sync_projection_scripts"] + lm["hook_scripts"] + lm["bench_scripts"]

    def _spend_delta(field: str):
        a, b = fresh["bench_spend"][field], last["bench_spend"][field]
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return a - b
        return "n/a"

    no_longer_correct = sum(
        1 for r in fresh["buy_vs_build"]
        if str(r.get("still_correct", "")).lower() in ("no", "no-longer-correct", "false"))
    return {
        "first_row": False,
        "against_release": last.get("release"),
        "duplicated_bytes_delta": fm["duplicated_bytes"] - lm["duplicated_bytes"],
        "script_count_delta": fresh_scripts - last_scripts,
        "bench_loc_delta": fm["bench_loc"] - lm["bench_loc"],
        "wall_clock_delta": _spend_delta("wall_clock_s"),
        "token_delta": _spend_delta("tokens"),
        "unresolved_triage": fresh["triage_load"]["unresolved"],
        "buy_vs_build_no_longer_correct": no_longer_correct,
    }


def check(target: Path) -> dict:
    """Recompute the fresh row and diff it against the last committed row WITHOUT
    writing. Returns the evidence bundle the gate adapter maps into its contract."""
    ledger = load_ledger(target)
    integrity = validate_integrity(ledger)
    last = ledger["rows"][-1] if ledger.get("rows") else None
    fresh = compute_row(target, _plugin_version(target), _today())
    bvb = buyvsbuild_violations(fresh)
    # An existing last row that itself declares a no-longer-correct-without-action
    # row is a standing violation the ledger already carries.
    if last is not None:
        bvb += buyvsbuild_violations(last)
    return {
        "fresh_row": fresh,
        "last_row": last,
        "rows_total": len(ledger.get("rows", [])),
        "integrity_errors": integrity,
        "buy_vs_build_violations": bvb,
        "diff": diff_row(fresh, last),
    }


def append_row(target: Path, release: str, date: str) -> dict:
    """Release-time only: compute and append a fresh row, refusing to rewrite or
    reorder existing rows. Raises ValueError on any integrity refusal."""
    ledger = load_ledger(target)
    pre = validate_integrity(ledger)
    if pre:
        raise ValueError("existing ledger is already corrupt: " + "; ".join(pre))
    rows = ledger.setdefault("rows", [])
    if any(r.get("release") == release for r in rows):
        raise ValueError(
            f"release {release!r} already has a row — the ledger only appends, it "
            f"never rewrites (rows are release-unique)")
    if rows and rows[-1].get("date") is not None and date < rows[-1]["date"]:
        raise ValueError(
            f"date {date!r} is earlier than the last row's {rows[-1]['date']!r} — "
            f"rows must be date-ordered")
    rows.append(compute_row(target, release, date))
    post = validate_integrity(ledger)
    if post:
        raise ValueError("append would corrupt the ledger: " + "; ".join(post))
    _write_ledger(target, ledger)
    return ledger


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ----------------------------------------------------------------- self-test

def self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    with tempfile.TemporaryDirectory(prefix="ownership-ledger-selftest-") as td:
        tmp = Path(td)
        # A miniature synthetic tree so compute_row runs without the real repo.
        _mk_synth_tree(tmp)

        row_a = compute_row(tmp, "1.0.0", "2026-01-01")
        ck("compute_row produces all four parts plus buy-vs-build and resolution",
           all(k in row_a for k in ("machinery", "manual_release_steps",
                                    "bench_spend", "triage_load", "buy_vs_build",
                                    "resolution")),
           str(list(row_a.keys())))
        ck("part (c) records 'n/a (not yet instrumented)' when no wall-clock field "
           "exists (never 0)",
           row_a["bench_spend"]["wall_clock_s"] == "n/a (not yet instrumented)",
           str(row_a["bench_spend"]))
        ck("part (c) records 'n/a' for an all-zero (decayed/uninstrumented) token "
           "field, never 0 (honesty rule 12)",
           row_a["bench_spend"]["tokens"] == "n/a (not yet instrumented)"
           and row_a["bench_spend"]["token_fields_found"] >= 1,
           str(row_a["bench_spend"]))
        ck("each buy-vs-build row cites the committed evaltool verdicts file with a "
           "live verdict call",
           all(r["cites"].endswith("evaltool-2026-07-17.json") and r["evaltool_verdict"]
               for r in row_a["buy_vs_build"]),
           str(row_a["buy_vs_build"][0]))

        # --append seeds row 1 on an empty ledger.
        append_row(tmp, "1.0.0", "2026-01-01")
        led = load_ledger(tmp)
        ck("--append writes row 1 on an empty ledger", len(led["rows"]) == 1, str(led["rows"]))

        # A second, later, unique release appends cleanly.
        append_row(tmp, "1.1.0", "2026-02-01")
        led = load_ledger(tmp)
        ck("--append adds a second date-ordered, release-unique row",
           len(led["rows"]) == 2 and validate_integrity(led) == [], str(validate_integrity(led)))

        # Duplicate release is refused.
        dup_ok = False
        try:
            append_row(tmp, "1.1.0", "2026-03-01")
        except ValueError:
            dup_ok = True
        ck("--append refuses a duplicate release (append-only, never rewrites)", dup_ok)

        # Out-of-order date is refused.
        order_ok = False
        try:
            append_row(tmp, "1.2.0", "2026-01-15")
        except ValueError:
            order_ok = True
        ck("--append refuses an out-of-order (earlier) date", order_ok)

        # check mode leaves the ledger byte-identical.
        before = (tmp / LEDGER_REL).read_bytes()
        result = check(tmp)
        after = (tmp / LEDGER_REL).read_bytes()
        ck("--check recomputes and diffs WITHOUT writing (ledger byte-identical)",
           before == after and "diff" in result, "ledger changed" if before != after else "")

        # Integrity detects a hand-corrupted (reordered) ledger.
        corrupt = json.loads((tmp / LEDGER_REL).read_text())
        corrupt["rows"] = list(reversed(corrupt["rows"]))
        ck("validate_integrity flags an out-of-order (reordered) ledger",
           bool(validate_integrity(corrupt)), str(validate_integrity(corrupt)))
        dup_led = {"rows": [{"release": "9.0.0", "date": "2026-05-01"},
                            {"release": "9.0.0", "date": "2026-06-01"}]}
        ck("validate_integrity flags a duplicate-release ledger",
           any("duplicate release" in e for e in validate_integrity(dup_led)),
           str(validate_integrity(dup_led)))

        # A no-longer-correct buy-vs-build row without an action or waiver is a violation.
        bad_row = json.loads(json.dumps(row_a))
        bad_row["buy_vs_build"][0]["still_correct"] = "no"
        bad_row["buy_vs_build"][0]["action"] = None
        bad_row["buy_vs_build"][0]["waiver"] = None
        ck("a no-longer-correct buy-vs-build row without an action or dated waiver is "
           "a violation",
           len(buyvsbuild_violations(bad_row)) == 1, str(buyvsbuild_violations(bad_row)))
        # An action clears the violation.
        ok_row = json.loads(json.dumps(bad_row))
        ok_row["buy_vs_build"][0]["action"] = "open a spike to evaluate the replacement"
        ck("an action on a no-longer-correct row clears the violation",
           buyvsbuild_violations(ok_row) == [], str(buyvsbuild_violations(ok_row)))
        # A dated waiver also clears it.
        wv_row = json.loads(json.dumps(bad_row))
        wv_row["buy_vs_build"][0]["waiver"] = {"date": "2026-07-18", "reason": "revisit next cycle"}
        ck("a dated waiver on a no-longer-correct row clears the violation",
           buyvsbuild_violations(wv_row) == [], str(buyvsbuild_violations(wv_row)))

        # diff_row reports zero cost deltas for an identical tree recomputation.
        same = compute_row(tmp, "1.2.0", "2026-07-01")
        d = diff_row(same, row_a)
        ck("diff_row reports zero cost deltas when the tree is unchanged",
           d["duplicated_bytes_delta"] == 0 and d["script_count_delta"] == 0
           and d["bench_loc_delta"] == 0 and d["buy_vs_build_no_longer_correct"] == 0,
           str(d))

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {detail}"))
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def _mk_synth_tree(root: Path) -> None:
    """A tiny tree exercising every compute_row scope without the real repo."""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.2.0"}))
    (root / "scripts" / "hooks").mkdir(parents=True)
    (root / "scripts" / "sync_codex_skills.py").write_text("# sync\n")
    (root / "scripts" / "check_shared_copies.py").write_text("# mirror check\n")
    (root / "scripts" / "hooks" / "precommit_gate.py").write_text("# release block\n")
    (root / "skills" / "_shared").mkdir(parents=True)
    (root / "skills" / "_shared" / "a.md").write_text("shared-A\n")
    (root / "skills" / "s1" / "references").mkdir(parents=True)
    (root / "skills" / "s1" / "references" / "a.md").write_text("shared-A\n")  # a mirror copy
    (root / ".agents").mkdir()
    (root / ".agents" / "proj.md").write_text("projection\n")
    (root / "hooks" / "scripts").mkdir(parents=True)
    (root / "hooks" / "scripts" / "guard_x.py").write_text("# hook\n")
    (root / "benchmarks" / "gate").mkdir(parents=True)
    (root / "benchmarks" / "gate" / "run_gate.py").write_text("# runner\nx = 1\n")
    (root / "benchmarks" / "results" / "gate").mkdir(parents=True)
    (root / "benchmarks" / "results" / "gate" / "2026-01-01-gate.json").write_text(
        json.dumps({"vectors": [{"id": "v", "findings": []}]}))
    (root / "benchmarks" / "dark" / "results").mkdir(parents=True)
    # Carries a token FIELD that is all-zero (decayed/uninstrumented) — rule 12: n/a, not 0.
    (root / "benchmarks" / "dark" / "results" / "2026-01-01-x.json").write_text(
        json.dumps({"findings": [{"id": "f1"}, {"id": "f2"}], "resolved": [{"id": "f0"}],
                    "metrics": {"output_tokens": 0}}))
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "docs" / "releases" / "v1.2.0-checklist.md").write_text(
        "# checklist\n- [x] step one\n- [ ] step two\n- [ ] step three\n")
    (root / "benchmarks" / "research").mkdir(parents=True)
    (root / "benchmarks" / "research" / "evaltool-2026-07-17.json").write_text(json.dumps({
        "areas": [
            {"key": "eval-frameworks", "verdict": {"call": "buy"}},
            {"key": "stats-telemetry", "verdict": {"call": "adopt-component"}},
            {"key": "agent-bench-harnesses", "verdict": {"call": "adopt-component"}},
        ]
    }))


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="append-only ownership cost ledger")
    ap.add_argument("--target", type=Path, default=ROOT,
                    help="karta repo root (default: this script's repo)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--append", action="store_true",
                      help="RELEASE TIME ONLY: append a fresh row and write the ledger")
    mode.add_argument("--check", action="store_true",
                      help="(default) recompute and diff the fresh row without writing")
    mode.add_argument("--self-test", action="store_true")
    ap.add_argument("--release", default=None,
                    help="row release label for --append (default: plugin.json version)")
    ap.add_argument("--date", default=None,
                    help="row date YYYY-MM-DD for --append (default: today)")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    target = args.target.resolve()

    if args.append:
        release = args.release or _plugin_version(target)
        date = args.date or _today()
        try:
            ledger = append_row(target, release, date)
        except ValueError as e:
            print(f"REFUSED: {e}")
            return 1
        print(f"APPENDED release {release} ({date}); ledger now has "
              f"{len(ledger['rows'])} row(s): {(target / LEDGER_REL).relative_to(target)}")
        return 0

    # default: --check
    result = check(target)
    print(json.dumps({
        "rows_total": result["rows_total"],
        "integrity_errors": result["integrity_errors"],
        "buy_vs_build_violations": result["buy_vs_build_violations"],
        "diff": result["diff"],
    }, indent=2))
    return 1 if result["integrity_errors"] or result["buy_vs_build_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
