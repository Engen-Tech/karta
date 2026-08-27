#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fixed-fixture cost miner — step 6 of benchmarks/perf/perf-fixture-cost-baseline.md.

Given the session transcripts of one timed fixture-v1 run, emit exactly the
metric set the card's step 6 names, per run:

  spawned-context count per item | per-phase turn counts | verify-retry count |
  merge-time re-validation count | per-phase output tokens | wall minutes |
  resolved model-ID set | claude CLI version

SHARED LINEAGE, NOT A FORK. Event parsing, delivery windowing, subagent parsing
and phase classification all come from benchmarks/perf/mine_sessions.py, loaded
by file path with importlib the way the telemetry probe already loads it, so
this file is cwd-independent and mine_sessions.py is never edited. What is
genuinely new here is only what that miner does not compute: turn counts,
per-item spawn attribution across gate agents as well as builders, verify
retries, and merge-time re-validation.

Two definitions worth stating out loud, because both are narrower than they sound:

  * A **verify retry** is a gate context (acceptance-reviewer or safety-auditor)
    spawned for an item that already had one in the same delivery window — the
    second and later spawns are the retries. Attribution is by item id appearing
    in the spawn's description, the same longest-id-first match mine_sessions
    uses for builders; a gate spawn naming no item lands in an explicit
    unattributed bucket and is never counted as somebody's retry.
  * A **merge-time re-validation** is credited only when the oracle runner
    (skills/karta-build/scripts/run_oracle.py) ran in the main session with no
    intervening item merge, before a `git merge` of a karta item branch. A
    re-validation done by hand-running the oracle command is indistinguishable
    from any other Bash line and is NOT credited — the count is of mechanically
    evidenced re-validations, and both the numerator and the merge denominator
    are emitted so a low ratio reads as "not evidenced", not as "not done".

Usage:
  python3 benchmarks/perf/mine_fixture.py <session.jsonl>... [--binders PATH...] [--out FILE]
  python3 benchmarks/perf/mine_fixture.py --self-test

--self-test validates the miner against the committed fixture transcript at
benchmarks/perf/fixtures/miner-transcript/ for every metric that fixture's
events can support, and against embedded synthetic events for the two it cannot
(verify retries, merge-time re-validation) — each with a negative control that
proves the check can fail. It prints [PASS]/[FAIL] lines and an N/N checks
passed summary, and exits 0 only when the summary is N/N.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MINER_PATH = HERE / "mine_sessions.py"
FIXTURE_REL = Path("benchmarks/perf/fixtures/miner-transcript")

ORACLE_RUNNER_RE = re.compile(r"run_oracle\.py")
ITEM_MERGE_RE = re.compile(r"git\s+(?:-C\s+\S+\s+)?merge\b.*item-")
GATE_PHASES = ("acceptance-reviewer", "safety-auditor")
PHASES = ("orchestrator", "builder", "acceptance-reviewer", "safety-auditor", "other")


