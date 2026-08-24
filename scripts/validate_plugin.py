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
import sync_codex_skills, sync_codex_agents, check_fact_traces  # noqa: E402


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
    _check_design_reference(errors)
    _check_fact_traces(errors)
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
    _check_font_provenance(errors, label, manifest)
    for f in sorted(p for p in fonts.iterdir() if p.is_file()):
        for problem in _mirror_drift(f, [m / f.name for m in mirror_fonts]):
            errors.append(f"{label}: {problem}")
    script = root / "skills" / WATCH_SCRIPT_REL
    if script.is_file():
        for problem in _mirror_drift(script, [root / m / WATCH_SCRIPT_REL
                                              for m in MIRROR_SKILL_ROOTS]):
            errors.append(f"skills/{WATCH_SCRIPT_REL.as_posix()}: {problem}")


# The manifest's provenance block is what a re-vendor is read back out of: the
# upstream commit, the source file and its digest, and the fontTools version the
# cut was made with. None of it is VERIFIED — no check here can confirm the bytes
# on disk came from that recipe rather than from somewhere else that renders the
# same, and the manifest says so in its own words. What is checked is the part a
# reader can be misled by: that the block is COMPLETE, and that it does not
# contradict itself. An incomplete provenance record reads as a stronger claim
# than it is, and a self-contradicting one reads as a checked claim.
#
# The version is required because the recipe is not byte-reproducible: the same
# flags on two fontTools versions give different byte counts, so "which version"
# is the difference between a record someone can act on and a record that only
# looks precise.

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _check_font_provenance(errors: list[str], label: str, manifest: dict) -> None:
    """The manifest's provenance record is complete and internally consistent.

    Complete: a fontTools version, an upstream commit, and a source file plus
    digest per face. Consistent: the commit appears in every family's source
    URL, two faces cut from the same source file record the same digest, and a
    family with a VARIABLE face declares the axes it carries and pins none —
    a pinned axis is exactly what a variable face was not instanced to, so a
    pin left behind describes the flattened cut it replaced.

    A face's source_file is checked for presence and used to group the digests,
    and deliberately not matched against its family's source_files: that field
    is written for a reader ("IBMPlexMono-{Regular,Medium,SemiBold}.ttf"), and
    teaching this check to expand a brace list would be a parser bought to
    satisfy a rule nobody asked for.

    Say where the digest rule stops, because it is narrower than it looks. It
    catches a digest that DISAGREES with another face cut from the same file, so
    it has teeth only for a family shipping two or more faces from one source —
    IBM Plex Sans, today. A family shipping ONE face from its source, which is
    what the serif became, has nothing to disagree with: swap that digest for a
    different well-formed one and this passes. Closing it would mean recording
    the same digest a second time in this same file so the two copies could be
    compared, and a value typed twice by the same hand catches a typo and
    nothing else — the cross-checks in this manifest earn their keep by reading
    the SAME fact out of independent records (the enumeration in code, the bytes
    on disk, the stylesheet), which a second hand-written copy is not. So this
    is disclosed rather than closed: the upstream digest is recorded, and
    whether it is the digest the bytes actually came from is not established
    here and is not established anywhere else in this repository either."""
    sub = manifest.get("subsetting") or {}
    version = str(sub.get("fonttools_version") or "").strip()
    if not version:
        errors.append(f"{label}/manifest.json: subsetting block records no "
                      "fonttools_version — the recipe is not byte-reproducible, "
                      "so the version is the only thing that makes the recorded "
                      "recipe replayable")
    commit = str(sub.get("upstream_commit") or "").strip()
    if not commit:
        errors.append(f"{label}/manifest.json: subsetting block records no "
                      "upstream_commit")
    families = manifest.get("families") or {}
    faces = manifest.get("faces") or []
    for family, entry in sorted(families.items()):
        entry = entry or {}
        if commit and commit not in str(entry.get("source_url") or ""):
            errors.append(f"{label}/manifest.json: family '{family}' records a "
                          f"source_url that does not name the pinned upstream "
                          f"commit {commit}")
        variable = [f for f in faces
                    if f.get("family") == family and f.get("variable")]
        if variable and entry.get("pinned_axes"):
            errors.append(f"{label}/manifest.json: family '{family}' ships a "
                          "variable face while still declaring pinned_axes "
                          f"({entry['pinned_axes']}) — a pin is what a variable "
                          "face was NOT instanced to")
        if variable and not entry.get("axes"):
            errors.append(f"{label}/manifest.json: family '{family}' ships a "
                          "variable face but records no axes")
    digests: dict[str, str] = {}
    for face in faces:
        name = f"{face.get('family')} {face.get('weight')}"
        source = str(face.get("source_file") or "").strip()
        digest = str(face.get("source_sha256") or "").strip()
        if not source:
            errors.append(f"{label}/manifest.json: face '{name}' records no "
                          "source_file")
        if not _SHA256_RE.match(digest):
            errors.append(f"{label}/manifest.json: face '{name}' records no "
                          "well-formed source_sha256")
            continue
        if digests.setdefault(source, digest) != digest:
            errors.append(f"{label}/manifest.json: face '{name}' records a "
                          f"source_sha256 for '{source}' that disagrees with "
                          "another face cut from the same file")


