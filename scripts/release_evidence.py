"""House release evidence: select commit bytes and fingerprint measured source.

Generated benchmark results are excluded to avoid making a run hash its own output.
Everything else known to Git (including nonignored new source) contributes bytes,
path, and Git file mode. This identifies content; it does not attest who ran it.
The coverage helpers below validate a result's structure and compare it with the
committed release inventory; they judge what a result claims, not how it was made.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess


def _git(root: Path, args: list[str], data: bytes | None = None) -> tuple[int, bytes]:
    p = subprocess.run(["git", "-C", str(root), *args], input=data, capture_output=True, timeout=30)
    return p.returncode, p.stdout


def _paths(git, args):
    code, output = git(args)
    if code:
        raise ValueError(f"cannot enumerate release source with git {' '.join(args)}")
    if isinstance(output, str):
        output = output.encode("utf-8")
    return {p.decode("utf-8") for p in output.split(b"\0") if p}


def is_result(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (len(parts) > 2 and parts[0] == "benchmarks" and "results" in parts[1:-1]
            or len(parts) == 3 and parts[:2] == ("benchmarks", "findings")
            and parts[2].startswith("doc-truth-") and parts[2].endswith(".json"))


def _working_entry(root: Path, path: str):
    p = root / path
    # Git records a replaced directory as a link, not the former descendants.
    if any(parent.is_symlink() for parent in p.parents if parent != root and root in parent.parents):
        return None
    try:
        mode = p.lstat().st_mode
        if stat.S_ISLNK(mode):
            code, oid = _git(root, ["hash-object", "--stdin"], os.readlink(p).encode("utf-8"))
            git_mode = "120000"
        if stat.S_ISREG(mode):
            # Ask Git to apply attributes/line-ending normalization, exactly as
            # add/commit will. Hashing does not write an object or touch the index.
            code, oid = _git(root, ["hash-object", "--path=" + path, str(p)])
            git_mode = "100755" if mode & stat.S_IXUSR else "100644"
            _, filemode = _git(root, ["config", "--bool", "core.filemode"])
            if filemode.strip() == b"false":
                _, tracked = _git(root, ["ls-files", "-s", "--", path])
                git_mode = tracked.split()[0].decode("ascii") if tracked.split() else "100644"
        elif stat.S_ISDIR(mode):
            _, tracked = _git(root, ["ls-files", "-s", "--", path])
            if tracked.startswith(b"160000 "):
                code, oid = _git(p, ["rev-parse", "HEAD"])
                if code:
                    raise ValueError(f"cannot identify release submodule: {path}")
                return "160000", oid.strip().decode("ascii")
            return None
        elif not stat.S_ISLNK(mode):
            raise ValueError(f"unsupported release source type: {path}")
        if code:
            raise ValueError(f"cannot hash release source: {path}")
        return git_mode, oid.strip().decode("ascii")
    except FileNotFoundError:
        return None


def _fingerprint(paths, read_entry):
    entries = []
    for path in sorted(paths):
        if is_result(path):
            continue
        entry = read_entry(path)
        if entry is not None:
            mode, oid = entry
            entries.append([path, mode, oid])
    return hashlib.sha256(json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


def working_fingerprint(root: Path) -> str:
    root = root.resolve()
    git = lambda args: _git(root, args)
    paths = _paths(git, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    return _fingerprint(paths, lambda path: _working_entry(root, path))


def commit_sources(command: str, git, root: Path):
    """Reuse the review gate's Git-selected index/worktree/HEAD semantics."""
    spec = importlib.util.spec_from_file_location("release_roundtable", Path(__file__).parent / "hooks/roundtable_gate.py")
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)
    try:
        _, words = rt.parse_invocation(command)
        expanded = []
        value_next = after_dashdash = False
        for word in words[2:]:
            # Expand only an option, never a message value or a literal path.
            if value_next or after_dashdash:
                expanded.append(word)
                value_next = False
                continue
            if word.text == "--":
                after_dashdash = True
            if word.text == "-am":
                expanded.extend(rt.tokenize("-a -m"))
                value_next = True
            else:
                expanded.append(word)
                value_next = word.text in rt.COMMIT_VALUE_OPTS
        commit = rt.parse_commit(expanded)
    except rt.Denial as e:
        raise ValueError(str(e)) from e

    def git_bytes(args):
        code, data = git(args)
        return code, data.encode("utf-8") if isinstance(data, str) else data

    def read_file(path):
        try:
            return (root / path).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None

    class ReleaseSources(rt.Sources):
        def source(self, path):
            if self.spec.all:
                code, data = self.git(["ls-files", "--", path])
                # A staged deletion is absent even if its old working file was
                # retained by git rm --cached; -a does not re-add that file.
                return "worktree" if code == 0 and path in rt._lines(data) else "absent"
            if self.spec.only and not self.spec.pathspecs:
                return "HEAD"
            return super().source(path)

        def read(self, path):
            try:
                return super().read(path)
            except rt.Denial as e:
                raise ValueError(str(e)) from e

    return ReleaseSources(commit, git_bytes, read_file, lambda path: (root / path).is_symlink())


