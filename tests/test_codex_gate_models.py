"""Exercise model propagation from canonical agents to Codex install artifacts."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "sync_codex_agents", REPO / "scripts/sync_codex_agents.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class GateModelsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        agents = self.root / "agents"
        agents.mkdir()
        (self.root / ".claude-plugin").mkdir()
        (self.root / ".claude-plugin/plugin.json").write_text(
            '{"name": "karta", "version": "9.8.7"}\n', encoding="utf-8")
        for name, model, effort in (
            ("karta-acceptance-reviewer", "gpt-6-astra", "high"),
            ("karta-safety-auditor", "gpt-5.6-sol", "xhigh"),
        ):
            (agents / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: Review\ntools: Read, Glob, Grep, Bash\n"
                f"model: opus\ncodex_model: {model}\neffort: {effort}\n"
                "---\nRead the diff.\n", encoding="utf-8")
        for name, value in (
            ("ROOT", self.root), ("AGENTS", agents),
            ("CODEX_AGENTS", self.root / ".codex/agents"),
        ):
            override = patch.object(sync, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.manifest = self.root / "skills/karta-verify/references/codex-gate-models.json"

    def test_fallback_models_follow_each_canonical_agent(self):
        artifacts = sync.projections()
        manifest = json.loads(artifacts[self.manifest])
        self.assertEqual(manifest, {
            "karta-acceptance-reviewer": {"model": "gpt-6-astra", "reasoning_effort": "high"},
            "karta-safety-auditor": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
        })
        for name, entry in manifest.items():
            registered = tomllib.loads(artifacts[self.root / f".codex/agents/{name}.toml"])
            self.assertEqual(entry["model"], registered["model"])
            self.assertEqual(entry["reasoning_effort"], registered["model_reasoning_effort"])
            self.assertEqual(registered["sandbox_mode"], "read-only")
            self.assertEqual(artifacts[self.manifest.parent / f"{name}.agent.md"],
                             "Read the diff.\n")

    def test_check_rejects_missing_or_changed_plugin_models(self):
        for path, content in sync.projections().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with patch("sys.argv", ["sync_codex_agents.py", "--check"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(), 0)
            self.manifest.write_text('{}\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sync.main(), 1)
            self.assertIn("codex-gate-models.json", output.getvalue())
            self.manifest.unlink()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sync.main(), 1)
            self.assertIn("codex-gate-models.json (missing)", output.getvalue())

    def test_missing_codex_model_never_falls_back_to_opus(self):
        path = self.root / "agents/karta-safety-auditor.md"
        path.write_text(path.read_text(encoding="utf-8").replace("codex_model: gpt-5.6-sol\n", ""), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "missing frontmatter 'codex_model'"):
            sync.projections()

    def test_copilot_profiles_use_native_fields_and_preserve_review_prompt(self):
        artifacts = sync.projections()
        settings = json.loads(artifacts[self.manifest])
        for name, entry in settings.items():
            profile = artifacts[self.root / f".github/agents/{name}.agent.md"]
            header, body = sync.parse_agent(profile)
            for field in ("model", "models", "modelPolicy"):
                self.assertNotIn(field, header)
            self.assertEqual(json.loads(header["reasoningEffort"]), entry["reasoning_effort"])
            self.assertEqual(json.loads(header["tools"]), ["read", "search", "execute"])
            self.assertNotIn("codex_model", header)
            self.assertNotIn("effort", header)
            self.assertTrue(body.endswith("Read the diff."))
            canonical, _ = sync.parse_agent((self.root / f"agents/{name}.md").read_text(encoding="utf-8"))
            self.assertEqual(canonical["model"], "opus")

    def test_copilot_plugin_selects_native_profiles_and_no_claude_hooks(self):
        artifacts = sync.projections()
        manifest = json.loads(artifacts[self.root / ".github/plugin/plugin.json"])
        self.assertEqual(manifest["name"], "karta")
        self.assertEqual(manifest["version"], "9.8.7")
        self.assertEqual(manifest["hooks"], {})
        for name in ("karta-acceptance-reviewer", "karta-safety-auditor"):
            self.assertIn(self.root / manifest["agents"] / f"{name}.agent.md", artifacts)

    def test_copilot_profile_drift_is_reported(self):
        for path, content in sync.projections().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        profile = self.root / ".github/agents/karta-safety-auditor.agent.md"
        profile.write_text(profile.read_text(encoding="utf-8").replace('---\n', '---\nmodel: "gpt-5.6-sol"\n', 1), encoding="utf-8")
        with patch("sys.argv", ["sync_codex_agents.py", "--check"]):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(sync.main(), 1)
        self.assertIn(".github/agents/karta-safety-auditor.agent.md",
                      output.getvalue().replace("\\", "/"))

    def test_copilot_reviewers_cannot_acquire_edit_tools(self):
        path = self.root / "agents/karta-safety-auditor.md"
        path.write_text(path.read_text(encoding="utf-8").replace("tools: Read", "tools: Read, Edit"), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "reviewer tools must stay read-only"):
            sync.projections()


if __name__ == "__main__":
    unittest.main()
