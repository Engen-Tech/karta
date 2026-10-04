"""Behavioral regressions for audit F11, F12 and F20; fixtures never edit the repo.

F11 — binder-schema.json is the one structural contract: raw JSON-schema validation
      and validate_binder.py give the same answer, with no runtime schema additions.
F12 — a partially delivered binder is repaired by a successor binder (`supersedes`),
      never by editing the committed plan; deliver_preflight.py proves carried work.
F20 — default-branch discovery is local, bounded, and refuses to guess.

Set AUDIT_SOURCE_ROOT to run the same negative controls against a prior snapshot.
"""
from __future__ import annotations

import copy
import importlib.util
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
SCHEMA = ROOT / "skills/karta-plan/references/binder-schema.json"
VALIDATE = ROOT / "skills/karta-plan/scripts/validate_binder.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vb = load("audit_pd_validate_binder", "skills/karta-plan/scripts/validate_binder.py")
pre = load("audit_pd_preflight", "skills/karta-deliver/scripts/deliver_preflight.py")
terms = load("audit_pd_shared_terms", "skills/karta-plan/scripts/check_shared_terms.py")


def item(iid, **extra):
    base = {"id": iid, "title": iid.upper(), "summary": "s", "touches": [f"{iid}.txt"],
            "oracle": {"type": "unit", "command": "true"}}
    base.update(extra)
    return base


def binder(slug, items, **extra):
    doc = {"slug": slug, "title": "T", "summary": "s", "motivation": "m",
           "scope": {"included": ["x"]}, "work_items": items}
    doc.update(extra)
    return doc


# --- F11: one schema, two checkers that agree ----------------------------------

_JSONSCHEMA_SCRIPT = r"""
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open(sys.argv[1], encoding="utf-8"))
Draft202012Validator.check_schema(schema)
doc = json.load(open(sys.argv[2], encoding="utf-8"))
errs = list(Draft202012Validator(schema).iter_errors(doc))
print("VALID" if not errs else "INVALID " + "; ".join(e.message for e in errs))
"""


