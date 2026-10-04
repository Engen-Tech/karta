"""Check the native Copilot hooks manifest wires every Codex-twin guard on both shells (G1, G17)."""
import json
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / ".github/plugin/hooks.json"
SCRIPTS = REPO / ".codex-plugin/hooks/scripts"


def entries(manifest: dict):
    """Yield (event, matcher, hook) for every command hook in a native Copilot manifest."""
    for event, groups in manifest["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                yield event, group.get("matcher"), hook


class CopilotHooksManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.hooks = list(entries(self.manifest))

    def test_plugin_manifest_points_at_native_hooks_file(self):
        plugin = json.loads((REPO / ".github/plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["hooks"], "./.github/plugin/hooks.json")
        self.assertEqual(self.manifest["version"], 1)

    def test_every_codex_guard_is_listed(self):
        guards = sorted(p.name for p in SCRIPTS.glob("*.py"))
        self.assertTrue(guards, "no guards found under .codex-plugin/hooks/scripts")
        for name in guards:
            with self.subTest(guard=name):
                hits = [h for _, _, h in self.hooks
                        if f".codex-plugin/hooks/scripts/{name}" in h.get("bash", "")
                        and f".codex-plugin/hooks/scripts/{name}" in h.get("powershell", "")]
                self.assertTrue(hits, f"{name} has no hooks.json entry naming it in both shells")

    def test_every_entry_has_bash_and_powershell(self):
        self.assertTrue(self.hooks)
        for event, matcher, hook in self.hooks:
            with self.subTest(event=event, matcher=matcher):
                self.assertEqual(hook.get("type"), "command")
                for shell in ("bash", "powershell"):
                    self.assertIsInstance(hook.get(shell), str)
                    self.assertTrue(hook[shell].strip(), f"empty {shell} entry")

    def test_powershell_entries_have_no_brace_expansion_and_exit_explicitly(self):
        for event, matcher, hook in self.hooks:
            with self.subTest(event=event, matcher=matcher, command=hook.get("powershell")):
                ps = hook["powershell"]
                self.assertNotIn("${", ps)
                self.assertTrue(ps.rstrip().endswith("exit $LASTEXITCODE"),
                                "pwsh -Command reports 1 for a nested exit 2 without it (G17)")


if __name__ == "__main__":
    unittest.main()
