"""Behavioral regressions for audit F15: oracle output encoding and memory bounds.

Every case runs a real shell command through run_oracle; nothing is mocked.
Set AUDIT_SOURCE_ROOT to run the same cases against a prior snapshot.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
SCRIPT = ROOT / "skills/karta-build/scripts/run_oracle.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


oracle = load("audit_remaining_oracle", SCRIPT)


def py(source: str) -> str:
    """A shell string running `source` in this interpreter, on either platform."""
    args = [sys.executable, "-c", source]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


class OracleCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-oracle-fix-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_oracle(self, command, expect=None, expect_re=None, timeout=60):
        return oracle.run_oracle(command, self.root, expect, expect_re, timeout)


class NonUtf8Output(OracleCase):
    """F15 / oracle-non-utf8-output: bytes that are not UTF-8 still yield evidence."""

    INVALID = py("import sys; sys.stdout.buffer.write(b'head-ok \\xff\\xfe mid \\xc3 TAIL-OK\\n'); sys.exit(4)")

    def test_invalid_bytes_produce_record_with_exit_status(self):
        record = self.run_oracle(self.INVALID)
        self.assertEqual(4, record["exit_status"])
        self.assertFalse(record["success"])
        out = record["decisive_output"]
        self.assertTrue(out["lossy"])
        self.assertEqual("utf-8", out["encoding"])
        self.assertEqual("replace", out["errors"])
        self.assertIn("head-ok", out["head"])
        self.assertIn("TAIL-OK", out["head"])
        self.assertIn("�", out["head"])
        self.assertEqual(len(b"head-ok \xff\xfe mid \xc3 TAIL-OK\n"), out["total_bytes"])
        json.dumps(record)  # the record must serialize as the evidence blob does

    def test_decode_failure_is_named_in_the_record(self):
        # The lossy flag alone says a replacement happened; the record also
        # names the first decode failure so a reader can find it.
        out = self.run_oracle(self.INVALID)["decisive_output"]
        self.assertIn("invalid start byte", out["decode_error"])
        self.assertIn("byte 8", out["decode_error"])
        truncated = self.run_oracle(py("import sys; sys.stdout.buffer.write(b'ok \\xc3')"))
        self.assertIn("unexpected end of data", truncated["decisive_output"]["decode_error"])
        clean = self.run_oracle(py("print('fine')"))["decisive_output"]
        self.assertIsNone(clean["decode_error"])

    def test_lossy_output_still_judged_by_exit_and_expect(self):
        cmd = py("import sys; sys.stdout.buffer.write(b'\\xff PASS-MARK\\n')")
        record = self.run_oracle(cmd, expect="PASS-MARK")
        self.assertTrue(record["success"])
        self.assertTrue(record["decisive_output"]["lossy"])
        miss = self.run_oracle(cmd, expect="ABSENT")
        self.assertFalse(miss["success"])

    def test_valid_utf8_is_not_lossy(self):
        # Positive control: multi-byte text survives intact and is not flagged.
        cmd = py("import sys; sys.stdout.buffer.write('caf\\u00e9 \\u2713\\n'.encode('utf-8'))")
        record = self.run_oracle(cmd, expect="café ✓")
        self.assertTrue(record["success"])
        self.assertFalse(record["decisive_output"]["lossy"])
        self.assertIn("café ✓", record["decisive_output"]["head"])

    def test_cli_emits_structured_record_not_a_crash(self):
        cli = subprocess.run(
            [sys.executable, str(SCRIPT), "--cwd", str(self.root), self.INVALID],
            capture_output=True, timeout=60)
        self.assertEqual(1, cli.returncode, cli.stderr.decode("utf-8", "replace"))
        record = json.loads(cli.stdout.decode("utf-8"))
        self.assertEqual(4, record["exit_status"])
        self.assertTrue(record["decisive_output"]["lossy"])

    def test_timeout_with_invalid_partial_output_is_evidence(self):
        cmd = py("import sys, time; sys.stdout.buffer.write(b'\\xff partial\\n'); "
                 "sys.stdout.flush(); time.sleep(30)")
        record = self.run_oracle(cmd, timeout=2)
        self.assertTrue(record["timed_out"])
        self.assertFalse(record["success"])
        self.assertIn("partial", record["decisive_output"]["head"])
        self.assertTrue(record["decisive_output"]["lossy"])


class BoundedCapture(OracleCase):
    """Output is drained completely, but only a capped head and tail are retained."""

    TOTAL = 300 * 1024 * 1024

    def test_huge_output_keeps_memory_bounded(self):
        # Measure the peak RSS of a fresh interpreter that runs one oracle
        # emitting 300 MiB. Collecting everything costs several copies of that.
        probe = (
            "import importlib.util, json, resource, sys\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('o', {str(SCRIPT)!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            f"cmd = 'head -c {self.TOTAL} /dev/zero | tr \"\\\\0\" x; echo; echo END-MARK'\n"
            f"rec = m.run_oracle(cmd, Path({str(self.root)!r}), 'END-MARK', None, 120)\n"
            "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
            "print(json.dumps({'grew_kib': after - before, 'record': rec}))\n"
        )
        if os.name == "nt":
            self.skipTest("resource.getrusage is POSIX-only")
        proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=300)
        self.assertEqual(0, proc.returncode, proc.stderr.decode("utf-8", "replace"))
        result = json.loads(proc.stdout)
        record = result["record"]
        out = record["decisive_output"]
        self.assertTrue(record["success"])
        self.assertEqual(self.TOTAL + len(b"\nEND-MARK\n"), out["total_bytes"])
        self.assertTrue(out["stream_truncated"])
        self.assertIn("END-MARK", out["tail"])
        self.assertLessEqual(len(out["head"].encode("utf-8")) + len(out["tail"].encode("utf-8")),
                             oracle.MAX_DECISIVE_BYTES)
        # 64 MiB of growth is far below one copy of the stream.
        self.assertLess(result["grew_kib"], 64 * 1024, result["grew_kib"])

    def test_expect_substring_matches_beyond_retained_window(self):
        # The marker sits in the middle of the stream, outside the retained
        # head and tail; the streaming substring match must still see it.
        # One line: cmd.exe ends the command at the first newline.
        cmd = py("import sys; w = sys.stdout.write; "
                 "w('a' * (8 * 1024 * 1024)); w('MID-MARK'); w('b' * (8 * 1024 * 1024))")
        record = self.run_oracle(cmd, expect="MID-MARK")
        self.assertTrue(record["decisive_output"]["stream_truncated"])
        self.assertTrue(record["expect"]["matched"])
        self.assertTrue(record["success"])
        # Negative control: an absent marker in the same stream does not match.
        self.assertFalse(self.run_oracle(cmd, expect="NOT-THERE")["success"])

    def test_expect_substring_across_chunk_boundary(self):
        # Chunk-boundary positions around a 64 KiB read are the hard case.
        for offset in (65536 - 4, 65536 - 1, 65536, 65536 * 3 - 3):
            cmd = py(f"import sys; sys.stdout.write('z' * {offset} + 'SPLIT-MARK' + 'z' * 70000)")
            with self.subTest(offset=offset):
                self.assertTrue(self.run_oracle(cmd, expect="SPLIT-MARK")["expect"]["matched"])

    def test_small_output_not_truncated(self):
        record = self.run_oracle("echo small")
        out = record["decisive_output"]
        self.assertFalse(out["stream_truncated"])
        self.assertEqual("small", out["head"].strip())


def _alive(pid: int) -> bool:
    """True while `pid` runs; a zombie awaiting its reaper counts as gone."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


