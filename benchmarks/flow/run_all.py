#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Composer for the consumer-repo delivery-state audit (field-delivery-state-audit).

Runs the four git-plumbing auditors — lint_delivery_refs, audit_hygiene,
check_markers, audit_binder_mutations — over a repo and composes their findings,
attributing each to its delivery cohort (binder slug + delivery date + the karta
plugin version active at that date from the release ledger). Two modes decide the
verdict and the evidence:

  GATE (fixture)  build the seeded delivery-state fixture, run the full detection
                  matrix, and require 100% detection of every reported violation
                  class with 0 false positives on the two pinned negative cases
                  (clean-ff, discarded). The gate stance: if the fixture matrix is
                  not perfect, every live result is discarded.
  LIVE (evidence) sweep real repos and record per-repo snapshots. Field findings
                  are recorded evidence, never a gate condition.

Before any field reporting the doctrine anchors are content-greped in the karta
skills; a missing sentence hard-fails the run (doctrine changed — update the
linter first). Stdlib only; every path is derived from --target, never hardcoded.

  run_all.py --self-test              # fixture matrix + doctrine + [PASS]/[FAIL] N/N
  run_all.py --target <karta-repo> --repo <repo>   # live audit of one repo, JSON
"""
from __future__ import annotations
import argparse, datetime, json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lint_delivery_refs, audit_hygiene, check_markers, audit_binder_mutations  # noqa: E402

LINTER_VERSION = "1.0.0"
FIXTURE_SH = Path(__file__).resolve().parent / "fixtures" / "delivery-state" / "build_fixture.sh"
FIXTURE_AUDIT_TS = "2026-07-20T00:00:00+00:00"  # pinned; matches build_fixture.sh

# The reported violation classes the seeded fixture must demonstrably detect.
REQUIRED_CLASSES = frozenset({
    "FORGED-DONE-REF", "BUILT-WITHOUT-DONE", "WAVE-TAG-DISORDER",
    "WAVE-BASE-NON-ANCESTOR", "MISSING-KARTA-ACCEPT-TRAILER",
    "LEFTOVER-IN-PROGRESS-REF", "SURVIVOR-POST-ARCHIVE", "COMPLETE-UNARCHIVED",
    "UNMARKED-WORK-COMMIT", "DANGLING-MARKER-ID", "MANUAL-SURGERY",
    "STALE-STASH", "MISSING-DOCTRINE-REF",
})
NEGATIVE_SLUGS = frozenset({"clean-ff", "discarded"})
# Artifact classes that never occur in the field yet — reported n/a, never "0".
FIXTURE_ONLY_CLASSES = frozenset({"MISSING-KARTA-ACCEPT-TRAILER"})

DOCTRINE_ANCHORS = (
    ("skills/karta-deliver/SKILL.md", "refs and wave tags remain in git"),
    ("skills/karta-build/SKILL.md", "Either form satisfies the requirement"),
    ("skills/karta-plan/SKILL.md", "read-only once committed"),
)


def _is_violation(f: dict) -> bool:
    return f.get("class") in REQUIRED_CLASSES or f.get("violation") is True


# --- doctrine anchors ----------------------------------------------------------

def check_doctrine(karta_repo: Path) -> list[str]:
    """Return the anchors whose sentence is gone (empty list == all present)."""
    missing = []
    for rel, sentence in DOCTRINE_ANCHORS:
        p = karta_repo / rel
        try:
            if sentence not in p.read_text(encoding="utf-8"):
                missing.append(f"{rel}: '{sentence}'")
        except OSError:
            missing.append(f"{rel}: unreadable ('{sentence}')")
    return missing


# --- audit composition ---------------------------------------------------------

def audit_all(repo: Path, audit_ts: int) -> list[dict]:
    findings = []
    findings += lint_delivery_refs.audit(repo)
    findings += audit_hygiene.audit(repo, audit_ts)
    findings += check_markers.audit(repo)
    findings += audit_binder_mutations.audit(repo)
    return findings


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                               "GIT_CONFIG_SYSTEM": "/dev/null"})
    return proc.stdout if proc.returncode == 0 else ""


def version_ledger(karta_repo: Path) -> list[tuple[int, str]]:
    """(commit_epoch, version) transitions of .claude-plugin/plugin.json, oldest
    first — the release ledger a cohort's delivery date maps onto."""
    out = _git(karta_repo, "log", "--reverse", "--format=%ct",
               "-p", "--", ".claude-plugin/plugin.json")
    ledger, epoch = [], None
    for line in out.splitlines():
        s = line.strip()
        if s.isdigit():
            epoch = int(s)
        elif s.startswith('+') and '"version"' in s:
            try:
                ver = s.split('"version"')[1].split('"')[1]
                if epoch and (not ledger or ledger[-1][1] != ver):
                    ledger.append((epoch, ver))
            except IndexError:
                pass
    return ledger


