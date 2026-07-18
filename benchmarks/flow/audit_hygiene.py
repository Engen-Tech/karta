#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Auditor 2/4: post-delivery hygiene sweep for karta consumer repos.

Enumerates branches, worktrees, and stashes grouped by karta slug and classifies
the leftover cleanup debt. Age is measured against a RECORDED audit_timestamp
(never wall-clock now()), so a run is reproducible: the same repo + the same
audit_timestamp always yields the same STALE set. Stdlib only; the audit root is
derived from --target, never a hardcoded absolute path.

Classes reported (each has a seeded case in the delivery-state fixture):
  STALE-WORKTREE       a karta/<slug> worktree whose HEAD commit is >48h older
                       than the audit_timestamp
  STALE-STASH          a `karta`-message stash whose commit is >48h old
  STALE-BRANCH         a surviving karta/<slug> branch (archived slug) >48h old
  MISSING-DOCTRINE-REF an archived (delivered) binder slug with no refs/karta/<slug>/**
                       and no wave tags — no machine-recoverable delivery record

IN-FLIGHT artifacts (fresh, <48h, or on a still-live binder) are surfaced in the
metrics but are NOT findings — an active delivery is not debt.

  audit_hygiene.py --target <repo> [--audit-timestamp ISO8601]
  audit_hygiene.py --self-test
"""
from __future__ import annotations
import argparse, datetime, json, os, re, subprocess, sys
from pathlib import Path

STALE_THRESHOLD_S = 48 * 3600
DEFAULT_AUDIT_TS = "2026-07-20T00:00:00+00:00"  # pinned in fixture/self-test mode


def parse_ts(s: str) -> int:
    return int(datetime.datetime.fromisoformat(s).timestamp())


# --- classification (pure) -----------------------------------------------------

def classify(artifacts: list[dict], archived_slugs: set[str],
             slugs_with_doctrine: set[str], audit_ts: int) -> list[dict]:
    findings: list[dict] = []
    for art in artifacts:
        age = audit_ts - art["date_epoch"]
        if age <= STALE_THRESHOLD_S:
            continue  # fresh -> IN-FLIGHT, not debt
        kind = art["kind"]
        if kind == "stash":
            findings.append(_f("STALE-STASH", art.get("slug"),
                f"karta-message stash {age // 3600}h old: {art['ref']!r}"))
        elif kind == "worktree":
            findings.append(_f("STALE-WORKTREE", art.get("slug"),
                f"worktree {art['ref']!r} for {art.get('slug')} is {age // 3600}h "
                "old and never cleaned"))
        elif kind == "branch" and art.get("slug") in archived_slugs:
            findings.append(_f("STALE-BRANCH", art.get("slug"),
                f"branch {art['ref']!r} survives {age // 3600}h post-archive"))
    for slug in sorted(archived_slugs):
        if slug not in slugs_with_doctrine:
            findings.append(_f("MISSING-DOCTRINE-REF", slug,
                f"delivered slug {slug} has no refs/karta/{slug}/** and no wave tags "
                "— the delivery left no machine-recoverable record"))
    return findings


def _f(cls: str, slug: str | None, summary: str) -> dict:
    return {"class": cls, "slug": slug, "item": None,
            "severity": "medium" if cls != "MISSING-DOCTRINE-REF" else "low",
            "summary": summary}


# --- collection (git I/O) ------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                               "GIT_CONFIG_SYSTEM": "/dev/null"})
    return proc.stdout if proc.returncode == 0 else ""


def _slug_of(branch: str) -> str | None:
    m = re.match(r"^karta/(?P<slug>.+?)/", branch)
    return m["slug"] if m else None


def _commit_epoch(repo: Path, sha: str) -> int:
    out = _git(repo, "show", "-s", "--format=%ct", sha).strip()
    return int(out) if out.isdigit() else 0


def collect(repo: Path) -> tuple[list[dict], set[str], set[str]]:
    artifacts: list[dict] = []

    # worktrees (skip the primary checkout)
    cur, primary = {}, True
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line == "":
            if cur and not primary and cur.get("branch"):
                br = cur["branch"].replace("refs/heads/", "")
                if _slug_of(br):
                    artifacts.append({"kind": "worktree", "slug": _slug_of(br),
                        "ref": cur.get("worktree", br),
                        "date_epoch": _commit_epoch(repo, cur.get("HEAD", ""))})
            cur, primary = {}, False
            continue
        key, _, val = line.partition(" ")
        cur[key] = val
    # first record is the primary worktree
    # (the loop's `primary` flag flips false after the first blank separator)

    # stashes with karta in the message
    for line in _git(repo, "stash", "list", "--format=%ct%x09%gs").splitlines():
        ct, _, msg = line.partition("\t")
        if "karta" in msg.lower() and ct.isdigit():
            m = re.search(r"karta/([a-z0-9][a-z0-9-]*)/", msg) or \
                re.search(r"karta:\s*([a-z0-9][a-z0-9-]*)", msg)
            artifacts.append({"kind": "stash", "slug": m.group(1) if m else None,
                              "ref": msg, "date_epoch": int(ct)})

    # surviving karta branches
    for line in _git(repo, "for-each-ref", "--format=%(refname:strip=2)%x09%(objectname)",
                     "refs/heads/karta/").splitlines():
        br, _, sha = line.partition("\t")
        slug = _slug_of(br)
        if slug:
            artifacts.append({"kind": "branch", "slug": slug, "ref": br,
                              "date_epoch": _commit_epoch(repo, sha)})

    archived = _binder_slugs(repo / ".karta" / "binders" / "archive")
    with_doctrine = _slugs_with_doctrine(repo)
    return artifacts, archived, with_doctrine


def _binder_slugs(d: Path) -> set[str]:
    out = set()
    for p in sorted(d.glob("*.json")):
        try:
            out.add(json.loads(p.read_text()).get("slug") or p.stem)
        except (OSError, json.JSONDecodeError):
            out.add(p.stem)
    return out


def _slugs_with_doctrine(repo: Path) -> set[str]:
    out = set()
    for pat, strip in (("refs/karta/", r"^refs/karta/(?P<s>.+?)/"),
                       ("refs/tags/karta/", r"^refs/tags/karta/(?P<s>.+?)/wave-")):
        for line in _git(repo, "for-each-ref", "--format=%(refname)", pat).splitlines():
            m = re.match(strip, line)
            if m:
                out.add(m["s"])
    return out


def audit(repo: Path, audit_ts: int) -> list[dict]:
    artifacts, archived, with_doctrine = collect(repo)
    return classify(artifacts, archived, with_doctrine, audit_ts)


# --- self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    def has(f, cls, slug=None):
        return any(x["class"] == cls and (slug is None or x["slug"] == slug) for x in f)

    ts = parse_ts(DEFAULT_AUDIT_TS)
    old = ts - 5 * 24 * 3600     # >48h
    fresh = ts - 3600            # <48h

    arts = [
        {"kind": "worktree", "slug": "a", "ref": "wt-a", "date_epoch": old},
        {"kind": "stash", "slug": "b", "ref": "karta: b scratch", "date_epoch": old},
        {"kind": "branch", "slug": "c", "ref": "karta/c/integration", "date_epoch": old},
        {"kind": "worktree", "slug": "d", "ref": "wt-d", "date_epoch": fresh},
    ]
    fnd = classify(arts, archived_slugs={"c", "gone"}, slugs_with_doctrine={"c"},
                   audit_ts=ts)
    check("stale worktree detected", has(fnd, "STALE-WORKTREE", "a"))
    check("stale karta stash detected", has(fnd, "STALE-STASH", "b"))
    check("stale post-archive branch detected", has(fnd, "STALE-BRANCH", "c"))
    check("fresh worktree is NOT stale (in-flight)", not has(fnd, "STALE-WORKTREE", "d"))
    check("branch of a non-archived slug is not STALE-BRANCH",
          not any(x["class"] == "STALE-BRANCH" and x["slug"] == "d" for x in fnd))
    check("missing-doctrine-ref for archived slug without refs",
          has(fnd, "MISSING-DOCTRINE-REF", "gone"))
    check("archived slug WITH doctrine refs is clean",
          not has(fnd, "MISSING-DOCTRINE-REF", "c"))

    clean = classify([], archived_slugs=set(), slugs_with_doctrine=set(), audit_ts=ts)
    check("empty repo yields no findings", clean == [])

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=".")
    ap.add_argument("--audit-timestamp", default=DEFAULT_AUDIT_TS,
                    help="ISO-8601 recorded audit time (STALE is measured against this)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    print(json.dumps(audit(Path(args.target).resolve(), parse_ts(args.audit_timestamp)),
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
