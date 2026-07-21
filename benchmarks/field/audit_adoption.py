#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Feature adoption auditor (card: benchmarks/field/field-feature-adoption-ledger.md).

Reads benchmarks/field/feature-manifest.json and, per enrolled consumer repo,
classifies every manifest feature as ADOPTED, NOT-EXERCISED-SINCE-SHIP, or
AVAILABLE-BUT-UNUSED — the only alarm class — writing the full attribution table
to benchmarks/field/results/<audit-date>-<repo>.json with the denominator always
shown. durable:false detections report current-state presence only
(CURRENT-STATE-ONLY) and are excluded from adoption-lag and AVAILABLE-BUT-UNUSED.

MANIFEST GATE (enforced, not asserted): releases are enumerated via
`git log --grep 'chore(release)' --format='%cI %s'`; the audit exits 2 when any
release newer than the newest manifest entry lacks both an entry and an explicit
{version, observable: false} sentinel.

ACTIVITY EVENTS are pinned to exactly three deterministic commit classes in the
consumer repo (committer dates): subject `plan(karta):`, subject
`chore(karta): archive binder`, and merge commits of karta/*/integration
branches. Nothing else counts.

FIRST-USE: tracked-content detections via `git log -S<pattern>` (earliest hit;
jq-kind detections use the final key token of the pattern, scoped to the entry's
path); durable-ref detections via the earliest ref-target committer date. Once a
first_use lands in a committed result JSON it is immutable — later audits carry
it forward verbatim (append-only ledger), so ref cleanup or marker removal can
never flip used->unused. adoption_lag medians only at n>=5; negative lags are
labeled pre-release-dogfood and excluded from the median.

The karta repo root derives from this file's own location (never a hardcoded
absolute path); consumer repos come from --consumers <path,...>, defaulting to
the sibling directories parchmark and gringotts of the --target repo's parent.
A missing consumer directory is a clear error (exit 3) in live mode.

Usage:
  python3 benchmarks/field/audit_adoption.py [--target <karta-root>]
      [--consumers <path,path>] [--date YYYY-MM-DD]
  python3 benchmarks/field/audit_adoption.py --self-test

--self-test builds a fixture git repo in a tempdir (no network, no writes to the
real repos) and prints [PASS]/[FAIL] lines and an N/N checks passed summary;
it exits 0 only when the summary is N/N checks passed, nonzero otherwise.
"""
from __future__ import annotations
import argparse, datetime, json, os, re, statistics, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_REL = Path("benchmarks") / "field" / "feature-manifest.json"
RESULTS_REL = Path("benchmarks") / "field" / "results"
DEFAULT_CONSUMERS = ("parchmark", "gringotts")
GIT_TIMEOUT_S = 30
RELEASE_RE = re.compile(r"bump plugin version to (\d+(?:\.\d+)+)")
INTEGRATION_MERGE_RE = re.compile(r"karta/[^\s']+/integration")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    return proc.stdout if proc.returncode == 0 else ""


def _iso(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def load_manifest(target: Path) -> dict:
    return json.loads((target / MANIFEST_REL).read_text())


def features_of(manifest: dict) -> list[dict]:
    return [e for e in manifest["entries"] if "id" in e]


def enumerate_releases(karta_root: Path) -> list[tuple[str, str]]:
    """[(version, committer-date-iso)] from chore(release) subjects, oldest first."""
    out = _git(karta_root, "log", "--grep", "chore(release)", "--format=%cI %s")
    releases = []
    for line in out.splitlines():
        date, _, subject = line.partition(" ")
        m = RELEASE_RE.search(subject)
        if m:
            releases.append((m.group(1), date))
    return list(reversed(releases))


def manifest_gate_violations(releases: list[tuple[str, str]], manifest: dict) -> list[str]:
    """Releases newer than the newest manifest entry with no entry and no sentinel."""
    covered = {e["karta_version"] for e in manifest["entries"] if "id" in e}
    covered |= {e["version"] for e in manifest["entries"] if "id" not in e}
    if not covered:
        return [f"manifest has no entries; uncovered release {v}" for v, _ in releases]
    newest = max(_semver(v) for v in covered)
    return [f"release {v} is newer than the newest manifest entry and has neither "
            f"an entry nor an observable:false sentinel"
            for v, _ in releases if _semver(v) > newest and v not in covered]


def activity_events(repo: Path) -> list[dict]:
    """The three pinned activity classes, oldest first, committer dates."""
    out = _git(repo, "log", "--format=%H|%cI|%P|%s")
    events = []
    for line in out.splitlines():
        sha, date, parents, subject = line.split("|", 3)
        if subject.startswith("plan(karta):"):
            cls = "plan"
        elif subject.startswith("chore(karta): archive binder"):
            cls = "archive"
        elif len(parents.split()) > 1 and INTEGRATION_MERGE_RE.search(subject):
            cls = "integration_merge"
        else:
            continue
        events.append({"sha": sha, "date": date, "class": cls})
    return list(reversed(events))


def _pickaxe_first(repo: Path, token: str, path: str) -> dict | None:
    args = ["log", f"-S{token}", "--format=%H %cI"]
    if path:
        args += ["--", path]
    out = _git(repo, *args)
    lines = out.strip().splitlines()
    if not lines:
        return None
    sha, _, date = lines[-1].partition(" ")  # oldest hit
    return {"commit": sha, "date": date}


def _refs_matching(repo: Path, pattern: str) -> list[tuple[str, str]]:
    out = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", pattern)
    return [tuple(line.split(" ", 1)) for line in out.strip().splitlines() if line]


def first_use(repo: Path, detection: dict) -> dict | None:
    kind, pattern, path = detection["kind"], detection["pattern"], detection.get("path", "")
    if kind == "grep":
        return _pickaxe_first(repo, pattern, path)
    if kind == "jq":
        token = pattern.rsplit(".", 1)[-1]
        return _pickaxe_first(repo, token, path)
    if kind == "ref":
        hits = []
        for _refname, sha in _refs_matching(repo, pattern):
            date = _git(repo, "show", "-s", "--format=%cI", sha).strip()
            if date:
                hits.append({"commit": sha, "date": date})
        return min(hits, key=lambda h: _iso(h["date"])) if hits else None
    raise ValueError(f"unknown detection kind {kind!r}")


def present_now(repo: Path, detection: dict) -> bool:
    kind, pattern, path = detection["kind"], detection["pattern"], detection.get("path", "")
    if kind == "ref":
        return bool(_refs_matching(repo, pattern))
    if kind == "grep":
        args = ["grep", "-q", pattern] + (["--", path] if path else [])
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                              timeout=GIT_TIMEOUT_S).returncode == 0
    if kind == "jq":
        f = repo / path
        try:
            node = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        for key in pattern.lstrip(".").split("."):
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return bool(node)
    raise ValueError(f"unknown detection kind {kind!r}")


def prior_first_use(karta_root: Path, repo_name: str) -> dict[str, dict]:
    """{feature_id: first_use} from the newest committed results file for repo_name."""
    out = _git(karta_root, "ls-files", "--", f"{RESULTS_REL}/*-{repo_name}.json")
    tracked = sorted(out.strip().splitlines())
    if not tracked:
        return {}
    try:
        doc = json.loads((karta_root / tracked[-1]).read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {f["id"]: f["first_use"] for f in doc.get("features", []) if f.get("first_use")}


def audit_repo(repo: Path, feats: list[dict], audit_date: datetime.date,
               prior: dict[str, dict]) -> dict:
    events = activity_events(repo)
    newest_activity = _iso(events[-1]["date"]) if events else None
    rows, lags = [], []
    for feat in feats:
        ship = _iso(feat["ship_date"])
        row = {"id": feat["id"], "karta_version": feat["karta_version"],
               "ship_date": feat["ship_date"], "durable": feat["durable"],
               "detection": feat["detection"], "status": None, "first_use": None,
               "carried_forward": False, "adoption_lag_days": None, "lag_label": None,
               "present_now": None, "days_since_last_activity": None}
        if not feat["durable"]:
            row["status"] = "CURRENT-STATE-ONLY"
            row["present_now"] = present_now(repo, feat["detection"])
        else:
            if feat["id"] in prior:  # committed first_use is immutable — carried verbatim
                row["first_use"] = prior[feat["id"]]
                row["carried_forward"] = True
            else:
                row["first_use"] = first_use(repo, feat["detection"])
            if row["first_use"]:
                row["status"] = "ADOPTED"
                lag = (_iso(row["first_use"]["date"]) - ship).days
                row["adoption_lag_days"] = lag
                if lag < 0:
                    row["lag_label"] = "pre-release-dogfood"
                else:
                    lags.append(lag)
            elif any(_iso(e["date"]) > ship for e in events):
                row["status"] = "AVAILABLE-BUT-UNUSED"
            else:
                row["status"] = "NOT-EXERCISED-SINCE-SHIP"
                if newest_activity:
                    row["days_since_last_activity"] = \
                        (audit_date - newest_activity.date()).days
        rows.append(row)
    alarm = [r["id"] for r in rows if r["status"] == "AVAILABLE-BUT-UNUSED"]
    classes = {c: sum(1 for e in events if e["class"] == c)
               for c in ("plan", "archive", "integration_merge")}
    return {
        "schema_version": 1,
        "vector": "field-feature-adoption-ledger",
        "audit_date": audit_date.isoformat(),
        "repo": repo.name,
        "repo_head": _git(repo, "rev-parse", "HEAD").strip(),
        "activity": {"events_total": len(events), "classes": classes,
                     "newest": events[-1]["date"] if events else None},
        "features": rows,
        "headline": {"available_but_unused": alarm, "count": len(alarm),
                     "denominator": len(rows)},
        "adoption_lag_n": len(lags),
        "adoption_lag_median_days": statistics.median(lags) if len(lags) >= 5 else None,
    }


# ---------------------------------------------------------------- self-test

def _mk_commit(repo: Path, subject: str, date: str, filename: str, content: str,
               extra_parent: str | None = None) -> None:
    (repo / filename).write_text(content)
    env_args = ["-c", "user.name=bench", "-c", "user.email=bench@example.invalid",
                "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", str(repo), *env_args, "add", "-A"],
                   capture_output=True, check=True)
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(repo), *env_args, "commit", "-q", "-m", subject],
                   capture_output=True, check=True, env=env)


def _mk_merge(repo: Path, subject: str, date: str, branch: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    env_args = ["-c", "user.name=bench", "-c", "user.email=bench@example.invalid",
                "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch],
                   capture_output=True, check=True)
    _mk_commit(repo, "work on integration", date, "wt.txt", "work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), *env_args, "merge", "-q", "--no-ff",
                    "-m", subject, branch], capture_output=True, check=True, env=env)


def self_test() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    with tempfile.TemporaryDirectory(prefix="adoption-selftest-") as td:
        tmp = Path(td)

        # fixture "karta" repo: two releases; manifest covering only the first
        fk = tmp / "karta-fixture"
        (fk / "benchmarks" / "field").mkdir(parents=True)
        subprocess.run(["git", "-C", str(fk), "init", "-q", "-b", "main"],
                       capture_output=True, check=True)
        _mk_commit(fk, "init", "2026-01-01T00:00:00+00:00", "seed.txt", "seed")
        _mk_commit(fk, "chore(release): bump plugin version to 0.1.0",
                   "2026-01-02T00:00:00+00:00", "v.txt", "0.1.0")
        _mk_commit(fk, "chore(release): bump plugin version to 0.2.0",
                   "2026-01-03T00:00:00+00:00", "v.txt", "0.2.0")

        releases = enumerate_releases(fk)
        check("release enumeration finds both chore(release) versions in order",
              [v for v, _ in releases] == ["0.1.0", "0.2.0"], str(releases))

        # fixture consumer repo
        fc = tmp / "consumer-fixture"
        fc.mkdir()
        subprocess.run(["git", "-C", str(fc), "init", "-q", "-b", "main"],
                       capture_output=True, check=True)
        _mk_commit(fc, "seed content", "2026-01-01T00:00:00+00:00",
                   "a.txt", "ADOPTED-TOKEN\n")
        _mk_commit(fc, "plan(karta): fixture binder", "2026-01-05T00:00:00+00:00",
                   "plan.txt", "binder\n")
        _mk_merge(fc, "Merge branch 'karta/fix/integration'",
                  "2026-01-06T00:00:00+00:00", "karta/fix/integration")
        head = _git(fc, "rev-parse", "HEAD").strip()
        subprocess.run(["git", "-C", str(fc), "update-ref",
                        "refs/karta/fix/item-a/failed", head],
                       capture_output=True, check=True)

        def feat(fid: str, ship: str, det: dict, durable: bool = True) -> dict:
            return {"id": fid, "karta_version": "0.1.0", "ship_date": ship,
                    "detection": det, "durable": durable}

        feats = [
            feat("feat-adopted", "2025-12-30T00:00:00+00:00",
                 {"kind": "grep", "pattern": "ADOPTED-TOKEN", "path": ""}),
            feat("feat-dogfood", "2026-01-03T00:00:00+00:00",
                 {"kind": "grep", "pattern": "ADOPTED-TOKEN", "path": ""}),
            feat("feat-unused", "2026-01-02T00:00:00+00:00",
                 {"kind": "grep", "pattern": "NEVER-TOKEN-1", "path": ""}),
            feat("feat-idle", "2026-02-01T00:00:00+00:00",
                 {"kind": "grep", "pattern": "NEVER-TOKEN-2", "path": ""}),
            feat("feat-gone", "2025-12-30T00:00:00+00:00",
                 {"kind": "grep", "pattern": "NEVER-TOKEN-3", "path": ""}),
            feat("feat-ephemeral", "2025-12-30T00:00:00+00:00",
                 {"kind": "ref", "pattern": "refs/karta/*/item-*/failed", "path": ""},
                 durable=False),
        ]
        prior = {"feat-gone": {"commit": "deadbeef", "date": "2025-12-31T00:00:00+00:00"}}
        table = audit_repo(fc, feats, datetime.date(2026, 3, 1), prior)
        rows = {r["id"]: r for r in table["features"]}

        check("adopted feature classifies ADOPTED with a 2-day adoption lag",
              rows["feat-adopted"]["status"] == "ADOPTED"
              and rows["feat-adopted"]["adoption_lag_days"] == 2,
              json.dumps(rows["feat-adopted"]))
        check("negative lag is labeled pre-release-dogfood and excluded from the median n",
              rows["feat-dogfood"]["status"] == "ADOPTED"
              and rows["feat-dogfood"]["adoption_lag_days"] < 0
              and rows["feat-dogfood"]["lag_label"] == "pre-release-dogfood"
              and table["adoption_lag_n"] == 2,
              json.dumps(rows["feat-dogfood"]))
        check("never-firing detection with post-ship activity is AVAILABLE-BUT-UNUSED, "
              "the only alarm id",
              rows["feat-unused"]["status"] == "AVAILABLE-BUT-UNUSED"
              and table["headline"]["available_but_unused"] == ["feat-unused"],
              json.dumps(table["headline"]))
        check("feature shipped after all activity is NOT-EXERCISED-SINCE-SHIP with "
              "days_since_last_activity recorded",
              rows["feat-idle"]["status"] == "NOT-EXERCISED-SINCE-SHIP"
              and rows["feat-idle"]["days_since_last_activity"] == 54,
              json.dumps(rows["feat-idle"]))
        check("durable:false detection is CURRENT-STATE-ONLY, present, and excluded "
              "from alarm and lag",
              rows["feat-ephemeral"]["status"] == "CURRENT-STATE-ONLY"
              and rows["feat-ephemeral"]["present_now"] is True
              and "feat-ephemeral" not in table["headline"]["available_but_unused"]
              and rows["feat-ephemeral"]["adoption_lag_days"] is None,
              json.dumps(rows["feat-ephemeral"]))
        check("committed first_use is carried forward verbatim even when the "
              "detection no longer fires",
              rows["feat-gone"]["status"] == "ADOPTED"
              and rows["feat-gone"]["carried_forward"] is True
              and rows["feat-gone"]["first_use"] == prior["feat-gone"],
              json.dumps(rows["feat-gone"]))
        check("denominator counts every manifest feature",
              table["headline"]["denominator"] == 6, str(table["headline"]))
        check("median is withheld below n>=5 (n recorded)",
              table["adoption_lag_median_days"] is None and table["adoption_lag_n"] == 2,
              "")
        check("activity classes count the three pinned classes only",
              table["activity"]["classes"] == {"plan": 1, "archive": 0,
                                               "integration_merge": 1},
              json.dumps(table["activity"]))

        # manifest gate: incomplete manifest -> exit 2 via the real CLI path
        incomplete = {"schema_version": 1, "entries": [
            {"id": "feat-x", "karta_version": "0.1.0",
             "ship_date": "2026-01-02T00:00:00+00:00",
             "detection": {"kind": "grep", "pattern": "X", "path": ""},
             "durable": True}]}
        (fk / MANIFEST_REL).write_text(json.dumps(incomplete))
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--target", str(fk),
             "--consumers", str(fc)], capture_output=True, text=True, timeout=120)
        check("manifest gate exits 2 when a release lacks both an entry and a sentinel",
              proc.returncode == 2 and "0.2.0" in proc.stdout + proc.stderr,
              f"rc={proc.returncode}")
        complete = dict(incomplete)
        complete["entries"] = incomplete["entries"] + [
            {"version": "0.2.0", "observable": False}]
        check("a sentinel satisfies the manifest gate",
              manifest_gate_violations(releases, complete) == [],
              str(manifest_gate_violations(releases, complete)))
        # missing consumer directory is a clear error in live mode
        (fk / MANIFEST_REL).write_text(json.dumps(complete))
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--target", str(fk),
             "--consumers", str(tmp / "no-such-repo")],
            capture_output=True, text=True, timeout=120)
        check("a missing consumer directory is a clear error (exit 3) in live mode",
              proc.returncode == 3 and "no-such-repo" in proc.stdout + proc.stderr,
              f"rc={proc.returncode}")
        # a consumers value naming no paths must refuse, never audit nothing
        for bad in (",", ""):  # set-but-empty in either spelling must refuse
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--target", str(fk),
                 "--consumers", bad],
                capture_output=True, text=True, timeout=120)
            check(f"--consumers {bad!r} is refused, never a vacuous empty audit",
                  proc.returncode != 0 and "names no paths" in proc.stderr,
                  f"rc={proc.returncode}")

    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {detail}"))
    print(f"{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="feature adoption auditor")
    ap.add_argument("--target", type=Path, default=ROOT,
                    help="karta repo root (default: this script's repo)")
    ap.add_argument("--consumers", default=None, metavar="PATH,PATH",
                    help="consumer repo paths (default: sibling parchmark,gringotts "
                         "of the target repo's parent)")
    ap.add_argument("--date", default=datetime.date.today().isoformat(),
                    help="audit date YYYY-MM-DD (default today; names the results files)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    target = args.target.resolve()
    try:
        manifest = load_manifest(target)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read {target / MANIFEST_REL}: {e}")
        return 3

    violations = manifest_gate_violations(enumerate_releases(target), manifest)
    if violations:
        for v in violations:
            print(f"MANIFEST-GATE: {v}")
        return 2

    if args.consumers is not None:  # set-but-empty refuses; only unset falls back
        consumers = [Path(p.strip()).resolve()
                     for p in args.consumers.split(",") if p.strip()]
        if not consumers:  # ""/","/" " must refuse, never a vacuous empty audit
            ap.error(f"--consumers is set but names no paths: {args.consumers!r}")
    else:
        consumers = [target.parent / name for name in DEFAULT_CONSUMERS]
    for repo in consumers:
        if not repo.is_dir():
            print(f"ERROR: enrolled consumer directory missing: {repo} "
                  f"(restore the sibling checkout or pass --consumers)")
            return 3

    audit_date = datetime.date.fromisoformat(args.date)
    feats = features_of(manifest)
    results_dir = target / RESULTS_REL
    results_dir.mkdir(parents=True, exist_ok=True)
    for repo in consumers:
        table = audit_repo(repo, feats, audit_date, prior_first_use(target, repo.name))
        out = results_dir / f"{audit_date.isoformat()}-{repo.name}.json"
        out.write_text(json.dumps(table, indent=2) + "\n")
        rel = out.relative_to(target)
        print(f"RESULTS {rel}")
        h = table["headline"]
        print(f"  {repo.name}: {h['count']}/{h['denominator']} AVAILABLE-BUT-UNUSED "
              f"{h['available_but_unused']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
