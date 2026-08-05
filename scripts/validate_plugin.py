# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Plugin integrity check: SKILL.md frontmatter, reference-link existence, hook assets.

Usage:
  uv run scripts/validate_plugin.py --self-test   # check this repo, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, shlex, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
HOOKS = ROOT / "hooks"
LINK_RE = re.compile(r"\(([^\s)]+\.(?:md|json|py))\)")        # markdown links (no spaces)
PATH_RE = re.compile(r"`(references/[^`]+|scripts/[^`]+)`")    # backticked paths

# Reuse the generators' projection logic so the validator and the writers can never
# disagree about what "in sync" means. (Importing is side-effect-free: argparse runs
# only under each script's __main__.)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_codex_skills, sync_codex_agents  # noqa: E402


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def check() -> list[str]:
    errors: list[str] = []
    skill_dirs = [p.parent for p in SKILLS.glob("*/SKILL.md")]
    if not skill_dirs:
        errors.append("no skills found under skills/*/SKILL.md")
    for sd in sorted(skill_dirs):
        text = (sd / "SKILL.md").read_text()
        fm = _frontmatter(text)
        for field in ("name", "description"):
            if not fm.get(field):
                errors.append(f"{sd.name}: SKILL.md missing frontmatter '{field}'")
        cited = set(LINK_RE.findall(text)) | set(PATH_RE.findall(text))
        for rel in sorted(cited):
            if rel.startswith(("http://", "https://")):
                continue
            if "<" in rel:
                continue  # placeholder path like references/sme/<id>.md, not a repo file
            target = (sd / rel).resolve()
            if not str(target).startswith(str(ROOT)):
                continue  # out-of-tree example path, not a repo file
            if not target.exists():
                errors.append(f"{sd.name}: SKILL.md cites missing path '{rel}'")
    # karta-owned agents: frontmatter only (no SKILL-style links)
    for agent in sorted((ROOT / "agents").glob("*.md")):
        fm = _frontmatter(agent.read_text())
        for field in ("name", "description"):
            if not fm.get(field):
                errors.append(f"agents/{agent.name}: missing frontmatter '{field}'")
    # Claude marketplace manifest: a plugin that enumerates skills must list exactly
    # the skill dirs present (a `strict` entry only loads what it lists), so a skill
    # dir added without a manifest line would silently never register.
    present = {sd.name for sd in skill_dirs}
    mp = ROOT / ".claude-plugin" / "marketplace.json"
    if mp.exists():
        try:
            data = json.loads(mp.read_text())
        except json.JSONDecodeError as e:
            errors.append(f".claude-plugin/marketplace.json: invalid JSON ({e})")
            data = {}
        for plugin in data.get("plugins", []):
            pname = plugin.get("name", "?")
            if plugin.get("source") != "./":
                errors.append(f".claude-plugin/marketplace.json: plugin '{pname}' source must stay './' for Claude plugin installs")
            listed_raw = plugin.get("skills")
            if not isinstance(listed_raw, list):
                continue  # directory-form ("./skills/") or absent — nothing to enumerate
            listed = {Path(s).name for s in listed_raw}
            for name in sorted(present - listed):
                errors.append(f"marketplace.json: skill '{name}' exists under skills/ but plugin '{pname}' does not list it")
            for name in sorted(listed - present):
                errors.append(f"marketplace.json: plugin '{pname}' lists '{name}' but skills/{name}/SKILL.md is missing")
    _check_codex(errors, present)
    _check_pi(errors)
    _check_hooks(errors)
    _check_skill_scripts(errors)
    return errors


def _load_json(path: Path, errors: list[str]) -> dict:
    if not path.exists():
        errors.append(f"{path.relative_to(ROOT)}: missing")
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON ({e})")
        return {}


