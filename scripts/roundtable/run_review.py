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

--round keeps a per-target HISTORY the record cannot carry: every review round,
what each provider said (or why it said nothing) and what was fixed or refuted,
appended to a sibling ledger (.karta/roundtable/<key>.rounds.json). --record
then refuses to file a record unless the ledger's last round reviewed the exact
bytes it is about to record — a record can only ever be the ledger's last round.

Zero dependencies (pure stdlib), so every invocation form behaves identically:
  ... | python3 run_review.py --record --target <slug> --kind binder   # file it
  ... | python3 run_review.py --round --target <slug> --kind binder \\
      --fixed "..." --refuted "..." --note "..."                       # log a round
  git show :<path> | python3 run_review.py --check --target <slug> \\
      --kind binder --bytes-stdin                                      # gate call
  python3 run_review.py --self-test                                    # exit 0/1
  uv run --script run_review.py --self-test                            # also fine
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = ".karta/roundtable.json"     # the house switch + panel settings
RECORD_DIR = ".karta/roundtable/"          # committed audit trail of reviews
BRANCH_PREFIX = "branch-"                   # branch record key prefix (hook contract)
DEFAULT_MIN_PROVIDERS = 2                   # floor when the config omits min_providers
RESERVED_KEYS = {"meta"}                    # non-panelist keys in the raw dispatch object
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")  # the only shape a bare binder slug may take


class TargetRefused(ValueError):
    """A refusal that is not a review judgement at all: an out-of-grammar
    target, an unresolvable one, a torn ledger, or a write that would land
    outside the repository. The CLI always maps this to exit 2, distinct from
    the exit-1 "panel below the floor" refusal --record has always used."""


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


# A failed dispatch reports one of these transport statuses. Such a status must
# never stand in as a review verdict, or a panel where every provider errored
# would satisfy the min_providers floor without a single real review happening.
ERROR_STATUSES = frozenset({"error", "timeout", "failed", "rate_limited", "cancelled"})


def _nonerror_status(entry: dict) -> str | None:
    """The transport status usable as a last-resort verdict — but only when the
    dispatch actually succeeded. An error-class status yields None so the entry
    carries no verdict and does not count toward the floor."""
    s = _first_str(entry.get("status"))
    return s if s and s.lower() not in ERROR_STATUSES else None


def raw_provider_entries(raw) -> list[tuple[str, dict]]:
    """(resolved_provider_name, raw_entry) for every non-reserved key in the raw
    dispatch object, in insertion order. Shared by normalize_panel (derives the
    stored verdict/summary) and round_review (also needs model/status straight
    from the raw entry, which normalize_panel's stored shape drops). Anything
    that is not the DispatchResult object shape is rejected (ValueError)."""
    if not isinstance(raw, dict):
        raise ValueError("panel input must be the roundtable-critique result object (a JSON object)")
    out: list[tuple[str, dict]] = []
    for key, val in raw.items():
        if key in RESERVED_KEYS:
            continue
        if not isinstance(val, dict):
            raise ValueError(f"panel entry {key!r} is not an object")
        out.append((_first_str(val.get("provider"), key), val))
    return out


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
    panel: list[dict] = []
    for provider, val in raw_provider_entries(raw):
        panel.append({
            "provider": provider,
            "verdict": _first_str(val.get("verdict")) or _structured_verdict(val) or _nonerror_status(val),
            "summary": _first_str(val.get("summary"), val.get("response")) or "",
        })
    return panel


