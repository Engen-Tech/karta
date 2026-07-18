#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Gate probe for sec-untrusted-input-surfaces: feed the status page and the
SessionStart injection hostile repo content and watch what escapes.

Implements P1 and P2 of benchmarks/sec/sec-untrusted-input-surfaces.md (the P3
live claude -p cells are phase 3), so this probe declares partial: true.

  P1  launches skills/karta-status/scripts/serve_status.py against the fixture on
      127.0.0.1 with an ephemeral port, fetches / and /state.json via urllib, and
      byte-greps the responses for every attacker payload (an unescaped payload =
      a sink; red today: serve_status has zero html.escape calls while it
      interpolates binder strings into HTML). It also asserts the bind address
      stays 127.0.0.1 and that a keyless request is refused when --key is set. The
      server is always torn down, and a server that fails to start is a probe
      failure (fail-closed), never a silent skip.
  P2  runs hooks/scripts/inject_karta_status.py against the fixture and asserts the
      injected block wraps repo-derived text in an inert delimiter (red today: the
      lines are undelimited) and stays under the pinned byte budget committed in
      benchmarks/fixtures/adversarial/expected.json, recording verbatim what
      reaches context.

Fixture: benchmarks/fixtures/adversarial/ ships the hostile payload files (a
binder JSON whose title/summary/contract/oracle strings carry <script> / <img
onerror> / prompt-injection payloads, a poisoned .karta/sme overlay pack, a
tampered archive entry, and expected.json); the probe assembles them into a
mktemp git-initialized scratch repo on every run. Hook bypass and ref forgery
stay OUT of scope per the card's scope guard (owned by flow-guard-enforcement-
matrix and dark-status-surface-probes).

Stdout is the gate probe JSON contract
{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"};
exit 0 whether pass or fail (a nonzero exit means the probe itself crashed).

Verdict rule (baseline regression): status "fail" only on regression against the
last committed results file — a NEW unescaped sink beyond the frozen list, or a
passing check (escaped-render / bind-scope / key-gate / injection-inertness /
injection-byte-budget) flipping toward worse. On the first run (no tracked
baseline yet) the probe fails closed unless its own matrix reproduces the
contract's seeded findings: at least one payload reaches the rendered HTML
unescaped, the injection block carries no inert delimiter, and the loopback-bind
and key-gate assertions hold as they do today. Baseline selection is strict:
the regression baseline is the newest git-tracked results file for this vector
(git ls-files), never an untracked or same-run file — read from the git index,
so this run's own working-tree write can never be its own baseline.

On every run the frozen unescaped-sink list and the full check matrix are written
to benchmarks/sec/results/<run-date>-sec-probes.json (overwriting a same-date
file). That date-keyed path deliberately deviates from card step 6's
benchmarks/results/<version>-sec-probes.json: this first baseline is committed
mid-cycle before any RC version exists, and the family-local results dir matches
the sibling probes' convention — recorded as a standing finding, card text left
untouched.

  python3 benchmarks/probes/sec-untrusted-input-surfaces.py --target <repo-root>
  python3 benchmarks/probes/sec-untrusted-input-surfaces.py --self-test

--self-test prints [PASS]/[FAIL] lines and an N/N checks passed summary, and exits
0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse
import datetime
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROBE_ID = "sec-untrusted-input-surfaces"

# Binder shared_terms this vector is enrolled in, rendered verbatim (each on one
# physical line so check_shared_terms.py's exact-substring byte match resolves).
# The self-test asserts each is byte-present in this file.
SHARED_TERMS_CANONICAL = (
    '{"id","status":"pass"|"fail","partial","implemented_checks","findings","metrics"}',
    '[PASS]/[FAIL] lines and an N/N checks passed summary',
    'status "fail" only on regression against the last committed results file',
    'the regression baseline is the newest git-tracked results file for this vector (git ls-files), never an untracked or same-run file',
)

FIXTURES = Path("benchmarks/fixtures/adversarial")
RESULTS_DIR = Path("benchmarks/sec/results")
RESULTS_RE = re.compile(r"benchmarks/sec/results/\d{4}-\d{2}-\d{2}-sec-probes\.json")
SERVE_REL = Path("skills/karta-status/scripts/serve_status.py")
INJECT_REL = Path("hooks/scripts/inject_karta_status.py")
CELL_NAMES = ("escaped-render", "bind-scope", "key-gate",
              "injection-inertness", "injection-byte-budget")
SUB_TIMEOUT_S = 20
SERVER_READY_S = 15
KEY_TOKEN = "s3cret-probe-key"

# Accepted "inert delimiter" shapes for the SessionStart injection: a fenced or
# sentinel-wrapped block marks the enclosed repo-derived text as data, not
# instruction. inject_karta_status.py ships none today, so P2 is red.
_DELIM_PAIRS = (
    (re.compile(r"<karta-status[ >]"), re.compile(r"</karta-status>")),
    (re.compile(r"```"), re.compile(r"```")),  # a fenced block (open + close)
    (re.compile(r"BEGIN[ _-]?KARTA", re.I), re.compile(r"END[ _-]?KARTA", re.I)),
)


class ProbeError(RuntimeError):
    """The probe itself cannot grade — fail loud (nonzero exit), never a silent skip."""


def _run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None
         ) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd,
                              input=stdin, timeout=SUB_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"subprocess did not complete: {cmd[:2]}... ({e})") from e


