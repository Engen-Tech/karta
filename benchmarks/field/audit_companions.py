#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Companion-loop health auditor: one JSON row for one consumer repo.

Walks a repo's git history to report whether the two opt-in companion loops
(doc-gardner, kaizen) fire on eligible deliveries and whether their edits stick:
gardner coverage per delivery (timing-race whiffs auto-attributed), the kaizen
additive floor (via the imported validate_packs grammar, never re-implemented),
correction incidents in a right-censored 0-14 day window, spawn utility, and
config staleness. Stdlib-only argparse with a --self-test mode printing
[PASS]/[FAIL] lines and an N/N checks passed summary (--self-test exits 0 only
when the summary is N/N checks passed, nonzero otherwise).

Usage:
  python3 benchmarks/field/audit_companions.py --repo <path> [--head <sha>]
  python3 benchmarks/field/audit_companions.py --self-test

When --head is given, every git read is scoped to that sha. Every fraction is
reported as n/N with its underlying list, and every row carries the analyzed
HEAD sha plus the no-version-stamp caveat.

DECLARED DEVIATIONS from the vector card (emitted as a permanent
card-contradiction finding in every row, for card maintenance): (a) the card's
steps 1/3 pin first-parent-only history walks, but subject-stream matching
(docs: gardner, kaizen:, correction commits) walks the full merge-reachable
history of the analyzed head (git log <head>, never --all) — first-parent is
reserved for epoch transitions and delivery enumeration; (b) the delivery
denominator is first-parent merges of karta/<slug>/integration, with
first-parent commits adding .karta/binders/archive/<slug>.json as the fallback
for fast-forward landings (dedupe by slug, merge wins), each dated by that
commit's committer date; epoch membership is strictly-after the first-parent
enable-transition commit.
"""
from __future__ import annotations
import argparse, importlib.util, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

CONFIGS = (".karta/doc-gardner.json", ".karta/kaizen.json")
PACK_RE = re.compile(r"^\.karta/sme/[^/]+\.md$")
ARCHIVE_RE = re.compile(r"^\.karta/binders/archive/([^/]+)\.json$")
INTEGRATION_RE = re.compile(r"karta/([A-Za-z0-9._-]+)/integration")
KAIZEN_RE = re.compile(r"^kaizen: ")
SEED_RE = re.compile(r"^kaizen: seed\b")
WINDOW_S = 14 * 86400
SHRINK_FRACTION = 0.20
CAVEAT = ("deliveries carry no plugin-version stamp, so this is loop health "
          "over calendar time, not a controlled version A/B")
CARD_CONTRADICTION = {
    "finding_id": "card-contradiction-history-walk",
    "severity": "info",
    "summary": ("permanent card contradiction: card steps 1/3 pin first-parent-only "
                "history walks, but they contradict the card's own step 4 and seed "
                "observation on the real repos — subject-stream matching (docs: "
                "gardner, kaizen:, correction commits) walks the full merge-reachable "
                "history of the analyzed head (never --all); first-parent is reserved "
                "for epoch transitions and delivery enumeration; the delivery "
                "denominator is integration merges with archive-adds as the "
                "fast-forward fallback (dedupe by slug, merge wins)"),
}
ROW_KEYS = ("repo", "head", "caveat", "deliveries", "epochs", "gardner",
            "kaizen", "config_staleness", "findings")


class GrammarImportError(RuntimeError):
    """The validate_packs grammar could not be imported (fail-closed)."""


class AuditError(RuntimeError):
    """The audited repo could not be read (bad path, git failure)."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AuditError(f"git {' '.join(args[:3])}... failed: "
                         f"{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else proc.returncode}")
    return proc.stdout


