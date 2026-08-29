#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Karta Watch derivation benchmark — re-prove the batching win on slow hardware.

Times two derivations of the same git facts against a repository with REAL commit
history, at 1 / 5 / 10 / 20 / 40 binders x 10 work items, best of five:

  reference — the pre-batch per-item walker (one `for-each-ref` per binder, one
              `rev-parse` per item, one `merge-base --is-ancestor` per done item)
  batched   — the shipped `gather_git_facts()` from
              skills/karta-status/scripts/karta_next.py

  python3 benchmarks/perf/derivation_bench.py [--repo <path>] [--out <file>]
  python3 benchmarks/perf/derivation_bench.py --self-test

Real history is the whole point. A synthesised single-commit repo has no graph to
walk, so `for-each-ref --merged` is free and the measurement understates the true
cost by roughly half — on karta's own 385-commit history the pre-batch derivation
costs 613 ms at 20 binders where a single-commit fixture reported 314 ms. So the
harness REFUSES a repository with one commit, layers its synthetic binder refs
onto real commits, and puts unmerged item refs on genuine side branches.

It never writes to the repository it is pointed at. It resolves the real git
directory with `git rev-parse --git-common-dir` (a linked worktree's `.git` is a
FILE pointing elsewhere, so copying that file would yield no refs and no objects),
copies that directory to a temporary location, and creates every synthetic ref in
the copy. Every git invocation against the copy runs with `core.hooksPath` pointed
at an empty directory — `.git/hooks/` is copied along with everything else and a
`reference-transaction` hook fires on ref creation, so an un-neutralised hook could
write straight back into the source. Every invocation also runs in a cleaned
environment: GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and GIT_COMMON_DIR inherited
from the calling shell would redirect resolution at a directory the harness never
intended to copy. It refuses to run against a repository with a non-empty
`objects/info/alternates`, because a copy that leaves the alternate object store
behind walks a partial graph and reports numbers lower than the truth — the exact
error this benchmark exists to prevent. The temporary copy is removed on exit,
including after a failure; the source was never written to, so there is nothing to
restore. Every ref writer additionally refuses to run unless GIT_DIR points inside a
copy the harness made, so that confinement is structural rather than a property of
where the call happens to sit.

Wall-clock is REPORTED, never gated: it depends on the machine and on history
depth, and the constrained-container invocation documented in
docs/specs/2026-08-15-watch-performance-baseline.md needs a container a consumer
machine does not have. The enforced invariant is the call-count one — git calls
stay constant as binder count grows — checked by `karta_next.py --self-test`. This
harness counts git subprocesses at the same boundary that check uses: the whole
derivation, default-branch resolution included, not just the calls inside one
helper. Flat means identical at every binder count, not equal to any particular
number, so the harness reports the per-request total rather than asserting it.

House self-test contract: --self-test prints [PASS]/[FAIL] lines and an N/N checks
passed summary, and exits non-zero on any failure.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_SCRIPT = REPO_ROOT / "skills" / "karta-status" / "scripts" / "karta_next.py"

BINDER_COUNTS = (1, 5, 10, 20, 40)
ITEMS_PER_BINDER = 10
RUNS = 5

# Inherited from the calling shell these redirect git's own resolution, which is a
# silent write path into a directory the harness never intended to touch.
GIT_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")

# Every temporary copy is made under a directory named with this prefix, which is
# what `_require_bench_copy` recognises before allowing any ref write.
COPY_PREFIX = "derivation-bench-"

# Fixed identity so `commit-tree` works on a machine with no git identity
# configured, and so repeated runs mint identical side commits.
BENCH_IDENTITY = {
    "GIT_AUTHOR_NAME": "karta-bench",
    "GIT_AUTHOR_EMAIL": "bench@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "karta-bench",
    "GIT_COMMITTER_EMAIL": "bench@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


class BenchError(Exception):
    """A refusal or a failure the harness names rather than measuring through."""


# ---------------------------------------------------------------------------
# git invocation
# ---------------------------------------------------------------------------

def _out(*args: str) -> str:
    """git stdout, empty on any failure. Mirrors karta_next's `_git` so the
    reference walker's call count matches the shape it is reproducing."""
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True).stdout
    except OSError:
        return ""


