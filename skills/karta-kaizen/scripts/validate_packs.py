# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate karta stack packs: frontmatter + review-checklist discipline.

Zero dependencies (pure stdlib), so every invocation form behaves identically —
nothing has to be provisioned before it runs:
  python3 validate_packs.py <pack.md>...        # validate packs, exit 0 clean / 1 findings
  python3 validate_packs.py --self-test          # run embedded fixtures, exit 0/1
  uv run --script validate_packs.py <pack.md>... # also fine — no deps to install

Checks (fail-closed):
  - frontmatter is strict line-based `key: value` pairs between two `---` lines;
    keys limited to name/description/match/always/see_also/disabled
  - name equals the file basename (sans .md)
  - exactly one of match/always, unless the pack is a suppression pack (disabled: true)
  - a "## Review checklist" section exists (unless disabled) and every line in it is
    either an active item "- [ ] <prefix>.<n> — <rule text>" or a tombstone
    "- ~~<prefix>.<n>~~ retired: <reason>"
  - ids use the pack's registered prefix, are unique, and a retired id never
    reappears as an active item
Packs above 3500 bytes warn (never fail) — packs are prompt text; keep them terse.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

# Registered per-pack rule-id prefixes. Ids in a registered pack must use exactly
# its prefix. An unregistered pack must use one consistent prefix of its own that
# does not collide with another pack's registered prefix.
PREFIXES = {
    "minimalism": "min",
    "angular": "ng",
    "vue": "vue",
    "python-fastapi": "fapi",
    "python": "py",
}
ALLOWED_KEYS = ("name", "description", "match", "always", "see_also", "disabled")
SIZE_WARN_BYTES = 3500

KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(\S.*?)\s*$")
ITEM_RE = re.compile(r"^- \[ \] ([a-z][a-z0-9-]*)\.(\d+) — (\S.*)$")
TOMBSTONE_RE = re.compile(r"^- ~~([a-z][a-z0-9-]*)\.(\d+)~~ retired: (\S.*)$")
HEADING_RE = re.compile(r"^## Review checklist\b")


def _parse_frontmatter(lines: list[str]) -> tuple[dict[str, str] | None, int, list[str]]:
    """Strict line-based frontmatter parse. Returns (fields, body_start, errors);
    fields is None when there is no frontmatter block to parse at all."""
    errors: list[str] = []
    if not lines or lines[0] != "---":
        return None, 0, ["frontmatter: file must start with a '---' line"]
    close = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if close is None:
        return None, 0, ["frontmatter: no closing '---' line"]
    fields: dict[str, str] = {}
    for ln in lines[1:close]:
        m = KV_RE.match(ln)
        if not m:
            errors.append(f"frontmatter: not a 'key: value' pair: {ln!r}")
            continue
        key, value = m.group(1), m.group(2)
        if key not in ALLOWED_KEYS:
            errors.append(f"frontmatter: unknown key '{key}' (allowed: {', '.join(ALLOWED_KEYS)})")
            continue
        if key in fields:
            errors.append(f"frontmatter: duplicate key '{key}'")
            continue
        fields[key] = value
    return fields, close + 1, errors


def _string_list(fields: dict[str, str], key: str, errors: list[str]) -> None:
    """A key whose value must be a JSON array of non-empty strings, e.g. ["fastapi"]."""
    if key not in fields:
        return
    try:
        val = json.loads(fields[key])
    except json.JSONDecodeError:
        errors.append(f"frontmatter: '{key}' must be a JSON list of strings, e.g. [\"fastapi\"]")
        return
    if (not isinstance(val, list) or not val
            or not all(isinstance(t, str) and t.strip() for t in val)):
        errors.append(f"frontmatter: '{key}' must be a non-empty JSON list of non-empty strings")


def _check_checklist(lines: list[str], body_start: int, stem: str, errors: list[str]) -> None:
    start = next((i for i in range(body_start, len(lines)) if HEADING_RE.match(lines[i])), None)
    if start is None:
        errors.append("missing '## Review checklist' section")
        return
    active: list[str] = []
    retired: list[str] = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if not ln.strip():
            continue
        if m := ITEM_RE.match(ln):
            active.append(f"{m.group(1)}.{m.group(2)}")
        elif m := TOMBSTONE_RE.match(ln):
            retired.append(f"{m.group(1)}.{m.group(2)}")
        else:
            errors.append(
                "checklist: line matches neither '- [ ] <id> — <rule text>' nor "
                f"'- ~~<id>~~ retired: <reason>': {ln!r}")
    if not active:
        errors.append("checklist: no active items — a pack with nothing to enforce "
                      "should be a suppression pack (disabled: true)")

    # prefix discipline
    prefixes = {pid.rsplit(".", 1)[0] for pid in active + retired}
    expected = PREFIXES.get(stem)
    if expected:
        for pfx in sorted(prefixes - {expected}):
            errors.append(f"checklist: prefix '{pfx}' is not this pack's registered prefix '{expected}'")
    elif prefixes:
        if len(prefixes) > 1:
            errors.append(f"checklist: mixed id prefixes {sorted(prefixes)} — one pack, one prefix")
        else:
            owner = {v: k for k, v in PREFIXES.items()}
            pfx = next(iter(prefixes))
            if pfx in owner:
                errors.append(f"checklist: prefix '{pfx}' is registered to pack '{owner[pfx]}'")

    # id uniqueness + tombstone respect
    for ids, kind in ((active, "active"), (retired, "tombstone")):
        seen: set[str] = set()
        for pid in ids:
            if pid in seen:
                errors.append(f"checklist: duplicate {kind} id '{pid}'")
            seen.add(pid)
    for pid in sorted(set(active) & set(retired)):
        errors.append(f"checklist: retired id '{pid}' reappears as an active item")


