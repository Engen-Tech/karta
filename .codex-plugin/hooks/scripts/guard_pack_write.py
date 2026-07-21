#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Codex Pre/PostToolUse guard: stack packs land only in validator-clean form.

Codex twin of hooks/scripts/guard_pack_write.py (the Claude Code guard). Same
rule, different payload shape: Codex file edits arrive as `apply_patch` calls
whose `tool_input.command` is the RAW PATCH BODY, never a `file_path`. Targets
under `.karta/sme/*.md` are checked with the plugin's pack validator
(`skills/karta-kaizen/scripts/validate_packs.py`, resolved via PLUGIN_ROOT —
the Codex plugin-hook root — then CLAUDE_PLUGIN_ROOT, then this script's own
plugin root):

  - PreToolUse: an `*** Add File:` op carries the full proposed content in its
    `+` lines, so it is validated from a temp file; a failure denies the patch
    (exit 2, findings on stderr). An `*** Update File:` op is only hunks — no
    full content to validate — so PostToolUse covers it.
  - PostToolUse: every pack file the patch touched (add, update, or move
    target) is validated on disk; a failure exits 2 so the findings reach the
    model as feedback it must fix.

This is the kaizen pre-land syntax check enforced below the agent. Any internal
error or unknown payload shape fails open (exit 0): this guard must never break
an unrelated tool call. Keep the rule semantics in step with the Claude twin —
the two files are maintained by hand, not generated.

  guard_pack_write.py              # hook mode: payload on stdin, exit 0/2
  guard_pack_write.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

PACK_RE = re.compile(r"(?:^|/)\.karta/sme/.+\.md$")
DIRECTIVE_RE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")
VALIDATOR_REL = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"


def parse_patch_ops(text: str) -> list[dict]:
    """apply_patch body -> [{op, path, move_to, body}] (twin of the parser in
    guard_binder_immutability.py). `body` keeps the op's raw lines so an Add
    File's full content can be reconstructed from its `+` prefixes."""
    ops: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = DIRECTIVE_RE.match(line)
        if m:
            kind, path = m.group(1), m.group(2).strip()
            if kind == "Move to":
                if cur is not None:
                    cur["move_to"] = path
            else:
                cur = {"op": kind, "path": path, "move_to": None, "body": []}
                ops.append(cur)
            continue
        if line.startswith("*** "):
            continue  # Begin Patch / End Patch / End of File markers
        if cur is not None:
            cur["body"].append(line)
    return ops


def _validator_path() -> Path | None:
    roots: list[Path] = []
    for env in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        val = os.environ.get(env)
        if val:
            roots.append(Path(val))
    # <plugin root>/.codex-plugin/hooks/scripts/guard_pack_write.py -> parents[3]
    roots.append(Path(__file__).resolve().parents[3])
    for root in roots:
        cand = root / VALIDATOR_REL
        if cand.is_file():
            return cand
    return None


def _run_validator(validator: Path, pack_file: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(validator), str(pack_file)],
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _patch_text(tool_input: dict) -> str | None:
    raw = tool_input.get("command")
    if isinstance(raw, list):
        raw = "\n".join(x for x in raw if isinstance(x, str))
    if not isinstance(raw, str) or "*** " not in raw:
        return None
    return raw


