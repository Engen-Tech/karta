"""Regressions for the second audit remediation batch."""
from __future__ import annotations

import os
import tempfile
import unittest
import subprocess
import sys
import functools
import http.server
import threading
import http.client
import json
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
from test_audit_priority_fixes import RepoCase, load

claude_binder = load("remaining_claude_binder", "hooks/scripts/guard_binder_immutability.py")
codex_binder = load("remaining_codex_binder", ".codex-plugin/hooks/scripts/guard_binder_immutability.py")
secrets = load("remaining_secrets", "skills/karta-build/scripts/scan_secrets.py")
design = load("remaining_design", "skills/karta-validate/scripts/serve_design.py")
watch = load("remaining_watch", "skills/karta-status/scripts/serve_status.py")


class BinderPaths(RepoCase):
    def setUp(self):
        super().setUp()
        self.write(".karta/binders/plan.json", "{}\n")
        self.commit()

    def check_path(self, path, expected):
        payload = {"cwd": str(self.root), "tool_input": {"file_path": path}}
        self.assertEqual(expected, claude_binder.decide(payload)[0])
        payload["tool_input"] = {"command": f"*** Begin Patch\n*** Update File: {path}\n@@\n-{{}}\n+{{\"changed\":true}}\n*** End Patch"}
        self.assertEqual(expected, codex_binder.decide(payload)[0])

    def test_dot_and_repeated_separator_aliases(self):
        for path in (".karta//binders/plan.json", ".karta/binders/./plan.json",
                     ".karta/binders/../binders/plan.json"):
            with self.subTest(path=path):
                self.check_path(path, 2)

    def test_symlink_file_and_parent_aliases(self):
        self.symlink("alias.json", self.root / ".karta/binders/plan.json")
        self.symlink("plans", self.root / ".karta/binders", directory=True)
        self.check_path("alias.json", 2)
        self.check_path("plans/plan.json", 2)

    def test_committed_binder_link_is_protected(self):
        self.symlink(".karta/binders/link.json", self.root / "source.txt")
        self.commit()
        self.check_path(".karta/binders/link.json", 2)

    def test_draft_and_unrelated_file_remain_writable(self):
        self.check_path(".karta/binders/draft.json", 0)
        self.check_path("source.txt", 0)

    def hard_link(self, link, target):
        try:
            os.link(target, link)
        except OSError as e:
            self.skipTest(f"hard link creation unavailable: {e}")

    def test_hard_link_to_a_committed_binder_is_protected(self):
        # realpath does not collapse a hard link, so only the inode can tell.
        self.write(".karta/binders/archive/old.json", "{}\n")
        self.commit()
        outside = tempfile.TemporaryDirectory(prefix="gpt-hardlink-outside-", dir=self.root.parent)
        self.addCleanup(outside.cleanup)
        binders = self.root / ".karta/binders"
        self.hard_link(self.root / "alias.json", binders / "plan.json")
        self.hard_link(self.root / "old-alias.txt", binders / "archive/old.json")
        self.hard_link(binders / "copy.json", binders / "plan.json")
        self.hard_link(Path(outside.name) / "far.json", binders / "plan.json")
        for path in ("alias.json", "old-alias.txt", ".karta/binders/copy.json",
                     str(Path(outside.name) / "far.json")):
            with self.subTest(path=path):
                self.check_path(path, 2)

    def check_path_from(self, cwd, path, expected):
        payload = {"cwd": str(cwd), "tool_input": {"file_path": path}}
        self.assertEqual(expected, claude_binder.decide(payload)[0])
        payload["tool_input"] = {"command": f"*** Begin Patch\n*** Update File: {path}\n@@\n-{{}}\n+{{\"changed\":true}}\n*** End Patch"}
        self.assertEqual(expected, codex_binder.decide(payload)[0])

    def test_hard_link_across_worktrees_of_one_repository_is_protected(self):
        # A linked worktree has its own copy of every tracked file, so a hard link made
        # there to the main checkout's binder shares no inode with anything the linked
        # worktree's HEAD names. Every worktree of the repository has to be compared.
        holder = tempfile.TemporaryDirectory(prefix="gpt-hardlink-wt-", dir=self.root.parent)
        self.addCleanup(holder.cleanup)
        wt = Path(holder.name) / "wt"
        self.addCleanup(lambda: subprocess.run(["git", "-C", str(self.root), "worktree", "remove",
                                                "--force", str(wt)], capture_output=True))
        self.git("worktree", "add", "-q", "-b", "item", str(wt), "HEAD")
        (wt / ".karta/binders/item-only.json").write_text("{}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(wt), "commit", "-qm", "item binder"], check=True)
        # From the linked worktree to the main checkout's committed binder.
        self.hard_link(wt / "alias.json", self.root / ".karta/binders/plan.json")
        # From the main checkout to a binder committed only on the linked worktree's branch.
        self.hard_link(self.root / "item-alias.txt", wt / ".karta/binders/item-only.json")
        # Positive control: a hard link across worktrees to a non-binder stays writable.
        self.hard_link(wt / "source-alias.txt", self.root / "source.txt")
        for cwd, path, want in ((wt, "alias.json", 2), (wt, str(wt / "alias.json"), 2),
                                (self.root, "item-alias.txt", 2), (wt, "source-alias.txt", 0),
                                (wt, ".karta/binders/draft.json", 0)):
            with self.subTest(cwd=cwd, path=path):
                self.check_path_from(cwd, path, want)

    def test_hard_link_to_a_non_binder_stays_writable(self):
        self.hard_link(self.root / "source-alias.txt", self.root / "source.txt")
        self.check_path("source-alias.txt", 0)
        self.check_path("source.txt", 0)

    def test_hard_link_denied_through_the_real_hook_payloads(self):
        self.hard_link(self.root / "alias.json", self.root / ".karta/binders/plan.json")
        claude = {"session_id": "fixture", "cwd": str(self.root), "hook_event_name": "PreToolUse",
                  "tool_name": "Write",
                  "tool_input": {"file_path": str(self.root / "alias.json"), "content": "{}"}}
        codex = {"session_id": "fixture", "cwd": str(self.root), "hook_event_name": "PreToolUse",
                 "tool_name": "apply_patch",
                 "tool_input": {"command": "*** Begin Patch\n*** Update File: alias.json\n@@\n-{}\n"
                                           "+{\"changed\": true}\n*** End Patch\n"}}
        for script, payload in ((claude_binder.__file__, claude), (codex_binder.__file__, codex)):
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, script], input=json.dumps(payload),
                                        capture_output=True, text=True)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("read-only", result.stderr)