# --- Watch design reference: self-contained, no-network, and the serving rig
# that later visual checks in the watch-fidelity binder depend on. -----------
#
# docs/designs/karta-watch-1440x900-light.html is a frozen capture of a live
# Claude Design export, committed instead of the export itself so the
# comparison never needs the network and never drifts when a font host does.
# The rule below is the guard against it quietly regaining a remote
# dependency: every http(s) reference is an error, and every local asset
# reference must resolve to a real file relative to the design file itself.
#
# docs/designs/fixtures/watch-fidelity-state is a hand-written repo root the
# serving rig points serve_status.py at (--root) so the page it renders is
# fixed by one committed binder file rather than by whatever binder happens
# to be live in this repo. Its slug is deliberately fictitious — checked
# against this repo's real karta/*/* refs so it can never collide.

DESIGN_REFERENCE_REL = Path("docs") / "designs" / "karta-watch-1440x900-light.html"
DESIGN_FIXTURE_REL = Path("docs") / "designs" / "fixtures" / "watch-fidelity-state"

# The frozen reference points its @font-face rules at the very files the page
# serves, so it renders in whatever the page renders in and agrees with the page
# about typefaces BY CONSTRUCTION — including when both are wrong. Giving it a
# pinned second copy of the fonts would only move the question, so the limit is
# not fixed here; it is DECLARED, in the file's own header, where the next
# person to run a font comparison against it will read it. Declared and then
# enforced, because a caveat only a reviewer maintains is the same reassurance
# the missing caveat already was: the header must say the file cannot witness a
# font difference, and must point at where the check that can is briefed.
DESIGN_FONT_CAVEAT_PHRASE = "cannot witness a font difference"
DESIGN_FONT_CAVEAT_POINTER = "docs/backlog/watch-optical-harness/FINDINGS.md"

# Absolute http(s) URLs, and the protocol-relative `//host/path` form that is
# just as much an external fetch while carrying no scheme to grep for. The
# leading (?<![:/\w]) keeps it off the `//` inside `https://…` (already matched
# by the first branch) and off a bare `path//x`.
_EXTERNAL_URL_RE = re.compile(r"https?://[^\s\"'()]+|(?<![:/\w])//[A-Za-z0-9-]+\.[^\s\"'()]+")
_ASSET_REF_RE = re.compile(r'(?:src|href)="([^"]+)"|url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')
_HEADER_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _design_reference_asset_paths(text: str) -> list[str]:
    """Every local asset a design capture points at (src=, href=, css url()) —
    skipping data: URIs and same-page #fragments, which resolve to nothing on
    disk by design."""
    paths: list[str] = []
    for m in _ASSET_REF_RE.finditer(text):
        ref = m.group(1) or m.group(2)
        if not ref or ref.startswith(("data:", "#")):
            continue
        paths.append(ref)
    return paths