def _check_pi(errors: list[str]) -> None:
    """Guard the Pi package contract and its version parity."""
    package = _load_json(ROOT / "package.json", errors)
    claude = _load_json(ROOT / ".claude-plugin" / "plugin.json", errors)
    lock = _load_json(ROOT / "package-lock.json", errors)
    if not package:
        return

    package_name = package.get("name")
    if not isinstance(package_name, str) or package_name.rsplit("/", 1)[-1] != "karta":
        errors.append("package.json: name must identify the karta package")
    for field in ("version", "license"):
        if claude and package.get(field) != claude.get(field):
            errors.append(
                f"package.json: '{field}' ({package.get(field)!r}) != "
                f".claude-plugin/plugin.json ({claude.get(field)!r})")
    if package.get("private") is not True:
        errors.append("package.json: private must stay true until publication is approved")
    if package.get("type") != "module":
        errors.append("package.json: type must be 'module'")
    if "pi-package" not in package.get("keywords", []):
        errors.append("package.json: keywords must include 'pi-package'")
    if not str(package.get("packageManager", "")).startswith("npm@"):
        errors.append("package.json: packageManager must pin npm")
    expected_files = [
        "extensions/pi/", "skills/", "agents/", "hooks/scripts/",
        "!**/__pycache__/", "!**/*.pyc",
    ]
    if package.get("files") != expected_files:
        errors.append(f"package.json: files must be {expected_files!r}")

    pi = package.get("pi")
    expected_extensions = ["./extensions/pi/index.ts"]
    if not isinstance(pi, dict):
        errors.append("package.json: missing pi manifest")
    else:
        if pi.get("extensions") != expected_extensions:
            errors.append(
                f"package.json: pi.extensions must be {expected_extensions!r}")
        if "skills" in pi:
            errors.append(
                "package.json: pi.skills must stay absent; the extension trust-gates skill discovery")
    for extension in expected_extensions:
        if not (ROOT / extension).is_file():
            errors.append(f"package.json: Pi extension '{extension}' is missing")

    consumer_script = re.compile(
        r"(?:python3|uv run(?: --script)?)\s+skills/karta-[a-z-]+/scripts/")
    for skill in sorted((ROOT / "skills").glob("karta-*/SKILL.md")):
        if consumer_script.search(skill.read_text()):
            errors.append(
                f"{skill.relative_to(ROOT)}: bundled script command resolves from the consumer cwd")

    peers = package.get("peerDependencies", {})
    for dependency in ("@earendil-works/pi-coding-agent", "typebox"):
        if peers.get(dependency) != "*":
            errors.append(f"package.json: peerDependencies.{dependency} must be '*'")
    tested_pi = package.get("devDependencies", {}).get("@earendil-works/pi-coding-agent")
    if not isinstance(tested_pi, str) or not re.fullmatch(r"\d+\.\d+\.\d+", tested_pi):
        errors.append(
            "package.json: devDependencies.@earendil-works/pi-coding-agent must pin one tested version")
    lifecycle = {"preinstall", "install", "postinstall", "prepare"}
    present_lifecycle = sorted(lifecycle & set(package.get("scripts", {})))
    if present_lifecycle:
        errors.append(
            f"package.json: install lifecycle scripts are forbidden ({', '.join(present_lifecycle)})")

    if lock:
        for field in ("name", "version"):
            if lock.get(field) != package.get(field):
                errors.append(
                    f"package-lock.json: '{field}' ({lock.get(field)!r}) != "
                    f"package.json ({package.get(field)!r})")
        locked_root = lock.get("packages", {}).get("", {})
        if locked_root.get("peerDependencies") != peers:
            errors.append("package-lock.json: root peerDependencies differ from package.json")
        if locked_root.get("devDependencies") != package.get("devDependencies"):
            errors.append("package-lock.json: root devDependencies differ from package.json")


