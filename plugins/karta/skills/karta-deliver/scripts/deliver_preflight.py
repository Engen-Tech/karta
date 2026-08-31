# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta deliver preflight: one JSON packet answering everything Phase 0/1 and the
wave loop's Steps 1-2 currently discover by hand.

Zero dependencies (pure stdlib), so every invocation form behaves identically:
  python3 deliver_preflight.py --binder <path> [--repo <dir>]   # print the packet, exit 0/1
  python3 deliver_preflight.py --self-test                       # embedded fixtures, exit 0/1
  uv run --script deliver_preflight.py --binder <path>            # also fine — no deps to install

The packet is a SNAPSHOT of git + the binder at the moment it runs, not a cache — the
deliver doctrine re-runs this after a Clear, after a Resume decision, and after any
other change to the integration branch or the ref namespace before trusting the
result again.

Top-level keys:
  validator          — {exit_status, output} from running validate_binder.py with the
                        CURRENT interpreter (the validator is zero-dependency stdlib, so
                        python3 and `uv run --script` behave identically). A nonzero exit
                        ALSO sets `halt` — an invalid binder is a stop, never a frontier
                        to build from.
  slug                — the binder's slug.
  default_branch      — detected via `git remote show origin`'s HEAD-branch line, else
                        whichever of main/master exists.
  integration_branch   — {name, exists, tip}.
  refs                — every refs/karta/<slug>/item-*/{built,failed,done,accepted,evidence}
                        that exists, mapped to its target sha. The canonical evidence
                        namespace is refs/karta/<slug>/item-<id>/evidence.
  wave_tags            — every karta/<slug>/wave-* tag, mapped to its target sha.
  done_provenance      — for every item carrying a done ref: the result of
                        `check_item_provenance.py --check-accepted --slug <slug>
                        --item <id> --range <done>^1..<done>` (the merge commit plus its
                        merged side — never <base>..<done>, which for an item merged
                        after a wave-mate would contain the wave-mate's commits too),
                        PLUS a --first-parent reachability check: the done target must
                        appear in `git rev-list --first-parent karta/<slug>/integration`.
                        Either check failing is reported per item and sets `halt` — a
                        forged accepted/done pair cannot hide behind a clean frontier.
  halt                 — true when the binder failed validation or any done_provenance
                        check failed. The deliver doctrine treats a packet with `halt`
                        set as a stop, never as a frontier to build from.
  frontier             — ready item ids: not done, and every depends_on id already
                        carries a done ref.
  parallelism          — {parallel, serialize, reasons, unresolved}, a partition of the
                        frontier from the four deterministic gates a script can decide
                        (a dependency edge, an explicit `serialize`, overlapping
                        `touches`, a co-declared `shared_resources`). The two gates a
                        script cannot decide are handled conservatively, never dropped:
                        an oracle needing a stateful, non-isolatable env goes to
                        serialize with reason undecidable:stateful-env; an item with no
                        touches goes to serialize with reason undecidable:missing-touches.
                        Both also land in `unresolved` for the orchestrator's judgment.
  tools                — absolute resolved paths of run_oracle.py, scan_secrets.py,
                        check_item_provenance.py, and item_context.py, resolved from this
                        script's own location so a worktree, a plugin install, and a repo
                        checkout all work.

Stdlib only. Invoked directly (not installed), matching the non-executable mode of
sibling scripts.

Exit codes: 0 = packet printed (regardless of `halt` — halt is a field, not an exit
status), 1 = self-test failure, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import shutil
from itertools import combinations
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VALIDATE_BINDER = (SCRIPT_DIR / ".." / ".." / "karta-plan" / "scripts" / "validate_binder.py").resolve()
RUN_ORACLE = (SCRIPT_DIR / ".." / ".." / "karta-build" / "scripts" / "run_oracle.py").resolve()
SCAN_SECRETS = (SCRIPT_DIR / ".." / ".." / "karta-build" / "scripts" / "scan_secrets.py").resolve()
CHECK_PROVENANCE = (SCRIPT_DIR / "check_item_provenance.py").resolve()
ITEM_CONTEXT = (SCRIPT_DIR / ".." / ".." / "karta-build" / "scripts" / "item_context.py").resolve()

ENV_ORACLE_TYPES = {"integration", "e2e", "visual"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(repo), *args])


