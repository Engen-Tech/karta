#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse guard: confined writers stay inside their declared surfaces.

Zero dependencies (pure stdlib). The harness invokes this on Write|Edit|NotebookEdit
and on Bash, with the hook payload JSON on stdin. It recognizes a confined writer by
the payload's top-level `agent_type` — exactly the bare name or an exact
`*:`-namespaced form, nothing wider (so `karta-kaizen-v2` is not recognized). Two
writers are confined:

  kaizen       — a `.karta/sme/` segment, and the exact file `.karta/kaizen.json`.
  doc-gardner  — its prose-doc surface: `README*`, `AGENTS.md`, `CLAUDE.md`,
                 `ARCHITECTURE*` (basename-anchored), anything under a `docs/`
                 segment, other top-level `*.md` (top-level judged against the
                 payload `cwd`), plus exactly `.gitignore` — its one non-doc
                 exception (the superpowers-salvage ignore line). Never `.karta/`.

On Write/Edit/NotebookEdit every target (`tool_input.file_path`,
`tool_input.notebook_path`) must sit inside the writer's surface after normpath
normalization; a call with no extractable string path is denied as unverifiable.

On Bash the command string is statically parsed for high-confidence file-write and
delete operations — output redirections (`>`, `>>`, `>|`, `&>`), `tee`, `sed`/`perl`/
`python`/`ruby` in-place (`-i`) flags, `mv`/`cp`/`install` destinations (`mv` checks
both ends: it deletes the source too), `rm`/`rmdir`/`unlink`, `git mv`/`git rm`
(honoring `git -C`), and one level of `bash -c`/`eval` unwrapping. Extracted targets
are resolved against the payload `cwd` and checked against the same surface. Commands
with no detected write operation (grep, ls, git status, ...) pass.

Fail posture — FAIL-CLOSED on the recognized shape, same as the kaizen-only cut and
`guard_auditor_dispatch.py`: for a recognized confined writer, an internal error
denies, an unverifiable call denies, and an AMBIGUOUS OR UNPARSEABLE Bash command
denies — unbalanced quotes, command/process substitution (`$(...)`, backticks),
heredocs, variables in a write-target position, relative targets after `cd` or with
no `cwd` in the payload, and `xargs`/`find -delete`-style runtime-determined targets
are all denied with a corrective reason, not waved through. Unrecognized shapes —
unreadable payload, missing `agent_type`, unknown writer, main thread — always pass.