def load_grammar(karta_root: Path):
    """Import ITEM_RE and TOMBSTONE_RE from validate_packs.py — never re-implement.

    spec_from_file_location because the hyphenated directory name is not a valid
    package path; validate_packs.py has no standalone extraction function."""
    path = karta_root / "skills" / "karta-kaizen" / "scripts" / "validate_packs.py"
    try:
        spec = importlib.util.spec_from_file_location("karta_validate_packs", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no import spec for {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ITEM_RE, mod.TOMBSTONE_RE
    except Exception as e:  # noqa: BLE001 — any failure is a probe-integrity failure
        raise GrammarImportError(f"parser-import-failure: cannot import ITEM_RE/"
                                 f"TOMBSTONE_RE from {path} ({e})") from e


def _stream(repo: Path, head: str, *extra: str, path: str | None = None) -> list[dict]:
    """Commits as {sha, ct, merge, subject}, newest first, merge-reachable from head."""
    args = ["log", "--format=%H%x00%ct%x00%P%x00%s", *extra, head]
    if path is not None:
        args += ["--", path]
    out = []
    for ln in _git(repo, *args).splitlines():
        sha, ct, parents, subject = ln.split("\0", 3)
        out.append({"sha": sha, "ct": int(ct),
                    "merge": len(parents.split()) > 1, "subject": subject})
    return out


def _touched(repo: Path, sha: str) -> list[str]:
    """Files changed by a commit, diffed against its first parent (root-safe)."""
    parents = _git(repo, "rev-list", "--parents", "-n1", sha).split()
    if len(parents) == 1:
        out = _git(repo, "diff-tree", "--root", "-r", "--name-only",
                   "--no-commit-id", sha)
    else:
        out = _git(repo, "diff-tree", "-r", "--name-only", "--no-commit-id",
                   f"{sha}^1", sha)
    return [ln for ln in out.splitlines() if ln]


def _show(repo: Path, sha: str, path: str) -> str | None:
    proc = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{path}"],
                          capture_output=True, text=True, timeout=60)
    return proc.stdout if proc.returncode == 0 else None


def _epochs(repo: Path, head: str, config: str) -> dict:
    """Enabled-value transitions on first-parent history (a --diff-filter=A walk
    alone would miss disable/re-enable toggles), oldest first."""
    commits = list(reversed(_stream(repo, head, "--first-parent", "--follow",
                                    path=config)))
    transitions, intervals = [], []
    prev = False
    for c in commits:
        text = _show(repo, c["sha"], config)
        enabled = False
        if text is not None:
            try:
                enabled = bool(json.loads(text).get("enabled") is True)
            except json.JSONDecodeError:
                enabled = False
        if enabled != prev:
            transitions.append({"sha": c["sha"], "ct": c["ct"], "to": enabled})
            if enabled:
                intervals.append({"enable_sha": c["sha"], "enable_ct": c["ct"],
                                  "end_ct": None})
            elif intervals:
                intervals[-1]["end_ct"] = c["ct"]
        prev = enabled
    return {"transitions": transitions, "intervals": intervals, "enabled_now": prev}


def _interval_for(ct: int, intervals: list[dict]) -> dict | None:
    """The enabled interval containing ct — membership strictly-after the
    enable-transition commit."""
    for iv in intervals:
        if ct > iv["enable_ct"] and (iv["end_ct"] is None or ct < iv["end_ct"]):
            return iv
    return None


def _deliveries(repo: Path, head: str) -> list[dict]:
    """Integration merges + archive-add fallback, dedupe by slug (merge wins)."""
    by_slug: dict[str, dict] = {}
    for c in _stream(repo, head, "--first-parent", "--merges"):
        m = INTEGRATION_RE.search(c["subject"])
        if m:
            slug = m.group(1)
            cur = by_slug.get(slug)
            if cur is None or cur["via"] != "integration-merge" or c["ct"] > cur["ct"]:
                by_slug[slug] = {"slug": slug, "sha": c["sha"], "ct": c["ct"],
                                 "via": "integration-merge"}
    out = _git(repo, "log", "--first-parent", "--diff-filter=A",
               "--diff-merges=first-parent", "--name-only",
               "--format=%x01%H%x00%ct", head, "--", ".karta/binders/archive/")
    for block in out.split("\x01"):
        if not block.strip():
            continue
        header, _, names = block.partition("\n")
        sha, ct = header.split("\0")
        for name in names.splitlines():
            m = ARCHIVE_RE.match(name.strip())
            if not m:
                continue
            slug = m.group(1)
            if slug not in by_slug:  # merge wins; first archive-add otherwise
                by_slug[slug] = {"slug": slug, "sha": sha, "ct": int(ct),
                                 "via": "archive-add"}
    return sorted(by_slug.values(), key=lambda d: (d["ct"], d["slug"]))


def _oldest_lineage_ct(repo: Path, merge_sha: str) -> int | None:
    """Committer date of the oldest commit on the merge's second-parent lineage."""
    try:
        cts = [int(x) for x in
               _git(repo, "log", "--format=%ct",
                    f"{merge_sha}^1..{merge_sha}^2").split()]
        if not cts:
            cts = [int(_git(repo, "log", "-1", "--format=%ct",
                            f"{merge_sha}^2").strip())]
        return min(cts)
    except (AuditError, ValueError):
        return None


def _gardner(repo: Path, head: str, deliveries: list[dict], intervals: list[dict],
             full_stream: list[dict]) -> dict:
    subjects = {}
    for c in full_stream:
        subjects.setdefault(c["subject"], c)
    per_slug, excluded, covered_n = [], [], 0
    for d in deliveries:
        iv = _interval_for(d["ct"], intervals)
        if iv is None:
            excluded.append({"slug": d["slug"], "delivery_ct": d["ct"],
                             "reason": "pre-enable"})
            continue
        hit = subjects.get(f"docs: gardner {d['slug']}")
        row = {"slug": d["slug"], "delivery_sha": d["sha"], "delivery_ct": d["ct"],
               "via": d["via"], "enable_ct": iv["enable_ct"],
               "covered": hit is not None,
               "gardner_sha": hit["sha"] if hit else None,
               "gardner_ct": hit["ct"] if hit else None,
               "whiff_class": None, "lineage_oldest_ct": None}
        if hit:
            covered_n += 1
        else:
            oldest = (_oldest_lineage_ct(repo, d["sha"])
                      if d["via"] == "integration-merge" else None)
            row["lineage_oldest_ct"] = oldest
            row["whiff_class"] = ("timing-race"
                                  if oldest is not None and oldest < iv["enable_ct"]
                                  else "true-whiff")
        per_slug.append(row)
    n_total = len(per_slug)
    return {"fraction": f"{covered_n}/{n_total}", "covered_n": covered_n,
            "denominator_N": n_total, "per_slug": per_slug,
            "whiffs": [r for r in per_slug if not r["covered"]],
            "excluded_pre_enable": excluded}


def _parse_pack(text: str, item_re, tombstone_re) -> tuple[dict[str, str], set[str]]:
    active: dict[str, str] = {}
    tombstones: set[str] = set()
    for ln in text.splitlines():
        if m := item_re.match(ln):
            active[f"{m.group(1)}.{m.group(2)}"] = m.group(3)
        elif m := tombstone_re.match(ln):
            tombstones.add(f"{m.group(1)}.{m.group(2)}")
    return active, tombstones


def _kaizen(repo: Path, head: str, full_stream: list[dict], deliveries: list[dict],
            intervals: list[dict], grammar, now: int) -> dict:
    item_re, tombstone_re = grammar
    kaizen = [dict(c, seed=bool(SEED_RE.match(c["subject"])),
                   window_open=(now - c["ct"] < WINDOW_S))
              for c in full_stream if KAIZEN_RE.match(c["subject"])]
    floor_fail, floor_warn, incidents = [], [], []
    touched_cache: dict[str, list[str]] = {}

    def touched(sha: str) -> list[str]:
        if sha not in touched_cache:
            touched_cache[sha] = _touched(repo, sha)
        return touched_cache[sha]

    for k in kaizen:
        packs = [p for p in touched(k["sha"]) if PACK_RE.match(p)]
        k["touched_packs"] = packs
        for pack in packs:
            before = _show(repo, f"{k['sha']}^", pack) or ""
            after = _show(repo, k["sha"], pack) or ""
            b_active, _ = _parse_pack(before, item_re, tombstone_re)
            a_active, a_tomb = _parse_pack(after, item_re, tombstone_re)
            for rid, rtext in b_active.items():
                if rid not in a_active:
                    if rid not in a_tomb:
                        floor_fail.append({"kaizen_sha": k["sha"], "pack": pack,
                                           "id": rid,
                                           "summary": f"active id {rid} vanished "
                                                      f"without a tombstone"})
                elif len(a_active[rid]) < len(rtext) * (1 - SHRINK_FRACTION):
                    floor_warn.append({"kaizen_sha": k["sha"], "pack": pack,
                                       "id": rid,
                                       "summary": f"rule text of {rid} shrank "
                                                  f">{int(SHRINK_FRACTION * 100)}% "
                                                  f"({len(rtext)} -> {len(a_active[rid])} chars)"})
        # correction incidents: non-kaizen commits touching a kaizen-touched pack
        # file 0-14 days AFTER the kaizen commit (window starts at 0). Merge
        # commits are skipped: a merge's first-parent diff replays its branch's
        # own edits (including the kaizen commit's), and any real correction
        # inside a merged branch appears as its own commit in the full stream.
        for c in full_stream:
            if (c["sha"] == k["sha"] or c["merge"]
                    or KAIZEN_RE.match(c["subject"])
                    or not 0 <= c["ct"] - k["ct"] <= WINDOW_S):
                continue
            if any(p in packs for p in touched(c["sha"])):
                incidents.append({"kaizen_sha": k["sha"], "correcting_sha": c["sha"],
                                  "delta_minutes": (c["ct"] - k["ct"]) // 60,
                                  "subject": c["subject"]})

    substantive = [k for k in kaizen if not k["seed"]]
    in_epoch = [d for d in deliveries if _interval_for(d["ct"], intervals)]
    closed = [k for k in kaizen if not k["window_open"]]
    return {
        "commits": [{k2: k[k2] for k2 in
                     ("sha", "ct", "subject", "seed", "window_open", "touched_packs")}
                    for k in kaizen],
        "floor": {"fail": floor_fail, "warn": floor_warn,
                  "checked_n": len(kaizen)},
        "incidents": sorted(incidents, key=lambda i: (i["kaizen_sha"],
                                                      i["delta_minutes"])),
        "closed_denominator_n": len(closed),
        "closed_denominator": [k["sha"] for k in closed],
        "window_open": [k["sha"] for k in kaizen if k["window_open"]],
        "spawn_utility": {
            "substantive_kaizen_commits_n": len(substantive),
            "substantive_kaizen_commits": [k["sha"] for k in substantive],
            "seed_excluded": [k["sha"] for k in kaizen if k["seed"]],
            "deliveries_in_kaizen_epoch_n": len(in_epoch),
            "deliveries_in_kaizen_epoch": [d["slug"] for d in in_epoch],
        },
    }


def _staleness(repo: Path, head: str, deliveries: list[dict]) -> list[dict]:
    out = []
    last5 = [d["slug"] for d in deliveries[-5:]]
    for config in CONFIGS:
        touches = _stream(repo, head, "--first-parent", "--follow", path=config)
        entry = {"config": config, "last_touch_sha": None, "last_touch_ct": None,
                 "deliveries_since_n": 0, "deliveries_since": [],
                 "focus": None, "last_5_delivery_slugs": last5}
        if touches:
            newest = touches[0]
            since = [d["slug"] for d in deliveries if d["ct"] > newest["ct"]]
            entry.update({"last_touch_sha": newest["sha"],
                          "last_touch_ct": newest["ct"],
                          "deliveries_since_n": len(since),
                          "deliveries_since": since})
        text = _show(repo, head, config)
        if text is not None:
            try:
                entry["focus"] = json.loads(text).get("focus")
            except json.JSONDecodeError:
                pass
        out.append(entry)
    return out


def audit(repo: Path, head: str | None, grammar, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    head_sha = _git(repo, "rev-parse", head or "HEAD").strip()
    full_stream = _stream(repo, head_sha)
    gardner_epochs = _epochs(repo, head_sha, ".karta/doc-gardner.json")
    kaizen_epochs = _epochs(repo, head_sha, ".karta/kaizen.json")
    deliveries = _deliveries(repo, head_sha)
    return {
        "repo": repo.name,
        "head": head_sha,
        "caveat": CAVEAT,
        "deliveries": deliveries,
        "epochs": {".karta/doc-gardner.json": gardner_epochs,
                   ".karta/kaizen.json": kaizen_epochs},
        "gardner": _gardner(repo, head_sha, deliveries,
                            gardner_epochs["intervals"], full_stream),
        "kaizen": _kaizen(repo, head_sha, full_stream, deliveries,
                          kaizen_epochs["intervals"], grammar, now),
        "config_staleness": _staleness(repo, head_sha, deliveries),
        "findings": [CARD_CONTRADICTION],
    }


# --- Self-test fixtures --------------------------------------------------------

T0 = 1700000000


def _run(repo: Path, *args: str, env: dict | None = None) -> None:
    e = dict(os.environ)
    if env:
        e.update(env)
    subprocess.run(["git", "-C", str(repo), *args], env=e, check=True,
                   capture_output=True, text=True, timeout=60)


def _commit(repo: Path, msg: str, ct: int,
            files: dict[str, str | None] | None = None) -> None:
    for rel, content in (files or {}).items():
        p = repo / rel
        if content is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "--allow-empty", "-m", msg,
         env={"GIT_AUTHOR_DATE": f"{ct} +0000", "GIT_COMMITTER_DATE": f"{ct} +0000"})


def _merge(repo: Path, branch: str, msg: str, ct: int) -> None:
    _run(repo, "merge", "--no-ff", "-q", "-m", msg, branch,
         env={"GIT_AUTHOR_DATE": f"{ct} +0000", "GIT_COMMITTER_DATE": f"{ct} +0000"})


def _init(repo: Path) -> None:
    _run(repo.parent, "init", "-q", "-b", "main", str(repo))
    _run(repo, "config", "user.name", "bench")
    _run(repo, "config", "user.email", "bench@test")


def _sha(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", rev).strip()


PACK_V0 = """---
name: testpack
description: test
always: true
---
## Review checklist
- [ ] tp.1 — Rule one text here for testing purposes.
- [ ] tp.2 — Rule two text that is long enough to shrink meaningfully later on.
"""


def _build_repo_a(root: Path) -> Path:
    repo = root / "repo-a"
    _init(repo)
    _commit(repo, "init", T0, {"README.md": "a\n"})
    base = _sha(repo)
    # zeta: delivered pre-enable
    _run(repo, "checkout", "-q", "-b", "z", base)
    _commit(repo, "zeta work", T0 + 50, {"z.txt": "z\n"})
    _run(repo, "checkout", "-q", "main")
    _merge(repo, "z", "Merge pull request 'Zeta' (#1) from karta/zeta/integration "
                      "into main", T0 + 150)
    # alpha branch work starts pre-enable (timing race)
    _run(repo, "checkout", "-q", "-b", "a", base)
    _commit(repo, "alpha work", T0 + 100, {"a.txt": "a\n"})
    _run(repo, "checkout", "-q", "main")
    _commit(repo, "chore: enable gardner", T0 + 200,
            {".karta/doc-gardner.json":
             '{"enabled": true, "focus": "test focus"}\n'})
    _merge(repo, "a", "Merge pull request 'Alpha' (#2) from karta/alpha/integration "
                      "into main", T0 + 300)
    # beta: post-enable, covered
    _run(repo, "checkout", "-q", "-b", "b", "main")
    _commit(repo, "beta work", T0 + 400, {"b.txt": "b\n"})
    _run(repo, "checkout", "-q", "main")
    _merge(repo, "b", "Merge pull request 'Beta' (#3) from karta/beta/integration "
                      "into main", T0 + 500)
    _commit(repo, "docs: gardner beta", T0 + 600, {"README.md": "a b\n"})
    # gamma: post-enable, true whiff
    _run(repo, "checkout", "-q", "-b", "g", "main")
    _commit(repo, "gamma work", T0 + 700, {"g.txt": "g\n"})
    _run(repo, "checkout", "-q", "main")
    _merge(repo, "g", "Merge pull request 'Gamma' (#4) from karta/gamma/integration "
                      "into main", T0 + 800)
    # delta: fast-forward landing, archive-add fallback
    _commit(repo, "chore(karta): archive binder delta — delivered", T0 + 900,
            {".karta/binders/archive/delta.json": "{}\n"})
    # beta archived late: dedupe, merge wins
    _commit(repo, "chore(karta): archive binder beta — delivered", T0 + 950,
            {".karta/binders/archive/beta.json": "{}\n"})
    return repo


def _build_repo_b(root: Path, now: int) -> tuple[Path, dict[str, str]]:
    repo = root / "repo-b"
    _init(repo)
    _commit(repo, "init", T0 + 10, {"README.md": "b\n"})
    _commit(repo, "chore: enable kaizen", T0 + 100,
            {".karta/kaizen.json": '{"enabled": true}\n'})
    _commit(repo, "kaizen: seed .karta/sme/ with testpack", T0 + 200,
            {".karta/sme/testpack.md": PACK_V0})
    _commit(repo, "chore: disable kaizen", T0 + 250,
            {".karta/kaizen.json": '{"enabled": false}\n'})
    _commit(repo, "chore: re-enable kaizen", T0 + 260,
            {".karta/kaizen.json": '{"enabled": true}\n'})
    shas: dict[str, str] = {}
    _commit(repo, "kaizen: drop tp.1", T0 + 300,
            {".karta/sme/testpack.md":
             PACK_V0.replace("- [ ] tp.1 — Rule one text here for testing "
                             "purposes.\n", "")})
    shas["k1"] = _sha(repo)
    _commit(repo, "sme(testpack): human fix", T0 + 300 + 1020,
            {".karta/sme/testpack.md":
             PACK_V0.replace("- [ ] tp.1 — Rule one text here for testing "
                             "purposes.\n", "") + "<!-- human note -->\n"})
    shas["corr1"] = _sha(repo)
    _commit(repo, "kaizen: shrink tp.2", T0 + 2000,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md").replace(
                 "- [ ] tp.2 — Rule two text that is long enough to shrink "
                 "meaningfully later on.", "- [ ] tp.2 — Short.")})
    shas["k2"] = _sha(repo)
    _commit(repo, "sme(testpack): too late", T0 + 300 + 15 * 86400,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md") + "<!-- late -->\n"})
    shas["corr_late"] = _sha(repo)
    shas["before_k3"] = _sha(repo)
    _commit(repo, "kaizen: add tp.3", now - 2 * 86400,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md")
             + "- [ ] tp.3 — New rule for recency.\n"})
    shas["k3"] = _sha(repo)
    _commit(repo, "sme(testpack): counter tweak", now - 86400,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md") + "<!-- tweak -->\n"})
    shas["corr3"] = _sha(repo)
    _commit(repo, "kaizen: retire tp.3 properly", now - 43200,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md").replace(
                 "- [ ] tp.3 — New rule for recency.",
                 "- ~~tp.3~~ retired: superseded.")})
    shas["k4"] = _sha(repo)
    # kaizen commit inside a merged branch: reachable via the full stream, but
    # the merge that lands it must never count as a correction incident
    _run(repo, "checkout", "-q", "-b", "wk")
    _commit(repo, "kaizen: branch edit", now - 40000,
            {".karta/sme/testpack.md":
             _show(repo, "HEAD", ".karta/sme/testpack.md")
             + "<!-- branch kaizen -->\n"})
    shas["k5"] = _sha(repo)
    _run(repo, "checkout", "-q", "main")
    _merge(repo, "wk", "Merge branch 'wk' into main", now - 39000)
    shas["wk_merge"] = _sha(repo)
    return repo, shas


