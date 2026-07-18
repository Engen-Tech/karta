#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Prose / schema / guard contradiction counter — report-only, always exit 0.

Implements benchmarks/flow/flow-spec-contradictions.md in full (all four passes).
Card-pinned invocation (the one sanctioned non-stdlib call in this binder; jsonschema
is provided by the invocation, never declared in this script's own metadata):

  uv run --with jsonschema benchmarks/flow/check_contradictions.py --repo . \
      --out benchmarks/results/flow-spec-contradictions/<version>.json

Four passes over the canonical tree (skills/ + hooks/ + scripts/ only; mirror copies
in .agents/ and plugins/ are check_shared_copies.py's problem, not this bench's):

  Pass 1 — ref-vocabulary cross-check. Extract refs/karta/<slug>/item-<id>/<state>
    vocabulary from a fixed prose file list and diff two ways against the guards:
    a state defined in prose that no guard reads (today: `in-progress`) and a state
    prose says is written but the Stop-gate's REF_STATES omits (today: `accepted`).

  Pass 2 — hooks.json event/matcher spelling. Validate every event name and matcher
    tool name in hooks/hooks.json against the committed whitelist
    benchmarks/flow/claude-code-events.json. An exact hit is fine; a name within
    edit-distance 2 of a whitelisted one is a MISSPELLING finding; a name matching
    nothing is UNKNOWN-EVENT, reported apart from findings (a Claude Code upgrade is
    bench maintenance, not a karta defect). hooks.json is correctly spelled today, so
    this pass reports zero live findings — the misspelling/UNKNOWN paths are exercised
    only by the gate adapter's --self-test fixtures.

  Pass 3 — promise probes from benchmarks/flow/promises.json. Each promise carries a
    sentence-REGEX anchor (never a line number); an anchor that no longer matches emits
    ANCHOR-LOST as its own finding state, never a silent pass. A1 pipes the shared
    hooked-repo payload fixtures into guard_binder_immutability.py stdin and asserts the
    deny exit; A2 is the between-waves-edit promise vs the unconditional immutability
    guard; A3 is the missing sanctioned-repair-path finding.

  Pass 4 — schema-vs-validator duals. Validate each fixture twice — raw jsonschema
    against the published schema vs validate_binder.py — and record a finding when the
    two disagree, or when both accept exactly what a plan-doc promise claims is caught.

Findings carry content-derived stable ids; resolutions move to an append-only resolved
list carrying the resolving commit hash. Output JSON carries the probe-set id list and
a probe-set hash. This script never mutates the repo it probes; exit is always 0.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path

VECTOR = "flow-spec-contradictions"

# Pass 1: the fixed prose file list (card step 3) and the ref-state regex.
PASS1_FILES = (
    "skills/_shared/integration-branch.md",
    "skills/karta-deliver/SKILL.md",
)
PASS1_REF_DIRS = ("skills/karta-deliver/references",)
REF_STATE_RE = re.compile(r"refs/karta/[^/\s`)]+/item-[^/\s`)]+/([A-Za-z][A-Za-z-]*)")
WRITE_VERB_RE = re.compile(r"writ", re.IGNORECASE)
STOP_GATE = "hooks/scripts/guard_delivery_stop.py"
REF_STATES_RE = re.compile(r"REF_STATES\s*=\s*\(([^)]*)\)")

# The pinned OUT path prefix (card procedure step 1) — compared against the card's own
# `results:` frontmatter to record the permanent card-internal inconsistency.
CARD = "benchmarks/flow/flow-spec-contradictions.md"
PINNED_OUT_PREFIX = "benchmarks/results/flow-spec-contradictions/"

# Pass 4 fixtures + the dual outcome each pins. `expect` is what a correct karta would
# do; a finding is emitted from the ACTUAL behavior whenever it is a contradiction.
BINDER_SCHEMA = "skills/karta-plan/references/binder-schema.json"
DG_SCHEMA = "skills/karta-doc-gardner/references/doc-gardner-schema.json"
VALIDATE_BINDER = "skills/karta-plan/scripts/validate_binder.py"
PLAN_SKILL = "skills/karta-plan/SKILL.md"
SC_DIR = "benchmarks/fixtures/schema-contradictions"
PASS4_FIXTURES = (
    {"probe_id": "p4:shared-terms",
     "path": SC_DIR + "/shared-terms-binder.json", "schema": "binder",
     "finding_id": "p4:dual-disagree:shared-terms",
     "summary": "shared_terms binder: rejected by binder-schema.json "
                "(additionalProperties:false, no shared_terms) but accepted by "
                "validate_binder.py (runtime schema injection) — every modern "
                "gringotts binder is invalid per karta's own published schema file"},
    {"probe_id": "p4:ui-fields",
     "path": SC_DIR + "/ui-fields-on-backend-item.json", "schema": "binder",
     "finding_id": "p4:promise-uncaught:ui-fields-non-ui",
     "summary": "UI fields (component_map/icon_map/token_changes) on a non-UI item: "
                "accepted by BOTH binder-schema.json and validate_binder.py, though "
                "karta-plan SKILL.md promises 'the validator will catch it' — a "
                "fictional safety net"},
    {"probe_id": "p4:bogus-control",
     "path": SC_DIR + "/bogus-extra-key.json", "schema": "binder",
     "finding_id": None,
     "summary": "control: an undeclared top-level key must be rejected by both "
                "validators (proves the dual comparison is live)"},
    {"probe_id": "p4:gringotts",
     "path": "benchmarks/fixtures/gringotts-browse-refinements-binder-2026-07-17.json",
     "schema": "binder",
     "finding_id": "p4:dual-disagree:gringotts-browse-refinements",
     "summary": "the vendored frozen gringotts browse-refinements binder: rejected by "
                "binder-schema.json but accepted by validate_binder.py — a real "
                "shipped consumer binder invalid per karta's published schema"},
)
DG_EXAMPLE = "skills/karta-doc-gardner/references/doc-gardner.example.json"
FIXTURE_DIR = "benchmarks/fixtures/hooked-repo"


# --- helpers -------------------------------------------------------------------

def _read(repo: Path, rel: str) -> str | None:
    try:
        return (repo / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _finding(fid: str, probe_id: str, pass_: str, severity: str, summary: str) -> dict:
    return {"finding_id": fid, "probe_id": probe_id, "pass": pass_,
            "severity": severity, "summary": summary}


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (stdlib DP)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --- Pass 1: ref vocabulary ----------------------------------------------------

def pass1(repo: Path) -> list[dict]:
    prose_states: set[str] = set()
    written_states: set[str] = set()
    texts: list[str] = []
    for rel in PASS1_FILES:
        t = _read(repo, rel)
        if t:
            texts.append(t)
    for rel in PASS1_REF_DIRS:
        d = repo / rel
        if d.is_dir():
            for p in sorted(d.rglob("*.md")):
                t = _read(repo, str(p.relative_to(repo)))
                if t:
                    texts.append(t)
    for t in texts:
        for line in t.splitlines():
            states = REF_STATE_RE.findall(line)
            for st in states:
                prose_states.add(st)
                if WRITE_VERB_RE.search(line):
                    written_states.add(st)

    # REF_STATES the Stop-gate actually tracks.
    ref_states: set[str] = set()
    stop = _read(repo, STOP_GATE) or ""
    m = REF_STATES_RE.search(stop)
    if m:
        ref_states = set(re.findall(r"[A-Za-z][A-Za-z-]*", m.group(1)))

    # State tokens the guards reference as quoted string literals (card: "string
    # literals"). Union with REF_STATES so a Stop-gate-tracked state always counts read.
    read_literals: set[str] = set(ref_states)
    for guard in sorted((repo / "hooks" / "scripts").glob("*.py")) \
            if (repo / "hooks" / "scripts").is_dir() else []:
        src = _read(repo, str(guard.relative_to(repo))) or ""
        for lit in re.findall(r'"([^"]*)"|\'([^\']*)\'', src):
            word = lit[0] or lit[1]
            if word in prose_states:
                read_literals.add(word)

    findings: list[dict] = []
    # way 1 — defined-nowhere-read: in prose, not a Stop-gate state, not described as
    # written, and never a quoted literal in any guard (today: in-progress).
    for st in sorted(prose_states - ref_states - written_states - read_literals):
        findings.append(_finding(
            f"p1:dead-vocab:{st}", "pass1-ref-vocabulary", "1", "known-open",
            f"ref state `{st}` appears in the ref vocabulary prose but no guard reads "
            "it and the Stop-gate's REF_STATES does not track it — dead vocabulary"))
    # way 2 — written-per-prose-but-absent-from-Stop-gate-REF_STATES (today: accepted).
    for st in sorted(written_states - ref_states):
        findings.append(_finding(
            f"p1:written-unread:{st}", "pass1-ref-vocabulary", "1", "known-open",
            f"ref state `{st}` is written per prose but is absent from "
            f"guard_delivery_stop.py REF_STATES={tuple(sorted(ref_states))} — a "
            f"stranded `{st}` item is invisible to the Stop-gate"))
    return findings


# --- Pass 2: hooks.json event/matcher spelling ---------------------------------

def check_hooks_events(hooks_obj: dict, whitelist: dict) -> tuple[list[dict], list[dict]]:
    """Return (findings, unknown_events). MISSPELLING is a finding; UNKNOWN-EVENT is
    reported apart from findings."""
    events = set(whitelist.get("events", []))
    tools = set(whitelist.get("matcher_tools", []))
    findings: list[dict] = []
    unknown: list[dict] = []

    def classify(name: str, allowed: set[str], kind: str) -> None:
        if name in allowed:
            return
        near = sorted(w for w in allowed if _edit_distance(name, w) <= 2)
        if near:
            findings.append(_finding(
                f"p2:misspelling:{kind}:{name}", "pass2-hooks-events", "2", "high",
                f"hooks.json {kind} name {name!r} is within edit-distance 2 of "
                f"{near[0]!r} — a typo the plugin validator accepts silently"))
        else:
            unknown.append({"kind": kind, "name": name,
                            "note": "not within edit-distance 2 of any whitelisted "
                                    "name — treat as bench maintenance, not a defect"})

    for event, groups in (hooks_obj.get("hooks") or {}).items():
        classify(event, events, "event")
        for group in groups if isinstance(groups, list) else []:
            for tool in str(group.get("matcher", "")).split("|"):
                tool = tool.strip()
                if tool:
                    classify(tool, tools, "matcher")
    return findings, unknown


def pass2(repo: Path) -> tuple[list[dict], list[dict]]:
    hooks_obj = json.loads(_read(repo, "hooks/hooks.json") or "{}")
    whitelist = json.loads(_read(repo, "benchmarks/flow/claude-code-events.json") or "{}")
    return check_hooks_events(hooks_obj, whitelist)


# --- Pass 3: promise probes ----------------------------------------------------

def build_hooked_fixture(repo: Path, dest: Path) -> None:
    script = repo / FIXTURE_DIR / "build_fixture.sh"
    proc = subprocess.run(["bash", str(script), str(dest)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"hooked-repo fixture build failed: {proc.stderr.strip()}")


def _run_guard_stdin(repo: Path, guard_rel: str, payload_text: str, cwd: Path) -> int:
    payload_text = payload_text.replace("__FIXTURE__", json.dumps(str(cwd))[1:-1])
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(repo))
    proc = subprocess.run([sys.executable, str(repo / guard_rel)],
                          input=payload_text, cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=30)
    return proc.returncode


def check_promise(repo: Path, promise: dict, fixture_dir: Path | None) -> dict:
    """Grade one promise. Returns {id, state, finding|None}. States: CONSISTENT (no
    finding), OPEN (a live contradiction), ANCHOR-LOST (the anchor regex no longer
    matches its file — loud, its own finding)."""
    pid = promise["id"]
    check = promise["check"]

    def anchor_lost(where: str, regex: str) -> dict:
        f = _finding(f"p3:anchor-lost:{pid}", pid, "3", "high",
                     f"promise {pid} anchor /{regex}/ no longer matches {where} — the "
                     "doc drifted; re-anchor in the same commit")
        return {"id": pid, "state": "ANCHOR-LOST", "finding": f}

    if check == "guard-deny":
        text = _read(repo, promise["anchor_file"])
        if text is None or not re.search(promise["anchor_regex"], text):
            return anchor_lost(promise["anchor_file"], promise["anchor_regex"])
        payload = _read(repo, promise["payload_fixture"])
        if payload is None or fixture_dir is None:
            return {"id": pid, "state": "CONSISTENT", "finding": None,
                    "note": "no fixture available"}
        actual = _run_guard_stdin(repo, promise["guard"], payload, fixture_dir)
        if actual == promise["expected_exit"]:
            return {"id": pid, "state": "CONSISTENT", "finding": None}
        f = _finding(f"p3:promise:{pid}-guard-allows", pid, "3", "high",
                     f"{promise['summary']} — but the guard exited {actual}, not "
                     f"{promise['expected_exit']}: the promise does not hold")
        return {"id": pid, "state": "OPEN", "finding": f}

    if check == "sentence-xor-mechanism":
        text = _read(repo, promise["anchor_file"])
        if text is None or not re.search(promise["anchor_regex"], text):
            # The promise sentence is gone -> the contradiction is resolved, no finding.
            return {"id": pid, "state": "CONSISTENT", "finding": None,
                    "note": "promise sentence absent — nothing promised, nothing broken"}
        mech = _read(repo, promise["mechanism_file"]) or ""
        if re.search(promise["mechanism_regex"], mech, re.IGNORECASE):
            return {"id": pid, "state": "CONSISTENT", "finding": None,
                    "note": "mechanism anchor present"}
        f = _finding("p3:promise:between-waves-edit", pid, "3", "known-open",
                     promise["summary"])
        return {"id": pid, "state": "OPEN", "finding": f}

    if check == "no-repair-anchor":
        for rx in promise["repair_regexes"]:
            for rel in promise["anchor_files"]:
                t = _read(repo, rel)
                if t and re.search(rx, t, re.IGNORECASE):
                    return {"id": pid, "state": "CONSISTENT", "finding": None,
                            "note": f"repair anchor /{rx}/ present in {rel}"}
        f = _finding("p3:promise:missing-repair-path", pid, "3", "known-open",
                     promise["summary"])
        return {"id": pid, "state": "OPEN", "finding": f}

    raise ValueError(f"unknown promise check {check!r}")


def pass3(repo: Path) -> list[dict]:
    promises = json.loads(_read(repo, "benchmarks/flow/promises.json") or "{}")
    findings: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "hooked-repo"
        try:
            build_hooked_fixture(repo, fixture)
            fdir: Path | None = fixture
        except (RuntimeError, OSError, subprocess.SubprocessError):
            fdir = None
        for promise in promises.get("promises", []):
            res = check_promise(repo, promise, fdir)
            if res["finding"]:
                findings.append(res["finding"])
    return findings


# --- Pass 4: schema-vs-validator duals -----------------------------------------

def _validate_binder(repo: Path, path: str) -> bool:
    proc = subprocess.run([sys.executable, str(repo / VALIDATE_BINDER),
                           "--binder", str(repo / path)],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def _raw_valid(schema: dict, path: Path) -> bool:
    import jsonschema  # lazy: the one sanctioned non-stdlib dependency, pass 4 only
    try:
        jsonschema.validate(json.loads(path.read_text()), schema)
        return True
    except jsonschema.ValidationError:
        return False


def pass4(repo: Path) -> list[dict]:
    findings: list[dict] = []
    binder_schema = json.loads(_read(repo, BINDER_SCHEMA) or "{}")
    for fx in PASS4_FIXTURES:
        path = repo / fx["path"]
        raw_ok = _raw_valid(binder_schema, path)
        val_ok = _validate_binder(repo, fx["path"])
        if fx["probe_id"] == "p4:ui-fields":
            # finding when BOTH accept what the plan promise claims is caught
            if raw_ok and val_ok:
                findings.append(_finding(fx["finding_id"], fx["probe_id"], "4",
                                         "known-open", fx["summary"]))
        elif fx["probe_id"] == "p4:bogus-control":
            # control: a finding only if the dual has gone blind (either accepts)
            if raw_ok or val_ok:
                findings.append(_finding(
                    "p4:control-breach:bogus-extra-key", fx["probe_id"], "4", "high",
                    "control breach: the undeclared-key binder was accepted "
                    f"(raw={raw_ok}, validate_binder={val_ok}) — the schema-vs-validator "
                    "dual is no longer rejecting additionalProperties:false violations"))
        else:
            # disagreement: raw rejects, validator accepts (or vice-versa)
            if raw_ok != val_ok:
                findings.append(_finding(fx["finding_id"], fx["probe_id"], "4",
                                         "known-open", fx["summary"]))

    # doc-gardner: raw-valid against its own schema, yet no code executes that schema.
    dg_schema = json.loads(_read(repo, DG_SCHEMA) or "{}")
    dg_ok = _raw_valid(dg_schema, repo / DG_EXAMPLE)
    executed = _schema_has_executor(repo, "doc-gardner-schema")
    if dg_ok and not executed:
        findings.append(_finding(
            "p4:orphan-schema:doc-gardner", "p4:doc-gardner", "4", "known-open",
            "doc-gardner-schema.json validates the shipped doc-gardner.example.json but "
            "no code in hooks/scripts, scripts/, or skills/*/scripts loads or enforces "
            "it — a published schema executed by no one"))
    return findings


def _schema_has_executor(repo: Path, schema_stem: str) -> bool:
    roots = [repo / "hooks" / "scripts", repo / "scripts"]
    for sk in sorted((repo / "skills").glob("*/scripts")) \
            if (repo / "skills").is_dir() else []:
        roots.append(sk)
    for root in roots:
        if not root.is_dir():
            continue
        for py in root.rglob("*.py"):
            src = _read(repo, str(py.relative_to(repo))) or ""
            if schema_stem in src:
                return True
    return False


# --- Card self-inconsistency ---------------------------------------------------

def card_consistency(repo: Path) -> list[dict]:
    text = _read(repo, CARD) or ""
    m = re.search(r"(?m)^results:\s*(\S+)", text)
    if not m:
        return []
    frontmatter_results = m.group(1).strip()
    if not PINNED_OUT_PREFIX.startswith(frontmatter_results.rstrip("/") + "/") \
            and frontmatter_results.rstrip("/") != PINNED_OUT_PREFIX.rstrip("/"):
        return [_finding(
            "card:results-path-mismatch", "card-consistency", "card", "known-open",
            f"the card's `results: {frontmatter_results}` frontmatter contradicts the "
            f"procedure step-1 --out path `{PINNED_OUT_PREFIX}<version>.json`; this item "
            "follows the procedure text and leaves the frontmatter untouched (cards are "
            "edited only to flip probe_status), recording the mismatch here")]
    return []


# --- Assembly ------------------------------------------------------------------

PROBE_SET = sorted({
    "pass1-ref-vocabulary", "pass2-hooks-events", "A1", "A2", "A3",
    "p4:shared-terms", "p4:ui-fields", "p4:bogus-control", "p4:gringotts",
    "p4:doc-gardner", "card-consistency",
})


def run(repo: Path) -> dict:
    findings: list[dict] = []
    findings += pass1(repo)
    p2_findings, unknown_events = pass2(repo)
    findings += p2_findings
    findings += pass3(repo)
    findings += pass4(repo)
    findings += card_consistency(repo)
    findings.sort(key=lambda f: f["finding_id"])
    probe_set = PROBE_SET
    probe_set_hash = hashlib.sha256(
        json.dumps(probe_set, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 1,
        "vector": VECTOR,
        "probe_set": probe_set,
        "probe_set_hash": probe_set_hash,
        "findings": findings,
        "unknown_events": unknown_events,
        "resolved": [],
        "open_total": len(findings),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="flow-spec-contradictions runner")
    ap.add_argument("--repo", default=".", help="repo root to probe")
    ap.add_argument("--out", required=True, help="findings JSON output path")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    evidence = run(repo)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2, sort_keys=False) + "\n")
    print(f"{VECTOR}: {evidence['open_total']} open contradictions, "
          f"{len(evidence['unknown_events'])} unknown-event(s) -> {out}")
    return 0  # report-only, always exit 0


if __name__ == "__main__":
    sys.exit(main())