def _check_design_self_contained(errors: list[str], design_file: Path) -> None:
    """The committed design capture opens with no network: no external host
    referenced anywhere, every local asset it points at resolves to a real
    file relative to the design file itself, and its header comment records
    the origin design, the capture date, the viewport and the theme."""
    if not design_file.is_file():
        errors.append(f"{design_file}: missing — the watch-fidelity binder's design "
                      "reference, and every visual check built on it, has nothing to "
                      "compare against")
        return
    text = design_file.read_text(encoding="utf-8")
    hosts = sorted(set(_EXTERNAL_URL_RE.findall(text)))
    if hosts:
        errors.append(f"{design_file}: references an external host ({', '.join(hosts[:3])}) "
                      "— the committed design reference must open with no network")
    for ref in _design_reference_asset_paths(text):
        if ref.startswith(("http://", "https://", "//")):
            continue  # already reported above as an external-host reference
        target = (design_file.parent / ref).resolve()
        if not target.is_file():
            errors.append(f"{design_file}: points at asset '{ref}' which does not "
                          "resolve to a file in this repo")
    header_m = _HEADER_COMMENT_RE.search(text)
    header = header_m.group(1) if header_m else ""
    if "1440" not in header or "900" not in header:
        errors.append(f"{design_file}: header comment must record the 1440x900 viewport")
    if "light" not in header.lower():
        errors.append(f"{design_file}: header comment must record the light theme")
    if not re.search(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", header):
        errors.append(f"{design_file}: header comment must record the capture date")
    if "origin" not in header.lower() and "claude-design://" not in header:
        errors.append(f"{design_file}: header comment must record the origin design")
    if DESIGN_FONT_CAVEAT_PHRASE not in header:
        errors.append(f"{design_file}: header comment must record that this file "
                      f"'{DESIGN_FONT_CAVEAT_PHRASE}' — it points at the page's own "
                      "vendored faces, so it agrees with the page about typefaces "
                      "whether or not either one is right, and a fidelity reference "
                      "read as complete is how a flattened serif goes unnoticed")
    elif DESIGN_FONT_CAVEAT_POINTER not in header:
        errors.append(f"{design_file}: the font caveat in the header comment must "
                      f"point at {DESIGN_FONT_CAVEAT_POINTER}, where the check that "
                      "CAN witness a font difference is briefed — a caveat naming "
                      "nowhere to go reads as reassurance")


def _check_design_fixture(errors: list[str], fixture_root: Path,
                          ref_prober=None) -> str | None:
    """The committed fixture is a repo root holding exactly one hand-written
    .karta/binders/<slug>.json, whose slug matches no karta/<slug>/* ref
    anywhere in this repo — so the wave shape the page renders when rooted
    there is fixed by that one committed file, readable in the diff, with
    nothing needing to be served to settle it. Returns the fixture's slug on
    success, None on any check failure. `ref_prober(slug) -> bool` (True if a
    matching ref exists) is the self-test injection seam — default probes
    this repo's real refs with `git for-each-ref`."""
    binders_dir = fixture_root / ".karta" / "binders"
    if not binders_dir.is_dir():
        errors.append(f"{fixture_root}: missing .karta/binders — not a fixture repo root")
        return None
    binder_files = sorted(binders_dir.glob("*.json"))
    if len(binder_files) != 1:
        errors.append(f"{fixture_root}/.karta/binders: must hold exactly one binder "
                      f"json (found {len(binder_files)})")
        return None
    try:
        binder = json.loads(binder_files[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{binder_files[0]}: invalid JSON ({e})")
        return None
    slug = binder.get("slug")
    if not slug or not isinstance(slug, str):
        errors.append(f"{binder_files[0]}: binder has no 'slug'")
        return None
    if ref_prober is None:
        def ref_prober(s: str) -> bool:
            proc = subprocess.run(["git", "for-each-ref", f"refs/karta/{s}/"],
                                  cwd=str(ROOT), capture_output=True, text=True)
            return bool(proc.stdout.strip())
    if ref_prober(slug):
        errors.append(f"{binder_files[0]}: slug '{slug}' matches a real karta/{slug}/* "
                      "ref in this repo — the fixture must use a slug that derives as "
                      "pending forever, never a real binder's slug")
        return None
    return slug


def _check_design_serving_rig(errors: list[str], fixture_root: Path, script: Path,
                              slug: str, *, timeout: float = 10.0) -> None:
    """Prove the rig this whole binder depends on, at item one rather than the
    end of the binder: start the committed page as a subprocess rooted at the
    committed fixture on a loopback port, request it with ?theme=light, and
    confirm HTTP 200 with the fixture's one binder actually rendered in the
    body. A wrong --root, a malformed fixture, or a page that stops serving
    then fails here instead of at the last item that needs it."""
    import socket
    import time
    import urllib.error
    import urllib.request

    if not script.is_file():
        errors.append(f"{script}: missing — cannot prove the watch-fidelity serving rig")
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, str(script), "--root", str(fixture_root), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        url = f"http://127.0.0.1:{port}/?theme=light"
        body = None
        last_err: Exception | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                last_err = RuntimeError(
                    f"exited early ({proc.returncode}): {proc.stderr.read() if proc.stderr else ''}")
                break
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        body = resp.read().decode("utf-8", "replace")
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last_err = e
                time.sleep(0.2)
        if body is None:
            errors.append(f"watch-fidelity serving rig: the page never answered 200 "
                          f"at --root {fixture_root} --port {port} ({last_err})")
            return
        if slug not in body:
            errors.append("watch-fidelity serving rig: the page answered but the "
                          f"fixture's binder ('{slug}') is not visibly rendered in the response")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# --- Watch design reference: the binder panel's ground, read from the design ---
#
# card-ground-and-tint-base puts the binder card on the surface role and the
# frame around it on the page-ground role. serve_status.py's own self-test
# proves WHICH role each container resolves to, but both of its sides descend
# from the page's _PALETTE, so it can only prove the page agrees with itself.
# The design side — the role the design's binder panel declares, and the value
# the design file itself declares for that role in each palette — is read HERE,
# from the file this validator already resolves by DESIGN_REFERENCE_REL, and
# held against what the shipped page resolves. It lives here and not in
# serve_status.py because that script's self-test is contracted to need no repo
# and ships to consumer installs that carry no docs/ at all.

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_ROOT_RULE_RE = re.compile(r"(:root[^{}]*)\{([^{}]*)\}")
_BODY_RULE_RE = re.compile(r"(?:^|[\s}])body\s*\{([^{}]*)\}")
_TOKEN_DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+)")
_ONE_TOKEN_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^<>]*)>")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
              "meta", "param", "source", "track", "wbr"}


def _decls(style: str) -> dict[str, str]:
    """`prop:value;…` as {prop: value} — an inline style or a rule body."""
    out: dict[str, str] = {}
    for decl in style.split(";"):
        prop, sep, value = decl.partition(":")
        if sep:
            out[prop.strip()] = value.strip()
    return out


def _inline_style(tag: str) -> dict[str, str]:
    m = re.search(r'style="([^"]*)"', tag)
    return _decls(m.group(1)) if m else {}


def _one_token(value: str) -> str | None:
    """The single palette token a declared value names, or None."""
    m = _ONE_TOKEN_RE.fullmatch((value or "").strip())
    return m.group(1) if m else None


def _design_palettes(text: str) -> dict[str, dict[str, str]]:
    """The design's palettes as {theme: {token: value}}, read off its :root
    rules: the light one is the bare :root or the rule naming
    data-theme="light"; the dark one names data-theme="dark"."""
    out: dict[str, dict[str, str]] = {}
    for prelude, body in _ROOT_RULE_RE.findall(text):
        selectors = [sel.strip() for sel in prelude.split(",")]
        themes = {"dark" if 'data-theme="dark"' in sel else "light"
                  for sel in selectors if sel == ":root" or "data-theme=" in sel}
        for theme in themes:
            out.setdefault(theme, {}).update(
                {k: v.strip() for k, v in _TOKEN_DECL_RE.findall(body)})
    return out