Honest limits (documented, deliberate): this is per-writer confinement, not a
sandbox — an interpreter invocation (`python x.py`) or an unlisted tool that writes
is not detected; segment/basename anchoring admits nested `docs/`/`README*` paths
(the same accepted residual as kaizen's nested `.karta/sme/`); a Bash-written pack
bypasses PostToolUse pack validation (kaizen carries no Bash today — this mode is
defense against tool-grant drift, like the NotebookEdit matcher).

  guard_writer_confinement.py              # hook mode: payload on stdin, exit 0/2
  guard_writer_confinement.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, re, shlex, sys

WRITERS: dict[str, dict] = {
    "karta-kaizen": {
        "label": "kaizen",
        "regexes": (re.compile(r"(?:^|/)\.karta/sme/"),
                    re.compile(r"(?:^|/)\.karta/kaizen\.json$")),
        "toplevel_md": False,
        "doctrine": ("kaizen's writable surface is exactly two things — `.karta/sme/` (the "
                     "project's stack packs) and `.karta/kaizen.json` (its opt-in switch)"),
    },
    "karta-doc-gardner": {
        "label": "doc-gardner",
        "regexes": (re.compile(r"(?:^|/)docs/"),
                    re.compile(r"(?:^|/)README[^/]*$"),
                    re.compile(r"(?:^|/)AGENTS\.md$"),
                    re.compile(r"(?:^|/)CLAUDE\.md$"),
                    re.compile(r"(?:^|/)ARCHITECTURE[^/]*$"),
                    re.compile(r"(?:^|/)\.gitignore$")),
        "toplevel_md": True,
        "doctrine": ("doc-gardner's writable surface is the prose-doc surface — `README*`, "
                     "anything under a `docs/` segment, `AGENTS.md`, `CLAUDE.md`, "
                     "`ARCHITECTURE*`, other top-level `*.md` — plus exactly `.gitignore` "
                     "(its one non-doc exception); code, tests, skills, and every "
                     "`.karta/` path are not its to write"),
    },
}

# Bash static analysis: commands that mutate files directly, shells/wrappers that
# can smuggle a mutation past a surface check, and substitution markers that hide
# commands from any static tokenization.
_DELETERS = {"rm", "rmdir", "unlink"}
_INPLACE_EDITORS = {"sed", "perl", "python", "python3", "ruby"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_RISKY = (_DELETERS | _SHELLS | _INPLACE_EDITORS
          | {"tee", "mv", "cp", "install", "git", "eval", "xargs"})
_TRANSPARENT_PREFIXES = {"sudo", "env", "command", "nohup", "nice", "time", "stdbuf"}
# Discard devices: redirecting output here is the quiet idiom (cmd >/dev/null 2>&1),
# never a file write worth confining.
_SINK_DEVICES = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SCRIPT_FLAG_RE = re.compile(r"-[A-Za-z]*[eEcCmM]$")  # option cluster consuming a script arg
_SUBSTITUTION_MARKERS = ("$(", "`", "<(", ">(")


def _recognized(agent_type: object) -> str | None:
    """Return the writer-table key for an exactly recognized confined writer."""
    if not isinstance(agent_type, str):
        return None
    for name in WRITERS:
        if agent_type == name or agent_type.endswith(":" + name):
            return name
    return None


def _allowed(conf: dict, path: str, cwd: object) -> bool:
    p = os.path.normpath(path).replace(os.sep, "/")
    if any(rx.search(p) for rx in conf["regexes"]):
        return True
    if conf["toplevel_md"] and p.endswith(".md"):
        if "/" not in p:
            return True  # bare relative filename — top level of the working dir
        if os.path.isabs(p) and isinstance(cwd, str) and cwd:
            rel = os.path.relpath(p, os.path.normpath(cwd).replace(os.sep, "/"))
            rel = rel.replace(os.sep, "/")
            if not rel.startswith("..") and "/" not in rel:
                return True
    return False


def _tokenize(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _operands(args: list[str]) -> list[str]:
    """Non-flag arguments, honoring `--` end-of-flags; `-` (stdin/stdout) skipped."""
    out: list[str] = []
    no_more_flags = False
    for a in args:
        if no_more_flags:
            if a != "-":
                out.append(a)
        elif a == "--":
            no_more_flags = True
        elif not a.startswith("-") or a == "-":
            if a != "-":
                out.append(a)
    return out


def _analyze(command: str, cwd: object, depth: int = 0) -> tuple[list[str], list[str]]:
    """Statically extract file-write/delete targets from one shell command string.

    Returns (resolved_targets, ambiguities). Any ambiguity means the command could
    not be pinned down with confidence — the caller denies for a recognized writer
    (fail-closed), never guesses.
    """
    targets: list[str] = []
    ambiguities: list[str] = []
    if depth > 3:
        return [], ["shell nesting deeper than 3 levels"]
    for marker in _SUBSTITUTION_MARKERS:
        if marker in command:
            return [], [f"command/process substitution ('{marker}') hides commands "
                        "from a static check"]
    try:
        tokens = _tokenize(command)
    except ValueError as e:
        return [], [f"unparseable shell syntax ({e})"]

    shifted = False  # a `cd`/`pushd` ran earlier — later relative paths are unknowable

    def resolve(raw: str, base: object = None) -> None:
        if raw in _SINK_DEVICES:
            return
        t = os.path.expanduser(raw)
        if "$" in t or "`" in t:
            ambiguities.append(f"write target '{raw}' contains a shell variable")
            return
        if not os.path.isabs(t):
            if shifted:
                ambiguities.append(f"relative write target '{raw}' after `cd` cannot "
                                   "be resolved statically")
                return
            root = base if isinstance(base, str) and base else cwd
            if not isinstance(root, str) or not root:
                ambiguities.append(f"relative write target '{raw}' with no cwd in "
                                   "the payload")
                return
            t = os.path.join(root, t)
        targets.append(t)

    def finalize(words: list[str]) -> None:
        nonlocal shifted
        while words and (_ASSIGNMENT_RE.match(words[0])
                         or words[0] in _TRANSPARENT_PREFIXES):
            if words[0] == "command" and len(words) > 1 and words[1].startswith("-"):
                if words[1] in ("-v", "-V"):
                    return  # `command -v X` — the POSIX tool-presence probe, read-only
                ambiguities.append("cannot statically resolve `command` with flags")
                return
            words.pop(0)
        if not words:
            return
        if words[0].startswith("-"):
            ambiguities.append(f"cannot statically identify the command behind "
                               f"'{words[0]}'")
            return
        cmd = os.path.basename(words[0])
        args = words[1:]
        if cmd in ("cd", "pushd", "popd"):
            shifted = True
            return
        if cmd in _SHELLS:
            script = None
            for j, a in enumerate(args):
                if a == "-c" or (re.fullmatch(r"-[A-Za-z]+", a) and "c" in a[1:]):
                    if j + 1 < len(args):
                        script = args[j + 1]
                    break
            if script is None:
                return  # `bash script.sh` — execution, not a direct write op
            if shifted:
                ambiguities.append("nested shell after `cd` cannot be resolved "
                                   "statically")
                return
            sub_t, sub_a = _analyze(script, cwd, depth + 1)
            targets.extend(sub_t)
            ambiguities.extend(sub_a)
            return
        if cmd == "eval":
            joined = " ".join(args)
            if "$" in joined or "`" in joined:
                ambiguities.append("eval over shell variables hides the real command")
                return
            sub_t, sub_a = _analyze(joined, cwd, depth + 1)
            targets.extend(sub_t)
            ambiguities.extend(sub_a)
            return
        if cmd == "xargs":
            if any(os.path.basename(a) in _RISKY for a in args if not a.startswith("-")):
                ambiguities.append("xargs feeds runtime-determined arguments to a "
                                   "command that can write or delete")
            return
        if cmd == "find":
            if "-delete" in args:
                ambiguities.append("find -delete removes runtime-determined paths")
            elif (any(a in ("-exec", "-execdir", "-ok", "-okdir") for a in args)
                  and any(os.path.basename(a) in _RISKY for a in args)):
                ambiguities.append("find -exec over a command that can write or "
                                   "delete has runtime-determined targets")
            return
        if cmd == "tee":
            for a in _operands(args):
                resolve(a)
            return
        if cmd in _DELETERS or cmd == "mv":
            # mv mutates both ends — the source is deleted, the destination written —
            # so every operand (including a `-t` directory) is checked.
            for a in _operands(args):
                resolve(a)
            return
        if cmd in ("cp", "install"):
            dest_from_t = None
            ops: list[str] = []
            j = 0
            while j < len(args):
                a = args[j]
                if a == "--":
                    ops.extend(x for x in args[j + 1:] if x != "-")
                    break
                if a in ("-t", "--target-directory"):
                    j += 1
                    if j < len(args):
                        dest_from_t = args[j]
                elif a.startswith("--target-directory="):
                    dest_from_t = a.split("=", 1)[1]
                elif a.startswith("-") and a != "-":
                    if cmd == "install" and a in ("-m", "-o", "-g", "-S", "--mode",
                                                  "--owner", "--group", "--suffix"):
                        j += 1  # this flag consumes a value
                else:
                    ops.append(a)
                j += 1
            if dest_from_t is not None:
                resolve(dest_from_t)
            elif len(ops) >= 2:
                resolve(ops[-1])  # only the destination is mutated; sources are reads
            return
        if cmd in _INPLACE_EDITORS:
            inplace = any(a == "--in-place" or a.startswith("--in-place=")
                          or (a.startswith("-") and not a.startswith("--")
                              and "i" in a[1:])
                          for a in args if a.startswith("-"))
            if not inplace:
                return  # plain interpreter/stream run — not a detected write op
            has_script_flag = False
            ops = []
            j = 0
            while j < len(args):
                a = args[j]
                if a == "--":
                    ops.extend(x for x in args[j + 1:] if x != "-")
                    break
                if a.startswith("-") and a != "-":
                    if cmd == "sed" and a in ("-e", "-f", "--expression", "--file"):
                        has_script_flag = True
                        j += 1  # the script / script-file argument (a read)
                    elif cmd == "sed" and (a.startswith("--expression=")
                                           or a.startswith("--file=")):
                        has_script_flag = True
                    elif cmd != "sed" and _SCRIPT_FLAG_RE.fullmatch(a):
                        j += 1  # interpreter script/module argument
                elif a != "-":
                    ops.append(a)
                j += 1
            if cmd == "sed" and not has_script_flag and ops:
                ops = ops[1:]  # the first bare operand is the sed script
            for a in ops:
                resolve(a)
            return
        if cmd == "git":
            base: object = None
            sub = None
            j = 0
            while j < len(args):
                a = args[j]
                if a == "-C":
                    j += 1
                    if j < len(args):
                        d = os.path.expanduser(args[j])
                        if "$" in d or "`" in d:
                            ambiguities.append("git -C over a shell variable cannot "
                                               "be resolved statically")
                            return
                        if not os.path.isabs(d):
                            if shifted or not isinstance(cwd, str) or not cwd:
                                ambiguities.append(f"git -C with unresolvable "
                                                   f"relative directory '{args[j]}'")
                                return
                            d = os.path.join(cwd, d)
                        base = d
                elif a in ("-c", "--git-dir", "--work-tree", "--namespace",
                           "--exec-path"):
                    j += 1
                elif a.startswith("-"):
                    pass
                else:
                    sub = a
                    break
                j += 1
            if sub in ("mv", "rm"):
                for a in _operands(args[j + 1:]):
                    resolve(a, base)
            return
        # anything else: not a recognized write/delete operation — passes

    cur: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if all(c in "();|&" for c in tok) and (">" not in tok and "<" not in tok):
            finalize(cur)
            cur = []
        elif all(c in "<>|&" for c in tok) and (">" in tok or "<" in tok):
            if cur and cur[-1].isdigit():
                cur.pop()  # fd number the tokenizer split off (2>file) — not an operand
            if tok == "<<":
                ambiguities.append("a heredoc defeats static tokenization")
                break
            i += 1
            nxt = tokens[i] if i < len(tokens) else None
            if ">" in tok:
                if nxt is None or all(c in "();<>|&" for c in nxt):
                    ambiguities.append("output redirection with no target")
                elif tok.endswith("&") and (nxt.isdigit() or nxt == "-"):
                    pass  # fd duplication/close (2>&1, >&-) — not a file target
                else:
                    resolve(nxt)
            # pure reads (<, <<<): operand consumed and ignored
        else:
            cur.append(tok)
        i += 1
    finalize(cur)
    return targets, ambiguities


def _decide_bash(writer: str, conf: dict, tool_input: object,
                 cwd: object) -> tuple[int, str]:
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 2, (
            f"karta: '{writer}' is the {conf['label']} writer and this Bash call "
            "carries no verifiable command string (tool_input.command missing or not "
            "a string) — an unverifiable call from a confined writer is denied, not "
            f"waved through. {conf['doctrine']}.")
    targets, ambiguities = _analyze(command, cwd)
    if ambiguities:
        return 2, (
            f"karta: '{writer}' is the {conf['label']} writer and this Bash command "
            f"is ambiguous to the confinement check ({ambiguities[0]}) — this guard "
            "fails closed for its recognized writers, so an ambiguous or unparseable "
            f"shell command is denied, not waved through. {conf['doctrine']}; use a "
            "plainly parseable command, or the Write/Edit tools, against that surface.")
    for target in targets:
        if not _allowed(conf, target, cwd):
            return 2, (
                f"karta: '{writer}' is the {conf['label']} writer and this Bash "
                f"command writes or deletes '{target}', outside its writable surface. "
                f"{conf['doctrine']}; nothing else is this writer's to touch via the "
                "shell either. Redirect the operation into that surface or drop it.")
    return 0, ""


def decide(payload: object) -> tuple[int, str]:
    """Return (exit_code, stderr_reason)."""
    if not isinstance(payload, dict):
        return 0, ""  # unrecognized shape — pass
    key = _recognized(payload.get("agent_type"))
    if key is None:
        return 0, ""  # main thread or unknown writer — pass
    conf = WRITERS[key]
    writer = payload["agent_type"]
    tool_input = payload.get("tool_input")
    if payload.get("tool_name") == "Bash":
        return _decide_bash(writer, conf, tool_input, payload.get("cwd"))
    targets = ([tool_input[k] for k in ("file_path", "notebook_path") if k in tool_input]
               if isinstance(tool_input, dict) else [])
    if not targets or not all(isinstance(t, str) for t in targets):
        return 2, (
            f"karta: '{writer}' is the {conf['label']} writer and this call carries "
            "no verifiable write target (tool_input.file_path / "
            "tool_input.notebook_path missing or not a string) — an unverifiable "
            f"write from a confined writer is denied, not waved through. "
            f"{conf['doctrine']}; every {conf['label']} write must name a path inside "
            "that surface.")
    for target in targets:
        if not _allowed(conf, target, payload.get("cwd")):
            return 2, (
                f"karta: '{writer}' is the {conf['label']} writer and '{target}' is "
                f"outside its writable surface. {conf['doctrine']}; this path is not, "
                "and nothing else is this writer's to write. Redirect the edit into "
                "that surface or drop it.")
    return 0, ""


def _run_self_test() -> int:
    def pre(agent_type: object = None, tool: str = "Write",
            tool_input: object = "__kwargs__", cwd: object = "/tmp",
            **ti: object) -> dict:
        payload: dict = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        payload["tool_input"] = ti if tool_input == "__kwargs__" else tool_input
        return payload

    def sh(agent_type: object, command: object = None, cwd: object = "/repo",
           tool_input: object = "__auto__") -> dict:
        payload: dict = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                         "cwd": cwd}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        payload["tool_input"] = ({"command": command} if tool_input == "__auto__"
                                 else tool_input)
        return payload

    cases = [
        # --- kaizen on Write/Edit/NotebookEdit (original cut, unchanged) ---
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
        ("doc-gardner (karta:karta-doc-gardner) writing docs/x.md passes (in surface)",
         pre("karta:karta-doc-gardner", file_path="docs/x.md", content="x"), 0, None),
        ("payload not a dict passes (unrecognized shape)", "not a dict", 0, None),
        ("prose mentioning kaizen without agent_type passes (identity from the field)",
         pre(file_path="docs/notes.md",
             content="karta-kaizen edits the packs; karta:karta-kaizen when namespaced"),
         0, None),
        # --- doc-gardner confinement row (A1) ---
        ("bare karta-doc-gardner writing README.md passes",
         pre("karta-doc-gardner", file_path="README.md", content="x"), 0, None),
        ("doc-gardner writing an absolute docs/ path passes (segment anchor)",
         pre("karta:karta-doc-gardner",
             file_path="/abs/worktree/docs/how-to/hooks.md", content="x"), 0, None),
        ("doc-gardner writing AGENTS.md passes",
         pre("karta-doc-gardner", file_path="AGENTS.md", content="x"), 0, None),
        ("doc-gardner writing CLAUDE.md passes",
         pre("karta-doc-gardner", file_path="CLAUDE.md", content="x"), 0, None),
        ("doc-gardner writing ARCHITECTURE.md passes (prefix pattern)",
         pre("karta-doc-gardner", file_path="ARCHITECTURE.md", content="x"), 0, None),
        ("doc-gardner writing .gitignore passes (the one non-doc exception)",
         pre("karta-doc-gardner", file_path=".gitignore", content="superpowers/"),
         0, None),
        ("doc-gardner writing other top-level markdown passes (cwd-relative)",
         pre("karta:karta-doc-gardner", file_path="/repo/CHANGELOG.md", content="x",
             cwd="/repo"), 0, None),
        ("doc-gardner absolute README in a sibling worktree passes (basename anchor)",
         pre("karta-doc-gardner", file_path="/abs/worktree/README.md", content="x"),
         0, None),
        ("doc-gardner writing nested non-doc markdown denied (not top-level)",
         pre("karta-doc-gardner", file_path="/repo/src/notes.md", content="x",
             cwd="/repo"), 2, "src/notes.md"),
        ("doc-gardner writing a skill file denied, reason names the doc surface",
         pre("karta:karta-doc-gardner", file_path="skills/karta-plan/SKILL.md",
             content="x"), 2, ("skills/karta-plan/SKILL.md", "`docs/`")),
        ("doc-gardner writing .karta/sme/python.md denied (zero .karta surface)",
         pre("karta-doc-gardner", file_path=".karta/sme/python.md", content="x"),
         2, ".karta/sme/python.md"),
        ("doc-gardner writing .karta/doc-gardner.json denied (config is user-authored)",
         pre("karta-doc-gardner", file_path=".karta/doc-gardner.json", content="{}"),
         2, None),
        ("doc-gardner writing src/app.py denied",
         pre("karta-doc-gardner", file_path="src/app.py", content="x"), 2, None),
        ("doc-gardner writing mydocs/x.md denied (docs/ segment boundary)",
         pre("karta-doc-gardner", file_path="mydocs/x.md", content="x"), 2, None),
        ("kaizen writing README.md denied (surfaces are per-writer)",
         pre("karta-kaizen", file_path="README.md", content="x"), 2, "README.md"),
        ("karta-doc-gardner-v2 writing anywhere passes (not an exact match)",
         pre("karta-doc-gardner-v2", file_path="src/app.py", content="x"), 0, None),
        # --- Bash coverage (A2): main thread / unrecognized always pass ---
        ("main-thread Bash rm -rf passes (no agent_type)",
         sh(None, "rm -rf /anything"), 0, None),
        ("unknown agent Bash redirect passes",
         sh("Explore", "echo x > /etc/motd"), 0, None),
        # --- Bash coverage: benign non-write commands pass ---
        ("kaizen `git status` passes (non-write command)",
         sh("karta-kaizen", "git status"), 0, None),
        ("kaizen `grep -r TODO src/` passes (non-write command)",
         sh("karta-kaizen", "grep -r TODO src/"), 0, None),
        ("kaizen `ls src 2>&1` passes (fd duplication is not a file target)",
         sh("karta-kaizen", "ls src 2>&1"), 0, None),
        ("kaizen quiet probe passes (sink devices are not write targets)",
         sh("karta-kaizen", "command -v plannotator >/dev/null 2>&1"), 0, None),
        ("doc-gardner stderr-to-/dev/null passes (quiet idiom)",
         sh("karta-doc-gardner", "grep -q pattern README.md 2>/dev/null"), 0, None),
        ("kaizen stdout-to-/dev/null passes (sink device)",
         sh("karta-kaizen", "git ls-files docs > /dev/null"), 0, None),
        ("doc-gardner fd-number redirect resolves the target, not the fd",
         sh("karta-doc-gardner", "rm docs/old.md 2>docs/err.log"), 0, None),
        ("kaizen fd-redirect delete outside the surface still blocked",
         sh("karta-kaizen", "rm README.md 2>/dev/null"), 2, "README.md"),
        ("kaizen `command` with a non-query flag stays ambiguous (blocked)",
         sh("karta-kaizen", "command -p rm .karta/sme/x.md"), 2, None),
        ("kaizen empty Bash command passes (nothing to write)",
         sh("karta-kaizen", ""), 0, None),
        # --- Bash coverage: in-surface writes pass, bypasses are blocked ---
        ("kaizen redirect into .karta/sme/ passes",
         sh("karta-kaizen", "echo note > .karta/sme/note.md"), 0, None),
        ("kaizen redirect outside the surface blocked",
         sh("karta-kaizen", "echo x > /etc/evil"), 2, "/etc/evil"),
        ("kaizen append redirect outside the surface blocked",
         sh("karta:karta-kaizen", "echo x >> src/app.py"), 2, "src/app.py"),
        ("kaizen tee bypass blocked (pipeline split)",
         sh("karta-kaizen", "cat .karta/sme/x.md | tee /tmp/leak"), 2, "/tmp/leak"),
        ("kaizen tee into the surface passes (multiple files)",
         sh("karta-kaizen", "echo x | tee .karta/sme/a.md .karta/sme/b.md"), 0, None),
        ("kaizen sed -i outside the surface blocked",
         sh("karta-kaizen", "sed -i s/a/b/ src/app.py"), 2, "src/app.py"),
        ("kaizen sed -i on a pack passes (script operand skipped)",
         sh("karta-kaizen", "sed -i s/a/b/ .karta/sme/python.md"), 0, None),
        ("kaizen rm of a pack passes (in-surface delete)",
         sh("karta-kaizen", "rm .karta/sme/stale.md"), 0, None),
        ("kaizen rm -rf outside the surface blocked (resolved against cwd)",
         sh("karta-kaizen", "rm -rf docs"), 2, "/repo/docs"),
        ("kaizen mv checks both ends — pack moved out of the surface blocked",
         sh("karta-kaizen", "mv .karta/sme/a.md /tmp/a.md"), 2, "/tmp/a.md"),
        ("kaizen git rm outside the surface blocked",
         sh("karta-kaizen", "git rm docs/x.md"), 2, "/repo/docs/x.md"),
        ("kaizen git -C resolves against the -C directory, blocked",
         sh("karta-kaizen", "git -C /elsewhere rm notes.txt"), 2,
         "/elsewhere/notes.txt"),
        ("env-assignment prefix does not hide the command — blocked",
         sh("karta-kaizen", "FOO=1 rm -rf src"), 2, "/repo/src"),
        ("bash -c wrapper is unwrapped — escape blocked",
         sh("karta-kaizen", "bash -c 'echo x > /etc/evil'"), 2, "/etc/evil"),
        ("bash -c wrapper with an in-surface write passes",
         sh("karta-kaizen", "bash -c 'echo x > .karta/sme/x.md'"), 0, None),
        # --- Bash coverage: doc-gardner surface ---
        ("doc-gardner redirect into docs/ passes",
         sh("karta-doc-gardner", "echo x >> docs/notes.md"), 0, None),
        ("doc-gardner redirect into top-level markdown passes (cwd resolution)",
         sh("karta-doc-gardner", "echo x >> CHANGELOG.md"), 0, None),
        ("doc-gardner git mv within docs/ passes",
         sh("karta:karta-doc-gardner", "git mv docs/a.md docs/b.md"), 0, None),
        ("doc-gardner rm within docs/ passes (relative resolution against cwd)",
         sh("karta-doc-gardner", "rm docs/old.md"), 0, None),
        ("doc-gardner cp checks the destination only (superpowers salvage shape)",
         sh("karta-doc-gardner",
            "cp superpowers/design.md docs/design-docs/design.md"), 0, None),
        ("doc-gardner redirect into src blocked",
         sh("karta-doc-gardner", "echo x > src/main.py"), 2, "/repo/src/main.py"),
        ("doc-gardner rm of code blocked",
         sh("karta-doc-gardner", "rm src/app.py"), 2, "/repo/src/app.py"),
        # --- Bash coverage: ambiguous/unparseable posture — fail-closed, denied ---
        ("variable write target is ambiguous — blocked (fail-closed posture)",
         sh("karta-kaizen", "echo x > $OUT"), 2, ("ambiguous", "fails closed")),
        ("command substitution is ambiguous — blocked even aimed at the surface",
         sh("karta-kaizen", "echo $(date) > .karta/sme/x.md"), 2, "ambiguous"),
        ("unparseable command (unbalanced quote) blocked",
         sh("karta-kaizen", 'echo "unterminated > .karta/sme/x.md'), 2, "ambiguous"),
        ("heredoc blocked as ambiguous even into the surface",
         sh("karta-kaizen", "cat << EOF > .karta/sme/x.md"), 2, "heredoc"),
        ("cd then relative write is ambiguous — blocked",
         sh("karta-doc-gardner", "cd /tmp && echo x > escape.md"), 2, "cd"),
        ("xargs into rm is ambiguous — blocked",
         sh("karta-kaizen", "find . -name '*.md' | xargs rm"), 2, "xargs"),
        ("relative target with no cwd in the payload is ambiguous — blocked",
         sh("karta-kaizen", "echo x > .karta/sme/x.md", cwd=None), 2, "no cwd"),
        ("recognized writer Bash with no command string blocked (unverifiable)",
         sh("karta-kaizen", tool_input={}), 2, "no verifiable"),
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
        # fail closed only on the writers this guard exists to confine; all else passes
        try:
            key = _recognized(payload.get("agent_type"))
        except Exception:  # noqa: BLE001
            key = None
        if key is None:
            return 0
        code, reason = 2, (
            "karta: internal error while checking a confined writer's call — this "
            f"guard fails closed for the writers it recognizes. "
            f"{WRITERS[key]['doctrine']}; retry with a target inside that surface.")
    if code == 2:
        print(reason, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