def validate_normalized(panel: list[dict], min_providers: int) -> tuple[bool, str]:
    """A panel is a multi-model review only if it carries at least min_providers
    distinct providers that each returned a real verdict. An entry with no
    derivable verdict — a failed or errored dispatch — is dropped, not counted,
    so one provider erroring never sinks a genuine panel; but an entry with no
    provider at all is malformed and rejects the whole panel. Returns (ok, why)."""
    if not panel:
        return False, "panel is empty — no reviewer entries"
    for entry in panel:
        if not entry.get("provider"):
            return False, "a panel entry is missing its provider"
    reviewed = {entry["provider"] for entry in panel if entry.get("verdict")}
    if len(reviewed) < min_providers:
        return False, (f"panel has {len(reviewed)} provider(s) with a real verdict "
                       f"(errored or empty dispatches do not count); min_providers requires at "
                       f"least {min_providers} — an all-error or single-model dispatch is not a "
                       f"multi-model review")
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


def validate_binder_target(root: Path, target: str) -> tuple[bool, str]:
    """Refuse a binder target outside the accepted slug/path grammar, before any
    file is touched. A bare slug (no .json suffix) must match SLUG_RE outright —
    which already rejects traversal (`..`), a slash, an absolute path, a leading
    `~`, and uppercase, since none of those characters are in the grammar. A
    `.json` path form must resolve (by its stem) to such a slug AND resolve
    beneath the repository's .karta/binders/ directory — a path escaping that
    directory (`../../etc/passwd.json`) fails the containment check even though
    its stem alone might look like a slug. Branch targets are not covered here:
    a branch target's only grammar is "resolves to a hex tip sha", already
    enforced by target_identity via resolve_branch_tip."""
    if not target:
        return False, "binder target is empty"
    p = Path(target)
    if p.suffix == ".json":
        candidate = p if p.is_absolute() else (root / p)
        try:
            resolved = candidate.resolve()
            binders_dir = (root / ".karta" / "binders").resolve()
            resolved.relative_to(binders_dir)
        except (OSError, ValueError):
            return False, f"binder path {target!r} does not resolve under .karta/binders/"
        slug = p.stem
    else:
        slug = target
    if not SLUG_RE.match(slug):
        return False, f"binder target {target!r} does not match the allowed slug grammar ^[a-z0-9][a-z0-9-]*$"
    return True, ""


def roundtable_dir_ok(root: Path) -> bool:
    """True iff the resolved .karta/roundtable directory is beneath the resolved
    repository root. A symlinked roundtable directory pointing outside the repo
    resolves to somewhere else entirely, and this returns False — refusing the
    write rather than following the link out of the repository."""
    try:
        resolved = (root / RECORD_DIR).resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
        return True
    except (OSError, ValueError):
        return False


def read_existing_ledger(path: Path) -> dict | None:
    """None when no ledger file exists yet (not torn — just absent). Raises
    TargetRefused when a ledger file exists but is torn: unparseable JSON, no
    (or an empty) rounds list, or a last round missing reviewed_hash. A torn
    ledger is never reinitialized and never appended onto."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise TargetRefused(f"existing ledger {path} is not valid JSON — refusing rather than overwriting a torn file")
    if not isinstance(data, dict) or not isinstance(data.get("rounds"), list) or not data["rounds"]:
        raise TargetRefused(f"existing ledger {path} has no rounds recorded — refusing rather than overwriting a torn file")
    last = data["rounds"][-1]
    if not isinstance(last, dict) or not last.get("reviewed_hash"):
        raise TargetRefused(f"existing ledger {path} last round is missing reviewed_hash — refusing rather than overwriting a torn file")
    return data


def atomic_write_json(path: Path, data: dict) -> None:
    """Write data as pretty JSON via a temp file in the same directory, then an
    atomic rename — a crash mid-write never leaves a torn ledger in its place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def ledger_relpath_for_key(key: str) -> str:
    """The ledger path sibling to a record key: <slug>.json -> <slug>.rounds.json,
    branch-<tip>.json -> branch-<tip>.rounds.json."""
    return f"{RECORD_DIR}{Path(key).stem}.rounds.json"


