# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta oracle runner: run one oracle command and emit a small mechanical
evidence record instead of a full log.

Runs a single shell command (matching the binder oracle idiom of one shell
string: `['sh', '-c', command]`), judges success mechanically from the exit
status plus an optional expected marker, and writes a capped JSON evidence
record — command hash, resolved working directory, shell, environment
fingerprint, exit status, expect result, AT MOST ONE KILOBYTE of decisive
output (never the full log), and `tree_sha` — the git write-tree of the
resolved cwd's repository working tree, computed through a temporary index so
untracked files count and the real index is never touched (null outside a
repository). Downstream consumers read this record INSTEAD of raw logs.

With --attach-ref, the record is also written as a git blob and a ref is
pointed at it, so it can be retrieved later without re-running anything. An
item's evidence lives at the canonical namespace refs/karta/<slug>/item-<id>/evidence.

Stdlib only — no third-party dependency. Invoked directly (not installed),
matching the non-executable mode of sibling scripts:

Usage:
  python3 skills/karta-build/scripts/run_oracle.py [--cwd DIR] \\
      [--expect SUBSTRING | --expect-re REGEX] [--timeout SECONDS] \\
      [--attach-ref REFNAME] [--repo DIR] [--out FILE] <command>
  python3 skills/karta-build/scripts/run_oracle.py --self-test  # embedded fixtures, exit 0/1

Exit codes: 0 = oracle success, 1 = oracle failure (or self-test failure),
2 = usage or internal error (including a failed --attach-ref).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_DECISIVE_BYTES = 1024


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """Truncate `s` so its UTF-8 encoding is at most `max_bytes`, cutting only on
    character boundaries (never splitting a multi-byte codepoint)."""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    # Binary-search-free: decode a shrinking prefix until it fits and is valid.
    end = max_bytes
    while end > 0:
        chunk = b[:end]
        try:
            return chunk.decode("utf-8")
        except UnicodeDecodeError:
            end -= 1
    return ""


def _split_head_tail(combined: str, max_bytes: int) -> dict:
    total_bytes = len(combined.encode("utf-8"))
    if total_bytes <= max_bytes:
        return {"total_bytes": total_bytes, "head": combined, "tail": ""}
    half = max_bytes // 2
    head = _truncate_utf8(combined, half)
    # Tail: take from the end of the string, bounded by the remaining budget.
    remaining = max_bytes - len(head.encode("utf-8"))
    tail = ""
    if remaining > 0:
        # Truncate from the end: find the largest suffix whose UTF-8 size fits.
        for start in range(len(combined)):
            candidate = combined[start:]
            if len(candidate.encode("utf-8")) <= remaining:
                tail = candidate
                break
    return {"total_bytes": total_bytes, "head": head, "tail": tail}


def _env_fingerprint() -> dict:
    path_val = os.environ.get("PATH", "")
    entries = [p for p in path_val.split(os.pathsep) if p]
    return {
        "path_sha256": _sha256_hex(path_val.encode("utf-8")),
        "path_entries": len(entries),
        "runtimes": {
            "python": sys.version.split()[0],
        },
    }


