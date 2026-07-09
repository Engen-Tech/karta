#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse guard: the kaizen writer is confined to its declared surface.

Zero dependencies (pure stdlib). The harness invokes this on Write|Edit|NotebookEdit
with the hook payload JSON on stdin. It recognizes the kaizen writer by the payload's
top-level `agent_type` — exactly `karta-kaizen`, bare or `*:`-namespaced; nothing
wider, so `karta-kaizen-v2` is not recognized. For a recognized writer every write
target (`tool_input.file_path` and `tool_input.notebook_path`) must sit inside
kaizen's writable surface — a `.karta/sme/` segment or the exact file
`.karta/kaizen.json` — after normpath normalization; anything else is denied (exit 2,
reason on stderr), and a call with no extractable string path is denied as an
unverifiable write. Like guard_auditor_dispatch.py this guard is FAIL-CLOSED on its
recognized shape: an internal error while checking a recognized kaizen write denies.
Unrecognized shapes — unreadable payload, missing `agent_type`, unknown writer —
always pass.

  guard_writer_confinement.py              # hook mode: payload on stdin, exit 0/2
  guard_writer_confinement.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, sys

KAIZEN = "karta-kaizen"
SME_RE = re.compile(r"(?:^|/)\.karta/sme/")
KAIZEN_JSON_RE = re.compile(r"(?:^|/)\.karta/kaizen\.json$")
DOCTRINE = ("kaizen's writable surface is exactly two things — `.karta/sme/` (the "
            "project's stack packs) and `.karta/kaizen.json` (its opt-in switch)")


def _recognized(agent_type: object) -> bool:
    return isinstance(agent_type, str) and (
        agent_type == KAIZEN or agent_type.endswith(":" + KAIZEN))


def _allowed(path: str) -> bool:
    p = os.path.normpath(path).replace(os.sep, "/")
    return bool(SME_RE.search(p) or KAIZEN_JSON_RE.search(p))


def decide(payload: object) -> tuple[int, str]:
    """Return (exit_code, stderr_reason)."""
    if not isinstance(payload, dict) or not _recognized(payload.get("agent_type")):
        return 0, ""  # main thread, unknown writer, or unrecognized shape — pass
    writer = payload["agent_type"]
    tool_input = payload.get("tool_input")
    targets = ([tool_input[k] for k in ("file_path", "notebook_path") if k in tool_input]
               if isinstance(tool_input, dict) else [])
    if not targets or not all(isinstance(t, str) for t in targets):
        return 2, (
            f"karta: '{writer}' is the kaizen writer and this call carries no verifiable "
            "write target (tool_input.file_path / tool_input.notebook_path missing or not "
            f"a string) — an unverifiable kaizen write is denied, not waved through. "
            f"{DOCTRINE}; every kaizen write must name a path inside that surface.")
    for target in targets:
        if not _allowed(target):
            return 2, (
                f"karta: '{writer}' is the kaizen writer and '{target}' is outside its "
                f"writable surface. {DOCTRINE}; this path is neither, and nothing else — "
                "code, skills, docs, binders — is kaizen's to write. Redirect the edit "
                "into that surface or drop it.")
    return 0, ""


