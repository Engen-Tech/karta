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
ITEM_MERGE_RE = re.compile(r"git\s+(?:-C\s+\S+\s+)?merge\b.*item-", re.S)  # -m messages span lines
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


HEADLESS_DELIVER_RE = re.compile(r"<command-name>/(?:karta:)?karta-deliver</command-name>")


def _unspawned_tool_use_ids(lines: list[dict]) -> dict[str, str]:
    """tool_use id -> why no subagent transcript is expected for it: "denied" when
    a PreToolUse hook refused the dispatch, "failed" when the tool itself errored
    (an unknown agent type, say). Either way no context spun up, so the absence
    of a transcript is a fact about the run, not a truncated record."""
    out: dict[str, str] = {}
    for line in lines:
        if line.get("type") != "user":
            continue
        content = (line.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"
                    and block.get("tool_use_id")):
                continue
            body = block.get("content")
            text = body if isinstance(body, str) else json.dumps(body or "")
            if "hook error" in text:
                out[str(block["tool_use_id"])] = "denied"
            elif block.get("is_error") or text.startswith("Agent type "):
                out[str(block["tool_use_id"])] = "failed"
    return out


def _headless_deliver_start(lines: list[dict], ms) -> "datetime | None":
    """The timestamp of the first user line that is a slash-invoked karta-deliver
    (`claude -p '/karta:karta-deliver <slug>'`), else None."""
    for line in lines:
        if line.get("type") != "user":
            continue
        content = (line.get("message") or {}).get("content")
        text = content if isinstance(content, str) else json.dumps(content or "")
        if HEADLESS_DELIVER_RE.search(text):
            return ms._parse_ts(line.get("timestamp"))
    return None


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
    if not windows:
        # The card's step 4 is `claude -p '/karta:karta-deliver bench'`: the skill is
        # invoked from the prompt, so there is no Skill tool event to open a window
        # on. A headless deliver prompt is one delivery spanning the whole session.
        headless_start = _headless_deliver_start(lines, ms)
        if headless_start is not None:
            deliver_starts = [headless_start]
            windows = [{"events": events, "end_ts": session_end}]

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

    unspawned = _unspawned_tool_use_ids(lines)
    spawns_per_item: Counter[str] = Counter()
    phase_turns: Counter[str] = Counter()
    phase_output_tokens: Counter[str] = Counter()
    gate_spawns_per_item: Counter[str] = Counter()
    denied_dispatches: Counter[str] = Counter()
    failed_dispatches: Counter[str] = Counter()
    gate_spawns_per_item_phase: Counter[tuple[str, str]] = Counter()
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
            if meta is None and event["id"] in unspawned:
                # No context spun up: a guard refused it, or the tool errored.
                # Counted, never spawned.
                (denied_dispatches if unspawned[event["id"]] == "denied"
                 else failed_dispatches)["unattributed"] += 1
                continue
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
                gate_spawns_per_item_phase[(item or "unattributed", phase)] += 1
            agent_lines = _read_jsonl(meta["_jsonl"])
            phase_turns[phase] += _assistant_turns(agent_lines)
            phase_output_tokens[phase] += parsed["output_tokens"]
            models |= _models(agent_lines)
            # Gate reviewers are spawned by the builder, one level down: walk the
            # builder's own tool_use blocks, attributing each to the builder's item.
            nested_unspawned = _unspawned_tool_use_ids(agent_lines)
            for nested in ms._iter_tool_events(agent_lines):
                if not (nested["kind"] == "task" and nested.get("id")):
                    continue
                nmeta = meta_by_tool_use.get(nested["id"])
                if nmeta is None and nested["id"] in nested_unspawned:
                    (denied_dispatches if nested_unspawned[nested["id"]] == "denied"
                     else failed_dispatches)[item or "unattributed"] += 1
                    continue
                nparsed = ms._parse_agent(nmeta["_jsonl"]) if nmeta else None
                if nparsed is None:
                    return {"session": str(session_file), "unmeasurable": True,
                            "claude_cli_version": cc_version}
                nphase = ms._classify(str(nmeta.get("agentType", "")), nparsed["first_user_text"])
                spawns_per_item[item or "unattributed"] += 1
                if nphase in GATE_PHASES:
                    gate_spawns_per_item[item or "unattributed"] += 1
                    gate_spawns_per_item_phase[(item or "unattributed", nphase)] += 1
                nlines = _read_jsonl(nmeta["_jsonl"])
                phase_turns[nphase] += _assistant_turns(nlines)
                phase_output_tokens[nphase] += nparsed["output_tokens"]
                models |= _models(nlines)

    phase_turns["orchestrator"] = _assistant_turns(lines)
    phase_output_tokens["orchestrator"] = _output_tokens(lines)
    evidenced, merges = _revalidation_before_merge(bash_commands)
    # A retry is a SECOND spawn of the SAME gate for one item; the acceptance and
    # safety gates are both expected once per item, so they are never each other's retry.
    retries: Counter[str] = Counter()
    for (item, _phase), n in gate_spawns_per_item_phase.items():
        if item != "unattributed" and n > 1:
            retries[item] += n - 1

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
        "denied_dispatches": dict(sorted(denied_dispatches.items())),
        "failed_dispatches": dict(sorted(failed_dispatches.items())),
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