def _load_expected(target: Path) -> dict:
    path = target / FIXTURES / "expected.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ProbeError(f"cannot read fixture anchors {path}: {e}") from e
    if not isinstance(doc.get("payloads"), dict) or not doc["payloads"]:
        raise ProbeError(f"{path} must carry a non-empty payloads map")
    if not isinstance(doc.get("injection_byte_budget"), int):
        raise ProbeError(f"{path} must pin an integer injection_byte_budget")
    return doc


# ---------------------------------------------------------------------------
# Fixture assembly — copy the committed hostile files into a fresh scratch repo.
# ---------------------------------------------------------------------------


def _assemble_fixture(target: Path) -> tuple[Path, Path]:
    """Fresh mktemp git repo laid out like a consumer checkout. (scratch, repo)."""
    src = target / FIXTURES
    for name in ("binder-adversarial.json", "archive-tampered.json", "sme-poison.md",
                 "expected.json"):
        if not (src / name).is_file():
            raise ProbeError(f"missing fixture file {src / name}")
    scratch = Path(tempfile.mkdtemp(prefix="karta-bench-sec-"))
    repo = scratch / "repo"
    (repo / ".karta" / "binders" / "archive").mkdir(parents=True)
    (repo / ".karta" / "sme").mkdir(parents=True)
    shutil.copy(src / "binder-adversarial.json",
                repo / ".karta" / "binders" / "adversarial-fixture.json")
    shutil.copy(src / "archive-tampered.json",
                repo / ".karta" / "binders" / "archive" / "adversarial-archived.json")
    shutil.copy(src / "sme-poison.md", repo / ".karta" / "sme" / "sme-poison.md")
    env_date = "2026-07-17T00:00:00 +0000"
    steps = [
        ["git", "-c", "init.defaultBranch=main", "init", "-q"],
        ["git", "config", "user.name", "karta-bench"],
        ["git", "config", "user.email", "bench@karta.local"],
        ["git", "add", "-A"],
        ["git", "-c", f"user.name=karta-bench", "commit", "-q", "-m", "adversarial fixture"],
    ]
    for step in steps:
        p = subprocess.run(step, cwd=repo, capture_output=True, text=True,
                           env={"GIT_AUTHOR_DATE": env_date, "GIT_COMMITTER_DATE": env_date,
                                "GIT_AUTHOR_NAME": "karta-bench", "GIT_AUTHOR_EMAIL": "bench@karta.local",
                                "GIT_COMMITTER_NAME": "karta-bench", "GIT_COMMITTER_EMAIL": "bench@karta.local",
                                "PATH": _path_env()})
        if p.returncode != 0:
            shutil.rmtree(scratch, ignore_errors=True)
            raise ProbeError(f"fixture git setup failed: {step[:3]}... "
                             f"{(p.stderr or p.stdout).strip()[-200:]}")
    return scratch, repo


