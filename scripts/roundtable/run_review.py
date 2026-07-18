# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Roundtable review record writer and freshness checker (karta repo tooling).

Roundtable is an MCP server the agent calls, not a CLI a script can invoke.
So this helper never runs the panel — it files a *completed* panel result as a
review record tied to the exact content reviewed, and later answers one yes/no
question: does a current, unstale review record still exist for this binder or
this branch? The enforcement hook (scripts/hooks/roundtable_gate.py) shells to
`--check`; the agent runs `--record` after piping in the panel result.

Records live under .karta/roundtable/ as <key>.json — a binder keys on its slug
(<slug>.json), a branch on its tip (branch-<tip-sha>.json). Each record stores a
reviewed_hash (a binder's staged/worktree bytes, or a branch tip sha), so any
edit to the reviewed content invalidates it. min_providers (read from
.karta/roundtable.json) keeps "multi-model" honest: a panel below the floor, or
with a malformed entry, is refused and nothing is written.

Zero dependencies (pure stdlib), so every invocation form behaves identically:
  ... | python3 run_review.py --record --target <slug> --kind binder   # file it
  git show :<path> | python3 run_review.py --check --target <slug> \\
      --kind binder --bytes-stdin                                      # gate call
  python3 run_review.py --self-test                                    # exit 0/1
  uv run --script run_review.py --self-test                            # also fine
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = ".karta/roundtable.json"     # the house switch + panel settings
RECORD_DIR = ".karta/roundtable/"          # committed audit trail of reviews
BRANCH_PREFIX = "branch-"                   # branch record key prefix (hook contract)
DEFAULT_MIN_PROVIDERS = 2                   # floor when the config omits min_providers
RESERVED_KEYS = {"meta"}                    # non-panelist keys in the raw dispatch object


# --- config -------------------------------------------------------------------

def load_config(root: Path) -> dict:
    """Best-effort read of .karta/roundtable.json — {} on absent/malformed.
    Shape is validated separately by scripts/validate_plugin.py; the helper only
    needs min_providers (the floor) and a snapshot for the record."""
    try:
        data = json.loads((root / CONFIG_PATH).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def min_providers_floor(config: dict) -> int:
    """The min_providers floor from config, defaulting to 2. Rejects bools and
    anything below 1 — a floor of 0 would defeat the multi-model requirement."""
    mp = config.get("min_providers", DEFAULT_MIN_PROVIDERS)
    if isinstance(mp, bool) or not isinstance(mp, int) or mp < 1:
        return DEFAULT_MIN_PROVIDERS
    return mp


# --- panel normalization ------------------------------------------------------

def _first_str(*vals) -> str | None:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v
    return None


def _structured_verdict(entry: dict) -> str | None:
    structured = entry.get("structured")
    if isinstance(structured, dict):
        return _first_str(structured.get("verdict"))
    return None


def normalize_panel(raw) -> list[dict]:
    """Normalize the raw roundtable-critique result object into the stored panel
    list of {provider, verdict, summary}. The raw object is the tool's own
    DispatchResult: a map of agent-name -> per-panelist result (provider,
    response, status, ...) plus a reserved `meta` key. Anything that is not that
    object shape is rejected (ValueError) — this is the one accepted input.

    Mapping per entry: provider <- the entry's provider field, else its map key;
    verdict <- an explicit verdict, else a structured.verdict, else the run
    status; summary <- the entry's summary, else its response text. A field that
    cannot be derived is left None so the caller can refuse the panel."""
    if not isinstance(raw, dict):
        raise ValueError("panel input must be the roundtable-critique result object (a JSON object)")
    panel: list[dict] = []
    for key, val in raw.items():
        if key in RESERVED_KEYS:
            continue
        if not isinstance(val, dict):
            raise ValueError(f"panel entry {key!r} is not an object")
        panel.append({
            "provider": _first_str(val.get("provider"), key),
            "verdict": _first_str(val.get("verdict")) or _structured_verdict(val) or _first_str(val.get("status")),
            "summary": _first_str(val.get("summary"), val.get("response")) or "",
        })
    return panel


def validate_normalized(panel: list[dict], min_providers: int) -> tuple[bool, str]:
    """A panel is a review only if every entry names its provider and a verdict,
    and it carries at least min_providers distinct providers. Returns (ok, why)."""
    if not panel:
        return False, "panel is empty — no reviewer entries"
    for entry in panel:
        if not entry.get("provider"):
            return False, "a panel entry is missing its provider"
        if not entry.get("verdict"):
            return False, "a panel entry is missing its verdict"
    distinct = {entry["provider"] for entry in panel}
    if len(distinct) < min_providers:
        return False, (f"panel has {len(distinct)} distinct provider(s); "
                       f"min_providers requires at least {min_providers} — a single-model "
                       f"panel is not a multi-model review")
    return True, ""


# --- keys, hashes, git plumbing ----------------------------------------------

def binder_key(target: str) -> str:
    """A binder record keys on its slug: Path(target).stem + .json, so both a
    bare slug and a full .karta/binders/<slug>.json path resolve alike."""
    return f"{Path(target).stem}.json"


def branch_key(tip_sha: str) -> str:
    """A branch record keys on its tip: branch-<tip-sha>.json."""
    return f"{BRANCH_PREFIX}{tip_sha}.json"


def infer_kind(target: str) -> str:
    """Infer the target kind when --kind is omitted: a karta/<slug>/integration
    branch ends in /integration; everything else is a binder."""
    return "branch" if target.rstrip("/").endswith("/integration") else "binder"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> tuple[int, str]:
    """Run a git plumbing command from root; stdout+stderr interleaved."""
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return proc.returncode, proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def resolve_branch_tip(root: Path, branch: str) -> str | None:
    """The commit sha a branch points at, via git plumbing; None if unresolved."""
    rc, out = _git(root, "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}")
    out = out.strip()
    return out if rc == 0 and out else None


def binder_path(root: Path, target: str) -> Path:
    """The worktree binder file for a slug or an explicit .karta/binders path."""
    p = Path(target)
    if p.suffix == ".json":
        return p if p.is_absolute() else root / p
    return root / ".karta/binders" / f"{target}.json"


def read_binder_bytes(root: Path, target: str) -> bytes | None:
    try:
        return binder_path(root, target).read_bytes()
    except OSError:
        return None


def target_identity(root: Path, target: str, kind: str,
                    candidate_bytes: bytes | None = None) -> tuple[str | None, str | None]:
    """(record_key, reviewed_hash) for the target's CURRENT state, or (…, None)
    when it can't be resolved. For a binder, reviewed_hash is the sha256 of the
    candidate bytes when given (the hook feeds the staged blob via --bytes-stdin),
    else of the worktree binder file. For a branch, the key and hash both derive
    from the resolved tip sha, so a new commit yields a new key with no record."""
    if kind == "branch":
        tip = resolve_branch_tip(root, target)
        if tip is None:
            return None, None
        return branch_key(tip), tip
    key = binder_key(target)
    if candidate_bytes is not None:
        return key, sha256_hex(candidate_bytes)
    data = read_binder_bytes(root, target)
    if data is None:
        return key, None
    return key, sha256_hex(data)


# --- record / check -----------------------------------------------------------

def record_review(root: Path, target: str, kind: str, panel_raw) -> tuple[bool, str, Path | None]:
    """File a completed panel as .karta/roundtable/<key>.json and git-add it.
    Raises ValueError (writing nothing) when the panel is below the min_providers
    floor, has a malformed entry, or the target can't be resolved."""
    config = load_config(root)
    floor = min_providers_floor(config)
    panel = normalize_panel(panel_raw)
    ok, why = validate_normalized(panel, floor)
    if not ok:
        raise ValueError(why)
    key, reviewed_hash = target_identity(root, target, kind)
    if key is None or reviewed_hash is None:
        raise ValueError(f"could not resolve {kind} target {target!r} to record against")
    record = {
        "reviewed_hash": reviewed_hash,
        "tool": _first_str(config.get("tool")) or "roundtable-critique",
        "target_kind": kind,
        "target_ref": target,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config_snapshot": config,
        "panel": panel,
    }
    relpath = f"{RECORD_DIR}{key}"
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    staged = git_add(root, relpath)
    note = "" if staged else " (warning: git add failed — stage it manually so it lands with the commit)"
    return True, f"recorded {relpath}{note}", path


def git_add(root: Path, relpath: str) -> bool:
    rc, _ = _git(root, "add", "--", relpath)
    return rc == 0


def check_fresh(root: Path, target: str, kind: str,
                candidate_bytes: bytes | None = None) -> bool:
    """True iff a record at the derived key exists whose reviewed_hash equals the
    freshly recomputed hash of the current target. A missing or stale record, or
    an unresolvable ref, is an expected negative -> False (the gate blocks)."""
    key, current_hash = target_identity(root, target, kind, candidate_bytes)
    if key is None or current_hash is None:
        return False
    path = root / f"{RECORD_DIR}{key}"
    if not path.is_file():
        return False
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("reviewed_hash") == current_hash


# --- self-test ----------------------------------------------------------------

def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


def _run_self_test() -> int:
    import tempfile
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    # key derivation
    check("binder key from a full path", binder_key(".karta/binders/roundtable-edict.json") == "roundtable-edict.json")
    check("binder key from a bare slug", binder_key("roundtable-edict") == "roundtable-edict.json")
    check("branch key carries the branch- prefix", branch_key("deadbeef") == "branch-deadbeef.json")
    check("infer_kind branch on /integration", infer_kind("karta/x/integration") == "branch")
    check("infer_kind binder on a .json path", infer_kind(".karta/binders/x.json") == "binder")
    check("infer_kind binder on a bare slug", infer_kind("roundtable-edict") == "binder")

    # raw roundtable-critique object -> stored {provider, verdict, summary} list
    raw = {
        "codex": {"provider": "codex", "status": "NEEDS-FIXES", "response": "found a bug", "model": "gpt"},
        "fireworks-kimi": {"provider": "fireworks-kimi", "status": "COMMIT-READY", "response": "looks fine"},
        "meta": {"total_elapsed_ms": 10, "files_referenced": []},
    }
    panel = normalize_panel(raw)
    check("normalize drops meta and keeps one entry per panelist", len(panel) == 2)
    check("normalize maps provider/verdict/summary",
          panel[0] == {"provider": "codex", "verdict": "NEEDS-FIXES", "summary": "found a bug"})
    check("normalize prefers an explicit verdict field over status",
          normalize_panel({"a": {"provider": "a", "verdict": "reject", "status": "ok", "response": "r"}})[0]["verdict"] == "reject")
    check("normalize reads a structured.verdict when present",
          normalize_panel({"a": {"provider": "a", "structured": {"verdict": "approve"}, "response": "r"}})[0]["verdict"] == "approve")
    check("normalize rejects a non-object panel (a bare list)", _raises(lambda: normalize_panel([1, 2, 3])))
    check("normalize rejects a non-object panelist entry", _raises(lambda: normalize_panel({"a": "not-an-object"})))

    # the min_providers floor and malformed-entry rejection (pure)
    ok, _ = validate_normalized(panel, 2)
    check("a two-provider panel meets a floor of 2", ok)
    ok, _ = validate_normalized(normalize_panel({"codex": {"provider": "codex", "status": "ok", "response": "x"}, "meta": {}}), 2)
    check("a single-provider panel is below the floor", not ok)
    ok, _ = validate_normalized(panel, 3)
    check("the same panel is refused when min_providers is 3", not ok)
    ok, _ = validate_normalized(normalize_panel({"a": {"provider": "a", "response": "x"}, "b": {"provider": "b", "response": "y"}}), 2)
    check("an entry missing a verdict is refused", not ok)
    ok, _ = validate_normalized(normalize_panel({"": {"status": "ok", "response": "x"}, "b": {"provider": "b", "status": "ok", "response": "y"}}), 2)
    check("an entry missing a provider is refused", not ok)

    # repo-backed: record, staging, --bytes-stdin match/mismatch, staleness
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "selftest")
        (root / ".karta" / "binders").mkdir(parents=True)
        cfg = root / CONFIG_PATH

        def write_cfg(min_providers: int) -> None:
            cfg.write_text(json.dumps({"enabled": True, "tool": "roundtable-critique", "providers": [],
                                       "min_providers": min_providers, "focus": "",
                                       "points": {"plan_commit": True, "deliver_merge": True}}))

        write_cfg(2)
        slug = "demo"
        binder = root / ".karta" / "binders" / f"{slug}.json"
        binder.write_text('{"slug": "demo", "v": 1}')
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "init")

        ok, _, path = record_review(root, slug, "binder", raw)
        check("record_review writes the binder record file", ok and path is not None and path.is_file())
        record = json.loads(path.read_text())
        check("binder record key is <slug>.json", path.name == "demo.json")
        check("binder reviewed_hash is sha256 of the binder bytes",
              record.get("reviewed_hash") == sha256_hex(binder.read_bytes()))
        check("record stores the normalized panel and a config_snapshot",
              isinstance(record.get("panel"), list) and len(record["panel"]) == 2 and isinstance(record.get("config_snapshot"), dict))
        _, staged_out = _git(root, "diff", "--cached", "--name-only")
        check("--record stages the record with git add", ".karta/roundtable/demo.json" in staged_out)

        check("--bytes-stdin matches bytes equal to the recorded blob (fresh)",
              check_fresh(root, slug, "binder", binder.read_bytes()) is True)
        check("--bytes-stdin rejects differing bytes (stale)",
              check_fresh(root, slug, "binder", b'{"slug": "demo", "v": 2}') is False)

        check("plain --check is fresh before the binder is edited",
              check_fresh(root, slug, "binder", None) is True)
        binder.write_text('{"slug": "demo", "v": 99}')
        check("plain --check goes stale after the binder bytes change",
              check_fresh(root, slug, "binder", None) is False)
        check("--check is non-zero for a slug with no record at all",
              check_fresh(root, "never-reviewed", "binder", b"whatever") is False)

        record_dir = root / ".karta" / "roundtable"
        check("a single-provider panel is refused and writes no file",
              _raises(lambda: record_review(root, "solo", "binder",
                                            {"codex": {"provider": "codex", "status": "ok", "response": "x"}, "meta": {}}))
              and not (record_dir / "solo.json").exists())
        check("a missing-verdict entry is refused and writes no file",
              _raises(lambda: record_review(root, "noverdict", "binder",
                                            {"a": {"provider": "a", "response": "x"}, "b": {"provider": "b", "response": "y"}}))
              and not (record_dir / "noverdict.json").exists())

        write_cfg(3)
        check("the floor reads min_providers from .karta/roundtable.json (3 refuses a 2-provider panel)",
              _raises(lambda: record_review(root, "demo3", "binder", raw)) and not (record_dir / "demo3.json").exists())
        write_cfg(2)

        # branch record + tip-advance staleness
        _git(root, "checkout", "-q", "-b", "karta/demo/integration")
        tip1 = resolve_branch_tip(root, "karta/demo/integration")
        ok, _, bpath = record_review(root, "karta/demo/integration", "branch", raw)
        check("branch record file is branch-<tip>.json", bpath is not None and bpath.name == f"branch-{tip1}.json")
        brec = json.loads(bpath.read_text())
        check("branch reviewed_hash is the integration tip sha", brec.get("reviewed_hash") == tip1)
        check("branch --check is fresh at the recorded tip",
              check_fresh(root, "karta/demo/integration", "branch") is True)
        (root / "advance.txt").write_text("x")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "advance the tip")
        tip2 = resolve_branch_tip(root, "karta/demo/integration")
        check("a new commit advances the branch tip", tip1 != tip2)
        check("branch --check goes stale after a new commit (new tip, no record)",
              check_fresh(root, "karta/demo/integration", "branch") is False)
        check("branch --check is non-zero for a ref that does not exist",
              check_fresh(root, "karta/missing/integration", "branch") is False)

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