def _safety_checks(root: Path, target: str, kind: str) -> None:
    """The checks --round and --record share before touching any file: binder
    grammar + no symlinked binder (binder targets only), and a roundtable
    directory that still resolves inside the repository (both kinds)."""
    if kind == "binder":
        ok, why = validate_binder_target(root, target)
        if not ok:
            raise TargetRefused(why)
        bpath = binder_path(root, target)
        if bpath.is_symlink():
            raise TargetRefused(f"binder path {bpath} is a symlink — refusing to hash through it")
    if not roundtable_dir_ok(root):
        raise TargetRefused("the resolved .karta/roundtable directory is not beneath the repository root")


# --- record / round / check ---------------------------------------------------

def record_review(root: Path, target: str, kind: str, panel_raw) -> tuple[bool, str, Path | None]:
    """File a completed panel as .karta/roundtable/<key>.json and git-add it.
    Raises TargetRefused (exit 2, writing nothing) for a grammar violation, a
    symlinked binder/roundtable-dir, a torn ledger, or a ledger whose last round
    reviewed different bytes than are about to be recorded. Raises ValueError
    (exit 1, the pre-existing behavior) when the panel is below the
    min_providers floor, has a malformed entry, or the target can't be
    resolved."""
    _safety_checks(root, target, kind)
    config = load_config(root)
    floor = min_providers_floor(config)
    panel = normalize_panel(panel_raw)
    ok, why = validate_normalized(panel, floor)
    if not ok:
        raise ValueError(why)
    key, reviewed_hash = target_identity(root, target, kind)
    if key is None or reviewed_hash is None:
        raise ValueError(f"could not resolve {kind} target {target!r} to record against")

    ledger_relpath = ledger_relpath_for_key(key)
    ledger = read_existing_ledger(root / ledger_relpath)
    extra: dict = {}
    notice = ""
    if ledger is not None:
        last_round = ledger["rounds"][-1]
        if last_round.get("reviewed_hash") != reviewed_hash:
            raise TargetRefused(
                f"the ledger's last round (round {last_round.get('round')}) reviewed different bytes than "
                f"are about to be recorded — run `python3 scripts/roundtable/run_review.py --round` on the "
                f"current content first")
        extra["rounds_ledger"] = ledger_relpath
        extra["final_round"] = last_round.get("round")
    else:
        notice = " (no round ledger found for this target — run `python3 scripts/roundtable/run_review.py --round` first to keep a history)"

    record = {
        "reviewed_hash": reviewed_hash,
        "tool": _first_str(config.get("tool")) or "roundtable-critique",
        "target_kind": kind,
        "target_ref": target,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config_snapshot": config,
        "panel": panel,
        **extra,
    }
    relpath = f"{RECORD_DIR}{key}"
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    staged = git_add(root, relpath)
    warn = "" if staged else " (warning: git add failed — stage it manually so it lands with the commit)"
    return True, f"recorded {relpath}{warn}{notice}", path


ROUND_HEADER_NOTE = (
    "Per-round ledger of this target's review history. The commit/merge gate keys freshness "
    "on the sibling record (<key>.json) — a fresh review of the exact reviewed bytes — but "
    "--record now refuses to file a record unless this ledger's last round reviewed those same "
    "bytes, so a record can never be filed for content the ledger never saw. This file is the "
    "history the record itself cannot carry: what each round found, what was fixed, what was "
    "refuted by execution. Rounds are appended in order; a header key already present is never "
    "rewritten or dropped by a later append."
)