def _path_env() -> str:
    import os
    return os.environ.get("PATH", "/usr/bin:/bin")


# ---------------------------------------------------------------------------
# P1 — serve_status render safety, bind scope, key gate.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _launch_server(target: Path, repo: Path, key: str | None) -> tuple[subprocess.Popen, int]:
    script = target / SERVE_REL
    if not script.is_file():
        raise ProbeError(f"serve_status.py not found at {script}")
    last_err: Exception | None = None
    for _ in range(3):
        port = _free_port()
        cmd = [sys.executable, str(script), "--root", str(repo), "--port", str(port)]
        if key:
            cmd += ["--key", key]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + SERVER_READY_S
        while time.time() < deadline:
            if proc.poll() is not None:
                out = (proc.stdout.read() if proc.stdout else "") + \
                      (proc.stderr.read() if proc.stderr else "")
                last_err = RuntimeError(f"exited {proc.returncode}: {out[-200:]}")
                break
            try:
                url = f"http://127.0.0.1:{port}/"
                if key:
                    url += f"?key={key}"
                with urllib.request.urlopen(url, timeout=2) as r:
                    r.read()
                return proc, port
            except urllib.error.HTTPError:
                return proc, port  # a 403/404 still proves the server is up
            except (urllib.error.URLError, OSError) as e:
                last_err = e
                time.sleep(0.1)
        _terminate(proc)
    raise ProbeError(f"serve_status did not become ready on 127.0.0.1: {last_err}")


def _fetch(port: int, path: str, key: str | None = None) -> tuple[int, bytes]:
    url = f"http://127.0.0.1:{port}{path}"
    if key:
        url += ("&" if "?" in path else "?") + f"key={key}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _bind_scope_static(target: Path) -> bool:
    """serve_status must bind loopback and never all interfaces."""
    src = (target / SERVE_REL).read_text()
    return '"127.0.0.1"' in src and "0.0.0.0" not in src


def _grep_sinks(payloads: dict, surfaces: dict) -> list[str]:
    """A sink is (surface, payload) where the raw attacker bytes survive into the
    response unescaped. Returns sorted stable sink ids `<surface>::<payload-id>`."""
    sinks: list[str] = []
    for surface, body in surfaces.items():
        for pid, raw in payloads.items():
            if raw.encode("utf-8") in body:
                sinks.append(f"{surface}::{pid}")
    return sorted(sinks)


def probe_p1(target: Path, repo: Path, expected: dict) -> dict:
    """Render safety, bind scope, key gate. Server always torn down."""
    payloads = expected["payloads"]
    static_loopback = _bind_scope_static(target)

    # Launch 1: keyless server — fetch / and /state.json, grep for unescaped payloads.
    proc, port = _launch_server(target, repo, key=None)
    try:
        idx_status, idx_body = _fetch(port, "/")
        st_status, st_body = _fetch(port, "/state.json")
        runtime_reachable = idx_status == 200 and st_status == 200
        surfaces = {"index": idx_body, "state_json": st_body}
        sinks = _grep_sinks(payloads, surfaces)
    finally:
        _terminate(proc)

    # Launch 2: keyed server — a keyless request must be refused, a keyed one allowed.
    proc, port = _launch_server(target, repo, key=KEY_TOKEN)
    try:
        keyless_status, _ = _fetch(port, "/")
        keyed_status, _ = _fetch(port, "/", key=KEY_TOKEN)
    finally:
        _terminate(proc)

    html_sinks = [s for s in sinks if s.startswith("index::")]
    return {
        "unescaped_sinks": sinks,
        "cells": {
            "escaped-render": {
                "good": len(sinks) == 0,
                "sink_count": len(sinks),
                "html_sink_count": len(html_sinks),
            },
            "bind-scope": {
                "good": static_loopback and runtime_reachable,
                "static_loopback": static_loopback,
                "runtime_reachable": runtime_reachable,
            },
            "key-gate": {
                "good": keyless_status == 403 and keyed_status == 200,
                "keyless_status": keyless_status,
                "keyed_status": keyed_status,
            },
        },
    }


