"""Check the repo-local Copilot hooks manifest runs both commit gates on both shells, once."""
import json
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / ".github/hooks/karta-repo.json"
GATES = ("scripts/hooks/precommit_gate.py", "scripts/hooks/roundtable_gate.py")


class CopilotRepoHooksManifestTest(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def hooks(self):
        return [h for g in self.manifest["hooks"]["PreToolUse"] for h in g["hooks"]]

    def test_version_and_single_bash_group(self):
        self.assertEqual(self.manifest["version"], 1)
        self.assertIsInstance(self.manifest.get("description"), str)
        groups = self.manifest["hooks"]["PreToolUse"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "Bash")

    def test_both_gates_named_in_both_shells_and_exist(self):
        for gate in GATES:
            with self.subTest(gate=gate):
                self.assertTrue((REPO / gate).is_file(), f"{gate} missing")
                hits = [h for h in self.hooks()
                        if gate in h.get("bash", "") and gate in h.get("powershell", "")]
                self.assertTrue(hits, f"{gate} has no entry naming it in both shells")

    def test_every_entry_marks_source_and_exits_explicitly(self):
        self.assertTrue(self.hooks())
        for hook in self.hooks():
            with self.subTest(bash=hook.get("bash")):
                self.assertEqual(hook.get("type"), "command")
                self.assertEqual(hook.get("env", {}).get("KARTA_HOOK_SOURCE"), "copilot")
                self.assertIsInstance(hook.get("timeoutSec"), int)
                self.assertTrue(hook["powershell"].rstrip().endswith("; exit $LASTEXITCODE"))
                for shell in ("bash", "powershell"):
                    self.assertNotIn("{{", hook[shell])
                    self.assertNotIn("uv ", hook[shell])


if __name__ == "__main__":
    unittest.main()