def round_review(root: Path, target: str, kind: str, panel_raw,
                 fixed: list[str] | None, refuted: list[str] | None, note: str | None) -> tuple[str, int]:
    """Append one round to the target's ledger (.karta/roundtable/<key>.rounds.json)
    and git-add it. Every refusal here — grammar, unresolvable target, a torn
    ledger, a rejected payload, an escaping roundtable dir — is a TargetRefused
    or ValueError; the CLI maps both to exit 2 for --round, because none of them
    is a round that happened. Unlike --record, a round below min_providers is
    still appended (carrying below_floor: true) — the floor gates a record,
    never a round; the ledger is history regardless of whether the panel met
    the bar that day."""
    _safety_checks(root, target, kind)
    panel = normalize_panel(panel_raw)  # ValueError on a payload that isn't the DispatchResult shape
    raw_by_provider = dict(raw_provider_entries(panel_raw))
    config = load_config(root)
    floor = min_providers_floor(config)
    ok, _why = validate_normalized(panel, floor)
    below_floor = not ok

    key, reviewed_hash = target_identity(root, target, kind)
    if key is None or reviewed_hash is None:
        raise TargetRefused(f"could not resolve {kind} target {target!r} to review against")

    ledger_relpath = ledger_relpath_for_key(key)
    ledger_path = root / ledger_relpath
    existing = read_existing_ledger(ledger_path)  # TargetRefused if torn; None if absent

    providers_map: dict[str, dict] = {}
    for entry in panel:
        raw_val = raw_by_provider.get(entry["provider"], {})
        providers_map[entry["provider"]] = {
            "model": _first_str(raw_val.get("model")),
            "verdict": entry["verdict"],
            "status": _first_str(raw_val.get("status")),
        }

    round_number = (existing["rounds"][-1].get("round", len(existing["rounds"])) + 1) if existing else 1
    round_entry = {
        "round": round_number,
        "reviewed_hash": reviewed_hash,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "providers": providers_map,
        "findings_fixed": list(fixed or []),
        "findings_refuted_or_deferred": list(refuted or []),
        "notes": note or "",
        "below_floor": below_floor,
    }

    if existing is None:
        agents = []
        for provider in providers_map:
            model = _first_str(raw_by_provider.get(provider, {}).get("model"))
            agents.append({"provider": provider, "model": model} if model else {"provider": provider})
        ledger = {
            "target_ref": target,
            "target_kind": kind,
            "tool": _first_str(config.get("tool")) or "roundtable-critique",
            "agents": agents,
            "note": ROUND_HEADER_NOTE,
            "rounds": [round_entry],
        }
    else:
        ledger = existing
        ledger["rounds"].append(round_entry)

    atomic_write_json(ledger_path, ledger)
    staged = git_add(root, ledger_relpath)
    warn = "" if staged else " (warning: git add failed — stage it manually so it lands with the commit)"
    return f"appended round {round_number} to {ledger_relpath}{warn}", round_number


def git_add(root: Path, relpath: str) -> bool:
    rc, _ = _git(root, "add", "--", relpath)
    return rc == 0