class SchemaAgreement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-f11-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _file(self, doc):
        p = self.dir / f"{doc['slug']}.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p

    def raw_jsonschema(self, doc) -> bool:
        """Validate against the schema FILE with the real jsonschema library."""
        uv = shutil.which("uv")
        if not uv:
            self.skipTest("uv unavailable: cannot provision the jsonschema library")
        env = dict(os.environ)
        env.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "gpt-uv-cache"))
        p = subprocess.run([uv, "run", "--quiet", "--with", "jsonschema", "python", "-c",
                            _JSONSCHEMA_SCRIPT, str(SCHEMA), str(self._file(doc))],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)
        if p.returncode != 0 or not p.stdout.strip():
            self.skipTest(f"jsonschema could not run (network or cache?): {p.stderr[-400:]}")
        return p.stdout.startswith("VALID")

    def raw_stdlib(self, doc) -> bool:
        """validate_binder's own checker, fed the schema file exactly as it is on disk."""
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        errors: list[str] = []
        vb._check(doc, schema, schema, [], errors)
        return not errors

    def cli(self, doc) -> bool:
        p = subprocess.run([sys.executable, str(VALIDATE), "--binder", str(self._file(doc))],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        return p.returncode == 0

    def verdicts(self, doc):
        return {"jsonschema": self.raw_jsonschema(doc), "stdlib-raw": self.raw_stdlib(doc),
                "validate_binder": self.cli(doc)}

    def test_shared_terms_binder_accepted_by_raw_schema_and_validator(self):
        doc = binder("st", [item("alpha"), item("beta")],
                     shared_terms=[{"id": "t", "canonical": "X", "items": ["alpha", "beta"]}])
        self.assertEqual(self.verdicts(doc), dict.fromkeys(
            ("jsonschema", "stdlib-raw", "validate_binder"), True))

    def test_malformed_shared_terms_rejected_by_raw_schema_too(self):
        doc = binder("st-bad", [item("alpha"), item("beta")],
                     shared_terms=[{"id": "t", "canonical": "X", "items": ["alpha"]}])
        self.assertEqual(self.verdicts(doc), dict.fromkeys(
            ("jsonschema", "stdlib-raw", "validate_binder"), False))

    def test_ui_fields_on_non_ui_item_rejected_by_raw_schema_and_validator(self):
        for field, value in (("component_map", [{"name": "DataTable"}]),
                             ("icon_map", [{"name": "database"}]),
                             ("token_changes", [{"name": "color.accent"}])):
            with self.subTest(field=field):
                doc = binder("ui-on-backend", [item("migration", **{field: value})])
                self.assertEqual(self.verdicts(doc), dict.fromkeys(
                    ("jsonschema", "stdlib-raw", "validate_binder"), False))

    def test_ui_fields_on_ui_item_accepted_positive_control(self):
        doc = binder("ui-ok", [item("screen", design_reference="none",
                                    component_map=[{"name": "Card"}], icon_map=[],
                                    token_changes=[])])
        self.assertEqual(self.verdicts(doc), dict.fromkeys(
            ("jsonschema", "stdlib-raw", "validate_binder"), True))

    def test_undeclared_key_rejected_by_both_control(self):
        doc = binder("bogus", [item("a")], bogus=True)
        self.assertEqual(self.verdicts(doc), dict.fromkeys(
            ("jsonschema", "stdlib-raw", "validate_binder"), False))

    def test_validator_adds_nothing_to_the_schema_at_runtime(self):
        loaded = vb._load_schema()
        self.assertEqual(loaded, json.loads(SCHEMA.read_text(encoding="utf-8")))
        self.assertIn("shared_terms", loaded["properties"])
        self.assertFalse(hasattr(vb, "_SHARED_TERMS_SCHEMA"),
                         "a second copy of a schema fragment lives in the validator")
        doc = binder("st", [item("alpha"), item("beta")],
                     shared_terms=[{"id": "t", "canonical": "X", "items": ["alpha", "beta"]}])
        with patch.object(vb, "_load_schema", lambda: json.loads(SCHEMA.read_text(encoding="utf-8"))):
            self.assertEqual(vb._schema_errors(doc), [])

    def test_supersedes_declared_in_schema_and_accepted(self):
        doc = binder("s-r2", [item("a"), item("b", depends_on=["a"])],
                     supersedes={"slug": "s", "carried": ["a"]})
        self.assertEqual(self.verdicts(doc), dict.fromkeys(
            ("jsonschema", "stdlib-raw", "validate_binder"), True))

    def test_supersedes_shape_errors(self):
        cases = {
            "no carried": {"slug": "s"},
            "empty carried": {"slug": "s", "carried": []},
            "duplicate carried": {"slug": "s", "carried": ["a", "a"]},
            "bad slug": {"slug": "S R", "carried": ["a"]},
        }
        for name, sup in cases.items():
            with self.subTest(name):
                doc = binder("s-r2", [item("a"), item("b")], supersedes=sup)
                self.assertEqual(self.verdicts(doc), dict.fromkeys(
                    ("jsonschema", "stdlib-raw", "validate_binder"), False))

    def test_supersedes_cross_reference_errors(self):
        dangling = binder("s-r2", [item("a")], supersedes={"slug": "s", "carried": ["ghost"]})
        self_ref = binder("s-r2", [item("a")], supersedes={"slug": "s-r2", "carried": ["a"]})
        self.assertTrue(any("supersedes" in e for e in vb.validate_binder(dangling)))
        self.assertTrue(any("supersedes" in e for e in vb.validate_binder(self_ref)))
        self.assertFalse(self.cli(dangling))

    def test_dropped_is_declared_and_accepted(self):
        doc = binder("s-r2", [item("a"), item("c")], supersedes={"slug": "s", "carried": ["a"], "dropped": ["b"]})
        self.assertEqual(self.verdicts(doc), dict.fromkeys(("jsonschema", "stdlib-raw", "validate_binder"), True))

    def test_duplicate_dropped_rejected_by_both(self):
        doc = binder("s-r2", [item("a")], supersedes={"slug": "s", "carried": ["a"], "dropped": ["b", "b"]})
        self.assertEqual(self.verdicts(doc), dict.fromkeys(("jsonschema", "stdlib-raw", "validate_binder"), False))

    def test_an_id_cannot_be_both_carried_and_dropped(self):
        doc = binder("s-r2", [item("a")], supersedes={"slug": "s", "carried": ["a"], "dropped": ["a"]})
        self.assertTrue(any("dropped" in e for e in vb.validate_binder(doc)))
        self.assertFalse(self.cli(doc))


# --- shared git fixture helpers --------------------------------------------------

class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-pd-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", "-C", str(cwd or self.root), *args], text=True,
                                       encoding="utf-8", stderr=subprocess.STDOUT).strip()

    def init(self, branch="main", root=None):
        root = root or self.root
        self.git("init", "-q", "-b", branch, cwd=root)
        self.git("config", "user.email", "fixture@example.invalid", cwd=root)
        self.git("config", "user.name", "Regression fixture", cwd=root)
        self.git("config", "core.autocrlf", "false", cwd=root)

    def write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def commit(self, msg="fixture"):
        self.git("add", "-A")
        self.git("commit", "-qm", msg, "--allow-empty")
        return self.git("rev-parse", "HEAD")