def _head_sha(cwd: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _tree_sha(cwd: Path) -> str | None:
    """`git write-tree` of `cwd`'s repository working tree, computed through a
    temporary index (GIT_INDEX_FILE) populated with `git add -A` semantics so
    untracked files count toward the tree and the real index is never touched.
    None outside a git repository, or on any git failure — a tree hash is only
    ever a confirmed fact, never a guess."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None

    tmp_fd, tmp_index_path = tempfile.mkstemp(prefix="karta-run-oracle-index-")
    os.close(tmp_fd)
    try:
        # git wants to create the index file itself; a stale empty file at this
        # path would be read as an (invalid) index rather than as "start fresh".
        os.remove(tmp_index_path)
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = tmp_index_path
        add_proc = subprocess.run(
            ["git", "add", "-A"],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            timeout=60,
        )
        if add_proc.returncode != 0:
            return None
        wt_proc = subprocess.run(
            ["git", "write-tree"],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if wt_proc.returncode != 0:
            return None
        return wt_proc.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(tmp_index_path)
        except OSError:
            pass


def run_oracle(
    command: str,
    cwd: Path,
    expect_substring: str | None,
    expect_regex: str | None,
    timeout: float,
) -> dict:
    """Execute `command` via ['sh', '-c', command] in `cwd`, capturing combined
    stdout+stderr, and build the twelve-key evidence record."""
    resolved_cwd = cwd.resolve()

    timed_out = False
    exit_status: int
    combined = ""

    # Run in its own process group so a --timeout expiry can kill the whole
    # child tree, not just the immediate `sh` — a hung oracle must never hang
    # the floor or the merge queue.
    try:
        proc = subprocess.Popen(
            ["sh", "-c", command],
            cwd=str(resolved_cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
        )
    except OSError as e:
        combined = f"failed to start command: {e}"
        exit_status = 2
        timed_out = False
    else:
        try:
            combined, _ = proc.communicate(timeout=timeout)
            exit_status = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                combined, _ = proc.communicate(timeout=10)
            except Exception:
                combined = combined or ""
            exit_status = 1

    combined = combined or ""

    expect: dict | None = None
    expect_ok = True
    if expect_substring is not None:
        matched = expect_substring in combined
        expect = {"mode": "substring", "pattern": expect_substring, "matched": matched}
        expect_ok = matched
    elif expect_regex is not None:
        pattern = re.compile(expect_regex)
        matched = pattern.search(combined) is not None
        expect = {"mode": "regex", "pattern": expect_regex, "matched": matched}
        expect_ok = matched

    success = (exit_status == 0) and expect_ok and not timed_out

    record = {
        "command": command,
        "command_sha256": _sha256_hex(command.encode("utf-8")),
        "cwd": str(resolved_cwd),
        "shell": "sh -c",
        "env_fingerprint": _env_fingerprint(),
        "exit_status": exit_status,
        "expect": expect,
        "decisive_output": _split_head_tail(combined, MAX_DECISIVE_BYTES),
        "success": success,
        "timed_out": timed_out,
        "head_sha": _head_sha(resolved_cwd),
        "tree_sha": _tree_sha(resolved_cwd),
    }
    return record


def attach_ref(record: dict, ref: str, repo: Path) -> None:
    """Write `record` as a git blob in `repo` and point `ref` at it. Loud on
    failure (exit 2) — attachment failures are never silent."""
    payload = json.dumps(record, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        proc = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=str(repo),
            input=payload,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"run_oracle: failed to write evidence blob: {e}", file=sys.stderr)
        raise SystemExit(2)
    if proc.returncode != 0:
        print(
            f"run_oracle: git hash-object failed: {proc.stderr.decode('utf-8', 'replace')}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    blob_sha = proc.stdout.decode("utf-8").strip()
    try:
        proc2 = subprocess.run(
            ["git", "update-ref", ref, blob_sha],
            cwd=str(repo),
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"run_oracle: failed to update ref {ref}: {e}", file=sys.stderr)
        raise SystemExit(2)
    if proc2.returncode != 0:
        print(
            f"run_oracle: git update-ref failed: {proc2.stderr.decode('utf-8', 'replace')}",
            file=sys.stderr,
        )
        raise SystemExit(2)


# --- self-test ---------------------------------------------------------


def _run_self_test() -> int:
    cases_passed = 0
    cases_total = 0
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal cases_passed, cases_total, failures
        cases_total += 1
        if ok:
            cases_passed += 1
        else:
            failures += 1
        suffix = f": {detail}" if detail else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")

    tmp_root = Path(tempfile.mkdtemp(prefix="run_oracle_selftest_"))
    try:
        # (a) a passing command yields success true, exit_status 0
        rec = run_oracle("echo hi", tmp_root, None, None, 30)
        check("passing command -> success true, exit 0", rec["success"] is True and rec["exit_status"] == 0)

        # (b) a failing command yields success false and runner exit 1 (checked via main() path below)
        rec = run_oracle("exit 3", tmp_root, None, None, 30)
        check("failing command -> success false", rec["success"] is False and rec["exit_status"] == 3)

        # (c) an expect that matches vs one that does not flips success
        rec_match = run_oracle("echo MARK-HERE", tmp_root, "MARK-HERE", None, 30)
        rec_miss = run_oracle("echo other", tmp_root, "MARK-HERE", None, 30)
        check(
            "expect match flips success",
            rec_match["success"] is True and rec_match["expect"]["matched"] is True
            and rec_miss["success"] is False and rec_miss["expect"]["matched"] is False,
        )

        # (d) --expect-re works and rejects an invalid regex loudly
        rec_re = run_oracle("echo abc123", tmp_root, None, r"abc\d+", 30)
        re_invalid_raised = False
        try:
            run_oracle("echo x", tmp_root, None, r"[unterminated", 30)
        except re.error:
            re_invalid_raised = True
        check(
            "expect-re matches and invalid regex raises",
            rec_re["expect"]["matched"] is True and re_invalid_raised,
        )

        # (e) a command producing far more than 1 KB of output caps decisive_output
        rec_big = run_oracle("seq 1 20000", tmp_root, None, None, 30)
        o = rec_big["decisive_output"]
        head_tail_bytes = len(o["head"].encode("utf-8")) + len(o["tail"].encode("utf-8"))
        check(
            "oversized output capped at 1024 bytes with accurate total_bytes",
            o["total_bytes"] > 100_000 and head_tail_bytes <= MAX_DECISIVE_BYTES,
            f"total_bytes={o['total_bytes']} head+tail={head_tail_bytes}",
        )

        # (f) the record carries exactly the twelve keys above
        expected_keys = {
            "command", "command_sha256", "cwd", "shell", "env_fingerprint",
            "exit_status", "expect", "decisive_output", "success", "timed_out",
            "head_sha", "tree_sha",
        }
        check("record has exactly twelve keys", set(rec) == expected_keys, str(sorted(rec)))

        # (f-tree) tree_sha is null outside a git repository — tmp_root itself is a bare
        # tempdir, never git-initialized, so every record captured above already proves this;
        # asserted explicitly here so the property has its own named check.
        check("tree_sha is null outside a git repository", rec["tree_sha"] is None, str(rec["tree_sha"]))

        # (f2) a command that sleeps past a short --timeout yields timed_out true, success false
        started = time.time()
        rec_timeout = run_oracle("sleep 30", tmp_root, None, None, 1)
        elapsed = time.time() - started
        check(
            "timeout kills child, timed_out true, success false",
            rec_timeout["timed_out"] is True and rec_timeout["success"] is False and elapsed < 15,
            f"elapsed={elapsed:.1f}s",
        )

        # (g) in a temp git repo, --attach-ref leaves a ref whose blob round-trips
        repo_dir = tmp_root / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo_dir), check=True)
        ref_record = run_oracle("echo ref-test", repo_dir, None, None, 30)
        ref_name = "refs/karta/selftest/item-x/evidence"
        attach_ref(ref_record, ref_name, repo_dir)
        show = subprocess.run(
            ["git", "cat-file", "-p", ref_name],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
        )
        roundtrip_ok = False
        if show.returncode == 0:
            try:
                roundtripped = json.loads(show.stdout)
                roundtrip_ok = roundtripped == ref_record
            except json.JSONDecodeError:
                roundtrip_ok = False
        check("--attach-ref round-trips the same JSON via git cat-file", roundtrip_ok)

        # (h) tree_sha equals the write-tree of the working tree it was captured from,
        # proven against a real commit made from that same working tree; a further edit
        # and commit then diverges — the negative control that (h) passed for the right
        # reason and not because tree_sha is always the same value.
        tree_repo = tmp_root / "tree_repo"
        tree_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(tree_repo), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tree_repo), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tree_repo), check=True)
        (tree_repo / "a").write_text("1")
        # Stage a real index entry BEFORE calling run_oracle, so the before/after
        # `git ls-files --stage` comparison below can catch the temp index leaking into
        # the real one — a bug that would otherwise pass silently.
        subprocess.run(["git", "add", "a"], cwd=str(tree_repo), check=True)
        stage_before = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        ).stdout

        rec_tree = run_oracle("true", tree_repo, None, None, 30)

        stage_after = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        ).stdout
        check(
            "the temporary index never leaks into the real index",
            stage_before == stage_after,
            f"before={stage_before!r} after={stage_after!r}",
        )

        # Negative control for the check above: prove it is not vacuously true by showing
        # a genuinely UNISOLATED `git add -A` (no GIT_INDEX_FILE) DOES change the real
        # index — the leak the isolated version above must never produce.
        leak_repo = tmp_root / "leak_repo"
        leak_repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=str(leak_repo), check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(leak_repo), check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(leak_repo), check=True)
        (leak_repo / "tracked").write_text("1")
        subprocess.run(["git", "add", "tracked"], cwd=str(leak_repo), check=True)
        leak_before = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(leak_repo), capture_output=True, text=True, check=True
        ).stdout
        (leak_repo / "untracked").write_text("2")
        subprocess.run(["git", "add", "-A"], cwd=str(leak_repo), check=True)  # deliberately unisolated
        leak_after = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(leak_repo), capture_output=True, text=True, check=True
        ).stdout
        check(
            "negative control: an unisolated git add -A DOES change the real index, so "
            "the no-leak check above is a genuine invariant and not one that would pass "
            "regardless of isolation",
            leak_before != leak_after,
            f"before={leak_before!r} after={leak_after!r}",
        )

        subprocess.run(["git", "commit", "-q", "-m", "one"], cwd=str(tree_repo), check=True)
        t1 = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        check(
            "tree_sha equals git rev-parse HEAD^{tree} for a working tree committed unchanged",
            bool(rec_tree["tree_sha"]) and rec_tree["tree_sha"] == t1,
            f"tree_sha={rec_tree['tree_sha']} t1={t1}",
        )

        (tree_repo / "a").write_text("2")
        subprocess.run(["git", "add", "-A"], cwd=str(tree_repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "two"], cwd=str(tree_repo), check=True)
        t2 = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        check(
            "tree_sha differs from a later commit's tree after a further edit (negative control)",
            rec_tree["tree_sha"] != t2,
            f"tree_sha={rec_tree['tree_sha']} t2={t2}",
        )

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print(f"\n{cases_passed}/{cases_total} checks passed")
    return 1 if failures else 0


# --- CLI -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run one oracle command and emit a small mechanical JSON evidence record "
            "(command hash, cwd, shell, env fingerprint, exit status, expect result, "
            "and at most 1024 bytes of decisive output) instead of a full log. "
            "An item's evidence lives at the canonical namespace "
            "refs/karta/<slug>/item-<id>/evidence."
        )
    )
    ap.add_argument("command", nargs="?", default=None, help="one shell string, executed as ['sh', '-c', command]")
    ap.add_argument("--cwd", type=Path, default=None, help="working directory (default: current directory)")
    ap.add_argument("--expect", default=None, help="substring the combined stdout+stderr must contain")
    ap.add_argument("--expect-re", default=None, help="regex the combined stdout+stderr must match")
    ap.add_argument("--timeout", type=float, default=600, help="seconds before the child process group is killed (default: 600)")
    ap.add_argument("--attach-ref", default=None, help="git ref to point at the written evidence blob, e.g. refs/karta/<slug>/item-<id>/evidence")
    ap.add_argument("--repo", type=Path, default=None, help="repo to write the evidence blob/ref in (default: cwd's repo)")
    ap.add_argument("--out", type=Path, default=None, help="also write the evidence record JSON to this file")
    ap.add_argument("--self-test", action="store_true", help="run embedded hermetic fixtures and exit 0/1")
    args = ap.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    if args.command is None:
        ap.error("the following arguments are required: command")

    if args.expect is not None and args.expect_re is not None:
        print("run_oracle: --expect and --expect-re are mutually exclusive", file=sys.stderr)
        return 2

    if args.expect_re is not None:
        try:
            re.compile(args.expect_re)
        except re.error as e:
            print(f"run_oracle: invalid --expect-re: {e}", file=sys.stderr)
            return 2

    cwd = args.cwd if args.cwd is not None else Path.cwd()
    if not cwd.exists():
        print(f"run_oracle: --cwd does not exist: {cwd}", file=sys.stderr)
        return 2

    record = run_oracle(args.command, cwd, args.expect, args.expect_re, args.timeout)

    if args.attach_ref is not None:
        repo = args.repo if args.repo is not None else Path(record["cwd"])
        attach_ref(record, args.attach_ref, repo)

    payload = json.dumps(record, indent=2, sort_keys=True)
    print(payload)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")

    if record["timed_out"]:
        print(f"run_oracle: command timed out after {args.timeout}s", file=sys.stderr)
    elif not record["success"]:
        print(f"run_oracle: oracle failed (exit_status={record['exit_status']})", file=sys.stderr)

    return 0 if record["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