def decide(payload: dict) -> tuple[int, str]:
    """Return (exit_code, stderr_message)."""
    event = payload.get("hook_event_name")
    if event not in ("PreToolUse", "PostToolUse"):
        return 0, ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""
    raw = _patch_text(tool_input)
    if raw is None:
        return 0, ""
    ops = parse_patch_ops(raw)
    is_pack = lambda p: bool(PACK_RE.search(p.replace("\\", "/")))  # noqa: E731
    touched = [op for op in ops
               if (op["op"] in ("Add File", "Update File") and is_pack(op["path"]))
               or (isinstance(op["move_to"], str) and is_pack(op["move_to"]))]
    if not touched:
        return 0, ""
    validator = _validator_path()
    if validator is None:
        return 0, ""  # fail open: no validator to consult
    cwd = payload.get("cwd") or os.getcwd()

    if event == "PreToolUse":
        for op in touched:
            if op["op"] != "Add File" or not is_pack(op["path"]):
                continue  # an update/move is only hunks; PostToolUse covers it
            content = "\n".join(l[1:] for l in op["body"] if l.startswith("+")) + "\n"
            with tempfile.TemporaryDirectory() as td:
                # keep the target's basename: the validator checks `name` == file basename
                probe = Path(td) / Path(op["path"].replace("\\", "/")).name
                probe.write_text(content)
                rc, findings = _run_validator(validator, probe)
            if rc != 0:
                return 2, (
                    f"karta: '{op['path']}' is a stack pack and the proposed content fails "
                    "validate_packs.py, so the patch is denied — packs land only in "
                    "validator-clean form (the kaizen pre-land syntax check, enforced below "
                    f"the agent). Fix the findings and apply the patch again.\n\n{findings}")
        return 0, ""

    # PostToolUse: the pack is already on disk — validate every touched pack file
    # and, on failure, feed the findings back so the model must repair it.
    targets: list[str] = []
    for op in touched:
        final = op["move_to"] if isinstance(op["move_to"], str) else op["path"]
        if is_pack(final) and final not in targets:
            targets.append(final)
    problems: list[str] = []
    for target in targets:
        abs_target = Path(target) if os.path.isabs(target) else Path(cwd) / target
        if not abs_target.is_file():
            continue
        rc, findings = _run_validator(validator, abs_target)
        if rc != 0:
            problems.append(f"'{target}':\n{findings}")
    if problems:
        return 2, (
            "karta: this patch left a stack pack on disk that fails validate_packs.py. "
            "Repair the pack until the validator passes before doing anything else — a "
            "malformed pack silently drops out of plan-time matching and audit-time "
            "checklists.\n\n" + "\n\n".join(problems))
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


def _add_patch(path: str, content: str) -> str:
    plus = "\n".join("+" + l for l in content.splitlines())
    return f"*** Begin Patch\n*** Add File: {path}\n{plus}\n*** End Patch"


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

        def pre(body: str) -> dict:
            return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                    "cwd": cwd, "tool_input": {"command": body}}

        def post(body: str) -> dict:
            return {"hook_event_name": "PostToolUse", "tool_name": "apply_patch",
                    "cwd": cwd, "tool_input": {"command": body},
                    "tool_response": {"success": True}}

        update_body = ("*** Begin Patch\n*** Update File: .karta/sme/broken.md\n"
                       "@@\n+- [ ] tf.2 — Another rule.\n*** End Patch")
        move_body = ("*** Begin Patch\n*** Update File: sme-drafts/broken.md\n"
                     "*** Move to: .karta/sme/broken.md\n*** End Patch")

        cases = [
            ("pre add of valid pack passes",
             pre(_add_patch(".karta/sme/terraform.md", _VALID_PACK)), 0, None),
            ("pre add of invalid pack denied",
             pre(_add_patch(".karta/sme/terraform.md", _INVALID_PACK)), 2, "frontmatter"),
            ("pre add name/basename mismatch denied",
             pre(_add_patch(".karta/sme/angular.md", _VALID_PACK)), 2, "basename"),
            ("pre add outside .karta/sme passes",
             pre(_add_patch("docs/sme/terraform.md", _INVALID_PACK)), 0, None),
            ("pre add non-md under .karta/sme passes",
             pre(_add_patch(".karta/sme/notes.txt", "x")), 0, None),
            ("pre update of a pack passes (PostToolUse covers it)",
             pre(update_body), 0, None),
            ("post update: invalid pack on disk feeds back",
             post(update_body), 2, "frontmatter"),
            ("post add: valid pack on disk passes",
             post(_add_patch(".karta/sme/terraform.md", _VALID_PACK)), 0, None),
            ("post move-to-pack target is validated on disk",
             post(move_body), 2, "frontmatter"),
            ("post on missing file passes",
             post(_add_patch(".karta/sme/ghost.md", _VALID_PACK)), 0, None),
            ("non-pack patch passes",
             pre(_add_patch("src/app.py", "print('x')")), 0, None),
            ("command as list is normalized",
             {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": cwd,
              "tool_input": {"command": [
                  "apply_patch", _add_patch(".karta/sme/terraform.md", _INVALID_PACK)]}},
             2, "frontmatter"),
            ("unknown event passes",
             {"hook_event_name": "SessionStart", "cwd": cwd,
              "tool_input": {"command": update_body}}, 0, None),
            ("tool_input not a dict passes",
             {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "cwd": cwd,
              "tool_input": "junk"}, 0, None),
            ("no patch text passes",
             {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "cwd": cwd,
              "tool_input": {}}, 0, None),
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
