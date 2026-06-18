# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate the Codex projections of karta's gate agents from the canonical `agents/*.md`.

Each `agents/<name>.md` (frontmatter + body) is the single source of truth. This
script emits two drift-guarded projections:

  1. `.codex/agents/<name>.toml`            — the registered Codex subagent
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
BUNDLE_DIR = ROOT / "skills" / "karta-verify" / "references"  # sole spawn site

SANDBOX_MODE = "read-only"


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


def render_toml(name: str, description: str, body: str) -> str:
    if "'''" in body:
        raise ValueError(f"{name}: body contains ''' which breaks a TOML literal string")
    return (
        f"# Generated from agents/{name}.md by scripts/sync_codex_agents.py — do not edit by hand.\n"
        f"name = {json.dumps(name)}\n"
        f"description = {json.dumps(description)}\n"
        f"sandbox_mode = {json.dumps(SANDBOX_MODE)}\n"
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
        out[CODEX_AGENTS / f"{name}.toml"] = render_toml(name, description, body)
        out[BUNDLE_DIR / f"{name}.agent.md"] = render_bundle(body)
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
