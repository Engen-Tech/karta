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
    _check_kaizen_shadow(errors)
    return errors


def _check_kaizen_shadow(errors: list[str]) -> None:
    """Kaizen dogfood guard: this repo authors the built-in packs, so its seeded
    .karta/sme/minimalism.md is a managed shadow that must stay byte-identical to
    the canonical skills/_shared/sme/minimalism.md. A kaizen (or hand) edit to the
    shadow must be either discarded or promoted upstream into the canonical pack —
    never left to drift."""
    shadow = ROOT / ".karta/sme/minimalism.md"
    canonical = ROOT / "skills/_shared/sme/minimalism.md"
    if shadow.exists() and canonical.exists() and shadow.read_bytes() != canonical.read_bytes():
        errors.append(
            ".karta/sme/minimalism.md: differs from skills/_shared/sme/minimalism.md — "
            "this repo's seeded copy is a managed shadow (kaizen dogfood policy, see AGENTS.md): "
            "discard the shadow edit, or promote it into the canonical pack and re-copy"
        )


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
    for p, content in sorted(want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from .agents/skills mirror (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical skill (run sync_codex_skills.py)")
    for p in sorted(set(sync_codex_skills.mirror_files()) - set(want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in mirror (no canonical source)")
    for name in sorted(sync_codex_skills.mirror_skill_names() - names):
        errors.append(f".agents/skills/{name}: orphaned (no skills/{name})")
    install_want = sync_codex_skills.expected_install_projection()
    install_have = set(sync_codex_skills.install_projection_files())
    for p, content in sorted(install_want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from Codex install projection (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical Codex install projection (run sync_codex_skills.py)")
    for p in sorted(install_have - set(install_want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in Codex install projection (no canonical source)")
    for name in sorted(sync_codex_skills.install_projection_skill_names() - names):
        errors.append(f"plugins/karta/skills/{name}: orphaned (no skills/{name})")

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

    # 6. doc-gardner opt-in config — if a repo commits one, it must be well-formed.
    dg = ROOT / ".karta" / "doc-gardner.json"
    if dg.exists():
        try:
            cfg = json.loads(dg.read_text())
        except json.JSONDecodeError as e:
            errors.append(f".karta/doc-gardner.json: invalid JSON ({e})")
            cfg = None
        if isinstance(cfg, dict):
            if not isinstance(cfg.get("enabled"), bool):
                errors.append(".karta/doc-gardner.json: 'enabled' must be a boolean")
            for key in cfg:
                if key not in ("enabled", "focus"):
                    errors.append(f".karta/doc-gardner.json: unknown key '{key}' (allowed: enabled, focus)")

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
        try:
            proc = subprocess.run([sys.executable, str(script), "--self-test"],
                                  capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"{script.relative_to(ROOT)}: --self-test did not run ({e})")
            continue
        if proc.returncode != 0:
            tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
            errors.append(f"{script.relative_to(ROOT)}: --self-test failed ({tail})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.parse_args()
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