def _check_codex(errors: list[str], skill_names: set[str]) -> None:
    """Guard the Codex artifacts and every generated projection against drift."""
    # 1. Codex plugin manifest — present, well-formed, and consistent with Claude's.
    claude = _load_json(ROOT / ".claude-plugin" / "plugin.json", errors)
    codex = _load_json(ROOT / ".codex-plugin" / "plugin.json", errors)
    if codex:
        for field in ("name", "version", "description"):
            if not codex.get(field):
                errors.append(f".codex-plugin/plugin.json: missing '{field}'")
        if claude:
            for field in ("name", "version"):
                if codex.get(field) != claude.get(field):
                    errors.append(
                        f".codex-plugin/plugin.json: '{field}' ({codex.get(field)!r}) "
                        f"!= .claude-plugin/plugin.json ({claude.get(field)!r})")
        skills_ptr = codex.get("skills")
        if isinstance(skills_ptr, str) and not (ROOT / skills_ptr).is_dir():
            errors.append(f".codex-plugin/plugin.json: skills path '{skills_ptr}' is not a directory")
        iface = codex.get("interface", {})
        for field in ("displayName", "shortDescription", "category"):
            if not iface.get(field):
                errors.append(f".codex-plugin/plugin.json: interface missing '{field}'")

    # 2. Codex repo marketplace — shape + plugin entry policy/category.
    market = _load_json(ROOT / ".agents" / "plugins" / "marketplace.json", errors)
    if market:
        if not market.get("name"):
            errors.append(".agents/plugins/marketplace.json: missing top-level 'name'")
        if not market.get("interface", {}).get("displayName"):
            errors.append(".agents/plugins/marketplace.json: missing interface.displayName")
        for entry in market.get("plugins", []):
            pn = entry.get("name", "?")
            src = entry.get("source", {})
            if not (src.get("source") and src.get("path")):
                errors.append(f".agents/plugins/marketplace.json: plugin '{pn}' missing source.source/source.path")
            expected_path = f"./plugins/{pn}"
            if src.get("path") != expected_path:
                errors.append(
                    f".agents/plugins/marketplace.json: plugin '{pn}' source.path "
                    f"{src.get('path')!r} != {expected_path!r}")
            else:
                plugin_root = ROOT / expected_path
                if not plugin_root.is_dir():
                    errors.append(f".agents/plugins/marketplace.json: plugin '{pn}' path '{expected_path}' is missing")
                elif not (plugin_root / ".codex-plugin" / "plugin.json").exists():
                    errors.append(f"{plugin_root.relative_to(ROOT)}/.codex-plugin/plugin.json: missing")
            pol = entry.get("policy", {})
            if not (pol.get("installation") and pol.get("authentication")):
                errors.append(f".agents/plugins/marketplace.json: plugin '{pn}' missing policy.installation/authentication")
            if not entry.get("category"):
                errors.append(f".agents/plugins/marketplace.json: plugin '{pn}' missing 'category'")
            if codex and pn != codex.get("name"):
                errors.append(f".agents/plugins/marketplace.json: plugin '{pn}' != plugin.json name '{codex.get('name')}'")

    # 3. Repo-local skill mirror — byte-parity for karta-owned skills, no
    # unmanaged orphans. Cross-runtime skills with complete skills-lock.json
    # entries share .agents/skills but are excluded from the karta plugin.
    want, names = sync_codex_skills.expected()
    for p, (content, exec_bits) in sorted(want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from .agents/skills mirror (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical skill (run sync_codex_skills.py)")
        elif (p.stat().st_mode & 0o111) != exec_bits:
            errors.append(f"{p.relative_to(ROOT)}: executable bit differs from canonical skill (run sync_codex_skills.py)")
    for p in sorted(set(sync_codex_skills.mirror_files()) - set(want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in mirror (no canonical source)")
    for name in sorted(sync_codex_skills.mirror_skill_names() - names):
        errors.append(f".agents/skills/{name}: orphaned (no skills/{name})")
    install_want = sync_codex_skills.expected_install_projection()
    install_have = set(sync_codex_skills.install_projection_files())
    for p, (content, exec_bits) in sorted(install_want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from Codex install projection (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical Codex install projection (run sync_codex_skills.py)")
        elif (p.stat().st_mode & 0o111) != exec_bits:
            errors.append(f"{p.relative_to(ROOT)}: executable bit differs from canonical Codex install projection (run sync_codex_skills.py)")
    for p in sorted(install_have - set(install_want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in Codex install projection (no canonical source)")
    for name in sorted(sync_codex_skills.install_projection_skill_names() - names):
        errors.append(f"plugins/karta/skills/{name}: orphaned (no skills/{name})")

    # 3b. External skill hash liveness — recompute each external skill's SKILL.md
    # content hash (computedHash = sha256 of the bytes as synced locally) against
    # skills-lock.json; a mismatch, degraded entry, or missing lock entry fails,
    # naming the skill. Shared with sync_codex_skills --check (one truth).
    errors.extend(sync_codex_skills.external_integrity_problems())

    # 4. Codex agent projections — TOML + bundled instructions match agents/*.md.
    for p, content in sorted(sync_codex_agents.projections().items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing (run sync_codex_agents.py)")
        elif p.read_text() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from agents/*.md (run sync_codex_agents.py)")
    for toml_path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        try:
            data = tomllib.loads(toml_path.read_text())
        except tomllib.TOMLDecodeError as e:
            errors.append(f".codex/agents/{toml_path.name}: invalid TOML ({e})")
            continue
        agent_md = ROOT / "agents" / f"{toml_path.stem}.md"
        if agent_md.exists():
            expected = sync_codex_agents.sandbox_mode_for(_frontmatter(agent_md.read_text()))
            if data.get("sandbox_mode") != expected:
                errors.append(
                    f".codex/agents/{toml_path.name}: sandbox_mode "
                    f"'{data.get('sandbox_mode')}' != derived '{expected}' (from agents/{toml_path.stem}.md tools)")
        for field in ("name", "description", "developer_instructions"):
            if not data.get(field):
                errors.append(f".codex/agents/{toml_path.name}: missing '{field}'")

    # 5. Per-skill Codex metadata — present and declares a display name.
    for name in sorted(skill_names):
        yml = SKILLS / name / "agents" / "openai.yaml"
        if not yml.exists():
            errors.append(f"{name}: missing agents/openai.yaml")
        elif "display_name:" not in yml.read_text():
            errors.append(f"{name}: agents/openai.yaml missing interface.display_name")

    # 6. doc-gardner opt-in config — if a repo commits one, it must match the shape
    # the shipped schema promises (docs/specs/2026-06-18-doc-gardner-design.md §5).
    _check_doc_gardner(errors)

    # 7. kaizen opt-in config — if a repo commits one, it must be well-formed.
    # KARTA-SME-OVERRIDE(min.4): mirrors the proven doc-gardner block above
    # pattern-for-pattern, and this repo ships no test framework by design (manual gate
    # scripts only) [ceiling: a third opt-in config copy; upgrade: factor the copies
    # into one shared, checked helper]
    kz = ROOT / ".karta" / "kaizen.json"
    if kz.exists():
        try:
            cfg = json.loads(kz.read_text())
        except json.JSONDecodeError as e:
            errors.append(f".karta/kaizen.json: invalid JSON ({e})")
            cfg = None
        if isinstance(cfg, dict):
            if not isinstance(cfg.get("enabled"), bool):
                errors.append(".karta/kaizen.json: 'enabled' must be a boolean")
            for key in cfg:
                if key not in ("enabled", "focus"):
                    errors.append(f".karta/kaizen.json: unknown key '{key}' (allowed: enabled, focus)")

    # 8. roundtable-edict opt-in config — if this repo commits one, it must be
    # well-formed. Richer than the doc-gardner/kaizen switches above (typed panel
    # settings + a nested points object), but the same house pattern: an absent file
    # or enabled:false disables every gate, and a malformed switch is caught at commit
    # by this validator (already run on every commit by precommit_gate.py).
    # KARTA-SME-OVERRIDE(min.4): this repo ships no test framework by design (manual gate
    # scripts only); the check for this new branch logic is validate_plugin's own run over
    # the committed config plus the item oracle's malformed-config probe [ceiling: a fourth
    # divergent opt-in config copy; upgrade: factor the shared enabled/unknown-key checks
    # into one schema-driven helper]
    rt = ROOT / ".karta" / "roundtable.json"
    if rt.exists():
        try:
            cfg = json.loads(rt.read_text())
        except json.JSONDecodeError as e:
            errors.append(f".karta/roundtable.json: invalid JSON ({e})")
            cfg = None
        if isinstance(cfg, dict):
            if not isinstance(cfg.get("enabled"), bool):
                errors.append(".karta/roundtable.json: 'enabled' must be a boolean")
            if not isinstance(cfg.get("tool"), str):
                errors.append(".karta/roundtable.json: 'tool' must be a string")
            if not isinstance(cfg.get("providers"), list):
                errors.append(".karta/roundtable.json: 'providers' must be a list")
            mp = cfg.get("min_providers")
            if not isinstance(mp, int) or isinstance(mp, bool) or mp < 1:
                errors.append(".karta/roundtable.json: 'min_providers' must be an integer >= 1")
            pts = cfg.get("points")
            if (not isinstance(pts, dict) or set(pts) != {"plan_commit", "deliver_merge"}
                    or not all(isinstance(pts.get(k), bool) for k in ("plan_commit", "deliver_merge"))):
                errors.append(
                    ".karta/roundtable.json: 'points' must be an object with exactly "
                    "boolean 'plan_commit' and 'deliver_merge'")
            for key in cfg:
                if key not in ("enabled", "tool", "providers", "min_providers", "focus", "points"):
                    errors.append(
                        f".karta/roundtable.json: unknown key '{key}' "
                        "(allowed: enabled, tool, providers, min_providers, focus, points)")


DG_SCHEMA = ROOT / "skills" / "karta-doc-gardner" / "references" / "doc-gardner-schema.json"
_TYPE_BY_NAME = {"boolean": bool, "string": str}


def _check_doc_gardner(errors: list[str], config: Path | None = None,
                       schema: Path | None = None) -> None:
    """Gate a committed .karta/doc-gardner.json against the shipped schema
    skills/karta-doc-gardner/references/doc-gardner-schema.json — a hand-rolled
    stdlib check of the schema's semantics (required keys, per-key type,
    additionalProperties: false), no jsonschema dependency. An absent config is
    valid; a missing or unreadable schema is a reported integrity failure (the
    schema ships with the plugin), never a crash. Booleans are checked with
    `type(x) is bool` — bool subclasses int, so an isinstance-family check
    against int would let `"enabled": 1` through."""
    cfg_path = config if config is not None else ROOT / ".karta" / "doc-gardner.json"
    schema_path = schema if schema is not None else DG_SCHEMA
    if not cfg_path.exists():
        return  # opt-in: an absent config is valid
    label = ".karta/doc-gardner.json"
    try:
        sch = json.loads(schema_path.read_text())
    except (OSError, ValueError) as e:
        errors.append(
            "skills/karta-doc-gardner/references/doc-gardner-schema.json: "
            f"missing or unreadable ({e}) — cannot gate {label}")
        return
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, ValueError) as e:
        errors.append(f"{label}: invalid JSON ({e})")
        return
    if type(cfg) is not dict:
        errors.append(f"{label}: must be a JSON object")
        return
    props = sch.get("properties", {})
    for key in sch.get("required", []):
        if key not in cfg:
            errors.append(f"{label}: missing required key '{key}'")
    for key, val in cfg.items():
        if key not in props:  # additionalProperties: false
            errors.append(f"{label}: unknown key '{key}' (allowed: {', '.join(sorted(props))})")
            continue
        want = _TYPE_BY_NAME.get(props[key].get("type"))
        if want is not None and type(val) is not want:
            errors.append(f"{label}: '{key}' must be a {props[key].get('type')}")


def _self_test() -> int:
    """Fixture-driven cases for the doc-gardner schema gate (run via --self-test)."""
    import tempfile
    real_schema = DG_SCHEMA.read_text() if DG_SCHEMA.exists() else "{}"
    # (name, config text, schema text or None for a missing schema, expected error substrings)
    cases = [
        ("valid minimal config passes", '{"enabled": true}', real_schema, []),
        ("valid config with focus passes", '{"enabled": false, "focus": "api docs"}', real_schema, []),
        ("missing enabled fails", '{"focus": "x"}', real_schema, ["missing required key 'enabled'"]),
        ("enabled: 1 fails (bool, never int)", '{"enabled": 1}', real_schema, ["'enabled' must be a boolean"]),
        ('enabled: "true" fails', '{"enabled": "true"}', real_schema, ["'enabled' must be a boolean"]),
        ("non-string focus fails", '{"enabled": true, "focus": 3}', real_schema, ["'focus' must be a string"]),
        ("unknown key fails", '{"enabled": true, "scope": "docs"}', real_schema, ["unknown key 'scope'"]),
        ("invalid config JSON fails", '{"enabled": tru', real_schema, ["invalid JSON"]),
        ("non-object config fails", '[true]', real_schema, ["must be a JSON object"]),
        ("missing schema file is reported, not a crash", '{"enabled": true}', None, ["missing or unreadable"]),
        ("unreadable schema is reported, not a crash", '{"enabled": true}', "{not json", ["missing or unreadable"]),
    ]
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        for i, (name, cfg_text, schema_text, want) in enumerate(cases):
            cfg = Path(td) / f"cfg{i}.json"
            cfg.write_text(cfg_text)
            schema = Path(td) / f"schema{i}.json"
            if schema_text is not None:
                schema.write_text(schema_text)
            errors: list[str] = []
            _check_doc_gardner(errors, config=cfg, schema=schema)
            ok = bool(errors) == bool(want) and all(any(w in e for e in errors) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errors!r}"))
            failures += 0 if ok else 1
        errors = []
        _check_doc_gardner(errors, config=Path(td) / "absent.json", schema=Path(td) / "schema0.json")
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] absent config stays valid" + ("" if ok else f" — got {errors!r}"))
        failures += 0 if ok else 1

        # _run_self_test enforces "every gated script exposes --self-test": check all three
        # dispositions on fabricated scripts. Their paths are outside ROOT, exercising rel().
        rst = Path(td) / "rst"
        rst.mkdir()
        (rst / "good.py").write_text(
            "import argparse\n"
            "p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true')\n"
            "p.parse_args()\n")
        (rst / "bad.py").write_text(
            "import argparse,sys\n"
            "p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true')\n"
            "a=p.parse_args()\n"
            "sys.exit(1 if a.self_test else 0)\n")
        (rst / "missing.py").write_text(
            "import argparse\n"
            "argparse.ArgumentParser().parse_args()\n")
        rst_cases = [
            ("_run_self_test: exposed & passing self-test -> no error", "good.py", False, None),
            ("_run_self_test: exposed & failing self-test -> named failure", "bad.py", True, "--self-test failed"),
            ("_run_self_test: absent --self-test -> distinct failure", "missing.py", True, "does not expose --self-test"),
        ]
        for name, fn, want_err, want_sub in rst_cases:
            errs: list[str] = []
            _run_self_test(rst / fn, errs)
            ok = (bool(errs) == want_err) and (want_sub is None or any(want_sub in e for e in errs))
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            failures += 0 if ok else 1
    total = len(cases) + 1 + len(rst_cases)
    print(f"self-test: {total - failures}/{total} embedded fixture cases passed")
    return 1 if failures else 0


def _run_self_test(script: Path, errors: list[str]) -> None:
    """Run `<script> --self-test` and append a reported error on failure. Shared by the
    hooks/scripts and skills/*/scripts passes so both self-test their fixtures identically.

    The floor assumes every gated script exposes --self-test, so that invariant is enforced,
    not narrated: a script whose --help does not list --self-test is a distinct, named failure
    ('does not expose --self-test') — never mistaken for a self-test that ran and failed, and
    never a silent pass behind argparse's generic 'unrecognized arguments' exit."""
    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return p.name
    try:
        helpp = subprocess.run([sys.executable, str(script), "--help"],
                               capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{rel(script)}: could not probe --help ({e})")
        return
    if "--self-test" not in (helpp.stdout + helpp.stderr):
        errors.append(f"{rel(script)}: does not expose --self-test "
                      f"(the validator floor self-tests every gated script; add a --self-test mode)")
        return
    try:
        proc = subprocess.run([sys.executable, str(script), "--self-test"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{rel(script)}: --self-test did not run ({e})")
        return
    if proc.returncode != 0:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
        errors.append(f"{rel(script)}: --self-test failed ({tail})")


def _check_skill_scripts(errors: list[str]) -> None:
    """Self-test every skills/*/scripts/*.py the way _check_hooks self-tests the hook
    scripts. The hooks pass never covered the skill-shipped scripts, so their embedded
    fixtures (e.g. resolve_pack_checklist.py) went unrun at commit; every skills script
    exposes --self-test, so this pass durably covers them."""
    for script in sorted(SKILLS.glob("*/scripts/*.py")):
        _run_self_test(script, errors)


def _check_hooks(errors: list[str]) -> None:
    """Guard the plugin hook assets: the manifest parses, every script it references
    exists and is executable, no hook script is orphaned (an unreferenced script would
    silently never run — same class as the marketplace skill-listing check), and each
    script's embedded fixtures (--self-test) pass."""
    data = _load_json(HOOKS / "hooks.json", errors)
    referenced: set[Path] = set()
    for event, groups in (data.get("hooks") or {}).items():
        if not isinstance(groups, list):
            errors.append(f"hooks/hooks.json: '{event}' must map to a list of matcher groups")
            continue
        for group in groups:
            hook_list = group.get("hooks") if isinstance(group, dict) else None
            for hook in hook_list or []:
                if not isinstance(hook, dict):
                    continue
                if hook.get("type") != "command":
                    errors.append(f"hooks/hooks.json: {event}: unexpected hook type {hook.get('type')!r}")
                    continue
                try:
                    tokens = shlex.split(hook.get("command", ""))
                except ValueError as e:
                    errors.append(f"hooks/hooks.json: {event}: unparseable command ({e})")
                    continue
                for tok in tokens:
                    if "${CLAUDE_PLUGIN_ROOT}" not in tok:
                        continue
                    path = (ROOT / tok.replace("${CLAUDE_PLUGIN_ROOT}/", "")).resolve()
                    if path in referenced:
                        continue  # a script may back several events; report it once
                    referenced.add(path)
                    if not path.is_file():
                        errors.append(f"hooks/hooks.json: {event} references missing script '{tok}'")
                    elif not os.access(path, os.X_OK):
                        errors.append(f"{path.relative_to(ROOT)}: not executable (chmod +x)")
    scripts_dir = HOOKS / "scripts"
    for script in sorted(scripts_dir.glob("*.py")) if scripts_dir.is_dir() else []:
        if script.resolve() not in referenced:
            errors.append(f"{script.relative_to(ROOT)}: not referenced by hooks/hooks.json — it would never run")
        _run_self_test(script, errors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded doc-gardner schema fixtures, then the repo check")
    args = ap.parse_args()
    if args.self_test and _self_test() != 0:
        print("PLUGIN INTEGRITY: FAIL")
        print("  - embedded --self-test fixtures failed")
        return 1
    errors = check()
    if errors:
        print("PLUGIN INTEGRITY: FAIL")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PLUGIN INTEGRITY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