def _run_self_test() -> int:
    def pre(agent_type: object = None, tool: str = "Write",
            tool_input: object = "__kwargs__", **ti: object) -> dict:
        payload: dict = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": "/tmp"}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        payload["tool_input"] = ti if tool_input == "__kwargs__" else tool_input
        return payload

    cases = [
        ("bare karta-kaizen writing a .karta/sme/ pack passes",
         pre("karta-kaizen", file_path=".karta/sme/python.md", content="x"), 0, None),
        ("namespaced karta:karta-kaizen writing a .karta/sme/ pack passes",
         pre("karta:karta-kaizen", file_path=".karta/sme/python.md", content="x"), 0, None),
        ("absolute worktree .karta/sme/ path passes (segment anchor)",
         pre("karta-kaizen",
             file_path="/abs/worktree/.karta/sme/minimalism.md", content="x"), 0, None),
        ("nested src/x/.karta/sme/y.md passes (accepted residual)",
         pre("karta-kaizen", file_path="src/x/.karta/sme/y.md", content="x"), 0, None),
        ("exact .karta/kaizen.json passes",
         pre("karta-kaizen", file_path=".karta/kaizen.json", content="{}"), 0, None),
        (".karta/kaizen.json.bak denied (end-anchored match)",
         pre("karta-kaizen", file_path=".karta/kaizen.json.bak", content="{}"),
         2, ".karta/kaizen.json.bak"),
        (".karta/sme-extras/x.md denied (segment boundary)",
         pre("karta-kaizen", file_path=".karta/sme-extras/x.md", content="x"),
         2, ".karta/sme-extras/x.md"),
        ("bare .karta/sme denied (no trailing segment)",
         pre("karta-kaizen", file_path=".karta/sme", content="x"), 2, None),
        ("out-of-surface skill file denied, reason names path and surface",
         pre("karta-kaizen", file_path="skills/karta-plan/SKILL.md", content="x"),
         2, ("skills/karta-plan/SKILL.md", "`.karta/sme/`", "`.karta/kaizen.json`")),
        (".karta/binders/x.json denied (confinement, independent of immutability)",
         pre("karta-kaizen", file_path=".karta/binders/x.json", content="{}"), 2, None),
        (".karta/sme/../../src/app.py traversal normalized then denied",
         pre("karta-kaizen", file_path=".karta/sme/../../src/app.py", content="x"), 2, None),
        ("NotebookEdit with notebook_path outside the surface denied",
         pre("karta-kaizen", tool="NotebookEdit",
             notebook_path="notebooks/scratch.ipynb"), 2, "notebooks/scratch.ipynb"),
        ("tool_input not a dict denied (unverifiable — fail-closed)",
         pre("karta-kaizen", tool_input="junk"), 2, "no verifiable"),
        ("non-string file_path denied (unverifiable)",
         pre("karta-kaizen", file_path=42, content="x"), 2, "no verifiable"),
        ("karta-kaizen-v2 writing anywhere passes (not an exact match)",
         pre("karta-kaizen-v2", file_path="src/app.py", content="x"), 0, None),
        ("main-thread write (no agent_type) passes",
         pre(file_path="skills/karta-plan/SKILL.md", content="x"), 0, None),
        ("unknown agent (Explore) writing anywhere passes",
         pre("Explore", file_path="src/app.py", content="x"), 0, None),
        ("doc-gardner (karta:karta-doc-gardner) writing docs passes",
         pre("karta:karta-doc-gardner", file_path="docs/x.md", content="x"), 0, None),
        ("payload not a dict passes (unrecognized shape)", "not a dict", 0, None),
        ("prose mentioning kaizen without agent_type passes (identity from the field)",
         pre(file_path="docs/notes.md",
             content="karta-kaizen edits the packs; karta:karta-kaizen when namespaced"),
         0, None),
    ]
    failures = 0
    for name, payload, want, needle in cases:
        code, reason = decide(payload)
        needles = (needle,) if isinstance(needle, str) else (needle or ())
        ok = (code == want and (want == 0) == (reason == "")
              and (want == 0 or reason.startswith("karta: "))
              and all(n in reason for n in needles))
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
    payload: dict = {}
    try:
        raw = json.load(sys.stdin)
        if isinstance(raw, dict):
            payload = raw
    except Exception:  # noqa: BLE001
        return 0  # an unreadable payload is an unrecognized shape — pass
    try:
        code, reason = decide(payload)
    except Exception:  # noqa: BLE001
        # fail closed only on the writer this guard exists to confine; all else passes
        try:
            recognized = _recognized(payload.get("agent_type"))
        except Exception:  # noqa: BLE001
            recognized = False
        if not recognized:
            return 0
        code, reason = 2, (
            "karta: internal error while checking a karta-kaizen write — this guard fails "
            f"closed for the kaizen writer. {DOCTRINE}; retry the write with a path inside "
            "that surface.")
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