def _write_headless_synthetic(root: Path) -> Path:
    """The card's step-4 shape, which the committed fixture does not carry: a
    headless `claude -p '/karta:karta-deliver <slug>'` session (no Skill tool
    event opens the window), the subagent tool named `Agent` (2.1.x), a gate
    reviewer spawned one level down inside the builder, one gate dispatch refused
    by a PreToolUse guard (a `hook error` tool_result, no transcript), and one
    builder dispatch whose transcript is simply missing."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "binder.json").write_text(json.dumps({
        "slug": "synth", "work_items": [
            {"id": "alpha", "title": "A", "summary": "s",
             "oracle": {"type": "unit", "command": "true"}}]}))

    def assistant(ts: str, block: dict, model: str = "model-main") -> str:
        return json.dumps({"type": "assistant",
                           "message": {"role": "assistant", "model": model,
                                       "content": [block], "usage": {"output_tokens": 10}},
                           "timestamp": ts})

    def agent(tid: str, description: str) -> dict:
        return {"type": "tool_use", "id": tid, "name": "Agent",
                "input": {"description": description, "prompt": "go"}}

    def result(tid: str, text: str, ts: str) -> str:
        return json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": text}]}, "timestamp": ts})

    rows = [
        json.dumps({"type": "user", "message": {"role": "user", "content":
                    "<command-message>karta:karta-deliver</command-message>\n"
                    "<command-name>/karta:karta-deliver</command-name>\n"
                    "<command-args>synth</command-args>"},
                    "timestamp": "2026-07-01T10:00:00.000Z", "version": "9.9.9"}),
        assistant("2026-07-01T10:01:00.000Z", agent("b1", "Build alpha item")),
        result("b1", "built", "2026-07-01T10:05:00.000Z"),
        json.dumps({"type": "user", "message": {"role": "user", "content": "end"},
                    "timestamp": "2026-07-01T10:06:00.000Z"}),
    ]
    session = root / "sess-headless.jsonl"
    session.write_text("\n".join(rows) + "\n")
    subagents = root / "sess-headless" / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    # the builder: one refused gate dispatch, then the real one, nested one level down
    (subagents / "agent-b1.meta.json").write_text(_SYNTH_META.format(
        agent_type="general-purpose", description="Build alpha item", tool_use_id="b1"))
    (subagents / "agent-b1.jsonl").write_text("\n".join([
        json.dumps({"type": "user", "message": {"role": "user", "content":
                    "Invoke the karta-build skill for item alpha"},
                    "timestamp": "2026-07-01T10:01:10.000Z"}),
        assistant("2026-07-01T10:02:00.000Z", agent("g0", "Acceptance gate for alpha item"), "model-build"),
        result("g0", "PreToolUse:Agent hook error: karta: this gate-reviewer dispatch names "
                     "an empty diff", "2026-07-01T10:02:05.000Z"),
        assistant("2026-07-01T10:03:00.000Z", agent("g1", "Acceptance gate for alpha item"), "model-build"),
        result("g1", "CONFORMANT", "2026-07-01T10:04:00.000Z"),
    ]) + "\n")
    (subagents / "agent-g1.meta.json").write_text(_SYNTH_META.format(
        agent_type="karta:karta-acceptance-reviewer", description="Acceptance gate for alpha item",
        tool_use_id="g1"))
    (subagents / "agent-g1.jsonl").write_text("\n".join([
        json.dumps({"type": "user", "message": {"role": "user", "content": "gate"},
                    "timestamp": "2026-07-01T10:03:10.000Z"}),
        assistant("2026-07-01T10:03:50.000Z", {"type": "text", "text": "CONFORMANT"}, "model-gate"),
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

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "headless"
        session = _write_headless_synthetic(root)
        run = mine_run(session, binder_paths=[root / "binder.json"])
        results.append(("headless: a slash-invoked karta-deliver prompt opens the delivery "
                        "window with no Skill tool event",
                        run.get("deliveries") == 1 and run.get("wall_minutes") == 6.0,
                        f"{run.get('deliveries')} {run.get('wall_minutes')}"))
        results.append(("headless: the `Agent` tool name is the subagent tool, and a gate "
                        "spawned inside the builder is attributed to the builder's item",
                        run.get("spawned_contexts_per_item") == {"alpha": 2}
                        and run.get("phase_turns", {}).get("acceptance-reviewer") == 1
                        and run.get("phase_turns", {}).get("builder") == 2,
                        f"{run.get('spawned_contexts_per_item')} {run.get('phase_turns')}"))
        results.append(("headless: a dispatch a PreToolUse guard refused is counted as denied, "
                        "not spawned and not a retry",
                        run.get("denied_dispatches") == {"alpha": 1}
                        and run.get("verify_retries", {}).get("total") == 0,
                        f"{run.get('denied_dispatches')} {run.get('verify_retries')}"))
        # negative control: the same session with the gate transcript deleted is
        # unmeasurable — a missing file is never read as a denial.
        (root / "sess-headless" / "subagents" / "agent-g1.jsonl").unlink()
        (root / "sess-headless" / "subagents" / "agent-g1.meta.json").unlink()
        broken = mine_run(session, binder_paths=[root / "binder.json"])
        results.append(("headless negative control: a spawned subagent whose transcript is "
                        "missing makes the run unmeasurable, not a denial",
                        broken.get("unmeasurable") is True, str(broken)[:120]))

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
