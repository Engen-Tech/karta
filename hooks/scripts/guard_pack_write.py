#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pre/PostToolUse guard: stack packs land only validator-clean and never as an illegal shadow.

Zero dependencies (pure stdlib). The harness invokes this with the hook payload
JSON on stdin. Targets under `.karta/sme/*.md` pass through two rules, in order:

1. Validator-clean (`skills/karta-kaizen/scripts/validate_packs.py`, resolved via
   CLAUDE_PLUGIN_ROOT, falling back to this script's own plugin root):
     - PreToolUse `Write`: the proposed content is validated from a temp file; a
       failure denies the write (exit 2, findings on stderr).
     - PostToolUse `Edit`/`Write`: the file on disk is validated; a failure exits 2
       so the findings reach the model as feedback it must fix.

2. Illegal-shadow deny (runs only AFTER the validator-clean check passes). The pack
   is classified against the shipped built-in it may shadow, via
   `skills/karta-plan/scripts/check_pack_provenance.py` (resolved the SAME way as the
   validator). A state of `illegal shadow` — a local delta silently forking a shipped
   built-in — is refused (exit 2) with a message quoting the canonical substring
   'illegal shadow: a local delta over the shipped built-in', naming the shadowed
   built-in, and offering the three ways forward (restore the seeded copy, move the
   delta to a project pack, or take the delta upstream). Every other state passes:
   seeded cache, stale cache, suppression, project pack, and any target whose built-in
   cannot be resolved. On PreToolUse `Write` the target is not on disk yet, so the
   PROPOSED content is piped to the classifier's `--stdin` mode — mirroring how the
   validator-clean check already validates proposed content from a temp file. On
   PostToolUse the on-disk file is classified the same way; an illegal shadow exits 2
   so the finding reaches the model as corrective feedback it must fix.

The Write-preventive / Edit-corrective asymmetry is deliberate and mirrors the existing
validator-clean architecture: PreToolUse fires on `Write` (deny before the fork lands),
PostToolUse on `Edit`|`Write` (diagnose a fork the model just wrote). This deny is a
deterrent for INTERACTIVE authoring only — a git pull/merge/checkout, an external editor,
or a sync tool bypasses it by construction, and the message never claims to be the
authoritative gate (plan time is, and stays a loud warning this release).

Any internal error fails open (exit 0): a missing/broken classifier or validator, a
subprocess crash, or unparseable output must never break an unrelated tool call.

  guard_pack_write.py              # hook mode: payload on stdin, exit 0/2
  guard_pack_write.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

PACK_RE = re.compile(r"(?:^|/)\.karta/sme/.+\.md$")
VALIDATOR_REL = Path("skills") / "karta-kaizen" / "scripts" / "validate_packs.py"
CLASSIFIER_REL = Path("skills") / "karta-plan" / "scripts" / "check_pack_provenance.py"

# The canonical illegal-shadow substring — byte-identical to the classifier, the plan
# skill's stack-pack step, and the docs (held together by the binder's shared_terms gate).
ILLEGAL_SHADOW_SUBSTR = "illegal shadow: a local delta over the shipped built-in"


def _resolve_rel(rel: Path) -> Path | None:
    """Resolve a plugin-relative path via CLAUDE_PLUGIN_ROOT, else this script's own
    plugin root — the single idiom both the validator and the classifier lookups use."""
    roots: list[Path] = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        roots.append(Path(env))
    roots.append(Path(__file__).resolve().parent.parent.parent)  # <plugin root>/hooks/scripts/..
    for root in roots:
        cand = root / rel
        if cand.is_file():
            return cand
    return None


def _validator_path() -> Path | None:
    return _resolve_rel(VALIDATOR_REL)


def _classifier_path() -> Path | None:
    return _resolve_rel(CLASSIFIER_REL)


def _run_validator(validator: Path, pack_file: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(validator), str(pack_file)],
                          capture_output=True, text=True, timeout=30)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _classify_state(classifier: Path | None, basename: str,
                    content: str) -> tuple[str | None, str | None]:
    """Classify proposed pack content by provenance, piping it to the classifier's
    `--stdin` mode. Return (state, shadowed-built-in-basename), or (None, None) on ANY
    failure — a missing/broken classifier, a nonzero exit, a crash, or unparseable output
    all mean 'cannot classify', and the guard fails open (allow) rather than break the
    tool call."""
    if classifier is None:
        return None, None
    try:
        proc = subprocess.run(
            [sys.executable, str(classifier), "--stdin", "--file", basename],
            input=content, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return None, None
        packs = json.loads(proc.stdout).get("packs")
        if not isinstance(packs, list) or not packs or not isinstance(packs[0], dict):
            return None, None
        return packs[0].get("state"), packs[0].get("builtin")
    except Exception:  # noqa: BLE001 — fail open: classification must never break the tool call
        return None, None


def _shadow_deny_msg(target: str, builtin: str | None, corrective: bool) -> str:
    """The illegal-shadow deny message: quotes the canonical substring, names the shadowed
    built-in, and offers the three ways forward. Never claims to be the authoritative gate."""
    b = builtin or "the shipped built-in"
    lead = (f"karta: '{target}' is now an {ILLEGAL_SHADOW_SUBSTR} ({b}). Repair it before "
            "doing anything else."
            if corrective else
            f"karta: '{target}' is an {ILLEGAL_SHADOW_SUBSTR} ({b}) — the write is denied.")
    return (
        f"{lead} This pack copy silently forks the shipped rules. Three ways forward:\n"
        f"  1. Restore the seeded copy of {b} — drop the local delta.\n"
        "  2. Move the delta to a project pack — a fresh basename carrying `extends` and `id_prefix`.\n"
        "  3. Take the delta upstream — change the built-in itself.\n"
        "This guard is an interactive-authoring deterrent, not the authoritative gate: a git "
        "pull/merge/checkout, an external editor, or a sync tool bypasses it by construction; "
        "plan time is the authoritative check and stays a loud warning this release.")


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

    basename = Path(target.replace("\\", "/")).name

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
        # Validator-clean: now deny a Write that would fork a shipped built-in (illegal
        # shadow) before it lands. The target is not on disk yet, so pipe the proposed
        # content to the classifier's --stdin mode.
        state, builtin = _classify_state(_classifier_path(), basename, content)
        if state == "illegal shadow":
            return 2, _shadow_deny_msg(target, builtin, corrective=False)
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
    # Validator-clean: classify the on-disk file the same way. An illegal shadow the model
    # just wrote (via Edit or Write) exits 2 so the finding reaches it as corrective feedback.
    try:
        disk_content = abs_target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, ""  # fail open: cannot read what we just validated
    state, builtin = _classify_state(_classifier_path(), basename, disk_content)
    if state == "illegal shadow":
        return 2, _shadow_deny_msg(target, builtin, corrective=True)
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

# Suppressing a shipped built-in is allowed: disabled: true classifies as `suppression`,
# never `illegal shadow`, even though the body diverges from the built-in. Its basename
# matches a real built-in (minimalism) so its `name` must too, to stay validator-clean.
_SUPPRESS_MIN = """\
---
name: minimalism
description: locally disabled in this repo
disabled: true
---
We deliberately turn this pack off here; the body is free commentary, never compared.
"""

# A project's own pack: a fresh basename that collides with no built-in — classifies as
# `project pack`, so the write is allowed.
_PROJECT_PACK = """\
---
name: acme
description: our house rules
match: ["acme"]
---
## Review checklist
- [ ] acme.1 — Our own project rule.
"""


def _builtin_source(basename: str) -> str | None:
    """Read a shipped built-in pack's exact bytes, resolved beside the classifier
    (references/sme/). The seeded-cache and illegal-shadow fixtures derive from real
    built-in bytes so they stay correct as the shipped packs evolve."""
    classifier = _classifier_path()
    if classifier is None:
        return None
    cand = classifier.parent.parent / "references" / "sme" / basename
    return cand.read_text(encoding="utf-8") if cand.is_file() else None


def _run_self_test() -> int:
    if _validator_path() is None:
        print("[FAIL] validator not resolvable (skills/karta-kaizen/scripts/validate_packs.py)")
        print("\n0/1 checks passed")
        return 1
    seed = _builtin_source("minimalism.md")
    if _classifier_path() is None or seed is None:
        print("[FAIL] classifier/built-in not resolvable for the illegal-shadow fixtures "
              "(skills/karta-plan/scripts/check_pack_provenance.py, references/sme/minimalism.md)")
        print("\n0/1 checks passed")
        return 1
    # A genuine local delta over the built-in: still validator-clean (min.5 keeps the pack's
    # own prefix), but its bytes differ from the shipped built-in and no past ledger hash —
    # so the classifier calls it an illegal shadow.
    shadow = seed + "- [ ] min.5 — a local delta the shipped pack never carried.\n"

    with tempfile.TemporaryDirectory() as td:
        cwd = str(td)
        sme = Path(td) / ".karta" / "sme"
        sme.mkdir(parents=True)
        (sme / "terraform.md").write_text(_VALID_PACK)
        (sme / "broken.md").write_text(_INVALID_PACK)
        # An illegal shadow the model just wrote to disk drives the corrective PostToolUse path.
        (sme / "minimalism.md").write_text(shadow)

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

            # --- illegal-shadow deny (rule 2) — Write-preventive ---
            ("pre-write byte-identical seeded cache passes",
             pre_write(".karta/sme/minimalism.md", seed), 0, None),
            ("pre-write illegal shadow is DENIED exit 2 with the canonical substring",
             pre_write(".karta/sme/minimalism.md", shadow), 2, ILLEGAL_SHADOW_SUBSTR),
            ("pre-write illegal shadow deny names the shadowed built-in",
             pre_write(".karta/sme/minimalism.md", shadow), 2, "minimalism.md"),
            ("pre-write illegal shadow deny offers a project pack as a way forward",
             pre_write(".karta/sme/minimalism.md", shadow), 2, "project pack"),
            ("pre-write suppression of a built-in passes (disabled: true)",
             pre_write(".karta/sme/minimalism.md", _SUPPRESS_MIN), 0, None),
            ("pre-write fresh-basename project pack passes",
             pre_write(".karta/sme/acme.md", _PROJECT_PACK), 0, None),
            # --- illegal-shadow deny — Edit-corrective (asymmetry: PreToolUse fires on
            # Write only, PostToolUse diagnoses an Edit|Write already on disk) ---
            ("post-edit illegal shadow on disk feeds back corrective exit 2",
             post("Edit", ".karta/sme/minimalism.md"), 2, ILLEGAL_SHADOW_SUBSTR),
            ("post-write illegal shadow on disk feeds back corrective exit 2",
             post("Write", ".karta/sme/minimalism.md"), 2, ILLEGAL_SHADOW_SUBSTR),
        ]
        failures = 0
        for name, payload, want, needle in cases:
            code, msg = decide(payload)
            ok = code == want and (needle is None or needle in msg)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: exit {code}")
            failures += 0 if ok else 1

        # fail-open (exit 0 == allow) whenever classification cannot be trusted — the guard
        # must never break an unrelated tool call. Drive the classify helper directly with a
        # deliberately broken classifier; a genuine-shadow input proves it is the failure, not
        # the content, that opens the gate.
        err = Path(td) / "err_classifier.py"
        err.write_text("import sys\nsys.exit(3)\n")
        junk = Path(td) / "junk_classifier.py"
        junk.write_text("print('not json')\n")
        extra: list[tuple[str, bool]] = [
            ("fail-open: no classifier resolvable -> allow (state None)",
             _classify_state(None, "minimalism.md", shadow) == (None, None)),
            ("fail-open: missing classifier path -> allow",
             _classify_state(Path(td) / "nope.py", "minimalism.md", shadow) == (None, None)),
            ("fail-open: classifier nonzero exit -> allow",
             _classify_state(err, "minimalism.md", shadow) == (None, None)),
            ("fail-open: classifier unparseable output -> allow",
             _classify_state(junk, "minimalism.md", shadow) == (None, None)),
        ]
        for name, ok in extra:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}")
            failures += 0 if ok else 1

    total = len(cases) + len(extra)
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