# --- F12: successor binders ------------------------------------------------------

class SuccessorFixture(RepoCase):
    """A predecessor `s` delivered item a (a real no-ff merge + done ref); the
    successor `s-r2` carries a and plans c on top of it."""

    def setUp(self):
        super().setUp()
        self.init()
        self.pred = binder("s", [item("a"), item("b", depends_on=["a"])])
        self.write(".karta/binders/s.json", json.dumps(self.pred, indent=1))
        self.commit("plan s")
        self.git("checkout", "-q", "-b", "karta/s/integration")
        self.git("checkout", "-q", "-b", "karta/s/item-a")
        self.write("a.txt", "a\n")
        self.commit("the work [karta:item-a]")
        self.git("checkout", "-q", "karta/s/integration")
        self.git("merge", "-q", "--no-ff", "karta/s/item-a",
                 "-m", "karta: merge item-a [karta:item-a]")
        self.done_a = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/karta/s/item-a/done", self.done_a)
        self.pred_tip = self.done_a
        self.git("checkout", "-q", "main")
        self.succ = binder("s-r2", [copy.deepcopy(self.pred["work_items"][0]),
                                    item("c", depends_on=["a"])],
                           supersedes={"slug": "s", "carried": ["a"]})

    def packet(self, doc=None):
        path = self.base / "succ.json"
        path.write_text(json.dumps(doc or self.succ), encoding="utf-8")
        return pre.build_packet(path, self.root)


