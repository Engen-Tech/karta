#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Delivery telemetry miner — modes 1 (cost) and 4 (compliance) of
benchmarks/perf/perf-delivery-telemetry.md. Modes 2 (timeline) and 3
(oracle-count) are NOT implemented (the card's own two-pass rollout order).

Pure JSONL parsing over recorded Claude Code session transcripts, no judge:
  python3 benchmarks/perf/mine_sessions.py <transcript-dir>... --since <date> --out <file>
  python3 benchmarks/perf/mine_sessions.py --self-test

Mode 1 (cost): a delivery window opens at a main-session Skill tool_use whose
skill matches /karta[-:]karta-deliver|karta-deliver/ and closes at the next
deliver invocation or session end. Subagents are joined via the sibling
`.meta.json` toolUseId matching an in-window Task tool_use id; the meta carries
NO usage, so tokens are summed from `message.usage` of the agent's own assistant
lines (output_tokens, cache_read_input_tokens), wall = last minus first
timestamp, model = mode of `message.model`. builder = agentType general-purpose
whose first user message contains 'karta-build'; gates = acceptance-reviewer /
safety-auditor agentTypes including karta:-scoped forms; anything else is
'other'. Builders map to binder item ids matched against the meta description;
an unmatched builder lands in an explicit 'unattributed' bucket, never dropped.
Per-item figures are totals over that item's builder spawns; per_item_median is
the median of those per-item totals across mapped items. overhead_ratio =
non-builder output tokens / builder output tokens, numerator and denominator
emitted separately.

Mode 4 (compliance), transcript-counted denominators, measurable sessions only:
  - scan_secrets.py before each git commit observed in a builder transcript
    (builders operate in item worktrees) with no intervening mutation
    (Write/Edit/NotebookEdit, or Bash matching git apply/checkout/merge/revert,
    sed -i, tee, or '>' redirection); denominator = such commits.
  - validate_binder.py before the first worktree-creation Bash in the window;
    denominator = measurable deliveries (a delivery creating no worktree counts
    compliant — nothing was violated).
  - check_shared_terms.py after each wave build (a wave = a maximal run of
    consecutive karta-build Task spawns unbroken by any other tool event),
    counted only when a loaded binder declares non-empty shared_terms;
    denominator = qualifying waves.
  - karta_next.py --footer before session end; denominator = deliver sessions.
Sessions with missing or truncated subagents/ files are reported as an
'unmeasurable' count and excluded from every denominator, never silently
dropped. Any per-session parse or IO error is contained and counted, never
raised. Binder dirs come from --binders <path>... (file or directory), else are
derived as <decoded-project-path>/.karta/binders/ plus archive/ from the
transcript dir's encoded project name.

House self-test contract: --self-test validates the miner against the committed
fixture transcript at benchmarks/perf/fixtures/miner-transcript/ and prints
[PASS]/[FAIL] lines and an N/N checks passed summary; it exits 0 only when the
summary is N/N checks passed, nonzero otherwise. The fixture, not decaying live
transcript dirs, is the correctness anchor; live mining is best-effort evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DELIVER_RE = re.compile(r"karta[-:]karta-deliver|karta-deliver")
MUTATING_BASH_RE = re.compile(r"git (apply|checkout|merge|revert)|sed -i|\btee\b|>")
MUTATING_TOOLS = {"Write", "Edit", "NotebookEdit"}
GENERAL_PURPOSE = "general-purpose"

COMPLIANCE_ROWS = (
    ("scan_secrets.py", "before each git commit in an item worktree with no intervening mutation"),
    ("validate_binder.py", "before the first worktree-creation Bash of the delivery"),
    ("check_shared_terms.py", "after each wave build (binders with non-empty shared_terms only)"),
    ("karta_next.py --footer", "before session end"),
)

# Pinned expectations for the committed fixture transcript. The gate probe
# imports this and fails closed on any mismatch (miner-correctness gating).
EXPECTED_FIXTURE = {
    "context": {
        "project": "miner-transcript",
        "binder_slug": "fixture-binder",
        "plugin_version": "unknown",
        "item_count": 3,
        "estimate_mix": {"S": 1, "M": 1, "L": 1},
        "claude_code_version": "2.0.0",
    },
    "spawns_per_type": {"builder": 4, "acceptance-reviewer": 1, "safety-auditor": 1, "other": 1},
    "per_item": {
        "alpha-endpoint": {"spawns": 1, "output_tokens": 1500, "cache_read_tokens": 30000, "wall_min": 10.0},
        "beta-view": {"spawns": 1, "output_tokens": 2000, "cache_read_tokens": 40000, "wall_min": 20.0},
        "gamma-docs": {"spawns": 1, "output_tokens": 800, "cache_read_tokens": 15000, "wall_min": 8.0},
    },
    "per_item_median": {"output_tokens": 1500, "cache_read_tokens": 30000, "wall_min": 10.0},
    "unattributed": {"spawns": 1, "output_tokens": 300, "cache_read_tokens": 5000, "wall_min": 5.0},
    "overhead_ratio": {"value": 0.1522, "numerator_output_tokens": 700, "denominator_output_tokens": 4600},
    "models_per_agent_type": {
        "builder": "model-a",
        "acceptance-reviewer": "model-c",
        "safety-auditor": "model-c",
        "other": "model-b",
    },
    "compliance": {
        "scan_secrets.py": {"compliant": 1, "denominator": 3},
        "validate_binder.py": {"compliant": 1, "denominator": 1},
        "check_shared_terms.py": {"compliant": 1, "denominator": 2},
        "karta_next.py --footer": {"compliant": 1, "denominator": 1},
    },
    "unmeasurable_sessions": 1,
    "deliveries_measurable": 1,
}


def encode_project_path(path: Path | str) -> str:
    """Encode a repo path the way Claude Code names its per-project transcript dir."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(path))


def decode_project_dir(name: str) -> Path | None:
    """Best-effort inverse of encode_project_path via filesystem walk.

    '-' in the encoded name may have been '/', '.', '_' or a literal '-'; try
    component splits depth-first, preferring '/', and return the first existing
    directory. None when nothing on disk matches (callers treat as unknown)."""
    if not name.startswith("-"):
        return None
    tokens = name[1:].split("-")
    if not tokens or len(tokens) > 24:
        return None

    def dfs(cur: str, i: int) -> str | None:
        if i == len(tokens):
            return cur if Path(cur).is_dir() else None
        if Path(cur).is_dir():
            hit = dfs(cur + "/" + tokens[i], i + 1)
            if hit:
                return hit
        for sep in ("-", ".", "_"):
            hit = dfs(cur + sep + tokens[i], i + 1)
            if hit:
                return hit
        return None

    try:
        hit = dfs("/" + tokens[0], 1)
    except OSError:
        return None
    return Path(hit) if hit else None


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_binders(binder_paths: list[Path] | None, transcript_dir: Path) -> tuple[list[dict], list[Path]]:
    """Load binder JSONs from explicit paths (file or dir) or derive from the
    transcript dir's encoded project name. Returns (binders, source_paths)."""
    files: list[Path] = []
    if binder_paths:
        for p in binder_paths:
            p = Path(p)
            if p.is_dir():
                files += sorted(p.glob("*.json")) + sorted((p / "archive").glob("*.json"))
            elif p.is_file():
                files.append(p)
    else:
        repo = decode_project_dir(transcript_dir.name)
        if repo is not None:
            base = repo / ".karta" / "binders"
            files += sorted(base.glob("*.json")) + sorted((base / "archive").glob("*.json"))
    binders, sources = [], []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("work_items"), list):
            binders.append(data)
            sources.append(f)
    return binders, sources


