#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Auditor 3/4: commit-marker conformance for karta consumer repos.

For every delivered slug that carries a wave-base tag, walks the delivery's work
commits (`git rev-list <done-tips> ^<wave-1-base> --no-merges`) and checks that
each carries a sanctioned karta marker — either a subject `[karta:item-<id>]`
prefix or a `Karta-Item: item-<id>` trailer (the two forms karta-build's doctrine
sentence "Either form satisfies the requirement" blesses) — and that the marker's
id is a real item id in the slug's binder. Stdlib only; the audit root comes from
--target, never a hardcoded absolute path.

Classes reported (each has a seeded case in the delivery-state fixture):
  UNMARKED-WORK-COMMIT  a non-merge work commit in range with neither marker form
  DANGLING-MARKER-ID    a marker whose item id is absent from the slug's binder

  check_markers.py --target <repo>
  check_markers.py --self-test
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

SUBJECT_RE = re.compile(r"^\[karta:item-(?P<id>[a-z0-9-]+)\]")
TRAILER_RE = re.compile(r"(?im)^Karta-Item:\s*item-(?P<id>[a-z0-9-]+)\s*$")


# --- classification (pure) -----------------------------------------------------

def marker_id(subject: str, body: str) -> str | None:
    m = SUBJECT_RE.match(subject)
    if m:
        return m["id"]
    t = TRAILER_RE.search(body)
    return t["id"] if t else None


def classify(commits: list[dict], binder_item_ids: set[str], slug: str) -> list[dict]:
    findings: list[dict] = []
    for c in commits:
        mid = marker_id(c["subject"], c.get("body", ""))
        if mid is None:
            findings.append({"class": "UNMARKED-WORK-COMMIT", "slug": slug,
                "item": None, "severity": "medium",
                "summary": f"{slug} work commit {c['sha'][:9]} carries no karta marker "
                           f"(subject: {c['subject']!r})"})
        elif mid not in binder_item_ids:
            findings.append({"class": "DANGLING-MARKER-ID", "slug": slug,
                "item": mid, "severity": "medium",
                "summary": f"{slug} commit {c['sha'][:9]} marks item id {mid!r}, absent "
                           "from the binder's work_items"})
    return findings


# --- collection (git I/O) ------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                               "GIT_CONFIG_SYSTEM": "/dev/null"})
    return proc.stdout if proc.returncode == 0 else ""


def _binders_by_slug(repo: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for d in (repo / ".karta" / "binders", repo / ".karta" / "binders" / "archive"):
        for p in sorted(d.glob("*.json")):
            try:
                data = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            slug = data.get("slug") or p.stem
            out.setdefault(slug, set()).update(
                it["id"] for it in data.get("work_items", [])
                if isinstance(it, dict) and "id" in it)
    return out


def collect(repo: Path) -> list[tuple[str, list[dict], set[str]]]:
    """Return (slug, commits, binder_item_ids) for each slug with a wave-1 base."""
    binders = _binders_by_slug(repo)

    # slug -> {lowest-N wave base sha, done tips}
    bases: dict[str, tuple[int, str]] = {}
    for line in _git(repo, "for-each-ref",
                     "--format=%(objectname) %(refname:strip=2)",
                     "refs/tags/karta/").splitlines():
        sha, _, tag = line.partition(" ")
        m = re.match(r"^karta/(?P<slug>.+?)/wave-(?P<n>\d+)-base$", tag)
        if m:
            n = int(m["n"])
            cur = bases.get(m["slug"])
            if cur is None or n < cur[0]:
                bases[m["slug"]] = (n, sha)

    dones: dict[str, list[str]] = {}
    for line in _git(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                     "refs/karta/").splitlines():
        sha, _, refname = line.partition(" ")
        m = re.match(r"^refs/karta/(?P<slug>.+?)/item-.+?/done$", refname)
        if m:
            dones.setdefault(m["slug"], []).append(sha)

    out = []
    for slug, (_, base) in sorted(bases.items()):
        tips = dones.get(slug, [])
        if not tips:
            continue
        raw = _git(repo, "rev-list", "--no-merges", *tips, f"^{base}").split()
        commits = []
        for sha in raw:
            subject = _git(repo, "show", "-s", "--format=%s", sha).strip()
            body = _git(repo, "show", "-s", "--format=%B", sha)
            commits.append({"sha": sha, "subject": subject, "body": body})
        out.append((slug, commits, binders.get(slug, set())))
    return out


def audit(repo: Path) -> list[dict]:
    findings = []
    for slug, commits, ids in collect(repo):
        findings += classify(commits, ids, slug)
    return findings


# --- self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    def has(f, cls):
        return any(x["class"] == cls for x in f)

    subj = [
        {"sha": "aaa", "subject": "[karta:item-good] work", "body": "[karta:item-good] work"},
        {"sha": "bbb", "subject": "feat: no marker", "body": "feat: no marker\n"},
        {"sha": "ccc", "subject": "feat(x): via trailer",
         "body": "feat(x): via trailer\n\nKarta-Item: item-good\n"},
        {"sha": "ddd", "subject": "[karta:item-ghost] work",
         "body": "[karta:item-ghost] work"},
    ]
    fnd = classify(subj, binder_item_ids={"good"}, slug="s")
    check("subject marker accepted", not any(x["item"] == "good" for x in fnd))
    check("unmarked commit detected", has(fnd, "UNMARKED-WORK-COMMIT"))
    check("Karta-Item trailer form accepted (no finding for ccc)",
          not any(x["slug"] == "s" and x.get("summary", "").startswith("s work commit ccc")
                  for x in fnd) and marker_id(subj[2]["subject"], subj[2]["body"]) == "good")
    check("dangling marker id detected", has(fnd, "DANGLING-MARKER-ID"))
    clean = classify([{"sha": "z", "subject": "[karta:item-good] w",
                       "body": "[karta:item-good] w"}], {"good"}, "s")
    check("fully-marked in-binder commit is clean", clean == [])
    check("empty commit list is clean", classify([], {"good"}, "s") == [])

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
