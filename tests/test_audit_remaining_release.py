"""Behavioral regressions for audit F17 (release coverage) and F22 (install prerequisites).

Fixtures are real temporary Git repositories and real subprocesses; nothing edits
the checkout. Set AUDIT_SOURCE_ROOT to run the same checks against another snapshot.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
INVENTORY_REL = "benchmarks/gate/release-required.json"
SPEC_REL = "benchmarks/bench-spec.json"
GATE_REL = "benchmarks/results/gate/2026-09-22-gate.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = load("audit_release_cov", "scripts/hooks/precommit_gate.py")
runner = load("audit_runner_cov", "benchmarks/gate/run_gate.py")
evidence = load("audit_evidence_cov", "scripts/release_evidence.py")


# --- F17: the committed inventory -------------------------------------------------

class Inventory(unittest.TestCase):
    def setUp(self):
        self.spec_ids = [v["id"] for v in json.loads((ROOT / SPEC_REL).read_text(encoding="utf-8"))["vectors"]]
        self.inventory = json.loads((ROOT / INVENTORY_REL).read_text(encoding="utf-8"))

    def test_every_vector_has_an_explicit_valid_decision(self):
        self.assertEqual([], evidence.inventory_problems(self.inventory, self.spec_ids))
        self.assertEqual(sorted(self.spec_ids), sorted(v["id"] for v in self.inventory["vectors"]))

    def test_vectors_without_a_probe_are_not_claimed_covered(self):
        for entry in self.inventory["vectors"]:
            if not (ROOT / "benchmarks/probes" / f"{entry['id']}.py").is_file():
                self.assertFalse(entry["required"], entry["id"])
                self.assertEqual("unimplemented", entry["not_required"], entry["id"])

    def test_deferred_prompt_injection_phase_is_a_recorded_partial(self):
        entry = {v["id"]: v for v in self.inventory["vectors"]}["sec-untrusted-input-surfaces"]
        self.assertTrue(entry["partial_allowed"])
        self.assertIn("prompt-injection", entry["partial_reason"])

    def test_decision_without_reason_is_rejected(self):
        broken = json.loads(json.dumps(self.inventory))
        for entry in broken["vectors"]:
            if not entry["required"]:
                entry["reason"] = ""
                victim = entry["id"]
                break
        problems = evidence.inventory_problems(broken, self.spec_ids)
        self.assertTrue(any(victim in p for p in problems), problems)

    def test_unjustified_partial_allowance_is_rejected(self):
        broken = json.loads(json.dumps(self.inventory))
        entry = next(e for e in broken["vectors"] if e["required"])
        entry["partial_allowed"] = True
        entry.pop("partial_reason", None)
        problems = evidence.inventory_problems(broken, self.spec_ids)
        self.assertTrue(any(entry["id"] in p for p in problems), problems)

    def test_vector_missing_from_inventory_is_named(self):
        broken = json.loads(json.dumps(self.inventory))
        dropped = broken["vectors"].pop()
        problems = evidence.inventory_problems(broken, self.spec_ids)
        self.assertTrue(any(dropped["id"] in p for p in problems), problems)


# --- F17: runner output and the release gate, on real temporary repositories -------

FIXTURE_SPEC = {"vectors": [{"id": v} for v in ("alpha", "beta", "gamma", "delta")]}
FIXTURE_INVENTORY = {"schema_version": 1, "vectors": [
    {"id": "alpha", "required": True, "partial_allowed": False},
    {"id": "beta", "required": True, "partial_allowed": True,
     "partial_reason": "live phase deferred; static half gated"},
    {"id": "gamma", "required": False, "not_required": "unimplemented", "reason": "no probe yet"},
    {"id": "delta", "required": False, "not_required": "needs-consumer",
     "reason": "needs enrolled consumer repositories", "needs_consumers": True},
]}


def row(vid, status="PASS", partial=False, findings=0):
    found = [{"finding_id": f"{vid}-{i}", "severity": "info", "summary": "known"} for i in range(findings)]
    if status in ("SKIPPED", "ERROR"):
        partial = None
    return {"id": vid, "status": status, "partial": partial,
            "implemented_checks": ["fixture"] if status in ("PASS", "FAIL") else [],
            "findings_count": len(found), "findings": found, "metrics": {}, "detail": ""}


class ReleaseRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-release-cov-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Regression fixture")
        self.git("config", "core.autocrlf", "false")
        self.write(".claude-plugin/plugin.json", json.dumps({"version": "1.0.0"}))
        self.write("source.txt", "good\n")
        self.write(SPEC_REL, json.dumps(FIXTURE_SPEC))
        self.write(INVENTORY_REL, json.dumps(FIXTURE_INVENTORY))
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True, encoding="utf-8").strip()

    def git_result(self, args):
        p = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout

    def write(self, path, content):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def run_runner(self, rows, argv=()):
        """Produce the result with the real runner; only the probe subprocess is replaced."""
        self.write(".claude-plugin/plugin.json", json.dumps({"version": "1.0.1"}))
        by_id = {r["id"]: r for r in rows}
        calls = []

        def probe(vid, *args):
            calls.append(vid)
            return json.loads(json.dumps(by_id.get(vid) or dict(row(vid, "SKIPPED"), detail="no probe yet")))

        with patch.object(runner, "ROOT", self.root), \
                patch.object(runner, "SPEC", self.root / SPEC_REL), \
                patch.object(runner, "RESULTS", self.root / "benchmarks/results/gate"), \
                patch.object(runner, "_run_probe", probe), \
                patch.object(sys, "argv", ["run_gate.py", "--date", "2026-09-22", *argv]), \
                patch.dict(os.environ, {runner.CONSUMERS_ENV: ""}), \
                contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop(runner.CONSUMERS_ENV, None)
            code = runner.main()
        doc = json.loads((self.root / GATE_REL).read_text(encoding="utf-8"))
        return code, doc, calls

    def decision(self, command="git commit -m release"):
        return release._release_block(command, self.git_result, self.root)

    def rewrite_result(self, mutate):
        doc = json.loads((self.root / GATE_REL).read_text(encoding="utf-8"))
        mutate(doc)
        self.write(GATE_REL, json.dumps(doc))


class RunnerResult(ReleaseRepo):
    def test_result_separates_known_open_from_regression_health(self):
        code, doc, _ = self.run_runner([row("alpha", findings=3), row("beta", partial=True)])
        self.assertEqual(0, code)
        alpha = {r["id"]: r for r in doc["vectors"]}["alpha"]
        self.assertEqual(3, alpha["known_open_count"])
        self.assertEqual(3, doc["summary"]["known_open"])
        self.assertEqual("green", doc["summary"]["regression_health"])
        self.assertEqual([], evidence.result_problems(doc))

    def test_consumer_vector_is_skipped_explicitly_without_consumers(self):
        _, doc, calls = self.run_runner([row("alpha"), row("beta")])
        self.assertNotIn("delta", calls)
        delta = {r["id"]: r for r in doc["vectors"]}["delta"]
        self.assertEqual("SKIPPED", delta["status"])
        self.assertIn("consumer", delta["detail"])

    def test_result_records_coverage_decision(self):
        _, doc, _ = self.run_runner([row("alpha"), row("beta", status="FAIL")])
        self.assertFalse(doc["coverage"]["complete"])
        self.assertTrue(any("beta" in p for p in doc["coverage"]["problems"]))

    def test_release_flag_refuses_incomplete_coverage(self):
        code, _, _ = self.run_runner([row("alpha"), row("beta")], argv=["--release"])
        self.assertEqual(0, code)
        (self.root / GATE_REL).unlink()
        code, _, _ = self.run_runner([row("alpha", partial=True), row("beta")], argv=["--release"])
        self.assertEqual(1, code)

    def test_structure_validator_rejects_a_lying_summary(self):
        _, doc, _ = self.run_runner([row("alpha"), row("beta", status="FAIL")])
        doc["summary"]["fail"] = 0
        doc["summary"]["regression_health"] = "green"
        self.assertTrue(evidence.result_problems(doc))

    def test_structure_validator_rejects_miscounted_findings(self):
        _, doc, _ = self.run_runner([row("alpha", findings=2), row("beta")])
        doc["vectors"][0]["findings_count"] = 0
        self.assertTrue(evidence.result_problems(doc))


class ReleaseCoverageGate(ReleaseRepo):
    def test_positive_control_complete_coverage_allows(self):
        self.run_runner([row("alpha", findings=2), row("beta", partial=True)])
        self.git("add", "-A")
        self.assertIsNone(self.decision())

    def test_missing_required_vector_is_refused_by_name(self):
        self.run_runner([row("alpha"), row("beta")])
        def drop_alpha(doc):
            doc["vectors"] = [r for r in doc["vectors"] if r["id"] != "alpha"]
            doc["summary"]["total"] -= 1
            doc["summary"]["pass"] -= 1
        self.rewrite_result(drop_alpha)
        self.git("add", "-A")
        reason = self.decision()
        self.assertIsNotNone(reason)
        self.assertIn("alpha", reason)

    def test_skipped_required_vector_is_refused(self):
        self.run_runner([row("alpha", status="SKIPPED"), row("beta")])
        self.git("add", "-A")
        reason = self.decision()
        self.assertIsNotNone(reason)
        self.assertIn("alpha", reason)

    def test_unjustified_partial_is_refused(self):
        self.run_runner([row("alpha", partial=True), row("beta")])
        self.git("add", "-A")
        reason = self.decision()
        self.assertIsNotNone(reason)
        self.assertIn("alpha", reason)

    def test_summary_that_disagrees_with_rows_is_refused(self):
        self.run_runner([row("alpha"), row("beta")])
        self.rewrite_result(lambda d: d["vectors"].__setitem__(0, row("alpha", status="FAIL")))
        self.git("add", "-A")
        self.assertIsNotNone(self.decision())

    def test_inventory_absent_from_commit_is_refused(self):
        self.run_runner([row("alpha"), row("beta")])
        self.git("add", "-A")
        self.git("rm", "-q", "--cached", INVENTORY_REL)
        reason = self.decision()
        self.assertIsNotNone(reason)
        self.assertIn(INVENTORY_REL, reason)

    def test_inventory_relaxed_after_the_run_is_refused(self):
        self.run_runner([row("alpha"), row("beta", status="SKIPPED")])
        relaxed = json.loads(json.dumps(FIXTURE_INVENTORY))
        relaxed["vectors"][1] = {"id": "beta", "required": False, "not_required": "unimplemented",
                                 "reason": "relaxed after the fact"}
        self.write(INVENTORY_REL, json.dumps(relaxed))
        self.git("add", "-A")
        self.assertIsNotNone(self.decision())

    def test_inventory_without_decision_for_spec_vector_is_refused(self):
        self.run_runner([row("alpha"), row("beta")])
        spec = json.loads(json.dumps(FIXTURE_SPEC))
        spec["vectors"].append({"id": "epsilon"})
        self.write(SPEC_REL, json.dumps(spec))
        self.git("add", "-A")
        reason = self.decision()
        self.assertIsNotNone(reason)
        self.assertIn("epsilon", reason)


# --- F22: install smoke and README prerequisites --------------------------------------

SMOKE = ROOT / "scripts/install_smoke.py"


def smoke(*args, env=None):
    return subprocess.run([sys.executable, str(SMOKE), *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=600)


def plugin_copy(dest: Path) -> Path:
    for rel in ("hooks", ".codex-plugin", ".claude-plugin"):
        shutil.copytree(ROOT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


@unittest.skipIf(os.name == "nt", "the POSIX hook launchers are exercised; Windows launchers are not")
class InstallSmoke(unittest.TestCase):
    def test_real_checkout_passes(self):
        p = smoke("--plugin-root", str(ROOT), "--json")
        self.assertEqual(0, p.returncode, p.stdout + p.stderr)
        report = json.loads(p.stdout)
        names = [c["name"] for c in report["checks"]]
        self.assertTrue(any("uv" in n for n in names))
        self.assertTrue(any("guard_binder_immutability" in n and "codex" in n for n in names))
        self.assertTrue(any("guard_delivery_stop" in n and "claude" in n for n in names))
        self.assertTrue(all(c["ok"] for c in report["checks"]))

    def test_manifest_pointing_at_missing_script_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = plugin_copy(Path(td))
            (root / "hooks/scripts/guard_pack_write.py").unlink()
            p = smoke("--plugin-root", str(root))
            self.assertNotEqual(0, p.returncode)
            self.assertIn("guard_pack_write.py", p.stdout)

    def test_codex_missing_script_is_caught_despite_fail_open_launcher(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = plugin_copy(Path(td))
            (root / ".codex-plugin/hooks/scripts/guard_delivery_stop.py").unlink()
            p = smoke("--plugin-root", str(root))
            self.assertNotEqual(0, p.returncode)
            self.assertIn("guard_delivery_stop.py", p.stdout)

    def test_guard_failing_on_benign_payload_fails(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = plugin_copy(Path(td))
            (root / "hooks/scripts/guard_subagent_whiff.py").write_text(
                "# /// script\n# requires-python = \">=3.11\"\n# dependencies = []\n# ///\n"
                "raise SystemExit(2)\n", encoding="utf-8")
            p = smoke("--plugin-root", str(root))
            self.assertNotEqual(0, p.returncode)
            self.assertIn("guard_subagent_whiff.py", p.stdout)

    def test_missing_uv_is_reported(self):
        python_dir = str(Path(sys.executable).parent)
        git = shutil.which("git")
        uv = shutil.which("uv")
        if uv and Path(uv).parent in {Path(python_dir), Path(git).parent if git else None}:
            self.skipTest("uv shares a directory with python/git; cannot hide it via PATH")
        path = os.pathsep.join(dict.fromkeys([python_dir, str(Path(git).parent) if git else "", "/bin"]))
        env = {"PATH": path, "HOME": tempfile.gettempdir()}
        p = smoke("--plugin-root", str(ROOT), env=env)
        self.assertNotEqual(0, p.returncode)
        self.assertIn("uv", p.stdout)


class ReadmePrerequisites(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_common_prerequisites_come_before_task_specific(self):
        common = self.text.find("Python 3.11")
        task = self.text.find("**`karta-validate`** —")
        self.assertNotEqual(-1, common)
        self.assertNotEqual(-1, task)
        self.assertLess(common, task)
        for needle in ("`git`", "`uv`", "`/hooks`", "disableAllHooks", "scripts/install_smoke.py"):
            self.assertIn(needle, self.text[:task], needle)

    def test_smoke_limit_is_stated(self):
        self.assertIn("does not prove", self.text)


if __name__ == "__main__":
    unittest.main()