def _plugin_version(binder_sources: list[Path]) -> str:
    """Version of the plugin repo a binder belongs to (.karta/binders/ layout only)."""
    for src in binder_sources:
        parent = src.parent
        if parent.name == "archive":
            parent = parent.parent
        if parent.name == "binders" and parent.parent.name == ".karta":
            manifest = parent.parent.parent / ".claude-plugin" / "plugin.json"
            try:
                version = json.loads(manifest.read_text()).get("version")
                if version:
                    return str(version)
            except (OSError, json.JSONDecodeError):
                pass
    return "unknown"


def _iter_tool_events(lines: list[dict]) -> list[dict]:
    """Flatten assistant tool_use blocks into ordered events:
    {ts, kind: deliver|task|bash|tool, id, command, karta_build}."""
    events: list[dict] = []
    for line in lines:
        if line.get("type") != "assistant":
            continue
        ts = _parse_ts(line.get("timestamp"))
        content = (line.get("message") or {}).get("content")
        if ts is None or not isinstance(content, list):
            continue
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            name = block.get("name")
            inp = block.get("input") or {}
            if name == "Skill" and DELIVER_RE.search(str(inp.get("skill", ""))):
                events.append({"ts": ts, "kind": "deliver"})
            elif name == "Task":
                text = f"{inp.get('description', '')} {inp.get('prompt', '')}"
                events.append({"ts": ts, "kind": "task", "id": block.get("id"),
                               "karta_build": "karta-build" in text})
            elif name == "Bash":
                events.append({"ts": ts, "kind": "bash", "command": str(inp.get("command", ""))})
            else:
                events.append({"ts": ts, "kind": "tool", "name": name})
    return events