class SuccessorBinder(SuccessorFixture):
    def test_successor_with_proven_carried_item_starts_from_predecessor_tip(self):
        p = self.packet()
        self.assertFalse(p["halt"], json.dumps(p.get("supersedes"), indent=1))
        self.assertEqual(p["frontier"], ["c"])
        self.assertEqual(p["integration_base"],
                         {"ref": "karta/s/integration", "sha": self.pred_tip})
        self.assertTrue(p["supersedes"]["ok"])
        self.assertTrue(p["supersedes"]["carried"]["a"]["ok"])

    def test_plain_binder_starts_from_default_branch_control(self):
        path = self.base / "plain.json"
        path.write_text(json.dumps(binder("plain", [item("a")])), encoding="utf-8")
        p = pre.build_packet(path, self.root)
        self.assertFalse(p["halt"])
        self.assertIsNone(p["supersedes"])
        self.assertEqual(p["integration_base"]["ref"], "main")

    def test_carried_item_without_predecessor_done_ref_halts(self):
        self.git("update-ref", "-d", "refs/karta/s/item-a/done")
        p = self.packet()
        self.assertTrue(p["halt"])
        self.assertFalse(p["supersedes"]["ok"])
        self.assertNotIn("c", p["frontier"])

    def test_carried_item_with_off_chain_done_ref_halts(self):
        self.git("checkout", "-q", "-b", "side", "karta/s/item-a")
        forged = self.commit("karta: forged merge [karta:item-a]")
        self.git("checkout", "-q", "main")
        self.git("update-ref", "refs/karta/s/item-a/done", forged)
        p = self.packet()
        self.assertTrue(p["halt"])
        self.assertFalse(p["supersedes"]["carried"]["a"]["first_parent_reachable"])

    def test_missing_predecessor_integration_branch_halts(self):
        self.git("branch", "-D", "karta/s/integration")
        p = self.packet()
        self.assertTrue(p["halt"])

    def test_carried_item_that_differs_from_the_predecessor_plan_halts(self):
        doc = copy.deepcopy(self.succ)
        doc["work_items"][0]["oracle"] = {"type": "unit", "command": "make other"}
        p = self.packet(doc)
        self.assertTrue(p["halt"])
        self.assertFalse(p["supersedes"]["ok"])

    def test_successor_integration_missing_predecessor_work_halts(self):
        self.git("branch", "karta/s-r2/integration", "main")
        p = self.packet()
        self.assertTrue(p["halt"])

    def test_successor_integration_started_from_predecessor_tip_passes(self):
        self.git("branch", "karta/s-r2/integration", "karta/s/integration")
        p = self.packet()
        self.assertFalse(p["halt"], json.dumps(p.get("supersedes"), indent=1))
        self.assertEqual(p["frontier"], ["c"])

    def test_predecessor_archived_on_its_integration_branch_still_resolves(self):
        self.git("checkout", "-q", "karta/s/integration")
        (self.root / ".karta/binders/archive").mkdir(parents=True)
        self.git("mv", ".karta/binders/s.json", ".karta/binders/archive/s.json")
        self.commit("chore(karta): archive binder s — superseded by s-r2")
        self.git("checkout", "-q", "main")
        p = self.packet()
        self.assertTrue(p["supersedes"]["ok"], json.dumps(p["supersedes"], indent=1))

    def test_shared_terms_counts_a_carried_item_as_landed(self):
        doc = copy.deepcopy(self.succ)
        doc["shared_terms"] = [{"id": "t", "canonical": "a", "items": ["a", "c"]}]
        self.git("checkout", "-q", "karta/s/integration")
        self.assertTrue(terms._landed("s", "a", self.root))
        # the carried item landed under the predecessor's slug, so it is not pending
        results = {r[0]: r for r in terms.evaluate_binder(doc, self.root)}
        self.assertEqual(results["t"][1], "PENDING")
        self.assertEqual(results["t"][2], ["c"])


class UncarriedDoneWork(SuccessorFixture):
    """F12 repair: the successor starts from the predecessor's integration tip, so a
    done predecessor item that is not carried still has its code on that branch. The
    successor must name it (carry it, or drop it for a planned revert) or preflight halts."""

    def setUp(self):
        super().setUp()
        self.git("checkout", "-q", "karta/s/integration")
        self.git("checkout", "-q", "-b", "karta/s/item-b")
        self.write("b.txt", "b\n")
        self.commit("the work [karta:item-b]")
        self.git("checkout", "-q", "karta/s/integration")
        self.git("merge", "-q", "--no-ff", "karta/s/item-b",
                 "-m", "karta: merge item-b [karta:item-b]")
        self.done_b = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/karta/s/item-b/done", self.done_b)
        self.pred_tip = self.done_b
        self.git("checkout", "-q", "main")

    def test_done_item_neither_carried_nor_dropped_halts(self):
        p = self.packet()
        self.assertTrue(p["halt"])
        self.assertFalse(p["supersedes"]["ok"])
        self.assertTrue(any("'b'" in f for f in p["supersedes"]["findings"]),
                        p["supersedes"]["findings"])

    def test_dropped_item_is_named_with_its_merge_and_does_not_halt(self):
        doc = copy.deepcopy(self.succ)
        doc["supersedes"]["dropped"] = ["b"]
        p = self.packet(doc)
        self.assertFalse(p["halt"], json.dumps(p["supersedes"], indent=1))
        self.assertEqual(p["supersedes"]["dropped"], {"b": self.done_b})

    def test_dropped_id_without_a_predecessor_done_ref_halts(self):
        doc = copy.deepcopy(self.succ)
        doc["supersedes"]["dropped"] = ["b", "ghost"]
        p = self.packet(doc)
        self.assertTrue(p["halt"])
        self.assertTrue(any("'ghost'" in f for f in p["supersedes"]["findings"]))

    def test_carrying_every_done_item_is_the_positive_control(self):
        doc = copy.deepcopy(self.succ)
        doc["work_items"] = [copy.deepcopy(i) for i in self.pred["work_items"]] + [item("c", depends_on=["a"])]
        doc["supersedes"]["carried"] = ["a", "b"]
        p = self.packet(doc)
        self.assertFalse(p["halt"], json.dumps(p["supersedes"], indent=1))


