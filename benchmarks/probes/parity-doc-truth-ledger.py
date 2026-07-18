#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for parity-doc-truth-ledger: docs' numbers, links, and promises vs repo truth.

Implements lanes B-G of benchmarks/parity/parity-doc-truth-ledger.md in one stdlib
runner (the card's procedure name doc_truth.py is superseded by this frontmatter/gate
name — card erratum, see benchmarks/meta/card-errata-2026-07-17.md):

  B  count claims: every `<number> [hyphenated-adjectives] agents|skills|gate agents`
     hit in README.md, docs/how-to/*.md, and .claude-plugin/marketplace.json must bind
     to a committed benchmarks/claims.yaml entry {id, file, line-regex, expect};
     verdicts: unadjudicated-claim, failed-expect, stale-entry. claims.yaml is written
     in the JSON subset of YAML so this stdlib probe parses it with json (card names
     .yaml; stdlib has no YAML parser — recorded card erratum).
  C  stale major-version pins: `Karta X.Y` with X < current major, outside the
     archival allowlist (docs/showcase|docs/releases|docs/specs).
  D  skill discoverability: every skills/ dir (minus _shared) named in README.md or
     docs/how-to/*.md.
  E  claim ledger (from claims.yaml "ledger"): DG-SCHEMA, KAIZEN-USERREPO,
     HOOKS-MATCHER-PARITY — promised-but-unexecuted checks surface as violations.
  F  relative-link existence in README.md + docs/how-to/*.md.
  G  phase-label root registry (docs/conventions/phase-labels.md Roots table):
     unregistered roots, wrong doc-path rows, duplicate (doc,leaf) pairs.

Truth vars are recomputed each run (N_agents, N_skills, writer split, plugin version).
The results file benchmarks/findings/doc-truth-<version>.json is re-emitted on every
run and is byte-deterministic (no timestamps, run dates, or shas). The metric is the
per-lane vector, never a scalar sum; retired claims/ledger entries are counted visibly.

Gate stance: emits the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}
on stdout and exits 0 whether pass or fail (nonzero exit means the probe itself
crashed) — card step 1's nonzero-exit-on-any-violation is superseded by step 10's
regression-only gate and run_gate.py's contract (recorded card erratum). The seeded
violations are the baseline product — any lane count above its last committed value:
status "fail" only on regression against the last committed results file
and the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file
(content is read from HEAD, so a same-run rewrite of the tracked path never grades
itself). With no committed baseline the run records the baseline and passes — but
fails closed if a seeded finding is absent from its own first baseline.

Usage:
  python3 benchmarks/probes/parity-doc-truth-ledger.py --target <repo-root>
  python3 benchmarks/probes/parity-doc-truth-ledger.py --self-test

--self-test drives every lane over fixtures synthesized at runtime (no committed
fixture files), printing [PASS]/[FAIL] lines and an N/N checks passed summary;
it exits 0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_ID = "parity-doc-truth-ledger"
LANES = ("B", "C", "D", "E", "F", "G")
IMPLEMENTED_CHECKS = [
    "lane-B-count-claims",
    "lane-C-version-pins",
    "lane-D-skill-discoverability",
    "lane-E-claim-ledger",
    "lane-F-relative-links",
    "lane-G-phase-labels",
]

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
             "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# Card step 3's regex is illustrative; the seed observation is normative: tolerate up
# to three intervening hyphenated-adjective tokens between the number and the noun.
COUNT_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"((?:\s+[A-Za-z]+(?:-[A-Za-z]+)+){0,3})"
    r"\s+(gate\s+agents|agents|skills)\b",
    re.IGNORECASE,
)
PIN_RE = re.compile(r"\bKarta (\d+)\.(\d+)\b")
PIN_ALLOWLIST = ("docs/showcase/", "docs/releases/", "docs/specs/")
TRAILING_LABEL_RE = re.compile(r"  `([a-z]+:[a-z]+(?::[a-z]+)?)`\s*$")
INLINE_LABEL_RE = re.compile(r"`([a-z]+:[a-z]+(?::[a-z]+)?)`")
ROOTS_ROW_RE = re.compile(r"^\|`([a-z]+)`\|`([^`]+)`\|\s*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TOOL_NAMES = {"Write", "Edit", "NotebookEdit", "Task", "Agent", "Read",
              "Bash", "Grep", "Glob", "WebFetch", "WebSearch"}
SKIP_DIRS = {".git", ".worktrees", "__pycache__", "node_modules"}

# The seeded findings this vector's first committed baseline must contain (work-item
# contract, bench-probe-buildout). Absence from the first baseline fails closed.
SEEDED_FINDING_IDS = frozenset({
    "B:failed-expect:claude-code-three-agents-intro",
    "B:failed-expect:claude-code-three-agents-registers",
    "B:failed-expect:marketplace-two-gate-agents-plugin-desc",
    "B:failed-expect:marketplace-two-gate-agents-detail-desc",
    "B:failed-expect:readme-five-skills-heading",
    "B:failed-expect:readme-two-agents-heading",
    "C:stale-pin:docs/how-to/codex.md:1.19:1",
    "C:stale-pin:docs/how-to/codex.md:1.19:2",
    "D:undiscoverable:karta-status",
    "E:ledger:DG-SCHEMA",
    "E:ledger:KAIZEN-USERREPO",
    "G:unregistered-root:docgardner",
    "G:unregistered-root:kaizen",
})


def _v(finding_id: str, summary: str, severity: str = "violation") -> dict:
    return {"finding_id": finding_id, "severity": severity, "summary": summary}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _lane_b_surfaces(root: Path) -> list[Path]:
    files = [root / "README.md"]
    files += sorted((root / "docs" / "how-to").glob("*.md"))
    files.append(root / ".claude-plugin" / "marketplace.json")
    return [f for f in files if f.is_file()]


def _to_int(token: str) -> int:
    return NUM_WORDS.get(token.lower()) if token.lower() in NUM_WORDS else int(token)


def _resolve_expect(expect: object, truth: dict) -> int | None:
    if isinstance(expect, bool):
        return None
    if isinstance(expect, int):
        return expect
    if isinstance(expect, str):
        val = truth.get(expect)
        return val if isinstance(val, int) else None
    return None


def compute_truth(root: Path) -> dict:
    agents = sorted((root / "agents").glob("*.md")) if (root / "agents").is_dir() else []
    writers = 0
    for agent in agents:
        m = re.search(r"^tools:\s*(.+)$", _read(agent), re.MULTILINE)
        if m and re.search(r"\b(Write|Edit)\b", m.group(1)):
            writers += 1
    skills_dir = root / "skills"
    n_skills = (sum(1 for d in skills_dir.iterdir() if d.is_dir() and d.name != "_shared")
                if skills_dir.is_dir() else 0)
    try:
        version = json.loads(_read(root / ".claude-plugin" / "plugin.json")).get("version", "unknown")
    except (OSError, json.JSONDecodeError):
        version = "unknown"
    return {"N_agents": len(agents), "N_skills": n_skills, "writer_agents": writers,
            "readonly_agents": len(agents) - writers, "plugin_version": version}


def lane_b(root: Path, claims: list[dict], truth: dict) -> list[dict]:
    violations: list[dict] = []
    active = [c for c in claims if "retired" not in c]
    for f in _lane_b_surfaces(root):
        rel = f.relative_to(root).as_posix()
        entries = [c for c in active if c.get("file") == rel]
        occurrences: dict[str, int] = {}
        for line in _read(f).splitlines():
            for m in COUNT_RE.finditer(line):
                number = _to_int(m.group(1))
                bound = next((c for c in entries if re.search(c.get("line-regex", ""), line)), None)
                if bound is None:
                    noun = re.sub(r"\s+", " ", m.group(3).lower())
                    key = f"{rel}:{number}-{noun}"
                    occurrences[key] = occurrences.get(key, 0) + 1
                    violations.append(_v(
                        f"B:unadjudicated:{key}:{occurrences[key]}",
                        f"unadjudicated count claim '{m.group(0)}' in {rel} "
                        f"has no benchmarks/claims.yaml entry"))
                    continue
                expected = _resolve_expect(bound.get("expect"), truth)
                if expected is None:
                    violations.append(_v(
                        f"B:bad-expect:{bound.get('id', '?')}",
                        f"claims entry {bound.get('id', '?')} has an unresolvable "
                        f"expect {bound.get('expect')!r}"))
                elif number != expected:
                    violations.append(_v(
                        f"B:failed-expect:{bound.get('id', '?')}",
                        f"claim '{m.group(0)}' in {rel} states {number}, "
                        f"truth is {expected}"))
    for c in active:
        path = root / c.get("file", "")
        regex = c.get("line-regex", "")
        if not path.is_file() or not any(re.search(regex, line) for line in _read(path).splitlines()):
            violations.append(_v(
                f"B:stale-entry:{c.get('id', '?')}",
                f"claims entry {c.get('id', '?')}: line-regex no longer matches "
                f"any line of {c.get('file', '?')}"))
    return _dedupe(violations)


def lane_c(root: Path, truth: dict) -> list[dict]:
    try:
        current_major = int(str(truth.get("plugin_version", "")).split(".")[0])
    except ValueError:
        return [_v("C:bad-version",
                   f"plugin version {truth.get('plugin_version')!r} is unparsable",
                   severity="error")]
    files = [root / "README.md"] if (root / "README.md").is_file() else []
    docs = root / "docs"
    if docs.is_dir():
        files += sorted(p for p in docs.rglob("*.md")
                        if not p.relative_to(root).as_posix().startswith(PIN_ALLOWLIST))
    violations = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        occurrences: dict[str, int] = {}
        for m in PIN_RE.finditer(_read(f)):
            major, minor = int(m.group(1)), int(m.group(2))
            if major < current_major:
                key = f"{rel}:{major}.{minor}"
                occurrences[key] = occurrences.get(key, 0) + 1
                violations.append(_v(
                    f"C:stale-pin:{key}:{occurrences[key]}",
                    f"stale major-version pin 'Karta {major}.{minor}' in {rel} "
                    f"(current major {current_major})"))
    return violations


def lane_d(root: Path) -> list[dict]:
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return []
    corpus = "".join(_read(f) for f in [root / "README.md"] + sorted((root / "docs" / "how-to").glob("*.md"))
                     if f.is_file())
    return [_v(f"D:undiscoverable:{d.name}",
               f"skill {d.name} appears nowhere in README.md or docs/how-to/*.md")
            for d in sorted(skills_dir.iterdir())
            if d.is_dir() and d.name != "_shared" and d.name not in corpus]


def _py_files(root: Path, scopes: list[str] | None = None) -> list[Path]:
    bases = [root / s for s in scopes] if scopes else [root]
    out = []
    for base in bases:
        if base.is_dir():
            out += [p for p in sorted(base.rglob("*.py"))
                    if not SKIP_DIRS.intersection(p.relative_to(root).parts)]
    return out


def _hooks_parity(root: Path, entry: dict) -> list[dict]:
    eid = entry.get("id", "?")
    doc = root / entry.get("doc", "")
    hooks_path = root / entry.get("hooks_json", "")
    if not doc.is_file() or not hooks_path.is_file():
        return [_v(f"E:ledger:{eid}:missing-input",
                   f"{eid}: {entry.get('doc')} or {entry.get('hooks_json')} is missing")]
    try:
        hooks = json.loads(_read(hooks_path))
    except json.JSONDecodeError as e:
        return [_v(f"E:ledger:{eid}:bad-hooks-json", f"{eid}: hooks.json unparsable ({e})")]
    guard_tools: dict[str, set[str]] = {}
    wired: set[str] = set()
    for groups in hooks.get("hooks", {}).values():
        for group in groups:
            commands = " ".join(h.get("command", "") for h in group.get("hooks", []))
            for guard in re.findall(r"([\w.-]+\.py)", commands):
                wired.add(guard)
                for token in group.get("matcher", "").split("|"):
                    if token and token != "*":
                        guard_tools.setdefault(guard, set()).add(token)
    lines = _read(doc).splitlines()
    violations = []
    for row in entry.get("rows", []):
        guard = row.get("guard", "?")
        line = next((ln for ln in lines
                     if ln.lstrip().startswith("|") and re.search(row.get("rule_regex", ""), ln)), None)
        if line is None:
            violations.append(_v(f"E:ledger:{eid}:row-missing:{guard}",
                                 f"{eid}: no table row matches {row.get('rule_regex')!r} "
                                 f"in {entry.get('doc')}"))
            continue
        if guard not in wired:
            violations.append(_v(f"E:ledger:{eid}:unwired:{guard}",
                                 f"{eid}: doc row claims {guard} but hooks.json never runs it"))
            continue
        claimed = {t for t in re.findall(r"`([^`]+)`", line) if t in TOOL_NAMES}
        actual = guard_tools.get(guard, set())
        if claimed and claimed != actual:
            violations.append(_v(
                f"E:ledger:{eid}:matcher-drift:{guard}",
                f"{eid}: {guard} doc row claims tools {sorted(claimed)} but "
                f"hooks.json matches {sorted(actual)}"))
    return violations


def lane_e(root: Path, ledger: list[dict]) -> list[dict]:
    violations = []
    for entry in (e for e in ledger if "retired" not in e):
        eid, kind = entry.get("id", "?"), entry.get("check")
        if kind == "py-grep":
            pattern = re.compile(entry.get("pattern", "(?!)"))
            count = sum(1 for p in _py_files(root, entry.get("scopes")) if pattern.search(_read(p)))
            if count < int(entry.get("expect_min_files", 1)):
                violations.append(_v(f"E:ledger:{eid}",
                                     f"{eid}: {entry.get('description', '')} "
                                     f"(matching .py files: {count})"))
        elif kind == "py-co-grep":
            patterns = [re.compile(p) for p in entry.get("patterns", [])]
            count = sum(1 for p in _py_files(root, entry.get("scopes"))
                        if patterns and all(rx.search(_read(p)) for rx in patterns))
            if count < int(entry.get("expect_min_files", 1)):
                violations.append(_v(f"E:ledger:{eid}",
                                     f"{eid}: {entry.get('description', '')} "
                                     f"(matching .py files: {count})"))
        elif kind == "hooks-parity":
            violations += _hooks_parity(root, entry)
        else:
            violations.append(_v(f"E:ledger:{eid}:unknown-check",
                                 f"{eid}: unknown ledger check kind {kind!r} — "
                                 f"a promised check that cannot run is a violation"))
    return violations


def lane_f(root: Path) -> list[dict]:
    violations = []
    for f in [root / "README.md"] + sorted((root / "docs" / "how-to").glob("*.md")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        for m in LINK_RE.finditer(_read(f)):
            link = m.group(1)
            if link.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            target = link.split("#")[0]
            if target and not (f.parent / target).exists():
                violations.append(_v(f"F:broken-link:{rel}:{target}",
                                     f"relative link {target} in {rel} does not exist"))
    return _dedupe(violations)


def lane_g(root: Path) -> list[dict]:
    labels_doc = root / "docs" / "conventions" / "phase-labels.md"
    if not labels_doc.is_file():
        return [_v("G:no-registry", "docs/conventions/phase-labels.md is missing",
                   severity="error")]
    table: dict[str, str] = {}
    in_roots = False
    for line in _read(labels_doc).splitlines():
        if line.startswith("## "):
            in_roots = line.strip() == "## Roots"
            continue
        if in_roots:
            m = ROOTS_ROW_RE.match(line.strip())
            if m and m.group(1) != "-":
                table[m.group(1)] = m.group(2)
    defs: list[tuple[str, str, str]] = []  # (doc, root, leaf-path)
    inline_roots: set[str] = set()
    for f in sorted(root.glob("skills/**/*.md")):
        rel = f.relative_to(root).as_posix()
        for line in _read(f).splitlines():
            trailing = TRAILING_LABEL_RE.search(line)
            if trailing:
                r, _, leaf = trailing.group(1).partition(":")
                defs.append((rel, r, leaf))
            for m in INLINE_LABEL_RE.finditer(line):
                inline_roots.add(m.group(1).split(":")[0])
    def_roots = {r for _, r, _ in defs}
    # An inline colon-token counts as a label cross-reference only when its root is
    # registered or defines labels somewhere — `file:line` citation syntax is not one.
    used_roots = def_roots | {r for r in inline_roots if r in table or r in def_roots}
    violations = [_v(f"G:unregistered-root:{r}",
                     f"label root '{r}' is in use but missing from the Roots table")
                  for r in sorted(used_roots - set(table))]
    for r, doc in sorted({(r, doc) for doc, r, _ in defs}):
        if r in table and table[r] != doc:
            violations.append(_v(f"G:wrong-doc-path:{r}:{doc}",
                                 f"root '{r}' labels live in {doc} but the Roots "
                                 f"table says {table[r]}"))
    seen: dict[tuple[str, str], int] = {}
    for doc, _, leaf in defs:
        seen[(doc, leaf)] = seen.get((doc, leaf), 0) + 1
    violations += [_v(f"G:duplicate-leaf:{doc}:{leaf}",
                      f"label leaf '{leaf}' is defined more than once in {doc}")
                   for (doc, leaf), n in sorted(seen.items()) if n > 1]
    return violations