def check_fresh(root: Path, target: str, kind: str,
                candidate_bytes: bytes | None = None) -> bool:
    """True iff a record at the derived key exists whose reviewed_hash equals the
    freshly recomputed hash of the current target. A missing or stale record, or
    an unresolvable ref, is an expected negative -> False (the gate blocks). This
    keys on the record alone — a ledger, with or without a record beside it,
    never satisfies --check; the ledger is history, not a review."""
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
    check("ledger path sibling to a binder key", ledger_relpath_for_key("demo.json") == ".karta/roundtable/demo.rounds.json")
    check("ledger path sibling to a branch key", ledger_relpath_for_key("branch-deadbeef.json") == ".karta/roundtable/branch-deadbeef.rounds.json")

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
    check("verdict-less entries do not count toward the floor", not ok)
    ok, _ = validate_normalized(normalize_panel({"": {"status": "ok", "response": "x"}, "b": {"provider": "b", "status": "ok", "response": "y"}}), 2)
    check("an entry missing a provider is refused", not ok)
    # an error-class status must not stand in as a verdict (the all-error gap)
    check("an errored dispatch carries no verdict",
          normalize_panel({"a": {"provider": "a", "status": "error", "response": ""}})[0]["verdict"] is None)
    all_error = {"a": {"provider": "a", "status": "error", "response": ""},
                 "b": {"provider": "b", "status": "timeout", "response": ""}}
    ok, _ = validate_normalized(normalize_panel(all_error), 2)
    check("an all-error two-provider panel is refused (no real review happened)", not ok)
    mixed = {"a": {"provider": "a", "status": "ok", "response": "x"},
             "b": {"provider": "b", "status": "ok", "response": "y"},
             "c": {"provider": "c", "status": "error", "response": ""}}
    ok, _ = validate_normalized(normalize_panel(mixed), 2)
    check("two real verdicts plus one errored provider meets a floor of 2", ok)

    # target grammar — pure, no filesystem needed for the bare-slug cases
    with tempfile.TemporaryDirectory() as gtd:
        groot = Path(gtd)
        (groot / ".karta" / "binders").mkdir(parents=True)
        (groot / ".karta" / "binders" / "demo.json").write_text('{"slug": "demo"}')
        check("a plain lowercase-hyphen slug is accepted", validate_binder_target(groot, "demo-two")[0])
        check("a slug starting with a digit is accepted", validate_binder_target(groot, "2cool")[0])
        check("a valid .karta/binders path is accepted", validate_binder_target(groot, ".karta/binders/demo.json")[0])
        for bad in ("../foo", "a/b", "/tmp/x", "~x", "Demo"):
            check(f"grammar refuses {bad!r}", not validate_binder_target(groot, bad)[0])
        check("a .json path escaping .karta/binders/ is refused",
              not validate_binder_target(groot, "../outside.json")[0])
        check("an empty target is refused", not validate_binder_target(groot, "")[0])

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
        check("a record with no ledger yet carries no rounds_ledger/final_round",
              "rounds_ledger" not in record and "final_round" not in record)
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
        binder.write_text('{"slug": "demo", "v": 1}')  # restore for the round-ledger section below

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

        # --round: first round creates the ledger, second appends, floor never blocks a round
        two_provider = {"claude": {"provider": "claude", "model": "m1", "status": "ok", "verdict": "revise", "response": "x"},
                        "codex": {"provider": "codex", "status": "ok", "verdict": "revise", "response": "y"},
                        "anti": {"provider": "anti", "status": "error", "response": ""},
                        "meta": {}}
        one_provider = {"claude": {"provider": "claude", "status": "ok", "verdict": "merge", "response": "x"}, "meta": {}}
        rslug = "round-demo"
        rbinder = root / ".karta" / "binders" / f"{rslug}.json"
        rbinder.write_text('{"slug": "round-demo", "v": 1}')
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "round-demo init")
        ledger_path = record_dir / f"{rslug}.rounds.json"

        msg, n = round_review(root, rslug, "binder", two_provider, ["f1"], ["r1"], "note1")
        check("first --round creates the ledger and reports round 1", n == 1 and ledger_path.is_file())
        led = json.loads(ledger_path.read_text())
        check("ledger header carries target_ref/target_kind", led.get("target_ref") == rslug and led.get("target_kind") == "binder")
        check("round 1 reviewed_hash is sha256 of the worktree binder bytes",
              led["rounds"][0]["reviewed_hash"] == sha256_hex(rbinder.read_bytes()))
        check("round 1 findings_fixed/refuted hold the given values",
              led["rounds"][0]["findings_fixed"] == ["f1"] and led["rounds"][0]["findings_refuted_or_deferred"] == ["r1"])
        check("round 1 keeps the errored provider with verdict null and a status string",
              led["rounds"][0]["providers"]["anti"]["verdict"] is None and led["rounds"][0]["providers"]["anti"]["status"])
        check("round 1 is not flagged below_floor (two real verdicts meets a floor of 2)",
              led["rounds"][0]["below_floor"] is False)
        _, staged_out = _git(root, "diff", "--cached", "--name-only")
        check("--round stages the ledger with git add", f".karta/roundtable/{rslug}.rounds.json" in staged_out)
        check("no leftover .tmp file after an atomic ledger write",
              not any(p.name.endswith(".tmp") for p in record_dir.iterdir()))

        msg, n = round_review(root, rslug, "binder", one_provider, None, None, None)
        check("a second --round with a single-provider panel is appended as round 2 flagged below_floor",
              n == 2 and json.loads(ledger_path.read_text())["rounds"][1]["below_floor"] is True)
        check("a below-floor --round writes no record", not (record_dir / f"{rslug}.json").exists())
        check("--check still fails for a target that has only a ledger, no record",
              check_fresh(root, rslug, "binder") is False)

        ok, msg, rpath = record_review(root, rslug, "binder", two_provider)
        rrec = json.loads(rpath.read_text())
        check("--record on a ledger-backed target writes rounds_ledger and final_round",
              rrec.get("rounds_ledger") == f".karta/roundtable/{rslug}.rounds.json" and rrec.get("final_round") == 2)

        rbinder.write_text('{"slug": "round-demo", "v": 2}')
        check("--record REFUSES (TargetRefused) once the binder changes under a stale ledger",
              isinstance(_exc(lambda: record_review(root, rslug, "binder", two_provider)), TargetRefused))
        check("a refused --record leaves the existing record's reviewed_hash untouched",
              json.loads(rpath.read_text()).get("reviewed_hash") == rrec.get("reviewed_hash"))
        round_review(root, rslug, "binder", two_provider, None, None, None)
        ok, msg, rpath2 = record_review(root, rslug, "binder", two_provider)
        check("--record passes again once a fresh --round reviewed the new bytes (final_round 3)",
              json.loads(rpath2.read_text()).get("final_round") == 3)

        led_now = json.loads(ledger_path.read_text())
        led_now["outcome"] = {"kept": True}
        ledger_path.write_text(json.dumps(led_now, indent=2) + "\n")
        round_review(root, rslug, "binder", one_provider, None, None, None)
        check("a header key an append did not write survives that append",
              json.loads(ledger_path.read_text()).get("outcome") == {"kept": True})

        # torn ledger — both modes refuse and leave the file byte-identical
        tornslug = "torn-demo"
        tbinder = root / ".karta" / "binders" / f"{tornslug}.json"
        tbinder.write_text('{"slug": "torn-demo"}')
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "torn-demo init")
        round_review(root, tornslug, "binder", two_provider, None, None, None)
        tpath = record_dir / f"{tornslug}.rounds.json"
        tpath.write_text("{torn")
        before_bytes = tpath.read_bytes()
        check("--round refuses on a torn ledger and leaves it byte-identical",
              _raises(lambda: round_review(root, tornslug, "binder", two_provider, None, None, None))
              and tpath.read_bytes() == before_bytes)
        check("--record refuses on a torn ledger, leaves it byte-identical, writes no record",
              _raises(lambda: record_review(root, tornslug, "binder", two_provider))
              and tpath.read_bytes() == before_bytes and not (record_dir / f"{tornslug}.json").exists())

        # symlinked roundtable dir — refused, nothing written through it
        with tempfile.TemporaryDirectory() as std, tempfile.TemporaryDirectory() as outside:
            sroot = Path(std)
            _git(sroot, "init", "-q")
            (sroot / ".karta" / "binders").mkdir(parents=True)
            (sroot / ".karta" / "binders" / "demo.json").write_text('{"slug": "demo"}')
            os.symlink(outside, str(sroot / ".karta" / "roundtable"))
            check("--round refuses through a symlinked roundtable dir resolving outside the repo",
                  isinstance(_exc(lambda: round_review(sroot, "demo", "binder", two_provider, None, None, None)), TargetRefused)
                  and not os.listdir(outside))

        # symlinked binder — refused
        with tempfile.TemporaryDirectory() as symd:
            symroot = Path(symd)
            _git(symroot, "init", "-q")
            (symroot / ".karta" / "binders").mkdir(parents=True)
            real = symroot / "real.json"
            real.write_text('{"slug": "linked"}')
            (symroot / ".karta" / "binders" / "linked.json").symlink_to(real)
            check("--round refuses to hash through a symlinked binder file",
                  isinstance(_exc(lambda: round_review(symroot, "linked", "binder", two_provider, None, None, None)), TargetRefused))

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

    # migrated context-economy ledger — shape sanity (the deep byte-preservation
    # walk against commit 4602ecf is the item oracle's job, run against the real
    # repo history; this self-test is a portable, repo-independent fixture check)
    ledger_file = Path(__file__).resolve().parents[2] / ".karta" / "roundtable" / "context-economy.rounds.json"
    if ledger_file.is_file():
        raw_text = ledger_file.read_text()
        led = json.loads(raw_text)
        check("migrated ledger no longer contains the string staged_blob_sha256", "staged_blob_sha256" not in raw_text)
        check("migrated ledger header uses target_ref/target_kind", led.get("target_ref") == "context-economy" and led.get("target_kind") == "binder")
        check("migrated ledger has thirteen rounds numbered 1..13",
              len(led.get("rounds", [])) == 13 and all(r.get("round") == i + 1 for i, r in enumerate(led["rounds"])))
        check("every migrated round carries reviewed_hash and below_floor is False",
              all("reviewed_hash" in r and r.get("below_floor") is False for r in led.get("rounds", [])))

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def _exc(fn) -> BaseException | None:
    try:
        fn()
        return None
    except Exception as e:  # noqa: BLE001 - the self-test inspects the exception type
        return e