def _find_windows(events: list[dict], session_end: datetime | None) -> list[dict]:
    """Delivery windows: from each deliver event to the next one or session end."""
    starts = [i for i, e in enumerate(events) if e["kind"] == "deliver"]
    windows = []
    for n, i in enumerate(starts):
        j = starts[n + 1] if n + 1 < len(starts) else len(events)
        end_ts = events[starts[n + 1]]["ts"] if n + 1 < len(starts) else session_end
        windows.append({"events": events[i + 1:j], "end_ts": end_ts})
    return windows


def _parse_agent(jsonl_path: Path) -> dict | None:
    """One subagent transcript -> usage totals, wall, model, first user text,
    ordered internal tool events (for the scan-before-commit row). None when the
    file is missing or truncated past use (no timestamped lines)."""
    try:
        raw = jsonl_path.read_text(errors="replace")
    except OSError:
        return None
    first_ts = last_ts = None
    output_tokens = cache_read = 0
    models: Counter[str] = Counter()
    first_user_text = None
    tool_events: list[tuple[datetime, str, str]] = []  # (ts, tool_name, command)
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            line = json.loads(ln)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(line.get("timestamp"))
        if ts is not None:
            first_ts = first_ts or ts
            last_ts = ts
        msg = line.get("message") or {}
        if line.get("type") == "user" and first_user_text is None:
            content = msg.get("content")
            if isinstance(content, str):
                first_user_text = content
            elif isinstance(content, list):
                first_user_text = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict))
        if line.get("type") != "assistant":
            continue
        usage = msg.get("usage") or {}
        output_tokens += int(usage.get("output_tokens") or 0)
        cache_read += int(usage.get("cache_read_input_tokens") or 0)
        if msg.get("model"):
            models[msg["model"]] += 1
        content = msg.get("content")
        if ts is not None and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    cmd = str((block.get("input") or {}).get("command", ""))
                    tool_events.append((ts, block.get("name") or "", cmd))
    if first_ts is None or last_ts is None:
        return None
    return {
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "wall_min": round((last_ts - first_ts).total_seconds() / 60, 2),
        "model": models.most_common(1)[0][0] if models else None,
        "first_user_text": first_user_text or "",
        "tool_events": tool_events,
    }


def _classify(agent_type: str, first_user_text: str) -> str:
    bare = agent_type.split(":", 1)[-1]  # karta:-scoped forms normalize to the bare name
    if "acceptance-reviewer" in bare:
        return "acceptance-reviewer"
    if "safety-auditor" in bare:
        return "safety-auditor"
    if bare == GENERAL_PURPOSE and "karta-build" in first_user_text:
        return "builder"
    return "other"


def _scan_before_commit(tool_events: list[tuple[datetime, str, str]]) -> tuple[int, int]:
    """(compliant, commits) over one builder transcript's ordered tool events."""
    compliant = commits = 0
    scan_fresh = False
    for _ts, name, cmd in tool_events:
        if name in MUTATING_TOOLS:
            scan_fresh = False
        elif name == "Bash":
            if "scan_secrets" in cmd:
                scan_fresh = True
            elif "git commit" in cmd:
                commits += 1
                compliant += int(scan_fresh)
            elif MUTATING_BASH_RE.search(cmd):
                scan_fresh = False
    return compliant, commits


