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
    _check_hooks(errors)
    _check_skill_scripts(errors)
    _check_behaviour_anchor(errors)
    _check_vendored_fonts(errors)
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


# --- the Karta Watch coverage floor ----------------------------------------
# serve_status.py is the file every item of a watch binder edits, so a check and
# the expectation it guards can be deleted together in one edit with nothing to
# notice. The anchor below lives beside it but is NOT it, and no restyle item
# touches the anchor — so the deletion still fails here.

WATCH_SCRIPT = SKILLS / "karta-status" / "scripts" / "serve_status.py"
BEHAVIOUR_ANCHOR = SKILLS / "karta-status" / "scripts" / "selftest_behaviours.txt"
KW_PREFIX = "data-kw-"


def _anchored_behaviours(anchor: Path) -> list[str]:
    """One behaviour name per line; blank lines and # comments ignored."""
    return [ln.strip() for ln in anchor.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _registered_behaviours(script: Path) -> tuple[dict, str | None]:
    """The live coverage registry, read from the page's own script. (registry, error)."""
    try:
        proc = subprocess.run([sys.executable, str(script), "--list-behaviours"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, f"could not read the coverage registry ({e})"
    if proc.returncode != 0:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-2:])
        return {}, f"--list-behaviours failed ({tail})"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {}, f"--list-behaviours emitted invalid JSON ({e})"
    if not isinstance(data, dict):
        return {}, "--list-behaviours must emit a JSON object of behaviour -> entry"
    return data, None


def _check_behaviour_anchor(errors: list[str], anchor: Path | None = None,
                            registry: dict | None = None,
                            script: Path | None = None) -> None:
    """Compare the committed anchor against the live coverage registry as a FLOOR:
    every anchored behaviour must still be registered; extra registrations pass, so
    a later item can add its own. Equality would be self-defeating — each restyle
    item introduces new behaviours and would fail against a frozen anchor.

    An absent or empty anchor is itself a failure: a floor compared against nothing
    passes vacuously, which is the exact hole the anchor exists to close."""
    anchor = anchor or BEHAVIOUR_ANCHOR
    try:
        label = str(anchor.relative_to(ROOT))
    except ValueError:
        label = anchor.name
    if not anchor.is_file():
        errors.append(f"{label}: missing — the Karta Watch coverage floor would "
                      "have nothing to compare against and would pass vacuously")
        return
    anchored = _anchored_behaviours(anchor)
    if not anchored:
        errors.append(f"{label}: empty — the coverage floor would pass vacuously; "
                      "it must name every behaviour the page's self-test must keep")
        return
    if registry is None:
        registry, failure = _registered_behaviours(script or WATCH_SCRIPT)
        if failure:
            errors.append(f"{label}: {failure}")
            return
    for name in anchored:
        if name not in registry:
            errors.append(f"{label}: anchors '{name}', which serve_status.py's "
                          "coverage registry no longer has — a behaviour lost its "
                          "check (add it back, or drop the anchor line deliberately)")
    # Entry shape: the kind rule, enforced from outside the file that declares it.
    for name, entry in sorted(registry.items()):
        entry = entry if isinstance(entry, dict) else {}
        kind = entry.get("kind")
        if kind == "rendered":
            if not str(entry.get("hook") or "").startswith(KW_PREFIX):
                errors.append(f"{label}: registry entry '{name}' is rendered but "
                              f"names no {KW_PREFIX}* hook")
        elif kind == "behaviour":
            if not entry.get("check"):
                errors.append(f"{label}: registry entry '{name}' is a behaviour but "
                              "names no check that exercises it")
        else:
            errors.append(f"{label}: registry entry '{name}' declares no kind "
                          "(expected rendered or behaviour)")


# --- the vendored typefaces -------------------------------------------------
# The page's three families are BINARY files inside the shipped plugin, and the
# two Codex mirrors are generated copies. Byte drift in a font is invisible in a
# diff and would ship a different face to Codex users than to Claude Code users,
# so the mirrors are compared byte for byte here rather than trusted. The same
# comparison covers serve_status.py, which every item of a watch binder edits.
#
# All three families are Open Font Licence: redistributing a face without its
# licence file is a licensing defect, not an untidiness, so an absent licence is
# an error and not a warning.

MIRROR_SKILL_ROOTS = (Path(".agents") / "skills",
                      Path("plugins") / "karta" / "skills")
WATCH_FONTS_REL = Path("karta-status") / "assets" / "fonts"
WATCH_SCRIPT_REL = Path("karta-status") / "scripts" / "serve_status.py"


def _mirror_drift(canonical: Path, twins: list[Path]) -> list[str]:
    """Byte-level drift of one canonical file against each generated mirror copy."""
    data = canonical.read_bytes()
    out = []
    for twin in twins:
        if not twin.is_file():
            out.append(f"{canonical.name} is missing from {twin.parent}")
        elif twin.read_bytes() != data:
            out.append(f"{canonical.name} differs from canonical in {twin.parent}")
    return out


def _check_vendored_fonts(errors: list[str], root: Path | None = None) -> None:
    """Every vendored font and licence, plus the page script itself, byte-identical
    in the canonical tree and both Codex mirrors — and one licence per family."""
    root = root or ROOT
    label = "skills/karta-status/assets/fonts"
    fonts = root / "skills" / WATCH_FONTS_REL
    mirror_fonts = [root / m / WATCH_FONTS_REL for m in MIRROR_SKILL_ROOTS]
    if not fonts.is_dir():
        errors.append(f"{label}: missing — the page declares vendored faces "
                      "with no files to serve")
        return
    manifest_path = fonts / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"{label}: manifest.json missing — the vendored faces have "
                      "no declared record to check the files against")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as e:
        errors.append(f"{label}/manifest.json: invalid JSON ({e})")
        return
    families = manifest.get("families") or {}
    if not families:
        errors.append(f"{label}/manifest.json: names no families, so no licence "
                      "can be required of it")
    for family, entry in sorted(families.items()):
        licence = (entry or {}).get("licence") or ""
        if not licence or not (fonts / licence).is_file():
            errors.append(f"{label}: family '{family}' ships no licence file "
                          f"('{licence or 'none declared'}') — an OFL face "
                          "redistributed without its licence")
    for f in sorted(p for p in fonts.iterdir() if p.is_file()):
        for problem in _mirror_drift(f, [m / f.name for m in mirror_fonts]):
            errors.append(f"{label}: {problem}")
    script = root / "skills" / WATCH_SCRIPT_REL
    if script.is_file():
        for problem in _mirror_drift(script, [root / m / WATCH_SCRIPT_REL
                                              for m in MIRROR_SKILL_ROOTS]):
            errors.append(f"skills/{WATCH_SCRIPT_REL.as_posix()}: {problem}")


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

        # The Karta Watch coverage floor: the anchor is compared as a floor, and an
        # absent/empty anchor or a malformed registry entry is itself a failure.
        live = {"a-behaviour": {"kind": "rendered", "hook": "data-kw-a", "check": "_c_a"},
                "another": {"kind": "behaviour", "hook": None, "check": "_c_b"}}
        anc = Path(td) / "anchor.txt"
        anchor_cases = [
            ("anchor floor: every anchored behaviour registered -> no error",
             "# a comment\n\na-behaviour\nanother\n", live, []),
            ("anchor floor: an extra registration the anchor omits still passes",
             "a-behaviour\n", live, []),
            ("anchor floor: an anchored behaviour missing from the registry fails",
             "a-behaviour\ngone-behaviour\n", live, ["anchors 'gone-behaviour'"]),
            ("anchor floor: an emptied anchor fails (a floor over nothing is vacuous)",
             "# only comments left\n\n", live, ["empty"]),
            ("anchor floor: a rendered entry naming no data-kw hook fails",
             "a-behaviour\n", {"a-behaviour": {"kind": "rendered", "hook": "", "check": "_c_a"}},
             ["names no data-kw-* hook"]),
            ("anchor floor: a behaviour entry naming no check fails",
             "a-behaviour\n", {"a-behaviour": {"kind": "behaviour", "hook": None, "check": ""}},
             ["names no check"]),
            ("anchor floor: an entry with neither kind fails",
             "a-behaviour\n", {"a-behaviour": {"kind": None, "hook": None, "check": None}},
             ["declares no kind"]),
        ]
        for name, anchor_text, reg, want in anchor_cases:
            anc.write_text(anchor_text)
            errs: list[str] = []
            _check_behaviour_anchor(errs, anchor=anc, registry=reg)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            failures += 0 if ok else 1
        errs = []
        _check_behaviour_anchor(errs, anchor=Path(td) / "absent-anchor.txt", registry=live)
        ok = bool(errs) and any("missing" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] anchor floor: a missing anchor fails"
              + ("" if ok else f" — got {errs!r}"))
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

        # The vendored fonts: a synthetic repo shape with the canonical tree and
        # both Codex mirrors, then one deliberately broken copy per rule. Font
        # drift is invisible in a diff, so each rule is driven against a known-bad
        # tree rather than only against the (clean) real one.
        def _font_tree(base: Path, *, licence=True, mirror_bytes=b"WOFF2",
                       script_bytes=b"# page\n", manifest=True):
            for rel in ("skills",) + tuple(m.as_posix() for m in MIRROR_SKILL_ROOTS):
                (base / rel / WATCH_FONTS_REL).mkdir(parents=True, exist_ok=True)
                (base / rel / WATCH_SCRIPT_REL).parent.mkdir(parents=True, exist_ok=True)
            canon = base / "skills" / WATCH_FONTS_REL
            (canon / "demo-400.woff2").write_bytes(b"WOFF2")
            if licence:
                (canon / "demo-OFL.txt").write_text("SIL OPEN FONT LICENSE")
            if manifest:
                (canon / "manifest.json").write_text(json.dumps(
                    {"families": {"Demo": {"licence": "demo-OFL.txt"}},
                     "faces": [{"family": "Demo", "weight": 400,
                                "file": "demo-400.woff2"}]}))
            (base / "skills" / WATCH_SCRIPT_REL).write_bytes(b"# page\n")
            for m in MIRROR_SKILL_ROOTS:
                mirror = base / m / WATCH_FONTS_REL
                (mirror / "demo-400.woff2").write_bytes(mirror_bytes)
                if licence:
                    (mirror / "demo-OFL.txt").write_text("SIL OPEN FONT LICENSE")
                if manifest:
                    (mirror / "manifest.json").write_bytes(
                        (canon / "manifest.json").read_bytes())
                (base / m / WATCH_SCRIPT_REL).write_bytes(script_bytes)
            return base

        font_cases = [
            ("fonts: canonical tree mirrored byte for byte, licence present -> no error",
             {}, []),
            ("fonts: a font byte-differing in a mirror fails (drift a diff cannot show)",
             {"mirror_bytes": b"WOFF2-tampered"}, ["differs from canonical"]),
            ("fonts: a family whose licence file is absent fails",
             {"licence": False}, ["ships no licence file"]),
            ("fonts: serve_status.py differing from its mirror fails",
             {"script_bytes": b"# drifted\n"}, ["serve_status.py"]),
            ("fonts: an absent manifest fails (nothing to check the files against)",
             {"manifest": False}, ["manifest.json missing"]),
        ]
        for i, (name, kwargs, want) in enumerate(font_cases):
            errs = []
            _check_vendored_fonts(errs, root=_font_tree(Path(td) / f"fonts{i}", **kwargs))
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            failures += 0 if ok else 1
    total = (len(cases) + 1 + len(rst_cases) + len(anchor_cases) + 1
             + len(font_cases))
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