@unittest.skipIf(os.name == "nt" or not Path("/proc/self/stat").exists(), "POSIX process-group path with /proc")
class DescendantContainment(OracleCase):
    """F15 repair: a descendant must not outlive the run or turn a hang into a pass."""

    def spawn(self, detach: str, timeout: float):
        pidfile = Path(self.tmp.name + "-pid")
        self.addCleanup(lambda: pidfile.unlink(missing_ok=True))
        record = self.run_oracle(f"sleep 60 {detach}& echo $! > {shlex.quote(str(pidfile))}; echo started",
                                 timeout=timeout)
        pid = int(pidfile.read_text(encoding="utf-8"))
        self.addCleanup(lambda: _kill_quietly(pid))
        return record, pid

    def gone_soon(self, pid: int) -> bool:
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not _alive(pid):
                return True
            time.sleep(0.05)
        return False

    def test_descendant_holding_the_pipe_is_a_timeout_and_is_killed(self):
        import time
        start = time.monotonic()
        record, pid = self.spawn("", timeout=3)
        self.assertLess(time.monotonic() - start, 3 + 12)
        self.assertTrue(record["timed_out"])
        self.assertFalse(record["success"])
        self.assertTrue(self.gone_soon(pid), "descendant survived the run")

    def test_detached_descendant_is_reaped_without_failing_the_run(self):
        record, pid = self.spawn(">/dev/null 2>&1 ", timeout=10)
        self.assertFalse(record["timed_out"])
        self.assertTrue(record["success"])
        self.assertTrue(self.gone_soon(pid), "descendant survived the run")

    def test_plain_command_is_unaffected(self):
        record = self.run_oracle("echo fine", timeout=5)
        self.assertTrue(record["success"])
        self.assertFalse(record["timed_out"])


def _kill_quietly(pid: int) -> None:
    import signal
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
