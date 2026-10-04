"""Behavioral regressions for audit F01–F04 and F07; fixtures never edit the repo.

Set AUDIT_SOURCE_ROOT to run the same negative controls against a prior snapshot.
"""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load("audit_release_fix", "scripts/hooks/precommit_gate.py")
reports = load("audit_report_fix", "skills/karta-verify/scripts/check_gate_report.py")
runner = load("audit_runner_fix", "benchmarks/gate/run_gate.py")
writer = load("audit_writer_fix", "hooks/scripts/guard_writer_confinement.py")
oracle = load("audit_oracle_fix", "skills/karta-build/scripts/run_oracle.py")


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-priority-fix-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Regression fixture")
        self.git("config", "core.autocrlf", "false")
        self.write(".claude-plugin/plugin.json", json.dumps({"version": "1.0.0"}))
        self.write("source.txt", "good\n")
        self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, encoding="utf-8").strip()

    def git_result(self, args):
        p = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout

    def write(self, path, content):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def symlink(self, path, target, directory=False):
        try:
            (self.root / path).symlink_to(target, target_is_directory=directory)
        except OSError as e:
            self.skipTest(f"symlink creation unavailable: {e}")


class ReleaseEvidence(RepoCase):
    gate_path = "benchmarks/results/gate/2026-09-22-gate.json"

    def setUp(self):
        super().setUp()
        # F17: a release result is judged against the committed spec and inventory.
        self.write("benchmarks/bench-spec.json", json.dumps({"vectors": [{"id": "demo"}]}))
        self.write("benchmarks/gate/release-required.json", json.dumps(
            {"schema_version": 1, "vectors": [{"id": "demo", "required": True}]}))
        self.commit()

    def evidence(self, fail=0):
        self.write(".claude-plugin/plugin.json", json.dumps({"version": "1.0.1"}))
        status = "FAIL" if fail else "PASS"
        result = {"schema_version": 2, "run_date": "2026-09-22", "strict": False,
                  "only": None, "plugin_version": "1.0.1",
                  "karta_sha": self.git("rev-parse", "HEAD"),
                  "vectors": [{"id": "demo", "status": status, "partial": False,
                               "implemented_checks": [], "findings_count": 0, "findings": [],
                               "known_open_count": None if fail else 0, "metrics": {}, "detail": ""}],
                  "summary": {"total": 1, "pass": 0 if fail else 1, "fail": fail, "error": 0,
                              "skipped": 0, "known_open": 0,
                              "regression_health": "red" if fail else "green"}}
        # The old snapshot lacks content binding; still exercise its original
        # byte-selection failures with the same fixtures.
        helper = ROOT / "scripts/release_evidence.py"
        if helper.is_file():
            evidence = load("audit_evidence_fix", "scripts/release_evidence.py")
            result["source_sha256"] = evidence.working_fingerprint(self.root)
            result["source_stable"] = True
        self.write(self.gate_path, json.dumps(result))
        return result

    def decision(self, command="git commit -m release"):
        return release._release_block(command, self.git_result, self.root)

    def test_red_index_cannot_borrow_green_worktree(self):
        data = self.evidence(fail=1)
        self.git("add", "-A")
        data["summary"]["fail"] = 0
        self.write(self.gate_path, json.dumps(data))
        self.assertIsNotNone(self.decision())

    def test_green_index_ignores_unstaged_red_worktree(self):
        data = self.evidence()
        self.git("add", "-A")
        data["summary"]["fail"] = 1
        self.write(self.gate_path, json.dumps(data))
        self.assertIsNone(self.decision())

    def test_pathspec_excluding_evidence_is_denied(self):
        self.evidence()
        self.git("add", "-A")
        self.assertIsNotNone(self.decision("git commit -m release -- .claude-plugin/plugin.json"))

    def test_message_looking_like_option_does_not_change_selection(self):
        self.evidence()
        self.git("add", "-A")
        self.assertIsNotNone(self.decision("git commit -m '-am' -- .claude-plugin/plugin.json"))

    def test_pathspec_including_both_is_allowed(self):
        self.evidence()
        self.git("add", "-A")
        self.assertIsNone(self.decision("git commit -m release -- .claude-plugin/plugin.json " + self.gate_path))

    def test_include_keeps_staged_evidence(self):
        self.evidence()
        self.git("add", "-A")
        self.assertIsNone(self.decision("git commit -m release --include .claude-plugin/plugin.json"))

    def test_all_does_not_include_untracked_evidence(self):
        self.evidence()
        self.assertIsNotNone(self.decision("git commit -am release"))

    def test_all_includes_newly_staged_evidence(self):
        self.evidence()
        self.git("add", self.gate_path)
        self.assertIsNone(self.decision("git commit -am release"))

    def test_staged_evidence_survives_worktree_deletion(self):
        self.evidence()
        self.git("add", "-A")
        (self.root / self.gate_path).unlink()
        self.assertIsNone(self.decision())

    def test_staged_source_changed_after_run_is_denied(self):
        self.evidence()
        self.write("source.txt", "untested\n")
        self.git("add", "-A")
        self.assertIsNotNone(self.decision())

    def test_unstaged_source_changed_after_run_does_not_taint_index(self):
        self.evidence()
        self.git("add", "-A")
        self.write("source.txt", "not part of commit\n")
        self.assertIsNone(self.decision())

    def test_crlf_normalization_matches_committed_content(self):
        self.git("config", "core.autocrlf", "true")
        (self.root / "source.txt").write_bytes(b"good\r\n")
        self.evidence()
        self.git("add", "-A")
        self.assertIsNone(self.decision())

    def test_missing_source_identity_cannot_authorize_release(self):
        data = self.evidence()
        data.pop("source_sha256", None)
        self.write(self.gate_path, json.dumps(data))
        self.git("add", "-A")
        self.assertIsNotNone(self.decision())

    def test_worktree_result_link_cannot_authorize_release(self):
        self.evidence()
        self.git("add", "-A")
        (self.root / self.gate_path).rename(self.root / "outside.json")
        self.symlink(self.gate_path, self.root / "outside.json")
        self.assertIsNotNone(self.decision("git commit -am release"))


