# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate the Codex projections of karta's gate agents from the canonical `agents/*.md`.

Each `agents/<name>.md` (frontmatter + body) is the single source of truth. This
script emits two drift-guarded projections:

  1. `.codex/agents/<name>.toml`            — the registered Codex subagent, carrying
     its pinned `model` (from frontmatter `codex_model`) and `model_reasoning_effort`
     (from frontmatter `effort`)
  2. `skills/karta-verify/references/<name>.agent.md` — the body, bundled in the
     karta-verify skill so the gate runs automatically on a Codex plugin install

Usage:
  uv run scripts/sync_codex_agents.py            # write both projections
  uv run scripts/sync_codex_agents.py --check    # report drift, exit 0/1 (no writes)
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"
CODEX_AGENTS = ROOT / ".codex" / "agents"

# Effort vocabulary shared by Claude Code and Codex — the only levels that mean the
# same thing on both hosts. `max` is Claude-only and `minimal` is Codex-only, so
# neither is allowed here: one `effort` value must project cleanly to both runtimes.
ALLOWED_EFFORT = {"low", "medium", "high", "xhigh"}

# Each agent's sole spawn-site skill. Its instructions are bundled in that skill's
# references/ so the gate/gardner runs on a Codex plugin install (which cannot
# register subagents). A new agent without a mapping here is a hard error.
BUNDLE_SITE = {
    "karta-acceptance-reviewer": "karta-verify",
    "karta-safety-auditor": "karta-verify",
    "karta-design-reviewer": "karta-verify",
    "karta-doc-gardner": "karta-doc-gardner",
    "karta-kaizen": "karta-kaizen",
}


def sandbox_mode_for(fm: dict[str, str]) -> str:
    """Derive the Codex sandbox from the agent's declared tools, never hand-set, so
    the sandbox always matches the tool surface: a doc-writer (Write/Edit in `tools`)
    gets workspace-write; a read-only gate gets read-only."""
    tools = {t.strip() for t in fm.get("tools", "").split(",") if t.strip()}
    return "workspace-write" if (tools & {"Write", "Edit"}) else "read-only"


def parse_agent(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body) for a `---`-fenced markdown agent file."""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    fm: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    body = text[end + len("\n---"):].lstrip("\n").rstrip()
    return fm, body


def render_toml(name: str, description: str, body: str, sandbox: str,
                codex_model: str, effort: str) -> str:
    if "'''" in body:
        raise ValueError(f"{name}: body contains ''' which breaks a TOML literal string")
    return (
        f"# Generated from agents/{name}.md by scripts/sync_codex_agents.py — do not edit by hand.\n"
        f"name = {json.dumps(name)}\n"
        f"description = {json.dumps(description)}\n"
        f"model = {json.dumps(codex_model)}\n"
        f"model_reasoning_effort = {json.dumps(effort)}\n"
        f"sandbox_mode = {json.dumps(sandbox)}\n"
        f"developer_instructions = '''\n{body}\n'''\n"
    )


def render_bundle(body: str) -> str:
    return body + "\n"


def projections() -> dict[Path, str]:
    """Map each projection path to its expected content."""
    out: dict[Path, str] = {}
    sources = sorted(AGENTS.glob("*.md"))
    if not sources:
        raise SystemExit("no agents found under agents/*.md")
    for src in sources:
        fm, body = parse_agent(src.read_text())
        name = fm.get("name") or src.stem
        description = fm.get("description", "")
        if not description:
            raise SystemExit(f"{src.name}: missing frontmatter 'description'")
        site = BUNDLE_SITE.get(name)
        if site is None:
            raise SystemExit(f"{src.name}: no BUNDLE_SITE mapping for agent '{name}' — add its spawn-site skill")
        codex_model = fm.get("codex_model")
        if not codex_model:
            raise SystemExit(f"{src.name}: missing frontmatter 'codex_model' — the Codex model to pin for this agent")
        effort = fm.get("effort")
        if effort not in ALLOWED_EFFORT:
            raise SystemExit(
                f"{src.name}: frontmatter 'effort' must be one of {sorted(ALLOWED_EFFORT)} "
                f"(shared Claude+Codex vocabulary; not Claude-only 'max' or Codex-only 'minimal'), got {effort!r}")
        out[CODEX_AGENTS / f"{name}.toml"] = render_toml(
            name, description, body, sandbox_mode_for(fm), codex_model, effort)
        out[ROOT / "skills" / site / "references" / f"{name}.agent.md"] = render_bundle(body)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    args = ap.parse_args()

    expected = projections()

    if args.check:
        drift = [p for p, content in expected.items()
                 if not p.exists() or p.read_text() != content]
        if drift:
            print("CODEX AGENTS: DRIFT")
            for p in sorted(drift):
                why = "missing" if not p.exists() else "differs from agents/*.md"
                print(f"  - {p.relative_to(ROOT)} ({why}) — run: uv run scripts/sync_codex_agents.py")
            return 1
        print("CODEX AGENTS: IN SYNC")
        return 0

    for p, content in expected.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        print(f"wrote {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