def _waves(window_events: list[dict]) -> list[dict]:
    """Maximal runs of consecutive karta-build Task spawns, broken by any other
    tool event. Returns [{'end_ts': ts-of-last-spawn}]."""
    waves, run_end = [], None
    for e in window_events:
        if e["kind"] == "task" and e.get("karta_build"):
            run_end = e["ts"]
        else:
            if run_end is not None:
                waves.append({"end_ts": run_end})
                run_end = None
    if run_end is not None:
        waves.append({"end_ts": run_end})
    return waves


def _mine_session(session_file: Path, binders: list[dict], item_ids: list[str]) -> dict:
    """One main-session file -> {unmeasurable | windows, agents, compliance}."""
    raw = session_file.read_text(errors="replace")
    if "karta-deliver" not in raw:  # cheap pre-filter: no deliver, nothing to mine
        return {"windows": 0, "agents": [], "compliance": None, "cc_version": None}
    lines = []
    cc_version = None
    session_end = None
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            line = json.loads(ln)
        except json.JSONDecodeError:
            continue
        lines.append(line)
        if cc_version is None and line.get("version"):
            cc_version = str(line["version"])
        ts = _parse_ts(line.get("timestamp"))
        if ts is not None:
            session_end = ts
    events = _iter_tool_events(lines)
    windows = _find_windows(events, session_end)
    if not windows:
        return {"windows": 0, "agents": [], "compliance": None, "cc_version": cc_version}

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

    agents = []
    comp = {name: [0, 0] for name, _rule in COMPLIANCE_ROWS}  # [compliant, denominator]
    qualifying = any(b.get("shared_terms") for b in binders)
    ids_longest_first = sorted(item_ids, key=len, reverse=True)

    for window in windows:
        wevents = window["events"]
        spawns = [e for e in wevents if e["kind"] == "task" and e.get("id")]
        for spawn in spawns:
            meta = meta_by_tool_use.get(spawn["id"])
            parsed = _parse_agent(meta["_jsonl"]) if meta else None
            if parsed is None:  # missing or truncated subagent file: whole session is unmeasurable
                return {"unmeasurable": True, "cc_version": cc_version}
            kind = _classify(str(meta.get("agentType", "")), parsed["first_user_text"])
            item = None
            if kind == "builder":
                desc = str(meta.get("description", ""))
                item = next((iid for iid in ids_longest_first if iid in desc), None)
            agents.append({"type": kind, "item": item, **parsed})

        bashes = [e for e in wevents if e["kind"] == "bash"]
        # validate_binder before the first worktree-creation Bash
        comp["validate_binder.py"][1] += 1
        first_wt = next((e for e in bashes if "git worktree add" in e["command"]), None)
        if first_wt is None or any(
                "validate_binder" in e["command"] for e in bashes if e["ts"] <= first_wt["ts"]
                and e is not first_wt):
            comp["validate_binder.py"][0] += 1
        # karta_next --footer before session end
        comp["karta_next.py --footer"][1] += 1
        if any("karta_next" in e["command"] and "--footer" in e["command"] for e in bashes):
            comp["karta_next.py --footer"][0] += 1
        # check_shared_terms after each qualifying wave
        if qualifying:
            waves = _waves(wevents)
            for n, wave in enumerate(waves):
                boundary = waves[n + 1]["end_ts"] if n + 1 < len(waves) else None
                comp["check_shared_terms.py"][1] += 1
                hit = any(
                    "check_shared_terms" in e["command"] and e["ts"] > wave["end_ts"]
                    and (boundary is None or e["ts"] < boundary)
                    for e in bashes)
                comp["check_shared_terms.py"][0] += int(hit)

    # scan-before-commit over builder transcripts (builders operate in item worktrees)
    for agent in agents:
        if agent["type"] == "builder":
            ok, commits = _scan_before_commit(agent["tool_events"])
            comp["scan_secrets.py"][0] += ok
            comp["scan_secrets.py"][1] += commits

    return {"windows": len(windows), "agents": agents, "compliance": comp,
            "cc_version": cc_version}