class ReportEvidence(unittest.TestCase):
    binder = {"sme": ["minimalism"], "work_items": [{"id": "w1", "oracle": {"assertions": ["It works"]}}]}

    def report(self, verdict, status="ran", judged=1):
        resolved = "" if status == "blocked" else "minimalism"
        return (f"**Verdict:** {verdict}\n**Work item id:** w1\n**Diff range:** base..tip\n"
                f"**Diff SHA256:** {'a' * 64}\n"
                f"Stack-pack check: {status} — pinned: [minimalism]; resolved: [{resolved}]; items judged: {judged}\n")

    def test_blocked_review_cannot_pass(self):
        self.assertTrue(reports.check_report(self.report("PASS", "blocked", 0), "safety", "pass", self.binder, "w1"))

    def test_zero_judgments_cannot_pass(self):
        self.assertTrue(reports.check_report(self.report("PASS", judged=0), "safety", "pass", self.binder, "w1"))

    def test_blocked_review_has_valid_blocked_route(self):
        self.assertEqual([], reports.check_report(self.report("BLOCKED", "blocked", 0), "safety", "blocked", self.binder, "w1"))

    def test_bare_acceptance_cannot_pass(self):
        self.assertTrue(reports.check_report("**Verdict:** CONFORMANT", "acceptance", "pass", self.binder, "w1"))

    def test_missing_assertion_cannot_pass(self):
        self.assertTrue(reports.check_report(self.report("CONFORMANT"), "acceptance", "pass", self.binder, "w1"))

    def test_valid_acceptance_is_allowed(self):
        report = self.report("CONFORMANT") + ("**Assertion disposition:**\n"
                 "- assertion 0 — It works — inspection-verifiable — CONFORMS\n"
                 "**Contract conformance:**\n- n/a (no contract)\n")
        self.assertEqual([], reports.check_report(report, "acceptance", "pass", self.binder, "w1"))

    def test_passing_report_cannot_omit_resolved_rule(self):
        rules = [{"id": "min.1", "text": "Keep it simple", "source": "minimalism"}]
        self.assertTrue(reports.check_report(self.report("PASS"), "safety", "pass", self.binder, "w1",
                                            expected_rules=rules))
        report = self.report("PASS") + "- rule min.1 — CONFORMS\n"
        self.assertEqual([], reports.check_report(report, "safety", "pass", self.binder, "w1",
                                                  expected_rules=rules))

    def test_disposition_cannot_substitute_a_different_assertion(self):
        report = self.report("CONFORMANT") + ("- assertion 0 — Something else — inspection-verifiable — CONFORMS\n"
                 "**Contract conformance:**\n- n/a (no contract)\n")
        self.assertTrue(reports.check_report(report, "acceptance", "pass", self.binder, "w1"))


class ReportCurrency(RepoCase):
    def test_same_range_with_changed_bytes_rejects_old_report(self):
        self.write("source.txt", "first change\n")
        digest = reports.diff_digest(self.root, "HEAD")
        report = ("**Verdict:** PASS\n**Work item id:** w1\n**Diff range:** HEAD\n"
                  f"**Diff SHA256:** {digest}\n"
                  "Stack-pack check: skipped — pinned: []; resolved: []; items judged: 0\n")
        binder = {"work_items": [{"id": "w1"}]}
        self.assertEqual([], reports.check_report(report, "safety", "pass", binder, "w1", "HEAD", digest))
        self.write("source.txt", "second change\n")
        self.assertTrue(reports.check_report(report, "safety", "pass", binder, "w1", "HEAD",
                                             reports.diff_digest(self.root, "HEAD")))


