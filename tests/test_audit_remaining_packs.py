"""Behavioral regressions for audit F18 (version-aware packs) and F19 (Vue style scoping).

Fixtures build real manifest trees in temp dirs and run the shipped scripts as
subprocesses, the way plan:sme and the benchmarks invoke them. Set
AUDIT_SOURCE_ROOT to run the same checks against a prior snapshot.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("AUDIT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
DETECT = ROOT / "skills/karta-plan/scripts/detect_stack.py"
VALIDATE = ROOT / "skills/karta-kaizen/scripts/validate_packs.py"
RESOLVE = ROOT / "skills/karta-kaizen/scripts/resolve_pack_checklist.py"
SHARED = ROOT / "skills/_shared/sme"
HOUSE_VUE = ROOT / ".karta/sme/karta-house-vue.md"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)


def checklist(path: Path) -> dict[str, str]:
    """Active checklist rule id -> rule text, parsed with the validator's own line shape."""
    out: dict[str, str] = {}
    in_list = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_list = line.startswith("## Review checklist")
            continue
        m = re.match(r"^- \[ \] ([a-z][a-z0-9-]*\.\d+) — (.*)$", line)
        if in_list and m:
            out[m.group(1)] = m.group(2)
    return out


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    end = lines.index("---", 1)
    return dict(l.split(": ", 1) for l in lines[1:end])


class ManifestTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gpt-packs-fix-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def detect(self):
        p = run(DETECT, self.root)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)


class DetectStackVersions(ManifestTree):
    """F18: stack detection carries the declared versions a version-aware rule needs."""

    def test_angular_versions_reach_the_output(self):
        self.write("package.json", json.dumps({
            "dependencies": {"@angular/core": "^22.0.1", "rxjs": "~7.8.0"},
            "devDependencies": {"@angular/cli": "^22.0.0"}}))
        out = self.detect()
        self.assertEqual(out["versions"]["@angular/core"], ["^22.0.1"])
        self.assertEqual(out["versions"]["@angular/cli"], ["^22.0.0"])
        self.assertEqual(out["versions"]["rxjs"], ["~7.8.0"])

    def test_existing_token_lists_are_unchanged(self):
        # Positive control for the consumers (plan:sme, match_pins): the two lists
        # keep their exact shape, and matching still works on the new output.
        self.write("package.json", json.dumps({"dependencies": {"vue": "^3.4.0"}}))
        self.write("api/pyproject.toml", '[project]\nname = "x"\nversion = "0"\n'
                                         'dependencies = ["fastapi>=0.110"]\n')
        out = self.detect()
        self.assertEqual(out["dependencies"], ["fastapi", "vue"])
        self.assertEqual(out["languages"], ["javascript", "node", "python"])
        pins = load("audit_packs_match_pins", "benchmarks/sme-static/match_pins.py")
        packs = {"vue": {"kind": "match", "tokens": ["vue"]},
                 "angular": {"kind": "match", "tokens": ["@angular/core"]}}
        self.assertEqual(pins.match_pins(packs, out), ["vue"])

    def test_monorepo_keeps_every_distinct_specifier(self):
        self.write("apps/old/package.json", json.dumps({"dependencies": {"@angular/core": "17.3.0"}}))
        self.write("apps/new/package.json", json.dumps({"dependencies": {"@angular/core": "^22.1.0"}}))
        out = self.detect()
        self.assertEqual(out["versions"]["@angular/core"], ["17.3.0", "^22.1.0"])

    def test_every_manifest_kind_reports_versions(self):
        self.write("pyproject.toml", '[project]\nname = "x"\nversion = "0"\n'
                                     'dependencies = ["fastapi>=0.110", "httpx"]\n'
                                     '[tool.poetry.dependencies]\npython = "^3.11"\n'
                                     'pydantic = "^2.7"\nuvicorn = { version = "^0.30" }\n')
        self.write("requirements.txt", "celery[redis]~=5.3 ; python_version >= '3.10'\nrequests\n")
        self.write("go.mod", "module m\n\ngo 1.22\n\nrequire (\n\tgithub.com/a-h/templ v0.2.771\n)\n")
        self.write("Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n'
                                 '[dependencies]\nserde = "1"\ntokio = { version = "1.38" }\n'
                                 'local = { path = "../local" }\n')
        self.write("Gemfile", 'gem "rails", "~> 7.1"\ngem "puma"\n')
        self.write("composer.json", json.dumps({"require": {"laravel/framework": "^11.0"}}))
        v = self.detect()["versions"]
        self.assertEqual(v["fastapi"], [">=0.110"])
        self.assertEqual(v["pydantic"], ["^2.7"])
        self.assertEqual(v["uvicorn"], ["^0.30"])
        self.assertEqual(v["celery"], ["~=5.3"])
        self.assertEqual(v["github.com/a-h/templ"], ["v0.2.771"])
        self.assertEqual(v["serde"], ["1"])
        self.assertEqual(v["tokio"], ["1.38"])
        self.assertEqual(v["rails"], ["~> 7.1"])
        self.assertEqual(v["laravel/framework"], ["^11.0"])
        # A dependency with no declared version appears in `dependencies` but has
        # no invented version: absence means "not declared", never "latest".
        for unversioned in ("httpx", "requests", "puma", "local"):
            self.assertNotIn(unversioned, v)
        self.assertIn("httpx", self.detect()["dependencies"])

    def test_self_test_still_passes(self):
        p = run(DETECT, "--self-test")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class AngularVersionAware(unittest.TestCase):
    """F18: ng.2 states the behavior and the versions; the v22 default satisfies it."""

    def setUp(self):
        self.rules = checklist(SHARED / "angular.md")

    def test_ng2_requires_behavior_not_a_redundant_declaration(self):
        ng2 = self.rules["ng.2"]
        self.assertIn("OnPush", ng2)
        self.assertRegex(ng2, r"v22")
        self.assertRegex(ng2, r"(?i)inherited default|default.{0,40}satisf")
        self.assertRegex(ng2, r"(?i)not required|need not|no .{0,20}declaration")

    def test_ng2_keeps_the_explicit_form_for_older_versions(self):
        # Older projects are judged by their own version, not called broken.
        ng2 = self.rules["ng.2"]
        self.assertRegex(ng2, r"(?i)before v22|below v22|v21 and (earlier|older)|older")
        self.assertIn("changeDetection: ChangeDetectionStrategy.OnPush", ng2)

    def test_ng4_standalone_default_is_version_aware(self):
        ng4 = self.rules["ng.4"]
        self.assertRegex(ng4, r"v19")
        self.assertRegex(ng4, r"(?i)default")

    def test_pack_points_at_detected_versions(self):
        text = (SHARED / "angular.md").read_text(encoding="utf-8")
        self.assertIn("versions", text)
        self.assertIn("@angular/core", text.split("## Do")[0])


