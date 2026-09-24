"""Behavioral regressions for audit F08 (reviewer write protection) and F16 (persisted
reviewer attempts); fixtures never edit the repo.

F08 drives the real writer-confinement hook with Claude Code's PreToolUse payload
shape, then emulates the harness: a command the hook allows is actually executed in a
temporary Git repository, and the repository is compared before and after. A denied
command never runs. Each denied shape also has a positive control showing the same
command really writes when the main thread runs it.

F16 drives the real attempt-ledger helper as separate processes, the way a resumed
session would, against real temporary repositories and worktrees.

Set AUDIT_SOURCE_ROOT to run the same negative controls against a prior snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
GUARD = ROOT / "hooks/scripts/guard_writer_confinement.py"
LEDGER = ROOT / "skills/karta-verify/scripts/gate_attempts.py"
REVIEWERS = ("karta-acceptance-reviewer", "karta-safety-auditor",
             "karta-design-reviewer")


class GitRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-reviewer-fix-")
        self.addCleanup(self.tmp.cleanup)
        self.repos = 0
        self.fresh_repo()

    def fresh_repo(self):
        self.repos += 1
        self.root = Path(self.tmp.name) / f"repo{self.repos}"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Regression fixture")
        self.git("config", "core.autocrlf", "false")
        self.write("src/app.py", "print('hello')\n")
        self.write("docs/notes.md", "notes\n")
        self.commit()

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", "-C", str(cwd or self.root), *args], text=True,
                                       encoding="utf-8").strip()

    def write(self, path, content):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def commit(self, message="fixture"):
        self.git("add", "-A")
        self.git("commit", "-qm", message)


def _payload(agent_type, tool_name, tool_input, cwd):
    """The documented PreToolUse input: common fields plus the subagent identity."""
    payload = {"session_id": "fixture-session", "transcript_path": "/dev/null", "cwd": str(cwd),
               "permission_mode": "default", "hook_event_name": "PreToolUse",
               "tool_name": tool_name, "tool_input": tool_input, "tool_use_id": "toolu_fixture"}
    if agent_type is not None:
        payload["agent_id"] = "agent-fixture"
        payload["agent_type"] = agent_type
    return payload


class ReviewerWriteProtection(GitRepo):
    def hook(self, agent_type, tool_name, tool_input):
        p = subprocess.run([sys.executable, str(GUARD)],
                           input=json.dumps(_payload(agent_type, tool_name, tool_input, self.root)),
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        return p.returncode, p.stderr

    def snapshot(self):
        """Everything a reviewer must not change: files, index entries, refs, stashes."""
        h = hashlib.sha256()
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root).as_posix()
            if rel == ".git" or rel.startswith(".git/"):
                continue
            h.update(rel.encode() + b"\0")
            if p.is_symlink():
                h.update(b"L" + os.readlink(p).encode())
            elif p.is_file():
                h.update(p.read_bytes())
            h.update(oct(p.lstat().st_mode).encode())
        for args in (("ls-files", "-s"), ("for-each-ref",), ("stash", "list"), ("config", "--local", "--list")):
            h.update(self.git(*args).encode())
        return h.hexdigest()

    def harness_bash(self, agent_type, command):
        """Run the hook; when it allows, execute the command as the harness would."""
        code, reason = self.hook(agent_type, "Bash", {"command": command, "description": "fixture"})
        if code == 0:
            subprocess.run(["bash", "-c", command], cwd=self.root, capture_output=True, timeout=60)
        return code, reason

    WRITE_SHAPES = (
        "echo leaked > leaked.txt",
        "echo leaked >> src/app.py",
        "printf x | tee leaked.txt",
        "sed -i s/hello/bye/ src/app.py",
        "rm src/app.py",
        "mv docs/notes.md docs/moved.md",
        "cp src/app.py src/copy.py",
        "mkdir newdir",
        "touch leaked.txt",
        "chmod 600 src/app.py",
        "ln -s src/app.py link.py",
        "git add -A",
        "git checkout -b reviewer-branch",
        "git branch reviewer-branch",
        "git tag reviewer-tag",
        "git stash",
        "git reset --hard HEAD",
        "git config user.name reviewer",
        "git diff --output=leaked.txt HEAD",
        "git -c core.pager=cat log -1",
        "pip install --target leaked requests",
        "npm install left-pad",
        "python3 -c \"open('leaked.txt', 'w').write('x')\"",
        "python3 -c \"from pathlib import Path; Path('leaked.txt').write_text('x')\"",
        "node -e \"require('fs').writeFileSync('leaked.txt', 'x')\"",
        "perl -e 'open(my $f, \">\", \"leaked.txt\")'",
        "bash -c 'touch leaked.txt'",
        "cd src && touch leaked.txt",
        "cat <<EOF > leaked.txt\nx\nEOF",
        "echo $(touch leaked.txt)",
        "find . -name '*.py' -exec chmod 600 {} +",
        "env FOO=1 touch leaked.txt",
        "timeout 5 touch leaked.txt",
    )

    READ_SHAPES = (
        "git status --short",
        "git diff HEAD~0..HEAD",
        "git diff --stat HEAD",
        "git diff --no-ext-diff --no-textconv --binary --no-color HEAD --",
        "git log --oneline -3",
        "git show HEAD:src/app.py",
        "git rev-parse HEAD",
        "git cat-file -p HEAD",
        "git ls-files",
        "git branch --show-current",
        "git branch -a",
        "git merge-base HEAD HEAD",
        "git diff --name-status HEAD | head -5",
        "grep -rn hello src",
        "cat src/app.py | wc -l",
        "ls -la",
        "find . -name '*.py' -not -path './.git/*'",
        "git diff HEAD > /dev/null 2>&1",
        "sha256sum src/app.py",
    )

    def test_edit_tools_denied_for_every_reviewer(self):
        target = str(self.root / "src/app.py")
        shapes = (("Write", {"file_path": target, "content": "x"}),
                  ("Edit", {"file_path": target, "old_string": "hello", "new_string": "bye",
                            "replace_all": False}),
                  ("MultiEdit", {"file_path": target, "edits": [{"old_string": "hello", "new_string": "bye"}]}),
                  ("NotebookEdit", {"notebook_path": str(self.root / "n.ipynb"), "new_source": "x"}))
        for name in REVIEWERS:
            for agent_type in (name, "karta:" + name):
                for tool, tool_input in shapes:
                    with self.subTest(agent=agent_type, tool=tool):
                        code, reason = self.hook(agent_type, tool, tool_input)
                        self.assertEqual(2, code)
                        self.assertIn("read-only", reason)

    def test_write_shaped_bash_denied_and_nothing_changes(self):
        for command in self.WRITE_SHAPES:
            for name in REVIEWERS:
                with self.subTest(agent=name, command=command):
                    before = self.snapshot()
                    code, reason = self.harness_bash("karta:" + name, command)
                    self.assertEqual(2, code, reason)
                    self.assertTrue(reason.startswith("karta: "))
                    self.assertEqual(before, self.snapshot())

    # `git -c` only reads here; it is denied because a config override can name a program.
    NOT_A_WRITE_BY_ITSELF = ("git -c core.pager=cat log -1",)

    def test_write_shapes_really_write_for_the_main_thread(self):
        # Positive control: each denied command is a real write, and the hook still lets
        # the main thread run it. A tracked file is left dirty first so stash, reset and
        # add have something to change. Network-bound installs are not executed.
        for command in self.WRITE_SHAPES:
            if "install" in command or command in self.NOT_A_WRITE_BY_ITSELF:
                continue
            with self.subTest(command=command):
                self.fresh_repo()
                self.write("docs/notes.md", "dirty\n")
                before = self.snapshot()
                code, _ = self.harness_bash(None, command)
                self.assertEqual(0, code)
                self.assertNotEqual(before, self.snapshot(), f"positive control did not write: {command}")

    def test_read_only_bash_allowed_for_reviewers(self):
        for command in self.READ_SHAPES:
            for name in REVIEWERS:
                with self.subTest(agent=name, command=command):
                    before = self.snapshot()
                    code, reason = self.harness_bash(name, command)
                    self.assertEqual(0, code, reason)
                    self.assertEqual(before, self.snapshot())

    # Script execution: every shape runs a committed script that writes `leaked.txt`.
    SCRIPT_SHAPES = (
        "bash tool.sh",
        "sh tool.sh",
        "zsh tool.sh",
        "dash tool.sh",
        "bash -x tool.sh",
        "bash -- tool.sh",
        "./tool.sh",
        "scripts/tool.sh",
        "{root}/tool.sh",
        "source tool.sh",
        ". tool.sh",
        "env bash tool.sh",
        "command ./tool.sh",
        "exec ./tool.sh",
        "timeout 5 ./tool.sh",
        "bash < tool.sh",
        "sh -s < tool.sh",
        "cat tool.sh | bash",
        "cat tool.sh | sh -s",
        "bash -c 'bash tool.sh'",
        "bash -c ./tool.sh",
        "git diff HEAD && ./tool.sh",
        "find . -name tool.sh -exec ./tool.sh ';'",
        "echo tool.sh | xargs ./tool.sh",
        "BASH_ENV={root}/tool.sh bash -c 'true'",
        "PATH=.:/usr/bin:/bin tool.sh",
        "bash -i -c 'true'",
    )

    def script_repo(self):
        self.fresh_repo()
        body = "#!/bin/sh\ntouch leaked.txt\n"
        for rel in ("tool.sh", "scripts/tool.sh"):
            self.write(rel, body)
            os.chmod(self.root / rel, 0o755)
        self.commit("script fixture")

    def test_script_execution_denied_for_reviewers_and_nothing_changes(self):
        self.script_repo()
        for template in self.SCRIPT_SHAPES:
            command = template.format(root=self.root)
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(2, code, f"allowed: {command}")
                        self.assertTrue(reason.startswith("karta: "))
                        self.assertEqual(before, self.snapshot())

    # Shapes whose write depends on the host's interactive start-up, not on tool.sh.
    SCRIPT_CONTROL_SKIP = ("bash -i -c 'true'",)

    def test_script_shapes_really_run_for_the_main_thread(self):
        # Positive control: the same commands really execute tool.sh and write, and the
        # hook leaves the main thread alone.
        for template in self.SCRIPT_SHAPES:
            if template in self.SCRIPT_CONTROL_SKIP:
                continue
            command = template.format(root=self.root)
            with self.subTest(command=command):
                self.script_repo()
                command = template.format(root=self.root)
                code, _ = self.harness_bash(None, command)
                self.assertEqual(0, code)
                self.assertTrue((self.root / "leaked.txt").exists(),
                                f"positive control did not run the script: {command}")

    def test_script_execution_unchanged_for_writers(self):
        self.script_repo()
        for agent_type in ("karta-kaizen", "karta:karta-doc-gardner"):
            for command in ("bash tool.sh", "./tool.sh", "source tool.sh", "cat tool.sh | bash"):
                with self.subTest(agent=agent_type, command=command):
                    self.assertEqual(0, self.hook(agent_type, "Bash", {"command": command})[0])

    # What the reviewer prompts and the verify skill tell a reviewer to run (git diff
    # over the range, its stat and name-status, the byte-exact digest, the reviewed
    # tree, the evidence ref, git -C into the worktree, text search), plus the plain
    # reads a reviewer reaches for. `{range}` is the fixture's last commit.
    PROMPTED_READS = (
        "git diff {range}",
        "git -C {root} diff {range}",
        "git diff --stat {range}",
        "git diff --name-status {range}",
        "git diff --stat {range} && git diff --name-status {range}",
        "git diff --name-only {range} | wc -l",
        "git diff {range} | wc -c",
        "git diff --no-ext-diff --no-textconv --binary --no-color {range} -- | sha256sum",
        "git rev-parse HEAD^{{tree}}",
        "git rev-parse HEAD HEAD~1",
        "git cat-file -p HEAD",
        "git cat-file -t HEAD",
        "git show HEAD:src/app.py",
        "git show --stat HEAD",
        "git log --oneline -3",
        "git log -1 --format=%H",
        "git status --short",
        "git ls-files",
        "git ls-tree -r --name-only HEAD",
        "git merge-base HEAD HEAD~1",
        "git branch --show-current",
        "git branch -a",
        "git for-each-ref refs/heads",
        "git config --get user.name",
        "git worktree list --porcelain",
        "git blame src/app.py",
        "git grep -n hello",
        "git describe --always",
        "git rev-list --count HEAD",
        "git symbolic-ref --short HEAD",
        "GIT_PAGER=cat git log -1",
        "LC_ALL=C sort src/app.py",
        "grep -rn hello src",
        "grep -n 'print' src/app.py",
        "rg -n hello src",
        "cat src/app.py",
        "cat src/app.py | head -1",
        "head -5 src/app.py",
        "tail -n 2 src/app.py",
        "wc -l src/app.py",
        "ls -la src",
        "stat src/app.py",
        "file src/app.py",
        "find . -name '*.py' -not -path './.git/*'",
        "jq -n '{{a: 1}}'",
        "sha256sum src/app.py",
        "diff src/app.py docs/notes.md || true",
        "test -f src/app.py && echo yes",
        "[ -f src/app.py ] || echo no",
        "git diff HEAD > /dev/null 2>&1",
        "git log --oneline -1\ngit status --short",
        "pwd; realpath src/app.py; basename src/app.py; dirname src/app.py",
        "echo done",
    )

    def test_prompted_reads_still_pass_for_reviewers(self):
        self.script_repo()
        for template in self.PROMPTED_READS:
            command = template.format(root=self.root, range="HEAD~1..HEAD")
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(0, code, reason)
                        self.assertEqual(before, self.snapshot())

    def test_git_c_into_a_linked_worktree_passes_and_elsewhere_is_denied(self):
        self.script_repo()
        other = Path(self.tmp.name) / "item-wt"
        self.git("worktree", "add", "-q", str(other), "HEAD")
        outside = Path(self.tmp.name) / "elsewhere"
        outside.mkdir()
        for name in REVIEWERS:
            with self.subTest(agent=name):
                self.assertEqual(0, self.hook(name, "Bash", {"command": f"git -C {other} diff HEAD~1..HEAD"})[0])
                self.assertEqual(0, self.hook(name, "Bash", {"command": f"git -C {other} rev-parse HEAD^{{tree}}"})[0])
                self.assertEqual(2, self.hook(name, "Bash", {"command": f"git -C {outside} status"})[0])
                self.assertEqual(2, self.hook(name, "Bash", {"command": "git -C .. status"})[0])

    # `printf -v NAME` assigns a shell variable; with HOME or PATH reassigned, a later
    # listed command reads a committed .gitconfig (whose diff.external runs tool.sh) or
    # runs a committed ./cat. printf is off the reviewers' list in every spelling.
    PRINTF_SHAPES = (
        "printf -vHOME .; git diff HEAD~1..HEAD",
        "printf -v HOME .; git diff HEAD~1..HEAD",
        "printf -v'HOME' %s . && git diff --stat HEAD~1..HEAD && git diff HEAD~1..HEAD",
        "printf -vPATH %s .:/usr/bin:/bin; cat src/app.py",
        "printf -v PATH -- %s .:/usr/bin:/bin; cat src/app.py",
        "printf -- -vHOME; printf -vHOME .; git diff HEAD~1..HEAD",
    )
    PRINTF_PLAIN = ("printf '%s\\n' done", "printf -- x", "echo x | printf x")

    def printf_repo(self):
        self.fresh_repo()
        self.write("tool.sh", "#!/bin/sh\ntouch leaked.txt\n")
        self.write("cat", "#!/bin/sh\ntouch leaked.txt\n")
        os.chmod(self.root / "tool.sh", 0o755)
        os.chmod(self.root / "cat", 0o755)
        self.write(".gitconfig", "[diff]\n\texternal = ./tool.sh\n")
        self.write("src/app.py", "print('changed')\n")
        self.commit("hostile config fixture")

    def test_printf_is_denied_in_every_spelling_and_nothing_changes(self):
        self.printf_repo()
        for command in self.PRINTF_SHAPES + self.PRINTF_PLAIN:
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(2, code, f"allowed: {command}")
                        self.assertTrue(reason.startswith("karta: "))
                        self.assertIn("`printf`", reason)
                        self.assertEqual(before, self.snapshot())

    def test_printf_shapes_really_run_a_program_for_the_main_thread(self):
        for command in self.PRINTF_SHAPES:
            with self.subTest(command=command):
                self.printf_repo()
                self.assertEqual(0, self.harness_bash(None, command)[0])
                self.assertTrue((self.root / "leaked.txt").exists(),
                                f"positive control did not run the program: {command}")

    def nested_repo(self):
        """An untracked repository inside the working tree whose own config names a diff
        driver that writes."""
        self.fresh_repo()
        sub = self.root / "sub"
        sub.mkdir()
        self.git("init", "-q", cwd=sub)
        self.git("config", "user.email", "fixture@example.invalid", cwd=sub)
        self.git("config", "user.name", "Regression fixture", cwd=sub)
        (sub / "tool.sh").write_text("#!/bin/sh\ntouch leaked.txt\n", encoding="utf-8")
        os.chmod(sub / "tool.sh", 0o755)
        (sub / "a.txt").write_text("one\n", encoding="utf-8")
        self.git("add", "-A", cwd=sub)
        self.git("commit", "-qm", "nested", cwd=sub)
        self.git("config", "diff.external", "./tool.sh", cwd=sub)
        (sub / "a.txt").write_text("two\n", encoding="utf-8")
        return sub

    NESTED_SHAPES = (
        "git -C sub diff",
        "git -C ./sub diff",
        "git -C {sub} diff",
        "git -C {root}/sub/. diff",
        "git -C . -C sub diff",
        "git -C sub/.. -C sub diff",
        "git -C {root} -C sub diff",
        "git --git-dir=sub/.git --work-tree=sub diff",
        "git --work-tree=sub diff",
    )

    def test_git_c_into_a_nested_repository_is_denied_and_nothing_changes(self):
        sub = self.nested_repo()
        for template in self.NESTED_SHAPES:
            command = template.format(root=self.root, sub=sub)
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(2, code, f"allowed: {command}")
                        self.assertTrue(reason.startswith("karta: "))
                        self.assertEqual(before, self.snapshot())
                        self.assertFalse((sub / "leaked.txt").exists())

    def test_git_c_into_a_nested_repository_really_runs_its_driver_for_the_main_thread(self):
        for template in ("git -C sub diff", "git -C . -C sub diff"):
            with self.subTest(command=template):
                sub = self.nested_repo()
                self.assertEqual(0, self.harness_bash(None, template)[0])
                self.assertTrue((sub / "leaked.txt").exists())

    def test_git_c_to_the_same_repository_still_passes(self):
        sub = self.nested_repo()
        other = Path(self.tmp.name) / "same-repo-wt"
        self.git("worktree", "add", "-q", str(other), "HEAD")
        for command in (f"git -C {other} diff HEAD~0", f"git -C {self.root} diff",
                        "git -C . diff", f"git -C {other}/ status --short",
                        f"git -C {self.root} -C {other} log -1 --oneline"):
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(0, code, reason)
                        self.assertEqual(before, self.snapshot())
                        self.assertFalse((sub / "leaked.txt").exists())

    # Commands the old deny list let through that the allowlist now refuses: none of
    # them is in a reviewer prompt, and each either runs a program, reads a script, or
    # names a tool outside the list.
    NOW_DENIED = (
        "python3 -c \"import json,sys; print(json.dumps({'a': 1}))\"",
        "python3 -c \"print(open('src/app.py').read())\"",
        "/usr/bin/git log --oneline -1",
        "/bin/cat src/app.py",
        "bash -c 'git diff HEAD | head -5'",
        "bash --version",
        "command -v bash",
        "sed -n 1p src/app.py",
        "awk '{print}' src/app.py",
        "less src/app.py",
        "more src/app.py",
        "man git",
        "tee /dev/null < src/app.py",
        "ls src/*.py",
        "cat src/ap?.py",
        "cat < src/app.py",
        "cd src && cat app.py",
        "git diff --ext-diff HEAD",
        "git show --textconv HEAD:src/app.py",
        "git cat-file --filters HEAD:src/app.py",
        "git grep -O hello",
        "git grep --open-files-in-pager=cat hello",
        "git branch reviewer-branch",
        "git branch --li",
        "git symbolic-ref HEAD refs/heads/other",
        "git config user.name reviewer",
        "git worktree add ../x",
        "git fetch",
        "git --git-dir=.git log",
        "git --exec-path=. log",
        "npx tsc --noEmit",
        "uv run mypy .",
        "npm run typecheck",
        "go vet ./...",
        "./node_modules/.bin/tsc --noEmit",
        "echo x &",
        "echo {a,b}",
        "echo \"$HOME\"",
        "FOO=1 cat src/app.py",
        "GIT_PAGER=less git log -1",
        "printf -v x 1",
        "rg --pre=cat hello src",
    )

    def test_commands_outside_the_allowlist_are_denied(self):
        self.script_repo()
        for command in self.NOW_DENIED:
            for name in REVIEWERS:
                with self.subTest(agent=name, command=command):
                    before = self.snapshot()
                    code, reason = self.harness_bash(name, command)
                    self.assertEqual(2, code, f"allowed: {command}")
                    self.assertTrue(reason.startswith("karta: "))
                    self.assertIn("read-only", reason)
                    self.assertEqual(before, self.snapshot())

    # The independent verifier's probes against the old deny list, rebuilt. Every one
    # writes (or runs a committed script that writes) when the main thread runs it,
    # except those in PROBE_CONTROL_SKIP, whose effect depends on the host.
    VERIFIER_PROBES = (
        "{{ touch leaked.txt; }}",
        "{{ rm src/app.py; }}",
        "{{ ./tool.sh; }}",
        "if true; then touch leaked.txt; fi",
        "if true; then bash tool.sh; fi",
        "if true; then ./tool.sh; fi",
        "if false; then true; else ./tool.sh; fi",
        "if false; then true; elif true; then ./tool.sh; fi",
        "for i in 1; do ./tool.sh; done",
        "while true; do ./tool.sh; break; done",
        "until false; do ./tool.sh; break; done",
        "! ./tool.sh",
        "( ./tool.sh )",
        "(./tool.sh)",
        "case a in a) ./tool.sh;; esac",
        "f() {{ ./tool.sh; }}; f",
        "function f {{ ./tool.sh; }}; f",
        "time ./tool.sh",
        "time bash tool.sh",
        "coproc ./tool.sh; wait",
        "[[ -n x ]] && ./tool.sh",
        "$SHELL tool.sh",
        "${{SHELL}} tool.sh",
        "/usr/bin/env bash tool.sh",
        "/usr/bin/nohup ./tool.sh",
        "nohup ./tool.sh",
        "setsid -w ./tool.sh",
        "nice ./tool.sh",
        "stdbuf -o0 ./tool.sh",
        "timeout 5 ./tool.sh",
        "echo a | xargs -n 1 ./tool.sh",
        "find . -name tool.sh -exec {{}} \\;",
        "find . -name tool.sh -exec env {{}} \\;",
        "trap ./tool.sh EXIT",
        "LESSOPEN='|./tool.sh %s' less src/app.py",
        "env 'BASH_FUNC_git%%=() {{ ./tool.sh; }}' bash -c 'git status'",
        "sed '1e ./tool.sh' src/app.py",
        "awk 'BEGIN {{ system(\"./tool.sh\") }}'",
        "awk -f tool.awk",
        "make",
        "make -f Makefile all",
        "busybox sh tool.sh",
        "eval ./tool.sh",
        "builtin source tool.sh",
        "command . ./tool.sh",
        "exec ./tool.sh",
        "git -c alias.x='!./tool.sh' x",
        "python3 tool.py",
        "node tool.js",
        "perl tool.pl",
        "bash <<< ./tool.sh",
        "bash <<EOF\n./tool.sh\nEOF",
        "echo $(./tool.sh)",
        "echo `./tool.sh`",
        "cat <(./tool.sh)",
        "x=1 ./tool.sh",
        "export PATH=.:/usr/bin:/bin; tool.sh",
        "echo x > leaked.txt",
        "echo x >| leaked.txt",
        "jq -n 1 > leaked.txt",
        "cat src/app.py 2> leaked.txt",
        "sort -o leaked.txt src/app.py",
        "sort --output=leaked.txt src/app.py",
        "sort --out=leaked.txt src/app.py",
        "sort --compress-program=./tool.sh src/app.py",
        "uniq src/app.py leaked.txt",
        "rg --pre ./tool.sh hello src",
        "find . -name app.py -delete",
        "find . -fprint leaked.txt",
        "git log --output=leaked.txt -1",
        "file -C -m src/app.py",
        "./tool.sh &",
        "git diff HEAD |& ./tool.sh",
        "cat tool.sh | bash",
    )

    PROBE_CONTROL_SKIP = (
        # less copies a file to a pipe without its input preprocessor.
        "LESSOPEN='|./tool.sh %s' less src/app.py",
        # sort starts a compressor only when it spills temporary files.
        "sort --compress-program=./tool.sh src/app.py",
        # file -C compiles the named magic source; a Python file is not one.
        "file -C -m src/app.py",
        # A background job may finish after the shell exits.
        "./tool.sh &",
    )

    def probe_repo(self):
        self.script_repo()
        self.write("tool.awk", "BEGIN { system(\"./tool.sh\") }\n")
        self.write("Makefile", "all:\n\t./tool.sh\n")
        self.write("tool.py", "open('leaked.txt', 'w').write('x')\n")
        self.write("tool.js", "require('fs').writeFileSync('leaked.txt', 'x')\n")
        self.write("tool.pl", "open(my $f, '>', 'leaked.txt');\n")
        self.commit("probe fixture")

    def test_verifier_probes_denied_for_every_reviewer_and_nothing_changes(self):
        self.probe_repo()
        for template in self.VERIFIER_PROBES:
            command = template.format(root=self.root)
            for name in REVIEWERS:
                for agent_type in (name, "karta:" + name):
                    with self.subTest(agent=agent_type, command=command):
                        before = self.snapshot()
                        code, reason = self.harness_bash(agent_type, command)
                        self.assertEqual(2, code, f"allowed: {command}")
                        self.assertTrue(reason.startswith("karta: "))
                        self.assertEqual(before, self.snapshot())

    def test_verifier_probes_really_write_for_the_main_thread(self):
        for template in self.VERIFIER_PROBES:
            if template in self.PROBE_CONTROL_SKIP:
                continue
            with self.subTest(command=template):
                self.probe_repo()
                command = template.format(root=self.root)
                before = self.snapshot()
                code, _ = self.harness_bash(None, command)
                self.assertEqual(0, code)
                self.assertNotEqual(before, self.snapshot(), f"positive control did not write: {command}")

    def test_main_thread_and_unknown_agents_pass_every_probe(self):
        commands = [t.format(root=self.root) for t in self.VERIFIER_PROBES] + list(self.NOW_DENIED)
        for command in commands:
            for agent_type in (None, "Explore", "karta-safety-auditor-v2"):
                with self.subTest(agent=agent_type, command=command):
                    self.assertEqual(0, self.hook(agent_type, "Bash", {"command": command})[0])

    # A shell reserved word, `!`, `{` or `(` in front of a write must not hide it from the
    # writers' surface check: each gets the verdict of the bare command.
    WRITER_WRAPPINGS = (
        "{cmd}",
        "{{ {cmd}; }}",
        "if true; then {cmd}; fi",
        "if false; then true; else {cmd}; fi",
        "if false; then true; elif true; then {cmd}; fi",
        "for i in 1; do {cmd}; done",
        "while true; do {cmd}; break; done",
        "until false; do {cmd}; break; done",
        "! {cmd}",
        "( {cmd} )",
        "case a in a) {cmd};; esac",
        "time {cmd}",
        "f() {{ {cmd}; }}",
        "function f {{ {cmd}; }}",
        "coproc {cmd}",
    )

    def test_reserved_words_do_not_hide_a_writers_write(self):
        cases = (("karta-kaizen", "rm src/app.py", 2),
                 ("karta-kaizen", "echo x > .karta/sme/x.md", 0),
                 ("karta:karta-doc-gardner", "rm src/app.py", 2),
                 ("karta-doc-gardner", "touch x; rm docs/notes.md", 0),
                 ("karta-doc-gardner", "echo x > src/leaked.py", 2))
        for agent_type, cmd, want in cases:
            for wrapping in self.WRITER_WRAPPINGS:
                command = wrapping.format(cmd=cmd)
                with self.subTest(agent=agent_type, command=command):
                    self.assertEqual(want, self.hook(agent_type, "Bash", {"command": command})[0])

    def test_reviewer_lookalikes_are_not_recognized(self):
        code, _ = self.hook("karta-safety-auditor-v2", "Bash", {"command": "touch x"})
        self.assertEqual(0, code)
        code, _ = self.hook("Explore", "Write", {"file_path": str(self.root / "x"), "content": "x"})
        self.assertEqual(0, code)

    def test_reviewer_bash_without_command_is_denied(self):
        code, reason = self.hook("karta-acceptance-reviewer", "Bash", {})
        self.assertEqual(2, code)
        self.assertIn("no verifiable", reason)

    def test_writers_and_main_thread_unchanged(self):
        cases = (("karta-kaizen", "Write",
                  {"file_path": ".karta/sme/python.md", "content": "x"}, 0),
                 ("karta-kaizen", "Write", {"file_path": "src/app.py", "content": "x"}, 2),
                 ("karta:karta-doc-gardner", "Bash",
                  {"command": "echo x >> docs/notes.md"}, 0),
                 ("karta-doc-gardner", "Bash", {"command": "rm src/app.py"}, 2),
                 (None, "Write", {"file_path": "src/app.py", "content": "x"}, 0),
                 (None, "Bash", {"command": "git add -A && touch x"}, 0))
        for agent_type, tool, tool_input, want in cases:
            with self.subTest(agent=agent_type, tool=tool, tool_input=tool_input):
                self.assertEqual(want, self.hook(agent_type, tool, tool_input)[0])

    def test_guard_self_test_passes(self):
        p = subprocess.run([sys.executable, str(GUARD), "--self-test"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stdout[-2000:])


class ReviewerHookRouting(unittest.TestCase):
    """hooks.json must route every reviewer write tool to the guard."""

    def matchers(self, manifest):
        data = json.loads((ROOT / manifest).read_text(encoding="utf-8"))
        return [e["matcher"] for e in data["hooks"]["PreToolUse"]
                if any("guard_writer_confinement.py" in h.get("command", "") for h in e["hooks"])]

    def test_claude_manifest_routes_write_tools_and_bash(self):
        matchers = self.matchers("hooks/hooks.json")
        def fires(matcher, tool):
            # Claude Code's documented rule: a matcher of letters, digits, `_`, `-`, spaces,
            # `,` and `|` is a list of exact names; anything else is an unanchored regex.
            if re.fullmatch(r"[A-Za-z0-9_\- ,|]*", matcher):
                return tool in {n.strip() for n in re.split(r"[|,]", matcher)}
            return re.search(matcher, tool) is not None
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"):
            with self.subTest(tool=tool):
                self.assertTrue(any(fires(m, tool) for m in matchers), matchers)

    def test_reviewer_tool_allowlists_exclude_edit_tools(self):
        for name in REVIEWERS:
            text = (ROOT / "agents" / f"{name}.md").read_text(encoding="utf-8")
            tools = re.search(r"^tools:\s*(.+)$", text, re.M).group(1)
            with self.subTest(agent=name):
                for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                    self.assertNotIn(tool, [t.strip() for t in tools.split(",")])

    def test_registered_codex_reviewers_request_read_only_sandbox(self):
        for name in REVIEWERS:
            text = (ROOT / ".codex/agents" / f"{name}.toml").read_text(encoding="utf-8")
            with self.subTest(agent=name):
                self.assertIn('sandbox_mode = "read-only"', text)


@unittest.skipUnless(shutil.which("codex"), "codex CLI not installed")
class CodexReadOnlySandbox(unittest.TestCase):
    """The host mechanism the registered Codex profiles request, exercised without a model."""

    def run_in(self, profile, directory):
        return subprocess.run(["codex", "sandbox", "-P", profile, "-C", str(directory), "--",
                               "sh", "-c", "touch probe"], capture_output=True, text=True, encoding="utf-8",
                              timeout=60)

    def test_read_only_profile_denies_a_harmless_write(self):
        with tempfile.TemporaryDirectory(prefix="gpt-codex-ro-") as d:
            p = self.run_in(":read-only", d)
            if "panicked" in p.stderr or "No such file" in p.stderr:
                self.skipTest("codex sandbox cannot start here: " + p.stderr[-300:])
            self.assertNotEqual(0, p.returncode)
            self.assertFalse((Path(d) / "probe").exists())

    def test_workspace_profile_positive_control_writes(self):
        with tempfile.TemporaryDirectory(prefix="gpt-codex-ww-") as d:
            p = self.run_in(":workspace", d)
            if p.returncode != 0:
                self.skipTest("codex workspace sandbox cannot start here: " + p.stderr[-300:])
            self.assertTrue((Path(d) / "probe").exists())


class AttemptLedger(GitRepo):
    def setUp(self):
        super().setUp()
        self.binder = self.root / ".karta/binders/demo.json"
        self.set_binder("Add greeting")
        self.commit("binder")
        self.base = self.git("rev-parse", "HEAD")
        self.write("src/app.py", "print('hello, world')\n")
        self.commit("build")
        self.range = f"{self.base}..HEAD"

    def set_binder(self, title):
        self.binder.parent.mkdir(parents=True, exist_ok=True)
        self.binder.write_text(json.dumps({"work_items": [
            {"id": "WI01", "title": title, "oracle": {"type": "unit", "assertions": ["greets"]}},
            {"id": "WI02", "title": "Other", "oracle": {"type": "unit", "assertions": ["x"]}}]}),
            encoding="utf-8")

    def ledger(self, *args, repo=None):
        p = subprocess.run([sys.executable, str(LEDGER), *args, "--repo", str(repo or self.root),
                            "--binder", str(self.binder)],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        return p.returncode, p.stdout, p.stderr

    def record(self, gate, verdict, item="WI01", repo=None, diff_range=None):
        return self.ledger("record", "--item", item, "--gate", gate, "--verdict", verdict,
                           "--range", diff_range or self.range, repo=repo)

    def check(self, gate, item="WI01", repo=None):
        code, out, err = self.ledger("check", "--item", item, "--gate", gate, repo=repo)
        return code, (json.loads(out) if out.strip() else None), err

    def ledger_path(self, gate, item="WI01"):
        common = Path(self.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
        return common / "karta/attempts/demo" / f"{item}.{gate}.jsonl"

    def test_acceptance_cap_survives_a_new_process(self):
        self.assertEqual(0, self.check("acceptance")[0])
        self.assertEqual(0, self.record("acceptance", "concerns")[0])
        code, state, _ = self.check("acceptance")
        self.assertEqual((0, 1, 2), (code, state["counted"], state["cap"]))
        self.assertEqual(0, self.record("acceptance", "concerns")[0])
        code, state, err = self.check("acceptance")
        self.assertEqual(1, code)
        self.assertTrue(state["exhausted"])
        self.assertIn("halt", err)

    def test_safety_cap_is_three_then_escalate(self):
        for _ in range(2):
            self.record("safety", "concerns")
        self.assertEqual(0, self.check("safety")[0])
        self.record("safety", "concerns")
        code, state, err = self.check("safety")
        self.assertEqual((1, 3, 3), (code, state["counted"], state["cap"]))
        self.assertIn("escalate", err)

    def test_changed_diff_does_not_reset_the_count(self):
        self.record("acceptance", "concerns")
        self.write("src/app.py", "print('fixed')\n")
        self.commit("fix")
        self.record("acceptance", "concerns")
        code, state, _ = self.check("acceptance")
        self.assertEqual(1, code)
        rows = [json.loads(line) for line in self.ledger_path("acceptance").read_text(encoding="utf-8").splitlines()]
        self.assertNotEqual(rows[0]["diff_sha256"], rows[1]["diff_sha256"])
        self.assertEqual([1, 2], [r["attempt"] for r in rows])

    def test_changed_item_spec_starts_a_new_sequence(self):
        self.record("acceptance", "concerns")
        self.record("acceptance", "concerns")
        self.assertEqual(1, self.check("acceptance")[0])
        self.set_binder("Add greeting, successor spec")
        code, state, _ = self.check("acceptance")
        self.assertEqual((0, 0), (code, state["counted"]))
        # Another item's edit is not this item's spec change.
        self.set_binder("Add greeting")
        self.assertEqual(1, self.check("acceptance")[0])

    def test_items_and_gates_are_independent(self):
        self.record("acceptance", "concerns")
        self.record("acceptance", "concerns")
        self.assertEqual(0, self.check("safety")[0])
        self.assertEqual(0, self.check("acceptance", item="WI02")[0])

    def test_passing_and_blocked_verdicts_do_not_spend_the_cap(self):
        self.record("acceptance", "pass")
        self.record("acceptance", "blocked")
        self.record("acceptance", "concerns")
        code, state, _ = self.check("acceptance")
        self.assertEqual((0, 1), (code, state["counted"]))
        self.assertEqual(2, state["next_attempt"])

    def test_ledger_is_shared_across_worktrees_and_never_in_the_tree(self):
        other = Path(self.tmp.name) / "wt"
        self.git("worktree", "add", "-q", str(other), "HEAD")
        self.record("acceptance", "concerns", repo=other)
        self.record("acceptance", "concerns")
        self.assertEqual(1, self.check("acceptance", repo=other)[0])
        self.assertEqual("", self.git("status", "--porcelain", "--ignored"))
        self.assertTrue(self.ledger_path("acceptance").is_file())

    def test_record_carries_the_identity_fields(self):
        self.record("safety", "concerns")
        row = json.loads(self.ledger_path("safety").read_text(encoding="utf-8").splitlines()[0])
        diff = subprocess.run(["git", "-C", str(self.root), "diff", "--no-ext-diff", "--no-textconv",
                               "--binary", "--no-color", self.range, "--"], capture_output=True).stdout
        item = json.loads(self.binder.read_text(encoding="utf-8"))["work_items"][0]
        spec = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(hashlib.sha256(diff).hexdigest(), row["diff_sha256"])
        self.assertEqual(hashlib.sha256(spec.encode()).hexdigest(), row["item_spec_sha256"])
        self.assertEqual(("safety", 1, "concerns", self.range, "WI01"),
                         (row["gate"], row["attempt"], row["verdict"], row["range"], row["item"]))

    def test_recording_past_the_cap_is_flagged(self):
        self.record("acceptance", "concerns")
        self.record("acceptance", "concerns")
        code, _, err = self.record("acceptance", "concerns")
        self.assertEqual(1, code)
        self.assertIn("cap", err)

    def test_bad_inputs_are_usage_errors(self):
        self.assertEqual(2, self.record("acceptance", "concerns", item="WI99")[0])
        self.assertEqual(2, self.record("acceptance", "concerns", item="../x")[0])
        self.assertEqual(2, self.record("acceptance", "concerns", diff_range="--output=x")[0])

    def test_caps_match_the_agent_files(self):
        acceptance = (ROOT / "agents/karta-acceptance-reviewer.md").read_text(encoding="utf-8")
        safety = (ROOT / "agents/karta-safety-auditor.md").read_text(encoding="utf-8")
        self.assertIn("Max attempts: 2, total.", acceptance)
        self.assertIn("Max attempts: 3.", safety)
        self.assertEqual(2, self.check("acceptance")[1]["cap"])
        self.assertEqual(3, self.check("safety")[1]["cap"])

    def test_helper_self_test_passes(self):
        p = subprocess.run([sys.executable, str(LEDGER), "--self-test"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, p.returncode, p.stdout[-2000:] + p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