class RepairDoctrine(unittest.TestCase):
    def test_between_wave_edit_instruction_is_gone(self):
        text = (ROOT / "skills/karta-deliver/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("edit the binder between waves", text)
        self.assertIn("supersedes", text)

    def test_repair_how_to_exists(self):
        doc = ROOT / "docs/how-to/binder-repair.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        for needle in ("supersedes", "-r2", "carried", "archive"):
            self.assertIn(needle, text)


# --- F20: default-branch discovery ----------------------------------------------

class DefaultBranch(RepoCase):
    def test_local_only_single_trunk_branch(self):
        self.init("trunk")
        self.commit()
        self.assertEqual(pre.detect_default_branch(self.root), "trunk")

    def test_explicit_git_config_wins(self):
        self.init("main")
        self.commit()
        self.git("branch", "develop")
        self.git("config", "karta.defaultBranch", "develop")
        self.assertEqual(pre.detect_default_branch(self.root), "develop")

    def test_configured_branch_that_does_not_exist_is_an_error_not_a_guess(self):
        self.init("main")
        self.commit()
        self.git("config", "karta.defaultBranch", "nope")
        name, problem = pre.resolve_default_branch(self.root)
        self.assertIsNone(name)
        self.assertIn("nope", problem)

    def test_local_origin_head_symbolic_ref(self):
        origin = self.base / "origin"
        origin.mkdir()
        self.init("develop", root=origin)
        self.git("commit", "-q", "--allow-empty", "-m", "o", cwd=origin)
        self.git("branch", "main", cwd=origin)
        subprocess.check_call(["git", "clone", "-q", str(origin), str(self.root / "c")])
        clone = self.root / "c"
        subprocess.check_call(["git", "-C", str(clone), "branch", "-q", "main", "origin/main"])
        self.assertEqual(pre.detect_default_branch(clone), "develop")

    def test_no_network_call_during_discovery(self):
        self.init("main")
        self.commit()
        self.git("branch", "other")
        marker = self.base / "network-was-touched"
        self.git("config", "protocol.ext.allow", "always")
        self.git("remote", "add", "origin", f"ext::sh -c touch% {marker}% ;false")
        self.assertEqual(pre.detect_default_branch(self.root), "main")
        self.assertFalse(marker.exists(), "default-branch discovery contacted the remote")

    def test_ambiguous_main_and_master_refuses_to_guess(self):
        self.init("main")
        self.commit()
        self.git("branch", "master")
        name, problem = pre.resolve_default_branch(self.root)
        self.assertIsNone(name)
        self.assertIn("git config karta.defaultBranch", problem)

    def test_ambiguous_branches_halt_the_packet_with_the_fix(self):
        self.init("feature-x")
        self.commit()
        self.git("branch", "trunk")
        path = self.base / "b.json"
        path.write_text(json.dumps(binder("x", [item("a")])), encoding="utf-8")
        p = pre.build_packet(path, self.root)
        self.assertIsNone(p["default_branch"])
        self.assertTrue(p["halt"])
        self.assertIn("karta.defaultBranch", p["default_branch_error"])

    def test_every_subprocess_carries_a_timeout(self):
        self.init("main")
        self.commit()
        real = subprocess.run
        calls = []

        def spy(*a, **kw):
            calls.append((a[0] if a else kw.get("args"), kw.get("timeout")))
            return real(*a, **kw)

        path = self.base / "b.json"
        path.write_text(json.dumps(binder("x", [item("a")])), encoding="utf-8")
        with patch.object(pre.subprocess, "run", spy):
            pre.build_packet(path, self.root)
        self.assertTrue(calls)
        missing = [c for c, t in calls if not t]
        self.assertEqual(missing, [], "subprocess calls without a timeout")


if __name__ == "__main__":
    unittest.main(verbosity=2)