def _git(*args: str, env: dict | None = None, stdin: str | None = None) -> str:
    """git stdout, raising BenchError on failure. Used for setup and probes,
    never inside a timed or counted region."""
    proc = subprocess.run(["git", *args], capture_output=True, text=True,
                          env=env, input=stdin)
    if proc.returncode != 0:
        raise BenchError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def clean_env(base: dict | None = None) -> dict:
    """The caller's environment with git's redirection variables removed."""
    env = dict(os.environ if base is None else base)
    for var in GIT_ENV_VARS:
        env.pop(var, None)
    return env


def measure_env(gitdir: Path, hooks_dir: Path, base: dict | None = None) -> dict:
    """A cleaned environment aimed at the COPY, with hooks neutralised.

    `GIT_CONFIG_*` carries the same weight as `git -c`, so it disarms hooks for
    every git invocation in the region — including ones inside the shipped
    derivation, which the harness does not pass arguments to."""
    env = clean_env(base)
    env["GIT_DIR"] = str(gitdir)
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    env["GIT_CONFIG_VALUE_0"] = str(hooks_dir)
    env.update(BENCH_IDENTITY)
    return env


@contextlib.contextmanager
def measuring_env(gitdir: Path, hooks_dir: Path):
    """Swap the process environment for the duration, so plain `git` — the
    shipped derivation included — resolves at the copy and runs hookless."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(measure_env(gitdir, hooks_dir, base=saved))
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---------------------------------------------------------------------------
# safety: resolve, refuse, copy aside, always clean up
# ---------------------------------------------------------------------------

def resolve_git_dir(repo: Path) -> Path:
    """The real git directory for `repo`, via `--git-common-dir`.

    A linked worktree's `.git` is a file pointing elsewhere and its own git dir
    holds no objects, so only the COMMON dir is worth copying. Resolution runs in
    a cleaned environment."""
    env = clean_env()
    try:
        raw = _git("-C", str(repo), "rev-parse", "--git-common-dir", env=env).strip()
    except BenchError as exc:
        raise BenchError(f"not a git repository: {repo} ({exc})") from exc
    if not raw:
        raise BenchError(f"could not resolve a git directory for {repo}")
    found = Path(raw)
    if not found.is_absolute():
        found = (Path(repo) / found)
    found = found.resolve()
    if not found.is_dir():
        raise BenchError(f"resolved git directory is not a directory: {found}")
    return found


def refuse_alternates(gitdir: Path) -> None:
    """Refuse a repository borrowing objects from elsewhere."""
    alternates = gitdir / "objects" / "info" / "alternates"
    try:
        text = alternates.read_text()
    except OSError:
        return
    entries = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if entries:
        raise BenchError(
            f"refusing to measure {gitdir}: objects/info/alternates is non-empty "
            f"({len(entries)} entry/entries). A copy leaves the alternate object "
            "store behind, so the copy walks a partial object graph and reports "
            "numbers lower than the truth.")


def refuse_shallow_history(gitdir: Path) -> int:
    """Refuse a repository with one commit, and return the commit count."""
    env = clean_env()
    env["GIT_DIR"] = str(gitdir)
    count = int(_git("rev-list", "--count", "--all", env=env).strip() or "0")
    if count < 2:
        raise BenchError(
            f"refusing to report figures for {gitdir}: it has {count} commit(s). "
            "With one commit there is no graph to walk, so `for-each-ref --merged` "
            "is free and the measurement understates the true cost by roughly "
            "half. Point the harness at a repository with real history.")
    return count


@contextlib.contextmanager
def copied_repo(repo: Path):
    """Yield `(gitdir_copy, hooks_dir, commit_count)` for a throwaway copy of
    `repo`'s git directory, removed on exit including after a failure."""
    source = resolve_git_dir(repo)
    refuse_alternates(source)
    commits = refuse_shallow_history(source)
    tmp = Path(tempfile.mkdtemp(prefix=COPY_PREFIX))
    try:
        gitdir = tmp / "git"
        hooks = tmp / "hooks-empty"
        hooks.mkdir()
        shutil.copytree(source, gitdir, symlinks=True)
        # HEAD may symref a branch the synthetic ref wipe deletes; detach it.
        with contextlib.suppress(BenchError):
            env = measure_env(gitdir, hooks)
            head = _git("rev-parse", "HEAD", env=env).strip()
            _git("update-ref", "--no-deref", "HEAD", head, env=env)
        yield gitdir, hooks, commits
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# the two derivations
# ---------------------------------------------------------------------------