def committed_fingerprint(root: Path, sources) -> str:
    """Fingerprint the prospective tree, including HEAD paths a pathspec retains."""
    root = root.resolve()
    git = lambda args: _git(root, args)
    paths = _paths(git, ["ls-files", "-z", "--cached"])
    paths |= _paths(git, ["ls-tree", "-rz", "--name-only", "HEAD"])

    def read_entry(path):
        source = sources.source(path)
        if source == "absent":
            return None
        if source == "worktree":
            return _working_entry(root, path)
        revision = "HEAD:" if source == "HEAD" else ":"
        code, oid = git(["rev-parse", "--verify", revision + path])
        if code:
            return None  # deleted from the selected tree
        args = ["ls-tree", "HEAD", "--", path] if source == "HEAD" else ["ls-files", "-s", "--", path]
        code, mode = git(args)
        if code or not mode.split():
            raise ValueError(f"cannot read release source mode: {path}")
        return mode.split()[0].decode("ascii"), oid.strip().decode("ascii")

    return _fingerprint(paths, read_entry)


# --- release coverage: the inventory and the result structure -------------------
#
# The inventory (benchmarks/gate/release-required.json) records one explicit release
# decision per bench-spec vector. The runner validates its own output with
# result_problems before writing; the release gate re-validates the committed bytes
# and recomputes coverage itself rather than trusting the result's summary objects.

INVENTORY_REL = "benchmarks/gate/release-required.json"
SPEC_REL = "benchmarks/bench-spec.json"
NOT_REQUIRED_KINDS = ("unimplemented", "needs-consumer", "deferred-live")
ROW_STATUSES = ("PASS", "FAIL", "ERROR", "SKIPPED")
RESULT_SCHEMA_VERSION = 2


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def inventory_problems(inventory, spec_ids) -> list[str]:
    """Every way the inventory fails to decide each spec vector explicitly."""
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1 \
            or not isinstance(inventory.get("vectors"), list):
        return ["inventory must be an object with schema_version 1 and a vectors list"]
    problems, seen = [], set()
    for entry in inventory["vectors"]:
        vid = entry.get("id") if isinstance(entry, dict) else None
        if not _text(vid):
            problems.append(f"inventory entry without an id: {entry!r}")
            continue
        if vid in seen:
            problems.append(f"{vid}: decided more than once")
        seen.add(vid)
        required = entry.get("required")
        if not isinstance(required, bool):
            problems.append(f"{vid}: 'required' must be true or false")
        elif required:
            if "not_required" in entry:
                problems.append(f"{vid}: a required vector cannot carry 'not_required'")
            allowed = entry.get("partial_allowed", False)
            if not isinstance(allowed, bool):
                problems.append(f"{vid}: 'partial_allowed' must be true or false")
            elif allowed and not _text(entry.get("partial_reason")):
                problems.append(f"{vid}: partial coverage is allowed without a written partial_reason")
        else:
            if entry.get("not_required") not in NOT_REQUIRED_KINDS:
                problems.append(f"{vid}: 'not_required' must be one of {', '.join(NOT_REQUIRED_KINDS)}")
            if not _text(entry.get("reason")):
                problems.append(f"{vid}: not required without a written reason")
            if entry.get("partial_allowed"):
                problems.append(f"{vid}: 'partial_allowed' applies only to required vectors")
        if not isinstance(entry.get("needs_consumers", False), bool):
            problems.append(f"{vid}: 'needs_consumers' must be true or false")
    missing = [v for v in spec_ids if v not in seen]
    unknown = sorted(seen - set(spec_ids))
    if missing:
        problems.append(f"no release decision for vector(s): {', '.join(missing)}")
    if unknown:
        problems.append(f"inventory names vector(s) not in the bench spec: {', '.join(unknown)}")
    return problems