def _direct_children(text: str, start: int) -> list[str]:
    """The start tags one level inside the element whose start tag begins at
    `start` — its direct children, depth-counted so a nested box of the same
    name cannot close it early and a void or self-closing tag opens nothing."""
    first = _TAG_RE.match(text, start)
    if not first:
        return []
    out, depth = [], 0
    for m in _TAG_RE.finditer(text, first.end()):
        closing, name, rest = m.group(1), m.group(2).lower(), m.group(3)
        if closing:
            depth -= 1
            if depth < 0:
                break
            continue
        if depth == 0:
            out.append(m.group(0))
        if name not in _VOID_TAGS and not rest.rstrip().endswith("/"):
            depth += 1
    return out


def _check_design_panel_ground(errors: list[str], design_file: Path, *,
                               page_card_roles: set[str], page_frame_roles: set[str],
                               page_palette: dict[str, dict[str, str]]) -> None:
    """The binder card resolves to the role the design gives its binder panel,
    and the frame around it to the role the design paints its page in — and
    the value the page resolves each role to, in both palettes, equals the value
    the design file itself declares. The design side is read from the file, so
    this cannot pass by agreeing with itself the way a check whose two sides
    both descend from _PALETTE does.

    The design's binder panel is found structurally: inside every
    data-kw-panel section, the one direct child carrying a 1px line border and
    a ground (export 294). The section itself must declare only display,
    direction and gap — nothing between it and that panel carries a surface
    (export 282), which is the fact the page's frame-on-the-page-ground answers
    to. The page ground is the role the design's body rule paints."""
    label = design_file.as_posix()
    if not design_file.is_file():
        return  # already reported by _check_design_self_contained
    text = design_file.read_text(encoding="utf-8")
    palettes = _design_palettes(text)
    missing = [t for t in ("light", "dark") if t not in palettes]
    if missing:
        errors.append(f"{label}: declares no {' or '.join(missing)} palette (:root rule) "
                      "to read the binder panel's ground from")
        return
    style = "\n".join(_STYLE_BLOCK_RE.findall(text))
    body = _BODY_RULE_RE.search(style)
    ground_role = _one_token(_decls(body.group(1)).get("background", "")) if body else None
    if ground_role is None:
        errors.append(f"{label}: the body rule does not paint the page ground as one token")
        return
    panel_roles: set[str] = set()
    sections = [m.group(0) for m in _TAG_RE.finditer(text)
                if not m.group(1) and "data-kw-panel=" in m.group(3)]
    if not sections:
        errors.append(f"{label}: no data-kw-panel section to read the binder panel from")
        return
    for sec in sections:
        own = _inline_style(sec)
        if "background" in own or "background-color" in own:
            errors.append(f"{label}: a data-kw-panel section carries a surface of its own "
                          "(a frame) — the design puts nothing between the section and "
                          "the binder panel that carries one (export 282)")
        if set(own) - {"display", "flex-direction", "gap"}:
            errors.append(f"{label}: a data-kw-panel section declares more than display, "
                          f"direction and gap ({', '.join(sorted(set(own) - {'display', 'flex-direction', 'gap'}))})")
        panels = [c for c in _direct_children(text, text.index(sec))
                  if _inline_style(c).get("border", "").startswith("1px solid")
                  and "background" in _inline_style(c)]
        if len(panels) != 1:
            errors.append(f"{label}: a data-kw-panel section holds {len(panels)} direct "
                          "children with a 1px border and a ground; exactly one is the binder panel")
            continue
        role = _one_token(_inline_style(panels[0])["background"])
        if role is None:
            errors.append(f"{label}: the binder panel's ground is not one palette token")
            continue
        panel_roles.add(role)
    if len(panel_roles) != 1:
        errors.append(f"{label}: the binder panels resolve to {len(panel_roles)} ground roles; "
                      "expected one shared role")
        return
    card_role = panel_roles.pop()
    for role in (card_role, ground_role):
        for theme in ("light", "dark"):
            if role not in palettes[theme]:
                errors.append(f"{label}: the {theme} palette does not declare {role}")
            elif role not in page_palette:
                errors.append(f"watch panel ground: the page's palette does not declare {role}, "
                              f"which the design's {theme} palette does")
            elif palettes[theme][role].lower() != page_palette[role][theme].strip().lower():
                errors.append(f"watch panel ground: the design declares {role} as "
                              f"{palettes[theme][role]} in its {theme} palette; the page resolves "
                              f"it to {page_palette[role][theme]}")
    for theme in ("light", "dark"):
        if palettes[theme].get(card_role) == palettes[theme].get(ground_role):
            errors.append(f"{label}: {card_role} and {ground_role} resolve to the same value in "
                          f"the {theme} palette, so the binder panel cannot advance off the page")
    if page_card_roles != {card_role}:
        errors.append(f"watch panel ground: the design's binder panel resolves to {card_role}; "
                      f"the page's binder card resolves to {sorted(page_card_roles) or 'no role'}")
    if page_frame_roles != {ground_role}:
        errors.append(f"watch panel ground: the design paints its page in {ground_role}; the "
                      f"page's frame around the binder card resolves to "
                      f"{sorted(page_frame_roles) or 'no role'} — the frame the design never "
                      "modelled must sit on the page ground, not on a surface of its own")