class SecretDiffs(RepoCase):
    def test_header_lookalikes_inside_hunks_are_content(self):
        diff = "+++ b/demo.txt\n@@ -0,0 +1,3 @@\n+++ b/not-a-header\n+++token\n+end\n+++ b/next.txt\n@@ -0,0 +9 @@\n+next\n"
        self.assertEqual([("demo.txt", 1, "++ b/not-a-header"), ("demo.txt", 2, "++token"),
                          ("demo.txt", 3, "end"), ("next.txt", 9, "next")], secrets.parse_added_lines(diff))

    def test_staged_secret_with_two_leading_pluses_is_detected(self):
        self.write("secret.txt", "++ghp_" + "x" * 36 + "\n")
        self.git("add", "secret.txt")
        result = subprocess.run([sys.executable, secrets.__file__], cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("secret.txt", result.stdout)


    def scan_staged(self, files):
        for name, data in files.items():
            (self.root / name).write_bytes(data)
            self.git("add", name)
        return subprocess.run([sys.executable, secrets.__file__], cwd=self.root, capture_output=True, text=True)

    def test_unicode_line_breaks_inside_an_added_line_do_not_close_the_hunk(self):
        token = b"ghp_" + b"A" * 36
        for sep in (b"\f", b"\v", b"\r", "\u2028".encode(), "\u0085".encode(), b"\x1c"):
            with self.subTest(sep=sep):
                result = self.scan_staged({"secret.txt": b"normal\na" + sep + b"+b\n" + token + b"\n"})
                self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                self.assertIn("secret.txt:3", result.stdout)
                self.git("rm", "-q", "--cached", "secret.txt")

    def test_context_or_removal_lookalike_keeps_the_next_file_label(self):
        token = b"ghp_" + b"B" * 36
        result = self.scan_staged({"a.txt": b"x\v-y\nz\f q\n", "b.txt": token + b"\n"})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("b.txt:1", result.stdout)

    def test_invalid_utf8_is_still_scanned(self):
        token = b"ghp_" + b"C" * 36
        result = self.scan_staged({"bin.txt": b"\xff\xfe\n" + token + b"\n"})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("bin.txt:2", result.stdout)

    def test_counter_underflow_fails_closed(self):
        with self.assertRaises(ValueError):
            secrets.parse_added_lines("+++ b/a.txt\n@@ -1 +1 @@\n-a\n-b\n")

    def test_clean_multiline_diff_stays_clean(self):
        result = self.scan_staged({"ok.txt": b"one\ntwo\fthree\n"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("CLEAN", result.stdout)

    # User diff settings that change the text the parser reads. interHunkContext merges
    # two -U0 hunks across a blank line; suppressBlankEmpty prints that blank context
    # line as an empty line; the prefix settings rename the `+++ b/` header.
    USER_DIFF_CONFIG = (("diff.interHunkContext", "3"), ("diff.suppressBlankEmpty", "true"),
                        ("color.diff", "always"), ("color.ui", "always"),
                        ("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"),
                        ("diff.relative", "true"))

    def user_config(self, settings):
        for key, value in settings:
            self.git("config", key, value)

    def test_blank_context_inside_a_merged_hunk_is_clean_under_user_config(self):
        self.write("notes.txt", "alpha\n\nbeta\n")
        self.commit()
        self.user_config(self.USER_DIFF_CONFIG[:2])
        result = self.scan_staged({"notes.txt": b"ALPHA\n\nBETA\n"})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("CLEAN", result.stdout)

    def test_every_user_diff_setting_together_keeps_the_scan_exact(self):
        self.write("notes.txt", "alpha\n\nbeta\n")
        self.write("sub/keep.txt", "keep\n")
        self.commit()
        self.user_config(self.USER_DIFF_CONFIG)
        token = b"ghp_" + b"D" * 36
        result = self.scan_staged({"notes.txt": b"ALPHA\n\n" + token + b"\n"})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("notes.txt:3: github-token", result.stdout)
        self.assertNotIn("ERROR", result.stdout)
        # The scan reads the whole staged diff even when started in a subdirectory
        # under diff.relative=true.
        result = subprocess.run([sys.executable, secrets.__file__], cwd=self.root / "sub",
                                capture_output=True, text=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("notes.txt:3: github-token", result.stdout)

    def test_empty_line_inside_a_hunk_is_context(self):
        diff = "+++ b/n.txt\n@@ -1,3 +1,3 @@\n-alpha\n+ALPHA\n\n-beta\n+BETA\n"
        self.assertEqual([("n.txt", 1, "ALPHA"), ("n.txt", 3, "BETA")],
                         secrets.parse_added_lines(diff))


class DesignBoundary(RepoCase):
    def test_http_targets_and_directory_indexes_cannot_escape(self):
        self.write("design/good.html", "allowed")
        self.write("private.txt", "private marker")
        self.symlink("design/escape.html", self.root / "private.txt")
        self.symlink("design/outside", self.root, directory=True)
        self.write("design/sub/placeholder", "x")
        self.symlink("design/sub/index.html", self.root / "private.txt")
        handler = functools.partial(design.QuietHandler, directory=str(self.root / "design"))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            with urlopen(origin + "/good.html") as response:
                self.assertEqual(b"allowed", response.read())
            for path in ("/escape.html", "/outside/private.txt", "/sub/"):
                with self.subTest(path=path), self.assertRaises(HTTPError) as denied:
                    urlopen(origin + path)
                self.assertEqual(403, denied.exception.code)
            with self.assertRaises(HTTPError):
                urlopen(origin + "/%2e%2e/private.txt")
        finally:
            server.shutdown(); server.server_close(); thread.join()


class WatchBoundary(unittest.TestCase):
    def test_host_is_checked_before_state_assets_and_conditional_responses(self):
        class Handler(watch._Handler):
            required_key = "session-key"
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def hit(path, host, method="GET", headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
            conn.request(method, path, headers={"Host": host, **(headers or {})})
            response = conn.getresponse(); response.read(); conn.close()
            return response.status
        try:
            with patch.object(watch, "current_state", return_value={}), patch.object(watch, "split_archived", side_effect=lambda x: x):
                for path in ("/state.json?key=session-key", "/assets/vendor/vue.global.prod.js", "/?key=session-key"):
                    self.assertEqual(403, hit(path, f"evil.example:{server.server_port}"))
                    self.assertEqual(403, hit(path, f"evil.example:{server.server_port}", "HEAD"))
                self.assertEqual(200, hit("/state.json?key=session-key", f"localhost:{server.server_port}"))
                self.assertEqual(403, hit("/state.json", f"localhost:{server.server_port}", headers={"If-None-Match": "*"}))
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