def _dedupe(violations: list[dict]) -> list[dict]:
    out, seen = [], set()
    for v in violations:
        if v["finding_id"] not in seen:
            seen.add(v["finding_id"])
            out.append(v)
    return out


def load_claims(root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (claims, ledger, violations). A missing/unparsable claims file is a
    loud lane-B violation (every count-claim hit would be unadjudicated anyway)."""
    path = root / "benchmarks" / "claims.yaml"
    if not path.is_file():
        return [], [], [_v("B:no-claims-file", "benchmarks/claims.yaml is missing")]
    try:
        data = json.loads(_read(path))  # JSON subset of YAML, parsed with stdlib json
    except json.JSONDecodeError as e:
        return [], [], [_v("B:bad-claims-file",
                           f"benchmarks/claims.yaml is not valid JSON-subset YAML ({e})")]
    return data.get("claims", []), data.get("ledger", []), []


def run_lanes(root: Path) -> tuple[dict, dict]:
    truth = compute_truth(root)
    claims, ledger, claim_errors = load_claims(root)
    lanes = {
        "B": claim_errors + lane_b(root, claims, truth),
        "C": lane_c(root, truth),
        "D": lane_d(root),
        "E": lane_e(root, ledger),
        "F": lane_f(root),
        "G": lane_g(root),
    }
    retired = {"claims": sum(1 for c in claims if "retired" in c),
               "ledger": sum(1 for e in ledger if "retired" in e)}
    results = {
        "id": PROBE_ID,
        "truth": truth,
        "lanes": {lane: {"count": len(v), "violations": sorted(v, key=lambda x: x["finding_id"])}
                  for lane, v in lanes.items()},
        "retired": retired,
    }
    return results, truth


def render_results(results: dict) -> str:
    return json.dumps(results, indent=1, sort_keys=True) + "\n"


def load_baseline(root: Path) -> tuple[dict | None, str | None]:
    """Newest committed doc-truth-*.json, read from HEAD (never the working tree)."""
    try:
        ls = subprocess.run(["git", "-C", str(root), "ls-files", "--",
                             "benchmarks/findings/doc-truth-*.json"],
                            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git ls-files did not run ({e})"
    if ls.returncode != 0:
        return None, f"git ls-files failed ({ls.stderr.strip()})"

    def version_key(path: str) -> tuple[int, ...]:
        m = re.search(r"doc-truth-(\d+)\.(\d+)\.(\d+)\.json$", path)
        return tuple(int(x) for x in m.groups()) if m else (-1, -1, -1)

    for path in sorted((p for p in ls.stdout.split() if p), key=version_key, reverse=True):
        show = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{path}"],
                              capture_output=True, text=True, timeout=30)
        if show.returncode != 0:
            continue  # tracked in the index but not committed: a same-run file, skipped
        try:
            return json.loads(show.stdout), None
        except json.JSONDecodeError:
            return None, f"committed baseline {path} is unparsable"
    return None, None  # no committed baseline: this run records it


def compare_lanes(baseline: dict, results: dict) -> list[dict]:
    regressions = []
    base_lanes = baseline.get("lanes", {})
    for lane in LANES:
        base = base_lanes.get(lane, {}).get("count", 0)
        live = results["lanes"][lane]["count"]
        if live > base:
            regressions.append(_v(f"REGRESSION:lane-{lane}",
                                  f"lane {lane} regressed: {base} -> {live} violations",
                                  severity="regression"))
    return regressions


def missing_seeds(results: dict) -> list[str]:
    live_ids = {v["finding_id"] for lane in results["lanes"].values()
                for v in lane["violations"]}
    return sorted(SEEDED_FINDING_IDS - live_ids)


def run_live(target: Path) -> dict:
    results, truth = run_lanes(target)
    out = target / "benchmarks" / "findings" / f"doc-truth-{truth['plugin_version']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_results(results), encoding="utf-8")

    findings = [v for lane in LANES for v in results["lanes"][lane]["violations"]]
    baseline, baseline_error = load_baseline(target)
    if baseline_error:
        status = "fail"
        findings.append(_v("BASELINE:error", baseline_error, severity="error"))
    elif baseline is None:
        absent = missing_seeds(results)
        status = "fail" if absent else "pass"
        findings += [_v(f"SEED-MISSING:{fid}",
                        f"seeded finding {fid} is absent from the first baseline "
                        f"(fail-closed)", severity="error") for fid in absent]
    else:
        regressions = compare_lanes(baseline, results)
        status = "fail" if regressions else "pass"
        findings += regressions

    metrics = {f"lane_{lane}": results["lanes"][lane]["count"] for lane in LANES}
    metrics["retired_claims"] = results["retired"]["claims"]
    metrics["retired_ledger"] = results["retired"]["ledger"]
    return {"id": PROBE_ID, "status": status, "partial": False,
            "implemented_checks": IMPLEMENTED_CHECKS, "findings": findings,
            "metrics": metrics}


# ---------------------------------------------------------------- self-test

def _write_tree(base: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return base


def self_test() -> int:
    checks: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(ok)
        suffix = f" — {detail}" if detail and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")

    truth = {"N_agents": 4, "N_skills": 10, "plugin_version": "2.21.0"}

    hits = COUNT_RE.findall("two READ-ONLY gate agents; two READ-ONLY agents; three agents")
    misses = COUNT_RE.findall("five subagents and the agents")
    check("count-claim matcher tolerates hyphenated adjectives and holds word boundaries",
          len(hits) == 3 and not misses, f"hits={hits!r} misses={misses!r}")

    with tempfile.TemporaryDirectory() as td:
        root = _write_tree(Path(td) / "unbound", {"README.md": "There are three agents here.\n"})
        got = lane_b(root, [], truth)
        check("unbound count-claim hit yields unadjudicated-claim",
              [v["finding_id"] for v in got] == ["B:unadjudicated:README.md:3-agents:1"],
              repr(got))

        root = _write_tree(Path(td) / "stale", {"README.md": "No claims live here.\n"})
        got = lane_b(root, [{"id": "gone", "file": "README.md",
                             "line-regex": "sentence that vanished", "expect": 2}], truth)
        check("claims entry whose line-regex no longer matches yields stale-entry",
              [v["finding_id"] for v in got] == ["B:stale-entry:gone"], repr(got))

        root = _write_tree(Path(td) / "expect", {
            "README.md": "It ships three agents.\nIt dispatches the two gate agents.\n"})
        got = lane_b(root, [
            {"id": "wrong", "file": "README.md", "line-regex": "ships three agents",
             "expect": "N_agents"},
            {"id": "pinned", "file": "README.md",
             "line-regex": "dispatches the two gate agents", "expect": 2},
        ], truth)
        check("failed-expect fires on truth-var mismatch; a literal-2 bind stays quiet",
              [v["finding_id"] for v in got] == ["B:failed-expect:wrong"], repr(got))

        root = _write_tree(Path(td) / "retired", {"README.md": "Nothing here.\n"})
        got = lane_b(root, [{"id": "old", "file": "README.md", "line-regex": "gone text",
                             "expect": 2, "retired": "superseded"}], truth)
        check("a retired claims entry is skipped, not stale", got == [], repr(got))

        root = _write_tree(Path(td) / "pins", {
            "docs/how-to/codex.md": "## Karta 1.19 features\nAnd Karta 1.19 again.\n",
            "docs/showcase/old.md": "Karta 1.2 archival mention.\n"})
        got = lane_c(root, truth)
        check("lane C flags stale major pins and honors the archival allowlist",
              [v["finding_id"] for v in got] == [
                  "C:stale-pin:docs/how-to/codex.md:1.19:1",
                  "C:stale-pin:docs/how-to/codex.md:1.19:2"], repr(got))

        root = _write_tree(Path(td) / "disc", {
            "README.md": "Only karta-found is described.\n",
            "skills/karta-found/SKILL.md": "x", "skills/karta-lost/SKILL.md": "x",
            "skills/_shared/notes.md": "x"})
        got = lane_d(root)
        check("lane D flags the undiscoverable skill and skips _shared",
              [v["finding_id"] for v in got] == ["D:undiscoverable:karta-lost"], repr(got))

        root = _write_tree(Path(td) / "ledger", {"scripts/other.py": "print('hi')\n"})
        entry = {"id": "DG-X", "check": "py-grep", "pattern": "promised-schema.json",
                 "expect_min_files": 1, "description": "a promised gate no code executes"}
        red = lane_e(root, [entry])
        (root / "scripts" / "gate.py").write_text("open('promised-schema.json')\n")
        green = lane_e(root, [entry])
        check("lane E py-grep goes red at zero matches and green at one",
              [v["finding_id"] for v in red] == ["E:ledger:DG-X"] and green == [],
              f"red={red!r} green={green!r}")

        root = _write_tree(Path(td) / "hooks", {
            "docs/hooks.md": "| Guard rule | `Write`, `Edit` blocked |\n",
            "hooks.json": json.dumps({"hooks": {"PreToolUse": [{"matcher": "Write",
                "hooks": [{"command": "guard_x.py"}]}]}})})
        got = lane_e(root, [{"id": "HP", "check": "hooks-parity", "doc": "docs/hooks.md",
                             "hooks_json": "hooks.json",
                             "rows": [{"rule_regex": "Guard rule", "guard": "guard_x.py"}]}])
        check("hooks-parity flags a doc row claiming more tools than the matcher",
              [v["finding_id"] for v in got] == ["E:ledger:HP:matcher-drift:guard_x.py"],
              repr(got))

        root = _write_tree(Path(td) / "links", {
            "README.md": "[ok](docs/how-to/a.md) [dead](missing.md) "
                         "[web](https://x.example) [anchor](#here)\n",
            "docs/how-to/a.md": "x"})
        got = lane_f(root)
        check("lane F flags only the dead relative link",
              [v["finding_id"] for v in got] == ["F:broken-link:README.md:missing.md"],
              repr(got))

        root = _write_tree(Path(td) / "labels", {
            "docs/conventions/phase-labels.md": "## Roots\n\n|Root|Doc|\n|-|-|\n"
                "|`foo`|`skills/a/SKILL.md`|\n",
            "skills/a/SKILL.md": "## One  `foo:one`\n## Two  `foo:one`\n"
                "See `file:line` citations.\n",
            "skills/b/SKILL.md": "## Stray  `bar:two`\n## Moved  `foo:three`\n"})
        got = [v["finding_id"] for v in lane_g(root)]
        check("lane G flags unregistered root, wrong doc-path, duplicate leaf — "
              "and ignores file:line citation tokens",
              got == ["G:unregistered-root:bar",
                      "G:wrong-doc-path:foo:skills/b/SKILL.md",
                      "G:duplicate-leaf:skills/a/SKILL.md:one"], repr(got))

    fake = {"lanes": {lane: {"count": 1, "violations": []} for lane in LANES},
            "retired": {"claims": 0, "ledger": 0}}
    worse = {"lanes": {lane: {"count": 2 if lane == "D" else 1, "violations": []}
                       for lane in LANES}}
    check("a synthetic lane regression flips status to fail",
          [v["finding_id"] for v in compare_lanes(fake, worse)] == ["REGRESSION:lane-D"])
    check("an unregressed vector stays pass", compare_lanes(fake, fake) == [])

    seeded = {"lanes": {lane: {"count": 0, "violations": []} for lane in LANES}}
    got = missing_seeds(seeded)
    check("a first baseline missing its seeded findings fails closed",
          set(got) == set(SEEDED_FINDING_IDS))

    doc = {"id": PROBE_ID, "truth": truth,
           "lanes": {lane: {"count": 0, "violations": []} for lane in LANES},
           "retired": {"claims": 0, "ledger": 0}}
    check("the results file rendering is byte-deterministic",
          render_results(doc) == render_results(json.loads(json.dumps(doc))))

    passed = sum(checks)
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="doc truth ledger gate probe")
    parser.add_argument("--target", type=Path,
                        default=Path(__file__).resolve().parent.parent.parent,
                        help="karta repo root (default: this probe's repo)")
    parser.add_argument("--self-test", action="store_true",
                        help="run the embedded fixture suite and exit")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    print(json.dumps(run_live(args.target.resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