def validate_pack(text: str, filename: str) -> tuple[list[str], list[str], bool]:
    """Return (errors, warnings, disabled); empty errors == valid."""
    errors: list[str] = []
    warnings: list[str] = []
    size = len(text.encode())
    if size > SIZE_WARN_BYTES:
        warnings.append(f"pack is {size} bytes (> {SIZE_WARN_BYTES}) — packs are prompt text; trim it")
    if not filename.endswith(".md"):
        errors.append(f"pack file must be a .md file, got '{filename}'")
        return errors, warnings, False
    stem = filename[:-3]

    lines = text.splitlines()
    fields, body_start, fm_errors = _parse_frontmatter(lines)
    errors.extend(fm_errors)
    if fields is None:
        return errors, warnings, False

    for key in ("name", "description"):
        if key not in fields:
            errors.append(f"frontmatter: missing '{key}'")
    if "name" in fields and fields["name"] != stem:
        errors.append(f"frontmatter: name '{fields['name']}' != file basename '{stem}'")
    for key in ("always", "disabled"):
        if key in fields and fields[key] != "true":
            errors.append(f"frontmatter: '{key}' must be exactly 'true' (omit the key otherwise)")
    _string_list(fields, "match", errors)
    _string_list(fields, "see_also", errors)

    disabled = fields.get("disabled") == "true"
    if disabled:
        return errors, warnings, True  # suppression pack: enumerated, never pinned — no body checks

    present = [k for k in ("match", "always") if k in fields]
    if len(present) != 1:
        errors.append("frontmatter: exactly one of 'match' or 'always' required "
                      f"(found: {present or 'neither'})")
    _check_checklist(lines, body_start, stem, errors)
    return errors, warnings, False


# --- Self-test fixtures --------------------------------------------------------

_GOOD_ALWAYS = """\
---
name: minimalism
description: Write the least code that works
always: true
see_also: ["platform-native"]
---
## The ladder (advisory)
Prose the validator must ignore.

## Review checklist (enforced — diff-checkable only)
- [ ] min.1 — No new third-party dependency where the stdlib already ships it.
- [ ] min.2 — No abstraction with a single implementation added speculatively.
- ~~min.3~~ retired: folded into min.2.
- [ ] min.4 — Non-trivial new logic leaves one runnable check.
"""

_GOOD_MATCH = """\
---
name: python-fastapi
description: FastAPI/Pydantic do's and don'ts
match: ["fastapi", "pydantic"]
---
## Review checklist
- [ ] fapi.1 — Every changed route declares request/response types.
- [ ] fapi.2 — No blocking I/O inside an `async def` route.
"""

_SUPPRESSED = """\
---
name: vue
description: Suppressed in this project
disabled: true
---
This project opts out of the vue pack.
"""