def load_mine_sessions():
    """Import the sibling session miner by file path — cwd-independent, and the
    single source of event parsing and phase classification for this file."""
    spec = importlib.util.spec_from_file_location("mine_sessions", MINER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_jsonl(path: Path) -> list[dict]:
    lines = []
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def _assistant_turns(lines: list[dict]) -> int:
    """Turns = assistant messages. mine_sessions parses usage, wall, model and
    tool events off these lines but never counts them, so this is the one small
    thing this miner reads for itself."""
    return sum(1 for line in lines if line.get("type") == "assistant")


def _output_tokens(lines: list[dict]) -> int:
    total = 0
    for line in lines:
        if line.get("type") != "assistant":
            continue
        usage = (line.get("message") or {}).get("usage") or {}
        total += int(usage.get("output_tokens") or 0)
    return total


def _models(lines: list[dict]) -> set[str]:
    out = set()
    for line in lines:
        model = (line.get("message") or {}).get("model")
        if model:
            out.add(str(model))
    return out


def _revalidation_before_merge(bash_commands: list[str]) -> tuple[int, int]:
    """(evidenced re-validations, item merges) over ordered main-session Bash
    commands. An oracle-runner call arms the credit; the next item merge spends
    it. Mirrors mine_sessions._scan_before_commit's freshness shape."""
    evidenced = merges = 0
    fresh = False
    for cmd in bash_commands:
        if ITEM_MERGE_RE.search(cmd):
            merges += 1
            evidenced += int(fresh)
            fresh = False
        elif ORACLE_RUNNER_RE.search(cmd):
            fresh = True
    return evidenced, merges


def mine_run(session_file: Path, binder_paths: list[Path] | None = None) -> dict:
    """One session transcript -> the card's step-6 metric set for that run."""
    ms = load_mine_sessions()
    session_file = Path(session_file)
    binders, _sources = ms._load_binders(binder_paths, session_file.parent)
    item_ids = [str(it["id"]) for b in binders for it in b["work_items"]
                if isinstance(it, dict) and it.get("id")]
    ids_longest_first = sorted(item_ids, key=len, reverse=True)

    lines = _read_jsonl(session_file)
    cc_version = next((str(line["version"]) for line in lines if line.get("version")),
                      "unknown")
    timestamps = [ts for ts in (ms._parse_ts(line.get("timestamp")) for line in lines)
                  if ts is not None]
    session_end = timestamps[-1] if timestamps else None

    events = ms._iter_tool_events(lines)
    deliver_starts = [e["ts"] for e in events if e["kind"] == "deliver"]
    windows = ms._find_windows(events, session_end)

    subagents_dir = session_file.parent / session_file.stem / "subagents"
    meta_by_tool_use: dict[str, dict] = {}
    if subagents_dir.is_dir():
        for meta_file in sorted(subagents_dir.glob("agent-*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            meta["_jsonl"] = meta_file.with_name(meta_file.name.replace(".meta.json", ".jsonl"))
            if meta.get("toolUseId"):
                meta_by_tool_use[meta["toolUseId"]] = meta

    spawns_per_item: Counter[str] = Counter()
    phase_turns: Counter[str] = Counter()
    phase_output_tokens: Counter[str] = Counter()
    gate_spawns_per_item: Counter[str] = Counter()
    models = _models(lines)
    bash_commands: list[str] = []
    wall_minutes = 0.0

    for start, window in zip(deliver_starts, windows):
        if start is not None and window["end_ts"] is not None:
            wall_minutes += (window["end_ts"] - start).total_seconds() / 60
        for event in window["events"]:
            if event["kind"] == "bash":
                bash_commands.append(event["command"])
            if not (event["kind"] == "task" and event.get("id")):
                continue
            meta = meta_by_tool_use.get(event["id"])
            parsed = ms._parse_agent(meta["_jsonl"]) if meta else None
            if parsed is None:  # missing or truncated subagent file
                return {"session": str(session_file), "unmeasurable": True,
                        "claude_cli_version": cc_version}
            phase = ms._classify(str(meta.get("agentType", "")), parsed["first_user_text"])
            description = str(meta.get("description", ""))
            item = next((iid for iid in ids_longest_first if iid in description), None)
            spawns_per_item[item or "unattributed"] += 1
            if phase in GATE_PHASES:
                gate_spawns_per_item[item or "unattributed"] += 1
            agent_lines = _read_jsonl(meta["_jsonl"])
            phase_turns[phase] += _assistant_turns(agent_lines)
            phase_output_tokens[phase] += parsed["output_tokens"]
            models |= _models(agent_lines)

    phase_turns["orchestrator"] = _assistant_turns(lines)
    phase_output_tokens["orchestrator"] = _output_tokens(lines)
    evidenced, merges = _revalidation_before_merge(bash_commands)
    retries = {item: n - 1 for item, n in gate_spawns_per_item.items()
               if item != "unattributed" and n > 1}

    return {
        "session": str(session_file),
        "deliveries": len(windows),
        "spawned_contexts_per_item": dict(sorted(spawns_per_item.items())),
        "phase_turns": {phase: phase_turns.get(phase, 0) for phase in PHASES},
        "verify_retries": {
            "total": sum(retries.values()),
            "per_item": dict(sorted(retries.items())),
            "unattributed_gate_spawns": gate_spawns_per_item.get("unattributed", 0),
        },
        "merge_revalidations": {"evidenced": evidenced, "item_merges": merges},
        "phase_output_tokens": {phase: phase_output_tokens.get(phase, 0) for phase in PHASES},
        "wall_minutes": round(wall_minutes, 2),
        "model_ids": sorted(models),
        "claude_cli_version": cc_version,
    }


def mine(session_files: list[Path], binder_paths: list[Path] | None = None) -> dict:
    return {
        "miner": "benchmarks/perf/mine_fixture.py",
        "shares_lineage_with": "benchmarks/perf/mine_sessions.py",
        "vector": "perf-fixture-cost-baseline",
        "runs": [mine_run(Path(f), binder_paths) for f in session_files],
    }


# ---------------------------------------------------------------- fixture check

# Pinned expectations for the committed miner-transcript fixture. The gate probe
# imports this and fails closed on any mismatch (miner-correctness gating). The
# fixture carries no gate retry and no merge, so those two rows pin the ZERO
# case here and the nonzero case is proved by the synthetic events in --self-test.
EXPECTED_FIXTURE = {
    "deliveries": 1,
    "spawned_contexts_per_item": {"alpha-endpoint": 2, "beta-view": 1,
                                  "gamma-docs": 1, "unattributed": 3},
    "phase_turns": {"orchestrator": 9, "builder": 10, "acceptance-reviewer": 1,
                    "safety-auditor": 1, "other": 1},
    "verify_retries": {"total": 0, "per_item": {}, "unattributed_gate_spawns": 1},
    "merge_revalidations": {"evidenced": 0, "item_merges": 0},
    "phase_output_tokens": {"orchestrator": 0, "builder": 4600,
                            "acceptance-reviewer": 400, "safety-auditor": 200,
                            "other": 100},
    "wall_minutes": 59.83,
    "model_ids": ["model-a", "model-b", "model-c", "model-main"],
    "claude_cli_version": "2.0.0",
}


def check_fixture(fixture_dir: Path, expected: dict | None = None) -> list[tuple[str, bool, str]]:
    """Mine the committed fixture transcript and compare against the pinned
    expectations. Returns [(check_name, ok, detail)]; the gate probe maps any
    mismatch to status "fail" (fail-closed on the miner's own correctness)."""
    exp = expected if expected is not None else EXPECTED_FIXTURE
    fixture_dir = Path(fixture_dir)
    run = mine_run(fixture_dir / "sess-delivery.jsonl",
                   binder_paths=[fixture_dir / "binder.json"])
    checks = [(f"fixture run: {key}", run.get(key) == want, f"got {run.get(key)!r}, expected {want!r}")
              for key, want in exp.items()]
    truncated = mine_run(fixture_dir / "sess-truncated.jsonl",
                         binder_paths=[fixture_dir / "binder.json"])
    checks.append(("truncated session is reported unmeasurable, not silently mined",
                   truncated.get("unmeasurable") is True, str(truncated)[:120]))
    return checks


# ------------------------------------------------------------------- self-test

_SYNTH_META = '{{"agentType":"{agent_type}","description":"{description}",' \
              '"toolUseId":"{tool_use_id}","spawnDepth":1}}'


def _write_synthetic(root: Path, merge_after_oracle: bool) -> Path:
    """A synthetic delivery carrying what the committed fixture cannot: a repeat
    gate dispatch for one item, and item merges around an oracle-runner call.

    `merge_after_oracle=False` is the negative control for the freshness rule —
    the same merge with the oracle-runner call moved to AFTER it, which must
    score zero evidenced re-validations while still counting the merge."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "binder.json").write_text(json.dumps({
        "slug": "synth", "title": "t", "summary": "s", "motivation": "m",
        "scope": {"included": ["src/"]},
        "work_items": [
            {"id": "alpha", "title": "A", "summary": "s",
             "oracle": {"type": "unit", "command": "true"}},
            {"id": "beta", "title": "B", "summary": "s",
             "oracle": {"type": "unit", "command": "true"}},
        ],
    }))

    def assistant(ts: str, block: dict) -> str:
        return json.dumps({"type": "assistant",
                           "message": {"role": "assistant", "model": "model-main",
                                       "content": [block]},
                           "timestamp": ts})

    def task(tid: str, description: str) -> dict:
        return {"type": "tool_use", "id": tid, "name": "Task",
                "input": {"description": description, "prompt": "Read-only gate."}}

    def bash(command: str) -> dict:
        return {"type": "tool_use", "name": "Bash", "input": {"command": command}}

    oracle_cmd = "python3 skills/karta-build/scripts/run_oracle.py 'make test'"
    merges = ["git merge --no-ff karta/synth/item-alpha",
              "git merge --no-ff karta/synth/item-beta"]
    sequence = [oracle_cmd] + merges if merge_after_oracle else merges + [oracle_cmd]
    ordered = [(cmd, f"10:0{6 + n}:00") for n, cmd in enumerate(sequence)]

    rows = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "go"},
                    "timestamp": "2026-07-01T10:00:00.000Z", "version": "9.9.9"}),
        assistant("2026-07-01T10:00:10.000Z",
                  {"type": "tool_use", "id": "s0", "name": "Skill",
                   "input": {"skill": "karta:karta-deliver", "args": "synth"}}),
        assistant("2026-07-01T10:01:00.000Z", task("s1", "Acceptance-review alpha")),
        assistant("2026-07-01T10:02:00.000Z", task("s2", "Acceptance-review alpha again")),
        assistant("2026-07-01T10:03:00.000Z", task("s3", "Safety audit of the wave diff")),
    ]
    rows += [assistant(f"2026-07-01T{clock}.000Z", bash(cmd)) for cmd, clock in ordered]
    rows.append(json.dumps({"type": "user", "message": {"role": "user", "content": "end"},
                            "timestamp": "2026-07-01T10:10:00.000Z"}))
    session = root / "sess-synth.jsonl"
    session.write_text("\n".join(rows) + "\n")

    subagents = root / "sess-synth" / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    agents = [("a1", "s1", "karta:karta-acceptance-reviewer", "Acceptance-review alpha", 100),
              ("a2", "s2", "karta:karta-acceptance-reviewer", "Acceptance-review alpha again", 150),
              ("a3", "s3", "karta-safety-auditor", "Safety audit of the wave diff", 50)]
    for name, tool_use_id, agent_type, description, tokens in agents:
        (subagents / f"agent-{name}.meta.json").write_text(_SYNTH_META.format(
            agent_type=agent_type, description=description, tool_use_id=tool_use_id))
        (subagents / f"agent-{name}.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "gate"},
                        "timestamp": "2026-07-01T10:01:10.000Z"}),
            json.dumps({"type": "assistant",
                        "message": {"role": "assistant", "model": "model-gate",
                                    "content": [{"type": "text", "text": "PASS"}],
                                    "usage": {"output_tokens": tokens}},
                        "timestamp": "2026-07-01T10:01:50.000Z"}),
        ]) + "\n")
    return session


def _run_self_test() -> int:
    repo_root = HERE.parent.parent
    fixture = repo_root / FIXTURE_REL
    results: list[tuple[str, bool, str]] = []

    results += check_fixture(fixture)

    doctored = dict(EXPECTED_FIXTURE, phase_turns=dict(EXPECTED_FIXTURE["phase_turns"],
                                                       builder=99))
    doctored_failures = [name for name, ok, _ in check_fixture(fixture, expected=doctored)
                         if not ok]
    results.append(("a doctored expectation makes the fixture check fail",
                    doctored_failures == ["fixture run: phase_turns"],
                    f"failed checks: {doctored_failures}"))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "synth"
        session = _write_synthetic(root, merge_after_oracle=True)
        run = mine_run(session, binder_paths=[root / "binder.json"])
        results.append(("synthetic: a repeat gate dispatch for one item is one verify retry",
                        run["verify_retries"] == {"total": 1, "per_item": {"alpha": 1},
                                                  "unattributed_gate_spawns": 1},
                        str(run["verify_retries"])))
        results.append(("synthetic: an oracle-runner call before a merge is an evidenced "
                        "re-validation, and both merges are counted",
                        run["merge_revalidations"] == {"evidenced": 1, "item_merges": 2},
                        str(run["merge_revalidations"])))
        results.append(("synthetic: gate spawns land in their own phases with their tokens",
                        run["phase_turns"]["acceptance-reviewer"] == 2
                        and run["phase_turns"]["safety-auditor"] == 1
                        and run["phase_output_tokens"]["acceptance-reviewer"] == 250
                        and run["phase_output_tokens"]["safety-auditor"] == 50,
                        f"{run['phase_turns']} {run['phase_output_tokens']}"))
        results.append(("synthetic: the model set unions main session and subagents",
                        run["model_ids"] == ["model-gate", "model-main"],
                        str(run["model_ids"])))
        results.append(("synthetic: claude CLI version is read off the transcript",
                        run["claude_cli_version"] == "9.9.9", run["claude_cli_version"]))

        control_root = Path(tmp) / "control"
        control = _write_synthetic(control_root, merge_after_oracle=False)
        control_run = mine_run(control, binder_paths=[control_root / "binder.json"])
        results.append(("negative control: an oracle-runner call AFTER the merge evidences "
                        "nothing, while the merges still count",
                        control_run["merge_revalidations"] == {"evidenced": 0, "item_merges": 2},
                        str(control_run["merge_revalidations"])))

    results.append(("the miner reuses mine_sessions rather than forking it",
                    all(hasattr(load_mine_sessions(), name)
                        for name in ("_iter_tool_events", "_find_windows",
                                     "_parse_agent", "_classify", "_load_binders")),
                    "mine_sessions is missing a primitive this miner relies on"))

    failures = 0
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f": {detail}"))
        failures += 0 if ok else 1
    print(f"\n{len(results) - failures}/{len(results)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fixed-fixture per-phase cost miner (step 6).")
    ap.add_argument("sessions", nargs="*", type=Path,
                    help="stream-json session transcripts of one fixture run")
    ap.add_argument("--binders", nargs="*", type=Path, default=None,
                    help="binder JSON files or dirs (default: derived from the "
                         "transcript dir's encoded project path)")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the report JSON here (default stdout)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()
    if not args.sessions:
        ap.error("provide at least one <session.jsonl> or --self-test")
    text = json.dumps(mine(args.sessions, args.binders), indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
