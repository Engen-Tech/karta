#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pre/PostToolUse guard: stack packs land only validator-clean.

Zero dependencies (pure stdlib). The harness invokes this with the hook payload
JSON on stdin. Targets under `.karta/sme/*.md` pass through one rule:

Validator-clean (`skills/karta-kaizen/scripts/validate_packs.py`, resolved via
PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT, falling back to this script's own plugin root):
  - PreToolUse `Write`: the proposed content is validated from a temp file; a
    failure denies the write (exit 2, findings on stderr).
  - PostToolUse `Edit`/`Write`: the file on disk is validated; a failure exits 2
    so the findings reach the model as feedback it must fix.

The Write-preventive / Edit-corrective asymmetry is deliberate: PreToolUse fires on
`Write` (deny before a malformed pack lands), PostToolUse on `Edit`|`Write` (diagnose
a malformed pack the model just wrote).
Codex `apply_patch` additions carry complete content and are checked before the
write. Updates and moves are checked on disk after the patch, including every
destination in a multi-file patch. Deletions leave no pack to validate.

Any internal error fails open (exit 0): a missing/broken validator, a subprocess
crash, or unparseable output must never break an unrelated tool call.

  guard_pack_write.py              # hook mode: payload on stdin, exit 0/2
  guard_pack_write.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

PACK_RE = re.compile(r"(?:^|/)\.karta/sme/.+\.md$")
VALIDATOR_REL = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"
DIRECTIVE_RE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


def _resolve_rel(rel: Path) -> Path | None:
    """Resolve from host roots, then the nearest enclosing plugin manifest."""
    roots: list[Path] = []
    for key in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        env = os.environ.get(key)
        if env:
            roots.append(Path(env))
    for parent in Path(__file__).resolve().parents:
        if (parent / ".codex-plugin" / "plugin.json").is_file():
            roots.append(parent)
            break
    for root in roots:
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def _validator_path() -> Path | None:
    return _resolve_rel(VALIDATOR_REL)


def _run_validator(validator: Path, pack_file: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(validator), str(pack_file)],
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _patch_writes(raw: str) -> list[dict]:
    """Extract final destinations and complete Add File content, never edit deltas."""
    writes: list[dict] = []
    current: dict | None = None
    # Codex splits LF/CRLF lines, not Python's additional Unicode/control separators.
    for line in raw.split("\n"):
        line = line.removesuffix("\r")
        match = DIRECTIVE_RE.match(line)
        if match:
            kind, path = match.groups()
            if kind == "Move to":
                if current is not None:
                    current["file_path"] = path.strip()
            elif kind == "Delete File":
                current = None
            else:
                current = {"file_path": path.strip()}
                if kind == "Add File":
                    current["content"] = ""
                writes.append(current)
        elif current is not None and "content" in current and line.startswith("+"):
            current["content"] += line[1:] + "\n"
    return writes


def decide(payload: dict) -> tuple[int, str]:
    """Return (exit_code, stderr_message)."""
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") == "apply_patch" and isinstance(tool_input, dict):
        raw = tool_input.get("command")
        if not isinstance(raw, str):
            return 0, ""
        findings: list[str] = []
        for write in _patch_writes(raw):
            code, reason = decide({**payload, "tool_input": write,
                                   "tool_name": "Write" if "content" in write else "Edit"})
            if code == 2:
                findings.append(reason)
        return (2, "\n\n".join(findings)) if findings else (0, "")
    target = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(target, str):
        return 0, ""
    cwd = payload.get("cwd") or os.getcwd()
    target_path = Path(target.replace("\\", "/"))
    abs_target = target_path if target_path.is_absolute() else Path(cwd) / target_path
    logical_target = Path(os.path.abspath(abs_target))
    if not PACK_RE.search(logical_target.as_posix()):
        # An alias into packs is a pack write too. Preserve logical pack paths
        # otherwise: .karta/sme may itself be a symlink to shared pack storage.
        abs_target = abs_target.resolve()
        if not PACK_RE.search(abs_target.as_posix()):
            return 0, ""
    validator = _validator_path()
    if validator is None:
        return 0, ""  # fail open: no validator to consult
    basename = abs_target.name

    if payload.get("hook_event_name") == "PreToolUse":
        if payload.get("tool_name") != "Write":
            return 0, ""  # an Edit delta has no full content to validate; PostToolUse covers it
        content = tool_input.get("content")
        if not isinstance(content, str):
            return 0, ""
        with tempfile.TemporaryDirectory() as td:
            # keep the target's basename: the validator checks `name` == file basename
            probe = Path(td) / basename
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
    # Do not let an installed plugin's environment mask a broken checkout fallback.
    from unittest.mock import patch
    with patch.dict(os.environ, {}, clear=True):
        if _validator_path() is None:
            print("[FAIL] validator not resolvable without plugin environment")
            return 1
    if _validator_path() is None:
        print("[FAIL] validator not resolvable (skills/karta-kaizen/scripts/validate_packs.py)")
        print("\n0/1 checks passed")
        return 1

    with tempfile.TemporaryDirectory() as td:
        cwd = str(td)
        # A damaged install must not discover a validator outside its own package.
        plugin = Path(td) / "plugin"
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / ".codex-plugin/plugin.json").write_text('{}\n')
        decoy = Path(td) / VALIDATOR_REL
        decoy.parent.mkdir(parents=True)
        decoy.write_text("# unrelated ancestor validator\n")
        with patch.dict(os.environ, {}, clear=True), patch.dict(
                globals(), {"__file__": str(plugin / ".codex-plugin/hooks/scripts/guard_pack_write.py")}):
            if _validator_path() is not None:
                print("[FAIL] missing packaged validator escaped its plugin root")
                return 1
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

        def codex(event: str, *lines: str) -> dict:
            return {"hook_event_name": event, "tool_name": "apply_patch", "cwd": cwd,
                    "tool_input": {"command": "\n".join(
                        ("*** Begin Patch", *lines, "*** End Patch"))}}

        cases = [
            ("Codex control separators stay in added content and are validated",
             codex("PreToolUse", "*** Add File: .karta/sme/terraform.md",
                   *("+" + line for line in _VALID_PACK.splitlines()), "+\x0bgarbage"),
             2, "checklist"),
            ("Codex normalized destination outside packs passes",
             codex("PreToolUse", "*** Add File: .karta/sme/../../notes.md", "+ordinary note"),
             0, None),
            ("Codex normalized destination inside packs is checked",
             codex("PreToolUse", "*** Add File: .karta/elsewhere/../sme/broken.md", "+bad"),
             2, "frontmatter"),
            ("Codex post-add checks actual disk content",
             codex("PostToolUse", "*** Add File: .karta/sme/broken.md",
                   *("+" + line for line in _VALID_PACK.splitlines())), 2, "frontmatter"),
            ("Codex invalid addition denied before writing",
             codex("PreToolUse", "*** Add File: .karta/sme/broken.md", "+no frontmatter"),
             2, "frontmatter"),
            ("Codex valid addition passes",
             codex("PreToolUse", "*** Add File: .karta/sme/terraform.md",
                   *("+" + line for line in _VALID_PACK.splitlines())), 0, None),
            ("Codex update deferred to post hook",
             codex("PreToolUse", "*** Update File: .karta/sme/broken.md", "@@", "+bad"),
             0, None),
            ("Codex invalid update reported from disk",
             codex("PostToolUse", "*** Update File: .karta/sme/broken.md", "@@", "+bad"),
             2, "frontmatter"),
            ("Codex multi-file patch checks later pack",
             codex("PostToolUse", "*** Update File: README.md", "@@", "+ok",
                   "*** Update File: .karta/sme/broken.md", "@@", "+bad"), 2, "frontmatter"),
            ("Codex move checks destination",
             codex("PostToolUse", "*** Update File: notes.md",
                   "*** Move to: .karta/sme/broken.md"), 2, "frontmatter"),
            ("Codex deletion has no resulting pack",
             codex("PostToolUse", "*** Delete File: .karta/sme/broken.md"), 0, None),
            ("Codex content cannot spoof a directive",
             codex("PreToolUse", "*** Add File: notes.md",
                   "+*** Add File: .karta/sme/broken.md", "+bad"), 0, None),
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
        crlf = codex("PreToolUse", "*** Add File: .karta/sme/terraform.md",
                     *("+" + line for line in _VALID_PACK.splitlines()))
        crlf["tool_input"]["command"] = crlf["tool_input"]["command"].replace("\n", "\r\n")
        cases.append(("Codex CRLF patch keeps valid added content", crlf, 0, None))

        linked = Path(td) / "linked"
        (linked / ".karta").mkdir(parents=True)
        shared = Path(td) / "shared-packs"
        shared.mkdir()
        (shared / "broken.md").write_text(_INVALID_PACK)
        (shared / "terraform.md").write_text(_VALID_PACK)
        try:
            (linked / ".karta/sme").symlink_to(shared, target_is_directory=True)
            (Path(td) / "alias").symlink_to(sme, target_is_directory=True)
            (sme / "alias.md").symlink_to(shared / "terraform.md")
        except OSError:
            if sys.platform != "win32":
                raise
            print("[SKIP] symlink fixtures require Windows symlink privileges")
        else:
            cases.extend([
                ("Codex pre-add into symlinked pack directory denied",
                 {**codex("PreToolUse", "*** Add File: .karta/sme/broken.md", "+bad"),
                  "cwd": str(linked)}, 2, "frontmatter"),
                ("Codex post-add into symlinked pack directory reported",
                 {**codex("PostToolUse", "*** Add File: .karta/sme/broken.md", "+bad"),
                  "cwd": str(linked)}, 2, "frontmatter"),
                ("legacy Write into symlinked pack directory denied",
                 {**pre_write(".karta/sme/broken.md", _INVALID_PACK),
                  "cwd": str(linked)}, 2, "frontmatter"),
                ("valid pack in symlinked directory passes",
                 {**post("Write", ".karta/sme/terraform.md"), "cwd": str(linked)}, 0, None),
                ("alias into pack directory is validated",
                 pre_write("alias/broken.md", _INVALID_PACK), 2, "frontmatter"),
                ("pack file symlink retains logical basename check",
                 post("Write", ".karta/sme/alias.md"), 2, "basename"),
            ])
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