def _aggregate(agents: list[dict]) -> dict:
    spawns_per_type: Counter[str] = Counter(a["type"] for a in agents)
    per_item: dict[str, dict] = {}
    unattributed = {"spawns": 0, "output_tokens": 0, "cache_read_tokens": 0, "wall_min": 0.0}
    models: dict[str, Counter] = {}
    for a in agents:
        models.setdefault(a["type"], Counter())
        if a["model"]:
            models[a["type"]][a["model"]] += 1
        if a["type"] != "builder":
            continue
        if a["item"] is None:
            unattributed["spawns"] += 1
            unattributed["output_tokens"] += a["output_tokens"]
            unattributed["cache_read_tokens"] += a["cache_read_tokens"]
            unattributed["wall_min"] = round(unattributed["wall_min"] + a["wall_min"], 2)
            continue
        row = per_item.setdefault(a["item"], {"spawns": 0, "output_tokens": 0,
                                              "cache_read_tokens": 0, "wall_min": 0.0})
        row["spawns"] += 1
        row["output_tokens"] += a["output_tokens"]
        row["cache_read_tokens"] += a["cache_read_tokens"]
        row["wall_min"] = round(row["wall_min"] + a["wall_min"], 2)
    numerator = sum(a["output_tokens"] for a in agents if a["type"] != "builder")
    denominator = sum(a["output_tokens"] for a in agents if a["type"] == "builder")
    median = {}
    if per_item:
        for key in ("output_tokens", "cache_read_tokens", "wall_min"):
            median[key] = statistics.median(row[key] for row in per_item.values())
    return {
        "spawns_per_type": dict(spawns_per_type),
        "per_item": per_item,
        "per_item_median": median,
        "unattributed": unattributed,
        "overhead_ratio": {
            "value": round(numerator / denominator, 4) if denominator else None,
            "numerator_output_tokens": numerator,
            "denominator_output_tokens": denominator,
        },
        "models_per_agent_type": {
            kind: counter.most_common(1)[0][0]
            for kind, counter in models.items() if counter
        },
    }


def mine_dir(transcript_dir: Path, since: datetime | None = None,
             binder_paths: list[Path] | None = None,
             deadline: float | None = None) -> dict:
    """Mine one transcript dir. Every per-session error is contained and counted."""
    transcript_dir = Path(transcript_dir)
    binders, sources = _load_binders(binder_paths, transcript_dir)
    item_ids, estimate_mix = [], Counter()
    for b in binders:
        for it in b["work_items"]:
            if isinstance(it, dict) and it.get("id"):
                item_ids.append(str(it["id"]))
                estimate_mix[str(it.get("estimate", "?"))] += 1
    decoded = decode_project_dir(transcript_dir.name)
    project = decoded.name if decoded is not None else transcript_dir.name

    stats = {"sessions_scanned": 0, "deliveries_measurable": 0, "unmeasurable_sessions": 0,
             "sessions_errored": 0, "sessions_skipped_budget": 0}
    all_agents: list[dict] = []
    comp_total = {name: [0, 0] for name, _rule in COMPLIANCE_ROWS}
    cc_version = None

    for session_file in sorted(transcript_dir.glob("*.jsonl")):
        if deadline is not None and time.monotonic() > deadline:
            stats["sessions_skipped_budget"] += 1
            continue
        try:
            if since is not None:
                mtime = datetime.fromtimestamp(session_file.stat().st_mtime, tz=timezone.utc)
                if mtime < since:
                    continue
            stats["sessions_scanned"] += 1
            mined = _mine_session(session_file, binders, item_ids)
            cc_version = cc_version or mined.get("cc_version")
            if mined.get("unmeasurable"):
                stats["unmeasurable_sessions"] += 1
                continue
            if not mined["windows"]:
                continue
            stats["deliveries_measurable"] += mined["windows"]
            all_agents += mined["agents"]
            for name, (ok, denom) in mined["compliance"].items():
                comp_total[name][0] += ok
                comp_total[name][1] += denom
        except Exception:  # error-contained by contract: count, never crash
            stats["sessions_errored"] += 1

    cost = _aggregate(all_agents)
    return {
        "dir": str(transcript_dir),
        "project": project,
        "context": {
            "project": project,
            "binder_slug": ",".join(b.get("slug", "?") for b in binders) or "unknown",
            "plugin_version": _plugin_version(sources),
            "item_count": len(item_ids),
            "estimate_mix": dict(estimate_mix),
            "models_per_agent_type": cost["models_per_agent_type"],
            "claude_code_version": cc_version or "unknown",
        },
        "mode_cost": {"deliveries_measurable": stats["deliveries_measurable"], **cost},
        "mode_compliance": {
            "rows": [
                {"script": name, "rule": rule,
                 "compliant": comp_total[name][0], "denominator": comp_total[name][1]}
                for name, rule in COMPLIANCE_ROWS
            ],
            "unmeasurable_sessions": stats["unmeasurable_sessions"],
        },
        **stats,
    }