# --- CLI ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Record a roundtable panel review, log a round, or check a fresh record exists.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="file a completed panel (read from stdin) as a review record")
    mode.add_argument("--round", action="store_true", help="append one review round (read from stdin) to the target's ledger")
    mode.add_argument("--check", action="store_true", help="exit 0 iff a fresh matching record exists for the target")
    mode.add_argument("--self-test", action="store_true", help="run embedded fixtures and exit 0/1")
    ap.add_argument("--target", help="a binder slug/path, or a karta/<slug>/integration branch")
    ap.add_argument("--kind", choices=["binder", "branch"], help="target kind (inferred from --target when omitted)")
    ap.add_argument("--bytes-stdin", action="store_true",
                    help="--check --kind binder: hash candidate bytes from stdin (the staged blob) not the worktree file")
    ap.add_argument("--fixed", action="append", default=[], help="--round: a finding this round fixed (repeatable)")
    ap.add_argument("--refuted", action="append", default=[], help="--round: a finding this round refuted or deferred (repeatable)")
    ap.add_argument("--note", default="", help="--round: a free-text note for this round")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_test()

    if not args.target:
        print("run_review: --target is required for --record/--round/--check", file=sys.stderr)
        return 2
    root = Path.cwd()
    kind = args.kind or infer_kind(args.target)

    if args.round:
        try:
            panel_raw = json.load(sys.stdin)
        except (ValueError, OSError) as e:
            print(f"run_review: refused to record round — the panel on stdin is not valid JSON ({e})", file=sys.stderr)
            return 2
        try:
            message, _round_no = round_review(root, args.target, kind, panel_raw, args.fixed, args.refuted, args.note)
        except Exception as e:  # noqa: BLE001 - every --round refusal is exit 2; none of it is a round that happened
            print(f"run_review: refused to record round — {e}", file=sys.stderr)
            return 2
        print(message)
        return 0

    if args.record:
        try:
            panel_raw = json.load(sys.stdin)
        except (ValueError, OSError) as e:
            print(f"run_review: refused to record — the panel on stdin is not valid JSON ({e})", file=sys.stderr)
            return 1
        try:
            _, message, _ = record_review(root, args.target, kind, panel_raw)
        except TargetRefused as e:
            print(f"run_review: refused to record — {e}", file=sys.stderr)
            return 2
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
