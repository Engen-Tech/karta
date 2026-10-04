"""Regressions for audit F21: the enforcement inventory must match configuration.

Every expectation here is derived from the configuration and adapters on disk
(`.karta/roundtable.json`, `.codex/hooks.json`, `.codex/agents/`,
`.codex-plugin/hooks/hooks.json`, `hooks/hooks.json`), never from the prose it
checks. The flow probes run read-only: fixtures go to a temp directory and no
results file is written into the tree.

Set AUDIT_SOURCE_ROOT to run the same negative controls against a prior snapshot.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def register_entry(inv_id):
    """The text of one invariant-register entry, heading to the next heading."""
    text = (ROOT / "docs/conventions/invariants.md").read_text(encoding="utf-8")
    m = re.search(rf"^### {re.escape(inv_id)} .*?(?=^#{{2,3}} |\Z)", text, re.M | re.S)
    if not m:
        raise AssertionError(f"{inv_id} missing from the register")
    return m.group(0)


def status_line(entry):
    """The entry's status bullet: the first bullet carrying a bold status word."""
    for line in entry.splitlines():
        if line.startswith("- ") and re.search(r"\*\*(enforced|partial|prose|prose by design|disabled)\*\*", line):
            return line
    raise AssertionError("entry has no status line")


def codex_repo_hook_scripts():
    """Script basenames each .codex/hooks.json matcher launches, keyed by tool."""
    out = {}
    for groups in read_json(".codex/hooks.json")["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                for script in re.findall(r"([\w.-]+\.py)", hook.get("command", "")):
                    for tool in group.get("matcher", "").split("|"):
                        out.setdefault(script, set()).add(tool)
    return out


def codex_plugin_hook_scripts():
    out = {}
    data = read_json(".codex-plugin/hooks/hooks.json")
    for groups in data["hooks"].values():
        for group in groups:
            for hook in group.get("hooks", []):
                for script in re.findall(r"([\w.-]+\.py)", hook.get("command", "")):
                    for tool in group.get("matcher", "").split("|"):
                        out.setdefault(script, set()).add(tool)
    return out


def manifest_rows():
    return {r["id"]: r for r in read_json("benchmarks/flow/mutation-surface.json")["rows"]}


class RegisterMatchesConfig(unittest.TestCase):
    def test_review_gate_status_follows_the_roundtable_switch(self):
        enabled = read_json(".karta/roundtable.json").get("enabled", True)
        line = status_line(register_entry("INV-14"))
        if enabled:
            self.assertIn("**enforced**", line)
        else:
            self.assertNotIn("**enforced**", line,
                             "INV-14 claims enforcement while roundtable.json disables it")
            self.assertIn("disabled", line)

    def test_human_accept_is_not_called_enforced(self):
        # A model-facing prompt procedure and Git trailers cannot show that a
        # human answered; the provenance checker proves structure only.
        line = status_line(register_entry("INV-10"))
        self.assertNotIn("**enforced**", line)
        self.assertIn("instruction-level", line)
        self.assertIn("check_item_provenance.py", line)

    def test_read_only_reviewers_carry_a_level_per_host(self):
        entry = register_entry("INV-22")
        for needle in ("Claude Code", "hook-enforced", "Codex", "host-enforced",
                       "instruction-level", "Copilot", "untested"):
            self.assertIn(needle, entry, f"INV-22 lacks {needle!r}")

    def test_register_defines_the_level_vocabulary(self):
        head = (ROOT / "docs/conventions/invariants.md").read_text(encoding="utf-8").split("## Rule-authoring")[0]
        for word in ("host-enforced", "hook-enforced", "instruction-level", "disabled", "untested"):
            self.assertIn(f"**{word}**", head)

    def test_landing_gate_names_its_only_host(self):
        # roundtable_gate.py is wired in .claude/settings.json only.
        codex = codex_repo_hook_scripts()
        entry = register_entry("INV-18")
        if "roundtable_gate.py" not in codex:
            self.assertIn("Codex", entry)


class MutationSurfaceMatchesConfig(unittest.TestCase):
    def test_codex_commit_row_follows_the_codex_commit_adapter(self):
        row = manifest_rows()["git-commit:codex"]
        if "codex_precommit_gate.py" in codex_repo_hook_scripts():
            self.assertNotEqual(row["status"], "bypass",
                                "Codex routes shell commands to codex_precommit_gate.py")
            self.assertNotIn("no commit gate", row["note"])
            self.assertIn("codex_precommit_gate.py", row["note"])

    def test_codex_dispatch_rows_follow_the_bundled_inspectors(self):
        plugin = codex_plugin_hook_scripts()
        rows = manifest_rows()
        for row_id, guard in (("sme-dispatch:codex", "guard_auditor_dispatch.py"),
                              ("gate-dispatch:codex", "guard_gate_dispatch.py")):
            if guard in plugin and "spawn_agent" in plugin[guard]:
                self.assertNotEqual(rows[row_id]["status"], "n/a",
                                    f"{row_id}: Codex runs {guard} on spawn_agent")
                self.assertIn(guard, rows[row_id]["note"])

    def test_codex_writer_rows_do_not_deny_registered_writers(self):
        agents = {p.stem for p in (ROOT / ".codex/agents").glob("*.toml")}
        rows = manifest_rows()
        for row_id, agent in (("kaizen-write:codex", "karta-kaizen"),
                              ("docgardner-write:codex", "karta-doc-gardner")):
            if agent in agents:
                self.assertNotRegex(rows[row_id]["note"], r"registers no|no registered",
                                    f"{row_id}: .codex/agents/{agent}.toml exists")

    def test_codex_rows_do_not_deny_the_hooks_surface(self):
        if not (ROOT / ".codex-plugin/hooks/hooks.json").is_file():
            self.skipTest("no Codex hooks manifest")
        for row_id, row in manifest_rows().items():
            if row["channel"] == "codex":
                self.assertNotIn("no Codex hooks surface", row["note"], row_id)

    def test_reviewer_writes_have_rows(self):
        hooks = read_json("hooks/hooks.json")
        src = (ROOT / "hooks/scripts/guard_writer_confinement.py").read_text(encoding="utf-8")
        if "karta-safety-auditor" not in src:
            self.skipTest("writer confinement does not cover reviewers here")
        rows = manifest_rows()
        for channel in ("write", "edit", "notebookedit", "bash"):
            row = rows.get(f"reviewer-write:{channel}")
            self.assertIsNotNone(row, f"reviewer-write:{channel} missing")
            self.assertEqual(row["status"], "enforced")
            self.assertEqual(row["guard"], "guard_writer_confinement.py")
        self.assertTrue(hooks)


class MultiEditIsRouted(unittest.TestCase):
    """MultiEdit writes a file exactly as Edit does, so every file guard that
    watches Edit must see it too — in hooks.json, the manifest, and the probe."""

    def claude_hook_tools(self, event):
        out = {}
        for group in read_json("hooks/hooks.json")["hooks"].get(event, []):
            for hook in group.get("hooks", []):
                for script in re.findall(r"([\w.-]+\.py)", hook.get("command", "")):
                    out.setdefault(script, set()).update(group.get("matcher", "").split("|"))
        return out

    def test_file_guards_that_watch_edit_also_watch_multiedit(self):
        for event in ("PreToolUse", "PostToolUse"):
            for script, tools in self.claude_hook_tools(event).items():
                if "Edit" in tools:
                    self.assertIn("MultiEdit", tools, f"{event} {script} sees Edit but not MultiEdit")
        self.assertIn("MultiEdit", self.claude_hook_tools("PreToolUse")["guard_binder_immutability.py"])
        self.assertIn("MultiEdit", self.claude_hook_tools("PostToolUse")["guard_pack_write.py"])

    def test_manifest_has_enforced_multiedit_rows(self):
        manifest = read_json("benchmarks/flow/mutation-surface.json")
        self.assertIn("multiedit", manifest["channels"])
        rows = manifest_rows()
        for row_id, guard, event in (
                ("binder-write:multiedit", "guard_binder_immutability.py", "PreToolUse"),
                ("pack-write:multiedit", "guard_pack_write.py", "PostToolUse"),
                ("kaizen-write:multiedit", "guard_writer_confinement.py", "PreToolUse"),
                ("docgardner-write:multiedit", "guard_writer_confinement.py", "PreToolUse"),
                ("reviewer-write:multiedit", "guard_writer_confinement.py", "PreToolUse")):
            row = rows.get(row_id)
            self.assertIsNotNone(row, f"{row_id} missing")
            self.assertEqual((row["status"], row["guard"], row["hook_event"]),
                             ("enforced", guard, event), row_id)

    def test_real_guard_denies_multiedit_on_committed_binder(self):
        # Real hooked-repo fixture (a real git repo with a HEAD-committed binder),
        # real guard script, the documented MultiEdit payload shape.
        probe = load("inventory_multiedit_probe", "benchmarks/probes/flow-guard-enforcement-matrix.py")
        ids = {row[0] for row in probe.FAMILY_A}
        self.assertIn("binder-multiedit-write", ids)
        with tempfile.TemporaryDirectory(prefix="gpt-multiedit-") as td:
            fixture = Path(td) / "hooked-repo"
            probe._build_fixture(ROOT, fixture)
            self.assertEqual(probe._run_guard(ROOT, fixture, "guard_binder_immutability.py",
                                              "binder-write-multiedit.json"), 2)
            # Positive control: the same shape on an ordinary file passes.
            benign = Path(td) / "benign.json"
            payload = json.loads((ROOT / probe.FIXTURE_DIR / "payloads"
                                  / "binder-write-multiedit.json").read_text(encoding="utf-8"))
            payload["cwd"] = str(fixture)
            payload["tool_input"]["file_path"] = "src/app.py"
            benign.write_text(json.dumps(payload), encoding="utf-8")
            import subprocess
            proc = subprocess.run([sys.executable, str(ROOT / "hooks/scripts/guard_binder_immutability.py")],
                                  input=benign.read_text(encoding="utf-8"), cwd=fixture,
                                  capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class FlowProbesAgreeWithCode(unittest.TestCase):
    """The probes' pinned expectations must hold against the running guards."""

    def test_guard_matrix_has_no_mismatch_and_no_missing_seed(self):
        probe = load("inventory_matrix_probe", "benchmarks/probes/flow-guard-enforcement-matrix.py")
        with tempfile.TemporaryDirectory(prefix="gpt-inventory-") as td:
            fixture = Path(td) / "hooked-repo"
            probe._build_fixture(ROOT, fixture)
            fam_a = probe._family_a(ROOT, fixture)
        evidence = probe._assemble(fam_a, probe._family_b(ROOT), None, None)
        bad = [f for f in evidence["findings"] if f["severity"] == "high"]
        self.assertEqual(bad, [])
        self.assertEqual(evidence["status"], "pass")

    def test_contradiction_seeds_are_still_open(self):
        probe = load("inventory_contra_probe", "benchmarks/probes/flow-spec-contradictions.py")
        os.environ.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "gpt-inventory-uv"))
        current, err = probe._run_runner(ROOT)
        if current is None and "host toolchain missing" in err:
            self.skipTest(err)
        self.assertIsNotNone(current, err)
        result = probe.assemble(current, None, None)
        missing = [f["finding_id"] for f in result["findings"]
                   if f["finding_id"].startswith("seed-missing:")]
        self.assertEqual(missing, [])

    def test_hooks_doc_rows_match_the_matchers(self):
        probe = load("inventory_ledger_probe", "benchmarks/probes/parity-doc-truth-ledger.py")
        _claims, ledger, _ = probe.load_claims(ROOT)
        self.assertEqual(probe.lane_e(ROOT, ledger), [])


if __name__ == "__main__":
    unittest.main()