def _self_test() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool) -> None:
        checks.append((name, ok))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    karta_root = Path(__file__).resolve().parents[2]
    grammar = load_grammar(karta_root)
    now = int(time.time())
    with tempfile.TemporaryDirectory(prefix="companions-selftest-") as td:
        root = Path(td)
        repo_a = _build_repo_a(root)
        row = audit(repo_a, None, grammar, now=now)
        g = row["gardner"]
        slugs = {r["slug"]: r for r in g["per_slug"]}
        check("row is well-formed (all keys, head sha, caveat)",
              all(k in row for k in ROW_KEYS)
              and row["head"] == _sha(repo_a) and row["caveat"] == CAVEAT)
        check("pre-enable delivery excluded from the gardner denominator",
              [e["slug"] for e in g["excluded_pre_enable"]] == ["zeta"]
              and "zeta" not in slugs)
        check("gardner fraction is n/N with the per-slug list",
              g["fraction"] == "1/4" and g["denominator_N"] == 4
              and len(g["per_slug"]) == 4)
        check("covered delivery matched by exact 'docs: gardner <slug>' subject",
              slugs["beta"]["covered"] and slugs["beta"]["gardner_sha"] is not None)
        check("dedupe by slug: merge wins over the later archive-add",
              slugs["beta"]["via"] == "integration-merge"
              and slugs["beta"]["delivery_ct"] == T0 + 500)
        check("whiff with branch lineage older than enable is a timing-race",
              slugs["alpha"]["whiff_class"] == "timing-race"
              and slugs["alpha"]["lineage_oldest_ct"] == T0 + 100
              and slugs["alpha"]["enable_ct"] == T0 + 200)
        check("whiff with post-enable lineage is a true whiff",
              slugs["gamma"]["whiff_class"] == "true-whiff")
        check("archive-add fallback delivery counted and graded (true whiff)",
              slugs["delta"]["via"] == "archive-add"
              and slugs["delta"]["whiff_class"] == "true-whiff")
        stale = {s["config"]: s for s in row["config_staleness"]}
        gs = stale[".karta/doc-gardner.json"]
        check("config staleness counts deliveries since last touch, with focus "
              "beside the last 5 delivery slugs",
              gs["deliveries_since_n"] == 4 and gs["focus"] == "test focus"
              and gs["last_5_delivery_slugs"] == ["zeta", "alpha", "beta",
                                                  "gamma", "delta"])
        check("permanent card-contradiction finding carried in the row",
              any(f["finding_id"] == "card-contradiction-history-walk"
                  for f in row["findings"]))

        repo_b, shas = _build_repo_b(root, now)
        row_b = audit(repo_b, None, grammar, now=now)
        kz = row_b["kaizen"]
        tr = row_b["epochs"][".karta/kaizen.json"]["transitions"]
        check("enable epochs track disable/re-enable value transitions "
              "(3 transitions, active enable is the re-enable commit)",
              len(tr) == 3 and tr[-1]["to"] is True and tr[-1]["ct"] == T0 + 260)
        check("additive floor FAILs when an active id vanishes without a "
              "tombstone (imported validate_packs grammar)",
              any(v["id"] == "tp.1" and v["kaizen_sha"] == shas["k1"]
                  for v in kz["floor"]["fail"]))
        check("additive floor WARNs on >20% rule-text shrink of a surviving id",
              any(v["id"] == "tp.2" and v["kaizen_sha"] == shas["k2"]
                  for v in kz["floor"]["warn"]))
        check("a tombstoned retirement is not a floor violation",
              not any(v["id"] == "tp.3" for v in kz["floor"]["fail"]))
        pairs = {(i["kaizen_sha"], i["correcting_sha"]): i for i in kz["incidents"]}
        check("17-minute correction lands in the 0-14d window (starts at 0)",
              pairs.get((shas["k1"], shas["corr1"]), {}).get("delta_minutes") == 17)
        check("a correction 15 days later is outside the window",
              (shas["k1"], shas["corr_late"]) not in pairs
              and (shas["k2"], shas["corr_late"]) not in pairs)
        check("window-open kaizen commit excluded from the closed denominator "
              "while its correction incident still appears in the list",
              shas["k3"] in kz["window_open"]
              and shas["k3"] not in kz["closed_denominator"]
              and (shas["k3"], shas["corr3"]) in pairs)
        check("spawn utility counts substantive kaizen commits, seed excluded",
              kz["spawn_utility"]["substantive_kaizen_commits_n"] == 5
              and shas["k1"] in kz["spawn_utility"]["substantive_kaizen_commits"]
              and kz["spawn_utility"]["seed_excluded"] != [])
        check("a kaizen commit inside a merged branch is reached by the full-"
              "history walk, and its landing merge is never a correction",
              any(c["sha"] == shas["k5"] for c in kz["commits"])
              and all(i["correcting_sha"] != shas["wk_merge"]
                      for i in kz["incidents"]))
        row_head = audit(repo_b, shas["before_k3"], grammar, now=now)
        check("--head scopes every git read to that sha (later commits invisible)",
              row_head["head"] == shas["before_k3"]
              and all(c["sha"] != shas["k3"] for c in row_head["kaizen"]["commits"])
              and all(i["correcting_sha"] != shas["corr3"]
                      for i in row_head["kaizen"]["incidents"]))
        try:
            load_grammar(root / "nowhere")
            check("grammar import failure raises fail-closed", False)
        except GrammarImportError:
            check("grammar import failure raises fail-closed", True)

    passed = sum(1 for _, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, help="consumer repo to audit")
    ap.add_argument("--head", default=None,
                    help="scope every git read to this sha (default: HEAD)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if args.repo is None:
        ap.error("provide --repo <path> or --self-test")
    try:
        grammar = load_grammar(Path(__file__).resolve().parents[2])
        row = audit(args.repo.resolve(), args.head, grammar)
    except (GrammarImportError, AuditError) as e:
        print(json.dumps({"error": str(e)}, indent=1))
        return 3
    print(json.dumps(row, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
