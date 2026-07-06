#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pre/PostToolUse guard: stack packs land only in validator-clean form.

Zero dependencies (pure stdlib). The harness invokes this with the hook payload
JSON on stdin. Targets under `.karta/sme/*.md` are checked with the plugin's pack
validator (`skills/karta-kaizen/scripts/validate_packs.py`, resolved via
CLAUDE_PLUGIN_ROOT, falling back to this script's own plugin root):

  - PreToolUse `Write`: the proposed content is validated from a temp file; a
    failure denies the write (exit 2, findings on stderr).
  - PostToolUse `Edit`/`Write`: the file on disk is validated; a failure exits 2
    so the findings reach the model as feedback it must fix.

This is the kaizen pre-land syntax check enforced below the agent. Any internal
error fails open (exit 0): this guard must never break an unrelated tool call.

  guard_pack_write.py              # hook mode: payload on stdin, exit 0/2
  guard_pack_write.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

PACK_RE = re.compile(r"(?:^|/)\.karta/sme/.+\.md$")
VALIDATOR_REL = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"


def _validator_path() -> Path | None:
    roots: list[Path] = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        roots.append(Path(env))
    roots.append(Path(__file__).resolve().parent.parent.parent)  # <plugin root>/hooks/scripts/..
    for root in roots:
        cand = root / VALIDATOR_REL
        if cand.is_file():
            return cand
    return None


def _run_validator(validator: Path, pack_file: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(validator), str(pack_file)],
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def decide(payload: dict) -> tuple[int, str]:
    """Return (exit_code, stderr_message)."""
    tool_input = payload.get("tool_input")
    target = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(target, str) or not PACK_RE.search(target.replace("\\", "/")):
        return 0, ""
    validator = _validator_path()
    if validator is None:
        return 0, ""  # fail open: no validator to consult
    cwd = payload.get("cwd") or os.getcwd()

    if payload.get("hook_event_name") == "PreToolUse":
        if payload.get("tool_name") != "Write":
            return 0, ""  # an Edit delta has no full content to validate; PostToolUse covers it
        content = tool_input.get("content")
        if not isinstance(content, str):
            return 0, ""
        with tempfile.TemporaryDirectory() as td:
            # keep the target's basename: the validator checks `name` == file basename
            probe = Path(td) / Path(target.replace("\\", "/")).name
            probe.write_text(content)
            rc, findings = _run_validator(validator, probe)
        if rc != 0:
            return 2, (
                f"karta: '{target}' is a stack pack and the proposed content fails "
                "validate_packs.py, so the write is denied — packs land only in validator-clean "
                "form (the kaizen pre-land syntax check, enforced below the agent). Fix the "
                f"findings and write the pack again.\n\n{findings}")
        return 0, ""

    # PostToolUse (Edit|Write): the pack is already on disk — validate it and, on
    # failure, feed the findings back so the model must repair it before moving on.
    abs_target = Path(target) if os.path.isabs(target) else Path(cwd) / target
    if not abs_target.is_file():
        return 0, ""
    rc, findings = _run_validator(validator, abs_target)
    if rc != 0:
        return 2, (
            f"karta: '{target}' is a stack pack and the content now on disk fails "
            "validate_packs.py. Repair the pack until the validator passes before doing anything "
            "else — a malformed pack silently drops out of plan-time matching and audit-time "
            f"checklists.\n\n{findings}")
    return 0, ""


# --- Self-test fixtures --------------------------------------------------------

_VALID_PACK = """\
---
name: terraform
description: Terraform pack fixture
match: ["terraform"]
---
## Review checklist
- [ ] tf.1 — Pin provider versions.
"""

_INVALID_PACK = """\
## Review checklist
- [ ] tf.1 — A pack with no frontmatter at all.
"""


def _run_self_test() -> int:
    if _validator_path() is None:
        print("[FAIL] validator not resolvable (skills/karta-kaizen/scripts/validate_packs.py)")
        print("\n0/1 checks passed")
        return 1

    with tempfile.TemporaryDirectory() as td:
        cwd = str(td)
        sme = Path(td) / ".karta" / "sme"
        sme.mkdir(parents=True)
        (sme / "terraform.md").write_text(_VALID_PACK)
        (sme / "broken.md").write_text(_INVALID_PACK)

        def pre_write(path: str, content: str | None) -> dict:
            ti: dict = {"file_path": path}
            if content is not None:
                ti["content"] = content
            return {"hook_event_name": "PreToolUse", "tool_name": "Write",
                    "cwd": cwd, "tool_input": ti}

        def post(tool: str, path: str) -> dict:
            return {"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": cwd,
                    "tool_input": {"file_path": path}, "tool_response": {"success": True}}

        cases = [
            ("pre-write valid pack passes",
             pre_write(".karta/sme/terraform.md", _VALID_PACK), 0, None),
            ("pre-write invalid pack denied",
             pre_write(".karta/sme/terraform.md", _INVALID_PACK), 2, "frontmatter"),
            ("pre-write name/basename mismatch denied",
             pre_write(".karta/sme/angular.md", _VALID_PACK), 2, "basename"),
            ("pre-write outside .karta/sme passes",
             pre_write("docs/sme/terraform.md", _INVALID_PACK), 0, None),
            ("pre-write non-md under .karta/sme passes",
             pre_write(".karta/sme/notes.txt", "x"), 0, None),
            ("pre-write without content passes (nothing to validate)",
             pre_write(".karta/sme/terraform.md", None), 0, None),
            ("PreToolUse Edit passes (PostToolUse covers it)",
             {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": cwd,
              "tool_input": {"file_path": ".karta/sme/broken.md",
                             "old_string": "a", "new_string": "b"}}, 0, None),
            ("post-write valid pack on disk passes",
             post("Write", ".karta/sme/terraform.md"), 0, None),
            ("post-edit invalid pack on disk feeds back",
             post("Edit", ".karta/sme/broken.md"), 2, "frontmatter"),
            ("post on missing file passes",
             post("Write", ".karta/sme/ghost.md"), 0, None),
            ("tool_input not a dict passes",
             {"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": cwd,
              "tool_input": "junk"}, 0, None),
        ]
        failures = 0
        for name, payload, want, needle in cases:
            code, msg = decide(payload)
            ok = code == want and (needle is None or needle in msg)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
            failures += 0 if ok else 1

    total = len(cases)
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        payload = json.load(sys.stdin)
        code, reason = decide(payload if isinstance(payload, dict) else {})
    except Exception:  # noqa: BLE001
        return 0  # fail open: a guard-internal error must never break the tool call
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