def _default_branch() -> str:
    """Duplicated from karta_next so the reference path stands alone, and so both
    timed paths resolve the default branch identically — the call count then
    covers the whole request, not one helper."""
    head = _out("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").strip()
    if head:
        return head.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _out("rev-parse", "--verify", "--quiet", cand).strip():
            return cand
    return "main"


def reference_facts(binders: list[dict], default_branch: str) -> dict:
    """The pre-batch per-item walker, deliberately duplicated here rather than
    imported: the harness has to run standalone against any checkout, including
    one predating the batching, where this code no longer exists."""
    facts = {"default_branch": default_branch, "binders": {}}
    for b in binders:
        slug = b["slug"]
        item_ids = [it["id"] for it in b.get("work_items", [])]
        refs = set(_out("for-each-ref", "--format=%(refname)",
                        f"refs/karta/{slug}/").splitlines())
        integration = bool(_out("rev-parse", "--verify", "--quiet",
                                f"karta/{slug}/integration").strip())
        items = {}
        for i in item_ids:
            base = f"refs/karta/{slug}/item-{i}"
            done = f"{base}/done" in refs
            done_in_default = done and subprocess.run(
                ["git", "merge-base", "--is-ancestor", f"{base}/done", default_branch]
            ).returncode == 0
            items[i] = {
                "done": done,
                "done_in_default": done_in_default,
                "built": f"{base}/built" in refs,
                "failed": f"{base}/failed" in refs,
                "branch": bool(_out("rev-parse", "--verify", "--quiet",
                                    f"karta/{slug}/item-{i}").strip()),
            }
        facts["binders"][slug] = {"integration_exists": integration, "items": items}
    return facts