def _run_self_test() -> int:
    def sub(text: str, old: str, new: str) -> str:
        assert old in text, f"self-test fixture bug: {old!r} not found"
        return text.replace(old, new)

    cases = [
        ("valid always pack (with tombstone)", _GOOD_ALWAYS, "minimalism.md", True),
        ("valid match pack", _GOOD_MATCH, "python-fastapi.md", True),
        ("valid suppression pack", _SUPPRESSED, "vue.md", True),
        ("no frontmatter at all", "# just prose\n", "minimalism.md", False),
        ("unclosed frontmatter", "---\nname: minimalism\n", "minimalism.md", False),
        ("unknown frontmatter key",
         sub(_GOOD_ALWAYS, "always: true", "always: true\nseverity: high"), "minimalism.md", False),
        ("non key-value frontmatter line",
         sub(_GOOD_ALWAYS, "always: true", "always: true\njust some prose"), "minimalism.md", False),
        ("duplicate frontmatter key",
         sub(_GOOD_ALWAYS, "always: true", "always: true\nalways: true"), "minimalism.md", False),
        ("name != basename", _GOOD_ALWAYS, "angular.md", False),
        ("both match and always",
         sub(_GOOD_ALWAYS, "always: true", 'always: true\nmatch: ["x"]'), "minimalism.md", False),
        ("neither match nor always",
         sub(_GOOD_ALWAYS, "always: true\n", ""), "minimalism.md", False),
        ("always must be literally true",
         sub(_GOOD_ALWAYS, "always: true", "always: yes"), "minimalism.md", False),
        ("match not a JSON list",
         sub(_GOOD_MATCH, '["fastapi", "pydantic"]', "fastapi"), "python-fastapi.md", False),
        ("missing review checklist",
         _GOOD_ALWAYS.split("## Review checklist")[0], "minimalism.md", False),
        ("hyphen instead of em-dash separator",
         sub(_GOOD_ALWAYS, "min.4 — Non-trivial", "min.4 - Non-trivial"), "minimalism.md", False),
        ("free-prose checklist line",
         sub(_GOOD_ALWAYS, "- [ ] min.4 —", "Also check the vibes.\n- [ ] min.4 —"), "minimalism.md", False),
        ("id missing (legacy un-numbered item)",
         sub(_GOOD_ALWAYS, "min.4 — ", ""), "minimalism.md", False),
        ("wrong prefix for registered pack",
         sub(_GOOD_ALWAYS, "min.4", "ng.4"), "minimalism.md", False),
        ("duplicate active id",
         sub(_GOOD_ALWAYS, "min.4", "min.2"), "minimalism.md", False),
        ("retired id reappears as active",
         sub(_GOOD_ALWAYS, "min.4", "min.3"), "minimalism.md", False),
        ("tombstone without reason",
         sub(_GOOD_ALWAYS, "retired: folded into min.2.", "retired:"), "minimalism.md", False),
        ("all items retired (no active left)",
         sub(_GOOD_MATCH, "- [ ] fapi.1 — Every changed route declares request/response types.\n"
                          "- [ ] fapi.2 — No blocking I/O inside an `async def` route.",
             "- ~~fapi.1~~ retired: superseded.\n- ~~fapi.2~~ retired: superseded."),
         "python-fastapi.md", False),
        ("unregistered pack, own consistent prefix",
         sub(sub(sub(_GOOD_MATCH, "python-fastapi", "terraform"), "fapi.", "tf."),
             '["fastapi", "pydantic"]', '["terraform"]'), "terraform.md", True),
        ("unregistered pack, mixed prefixes",
         sub(sub(sub(_GOOD_MATCH, "python-fastapi", "terraform"), "fapi.1", "tf.1"),
             '["fastapi", "pydantic"]', '["terraform"]'), "terraform.md", False),
        ("unregistered pack squatting a registered prefix",
         sub(sub(sub(_GOOD_MATCH, "python-fastapi", "terraform"), "fapi.", "min."),
             '["fastapi", "pydantic"]', '["terraform"]'), "terraform.md", False),
        ("disabled pack needs no match/always/checklist", _SUPPRESSED, "vue.md", True),
        ("disabled must be literally true",
         sub(_SUPPRESSED, "disabled: true", "disabled: yes"), "vue.md", False),
    ]
    failures = 0
    for name, text, filename, should_pass in cases:
        errs, _, _ = validate_pack(text, filename)
        ok = (not errs) == should_pass
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: "
              f"{'valid' if not errs else 'invalid (' + '; '.join(errs) + ')'}")
        if not ok:
            failures += 1

    # size warning: fires above 3500 bytes, never fails validation
    padded = _GOOD_ALWAYS + "\n## Notes\n" + ("x" * SIZE_WARN_BYTES)
    errs, warns, _ = validate_pack(padded, "minimalism.md")
    ok = not errs and len(warns) == 1
    print(f"[{'PASS' if ok else 'FAIL'}] oversize pack warns but stays valid: warnings={warns}")
    failures += 0 if ok else 1

    # suppression packs are surfaced as suppressed
    _, _, disabled = validate_pack(_SUPPRESSED, "vue.md")
    ok = disabled and not validate_pack(_GOOD_ALWAYS, "minimalism.md")[2]
    print(f"[{'PASS' if ok else 'FAIL'}] disabled flag reported only for suppression packs")
    failures += 0 if ok else 1

    total = len(cases) + 2
    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("packs", nargs="*", type=Path, metavar="pack.md")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    if not args.packs:
        ap.error("provide one or more pack files or --self-test")
    failed = False
    for path in args.packs:
        try:
            text = path.read_text()
        except OSError as e:
            print(f"{path}: unreadable ({e})")
            failed = True
            continue
        errors, warnings, disabled = validate_pack(text, path.name)
        for w in warnings:
            print(f"{path}: warning: {w}")
        if errors:
            failed = True
            print(f"{path}: INVALID")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"{path}: OK{' (suppressed: disabled pack, never pinned)' if disabled else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