def detect_default_branch(repo: Path) -> str | None:
    proc = _git(repo, "remote", "show", "origin")
    if proc.returncode == 0:
        m = re.search(r"HEAD branch:\s*(\S+)", proc.stdout)
        if m and m.group(1) != "(unknown)":
            return m.group(1)
    for cand in ("main", "master"):
        if _git(repo, "rev-parse", "--verify", "--quiet", cand).returncode == 0:
            return cand
    return None


def ref_target(repo: Path, ref: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    out = proc.stdout.strip()
    return out or None


def list_prefixed_refs(repo: Path, prefix: str) -> dict[str, str]:
    proc = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix)
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name, sha = line.rsplit(" ", 1)
        out[name] = sha
    return out


def list_wave_tags(repo: Path, slug: str) -> dict[str, str]:
    return {
        name[len("refs/tags/"):]: sha
        for name, sha in list_prefixed_refs(repo, f"refs/tags/karta/{slug}/wave-*").items()
    }


def first_parent_chain(repo: Path, branch: str) -> set[str]:
    proc = _git(repo, "rev-list", "--first-parent", branch)
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def compute_done_provenance(repo: Path, slug: str, refs: dict[str, str],
                             integration_name: str) -> tuple[dict, bool]:
    """Rule: every done ref must pass check_item_provenance --check-accepted over its
    own merge range, AND be --first-parent reachable on the integration branch. Either
    failing is a per-item finding and sets halt True."""
    chain = first_parent_chain(repo, integration_name) if ref_target(repo, integration_name) else set()
    done_pat = re.compile(rf"^refs/karta/{re.escape(slug)}/item-(.+)/done$")
    result: dict[str, dict] = {}
    halt = False
    for refname, sha in refs.items():
        m = done_pat.match(refname)
        if not m:
            continue
        item_id = m.group(1)
        proc = _run([sys.executable, str(CHECK_PROVENANCE), "--repo", str(repo),
                     "--item", item_id, "--range", f"{sha}^1..{sha}",
                     "--slug", slug, "--check-accepted"])
        checker_ok = proc.returncode == 0
        reachable = sha in chain
        ok = checker_ok and reachable
        result[item_id] = {
            "target": sha,
            "checker_exit": proc.returncode,
            "checker_output": (proc.stdout + proc.stderr).strip(),
            "first_parent_reachable": reachable,
            "ok": ok,
        }
        if not ok:
            halt = True
    return result, halt


def compute_parallelism(items_by_id: dict, frontier: list[str], env_contract: dict | None) -> dict:
    """Partition the frontier per skills/_shared/parallelism-gates.md. Four gates a
    script can decide (dependency edge, explicit serialize, touches overlap,
    shared_resources overlap) never infer from prose. Two gates a script cannot decide
    are handled conservatively, never dropped, and also land in `unresolved`."""
    frontier_set = set(frontier)
    serialize: list[str] = []
    reasons: dict[str, list[str]] = {}
    unresolved: list[str] = []

    def mark(item_id: str, reason: str, *, is_undecidable: bool = False) -> None:
        reasons.setdefault(item_id, [])
        if reason not in reasons[item_id]:
            reasons[item_id].append(reason)
        if item_id not in serialize:
            serialize.append(item_id)
        if is_undecidable and item_id not in unresolved:
            unresolved.append(item_id)

    supports_isolation = bool((env_contract or {}).get("supports_isolation", False))

    for item_id in frontier:
        item = items_by_id[item_id]
        if item.get("serialize"):
            mark(item_id, "explicit-serialize")
        if set(item.get("depends_on") or []) & frontier_set:
            mark(item_id, "dependency-edge")
        if not item.get("touches"):
            mark(item_id, "undecidable:missing-touches", is_undecidable=True)
        oracle_type = (item.get("oracle") or {}).get("type")
        if oracle_type in ENV_ORACLE_TYPES and not supports_isolation:
            mark(item_id, "undecidable:stateful-env", is_undecidable=True)

    for a, b in combinations(frontier, 2):
        ia, ib = items_by_id[a], items_by_id[b]
        if set(ia.get("touches") or []) & set(ib.get("touches") or []):
            mark(a, "touches-overlap")
            mark(b, "touches-overlap")
        if set(ia.get("shared_resources") or []) & set(ib.get("shared_resources") or []):
            mark(a, "shared-resources")
            mark(b, "shared-resources")

    parallel = [i for i in frontier if i not in serialize]
    return {"parallel": parallel, "serialize": serialize, "reasons": reasons, "unresolved": unresolved}