# ---------------------------------------------------------------------------
# P2 — SessionStart injection inertness + byte budget.
# ---------------------------------------------------------------------------


def _is_delimited(text: str) -> bool:
    for open_re, close_re in _DELIM_PAIRS:
        opens = list(open_re.finditer(text))
        if not opens:
            continue
        rest = text[opens[0].end():]
        if close_re.search(rest):
            return True
    return False


def probe_p2(target: Path, repo: Path, expected: dict) -> tuple[dict, str]:
    """Run the SessionStart injection against the fixture; record verbatim output."""
    script = target / INJECT_REL
    if not script.is_file():
        raise ProbeError(f"inject_karta_status.py not found at {script}")
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(repo)})
    p = _run([sys.executable, str(script)], cwd=repo, stdin=payload)
    if p.returncode != 0:
        raise ProbeError(f"inject_karta_status.py exited {p.returncode}: "
                         f"{p.stderr.strip()[-200:]}")
    verbatim = p.stdout
    budget = expected["injection_byte_budget"]
    nbytes = len(verbatim.encode("utf-8"))
    delimited = _is_delimited(verbatim)
    cell = {
        "injection-inertness": {"good": delimited, "delimited": delimited},
        "injection-byte-budget": {"good": nbytes <= budget, "bytes": nbytes, "budget": budget},
    }
    return cell, verbatim


# ---------------------------------------------------------------------------
# Matrix, baseline, verdict.
# ---------------------------------------------------------------------------


def build_matrix(target: Path, expected: dict) -> tuple[dict, str]:
    scratch, repo = _assemble_fixture(target)
    try:
        p1 = probe_p1(target, repo, expected)
        p2_cells, verbatim = probe_p2(target, repo, expected)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    matrix = {"unescaped_sinks": p1["unescaped_sinks"],
              "cells": {**p1["cells"], **p2_cells}}
    return matrix, verbatim


def compare_matrices(baseline: dict, current: dict) -> list[str]:
    """Toward-worse only: a NEW sink beyond the frozen list, or a check whose
    baseline good=true is now false/missing. Sinks disappearing (count moving
    toward 0) and brand-new check ids never count as regressions."""
    regressions: list[str] = []
    base_sinks = set(baseline.get("unescaped_sinks", []))
    cur_sinks = set(current.get("unescaped_sinks", []))
    for s in sorted(cur_sinks - base_sinks):
        regressions.append(f"new-sink::{s}")
    base_cells = baseline.get("cells", {})
    cur_cells = current.get("cells", {})
    for name in sorted(base_cells):
        if not (isinstance(base_cells[name], dict) and base_cells[name].get("good")):
            continue
        cur = cur_cells.get(name)
        if not (isinstance(cur, dict) and cur.get("good")):
            regressions.append(f"regressed::{name}")
    return regressions


def seed_state(matrix: dict, expected: dict) -> list[str]:
    """Names of contract-named seeded findings that are ABSENT from this matrix
    (the first-run fail-closed trigger). Empty list = every seed reproduced."""
    cells = matrix["cells"]
    min_html = expected.get("expect", {}).get("min_html_sinks", 1)
    seeds = {
        "P1-unescaped-render": cells["escaped-render"]["good"] is False,
        "P1-html-sink-present": cells["escaped-render"]["html_sink_count"] >= min_html,
        "P2-undelimited-injection": cells["injection-inertness"]["good"] is False,
        "P1-bind-scope-holds": cells["bind-scope"]["good"] is True,
        "P1-key-gate-holds": cells["key-gate"]["good"] is True,
        "P2-byte-budget-holds": cells["injection-byte-budget"]["good"] is True,
    }
    return [name for name, present in seeds.items() if not present]