# --- CLI ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Record a roundtable panel review, or check a fresh one exists.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="file a completed panel (read from stdin) as a review record")
    mode.add_argument("--check", action="store_true", help="exit 0 iff a fresh matching record exists for the target")
    mode.add_argument("--self-test", action="store_true", help="run embedded fixtures and exit 0/1")
    ap.add_argument("--target", help="a binder slug/path, or a karta/<slug>/integration branch")
    ap.add_argument("--kind", choices=["binder", "branch"], help="target kind (inferred from --target when omitted)")
    ap.add_argument("--bytes-stdin", action="store_true",
                    help="--check --kind binder: hash candidate bytes from stdin (the staged blob) not the worktree file")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()

    if not args.target:
        print("run_review: --target is required for --record/--check", file=sys.stderr)
        return 2
    root = Path.cwd()
    kind = args.kind or infer_kind(args.target)

    if args.record:
        try:
            panel_raw = json.load(sys.stdin)
        except (ValueError, OSError) as e:
            print(f"run_review: refused to record — the panel on stdin is not valid JSON ({e})", file=sys.stderr)
            return 1
        try:
            _, message, _ = record_review(root, args.target, kind, panel_raw)
        except ValueError as e:
            print(f"run_review: refused to record — {e}", file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001 - report and fail, never write a half record
            print(f"run_review: error recording — {e}", file=sys.stderr)
            return 1
        print(message)
        return 0

    # --check
    candidate = sys.stdin.buffer.read() if args.bytes_stdin else None
    return 0 if check_fresh(root, args.target, kind, candidate) else 1


if __name__ == "__main__":
    sys.exit(main())