def build_packet(binder_path: Path, repo: Path) -> dict:
    validator_proc = _run([sys.executable, str(VALIDATE_BINDER), "--binder", str(binder_path)])
    validator = {"exit_status": validator_proc.returncode,
                 "output": (validator_proc.stdout + validator_proc.stderr).strip()}
    halt = validator_proc.returncode != 0

    binder: dict | None = None
    try:
        binder = json.loads(binder_path.read_text())
    except (OSError, json.JSONDecodeError):
        binder = None

    slug = binder.get("slug") if isinstance(binder, dict) else None
    default_branch = detect_default_branch(repo)
    integration_name = f"karta/{slug}/integration" if slug else None
    integration_tip = ref_target(repo, integration_name) if integration_name else None
    integration_branch = {"name": integration_name, "exists": integration_tip is not None,
                          "tip": integration_tip}

    refs: dict[str, str] = {}
    wave_tags: dict[str, str] = {}
    done_provenance: dict = {}
    frontier: list[str] = []
    parallelism = {"parallel": [], "serialize": [], "reasons": {}, "unresolved": []}

    if slug:
        refs = list_prefixed_refs(repo, f"refs/karta/{slug}/")
        wave_tags = list_wave_tags(repo, slug)
        done_provenance, dp_halt = compute_done_provenance(repo, slug, refs, integration_name)
        halt = halt or dp_halt

        if isinstance(binder, dict):
            items = binder.get("work_items", [])
            items_by_id = {it["id"]: it for it in items}
            done_ids = set(done_provenance)
            frontier = [it["id"] for it in items
                        if it["id"] not in done_ids
                        and all(d in done_ids for d in (it.get("depends_on") or []))]
            parallelism = compute_parallelism(items_by_id, frontier, binder.get("env_contract"))

    if validator_proc.returncode != 0:
        frontier = []

    tools = {
        "run_oracle.py": str(RUN_ORACLE),
        "scan_secrets.py": str(SCAN_SECRETS),
        "check_item_provenance.py": str(CHECK_PROVENANCE),
        "item_context.py": str(ITEM_CONTEXT),
    }

    return {
        "validator": validator,
        "slug": slug,
        "default_branch": default_branch,
        "integration_branch": integration_branch,
        "refs": refs,
        "wave_tags": wave_tags,
        "done_provenance": done_provenance,
        "halt": bool(halt),
        "frontier": frontier,
        "parallelism": parallelism,
        "tools": tools,
    }


# --- Self-test -------------------------------------------------------------------