def _watch_page_grounds() -> tuple[set[str], set[str], dict[str, dict[str, str]]]:
    """What the shipped page resolves: the binder card's and the delivery
    frame's ground roles — read by their data-kw hooks through the page's own
    stylesheet readers, never by class name — and the page's palette."""
    import importlib.util
    script = ROOT / "skills" / WATCH_SCRIPT_REL
    spec = importlib.util.spec_from_file_location("karta_watch_page", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # the page inserts its own directory on sys.path at import (to reach its
    # sibling engine); that is the page's business, not the validator's
    path_before = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = path_before
    css = mod._strip_css_comments(mod._page_css())

    def roles(hook: str) -> set[str]:
        tags = mod._tags_with(mod._APP_JS, hook)
        if len(tags) != 1:
            return set()
        return set(mod._VAR_REF_RE.findall(
            mod._resolved(mod._rules_for_tag(css, tags[0]), "background")))
    return roles("data-kw-binder"), roles("data-kw-delivery-panel"), mod._PALETTE


def _check_design_reference(errors: list[str]) -> None:
    """The watch-fidelity binder's design reference is self-contained and its
    fixture is well-formed, then — only once both hold — the serving rig
    itself is proven for real against the committed files. The three parts
    each take their target as an argument, so `_self_test()` drives every one
    of them against synthetic fixtures; this composes them over the real repo."""
    _check_design_self_contained(errors, ROOT / DESIGN_REFERENCE_REL)
    try:
        card_roles, frame_roles, palette = _watch_page_grounds()
    except Exception as e:  # a page that cannot be read is a reported failure, never a crash
        errors.append(f"skills/{WATCH_SCRIPT_REL.as_posix()}: could not read the page's "
                      f"grounds for the design comparison ({e})")
    else:
        _check_design_panel_ground(errors, ROOT / DESIGN_REFERENCE_REL,
                                   page_card_roles=card_roles, page_frame_roles=frame_roles,
                                   page_palette=palette)
    fixture_root = ROOT / DESIGN_FIXTURE_REL
    before = len(errors)
    slug = _check_design_fixture(errors, fixture_root)
    if slug is None or len(errors) > before:
        return  # fixture is malformed — nothing to serve, don't spin up the rig
    _check_design_serving_rig(errors, fixture_root, ROOT / "skills" / WATCH_SCRIPT_REL, slug)


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
    total = 0  # incremented once per [PASS]/[FAIL] line printed below — never hand-summed,
    # so a case group (or a standalone check with no list of its own) added later cannot
    # silently under-report: it counts itself the moment it prints its own result line.
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
            total += 1
            failures += 0 if ok else 1
        errors = []
        _check_doc_gardner(errors, config=Path(td) / "absent.json", schema=Path(td) / "schema0.json")
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] absent config stays valid" + ("" if ok else f" — got {errors!r}"))
        total += 1
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
            total += 1
            failures += 0 if ok else 1
        errs = []
        _check_behaviour_anchor(errs, anchor=Path(td) / "absent-anchor.txt", registry=live)
        ok = bool(errs) and any("missing" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] anchor floor: a missing anchor fails"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # The fact-trace floor: the sweep is wired, it fails on an untraced fact, and it
        # reaches an archive/ subdirectory too — a LIST-shaped fact table there is swept
        # exactly like a live one (frozen history is no excuse for a broken trace).
        def _fact_binder(traced: bool) -> str:
            row = {"id": "a-fact", "claim": "x", "traced_by": ["it:0"] if traced else []}
            return json.dumps({"slug": "fx", "work_items": [{"id": "it", "oracle": {"assertions": ["a"]}}],
                               "token_manifest": {"design_fact_table": [row]}})
        bd = Path(td) / "binders"
        (bd / "archive").mkdir(parents=True)
        (bd / "ok.json").write_text(_fact_binder(True))
        (bd / "archive" / "frozen.json").write_text(_fact_binder(False))
        errs = []
        _check_fact_traces(errs, binders_dir=bd)
        ok = (len(errs) == 1 and "archive" in errs[0] and "frozen.json" in errs[0]
              and "fact 'a-fact' is untraced" in errs[0])
        print(f"[{'PASS' if ok else 'FAIL'}] fact traces: a traced live binder passes; an untraced archived binder fails too"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1
        (bd / "gap.json").write_text(_fact_binder(False))
        errs = []
        _check_fact_traces(errs, binders_dir=bd)
        ok = (len(errs) == 2
              and any("gap.json" in e and "fact 'a-fact' is untraced" in e for e in errs)
              and any("archive" in e and "frozen.json" in e and "fact 'a-fact' is untraced" in e for e in errs))
        print(f"[{'PASS' if ok else 'FAIL'}] fact traces: an untraced fact in a live binder fails the floor"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
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
            total += 1
            failures += 0 if ok else 1

        # The vendored fonts: a synthetic repo shape with the canonical tree and
        # both Codex mirrors, then one deliberately broken copy per rule. Font
        # drift is invisible in a diff, so each rule is driven against a known-bad
        # tree rather than only against the (clean) real one.
        # The synthetic manifest is a COMPLETE one — provenance block, a
        # variable face and a static one cut from the same source — so each
        # negative control below can break exactly one rule by patching it.
        demo_commit = "0" * 40

        def _demo_manifest() -> dict:
            return {
                "subsetting": {"fonttools_version": "4.63.0",
                               "upstream_commit": demo_commit},
                "families": {"Demo": {
                    "licence": "demo-OFL.txt",
                    "source_url": f"https://example.invalid/{demo_commit}/demo",
                    "source_files": "Demo[wght].ttf",
                    "pinned_axes": None,
                    "axes": {"wght": "400..500"}}},
                "faces": [
                    {"family": "Demo", "weight": "400 500",
                     "file": "demo-400.woff2", "source_file": "Demo[wght].ttf",
                     "source_sha256": "a" * 64, "variable": True},
                    {"family": "Demo", "weight": 600, "file": "demo-600.woff2",
                     "source_file": "Demo[wght].ttf",
                     "source_sha256": "a" * 64}],
            }

        def _font_tree(base: Path, *, licence=True, mirror_bytes=b"WOFF2",
                       script_bytes=b"# page\n", manifest=True, patch=None):
            for rel in ("skills",) + tuple(m.as_posix() for m in MIRROR_SKILL_ROOTS):
                (base / rel / WATCH_FONTS_REL).mkdir(parents=True, exist_ok=True)
                (base / rel / WATCH_SCRIPT_REL).parent.mkdir(parents=True, exist_ok=True)
            canon = base / "skills" / WATCH_FONTS_REL
            (canon / "demo-400.woff2").write_bytes(b"WOFF2")
            if licence:
                (canon / "demo-OFL.txt").write_text("SIL OPEN FONT LICENSE")
            if manifest:
                doc = _demo_manifest()
                if patch:
                    patch(doc)
                (canon / "manifest.json").write_text(json.dumps(doc))
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
            # provenance: complete, and not contradicting itself. None of it is
            # verified against the upstream — these controls prove the record is
            # held to being whole and self-consistent, which is all it claims.
            ("fonts: a manifest with no fontTools version fails (the recipe is not "
             "byte-reproducible, so the version is the record)",
             {"patch": lambda d: d["subsetting"].pop("fonttools_version")},
             ["records no fonttools_version"]),
            ("fonts: an EMPTY fontTools version fails rather than passing as present",
             {"patch": lambda d: d["subsetting"].update(fonttools_version="  ")},
             ["records no fonttools_version"]),
            ("fonts: a manifest with no upstream commit fails",
             {"patch": lambda d: d["subsetting"].pop("upstream_commit")},
             ["records no upstream_commit"]),
            ("fonts: a family source_url that does not name the pinned commit fails",
             {"patch": lambda d: d["families"]["Demo"].update(
                 source_url="https://example.invalid/deadbeef/demo")},
             ["does not name the pinned upstream commit"]),
            ("fonts: a face with no well-formed source digest fails",
             {"patch": lambda d: d["faces"][0].update(source_sha256="nope")},
             ["no well-formed source_sha256"]),
            ("fonts: two faces cut from one source file recording different digests fails",
             {"patch": lambda d: d["faces"][1].update(source_sha256="b" * 64)},
             ["disagrees with another face"]),
            ("fonts: a variable face whose family still pins an axis fails — the pin "
             "describes the flattened cut it replaced",
             {"patch": lambda d: d["families"]["Demo"].update(
                 pinned_axes={"opsz": 18})},
             ["still declaring pinned_axes"]),
            ("fonts: a variable face whose family records no axes fails",
             {"patch": lambda d: d["families"]["Demo"].update(axes=None)},
             ["records no axes"]),
        ]
        for i, (name, kwargs, want) in enumerate(font_cases):
            errs = []
            _check_vendored_fonts(errs, root=_font_tree(Path(td) / f"fonts{i}", **kwargs))
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The watch design reference: a self-contained good capture, then one
        # deliberately violating negative control per rule — carrying an
        # external stylesheet, a dangling asset, a bare/incomplete header, or
        # a malformed fixture. Each is built fresh in this temp dir and
        # committed nowhere; the checks below are the static, fast half of
        # _check_design_reference (no subprocess) — the real serving-rig
        # proof runs only against the real repo, from `check()`.
        caveat_line = (f'  This file {DESIGN_FONT_CAVEAT_PHRASE}; see '
                       f'{DESIGN_FONT_CAVEAT_POINTER}.\n')
        good_header = ('<!--\n  Origin design : claude-design://demo/Demo.dc.html\n'
                       '  Captured      : 2026-08-17\n  Viewport      : 1440x900\n'
                       '  Theme         : light\n' + caveat_line + '-->\n')

        def _design_file(base: Path, *, header=good_header,
                         asset_tag='<img src="mascot.png">',
                         extra="", with_asset=True) -> Path:
            base.mkdir(parents=True, exist_ok=True)
            if with_asset:
                (base / "mascot.png").write_bytes(b"PNG")
            f = base / "design.html"
            f.write_text(f"<!DOCTYPE html>\n<html>\n<head>\n{header}"
                         f"<style>body{{color:red}}</style>\n</head>\n"
                         f"<body>\n{asset_tag}\n{extra}\n</body>\n</html>\n")
            return f

        design_cases = [
            ("design: self-contained capture with a full header -> no error",
             lambda b: _design_file(b), []),
            ("design: an external stylesheet fails (the required negative control)",
             lambda b: _design_file(b, extra='<link href="https://fonts.googleapis.com/x" rel="stylesheet">'),
             ["references an external host"]),
            ("design: a dangling asset reference fails",
             lambda b: _design_file(b, asset_tag='<img src="missing.png">', with_asset=False),
             ["does not resolve to a file"]),
            ("design: a header missing the viewport fails",
             lambda b: _design_file(b, header='<!--\n  Captured: 2026-08-17\n  Theme: light\n  Origin: demo\n-->\n'),
             ["must record the 1440x900 viewport"]),
            ("design: a header missing the theme fails",
             lambda b: _design_file(b, header='<!--\n  Captured: 2026-08-17\n  Viewport: 1440x900\n  Origin: demo\n-->\n'),
             ["must record the light theme"]),
            ("design: a header missing the capture date fails",
             lambda b: _design_file(b, header='<!--\n  Viewport: 1440x900\n  Theme: light\n  Origin: demo\n-->\n'),
             ["must record the capture date"]),
            ("design: a missing design file fails",
             lambda b: b / "nope.html", ["missing"]),
            ("design: a protocol-relative //host reference fails too (no scheme to grep for)",
             lambda b: _design_file(b, extra='<link href="//fonts.googleapis.com/x" rel="stylesheet">'),
             ["references an external host"]),
            ("design: a header with no font caveat fails — the reference renders in "
             "the page's own faces and must say so",
             lambda b: _design_file(b, header=good_header.replace(caveat_line, "")),
             ["cannot witness a font difference"]),
            ("design: a font caveat pointing nowhere fails — a caveat with no brief "
             "behind it reads as reassurance",
             lambda b: _design_file(b, header=good_header.replace(
                 caveat_line, f'  This file {DESIGN_FONT_CAVEAT_PHRASE}.\n')),
             ["must point at"]),
        ]
        for i, (name, make, want) in enumerate(design_cases):
            errs: list[str] = []
            target = make(Path(td) / f"design{i}")
            _check_design_self_contained(errs, target)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The design fixture: a well-formed synthetic fixture repo root, then
        # one malformed variant per rule — no .karta/binders, zero or two
        # binder files, invalid JSON, no slug, and a slug a fake ref_prober
        # reports as already real (the collision this fixture's whole point
        # is to avoid).
        def _fixture_root(base: Path, *, binders="one", slug="fixture-demo-slug",
                          valid_json=True) -> Path:
            d = base / ".karta" / "binders"
            if binders != "absent":
                d.mkdir(parents=True, exist_ok=True)
            if binders in ("one", "two"):
                payload = json.dumps({"slug": slug}) if valid_json else "{not json"
                (d / "a.json").write_text(payload)
            if binders == "two":
                (d / "b.json").write_text(json.dumps({"slug": slug + "-2"}))
            if binders == "no-slug":
                (d / "a.json").write_text(json.dumps({"title": "no slug here"}))
            return base

        fixture_cases = [
            ("fixture: one well-formed binder, unclaimed slug -> no error",
             lambda b: _fixture_root(b), None, []),
            ("fixture: no .karta/binders -> not a fixture repo root",
             lambda b: _fixture_root(b, binders="absent"), None,
             ["not a fixture repo root"]),
            ("fixture: two binder files -> exactly one required",
             lambda b: _fixture_root(b, binders="two"), None,
             ["must hold exactly one binder"]),
            ("fixture: invalid JSON -> reported, not crashed",
             lambda b: _fixture_root(b, valid_json=False), None,
             ["invalid JSON"]),
            ("fixture: binder with no slug -> reported",
             lambda b: _fixture_root(b, binders="no-slug"), None,
             ["binder has no 'slug'"]),
            ("fixture: slug the prober reports as a real ref -> collision fails",
             lambda b: _fixture_root(b), lambda s: True,
             ["matches a real"]),
        ]
        for i, (name, make, prober, want) in enumerate(fixture_cases):
            errs: list[str] = []
            root = make(Path(td) / f"fixture{i}")
            _check_design_fixture(errs, root, ref_prober=prober)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The binder panel's ground, read from a synthetic design against a
        # stand-in page side: the agreeing pair first, then one violating
        # control per rule — the page's pre-item assignment (card on the
        # ground, frame on the surface) and each half of it alone, a design
        # value the page does not resolve to, a design panel on the page
        # ground, a section carrying a surface of its own, a design with no
        # dark palette, and a body that paints no single token.
        def _ground_design(base: Path, *, panel_bg="var(--surface)",
                           section_style="display:flex;flex-direction:column;gap:22px",
                           light_surface="#FFFFFF", dark=True, body_bg="var(--bg)") -> Path:
            base.mkdir(parents=True, exist_ok=True)
            dark_rule = (':root[data-theme="dark"]{--bg:#2B0F14;--surface:#3B141B;--line:#444}'
                         if dark else "")
            f = base / "design.html"
            f.write_text(
                "<!DOCTYPE html>\n<html>\n<head>\n<style>\n"
                ':root, :root[data-theme="light"]{--bg:#F6EFEE;--surface:' + light_surface
                + ";--line:#DFCBC6}\n" + dark_rule + "\n*{margin:0}\n"
                "body{background:" + body_bg + ";color:#000}\n</style>\n</head>\n<body>\n"
                '<main><section data-kw-panel="a" style="' + section_style + '">\n'
                '<div style="background:var(--band);border-radius:16px"><p>band</p></div>\n'
                '<div style="background:' + panel_bg + ';border:1px solid var(--line);'
                'border-radius:16px"><div style="padding:4px"><br>x</div></div>\n'
                "</section></main>\n</body>\n</html>\n")
            return f

        page_side = {"page_card_roles": {"--surface"}, "page_frame_roles": {"--bg"},
                     "page_palette": {"--bg": {"light": "#F6EFEE", "dark": "#2B0F14"},
                                      "--surface": {"light": "#FFFFFF", "dark": "#3B141B"}}}
        ground_cases = [
            ("ground: design panel on the surface, page card on it, values agree -> no error",
             lambda b: _ground_design(b), page_side, []),
            ("ground: the pre-item assignment — card on the ground, frame on the surface — fails",
             lambda b: _ground_design(b),
             dict(page_side, page_card_roles={"--bg"}, page_frame_roles={"--surface"}),
             ["binder card resolves to ['--bg']", "frame around the binder card resolves to ['--surface']"]),
            ("ground: the page card on the ground alone fails",
             lambda b: _ground_design(b), dict(page_side, page_card_roles={"--bg"}),
             ["binder card resolves to ['--bg']"]),
            ("ground: the page frame on the surface alone fails",
             lambda b: _ground_design(b), dict(page_side, page_frame_roles={"--surface"}),
             ["frame around the binder card resolves to ['--surface']"]),
            ("ground: a page card with no role (a literal) fails",
             lambda b: _ground_design(b), dict(page_side, page_card_roles=set()),
             ["binder card resolves to no role"]),
            ("ground: a design surface value the page does not resolve to fails",
             lambda b: _ground_design(b, light_surface="#FFFFFE"), page_side,
             ["design declares --surface as #FFFFFE in its light palette"]),
            ("ground: a design panel declared on the page ground fails",
             lambda b: _ground_design(b, panel_bg="var(--bg)"), page_side,
             ["binder panel resolves to --bg"]),
            ("ground: a section carrying a surface of its own (a frame) fails",
             lambda b: _ground_design(b, section_style="display:flex;flex-direction:column;"
                                                       "gap:22px;background:var(--surface)"),
             page_side, ["carries a surface of its own"]),
            ("ground: a design with no dark palette fails",
             lambda b: _ground_design(b, dark=False), page_side, ["declares no dark palette"]),
            ("ground: a body that paints no single token fails",
             lambda b: _ground_design(b, body_bg="#fff"), page_side,
             ["body rule does not paint the page ground as one token"]),
        ]
        for i, (name, make, side, want) in enumerate(ground_cases):
            errs: list[str] = []
            _check_design_panel_ground(errs, make(Path(td) / f"ground{i}"), **side)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The serving rig, driven against stand-in pages rather than only
        # against the real one. The rig check is the assertion that a wrong
        # --root, a malformed fixture, or a page that stopped serving fails
        # at item one — so it ships with the pages that make it fail: one
        # that never answers, one that answers 200 without the fixture's
        # binder in the body, and a missing script. `serve_status.py` is
        # never started here; each stand-in takes the same --root/--port.
        rig_dir = Path(td) / "rig"
        rig_dir.mkdir()

        def _stand_in(name: str, body_expr: str) -> Path:
            p = rig_dir / name
            p.write_text(
                "import argparse, http.server\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--root'); p.add_argument('--port', type=int)\n"
                "a = p.parse_args()\n"
                f"BODY = {body_expr}\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        b = BODY.encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Length', str(len(b)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(b)\n"
                "    def log_message(self, *a): pass\n"
                "http.server.HTTPServer(('127.0.0.1', a.port), H).serve_forever()\n")
            return p

        serving = _stand_in("serving.py", "'<html>fixture-demo-slug rendered</html>'")
        blank = _stand_in("blank.py", "'<html>no binder here</html>'")
        dead = rig_dir / "dead.py"
        dead.write_text("import sys\nsys.exit(3)\n")

        rig_cases = [
            ("rig: a page serving 200 with the fixture's binder in the body -> no error",
             serving, []),
            ("rig: a page answering 200 WITHOUT the fixture's binder fails "
             "(the negative control for a wrong --root)",
             blank, ["is not visibly rendered"]),
            ("rig: a page that exits instead of serving fails",
             dead, ["never answered 200"]),
            ("rig: a missing page script fails",
             rig_dir / "absent.py", ["missing"]),
        ]
        for name, script, want in rig_cases:
            errs: list[str] = []
            _check_design_serving_rig(errs, rig_dir, script, "fixture-demo-slug",
                                      timeout=4.0)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1
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


def _check_fact_traces(errors: list[str], binders_dir: Path | None = None) -> None:
    """Every design fact a binder records must be traced to an assertion
    ('<item-id>:<0-based assertion index>') or carry an untraced_reason — the rule
    scripts/check_fact_traces.py owns. It runs here so a fact table added later cannot
    go untraced without failing the floor. The sweep is .karta/binders/*.json plus its
    archive/ subdirectory (see check_fact_traces.check_path): a LIST-shaped fact table
    is checked there exactly like a live one — archived does not mean exempt — while a
    DICT-shaped, pre-convention table is reported as OUT OF SCOPE by name rather than
    checked or silently passed over. That report must be visible at THIS enforced floor,
    not only in the standalone script, so its notes are printed here rather than
    discarded. The script's own fixtures run too, the way every other gated script's do.
    `binders_dir` is the self-test seam; the default sweeps this repo."""
    if binders_dir is None:
        binders_dir = ROOT / ".karta" / "binders"
        _run_self_test(ROOT / "scripts" / "check_fact_traces.py", errors)
    if binders_dir.is_dir():
        swept, notes = check_fact_traces.check_path(binders_dir)
        errors.extend(f"fact traces: {e}" for e in swept)
        for n in notes:
            print(f"  ~ fact traces: {n}")


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