def version_at(ledger: list[tuple[int, str]], epoch: int) -> str:
    ver = "unknown"
    for e, v in ledger:
        if e <= epoch:
            ver = v
        else:
            break
    return ver


def _slug_delivery_epoch(repo: Path, slug: str) -> int:
    epochs = []
    for line in _git(repo, "for-each-ref", "--format=%(objectname)",
                     f"refs/karta/{slug}/", f"refs/tags/karta/{slug}/").splitlines():
        sha = line.strip()
        ct = _git(repo, "show", "-s", "--format=%ct", sha).strip()
        if ct.isdigit():
            epochs.append(int(ct))
    return max(epochs) if epochs else 0


def attribute(repo: Path, findings: list[dict], ledger: list[tuple[int, str]]) -> None:
    cache: dict[str, dict] = {}
    for f in findings:
        slug = f.get("slug")
        if slug and slug not in cache:
            ep = _slug_delivery_epoch(repo, slug)
            cache[slug] = {"slug": slug,
                "delivery_date": datetime.date.fromtimestamp(ep).isoformat() if ep else None,
                "plugin_version": version_at(ledger, ep) if ep else "unknown"}
        f["cohort"] = cache.get(slug, {"slug": slug, "delivery_date": None,
                                       "plugin_version": "unknown"})


# --- fixture matrix (the gate) -------------------------------------------------

def build_fixture(dest: Path) -> None:
    r = subprocess.run(["bash", str(FIXTURE_SH), str(dest)],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"fixture build failed: {r.stderr.strip()}")


def fixture_matrix() -> dict:
    with tempfile.TemporaryDirectory() as td:
        fx = Path(td) / "delivery-state"
        build_fixture(fx)
        findings = audit_all(fx, audit_hygiene.parse_ts(FIXTURE_AUDIT_TS))
    detected = {f["class"] for f in findings if f["class"] in REQUIRED_CLASSES}
    missing = sorted(REQUIRED_CLASSES - detected)
    false_positives = [f for f in findings
                       if f.get("slug") in NEGATIVE_SLUGS and _is_violation(f)]
    return {"detected": sorted(detected), "missing": missing,
            "false_positives": false_positives,
            "detection_rate": f"{len(detected)}/{len(REQUIRED_CLASSES)}",
            "negatives_clean": not false_positives,
            "ok": not missing and not false_positives}


# --- live snapshot -------------------------------------------------------------

