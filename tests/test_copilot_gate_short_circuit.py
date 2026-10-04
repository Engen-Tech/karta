"""Both repo gates decide once under Copilot (G4): the .claude/settings.json copy is a no-op there."""
import importlib.util
import json
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts/hooks" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


precommit = load("precommit_gate")
roundtable = load("roundtable_gate")

LEGACY = {"COPILOT_CLI": "1"}
COPILOT = {"COPILOT_CLI": "1", "KARTA_HOOK_SOURCE": "copilot"}
CLAUDE = {}


def payload(command: str) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(REPO),
                       "tool_input": {"command": command}})


def failing_runner(name, argv):
    return 1, f"{name}: failed"


def no_git(argv, input_bytes=None):
    raise AssertionError("git must not be consulted for this payload")


def no_helper(*args, **kwargs):
    raise AssertionError("helper must not be consulted for this payload")


ON = {"enabled": True, "points": {"plan_commit": True, "deliver_merge": True}}
# A commit hidden behind env -S: with the gate on, it is denied before any git lookup.
HIDDEN_COMMIT = payload("env -S 'git commit -m x'")


class PrecommitGateShortCircuitTest(unittest.TestCase):
    def run_gate(self, env):
        return precommit.hook_main(payload("git commit -m x"), env, failing_runner)

    def test_legacy_copy_under_copilot_is_a_silent_no_op(self):
        self.assertEqual(self.run_gate(LEGACY), (0, ""))

    def test_copilot_manifest_copy_still_blocks(self):
        code, message = self.run_gate(COPILOT)
        self.assertEqual(code, 2)
        self.assertIn("Commit blocked", message)

    def test_claude_code_still_blocks(self):
        code, message = self.run_gate(CLAUDE)
        self.assertEqual(code, 2)
        self.assertIn("Commit blocked", message)


class RoundtableGateShortCircuitTest(unittest.TestCase):
    def run_gate(self, env):
        return roundtable.hook_main(HIDDEN_COMMIT, env, no_git, no_helper, ON)

    def test_legacy_copy_under_copilot_is_a_silent_no_op(self):
        self.assertEqual(self.run_gate(LEGACY), (0, ""))

    def test_copilot_manifest_copy_still_blocks(self):
        code, message = self.run_gate(COPILOT)
        self.assertEqual(code, 2)
        self.assertIn("hides the command", message)

    def test_claude_code_still_blocks(self):
        code, message = self.run_gate(CLAUDE)
        self.assertEqual(code, 2)
        self.assertIn("hides the command", message)


if __name__ == "__main__":
    unittest.main()