class OtherVersionSensitivePacks(unittest.TestCase):
    """F18 follow-through: the htmx config rules name the major they are written for."""

    def test_htmx_config_rules_are_scoped_to_their_major(self):
        rules = checklist(SHARED / "go-htmx.md")
        for rid in ("htmx.4", "htmx.5"):
            self.assertRegex(rules[rid], r"htmx 2", rid)


class VueStyleScoping(unittest.TestCase):
    """F19: SFC/build and TypeScript rules apply only to projects that chose them."""

    def setUp(self):
        self.rules = checklist(SHARED / "vue.md")

    def test_script_setup_rule_is_scoped_to_sfc_build_projects(self):
        vue1 = self.rules["vue.1"]
        self.assertIn("<script setup>", vue1)
        self.assertRegex(vue1, r"(?i)build step")
        self.assertRegex(vue1, r"(?i)Options API.{0,80}(pass|supported)")

    def test_typed_rules_are_scoped_to_typescript(self):
        for rid in ("vue.2", "vue.3"):
            self.assertRegex(self.rules[rid], r"TypeScript", rid)
            self.assertRegex(self.rules[rid], r"(?i)JavaScript.{0,60}pass", rid)

    def test_teardown_rule_names_the_options_api_hooks(self):
        self.assertIn("beforeUnmount", self.rules["vue.6"])

    def test_house_pack_prose_matches_detection_and_stays_inactive(self):
        fm = frontmatter(HOUSE_VUE)
        # Not activated: activation adds gating and is a human policy decision.
        self.assertNotIn("always", fm)
        self.assertEqual(fm["match"], '["vue"]')
        body = HOUSE_VUE.read_text(encoding="utf-8").split("---", 2)[2]
        # The old body claimed "This pack is `always: true`"; naming the key as what
        # the pack does NOT carry is fine, claiming it is not.
        self.assertNotRegex(body, r"(?i)this\s+pack\s+is\s+`?always: true")
        self.assertRegex(body, r"(?i)inactive|never (matches|pinned|applies)")

    def test_house_pack_still_composes(self):
        p = run(RESOLVE, HOUSE_VUE)
        self.assertEqual(p.returncode, 0, p.stderr)
        ids = [i["id"] for i in json.loads(p.stdout)]
        self.assertNotIn("vue.1", ids)
        self.assertIn("vue.4", ids)
        self.assertIn("hvue.1", ids)


class PacksStayValid(unittest.TestCase):
    def test_all_builtin_and_house_packs_validate(self):
        packs = [p for p in sorted(SHARED.glob("*.md")) if p.name != "platform-native.md"]
        packs += sorted((ROOT / ".karta/sme").glob("*.md"))
        p = run(VALIDATE, *packs)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_shared_copies_are_byte_equal(self):
        p = run(ROOT / "scripts/check_shared_copies.py")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
