# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""karta oracle runner: run one oracle command and emit a small mechanical
evidence record instead of a full log.

Runs a single shell command (sh -c on POSIX, cmd.exe /d /s /c on Windows),
matching the binder's one-shell-string form. Judges success from the exit
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
import contextlib
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
    # `b` is the UTF-8 encoding of `s`, so the only invalid bytes a cut can
    # produce are the truncated tail of one codepoint — exactly what "ignore"
    # drops. Equivalent to shrinking until the prefix decodes, without the loop.
    return b[:max_bytes].decode("utf-8", "ignore")


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
         encoding="utf-8")
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
         encoding="utf-8")
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
         encoding="utf-8")
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


class _WindowsJob:
    """Private, non-inheritable job. No child may break away.

    https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self._api, name)
            fn.argtypes, fn.restype = args, result
        self.handle = self._api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE only
        if not self._api.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, proc: subprocess.Popen) -> None:
        # The owned process is alive and blocked at the gate, so its PID cannot
        # be reused before we open this handle.
        process = self._api.OpenProcess(0x0101, False, proc.pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            if not self._api.AssignProcessToJobObject(self.handle, process):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._api.CloseHandle(process)

    def terminate(self) -> None:
        if not self._api.TerminateJobObject(self.handle, 1):
            raise self._ctypes.WinError(self._ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            if not self._api.CloseHandle(self.handle):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            self.handle = None


# Isolated Python startup runs no site/customization code. The shell is created
# only after the parent assigns this waiting process to the private job and
# releases its stdin gate. EOF without release must never launch the command.
_WINDOWS_GATE = (
    "import sys\n"
    "if sys.stdin.buffer.read(1) != b'G': sys.exit(2)\n"
    "import subprocess\n"
    "sys.exit(subprocess.call(sys.argv[1], stdin=subprocess.DEVNULL))\n"
)


def _run_windows(command: str, cwd: Path, timeout: float) -> tuple[str, int, bool]:
    shell = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
    # /s strips precisely the outer pair. Do not apply CRT list quoting to the
    # shell command itself: that would corrupt embedded quotes and batch paths.
    shell_command = f'"{shell}" /d /s /c "{command}"'
    job = _WindowsJob()
    proc = None
    abandoned = False
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _WINDOWS_GATE, shell_command],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
         encoding="utf-8")
        try:
            job.assign(proc)
        except BaseException:
            # Still at the gate: no shell or descendants can have been started.
            proc.kill()
            raise
        try:
            combined, _ = proc.communicate(input="G", timeout=timeout)
            return combined, proc.returncode, False
        except subprocess.TimeoutExpired:
            job.terminate()
            try:
                combined, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                # A descendant survived the job kill and is holding the output
                # pipe open. That is possible even with the assign-then-release
                # protocol airtight: launching an MSIX app-execution alias (the
                # Store python3, for one) activates the real process through a
                # broker service, so it is never a child and never joins the
                # job. Waiting for its EOF would block until IT exits — the
                # unbounded stall this runner exists to prevent — so abandon
                # the pipe: the timeout itself is the evidence, and partial
                # output is not worth an unbounded wait. proc (the gate) is in
                # the job and already dead; kill() is a no-op belt. The finally
                # must not close the abandoned streams either: communicate()'s
                # orphaned reader thread is still blocked in them, and close()
                # would block right back until the survivor exits — the reader
                # and the pipe are left to the interpreter's cleanup instead.
                abandoned = True
                proc.kill()
                return "", 1, True
            return combined, 1, True
    finally:
        # Also reap descendants left behind by a shell that exited normally.
        job.close()
        if proc is not None and not abandoned:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()


def run_oracle(
    command: str,
    cwd: Path,
    expect_substring: str | None,
    expect_regex: str | None,
    timeout: float,
) -> dict:
    """Execute `command` through the platform shell in `cwd`, capturing combined
    stdout+stderr, and build the twelve-key evidence record."""
    resolved_cwd = cwd.resolve()

    timed_out = False
    exit_status: int
    combined = ""

    # Run in its own process group so a --timeout expiry can kill the whole
    # child tree, not just the immediate `sh` — a hung oracle must never hang
    # the floor or the merge queue.
    if os.name == "nt":
        try:
            combined, exit_status, timed_out = _run_windows(command, resolved_cwd, timeout)
        except OSError as e:
            combined = f"failed to start command: {e}"
            exit_status = 2
    else:
        try:
            proc = subprocess.Popen(
                ["sh", "-c", command],
                cwd=str(resolved_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid,
             encoding="utf-8")
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
        "shell": "cmd.exe /d /s /c" if os.name == "nt" else "sh -c",
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
        def python_command(source: str) -> str:
            script = tmp_root / "script space & quote" / "probe.py"
            script.parent.mkdir(exist_ok=True)
            script.write_text(source, encoding="utf-8")
            if os.name == "nt":
                return f'"{sys.executable}" "{script}"'
            import shlex
            return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"

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
        rec_big = run_oracle(
            python_command("print('x' * 120000)"), tmp_root, None, None, 30)
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
        rec_timeout = run_oracle(
            python_command("import time; time.sleep(30)"), tmp_root, None, None, 1)
        elapsed = time.time() - started
        check(
            "timeout kills child, timed_out true, success false",
            rec_timeout["timed_out"] is True and rec_timeout["success"] is False and elapsed < 15,
            f"elapsed={elapsed:.1f}s",
        )
        check("shell evidence names actual platform shell",
              rec_timeout["shell"] == ("cmd.exe /d /s /c" if os.name == "nt" else "sh -c"))
        cmd = python_command("import os; print(os.getcwd())") + " && echo CHAIN-OK"
        chained = run_oracle(cmd, tmp_root, "CHAIN-OK", None, 30)
        check("quoted paths, backslashes, ampersand, cwd, and chaining",
              chained["success"] and str(tmp_root.resolve()) in chained["decisive_output"]["head"]
              and chained["command_sha256"] == _sha256_hex(cmd.encode("utf-8")))
        regex_miss = run_oracle("echo abc", tmp_root, None, r"xyz\d+", 30)
        check("regex mismatch fails", not regex_miss["success"]
              and not regex_miss["expect"]["matched"])
        bad_cwd = run_oracle("echo must-not-run", tmp_root / "missing", None, None, 30)
        check("launch failure is evidence, not success",
              not bad_cwd["success"] and bad_cwd["exit_status"] == 2)
        cli = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--cwd", str(tmp_root), "exit 7"],
            capture_output=True, text=True, timeout=30, encoding="utf-8")
        check("CLI maps command nonzero to runner exit 1",
              cli.returncode == 1 and json.loads(cli.stdout)["exit_status"] == 7)
        if os.name == "nt":
            batch = tmp_root / "script space & quote" / "npm.cmd"
            batch.write_bytes(b"@echo off\r\necho BATCH-%1\r\nexit /b 0\r\n")
            batch_rec = run_oracle(f'"{batch}" run && echo BATCH-CHAIN', tmp_root,
                                   "BATCH-CHAIN", None, 30)
            check("batch commands run with arguments and &&",
                  batch_rec["success"] and "BATCH-run" in batch_rec["decisive_output"]["head"])

            # A waiting bootstrap must not start user code, even on EOF or a
            # rejected assignment. These are real kernel calls, not API mocks.
            marker = tmp_root / "gate-marker"
            gate_command = python_command(
                f"from pathlib import Path; Path({str(marker)!r}).touch()")
            shell = str(Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe")
            for released in (False, True):
                with subprocess.Popen(
                    [sys.executable, "-I", "-S", "-c", _WINDOWS_GATE,
                     f'"{shell}" /d /s /c "{gate_command}"'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True,
                 encoding="utf-8") as gated:
                    job = _WindowsJob()
                    try:
                        if released:
                            job.assign(gated)
                        else:
                            job.close()
                            try:
                                job.assign(gated)
                            except OSError:
                                check("invalid job assignment fails closed", not marker.exists())
                            else:
                                check("invalid job assignment fails closed", False)
                        # 60, not 10: this waits on a python -> cmd.exe -> python
                        # spawn chain, and a loaded machine (a gated commit runs
                        # the whole floor) or a cold AV scan can hold it past 10s.
                        # The check is about gating semantics, never speed.
                        gated.communicate(input="G" if released else "", timeout=60)
                        check("gate runs only after assignment and release" if released else
                              "gate EOF never launches user code",
                              marker.exists() is released
                              and gated.returncode == (0 if released else 2))
                    finally:
                        job.close()

            # The two descendant-containment fixtures need descendants the job
            # CAN contain. When this interpreter is an MSIX app-execution alias
            # (the Store python3), every python the tree spawns through cmd.exe
            # is activated by the AppX broker OUTSIDE any job — the fixtures
            # would measure Windows activation, not this runner's containment.
            # The real packaged binary cannot stand in (cmd gets Access is
            # denied launching it), so the two claims are unverifiable under
            # this interpreter and are skipped BY NAME rather than left to fail
            # as if the runner were broken. The limit is production-real and
            # documented in _run_windows: an alias-activated descendant
            # survives the job kill, and the drain abandons its pipe.
            alias_python = "\\microsoft\\windowsapps\\" in sys.executable.lower()
            if alias_python:
                check("timeout kills children and grandchildren — SKIPPED: "
                      "app-execution-alias python, MSIX activation escapes any "
                      "job (documented containment limit)", True)
                check("timeout leaves unrelated owned control alive — SKIPPED: "
                      "same alias limit", True)

            if not alias_python:
                # Hold process handles, not PID guesses, to prove descendant death.
                import ctypes
                from ctypes import wintypes
                api = ctypes.WinDLL("kernel32", use_last_error=True)
                api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                api.OpenProcess.restype = wintypes.HANDLE
                api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                api.WaitForSingleObject.restype = wintypes.DWORD
                api.CloseHandle.argtypes = [wintypes.HANDLE]
                api.CloseHandle.restype = wintypes.BOOL
                import threading
                handles: list[int] = []
                monitor_errors: list[str] = []
                ready = tmp_root / "descendants-ready"
                release = tmp_root / "handles-open"
                tree_source = (
                    "import os, pathlib, subprocess, sys, time\n"
                    "root = pathlib.Path(sys.argv[1])\n"
                    "depth = int(sys.argv[2])\n"
                    "(root / ('pid-' + str(depth))).write_text(str(os.getpid()))\n"
                    "if depth:\n"
                    "    subprocess.Popen([sys.executable, __file__, str(root), str(depth - 1)])\n"
                    "else:\n"
                    "    (root / 'descendants-ready').touch()\n"
                    "while not (root / 'handles-open').exists(): time.sleep(0.02)\n"
                    "print('TREE-READY', flush=True)\n"
                    "time.sleep(60)\n"
                )
                tree = tmp_root / "tree.py"
                tree.write_text(tree_source, encoding="utf-8")

                def monitor() -> None:
                    deadline = time.monotonic() + 8
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    try:
                        for depth in range(3):
                            pid = int((tmp_root / f"pid-{depth}").read_text(encoding="utf-8"))
                            handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                            if not handle:
                                raise ctypes.WinError(ctypes.get_last_error())
                            handles.append(handle)
                        release.touch()
                    except Exception as exc:
                        monitor_errors.append(str(exc))

                unrelated = subprocess.Popen([sys.executable, "-I", "-S", "-c",
                                               "import time; time.sleep(60)"])
                watcher = threading.Thread(target=monitor)
                watcher.start()
                try:
                    started = time.monotonic()
                    tree_rec = run_oracle(
                        f'"{sys.executable}" "{tree}" "{tmp_root}" 2',
                        tmp_root, "TREE-READY", None, 10)
                    watcher.join()
                    check("timeout kills children and grandchildren",
                          not monitor_errors and len(handles) == 3
                          and all(api.WaitForSingleObject(h, 5000) == 0 for h in handles)
                          and tree_rec["timed_out"] and not tree_rec["success"]
                          and tree_rec["expect"]["matched"] and time.monotonic() - started < 20,
                          str(monitor_errors))
                    check("timeout leaves unrelated owned control alive", unrelated.poll() is None)
                finally:
                    watcher.join()
                    unrelated.kill()
                    unrelated.wait(timeout=10)
                    for handle in handles:
                        api.CloseHandle(handle)

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
         encoding="utf-8")
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
        (tree_repo / "a").write_text("1", encoding="utf-8")
        # Stage a real index entry BEFORE calling run_oracle, so the before/after
        # `git ls-files --stage` comparison below can catch the temp index leaking into
        # the real one — a bug that would otherwise pass silently.
        subprocess.run(["git", "add", "a"], cwd=str(tree_repo), check=True)
        stage_before = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        , encoding="utf-8").stdout

        rec_tree = run_oracle("echo tree-test", tree_repo, None, None, 30)

        stage_after = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        , encoding="utf-8").stdout
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
        (leak_repo / "tracked").write_text("1", encoding="utf-8")
        subprocess.run(["git", "add", "tracked"], cwd=str(leak_repo), check=True)
        leak_before = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(leak_repo), capture_output=True, text=True, check=True
        , encoding="utf-8").stdout
        (leak_repo / "untracked").write_text("2", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(leak_repo), check=True)  # deliberately unisolated
        leak_after = subprocess.run(
            ["git", "ls-files", "--stage"], cwd=str(leak_repo), capture_output=True, text=True, check=True
        , encoding="utf-8").stdout
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
        , encoding="utf-8").stdout.strip()
        check(
            "tree_sha equals git rev-parse HEAD^{tree} for a working tree committed unchanged",
            bool(rec_tree["tree_sha"]) and rec_tree["tree_sha"] == t1,
            f"tree_sha={rec_tree['tree_sha']} t1={t1}",
        )

        (tree_repo / "a").write_text("2", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(tree_repo), check=True)
        subprocess.run(["git", "commit", "-q", "-m", "two"], cwd=str(tree_repo), check=True)
        t2 = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=str(tree_repo), capture_output=True, text=True, check=True
        , encoding="utf-8").stdout.strip()
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
    ap.add_argument("command", nargs="?", default=None,
                    help="one shell string: sh -c on POSIX, cmd.exe /d /s /c on Windows")
    ap.add_argument("--cwd", type=Path, default=None, help="working directory (default: current directory)")
    ap.add_argument("--expect", default=None, help="substring the combined stdout+stderr must contain")
    ap.add_argument("--expect-re", default=None, help="regex the combined stdout+stderr must match")
    ap.add_argument("--timeout", type=float, default=600,
                    help="seconds before the child process group or Windows job is killed (default: 600)")
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