def result_problems(doc) -> list[str]:
    """Structural defects in a gate result, including summaries that disagree with rows."""
    if not isinstance(doc, dict):
        return ["result is not a JSON object"]
    problems = []
    if doc.get("schema_version") != RESULT_SCHEMA_VERSION:
        problems.append(f"schema_version must be {RESULT_SCHEMA_VERSION}")
    for key in ("run_date", "karta_sha", "plugin_version"):
        if not _text(doc.get(key)):
            problems.append(f"'{key}' must be a non-empty string")
    source = doc.get("source_sha256")
    if not (isinstance(source, str) and len(source) == 64
            and all(c in "0123456789abcdef" for c in source)):
        problems.append("'source_sha256' must be a sha256 hex digest")
    for key in ("source_stable", "strict"):
        if not isinstance(doc.get(key), bool):
            problems.append(f"'{key}' must be true or false")
    if doc.get("only") is not None and not isinstance(doc.get("only"), list):
        problems.append("'only' must be null or a list")
    rows = doc.get("vectors")
    summary = doc.get("summary")
    if not isinstance(rows, list) or not isinstance(summary, dict):
        return problems + ["'vectors' must be a list and 'summary' an object"]
    counts = {s: 0 for s in ROW_STATUSES}
    known_open, ids = 0, set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or not _text(r.get("id")):
            problems.append(f"vector row {i} lacks an id")
            continue
        vid, status = r["id"], r.get("status")
        if vid in ids:
            problems.append(f"{vid}: reported more than once")
        ids.add(vid)
        if status not in ROW_STATUSES:
            problems.append(f"{vid}: status must be one of {', '.join(ROW_STATUSES)}")
            continue
        counts[status] += 1
        partial = r.get("partial")
        if status in ("PASS", "FAIL") and not isinstance(partial, bool):
            problems.append(f"{vid}: a {status} row must say whether it is partial")
        if status in ("SKIPPED", "ERROR") and partial is not None:
            problems.append(f"{vid}: a {status} row measured nothing and cannot carry a partial flag")
        findings = r.get("findings")
        if not isinstance(findings, list) or r.get("findings_count") != len(findings):
            problems.append(f"{vid}: findings_count must equal the number of findings")
            findings = []
        if not isinstance(r.get("implemented_checks"), list) or not isinstance(r.get("metrics"), dict) \
                or not isinstance(r.get("detail"), str):
            problems.append(f"{vid}: implemented_checks, metrics, and detail are required")
        # A passing regression check that still reports findings is carrying known,
        # open defects; a non-passing row's findings are not separable that way.
        expected = len(findings) if status == "PASS" else None
        if r.get("known_open_count") != expected:
            problems.append(f"{vid}: known_open_count must be {expected}")
        known_open += expected or 0
    want = {"total": len(rows), **{s.lower(): n for s, n in counts.items()},
            "known_open": known_open,
            "regression_health": "green" if not counts["FAIL"] and not counts["ERROR"] else "red"}
    for key, value in want.items():
        if summary.get(key) != value:
            problems.append(f"summary.{key} is {summary.get(key)!r} but the rows give {value!r}")
    return problems


def coverage_problems(doc, inventory) -> list[str]:
    """Why this result does not cover the release inventory; names each vector."""
    if doc.get("only") is not None:
        return ["a subset (--only) run cannot establish release coverage"]
    rows = {r.get("id"): r for r in doc.get("vectors", []) if isinstance(r, dict)}
    decided = {e["id"]: e for e in inventory["vectors"]}
    problems = []
    for vid, entry in decided.items():
        if not entry["required"]:
            continue
        r = rows.get(vid)
        if r is None:
            problems.append(f"required vector {vid} has no result")
        elif r.get("status") != "PASS":
            problems.append(f"required vector {vid} is {r.get('status')}, not PASS")
        elif r.get("partial") and not entry.get("partial_allowed", False):
            problems.append(f"required vector {vid} reported partial coverage, which the inventory does not justify")
    for vid in rows:
        if vid not in decided:
            problems.append(f"result vector {vid} has no release decision in {INVENTORY_REL}")
    return problems
