#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Auditor 1/4: delivery-ref topology linter for karta consumer repos.

Sweeps a repo's refs/karta/** namespace, refs/tags/karta/** wave tags, and
.karta/binders/{,archive/} against the git invariants karta's delivery doctrine
promises, and reports one finding per violation. Pure git plumbing, stdlib only
(jq field-diffs are done with stdlib json per the binder-wide no-new-dependency
rule; the audit root is derived from --target, never a hardcoded absolute path).

Classes reported (each has a seeded case in the delivery-state fixture):
  FORGED-DONE-REF            a done ref not reachable from the slug's integration
                             branch (or post-merge main when none) — never merged
  BUILT-WITHOUT-DONE         an archived slug's item has a built ref but no
                             done/failed ref (unpaired, stranded)
  WAVE-TAG-DISORDER          wave-<k> is not an ancestor of wave-<k+1>
  WAVE-BASE-NON-ANCESTOR     wave-<N>-base is not an ancestor of wave-<N>
  MISSING-KARTA-ACCEPT-TRAILER   an accepted ref whose commit carries no
                             `Karta-Accept:` trailer (accept flow is fixture-only
                             in the field — never yet exercised on 54 items)
  LEFTOVER-IN-PROGRESS-REF   a stray refs/karta/**/in-progress ref
  SURVIVOR-POST-ARCHIVE      an archived slug with a live refs/heads/karta/<slug>/**
                             branch still standing
  COMPLETE-UNARCHIVED        a live binder whose items are all done and whose
                             delivery landed on main, yet was never archived
                             (the discard path — integration never merged onto
                             main — is exempt)

Collection (git I/O) is kept separate from classification (pure), so --self-test
grades the classifier on synthetic state dicts without fabricating real repos.

  lint_delivery_refs.py --target <repo>   # audit a repo, print findings JSON
  lint_delivery_refs.py --self-test       # embedded fixtures, [PASS]/[FAIL] + N/N
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

DISCARD_EXEMPTION = "the discard path — integration never merged onto main — is exempt"


# --- classification (pure) -----------------------------------------------------

def classify(state: dict) -> list[dict]:
    """Reduce a collected state dict into findings. `state` carries per-slug data
    plus callables: is_ancestor(a, b), reachable(slug, sha), has_accept_trailer(sha)."""
    findings: list[dict] = []
    is_anc = state["is_ancestor"]
    reachable = state["reachable"]
    has_trailer = state["has_accept_trailer"]

    for slug, s in sorted(state["slugs"].items()):
        archived, live = s["archived"], s["live"]
        items = s["items"]

        for iid, refs in sorted(items.items()):
            done, built, failed = refs.get("done"), refs.get("built"), refs.get("failed")
            accepted, inprog = refs.get("accepted"), refs.get("in_progress")
            if done and not reachable(slug, done):
                findings.append(_f("FORGED-DONE-REF", slug, iid,
                    f"done ref for {slug}/item-{iid} points at {done[:9]}, not reachable "
                    "from the integration branch or post-merge main — never merged"))
            if archived and built and not done and not failed:
                findings.append(_f("BUILT-WITHOUT-DONE", slug, iid,
                    f"archived slug {slug} item-{iid} has a built ref with no done/failed "
                    "ref — stranded, unpaired"))
            if accepted and not has_trailer(accepted):
                findings.append(_f("MISSING-KARTA-ACCEPT-TRAILER", slug, iid,
                    f"accepted ref for {slug}/item-{iid} at {accepted[:9]} has no "
                    "Karta-Accept: trailer on its commit"))
            if inprog:
                findings.append(_f("LEFTOVER-IN-PROGRESS-REF", slug, iid,
                    f"stray in-progress ref refs/karta/{slug}/item-{iid}/in-progress "
                    "left standing"))

        waves = s["waves"]
        nums = sorted(waves)
        for n in nums:
            w = waves[n]
            if w.get("tag") and w.get("base") and not is_anc(w["base"], w["tag"]):
                findings.append(_f("WAVE-BASE-NON-ANCESTOR", slug, None,
                    f"{slug} wave-{n}-base is not an ancestor of wave-{n}"))
        for a, b in zip(nums, nums[1:]):
            ta, tb = waves[a].get("tag"), waves[b].get("tag")
            if ta and tb and not is_anc(ta, tb):
                findings.append(_f("WAVE-TAG-DISORDER", slug, None,
                    f"{slug} wave-{a} is not an ancestor of wave-{b} — tags out of order"))

        if archived and s["branches"]:
            findings.append(_f("SURVIVOR-POST-ARCHIVE", slug, None,
                f"archived slug {slug} still has live branch(es): "
                f"{', '.join(sorted(s['branches']))}"))

        if live and not archived and s["binder_item_ids"]:
            all_done = all(items.get(iid, {}).get("done") for iid in s["binder_item_ids"])
            landed = any(reachable(None, items[iid]["done"])  # reachable from main
                         for iid in s["binder_item_ids"]
                         if items.get(iid, {}).get("done")
                         and state["is_ancestor"](items[iid]["done"], state["main_tip"]))
            if all_done and landed:
                findings.append(_f("COMPLETE-UNARCHIVED", slug, None,
                    f"live binder {slug} is complete (all items done) and landed on main "
                    f"but was never archived ({DISCARD_EXEMPTION})"))
    return findings


def _f(cls: str, slug: str, item: str | None, summary: str) -> dict:
    sev = "high" if cls in ("FORGED-DONE-REF", "COMPLETE-UNARCHIVED") else "medium"
    return {"class": cls, "slug": slug, "item": item, "severity": sev, "summary": summary}


# --- collection (git I/O) ------------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    import os
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=60)
    return proc.stdout if proc.returncode == 0 else ""


def _default_branch(repo: Path) -> str:
    for cand in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", cand).strip():
            return cand
    return "HEAD"


def collect(repo: Path) -> dict:
    slugs: dict[str, dict] = {}

    def slot(slug: str) -> dict:
        return slugs.setdefault(slug, {"archived": False, "live": False,
            "items": {}, "waves": {}, "branches": [], "binder_item_ids": set()})

    # refs/karta/<slug>/item-<id>/<kind>
    ref_re = re.compile(r"^refs/karta/(?P<slug>.+?)/item-(?P<item>.+?)/(?P<kind>built|done|failed|accepted|in-progress)$")
    for line in _git(repo, "for-each-ref", "--format=%(objectname) %(refname)",
                     "refs/karta/").splitlines():
        sha, _, refname = line.partition(" ")
        m = ref_re.match(refname)
        if not m:
            continue
        s = slot(m["slug"])
        kind = m["kind"].replace("-", "_")
        s["items"].setdefault(m["item"], {})[kind] = sha

    # refs/tags/karta/<slug>/wave-<N>[-base]
    tag_re = re.compile(r"^karta/(?P<slug>.+?)/wave-(?P<n>\d+)(?P<base>-base)?$")
    for line in _git(repo, "for-each-ref", "--format=%(objectname) %(refname:strip=2)",
                     "refs/tags/karta/").splitlines():
        sha, _, tag = line.partition(" ")
        m = tag_re.match(tag)
        if not m:
            continue
        w = slot(m["slug"])["waves"].setdefault(int(m["n"]), {})
        w["base" if m["base"] else "tag"] = sha

    # refs/heads/karta/<slug>/**  (survivor branches)
    head_re = re.compile(r"^karta/(?P<slug>.+?)/(?P<rest>.+)$")
    for line in _git(repo, "for-each-ref", "--format=%(refname:strip=2)",
                     "refs/heads/karta/").splitlines():
        m = head_re.match(line.strip())
        if m:
            slot(m["slug"])["branches"].append(line.strip())

    # binders live + archived
    for p in sorted((repo / ".karta" / "binders").glob("*.json")):
        _mark_binder(slot, p, archived=False)
    arch = repo / ".karta" / "binders" / "archive"
    for p in sorted(arch.glob("*.json")):
        _mark_binder(slot, p, archived=True)

    default = _default_branch(repo)
    main_tip = _git(repo, "rev-parse", default).strip()

    anc_cache: dict[tuple[str, str], bool] = {}

    def is_ancestor(a: str, b: str) -> bool:
        if not a or not b:
            return False
        key = (a, b)
        if key not in anc_cache:
            import os
            r = subprocess.run(["git", "-C", str(repo), "merge-base",
                                "--is-ancestor", a, b],
                               env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                                    "GIT_CONFIG_SYSTEM": "/dev/null"}, timeout=60)
            anc_cache[key] = r.returncode == 0
        return anc_cache[key]

    def reachable(slug: str | None, sha: str) -> bool:
        if slug is not None:
            intbr = f"karta/{slug}/integration"
            if _git(repo, "rev-parse", "--verify", "--quiet", intbr).strip():
                tip = _git(repo, "rev-parse", intbr).strip()
                return is_ancestor(sha, tip)
        return is_ancestor(sha, main_tip)

    def has_accept_trailer(sha: str) -> bool:
        body = _git(repo, "show", "-s", "--format=%B", sha)
        parsed = subprocess.run(["git", "interpret-trailers", "--parse"],
                                input=body, capture_output=True, text=True)
        return bool(re.search(r"(?im)^Karta-Accept:", parsed.stdout))

    return {"slugs": slugs, "main_tip": main_tip, "is_ancestor": is_ancestor,
            "reachable": reachable, "has_accept_trailer": has_accept_trailer}


def _mark_binder(slot, path: Path, archived: bool) -> None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    slug = data.get("slug") or path.stem
    s = slot(slug)
    s["archived" if archived else "live"] = True
    s["binder_item_ids"] |= {it["id"] for it in data.get("work_items", [])
                             if isinstance(it, dict) and "id" in it}


def audit(repo: Path) -> list[dict]:
    return classify(collect(repo))


# --- self-test -----------------------------------------------------------------

def _synth(ancestry: set, reachable_set: set, trailer_set: set, main_tip="MAIN"):
    def is_anc(a, b):
        return a == b or (a, b) in ancestry
    def reach(slug, sha):
        return (slug, sha) in reachable_set or ("*", sha) in reachable_set
    return {"is_ancestor": is_anc, "reachable": reach,
            "has_accept_trailer": lambda s: s in trailer_set, "main_tip": main_tip}


def _run_self_test() -> int:
    results: list[bool] = []

    def check(name: str, ok: bool) -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    def has(findings, cls, slug=None):
        return any(f["class"] == cls and (slug is None or f["slug"] == slug) for f in findings)

    # clean archived slug -> no findings
    base = _synth(ancestry={("B", "T")}, reachable_set={("clean", "D")}, trailer_set=set())
    st = {**base, "slugs": {"clean": {"archived": True, "live": False,
        "items": {"a": {"built": "D", "done": "D"}}, "waves": {1: {"tag": "T", "base": "B"}},
        "branches": [], "binder_item_ids": {"a"}}}}
    check("clean archived slug yields no findings", classify(st) == [])

    # forged done
    st = {**base, "slugs": {"f": {"archived": True, "live": False,
        "items": {"x": {"built": "D", "done": "Z"}}, "waves": {}, "branches": [],
        "binder_item_ids": {"x"}}}}
    check("forged done ref detected", has(classify(st), "FORGED-DONE-REF"))

    # built without done
    st = {**base, "slugs": {"s": {"archived": True, "live": False,
        "items": {"y": {"built": "D"}}, "waves": {}, "branches": [],
        "binder_item_ids": {"y"}}}}
    check("built-without-done detected", has(classify(st), "BUILT-WITHOUT-DONE"))

    # wave disorder + base non-ancestor
    b2 = _synth(ancestry=set(), reachable_set={("*", "H")}, trailer_set=set())
    st = {**b2, "slugs": {"w": {"archived": True, "live": False, "items": {},
        "waves": {1: {"tag": "H", "base": "H"}, 2: {"tag": "L", "base": "H"}},
        "branches": [], "binder_item_ids": set()}}}
    fnd = classify(st)
    check("wave-tag disorder detected", has(fnd, "WAVE-TAG-DISORDER"))
    check("wave-base non-ancestor detected", has(fnd, "WAVE-BASE-NON-ANCESTOR"))

    # ordered waves -> clean
    b3 = _synth(ancestry={("B", "T1"), ("T1", "T2"), ("B", "T2")}, reachable_set=set(),
                trailer_set=set())
    st = {**b3, "slugs": {"w": {"archived": True, "live": False, "items": {},
        "waves": {1: {"tag": "T1", "base": "B"}, 2: {"tag": "T2", "base": "T1"}},
        "branches": [], "binder_item_ids": set()}}}
    check("ordered waves stay clean", classify(st) == [])

    # accept trailer present vs missing
    st_missing = {**_synth(set(), {("a", "C")}, trailer_set=set()),
        "slugs": {"a": {"archived": True, "live": False,
        "items": {"z": {"done": "C", "accepted": "C"}}, "waves": {}, "branches": [],
        "binder_item_ids": {"z"}}}}
    check("missing Karta-Accept trailer detected",
          has(classify(st_missing), "MISSING-KARTA-ACCEPT-TRAILER"))
    st_ok = {**_synth(set(), {("a", "C")}, trailer_set={"C"}),
        "slugs": {"a": {"archived": True, "live": False,
        "items": {"z": {"done": "C", "accepted": "C"}}, "waves": {}, "branches": [],
        "binder_item_ids": {"z"}}}}
    check("present Karta-Accept trailer stays clean",
          not has(classify(st_ok), "MISSING-KARTA-ACCEPT-TRAILER"))

    # leftover in-progress
    st = {**base, "slugs": {"i": {"archived": True, "live": False,
        "items": {"w": {"done": "D", "in_progress": "D"}}, "waves": {}, "branches": [],
        "binder_item_ids": {"w"}}}}
    st["reachable"] = lambda s, sha: True
    check("leftover in-progress ref detected", has(classify(st), "LEFTOVER-IN-PROGRESS-REF"))

    # survivor branch post-archive
    st = {**base, "slugs": {"sv": {"archived": True, "live": False, "items": {},
        "waves": {}, "branches": ["karta/sv/integration"], "binder_item_ids": set()}}}
    check("survivor branch post-archive detected", has(classify(st), "SURVIVOR-POST-ARCHIVE"))

    # complete-unarchived (landed on main) vs discarded (not on main)
    bcu = _synth(ancestry={("D", "MAIN")}, reachable_set={("*", "D")}, trailer_set=set())
    st = {**bcu, "slugs": {"cu": {"archived": False, "live": True,
        "items": {"o": {"done": "D"}}, "waves": {}, "branches": [], "binder_item_ids": {"o"}}}}
    check("complete-unarchived detected when landed on main",
          has(classify(st), "COMPLETE-UNARCHIVED"))
    bdisc = _synth(ancestry=set(), reachable_set={("disc", "D")}, trailer_set=set())
    st = {**bdisc, "slugs": {"disc": {"archived": False, "live": True,
        "items": {"o": {"done": "D"}}, "waves": {}, "branches": ["karta/disc/integration"],
        "binder_item_ids": {"o"}}}}
    check("discarded (integration not on main) is exempt",
          not has(classify(st), "COMPLETE-UNARCHIVED"))

    total, passed = len(results), sum(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=".", help="repo root to audit")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    print(json.dumps(audit(Path(args.target).resolve()), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
