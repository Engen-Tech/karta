#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Auditor 4/4: binder-mutation audit for karta consumer repos.

Walks each binder's post-birth commit history and classifies every change to the
committed binder blob by field-level diff. The binder is doctrine that is
"read-only once committed" (karta-plan) except through three sanctioned moves —
a re-plan, an archive move, or an additive metadata retrofit; anything else is
MANUAL-SURGERY, the one class that counts as a violation. jq field diffs are done
with stdlib json (no jq binary), per the binder-wide no-new-dependency rule; the
audit root comes from --target, never a hardcoded absolute path.

Classes (MANUAL-SURGERY is the violation; the other three are the sanctioned
mutation vocabulary, reported as informational evidence):
  ARCHIVE-MOVE       binder path moved to/from archive/, blob content unchanged
  RE-PLAN            the work_items set changed (item id added or removed)
  METADATA-RETROFIT  a top-level key was added, no existing value changed
  MANUAL-SURGERY     an existing field's value was edited in place — a hand edit

  audit_binder_mutations.py --target <repo>
  audit_binder_mutations.py --self-test
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

VIOLATION_CLASS = "MANUAL-SURGERY"


# --- classification (pure) -----------------------------------------------------

def _item_ids(obj: dict) -> set[str]:
    return {it["id"] for it in obj.get("work_items", [])
            if isinstance(it, dict) and "id" in it}


def _in_archive(path: str) -> bool:
    return "/archive/" in path


def classify_change(prev_obj: dict, curr_obj: dict,
                    prev_path: str, curr_path: str) -> str | None:
    """Classify one binder blob change into exactly one mutation class, or None
    when nothing material changed (pure formatting)."""
    if _in_archive(prev_path) != _in_archive(curr_path) and prev_obj == curr_obj:
        return "ARCHIVE-MOVE"
    if prev_obj == curr_obj:
        return None
    if _item_ids(prev_obj) != _item_ids(curr_obj):
        return "RE-PLAN"
    prev_keys, curr_keys = set(prev_obj), set(curr_obj)
    added, removed = curr_keys - prev_keys, prev_keys - curr_keys
    changed_existing = any(prev_obj.get(k) != curr_obj.get(k)
                           for k in prev_keys & curr_keys)
    if added and not removed and not changed_existing:
        return "METADATA-RETROFIT"
    return "MANUAL-SURGERY"


def to_finding(cls: str, slug: str, sha: str, prev_path: str, curr_path: str) -> dict:
    violation = cls == VIOLATION_CLASS
    return {"class": cls, "slug": slug, "item": None,
            "severity": "high" if violation else "info", "violation": violation,
            "summary": f"{slug} binder commit {sha[:9]}: {cls}"
                       + (f" ({prev_path} -> {curr_path})" if prev_path != curr_path else "")}


# --- collection (git I/O) ------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                               "GIT_CONFIG_SYSTEM": "/dev/null"})


def _blob(repo: Path, sha: str, path: str) -> dict | None:
    p = _git(repo, "show", f"{sha}:{path}")
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def _history(repo: Path, path: str) -> list[tuple[str, str, str]]:
    """Oldest-first (sha, status, path-at-commit / 'oldpath\\tnewpath' for renames)."""
    # --follow misbehaves with --reverse, so collect newest-first and reverse here.
    out = _git(repo, "log", "--follow", "--format=__C__%H",
               "--name-status", "--", path)
    seq, sha = [], None
    for line in out.stdout.splitlines():
        if line.startswith("__C__"):
            sha = line[5:].strip()
        elif line.strip() and sha:
            parts = line.split("\t")
            seq.append((sha, parts[0], "\t".join(parts[1:])))
    return list(reversed(seq))


def audit(repo: Path) -> list[dict]:
    findings: list[dict] = []
    paths = set()
    for d in (repo / ".karta" / "binders", repo / ".karta" / "binders" / "archive"):
        for p in sorted(d.glob("*.json")):
            paths.add(str(p.relative_to(repo)))
    seen_slugs: set[str] = set()
    for path in sorted(paths):
        hist = _history(repo, path)
        if not hist:
            continue
        # reconstruct (sha, path) per step; the last path is `path`
        prev_obj, prev_path = None, None
        slug = None
        for sha, status, spec in hist:
            if status.startswith("R"):
                old, new = spec.split("\t")
                cur_path = new
            else:
                cur_path = spec
            curr_obj = _blob(repo, sha, cur_path)
            if curr_obj is None:
                prev_path = cur_path
                continue
            if slug is None:
                slug = curr_obj.get("slug") or Path(cur_path).stem
            if slug in seen_slugs and prev_obj is None:
                break  # this binder's history already covered via another path
            if prev_obj is not None:
                cls = classify_change(prev_obj, curr_obj, prev_path or cur_path, cur_path)
                if cls:
                    findings.append(to_finding(cls, slug, sha, prev_path or cur_path,
                                               cur_path))
            prev_obj, prev_path = curr_obj, cur_path
        if slug:
            seen_slugs.add(slug)
    return findings


# --- self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    live = ".karta/binders/x.json"
    arch = ".karta/binders/archive/x.json"
    base = {"slug": "x", "work_items": [{"id": "a"}]}

    check("archive move (content identical) -> ARCHIVE-MOVE",
          classify_change(base, base, live, arch) == "ARCHIVE-MOVE")
    check("added top-level key -> METADATA-RETROFIT",
          classify_change(base, {**base, "shared_terms": []}, live, live)
          == "METADATA-RETROFIT")
    check("added work item -> RE-PLAN",
          classify_change(base, {"slug": "x", "work_items": [{"id": "a"}, {"id": "b"}]},
                          live, live) == "RE-PLAN")
    check("in-place item edit (same ids) -> MANUAL-SURGERY",
          classify_change(base, {"slug": "x", "work_items": [{"id": "a", "title": "z"}]},
                          live, live) == "MANUAL-SURGERY")
    check("top-level value bump -> MANUAL-SURGERY",
          classify_change({**base, "runtime_contract": {"v": 1}},
                          {**base, "runtime_contract": {"v": 2}}, live, live)
          == "MANUAL-SURGERY")
    check("no material change -> None",
          classify_change(base, dict(base), live, live) is None)
    check("MANUAL-SURGERY is the only violation",
          to_finding("MANUAL-SURGERY", "x", "abc", live, live)["violation"] is True
          and to_finding("ARCHIVE-MOVE", "x", "abc", live, arch)["violation"] is False)

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=".")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    print(json.dumps(audit(Path(args.target).resolve()), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