class ProbeExit(unittest.TestCase):
    def test_valid_json_cannot_mask_process_crash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            data = {"id": "demo", "status": "pass", "partial": False,
                    "implemented_checks": [], "findings": [], "metrics": {}}
            (p / "demo.py").write_text(f"print({json.dumps(data)!r})\nraise SystemExit(7)\n", encoding="utf-8")
            with patch.object(runner, "PROBES", p):
                result = runner._run_probe("demo")
            self.assertEqual("ERROR", result["status"])
            self.assertIn("7", result["detail"])


class ReleaseProducer(RepoCase):
    def run_fixture(self, mutate):
        spec = self.root / "benchmarks/bench-spec.json"
        self.write(str(spec.relative_to(self.root)), json.dumps({"vectors": [{"id": "demo"}]}))
        results = self.root / "benchmarks/results/gate"

        def probe(*args):
            if mutate:
                self.write("source.txt", "changed during benchmark\n")
            return {"id": "demo", "status": "PASS", "partial": False,
                    "implemented_checks": ["fixture"], "findings_count": 0,
                    "findings": [], "metrics": {}, "detail": ""}

        with patch.object(runner, "ROOT", self.root), patch.object(runner, "SPEC", spec), \
                patch.object(runner, "RESULTS", results), patch.object(runner, "_run_probe", probe), \
                patch.object(sys, "argv", ["run_gate.py", "--date", "2026-09-22"]), \
                contextlib.redirect_stdout(io.StringIO()):
            result = runner.main()
        data = json.loads((results / "2026-09-22-gate.json").read_text(encoding="utf-8"))
        return result, data

    def test_generated_result_does_not_invalidate_stable_source(self):
        result, data = self.run_fixture(False)
        self.assertEqual(0, result)
        self.assertTrue(data["source_stable"])
        self.assertEqual(64, len(data["source_sha256"]))

    def test_source_mutation_invalidates_otherwise_passing_run(self):
        result, data = self.run_fixture(True)
        self.assertEqual(1, result)
        self.assertFalse(data["source_stable"])


class WriterBoundaries(RepoCase):
    def decision(self, path, tool="Edit"):
        ti = {"command": f"echo changed > {shlex.quote(str(self.root / path))}"} if tool == "Bash" else {"file_path": str(self.root / path)}
        return writer.decide({"agent_type": "karta-doc-gardner", "tool_name": tool,
                              "cwd": str(self.root), "tool_input": ti})[0]

    def test_symlink_to_code_is_denied_in_file_and_shell_tools(self):
        (self.root / "docs").mkdir()
        self.symlink("docs/code.md", self.root / "source.txt")
        for tool in ("Edit", "Bash"):
            with self.subTest(tool=tool):
                self.assertEqual(2, self.decision("docs/code.md", tool))

    def test_symlink_parent_cannot_create_code(self):
        (self.root / "src").mkdir()
        self.symlink("docs", self.root / "src", directory=True)
        self.assertEqual(2, self.decision("docs/new.py"))

    def test_external_docs_directory_is_denied(self):
        with tempfile.TemporaryDirectory() as external:
            target = Path(external) / "docs"
            target.mkdir()
            self.symlink("docs", target, directory=True)
            self.assertEqual(2, self.decision("docs/new.md"))

    def test_alias_within_docs_is_allowed(self):
        self.write("docs/real.md", "docs")
        self.symlink("docs/alias.md", self.root / "docs/real.md")
        self.assertEqual(0, self.decision("docs/alias.md"))


class OracleIdentity(RepoCase):
    def test_mutating_command_cannot_certify_new_tree(self):
        before = self.git("rev-parse", "HEAD^{tree}")
        script = "from pathlib import Path; p=Path('source.txt'); assert p.read_text()=='good\\n'; p.write_text('bad\\n')"
        args = [sys.executable, "-c", script]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        record = oracle.run_oracle(command, self.root, None, None, 10)
        self.assertFalse(record["success"])
        self.assertEqual(0, record["exit_status"])
        self.assertEqual(before, record["tree_before_sha"])
        self.assertTrue(record["tree_changed"])
        self.commit()
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}"), record["tree_sha"])

    def test_stable_command_can_certify_tree(self):
        record = oracle.run_oracle("echo ok", self.root, "ok", None, 10)
        self.assertTrue(record["success"])
        self.assertFalse(record["tree_changed"])
        self.assertEqual(record["tree_before_sha"], record["tree_sha"])


if __name__ == "__main__":
    unittest.main()