def bundled_batched_facts(binders: list[dict], default_branch: str) -> dict:
    """The batched derivation, bundled so a checkout predating batching can still
    produce the after-half of the table. `load_batched` prefers the shipped one."""
    def query(*args: str) -> tuple[set[str], bool]:
        proc = subprocess.run(["git", "for-each-ref", *args],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return set(), False
        return {ln for ln in proc.stdout.splitlines() if ln}, True

    markers, markers_ok = query("--format=%(refname)", "refs/karta/")
    branches, branches_ok = query("--format=%(refname)", "refs/heads/karta/")
    merged, merged_ok = query("--format=%(refname)",
                              f"--merged={default_branch}", "refs/karta/")
    marker_set = markers if markers_ok else None
    branch_set = branches if branches_ok else None
    merged_set = merged if merged_ok else None

    facts = {"default_branch": default_branch, "binders": {}}
    for b in binders:
        slug = b["slug"]
        integration = (None if branch_set is None else
                       f"refs/heads/karta/{slug}/integration" in branch_set)
        items = {}
        for it in b.get("work_items", []):
            i = it["id"]
            base = f"refs/karta/{slug}/item-{i}"
            done = None if marker_set is None else f"{base}/done" in marker_set
            if done is None:
                done_in_default = None
            elif not done:
                done_in_default = False
            elif merged_set is None:
                done_in_default = None
            else:
                done_in_default = f"{base}/done" in merged_set
            items[i] = {
                "done": done,
                "done_in_default": done_in_default,
                "built": None if marker_set is None else f"{base}/built" in marker_set,
                "failed": None if marker_set is None else f"{base}/failed" in marker_set,
                "branch": (None if branch_set is None else
                           f"refs/heads/karta/{slug}/item-{i}" in branch_set),
            }
        facts["binders"][slug] = {"integration_exists": integration, "items": items}
    return facts


def load_batched(script: Path = SHIPPED_SCRIPT):
    """Return `(callable, source)` for the batched path: the shipped
    `gather_git_facts` when the checkout has it, else the bundled equivalent."""
    try:
        spec = importlib.util.spec_from_file_location("karta_next_bench", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        shipped = getattr(module, "gather_git_facts", None)
    except Exception:
        shipped = None
    if shipped is None:
        return bundled_batched_facts, "bundled (checkout has no gather_git_facts)"
    return shipped, f"shipped ({script})"


# ---------------------------------------------------------------------------
# synthetic ref topology, laid on real commits
# ---------------------------------------------------------------------------

def _require_bench_copy() -> None:
    """Refuse to create or delete a ref unless GIT_DIR points inside a temporary
    copy this harness made. Every writer below calls this, so the confinement is
    structural rather than a property of where the call happens to sit."""
    gitdir = os.environ.get("GIT_DIR", "")
    if not gitdir or not Path(gitdir).parent.name.startswith(COPY_PREFIX):
        raise BenchError(
            "refusing to write refs: GIT_DIR is not a temporary copy made by this "
            f"harness (GIT_DIR={gitdir or 'unset'}). Ref creation and deletion only "
            "ever run against a copy, never against a repository it was pointed at.")


def wipe_karta_refs() -> None:
    """Clear the copy's karta namespace so a scale measures only its own refs."""
    _require_bench_copy()
    refs = _out("for-each-ref", "--format=%(refname)",
                "refs/karta/", "refs/heads/karta/").split()
    if refs:
        _git("update-ref", "--stdin",
             stdin="".join(f"delete {r}\n" for r in refs))


def commit_pool(default_branch: str, size: int = 8) -> tuple[list[str], list[str]]:
    """`(merged, unmerged)` real commits.

    `merged` are commits on the default branch, spread through history. `unmerged`
    are new commits laid on genuine side branches off those historical commits, so
    they are real commits that `--merged` has to walk the graph to rule out."""
    _require_bench_copy()
    history = _out("rev-list", default_branch).split()
    if len(history) < 2:
        raise BenchError(f"{default_branch} has fewer than two commits")
    step = max(1, len(history) // size)
    merged = history[::step][:size] or history[:1]
    unmerged, updates = [], []
    for idx, base in enumerate(merged):
        tree = _git("rev-parse", f"{base}^{{tree}}").strip()
        sha = _git("commit-tree", tree, "-p", base, "-m",
                   f"karta-bench side branch {idx}").strip()
        unmerged.append(sha)
        updates.append(f"update refs/heads/bench-side/{idx} {sha}")
    _git("update-ref", "--stdin", stdin="\n".join(updates) + "\n")
    return merged, unmerged


def build_refs(n_binders: int, n_items: int,
               pool: tuple[list[str], list[str]]) -> list[dict]:
    """Create the ref shapes a real run leaves and return the matching binders:
    done and built markers, per-item branches, an integration branch per binder,
    and a mix of merged and unmerged done refs."""
    _require_bench_copy()
    merged, unmerged = pool
    wipe_karta_refs()
    updates, binders = [], []
    for bi in range(n_binders):
        slug = f"bench{bi}"
        item_ids = [f"i{ii}" for ii in range(n_items)]
        for ii, iid in enumerate(item_ids):
            base = f"refs/karta/{slug}/item-{iid}"
            pat = (bi + ii) % 4
            if pat == 0:
                updates.append(f"update {base}/done {merged[(bi + ii) % len(merged)]}")
            elif pat == 1:
                updates.append(f"update {base}/done {unmerged[(bi + ii) % len(unmerged)]}")
            elif pat == 2:
                updates.append(f"update {base}/built {merged[(bi + ii) % len(merged)]}")
            else:
                updates.append(
                    f"update refs/heads/karta/{slug}/item-{iid} {merged[0]}")
        updates.append(f"update refs/heads/karta/{slug}/integration {merged[0]}")
        binders.append({"slug": slug,
                        "work_items": [{"id": i} for i in item_ids]})
    if updates:
        _git("update-ref", "--stdin", stdin="\n".join(updates) + "\n")
    return binders


# ---------------------------------------------------------------------------
# timing and counting
# ---------------------------------------------------------------------------

def summarize(samples: list[float]) -> dict:
    """Best of N, plus the spread. Best-of-five alone lets a noisy machine look
    clean, so the spread travels with it."""
    ordered = sorted(round(s, 3) for s in samples)
    return {
        "ms_best": ordered[0],
        "ms_worst": ordered[-1],
        "ms_spread": round(ordered[-1] - ordered[0], 3),
        "ms_samples": [round(s, 3) for s in samples],
    }


def time_request(request, runs: int = RUNS) -> dict:
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        request()
        samples.append((time.perf_counter() - start) * 1000.0)
    return summarize(samples)


def count_git_calls(request) -> int:
    """Git subprocesses one whole request issues, counted at the subprocess
    boundary — default-branch resolution included, not just the calls inside one
    helper. This is the boundary karta_next's invariant check counts at."""
    original = subprocess.run
    seen = [0]

    def counting(*args, **kwargs):
        seen[0] += 1
        return original(*args, **kwargs)

    subprocess.run = counting
    try:
        request()
    finally:
        subprocess.run = original
    return seen[0]


def calls_stay_constant(counts: list[int]) -> bool:
    """git calls stay constant as binder count grows — identical at every binder
    count, not equal to any particular number. The per-request total includes
    default-branch resolution, so the figure itself is whatever the batched
    implementation settles on; the harness reports it rather than asserting it."""
    return len(counts) > 1 and len(set(counts)) == 1


def calls_grow(counts: list[int]) -> bool:
    """Strictly increasing — what the per-item walker does by construction."""
    return len(counts) > 1 and all(a < b for a, b in zip(counts, counts[1:]))


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------

def measure(repo: Path, binder_counts=BINDER_COUNTS, items: int = ITEMS_PER_BINDER,
            runs: int = RUNS) -> dict:
    batched, batched_source = load_batched()
    with copied_repo(repo) as (gitdir, hooks, commits):
        with measuring_env(gitdir, hooks):
            default_branch = _default_branch()
            pool = commit_pool(default_branch)
            records = []
            for n in binder_counts:
                binders = build_refs(n, items, pool)

                def reference_request(_b=binders):
                    return reference_facts(_b, _default_branch())

                def batched_request(_b=binders):
                    return batched(_b, _default_branch())

                record = {
                    "binders": n,
                    "items": n * items,
                    "reference": time_request(reference_request, runs),
                    "batched": time_request(batched_request, runs),
                }
                record["reference"]["git_calls"] = count_git_calls(reference_request)
                record["batched"]["git_calls"] = count_git_calls(batched_request)
                record["speedup"] = round(
                    record["reference"]["ms_best"] / record["batched"]["ms_best"], 1)
                records.append(record)

    ref_counts = [r["reference"]["git_calls"] for r in records]
    bat_counts = [r["batched"]["git_calls"] for r in records]
    return {
        "schema": "karta/derivation-bench/1",
        "repo": str(Path(repo).resolve()),
        "commits": commits,
        "items_per_binder": items,
        "runs": runs,
        "batched_source": batched_source,
        "records": records,
        "reference_git_calls_grow": calls_grow(ref_counts),
        "batched_git_calls_stay_constant": calls_stay_constant(bat_counts),
        "batched_git_calls_per_request": bat_counts[0] if bat_counts else None,
    }


def render_table(report: dict) -> str:
    lines = [
        f"repo: {report['repo']} ({report['commits']} commits), "
        f"{report['items_per_binder']} items per binder, best of {report['runs']}",
        f"batched path: {report['batched_source']}",
        "",
        "| binders | items | reference ms | reference calls | batched ms | "
        "batched calls | speedup |",
        "|-|-|-|-|-|-|-|",
    ]
    for r in report["records"]:
        lines.append(
            f"| {r['binders']} | {r['items']} | {r['reference']['ms_best']} | "
            f"{r['reference']['git_calls']} | {r['batched']['ms_best']} | "
            f"{r['batched']['git_calls']} | {r['speedup']}x |")
    lines.append("")
    lines.append("git calls stay constant as binder count grows: "
                 f"{report['batched_git_calls_stay_constant']} "
                 f"({report['batched_git_calls_per_request']} per request, "
                 "default-branch resolution included)")
    lines.append("reference git calls grow with binder count: "
                 f"{report['reference_git_calls_grow']}")
    lines.append("spread across runs is in the JSON block below, not this table.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _setup(args: list[str], cwd: Path, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True, env=clean_env(), **kw)


def _mk_history(path: Path, commits: int = 4) -> Path:
    """A small repository with real history: a default branch plus a side branch,
    so `--merged` has something to rule out."""
    path.mkdir(parents=True, exist_ok=True)
    _setup(["init", "-q", "-b", "main", "."], path)
    _setup(["config", "user.email", "t@example.invalid"], path)
    _setup(["config", "user.name", "t"], path)
    for n in range(commits):
        (path / f"f{n}").write_text(f"c{n}")
        _setup(["add", "-A"], path)
        _setup(["commit", "-q", "-m", f"c{n}"], path)
    return path


def _ref_listing(repo: Path) -> str:
    env = clean_env()
    env["GIT_DIR"] = str(resolve_git_dir(repo))
    return _git("for-each-ref", "--format=%(refname) %(objectname)", env=env)


def _self_test_checks() -> list[tuple[str, bool]]:
    checks: list[tuple[str, bool]] = []
    small = {"binder_counts": (1, 2, 3), "items": 2, "runs": 2}

    with tempfile.TemporaryDirectory() as sd:
        root = Path(sd)

        # 1. single-commit refusal, naming the reason
        single = _mk_history(root / "single", commits=1)
        try:
            measure(single, **small)
            single_msg = ""
        except BenchError as exc:
            single_msg = str(exc)
        checks.append((
            "refuses a one-commit repository, naming the reason",
            "1 commit" in single_msg and "understates" in single_msg))

        repo = _mk_history(root / "repo")

        # 2. real history, and a done ref genuinely not an ancestor of default
        with copied_repo(repo) as (gitdir, hooks, commits):
            with measuring_env(gitdir, hooks):
                db = _default_branch()
                pool = commit_pool(db, size=3)
                binders = build_refs(2, 4, pool)
                facts = bundled_batched_facts(binders, db)
                every = [it for b in facts["binders"].values()
                         for it in b["items"].values()]
                done_refs = [it for it in every if it["done"]]
                unmerged_done = [it for it in done_refs if it["done_in_default"] is False]
                merged_done = [it for it in done_refs if it["done_in_default"] is True]
                shapes_built = any(it["built"] for it in every)
                shapes_branch = any(it["branch"] for it in every)
                shapes_integration = all(b["integration_exists"]
                                         for b in facts["binders"].values())
                ref_equal = reference_facts(binders, db) == facts
                shipped, source = load_batched()
                shipped_equal = shipped(binders, db) == reference_facts(binders, db)
                hooks_env_ok = (Path(os.environ["GIT_CONFIG_VALUE_0"]) == hooks
                                and os.environ["GIT_CONFIG_KEY_0"] == "core.hooksPath"
                                and hooks.is_dir() and not any(hooks.iterdir()))
        checks.append((
            "the measured repository has more than one commit, and at least one "
            "done ref it creates is genuinely not an ancestor of the default branch",
            commits > 1 and len(merged_done) > 0 and len(unmerged_done) > 0))
        checks.append((
            "the synthetic ref builder produces the declared shapes: done and built "
            "markers, per-item branches, an integration branch, and a mix of merged "
            "and unmerged done refs",
            len(done_refs) > 0 and shapes_built and shapes_branch
            and shapes_integration and len(merged_done) > 0 and len(unmerged_done) > 0))
        checks.append((
            "the reference walker and the shipped batched derivation return "
            "identical facts, so the two timed paths measure the same answer",
            source.startswith("shipped") and shipped_equal and ref_equal))
        checks.append((
            "git invocations against the copy point core.hooksPath at an empty "
            "directory",
            hooks_env_ok))

        # 3. a linked worktree, whose .git is a FILE
        wt = root / "linked"
        _setup(["worktree", "add", "-q", "-b", "linked", str(wt)], repo)
        checks.append((
            "resolves the git directory through --git-common-dir, so a linked "
            "worktree whose .git is a file still measures a real repository",
            (wt / ".git").is_file()
            and resolve_git_dir(wt) == resolve_git_dir(repo)
            and resolve_git_dir(wt).is_dir()
            and bool(measure(wt, **small)["records"])))

        # 4. a reference-transaction hook that would write into the source
        marker = root / "hook-fired"
        hook = repo / ".git" / "hooks" / "reference-transaction"
        hook.write_text(f'#!/bin/sh\necho fired >> "{marker}"\n')
        hook.chmod(0o755)
        before_hook = _ref_listing(repo)
        measure(repo, **small)
        checks.append((
            "a repository carrying a reference-transaction hook that would write to "
            "the source is left untouched — the hook never fires against the copy",
            not marker.exists() and _ref_listing(repo) == before_hook))
        hook.unlink()

        # 5. non-empty objects/info/alternates
        alt = _mk_history(root / "alt")
        (alt / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (alt / ".git" / "objects" / "info" / "alternates").write_text(
            str(repo / ".git" / "objects") + "\n")
        try:
            measure(alt, **small)
            alt_msg = ""
        except BenchError as exc:
            alt_msg = str(exc)
        checks.append((
            "refuses a repository with a non-empty objects/info/alternates, naming "
            "the reason, rather than reporting from a partial object graph",
            "alternates" in alt_msg and "partial object graph" in alt_msg))

        # 6. the source's ref namespace is byte-identical after a run
        before = _ref_listing(repo)
        report = measure(repo, **small)
        checks.append((
            "pointing the harness at a repository leaves that repository's ref "
            "namespace byte-identical afterwards",
            _ref_listing(repo) == before))

        # 7. an interrupted run leaves the refs alone and removes the copy
        interrupted_copy: list[Path] = []
        try:
            with copied_repo(repo) as (gitdir, hooks, _c):
                interrupted_copy.append(gitdir)
                with measuring_env(gitdir, hooks):
                    build_refs(1, 2, commit_pool(_default_branch(), size=2))
                raise KeyboardInterrupt("interrupted partway through")
        except KeyboardInterrupt:
            pass
        checks.append((
            "a run interrupted partway through still leaves the source repository's "
            "ref listing unchanged, and removes its temporary copy",
            _ref_listing(repo) == before
            and bool(interrupted_copy) and not interrupted_copy[0].exists()))
        checks.append((
            "the temporary copy, with every synthetic ref in it, is removed when the "
            "run finishes",
            not interrupted_copy[0].parent.exists()))

        # 8. call counts: growing for the walker, flat for the batched path
        ref_counts = [r["reference"]["git_calls"] for r in report["records"]]
        bat_counts = [r["batched"]["git_calls"] for r in report["records"]]
        checks.append((
            "the call counter reports a GROWING count for the reference walker and "
            "a FLAT count for the batched path — git calls stay constant as binder "
            "count grows, flat meaning identical at every binder count rather than "
            "equal to any particular number",
            calls_grow(ref_counts) and calls_stay_constant(bat_counts)
            and report["reference_git_calls_grow"]
            and report["batched_git_calls_stay_constant"]))

        # 9. the timing loop reports the best of five, not the first or the mean
        summary = summarize([5.0, 1.0, 3.0, 9.0, 2.0])
        checks.append((
            "the timing loop reports the best of five runs, not the first or the mean",
            RUNS == 5 and summary["ms_best"] == 1.0
            and len(summary["ms_samples"]) == 5))

        # 10. the JSON block's shape
        record = report["records"][0]
        checks.append((
            "the JSON block carries one record per binder count with the binder "
            "count, item count, milliseconds and git call count for both paths",
            len(report["records"]) == 3
            and all(set(("binders", "items")) <= set(r) for r in report["records"])
            and all(set(("ms_best", "git_calls")) <= set(r[p])
                    for r in report["records"] for p in ("reference", "batched"))
            and json.loads(json.dumps(report))["schema"] == report["schema"]))
        checks.append((
            "the JSON block reports the spread across runs, not only the best time",
            summary["ms_spread"] == 8.0
            and all(r[p]["ms_spread"] == round(max(r[p]["ms_samples"])
                                               - min(r[p]["ms_samples"]), 3)
                    for r in report["records"] for p in ("reference", "batched"))
            and "ms_spread" in record["reference"]))

        # 11. ref writes are confined structurally, not by where they are called
        saved_gitdir = os.environ.pop("GIT_DIR", None)
        refused = []
        try:
            for writer in (wipe_karta_refs,
                           lambda: build_refs(1, 1, (["x"], ["y"])),
                           lambda: commit_pool("main", size=1)):
                try:
                    writer()
                except BenchError as exc:
                    refused.append(str(exc))
        finally:
            if saved_gitdir is not None:
                os.environ["GIT_DIR"] = saved_gitdir
        checks.append((
            "every ref writer refuses to run unless GIT_DIR points inside a temporary "
            "copy this harness made, so the confinement is structural rather than a "
            "property of the call site",
            len(refused) == 3
            and all("refusing to write refs" in m for m in refused)
            and _ref_listing(repo) == before))

        # 12. the self-test's own exit contract
        checks.append((
            "the self-test emits [PASS]/[FAIL] lines and exits non-zero on any "
            "failure",
            _exit_code([("ok", True)]) == 0
            and _exit_code([("ok", True), ("bad", False)]) == 1))

    return checks


def _exit_code(checks: list[tuple[str, bool]]) -> int:
    return 1 if any(not ok for _, ok in checks) else 0


def _run_self_test() -> int:
    checks = _self_test_checks()
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return _exit_code(checks)


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Karta Watch derivation benchmark against real git history.")
    ap.add_argument("--repo", type=Path, default=REPO_ROOT,
                    help="repository to measure against (never written to; "
                         "default: this checkout)")
    ap.add_argument("--binders", type=str,
                    default=",".join(str(n) for n in BINDER_COUNTS),
                    help="comma-separated binder counts")
    ap.add_argument("--items", type=int, default=ITEMS_PER_BINDER,
                    help="work items per binder")
    ap.add_argument("--runs", type=int, default=RUNS,
                    help="timed runs per measurement; the best is reported")
    ap.add_argument("--out", type=Path, default=None,
                    help="also write the JSON block here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()

    try:
        counts = tuple(int(part) for part in args.binders.split(",") if part.strip())
        report = measure(args.repo, binder_counts=counts, items=args.items,
                         runs=args.runs)
    except BenchError as exc:
        print(f"derivation_bench: {exc}", file=sys.stderr)
        return 2

    print(render_table(report))
    print()
    blob = json.dumps(report, indent=2)
    print(blob)
    if args.out:
        args.out.write_text(blob + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