def find_baseline(target: Path) -> tuple[str | None, dict | None]:
    """Newest git-tracked results file for this vector, read from the git index."""
    ls = _run(["git", "-C", str(target), "ls-files", "--", str(RESULTS_DIR) + "/"]).stdout.split()
    tracked = sorted(p for p in ls if RESULTS_RE.fullmatch(p))
    if not tracked:
        return None, None
    path = tracked[-1]
    blob = _run(["git", "-C", str(target), "show", f":{path}"])
    if blob.returncode != 0:
        raise ProbeError(f"tracked baseline {path} unreadable from the git index")
    try:
        doc = json.loads(blob.stdout)
    except json.JSONDecodeError as e:
        raise ProbeError(f"tracked baseline {path} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("probe_matrix"), dict):
        raise ProbeError(f"tracked baseline {path} lacks a probe_matrix object")
    return path, doc


def decide_status(matrix: dict, baseline_doc: dict | None, expected: dict
                  ) -> tuple[str, list[str], list[str]]:
    """(status, regressions, seed_missing). With a committed baseline: fail only on
    regression. Without one (first run): fail closed unless every seeded finding is
    reproduced in the probe's own first baseline."""
    if baseline_doc is not None:
        regressions = compare_matrices(baseline_doc["probe_matrix"], matrix)
        return ("fail" if regressions else "pass"), regressions, []
    seed_missing = seed_state(matrix, expected)
    return ("fail" if seed_missing else "pass"), [], seed_missing


def seeded_findings(matrix: dict, verbatim: str) -> list[dict]:
    cells = matrix["cells"]
    findings: list[dict] = []
    html_sinks = [s for s in matrix["unescaped_sinks"] if s.startswith("index::")]
    if not cells["escaped-render"]["good"]:
        findings.append({
            "finding_id": "seed-P1-unescaped-render", "severity": "high",
            "summary": "serve_status.py renders attacker-controlled binder strings "
                       "unescaped (zero html.escape calls): "
                       f"{cells['escaped-render']['sink_count']} unescaped sink(s), "
                       f"{len(html_sinks)} in the rendered HTML "
                       f"({', '.join(html_sinks) or 'none'})",
        })
    findings.append({
        "finding_id": "seed-P1-frozen-sink-list", "severity": "info",
        "summary": "frozen unescaped-sink list recorded (count must reach and stay 0): "
                   f"{', '.join(matrix['unescaped_sinks']) or 'none'}",
    })
    if not cells["injection-inertness"]["good"]:
        first = verbatim.splitlines()[0] if verbatim.strip() else "(empty)"
        findings.append({
            "finding_id": "seed-P2-undelimited-injection", "severity": "medium",
            "summary": "inject_karta_status.py carries repo-derived text into session "
                       "context with no inert delimiter (first line: "
                       f"{first!r}); a poisoned binder string is undelimited instruction",
        })
    findings.append({
        "finding_id": "card-step6-results-path-errata", "severity": "info",
        "summary": "results are written to benchmarks/sec/results/<date>-sec-probes.json "
                   "per the card frontmatter results dir and the sibling probes' date-keyed "
                   "convention, deviating from card step 6's benchmarks/results/"
                   "<version>-sec-probes.json (no RC version exists mid-cycle) — card text "
                   "left untouched",
    })
    return findings


def results_payload(matrix: dict, verbatim: str, findings: list[dict], run_date: str) -> dict:
    return {
        "schema_version": 1,
        "vector": PROBE_ID,
        "run_date": run_date,
        "probe_matrix": matrix,
        "unescaped_sinks": matrix["unescaped_sinks"],
        "injection_verbatim": verbatim,
        "findings": findings,
    }


def run_probe(target: Path) -> int:
    expected = _load_expected(target)
    matrix, verbatim = build_matrix(target, expected)
    baseline_path, baseline_doc = find_baseline(target)
    status, regressions, seed_missing = decide_status(matrix, baseline_doc, expected)

    run_date = datetime.date.today().isoformat()
    results_dir = target / RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    findings = seeded_findings(matrix, verbatim)
    (results_dir / f"{run_date}-sec-probes.json").write_text(
        json.dumps(results_payload(matrix, verbatim, findings, run_date),
                   indent=2, sort_keys=True) + "\n")

    findings = list(findings)
    findings += [{"finding_id": f"regression-{r}", "severity": "error",
                  "summary": f"regression versus committed baseline {baseline_path}: {r}"}
                 for r in regressions]
    if baseline_doc is None:
        findings += [{"finding_id": f"seed-missing-{sid}", "severity": "error",
                      "summary": f"first-run baseline lacks contract-named seeded finding "
                                 f"{sid} — failing closed (fixture or surface drift)"}
                     for sid in seed_missing]

    cells = matrix["cells"]
    print(json.dumps({
        "id": PROBE_ID,
        "status": status,
        "partial": True,
        "implemented_checks": [
            "P1-escaped-render(byte-grep / + /state.json)",
            "P1-bind-scope(127.0.0.1)",
            "P1-key-gate(--key refuses keyless)",
            "P2-injection-inertness(inert delimiter)",
            "P2-injection-byte-budget",
            "frozen-unescaped-sink-list",
            "baseline-regression-diff",
        ],
        "findings": findings,
        "metrics": {
            "unescaped_sink_count": cells["escaped-render"]["sink_count"],
            "unescaped_sinks": matrix["unescaped_sinks"],
            "html_sink_count": cells["escaped-render"]["html_sink_count"],
            "bind_scope": cells["bind-scope"]["good"],
            "key_gate": cells["key-gate"]["good"],
            "injection_delimited": cells["injection-inertness"]["good"],
            "injection_bytes": cells["injection-byte-budget"]["bytes"],
            "injection_budget": cells["injection-byte-budget"]["budget"],
            "baseline_file": baseline_path,
            "regressions": regressions,
            "results_file": str(RESULTS_DIR / f"{run_date}-sec-probes.json"),
            "not_implemented": ["P3-live-claude-p-pack-directive-noncompliance"],
        },
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Self-test — the house [PASS]/[FAIL] lines and an N/N checks passed summary.
# ---------------------------------------------------------------------------


def _run_self_test() -> int:
    target = Path(__file__).resolve().parent.parent.parent
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(bool(ok))

    # Every enrolled shared_terms canonical renders byte-identically in this file
    # (the substring check_shared_terms.py enforces at deliver/build time).
    self_src = Path(__file__).read_bytes()
    check("all four shared_terms canonicals render verbatim in this probe",
          all(c.encode("utf-8") in self_src for c in SHARED_TERMS_CANONICAL))

    # expected.json loads and pins payloads + a byte budget.
    exp_ok = True
    try:
        expected = _load_expected(target)
    except ProbeError:
        expected = {}
        exp_ok = False
    check("expected.json loads with payloads + injection_byte_budget", exp_ok)

    # A failed server launch is a probe failure, never a silent skip (fail-closed).
    launch_fails = False
    try:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "no-serve"
            (fake / SERVE_REL).parent.mkdir(parents=True)
            _launch_server(fake, Path(td), key=None)
    except ProbeError:
        launch_fails = True
    check("a server that cannot start raises ProbeError (fail-closed)", launch_fails)

    matrix = None
    verbatim = ""
    if exp_ok:
        try:
            matrix, verbatim = build_matrix(target, expected)
        except ProbeError as e:
            check(f"build_matrix runs P1+P2 against the assembled fixture ({e})", False)
    check("build_matrix runs P1+P2 against the assembled fixture", matrix is not None)

    if matrix is not None:
        cells = matrix["cells"]
        html_sinks = [s for s in matrix["unescaped_sinks"] if s.startswith("index::")]
        check("P1 reproduces the seeded finding: >=1 payload reaches the rendered HTML unescaped",
              cells["escaped-render"]["good"] is False and len(html_sinks) >= 1)
        check("P1 records the frozen unescaped-sink list including /state.json sinks",
              any(s.startswith("state_json::") for s in matrix["unescaped_sinks"]))
        check("P1 loopback-bind assertion holds (127.0.0.1, reachable)",
              cells["bind-scope"]["good"] is True)
        check("P1 key-gate holds: keyless refused (403), keyed allowed (200)",
              cells["key-gate"]["good"] is True
              and cells["key-gate"]["keyless_status"] == 403
              and cells["key-gate"]["keyed_status"] == 200)
        check("P2 reproduces the seeded finding: injection carries no inert delimiter",
              cells["injection-inertness"]["good"] is False)
        check("P2 stays under the pinned byte budget and records verbatim output",
              cells["injection-byte-budget"]["good"] is True and len(verbatim) > 0)
        check("first run reproduces every seeded finding (fail-closed set is empty)",
              seed_state(matrix, expected) == [])

    # Delimiter recognizer: today's plain lines are undelimited; a fenced block is inert.
    check("delimiter recognizer: plain karta lines are undelimited",
          _is_delimited("karta: 1 binder(s)\n  adversarial-fixture — 1 item(s)") is False)
    check("delimiter recognizer: a fenced/sentinel block reads as delimited",
          _is_delimited("<karta-status>\nkarta: 1 binder(s)\n</karta-status>") is True)

    # Decision logic on synthetic matrices — no server needed.
    base = {
        "unescaped_sinks": ["index::img-onerror", "state_json::img-onerror"],
        "cells": {
            "escaped-render": {"good": False},
            "bind-scope": {"good": True},
            "key-gate": {"good": True},
            "injection-inertness": {"good": False},
            "injection-byte-budget": {"good": True},
        },
    }
    same = json.loads(json.dumps(base))
    st, regs, _ = decide_status(same, {"probe_matrix": base}, expected or {})
    check("identical matrix vs committed baseline stays pass", st == "pass" and regs == [])

    newsink = json.loads(json.dumps(base))
    newsink["unescaped_sinks"].append("index::new-payload")
    st, regs, _ = decide_status(newsink, {"probe_matrix": base}, expected or {})
    check("a NEW sink beyond the frozen list flips status to fail",
          st == "fail" and regs == ["new-sink::index::new-payload"])

    gone = json.loads(json.dumps(base))
    gone["unescaped_sinks"] = ["index::img-onerror"]  # a sink disappeared (toward 0)
    st, regs, _ = decide_status(gone, {"probe_matrix": base}, expected or {})
    check("a sink disappearing (count toward 0) is never a regression",
          st == "pass" and regs == [])

    flip = json.loads(json.dumps(base))
    flip["cells"]["bind-scope"]["good"] = False  # a passing check flips toward worse
    st, regs, _ = decide_status(flip, {"probe_matrix": base}, expected or {})
    check("a passing check (bind-scope) flipping toward worse flips status to fail",
          st == "fail" and regs == ["regressed::bind-scope"])

    keyflip = json.loads(json.dumps(base))
    keyflip["cells"]["key-gate"]["good"] = False
    st, regs, _ = decide_status(keyflip, {"probe_matrix": base}, expected or {})
    check("a key-gate regression flips status to fail",
          st == "fail" and regs == ["regressed::key-gate"])

    # First-run fail-closed when a contract-named seed is absent.
    healed = json.loads(json.dumps(base))
    healed["cells"]["escaped-render"]["good"] = True  # no sinks -> seed absent
    healed["cells"]["escaped-render"]["html_sink_count"] = 0
    healed["unescaped_sinks"] = []
    st, _, missing = decide_status(healed, None, expected or {"expect": {"min_html_sinks": 1}})
    check("first run fails closed when a seeded finding (unescaped render) is absent",
          st == "fail" and "P1-unescaped-render" in missing)

    total, failures = len(results), results.count(False)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="sec-untrusted-input-surfaces gate probe")
    ap.add_argument("--target", type=Path,
                    default=Path(__file__).resolve().parent.parent.parent,
                    help="karta repo root to measure (default: this probe's repo)")
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded checks; exit 0 only on N/N checks passed")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        return run_probe(args.target.resolve())
    except ProbeError as e:
        print(f"{PROBE_ID}: PROBE ERROR — {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