def live_snapshot(repo: Path, repo_name: str, karta_repo: Path,
                  audit_ts_iso: str) -> dict:
    audit_ts = audit_hygiene.parse_ts(audit_ts_iso)
    ledger = version_ledger(karta_repo)
    findings = audit_all(repo, audit_ts)
    attribute(repo, findings, ledger)
    by_class: dict[str, int] = {}
    for f in findings:
        by_class[f["class"]] = by_class.get(f["class"], 0) + 1
    # never-exercised classes report n/a-fixture-only, never "0 violations"
    class_counts = {c: (by_class.get(c) if c not in FIXTURE_ONLY_CLASSES
                        else "n/a (fixture-only)")
                    for c in sorted(REQUIRED_CLASSES)}
    for c in sorted(FIXTURE_ONLY_CLASSES):
        class_counts[c] = "n/a (fixture-only)"
    return {"schema_version": 1, "repo": repo_name, "linter_version": LINTER_VERSION,
            "audit_timestamp": audit_ts_iso, "finding_count": len(findings),
            "findings": findings, "class_counts": class_counts,
            "adoption_rows": _adoption_rows(findings),
            "card_inconsistencies": [
                "field-delivery-state-audit.md frontmatter `results: benchmarks/field/results/` "
                "contradicts procedure step 7 `benchmarks/flow/results/`; this linter follows "
                "the procedure text (snapshots land under benchmarks/flow/results/) and leaves "
                "the card frontmatter untouched (cards are edited only to flip probe_status)"],
            "metrics": {"stale_artifact_count":
                            sum(1 for f in findings if f["class"].startswith("STALE-")),
                        "manual_surgery_count":
                            sum(1 for f in findings if f["class"] == "MANUAL-SURGERY"),
                        "missing_doctrine_ref_count":
                            sum(1 for f in findings if f["class"] == "MISSING-DOCTRINE-REF")}}


def _adoption_rows(findings: list[dict]) -> list[dict]:
    """binder-EOL-never-invoked / cleanup-not-run facts, exported for the adoption
    ledger to join on later (no cross-item dependency created)."""
    rows = {}
    for f in findings:
        slug = f.get("slug")
        if not slug:
            continue
        row = rows.setdefault(slug, {"slug": slug, "binder_eol_invoked": True,
                                     "cleanup_run": True})
        if f["class"] == "COMPLETE-UNARCHIVED":
            row["binder_eol_invoked"] = False
        if f["class"].startswith("STALE-") or f["class"] == "SURVIVOR-POST-ARCHIVE":
            row["cleanup_run"] = False
    return sorted(rows.values(), key=lambda r: r["slug"])


# --- self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        suffix = f" ({detail})" if detail and not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
        results.append(ok)

    m = fixture_matrix()
    check("every reported violation class is detected on the fixture",
          not m["missing"], f"missing: {m['missing']}")
    check("0 false positives on the two pinned negative cases",
          m["negatives_clean"],
          f"FPs: {[(f['slug'], f['class']) for f in m['false_positives']]}")
    check(f"detection rate is 100% ({m['detection_rate']})", m["ok"])

    # doctrine anchors present in the real karta repo (the linter lives here)
    karta_repo = Path(__file__).resolve().parents[2]
    check("doctrine anchors all present in the karta skills",
          check_doctrine(karta_repo) == [],
          f"missing: {check_doctrine(karta_repo)}")

    # a removed doctrine sentence hard-fails (synthetic skills tree)
    with tempfile.TemporaryDirectory() as td:
        fake = Path(td)
        for rel, sentence in DOCTRINE_ANCHORS:
            p = fake / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("prose without the anchor\n")
        missing = check_doctrine(fake)
        check("a removed doctrine sentence is reported (hard-fail signal)",
              len(missing) == len(DOCTRINE_ANCHORS))

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=".", help="karta repo root (doctrine + ledger)")
    ap.add_argument("--repo", help="repo to audit (defaults to --target)")
    ap.add_argument("--repo-name", help="cohort name for the snapshot")
    ap.add_argument("--audit-timestamp", default=None,
                    help="ISO-8601 recorded audit time (default: now)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()

    karta_repo = Path(args.target).resolve()
    missing = check_doctrine(karta_repo)
    if missing:
        print(json.dumps({"error": "doctrine-anchor-missing", "missing": missing}))
        return 2
    repo = Path(args.repo).resolve() if args.repo else karta_repo
    name = args.repo_name or repo.name
    ts = args.audit_timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(live_snapshot(repo, name, karta_repo, ts), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