def mine(transcript_dirs: list[Path], since: datetime | None = None,
         binder_paths: list[Path] | None = None, budget_s: float | None = None) -> dict:
    deadline = time.monotonic() + budget_s if budget_s else None
    return {
        "miner": "benchmarks/perf/mine_sessions.py",
        "modes": ["cost", "compliance"],
        "since": since.date().isoformat() if since else None,
        "reports": [mine_dir(d, since, binder_paths, deadline) for d in transcript_dirs],
    }


# ---------------------------------------------------------------- fixture check

FIXTURE_REL = Path("benchmarks/perf/fixtures/miner-transcript")


def check_fixture(fixture_dir: Path, expected: dict | None = None) -> list[tuple[str, bool, str]]:
    """Run the miner on the committed fixture and compare against the pinned
    expectations. Returns [(check_name, ok, detail)]; the gate probe maps any
    mismatch to status \"fail\" (fail-closed on the miner's own correctness)."""
    exp = expected if expected is not None else EXPECTED_FIXTURE
    fixture_dir = Path(fixture_dir)
    report = mine([fixture_dir], binder_paths=[fixture_dir / "binder.json"])["reports"][0]
    cost, comp = report["mode_cost"], report["mode_compliance"]
    comp_rows = {r["script"]: {"compliant": r["compliant"], "denominator": r["denominator"]}
                 for r in comp["rows"]}
    got_context = {k: report["context"].get(k) for k in exp["context"]}

    checks: list[tuple[str, bool, str]] = []

    def add(name: str, got, want) -> None:
        checks.append((name, got == want, f"got {got!r}, expected {want!r}"))

    add("context block", got_context, exp["context"])
    add("spawns per agentType", cost["spawns_per_type"], exp["spawns_per_type"])
    for iid, want in exp["per_item"].items():
        add(f"per-item totals: {iid}", cost["per_item"].get(iid), want)
    add("no extra mapped items", sorted(cost["per_item"]), sorted(exp["per_item"]))
    add("per-item medians", cost["per_item_median"], exp["per_item_median"])
    add("unattributed bucket", cost["unattributed"], exp["unattributed"])
    add("overhead ratio (num/den separate)", cost["overhead_ratio"], exp["overhead_ratio"])
    add("models per agentType", cost["models_per_agent_type"], exp["models_per_agent_type"])
    add("context models mirror mode-1", report["context"]["models_per_agent_type"],
        exp["models_per_agent_type"])
    for script, want in exp["compliance"].items():
        add(f"compliance row: {script}", comp_rows.get(script), want)
    add("unmeasurable sessions counted", comp["unmeasurable_sessions"], exp["unmeasurable_sessions"])
    add("measurable deliveries", cost["deliveries_measurable"], exp["deliveries_measurable"])
    add("modes 2/3 absent from output",
        [k for k in report if k.startswith("mode_")], ["mode_cost", "mode_compliance"])
    return checks


def _run_self_test() -> int:
    fixture = Path(__file__).resolve().parent / "fixtures" / "miner-transcript"
    checks = check_fixture(fixture)
    failures = 0
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f": {detail}"))
        failures += 0 if ok else 1
    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Delivery telemetry miner (modes 1+4).")
    ap.add_argument("transcript_dirs", nargs="*", type=Path,
                    help="Claude Code per-project transcript dirs")
    ap.add_argument("--since", type=str, default=None, metavar="YYYY-MM-DD",
                    help="skip session files last modified before this date")
    ap.add_argument("--out", type=Path, default=None, help="write report JSON here (default stdout)")
    ap.add_argument("--binders", nargs="*", type=Path, default=None,
                    help="binder JSON files or dirs (default: derived from each "
                         "transcript dir's encoded project path)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()
    if not args.transcript_dirs:
        ap.error("provide at least one <transcript-dir> or --self-test")
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    report = mine(args.transcript_dirs, since, args.binders)
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