def _run_self_test() -> int:
    passed = 0
    total = 0
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total, failures
        total += 1
        if ok:
            passed += 1
        else:
            failures += 1
        suffix = f": {detail}" if detail else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")

    def init(root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "t@example.invalid")
        _git(root, "config", "user.name", "karta self-test")
        return root

    def commit(root: Path, message: str) -> str:
        _git(root, "commit", "-q", "--allow-empty", "-m", message)
        return ref_target(root, "HEAD") or ""

    def write_binder(path: Path, doc: dict) -> None:
        path.write_text(json.dumps(doc))

    def minimal_item(item_id: str, **extra) -> dict:
        base = {"id": item_id, "title": item_id, "summary": "s",
                "oracle": {"opt_out": True, "reason": "fixture"}}
        base.update(extra)
        return base

    def minimal_binder(slug: str, items: list[dict], env_contract: dict | None = None) -> dict:
        doc = {"slug": slug, "title": "t", "summary": "s", "motivation": "m",
               "scope": {"included": ["x"]}, "work_items": items}
        if env_contract is not None:
            doc["env_contract"] = env_contract
        return doc

    tmp = Path(tempfile.mkdtemp(prefix="deliver_preflight_selftest_"))
    try:
        # --- invalid-binder: a dangling depends_on fails validate_binder.py ---------
        r = init(tmp / "invalid-binder")
        commit(r, "base")
        binder_path = tmp / "invalid-binder.json"
        write_binder(binder_path, minimal_binder(
            "fixture-invalid-binder",
            [minimal_item("a", depends_on=["ghost"])]))
        packet = build_packet(binder_path, r)
        check("[invalid-binder] a dangling depends_on fails the validator and sets halt "
              "with an empty frontier",
              packet["halt"] is True and packet["frontier"] == [] and packet["validator"]["exit_status"] != 0,
              json.dumps({"halt": packet["halt"], "frontier": packet["frontier"]}))

        # --- genuine clean done merge: passes, and does not appear in the frontier ---
        r = init(tmp / "clean-done")
        commit(r, "base")
        _git(r, "checkout", "-q", "-b", "karta/s/integration")
        _git(r, "checkout", "-q", "-b", "karta/s/item-a")
        commit(r, "the work [karta:item-a]")
        _git(r, "checkout", "-q", "karta/s/integration")
        _git(r, "merge", "-q", "--no-ff", "karta/s/item-a", "-m", "karta: merge item-a [karta:item-a]")
        done_a = ref_target(r, "HEAD") or ""
        _git(r, "update-ref", "refs/karta/s/item-a/done", done_a)
        binder_path = tmp / "clean-done.json"
        write_binder(binder_path, minimal_binder("s", [minimal_item("a"), minimal_item("b", depends_on=["a"])]))
        packet = build_packet(binder_path, r)
        check("a genuine done merge passes (no halt) and is absent from the frontier",
              packet["halt"] is False and "a" not in packet["frontier"] and "b" in packet["frontier"]
              and packet["done_provenance"]["a"]["ok"] is True,
              json.dumps(packet["done_provenance"]))

        # --- a valid second done merge, following the first, also passes and is absent
        _git(r, "checkout", "-q", "-b", "karta/s/item-b")
        commit(r, "the work [karta:item-b]")
        _git(r, "checkout", "-q", "karta/s/integration")
        _git(r, "merge", "-q", "--no-ff", "karta/s/item-b", "-m", "karta: merge item-b [karta:item-b]")
        done_b = ref_target(r, "HEAD") or ""
        _git(r, "update-ref", "refs/karta/s/item-b/done", done_b)
        packet2 = build_packet(binder_path, r)
        check("a second, later done merge that follows the first also passes and both "
              "are absent from the frontier",
              packet2["halt"] is False and packet2["frontier"] == []
              and packet2["done_provenance"]["b"]["ok"] is True,
              json.dumps(packet2["done_provenance"]))

        # --- forged-done: an off-chain plain clean done ------------------------------
        r = init(tmp / "forged-done-plain")
        commit(r, "base")
        _git(r, "checkout", "-q", "-b", "karta/s/integration")
        _git(r, "checkout", "-q", "-b", "karta/s/item-a")
        commit(r, "the work [karta:item-a]")
        # a merge commit exists, but done points at a commit OFF the first-parent chain
        _git(r, "checkout", "-q", "-b", "side")
        forged = commit(r, "karta: forged merge for item-a [karta:item-a]")
        _git(r, "checkout", "-q", "karta/s/integration")
        _git(r, "update-ref", "refs/karta/s/item-a/done", forged)
        binder_path = tmp / "forged-done-plain.json"
        write_binder(binder_path, minimal_binder("s", [minimal_item("a")]))
        packet = build_packet(binder_path, r)
        check("[forged-done] a plain clean done ref pointing off the integration "
              "branch's first-parent history sets halt",
              packet["halt"] is True and packet["done_provenance"]["a"]["ok"] is False
              and packet["done_provenance"]["a"]["first_parent_reachable"] is False,
              json.dumps(packet["done_provenance"]))

        # --- forged-done: an accepted ref alongside an off-chain done ----------------
        r = init(tmp / "forged-done-accepted")
        commit(r, "base")
        _git(r, "checkout", "-q", "-b", "karta/s/integration")
        _git(r, "checkout", "-q", "-b", "karta/s/item-a")
        tip = commit(r, "the work [karta:item-a]")
        _git(r, "checkout", "-q", "-b", "side")
        forged = commit(r, "karta: merge item-a\n\nKarta-Accepted: item-a\n"
                          "Karta-Accept-Reason: forged off-chain")
        _git(r, "checkout", "-q", "karta/s/integration")
        _git(r, "update-ref", "refs/karta/s/item-a/done", forged)
        _git(r, "update-ref", "refs/karta/s/item-a/accepted", tip)
        binder_path = tmp / "forged-done-accepted.json"
        write_binder(binder_path, minimal_binder("s", [minimal_item("a")]))
        packet = build_packet(binder_path, r)
        check("[forged-done] an accepted ref alongside an off-chain done also sets halt",
              packet["halt"] is True and packet["done_provenance"]["a"]["ok"] is False,
              json.dumps(packet["done_provenance"]))

        # --- parallelism: NEGATIVE CONTROLS ------------------------------------------
        items_by_id = {
            "iso": minimal_item("iso", touches=["x.py"], oracle={"type": "integration", "assertions": ["x"], "command": "true"}),
            "notouch": minimal_item("notouch", touches=["y.py"]),
        }
        items_by_id["notouch"] = minimal_item("notouch")  # no touches at all
        frontier = ["iso", "notouch"]
        par_isolated = compute_parallelism(items_by_id, frontier, {"supports_isolation": True})
        check("NEGATIVE CONTROL: supports_isolation true keeps a stateful-env oracle item parallel",
              "iso" in par_isolated["parallel"] and "iso" not in par_isolated["serialize"],
              json.dumps(par_isolated))

        items_by_id2 = {
            "hastouch": minimal_item("hastouch", touches=["z.py"]),
        }
        par_touches = compute_parallelism(items_by_id2, ["hastouch"], {"supports_isolation": False})
        check("NEGATIVE CONTROL: an item with touches present stays parallel "
              "(not undecidable:missing-touches)",
              "hastouch" in par_touches["parallel"] and "hastouch" not in par_touches["serialize"],
              json.dumps(par_touches))

        # --- parallelism: the two undecidable gates fire, conservatively -------------
        items_by_id3 = {
            "env": minimal_item("env", touches=["a.py"],
                                 oracle={"type": "e2e", "assertions": ["x"], "command": "true"}),
            "notouch2": minimal_item("notouch2"),
        }
        par_undecidable = compute_parallelism(items_by_id3, ["env", "notouch2"], {"supports_isolation": False})
        check("undecidable:stateful-env and undecidable:missing-touches both serialize "
              "and land in unresolved",
              "env" in par_undecidable["serialize"] and "notouch2" in par_undecidable["serialize"]
              and set(par_undecidable["unresolved"]) == {"env", "notouch2"}
              and "undecidable:stateful-env" in par_undecidable["reasons"]["env"]
              and "undecidable:missing-touches" in par_undecidable["reasons"]["notouch2"],
              json.dumps(par_undecidable))

        # --- parallelism: deterministic gates (touches overlap, shared_resources) ----
        items_by_id4 = {
            "p": minimal_item("p", touches=["shared.py"]),
            "q": minimal_item("q", touches=["shared.py"]),
            "r": minimal_item("r", touches=["r-only.py"], shared_resources=["db"]),
            "s": minimal_item("s", touches=["s-only.py"], shared_resources=["db"]),
            "t": minimal_item("t", touches=["t-only.py"], serialize=True),
        }
        par_det = compute_parallelism(items_by_id4, ["p", "q", "r", "s", "t"], {"supports_isolation": True})
        check("deterministic gates: touches-overlap, shared_resources, explicit serialize",
              {"p", "q"} <= set(par_det["serialize"]) and {"r", "s"} <= set(par_det["serialize"])
              and "t" in par_det["serialize"] and "explicit-serialize" in par_det["reasons"]["t"],
              json.dumps(par_det))

        # --- tools map: absolute, existing paths -------------------------------------
        binder_path = tmp / "tools.json"
        write_binder(binder_path, minimal_binder("tools-fixture", [minimal_item("a")]))
        r = init(tmp / "tools")
        commit(r, "base")
        packet = build_packet(binder_path, r)
        tools_ok = all(Path(p).is_absolute() and Path(p).name == name and Path(p).is_file()
                       for name, p in packet["tools"].items())
        check("tools map carries absolute, existing paths for all four scripts", tools_ok,
              json.dumps(packet["tools"]))

        # --- required top-level keys always present ----------------------------------
        need = {"validator", "slug", "default_branch", "integration_branch", "refs",
                "wave_tags", "done_provenance", "halt", "frontier", "parallelism", "tools"}
        check("packet always carries every contracted top-level key", need <= set(packet))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"self-test: {passed}/{total} cases passed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="deliver_preflight.py",
        description="Print one JSON packet answering deliver's Phase 0/1 and wave-loop Steps 1-2.",
    )
    ap.add_argument("--binder", type=Path, help="path to the binder JSON")
    ap.add_argument("--repo", type=Path, default=Path("."), help="repository to read (default: cwd)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _run_self_test()
    if args.binder is None:
        ap.error("provide --binder <path> or --self-test")
    if not args.binder.is_file():
        print(f"deliver_preflight: binder file not found: {args.binder}", file=sys.stderr)
        return 2

    packet = build_packet(args.binder, args.repo.resolve())
    print(json.dumps(packet, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
