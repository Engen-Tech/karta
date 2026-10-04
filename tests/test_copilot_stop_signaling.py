"""Pin the Copilot-visible stdout of both Codex Stop twins and their plugin mirrors (G2).

Copilot reads a Stop hook's block from stdout: exactly one JSON object
{"decision": "block", "reason": ...} with exit 2. A pass must carry no block
decision. Both guards nest their --self-test fixture helpers inside
_run_self_test, so nothing is importable; the minimal git fixtures are built
inline here instead.

Pass output differs by design: guard_delivery_stop.py prints "{}" on every
pass ("Codex requires JSON on every pass"); guard_subagent_whiff.py prints
nothing. Each class pins its own script's pass output.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
COPIES = {
    "root": REPO / ".codex-plugin/hooks/scripts",
    "mirror": REPO / "plugins/karta/.codex-plugin/hooks/scripts",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=karta@test", "-c", "user.name=karta",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True, encoding="utf-8")


def init_repo(root: Path, binder_ids: list[str] | None = None) -> Path:
    """A git repo with one seed commit and, optionally, a committed binder `wip`."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    if binder_ids is not None:
        binders = repo / ".karta/binders"
        binders.mkdir(parents=True)
        (binders / "wip.json").write_text(
            json.dumps({"slug": "wip", "work_items": [{"id": i} for i in binder_ids]}),
            encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


class StopSignalingContract:
    """Mixin: subclasses set SCRIPT, PASS_STDOUT and the fixture/payload hooks."""

    SCRIPT: Path
    PASS_STDOUT: str

    def blocking_repo(self, root: Path) -> Path:
        raise NotImplementedError

    def payload(self, repo: Path, **over: object) -> dict:
        raise NotImplementedError

    def run_hook(self, payload: dict) -> subprocess.CompletedProcess:
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(REPO))
        return subprocess.run(
            [sys.executable, str(self.SCRIPT)], input=json.dumps(payload),
            cwd=payload["cwd"], env=env, capture_output=True, text=True, encoding="utf-8")

    def assert_block(self, proc: subprocess.CompletedProcess) -> None:
        self.assertEqual(proc.returncode, 2, proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"want one stdout line, got {proc.stdout!r}")
        obj = json.loads(proc.stdout.strip())
        self.assertIsInstance(obj, dict)
        self.assertEqual(obj["decision"], "block")
        self.assertIsInstance(obj["reason"], str)
        self.assertTrue(obj["reason"].strip())

    def test_block_emits_one_json_decision(self):
        with tempfile.TemporaryDirectory() as td:
            repo = self.blocking_repo(Path(td))
            self.assert_block(self.run_hook(self.payload(repo)))

    def test_clean_emits_no_decision(self):
        with tempfile.TemporaryDirectory() as td:
            repo = init_repo(Path(td))
            proc = self.run_hook(self.payload(repo))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), self.PASS_STDOUT)

    def test_stop_hook_active_output_stays_well_formed(self):
        # Copilot stops forcing continuation after repeated blocks; whatever the
        # guard prints then must still be empty or one well-formed JSON object.
        # Both guards treat stop_hook_active as the harness loop flag and pass.
        with tempfile.TemporaryDirectory() as td:
            repo = self.blocking_repo(Path(td))
            proc = self.run_hook(self.payload(repo, stop_hook_active=True))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = proc.stdout.strip()
            self.assertEqual(out, self.PASS_STDOUT)
            if out:
                self.assertIsInstance(json.loads(out), dict)


class DeliveryStopMixin(StopSignalingContract):
    PASS_STDOUT = "{}"

    def blocking_repo(self, root: Path) -> Path:
        # Item b carries a built ref with no done ref: a dirty delivery.
        repo = init_repo(root, ["b"])
        git(repo, "update-ref", "refs/karta/wip/item-b/built", "HEAD")
        return repo

    def payload(self, repo: Path, **over: object) -> dict:
        return {"hook_event_name": "Stop", "session_id": "s1", "cwd": str(repo),
                "stop_hook_active": False, **over}


class SubagentWhiffMixin(StopSignalingContract):
    PASS_STDOUT = ""

    def blocking_repo(self, root: Path) -> Path:
        # Item branch at the integration tip with no state ref: an empty whiff.
        repo = init_repo(root, ["a"])
        git(repo, "branch", "karta/wip/integration")
        git(repo, "branch", "karta/wip/item-a", "karta/wip/integration")
        return repo

    def payload(self, repo: Path, **over: object) -> dict:
        return {"hook_event_name": "SubagentStop", "agent_type": "karta-acceptance-reviewer",
                "agent_id": "x1", "cwd": str(repo), "stop_hook_active": False, **over}


class DeliveryStopRootTest(DeliveryStopMixin, unittest.TestCase):
    SCRIPT = COPIES["root"] / "guard_delivery_stop.py"


class DeliveryStopMirrorTest(DeliveryStopMixin, unittest.TestCase):
    SCRIPT = COPIES["mirror"] / "guard_delivery_stop.py"


class SubagentWhiffRootTest(SubagentWhiffMixin, unittest.TestCase):
    SCRIPT = COPIES["root"] / "guard_subagent_whiff.py"


class SubagentWhiffMirrorTest(SubagentWhiffMixin, unittest.TestCase):
    SCRIPT = COPIES["mirror"] / "guard_subagent_whiff.py"


if __name__ == "__main__":
    unittest.main()
