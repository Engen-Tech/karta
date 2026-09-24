"""Behavioral regressions for audit F10, F13 and F14 (status surfaces).

Every fixture is a real temporary git repo built with plain git; every Watch
check talks to a real serve_status.py process over loopback HTTP; every stop
guard check feeds the hook script the real Stop payload shape on stdin.

Set AUDIT_SOURCE_ROOT to run the same checks against a prior snapshot.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
NEXT = ROOT / "skills/karta-status/scripts/karta_next.py"
SERVE = ROOT / "skills/karta-status/scripts/serve_status.py"
STOP_GUARDS = {
    "claude": ROOT / "hooks/scripts/guard_delivery_stop.py",
    "codex": ROOT / ".codex-plugin/hooks/scripts/guard_delivery_stop.py",
}
ENGINE_REF_STATES = {"built", "done", "accepted", "failed", "evidence", "in-progress"}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(k, None)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid"})
    if extra:
        env.update(extra)
    return env


class Repo:
    """A throwaway git repo that fabricates delivery states the way the
    orchestrator writes them: integration branch, marked item commits, a
    --no-ff marked merge, and the done/built/accepted refs."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        (root / "README.md").write_text("seed\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "seed")

    def git(self, *args: str, check: bool = True) -> str:
        p = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                           text=True, encoding="utf-8", env=_env())
        if check and p.returncode != 0:
            raise AssertionError(f"git {args}: {p.stderr}")
        return p.stdout.strip()

    def binder(self, slug: str, ids: list[str], raw: str | None = None) -> None:
        d = self.root / ".karta" / "binders"
        d.mkdir(parents=True, exist_ok=True)
        body = raw if raw is not None else json.dumps(
            {"slug": slug, "motivation": "m", "scope": {"included": ["x"]},
             "work_items": [{"id": i, "title": i.upper(),
                             "oracle": {"type": "unit"}} for i in ids]})
        (d / f"{slug}.json").write_text(body, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", f"binder {slug}")

    def integration(self, slug: str) -> str:
        name = f"karta/{slug}/integration"
        if not self.git("rev-parse", "--verify", "--quiet", name, check=False):
            self.git("branch", name, "main")
        return name

    def ref(self, slug: str, item: str, state: str, sha: str) -> None:
        self.git("update-ref", f"refs/karta/{slug}/item-{item}/{state}", sha)

    def build(self, slug: str, item: str, built: bool = True) -> str:
        """Commit the item on its own branch off the integration tip."""
        integ = self.integration(slug)
        self.git("checkout", "-q", "-b", f"karta/{slug}/item-{item}", integ)
        (self.root / f"{item}.txt").write_text(f"{item}\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", f"[karta:item-{item}] {item} work")
        tip = self.git("rev-parse", "HEAD")
        if built:
            self.ref(slug, item, "built", tip)
        self.git("checkout", "-q", "main")
        return tip

    def deliver(self, slug: str, item: str, accept_reason: str | None = None) -> str:
        """Build and merge an item exactly as the merge queue (or an accept) does."""
        tip = self.build(slug, item, built=accept_reason is None)
        self.git("checkout", "-q", f"karta/{slug}/integration")
        if accept_reason is None:
            msg = f"Merge item {item} into integration [karta:item-{item}]"
        else:
            msg = (f"Accept item {item} into integration [karta:item-{item}]\n\n"
                   f"Karta-Accepted: item-{item}\n"
                   f"Karta-Accept-Reason: {accept_reason}")
        self.git("merge", "-q", "--no-ff", "-m", msg, f"karta/{slug}/item-{item}")
        merge = self.git("rev-parse", "HEAD")
        self.ref(slug, item, "done", merge)
        if accept_reason is not None:
            self.ref(slug, item, "accepted", tip)
        self.git("checkout", "-q", "main")
        return merge


def status_json(repo: Repo) -> dict:
    p = subprocess.run([sys.executable, str(NEXT), "--json"], cwd=repo.root,
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=_env({"KARTA_WATCH_STATE_DIR":
                                 str(repo.root.parent / "watch-state")}))
    if p.returncode != 0:
        raise AssertionError(f"karta_next --json exited {p.returncode}: {p.stderr[-800:]}")
    return json.loads(p.stdout)


def status_text(repo: Repo) -> str:
    p = subprocess.run([sys.executable, str(NEXT)], cwd=repo.root,
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=_env({"KARTA_WATCH_STATE_DIR":
                                 str(repo.root.parent / "watch-state")}))
    if p.returncode != 0:
        raise AssertionError(f"karta_next exited {p.returncode}: {p.stderr[-800:]}")
    return p.stdout


def item_row(state: dict, slug: str, item: str) -> dict:
    b = next(b for b in state["binders"] if b["slug"] == slug)
    return next(d for d in b["items"]["detail"] if d["id"] == item)


def stop(guard: Path, repo: Repo, session: str = "s1") -> tuple[int, str]:
    payload = {"hook_event_name": "Stop", "session_id": session, "cwd": str(repo.root),
               "stop_hook_active": False, "transcript_path": "/dev/null"}
    p = subprocess.run([sys.executable, str(guard)], input=json.dumps(payload),
                       capture_output=True, text=True, encoding="utf-8", timeout=120,
                       env=_env())
    return p.returncode, p.stderr


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-status-fix-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def repo(self, name: str = "r") -> Repo:
        return Repo(self.base / name)


# --------------------------------------------------------------------------- F10

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EphemeralWatch(Case):
    """Ephemeral Watch over real loopback HTTP."""

    def setUp(self):
        super().setUp()
        r = self.repo()
        r.binder("wip", ["a"])
        self.port = _free_port()
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVE), "--root", str(r.root), "--port", str(self.port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            env=_env({"KARTA_WATCH_STATE_DIR": str(self.base / "watch-state"),
                      "PYTHONUNBUFFERED": "1"}))
        self.addCleanup(self._stop)
        box: list[str] = []
        reader = threading.Thread(target=lambda: box.append(self.proc.stdout.readline()),
                                  daemon=True)
        reader.start()
        reader.join(30)
        self.banner = box[0] if box else ""
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        m = re.search(r"\?key=([^\s&]+)", self.banner)
        self.key = m.group(1) if m else None

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()

    def get(self, path: str, host: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest("GET", path, skip_host=True)
            conn.putheader("Host", host)
            conn.endheaders()
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def test_startup_prints_the_working_keyed_url(self):
        self.assertIsNotNone(self.key, f"no ?key= in the startup line: {self.banner!r}")
        self.assertIn(f"http://127.0.0.1:{self.port}/?key=", self.banner)
        status, body = self.get(f"/?key={self.key}", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 200)
        # the page's poll carries the same query string, so the key rides along
        self.assertIn(b"fetch('state.json' + location.search", body)
        status, body = self.get(f"/state.json?key={self.key}", f"localhost:{self.port}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["binders"][0]["slug"], "wip")

    def test_untrusted_host_is_rejected_even_with_the_key(self):
        self.assertIsNotNone(self.key)
        for path in (f"/state.json?key={self.key}", f"/?key={self.key}", "/assets/mascot.png"):
            status, body = self.get(path, f"untrusted.example:{self.port}")
            self.assertEqual(status, 403, path)
            self.assertNotIn(b"wip", body)

    def test_keyless_request_is_refused(self):
        status, body = self.get("/state.json", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 403)
        self.assertNotIn(b'"binders"', body)
        status, _ = self.get("/state.json?key=wrong", f"127.0.0.1:{self.port}")
        self.assertEqual(status, 403)


# --------------------------------------------------------------------------- F13

class WaiverVisible(Case):
    def test_accepted_item_is_distinct_from_passed_and_carries_its_reason(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.deliver("wip", "a", accept_reason="flaky e2e waived by the reviewer")
        r.deliver("wip", "b")
        st = status_json(r)
        a, b = item_row(st, "wip", "a"), item_row(st, "wip", "b")
        self.assertEqual(a["status"], "accepted")
        self.assertEqual(a.get("waiver_reason"), "flaky e2e waived by the reviewer")
        self.assertEqual(b["status"], "done")          # positive control: clean done
        self.assertNotIn("waiver_reason", b)
        self.assertEqual(st["warnings"], [])
        text = status_text(r)
        self.assertRegex(text, r"a\s+accepted")
        self.assertIn("flaky e2e waived by the reviewer", text)

    def test_watch_metadata_never_badges_accepted_as_passed(self):
        serve = load("audit_status_serve", SERVE)
        meta = serve._STATE_META.get("accepted")
        self.assertIsNotNone(meta, "no Watch treatment for the accepted state")
        self.assertNotEqual(meta["word"], serve._STATE_META["done"]["word"])
        self.assertIn("accepted", serve._ENGINE_ITEM_STATES)
        rows = serve.item_detail({"id": "a", "status": "accepted", "oracle": "unit",
                                  "waiver_reason": "human said so"}, "wip", {})
        waiver = [row for row in rows if row["key"] == "waiver"]
        self.assertEqual(len(waiver), 1)
        self.assertEqual(waiver[0]["text"], "human said so")


class SuspectCompletion(Case):
    """Forged completion refs (evidence cases F1-F4) must not read as done."""

    def assertSuspect(self, st: dict, item: str, pattern: str = "suspect"):
        hits = [w for w in st["warnings"] if item in w and re.search(pattern, w)]
        self.assertTrue(hits, f"no suspect warning for {item}: {st['warnings']}")

    def test_f1_done_ref_off_the_integration_chain(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.integration("wip")
        tip = r.build("wip", "a")
        r.ref("wip", "a", "done", tip)            # forged: never merged
        st = status_json(r)
        self.assertNotIn(item_row(st, "wip", "a")["status"], ("done", "accepted"))
        self.assertSuspect(st, "a")
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                code, reason = stop(guard, r, session=f"f1-{name}")
                self.assertEqual(code, 2, reason)
                self.assertIn("a", reason)

    def test_f2_accepted_ref_without_waiver_trailers(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        merge = r.deliver("wip", "a")              # a clean merge, no trailers
        r.ref("wip", "a", "accepted", r.git("rev-parse", "karta/wip/item-a"))
        st = status_json(r)
        self.assertNotIn(item_row(st, "wip", "a")["status"], ("done", "accepted"))
        self.assertSuspect(st, "a")
        self.assertTrue(merge)

    def test_f3_done_ref_on_an_unmarked_commit(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.integration("wip")
        head = r.git("rev-parse", "HEAD")
        r.ref("wip", "a", "built", head)
        r.ref("wip", "a", "done", head)
        st = status_json(r)
        self.assertNotIn(item_row(st, "wip", "a")["status"], ("done", "accepted"))
        self.assertSuspect(st, "a")
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                code, reason = stop(guard, r, session=f"f3-{name}")
                self.assertEqual(code, 2, reason)

    def test_f4_done_ref_for_an_item_not_in_the_binder(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.git("update-ref", "refs/karta/wip/item-ghost/done", r.git("rev-parse", "HEAD"))
        st = status_json(r)
        self.assertTrue([w for w in st["warnings"] if "ghost" in w and "not in" in w],
                        st["warnings"])

    def test_clean_delivery_draws_no_warning_and_passes_the_stop_guard(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.deliver("wip", "a")
        st = status_json(r)
        self.assertEqual(item_row(st, "wip", "a")["status"], "done")
        self.assertEqual(st["warnings"], [])
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                self.assertEqual(stop(guard, r, session=f"clean-{name}")[0], 0)


    def _bare_forged_done(self, r: Repo, item: str) -> None:
        """A done ref on an unmarked commit, with no built or accepted ref."""
        (r.root / f"{item}-forged.txt").write_text("forged\n", encoding="utf-8")
        r.git("add", "-A")
        r.git("commit", "-qm", "unmarked change")
        r.ref("wip", item, "done", r.git("rev-parse", "HEAD"))

    def test_bare_forged_done_that_completes_the_binder_blocks_the_stop(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.deliver("wip", "a")
        self._bare_forged_done(r, "b")
        st = status_json(r)
        self.assertNotIn(item_row(st, "wip", "b")["status"], ("done", "accepted"))
        self.assertSuspect(st, "b")
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                code, reason = stop(guard, r, session=f"bare-{name}")
                self.assertEqual(code, 2, reason)
                self.assertIn("items b", reason)
                self.assertIn("provenance", reason)

    def test_bare_forged_done_mid_delivery_blocks_the_stop(self):
        r = self.repo()
        r.binder("wip", ["a", "b", "c"])
        r.deliver("wip", "a")
        self._bare_forged_done(r, "b")
        st = status_json(r)
        self.assertSuspect(st, "b")
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                code, reason = stop(guard, r, session=f"bare-mid-{name}")
                self.assertEqual(code, 2, reason)
                self.assertIn("items b", reason)


class StopGuardVocabulary(Case):
    def test_ref_vocabulary_matches_the_engine(self):
        for name, path in STOP_GUARDS.items():
            with self.subTest(guard=name):
                guard = load(f"audit_stop_{name}", path)
                self.assertEqual(set(guard.REF_STATES), ENGINE_REF_STATES)

    def test_accepted_ref_with_no_done_merge_is_stranded(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        tip = r.build("wip", "a", built=False)
        r.ref("wip", "a", "accepted", tip)
        for name, guard in STOP_GUARDS.items():
            with self.subTest(guard=name):
                code, reason = stop(guard, r, session=f"acc-{name}")
                self.assertEqual(code, 2, reason)
                self.assertIn("accepted", reason)


# --------------------------------------------------------------------------- F14

class BinderLoadErrors(Case):
    def test_non_object_and_malformed_binders_are_reported_per_file(self):
        r = self.repo()
        r.binder("good", ["a"])
        r.binder("listy", [], raw='["corrupt","non-dict","binder"]')
        r.binder("broken", [], raw="{not json")
        st = status_json(r)
        self.assertEqual([b["slug"] for b in st["binders"]], ["good"])
        errs = "\n".join(st["errors"])
        self.assertIn("listy.json", errs)
        self.assertIn("broken.json", errs)
        self.assertIsNone(st["next_action"]["command"])
        self.assertIn("listy.json", st["next_action"]["human"])

    def test_load_binders_returns_errors_instead_of_dropping(self):
        nxt = load("audit_status_next", NEXT)
        d = self.base / "binders"
        d.mkdir()
        (d / "ok.json").write_text(json.dumps({"slug": "ok", "work_items": []}), encoding="utf-8")
        (d / "bad.json").write_text("[1, 2]", encoding="utf-8")
        binders, errors = nxt.load_binders(d)
        self.assertEqual([b["slug"] for b in binders], ["ok"])
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.json", errors[0])


class SuccessorBinderDisplay(Case):
    """F12 follow-through: a successor binder's carried items are done by the
    predecessor, proven by the predecessor's own done refs, and a live
    predecessor that a live successor supersedes is not open work."""

    def successor(self, r: Repo, carried=("a",)):
        body = {"slug": "wip-r2", "motivation": "m", "scope": {"included": ["x"]},
                "supersedes": {"slug": "wip", "carried": list(carried)},
                "work_items": [{"id": "a", "title": "A", "oracle": {"type": "unit"}},
                               {"id": "c", "title": "C", "depends_on": ["a"],
                                "oracle": {"type": "unit"}}]}
        r.binder("wip-r2", [], raw=json.dumps(body))

    def test_carried_item_is_done_by_the_predecessor(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.deliver("wip", "a")
        self.successor(r)
        st = status_json(r)
        a = item_row(st, "wip-r2", "a")
        self.assertEqual(a["status"], "done")
        self.assertEqual(a.get("carried_from"), "wip")
        self.assertEqual(item_row(st, "wip-r2", "c")["status"], "ready")
        succ = next(b for b in st["binders"] if b["slug"] == "wip-r2")
        self.assertEqual(succ["status"], "not_started")
        pred = next(b for b in st["binders"] if b["slug"] == "wip")
        self.assertEqual(pred.get("superseded_by"), "wip-r2")
        self.assertFalse(pred.get("is_next"))
        self.assertEqual(st["next_action"]["command"], "karta-deliver wip-r2")
        self.assertIn("superseded by wip-r2", status_text(r))

    def test_forged_predecessor_done_is_not_carried(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.integration("wip")
        head = r.git("rev-parse", "HEAD")
        r.ref("wip", "a", "done", head)          # unmarked commit: fails provenance
        self.successor(r)
        st = status_json(r)
        a = item_row(st, "wip-r2", "a")
        self.assertNotIn(a["status"], ("done", "accepted"))
        self.assertNotIn("carried_from", a)
        self.assertEqual(item_row(st, "wip-r2", "c")["status"], "blocked")
        self.assertTrue([w for w in st["warnings"] if "wip-r2" in w and "carried" in w],
                        st["warnings"])

    def test_page_detail_points_a_carried_item_at_the_predecessor(self):
        serve = load("status_fix_serve_carried", SERVE)
        rows = {row["key"]: row for row in serve.item_detail(
            {"id": "a", "status": "done", "oracle": "unit", "carried_from": "wip"},
            "wip-r2", {})}
        self.assertIn("wip", rows["carried"]["text"])
        self.assertEqual(rows["ref"]["text"], "refs/karta/wip/item-a/done")
        plain = {row["key"] for row in serve.item_detail(
            {"id": "a", "status": "done", "oracle": "unit"}, "wip", {})}
        self.assertNotIn("carried", plain)


class RecoveryNextSteps(Case):
    def test_s1_built_but_unmerged_resumes_the_merge_queue(self):
        r = self.repo()
        r.binder("wip", ["a", "b"])
        r.deliver("wip", "a")
        r.build("wip", "b")
        na = status_json(r)["next_action"]
        self.assertEqual(na["command"], "karta-deliver wip")
        self.assertIn("b", na["human"])
        self.assertIn("built", na["human"])

    def test_s2_awaiting_landing_names_the_human_decision_and_runs_nothing(self):
        r = self.repo()
        r.binder("wip", ["a"])
        r.deliver("wip", "a")
        na = status_json(r)["next_action"]
        self.assertEqual(na["level"], "landing")
        self.assertIsNone(na["command"])
        self.assertIn("karta/wip/integration", na["human"])
        self.assertIn("merge it yourself", na["human"])
        self.assertNotIn("LANDING_APPROVED", json.dumps(na))

    def test_s6_binder_deleted_from_the_working_tree(self):
        r = self.repo()
        r.binder("wip", ["a"])
        (r.root / ".karta/binders/wip.json").unlink()
        st = status_json(r)
        self.assertTrue([w for w in st["warnings"] if "wip" in w and "working tree" in w],
                        st["warnings"])
        self.assertIn(".karta/binders/wip.json", st["next_action"]["command"] or "")

    def _landed(self, r: Repo) -> None:
        r.binder("wip", ["a"])
        r.deliver("wip", "a")
        r.git("checkout", "-q", "karta/wip/integration")
        (r.root / ".karta/binders/archive").mkdir(parents=True)
        r.git("mv", ".karta/binders/wip.json", ".karta/binders/archive/wip.json")
        r.git("commit", "-qm", "chore(karta): archive binder wip — delivered")
        r.git("checkout", "-q", "main")
        r.git("merge", "-q", "--no-ff", "-m", "land wip", "karta/wip/integration")

    def test_s7_leftover_branches_after_landing_get_a_cleanup_step(self):
        r = self.repo()
        self._landed(r)
        na = status_json(r)["next_action"]
        self.assertEqual(na["level"], "cleanup")
        self.assertIn("git branch -d", na["command"])
        self.assertIn("karta/wip/integration", na["command"])
        self.assertIn("karta/wip/item-a", na["command"])

    def test_s8_mounted_worktree_is_part_of_the_cleanup(self):
        r = self.repo()
        self._landed(r)
        wt = self.base / "wt-item-a"
        r.git("worktree", "add", "-q", str(wt), "karta/wip/item-a")
        na = status_json(r)["next_action"]
        self.assertEqual(na["level"], "cleanup")
        self.assertIn("git worktree remove", na["command"])
        self.assertIn(str(wt), na["command"])

    def test_landed_and_clean_is_calm_done(self):
        r = self.repo()
        self._landed(r)
        r.git("branch", "-D", "karta/wip/integration", "karta/wip/item-a")
        na = status_json(r)["next_action"]
        self.assertEqual(na["level"], "done")

    def test_empty_repo_points_at_planning_not_at_missing_warnings(self):
        r = self.repo()
        st = status_json(r)
        na = st["next_action"]
        self.assertEqual((st["warnings"], st["errors"]), ([], []))
        self.assertEqual(na["level"], "empty")
        self.assertEqual(na["command"], "karta-plan")
        self.assertNotIn("warnings/errors", na["human"])

    def test_watch_page_lists_engine_errors(self):
        serve = load("audit_status_serve_page", SERVE)
        state = {"repo": {"default_branch": "main"}, "order": [], "binders": [],
                 "next_action": {"level": "error", "command": None, "human": "fix it"},
                 "warnings": ["w-one"], "errors": ["e-one"]}
        page = serve.render_app_html(state, None, repo_name="r")
        self.assertIn("data-kw-notices", page)
        self.assertRegex(page, r"v-for=\"[^\"]*in notices")


# --------------------------------------------------------------------------- F20 (repair)

class DefaultBranchAgreement(Case):
    """Status and both Stop guards resolve the default branch the way
    deliver_preflight does: config, local origin/HEAD, then one unambiguous
    local branch. Before this they ignored the config and guessed main."""

    def trunk_repo(self, configure: bool, extra_branch: bool = True) -> Repo:
        r = Repo(self.base / "t")
        r.git("branch", "-m", "main", "trunk")
        if extra_branch:
            r.git("branch", "feature")
        if configure:
            r.git("config", "karta.defaultBranch", "trunk")
        return r

    def guard_revs(self, repo: Repo) -> dict[str, list[str]]:
        return {name: load(f"stop_{name}_{id(repo)}", path)._default_branch_revs(str(repo.root))
                for name, path in STOP_GUARDS.items()}

    def test_configured_trunk_is_the_default_everywhere(self):
        r = self.trunk_repo(configure=True)
        self.assertEqual("trunk", status_json(r)["repo"]["default_branch"])
        for name, revs in self.guard_revs(r).items():
            with self.subTest(guard=name):
                self.assertEqual("refs/heads/trunk", revs[0])

    def test_sole_local_branch_is_the_default_everywhere(self):
        r = self.trunk_repo(configure=False, extra_branch=False)
        r.git("branch", "karta/x/integration")
        self.assertEqual("trunk", status_json(r)["repo"]["default_branch"])
        for name, revs in self.guard_revs(r).items():
            with self.subTest(guard=name):
                self.assertEqual("refs/heads/trunk", revs[0])

    def test_origin_head_keeps_a_slashed_branch_name(self):
        r = self.trunk_repo(configure=False)
        r.git("update-ref", "refs/remotes/origin/release/2026", "HEAD")
        r.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release/2026")
        self.assertEqual("release/2026", status_json(r)["repo"]["default_branch"])
        for name, revs in self.guard_revs(r).items():
            with self.subTest(guard=name):
                self.assertIn("refs/remotes/origin/release/2026", revs)

    def test_conventional_main_still_resolves(self):
        r = self.repo()
        r.git("branch", "feature")
        self.assertEqual("main", status_json(r)["repo"]["default_branch"])
        for name, revs in self.guard_revs(r).items():
            with self.subTest(guard=name):
                self.assertEqual(["refs/heads/main"], revs)

    def test_archive_committed_only_on_configured_trunk_is_seen_by_the_guard(self):
        r = self.trunk_repo(configure=True)
        d = r.root / ".karta" / "binders" / "archive"
        d.mkdir(parents=True)
        (d / "done.json").write_text("{}\n", encoding="utf-8")
        r.git("add", "-A")
        r.git("commit", "-qm", "archive")
        r.git("checkout", "-q", "feature")
        for name, path in STOP_GUARDS.items():
            with self.subTest(guard=name):
                mod = load(f"stop_arch_{name}", path)
                self.assertTrue(mod._archive_committed(str(r.root), "done"))


if __name__ == "__main__":
    unittest.main()
