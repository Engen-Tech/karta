# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Plugin integrity check: SKILL.md frontmatter, reference-link existence, hook assets.

Usage:
  uv run scripts/validate_plugin.py --self-test   # check this repo, exit 0/1
"""
from __future__ import annotations
import argparse, ast, json, os, re, shlex, subprocess, sys, tomllib
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
import sync_codex_skills, sync_codex_agents, check_fact_traces  # noqa: E402


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


# Portability policy: what the encoding rule reads is committed as data
# (.karta/portability.json) rather than written into the code, so widening it
# is a reviewable diff and a fixture can prove the rule consults it. The file
# also carries the command-portability data the windows-support branch wrote;
# that sibling rule arrives with the Codex-launch work and reads the SAME
# exclusions array through the SAME predicate, which is the point of one file.
POLICY_PATH = ROOT / ".karta" / "portability.json"

# The subprocess entry points whose first argument is an argv.
CP_SUBPROCESS_CALLS = frozenset({"run", "Popen", "call", "check_call", "check_output"})

def _excluded(rel: str, policy: dict) -> bool:
    """True when a repo-relative posix path falls under a committed exclusion."""
    return any(rel.startswith(x["path"])
               for x in policy.get("exclusions", [])
               if isinstance(x, dict) and isinstance(x.get("path"), str))

def _cp_tracked(errors: list[str], root: Path | None = None) -> list[str] | None:
    """Every tracked path, from git. The rule's reach is derived, never hand-listed.

    `root` is a parameter so a fixture can point the SAME derivation at a repo of
    its own making — the only way to prove the reach is computed at check time
    rather than read off a list."""
    root = ROOT if root is None else root
    try:
        proc = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"command portability: could not enumerate tracked files ({e}) — the "
                      f"rule's reach comes from git, so this is a broken check, not a clean one")
        return None
    if proc.returncode != 0:
        errors.append(f"command portability: `git ls-files` failed "
                      f"({proc.stderr.decode('utf-8', 'replace').strip()})")
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]

EC_TEXT_FILE_METHODS = frozenset({"read_text", "write_text"})
EC_LOG_FILE_HANDLERS = frozenset({"FileHandler", "RotatingFileHandler",
                                  "TimedRotatingFileHandler", "WatchedFileHandler"})
# UnicodeDecodeError IS a ValueError, so catching the parent hides the identical
# bug; a bare `except:` hides it too. All four are the swallow shape.

EC_SWALLOWABLE = frozenset({"UnicodeDecodeError", "UnicodeError", "ValueError", "Exception"})
EC_TEXT_SUBPROCESS_KWARGS = ("text", "universal_newlines")


def _ec_kw(node: ast.Call, name: str):
    return next((k.value for k in node.keywords if k.arg == name), None)


def _ec_opener(fn) -> str | None:
    """'open' / 'io.open' / 'codecs.open' for a call that opens a file, else None."""
    if isinstance(fn, ast.Name) and fn.id == "open":
        return "open"
    if (isinstance(fn, ast.Attribute) and fn.attr == "open"
            and isinstance(fn.value, ast.Name) and fn.value.id in ("io", "codecs")):
        return f"{fn.value.id}.open"
    return None


def _ec_mode(node: ast.Call) -> str:
    """'text' | 'binary' | 'computed' for an opener call.

    A COMPUTED mode is deliberately not a finding. The rule must not guess at a
    mode string it cannot read: a wrong guess fires on correct code, and a gate
    every commit passes through wedges the repo on its first false positive."""
    mode = node.args[1] if len(node.args) > 1 else _ec_kw(node, "mode")
    if mode is None:
        return "text"
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "binary" if "b" in mode.value else "text"
    return "computed"


def _ec_is_subprocess(fn, imported: bool) -> bool:
    if isinstance(fn, ast.Attribute):
        return fn.attr in CP_SUBPROCESS_CALLS
    return isinstance(fn, ast.Name) and imported and fn.id in CP_SUBPROCESS_CALLS


def _ec_call_findings(node: ast.Call, imported: bool) -> list[str]:
    """Every way one call crosses a text boundary without naming its codec."""
    out: list[str] = []
    fn = node.func
    encoded = _ec_kw(node, "encoding") is not None
    name = fn.attr if isinstance(fn, ast.Attribute) else (
        fn.id if isinstance(fn, ast.Name) else "")
    if name in EC_TEXT_FILE_METHODS and not encoded:
        out.append(f'{name}() names no encoding — it uses the locale codec (cp1252 on a '
                   f'stock Windows checkout); pass encoding="utf-8"')
    opener = _ec_opener(fn)
    if opener:
        # codecs.open takes encoding third positionally, builtin open fourth.
        positional = len(node.args) > (2 if opener == "codecs.open" else 3)
        if _ec_mode(node) == "text" and not (encoded or positional):
            out.append(f'{opener}() in text mode names no encoding; pass encoding="utf-8" '
                       f'or open in binary mode')
    if name in EC_LOG_FILE_HANDLERS and not encoded:
        out.append(f'{name}() names no encoding — a log record outside the locale codec '
                   f'raises inside logging; pass encoding="utf-8"')
    if (isinstance(fn, ast.Attribute) and fn.attr in ("decode", "encode")
            and not node.args and not node.keywords):
        out.append(f'bare .{fn.attr}() — same boundary and same locale codec as '
                   f'subprocess(text=True), different API; name the codec')
    if _ec_is_subprocess(fn, imported) and not encoded:
        for kw in EC_TEXT_SUBPROCESS_KWARGS:
            arg = _ec_kw(node, kw)
            if isinstance(arg, ast.Constant) and arg.value is True:
                out.append(f'subprocess capture with {kw}=True names no encoding — the '
                           f'child\'s bytes are decoded with the locale codec; pass '
                           f'encoding="utf-8" (and errors=)')
                break
    return out


# The error handlers that make a codec TOTAL: it substitutes rather than raises,
# so no decode failure exists at that call for a handler to hide.
EC_TOTAL_ERROR_HANDLERS = frozenset({
    "replace", "ignore", "surrogateescape", "backslashreplace", "namereplace",
    "xmlcharrefreplace", "surrogatepass"})


def _ec_total(node: ast.Call) -> bool:
    """True when the call names an errors= handler that cannot raise on bad bytes.

    `.decode("utf-8", "replace")` is a DECLARED decision to substitute, not a
    swallow: the caller chose what bad bytes become. The swallowed-error rule is
    about handlers that hide a failure, so a call with no failure to hide is not
    the boundary it scopes over."""
    err = _ec_kw(node, "errors")
    if (err is None and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("decode", "encode") and len(node.args) > 1):
        err = node.args[1]                        # bytes.decode(encoding, errors)
    return isinstance(err, ast.Constant) and err.value in EC_TOTAL_ERROR_HANDLERS


def _ec_is_boundary(node: ast.Call, imported: bool) -> bool:
    """True when a call decodes or encodes text and COULD raise on bad bytes.

    Scopes the swallowed-error rule: a handler is judged for hiding an encoding
    defect only when the try body could actually raise one."""
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else ""
    if _ec_total(node):
        return False
    if name in EC_TEXT_FILE_METHODS or name in ("decode", "encode"):
        return True
    if _ec_opener(fn):
        # Binary mode has nothing to decode, so it cannot be the failure the
        # handler would hide; computed mode stays a boundary — the rule must
        # not guess a mode it cannot read in the caller's favour.
        return _ec_mode(node) != "binary"
    if _ec_is_subprocess(fn, imported):
        return any(_ec_kw(node, k) is not None
                   for k in (*EC_TEXT_SUBPROCESS_KWARGS, "encoding"))
    return False


def _ec_caught(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"Exception"}                      # a bare except hides everything
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    out = set()
    for n in nodes:
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def _ec_surfaces(handler: ast.ExceptHandler) -> bool:
    """True when the handler tells someone the failure happened.

    Re-raising, exiting, or naming the bound exception all surface it. A handler
    that does none of the three returns a DEGRADED result and exits 0 — the
    encoding defect turned into a wrong answer, which is worse than the crash."""
    for n in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            fn = n.func
            if ((isinstance(fn, ast.Name) and fn.id in ("exit", "_exit"))
                    or (isinstance(fn, ast.Attribute) and fn.attr in ("exit", "_exit"))):
                return True
        if (handler.name and isinstance(n, ast.Name) and n.id == handler.name
                and isinstance(n.ctx, ast.Load)):
            return True
    return False


def _ec_stdin_findings(tree: ast.AST) -> list[tuple[int, str]]:
    """The stdin rule is per-ENTRY-POINT, not per-call-site.

    `json.load(sys.stdin)` is the shape this work actually found, and a rule
    hunting `sys.stdin.read()` misses it entirely. Once a module reconfigures the
    stream there is no violation left at any read site, so the invariant that can
    be enforced is: a module that reads sys.stdin as TEXT reconfigures it."""
    reconfigured = False
    uses: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == "stdin"
                and isinstance(node.value, ast.Name) and node.value.id == "sys"):
            continue
        parent = getattr(node, "_ec_parent", None)
        if isinstance(parent, ast.Attribute):
            if parent.attr == "buffer":
                continue                          # bytes, not text — correct by construction
            if parent.attr == "reconfigure":
                reconfigured = True
                continue
        # `getattr(sys.stdin, "buffer", sys.stdin)` is this repo's binary-first
        # idiom: it reads the BYTE stream and decodes explicitly, falling back to
        # the object itself only when a test double has no .buffer. Both mentions
        # of sys.stdin belong to that one expression, so neither is a text read —
        # firing here would reject the very shape the repairs standardised on.
        if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                and parent.func.id == "getattr" and len(parent.args) >= 2
                and isinstance(parent.args[1], ast.Constant)
                and parent.args[1].value == "buffer"):
            continue
        uses.append(node.lineno)
    if reconfigured or not uses:
        return []
    return [(min(uses), "reads sys.stdin as text without sys.stdin.reconfigure(encoding=...) "
                        "at the entry point — the stream decodes with the locale codec, and "
                        "`json.load(sys.stdin)` is the shape that hides it")]


def _ec_python_findings(source: str) -> list[tuple[int, str]]:
    """(line, reason) for every unnamed text boundary in one module."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._ec_parent = node
    imported = any(isinstance(n, ast.ImportFrom) and n.module == "subprocess"
                   for n in ast.walk(tree))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for why in _ec_call_findings(node, imported):
                found.append((node.lineno, why))
        elif isinstance(node, ast.Try):
            if not any(isinstance(n, ast.Call) and _ec_is_boundary(n, imported)
                       for stmt in node.body for n in ast.walk(stmt)):
                continue
            for handler in node.handlers:
                caught = _ec_caught(handler) & EC_SWALLOWABLE
                if caught and not _ec_surfaces(handler):
                    found.append((handler.lineno,
                                  f"catches {', '.join(sorted(caught))} around a text boundary "
                                  f"and neither raises, exits, nor names the error — a decode "
                                  f"failure becomes a degraded result that exits 0"))
    found.extend(_ec_stdin_findings(tree))
    return sorted(set(found))


def _ec_imported_modules(tree: ast.AST) -> set[str]:
    mods: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
            mods.update(f"{n.module}.{a.name}" for a in n.names)
    return mods


def _ec_loader_sites(tree: ast.AST, rel: str, loaders) -> list[str]:
    """Karta-owned call sites into a stdlib loader that opens text internally.

    The NAMED LIMITATION, made countable. This rule inspects call sites, so a
    loader that opens its own file (configparser, xml.etree, email,
    argparse.FileType) never trips the encoding rule no matter what codec it
    picks. These sites are therefore INVENTORIED, not judged: the committed list
    in the policy must match what the tree holds, so the gap stays bounded and a
    new one cannot enter unnoticed."""
    mods = _ec_imported_modules(tree)
    out: set[str] = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = (n.func.attr if isinstance(n.func, ast.Attribute)
                else n.func.id if isinstance(n.func, ast.Name) else "")
        for spec in loaders:
            mod, call = spec.get("module"), spec.get("call")
            if not (isinstance(mod, str) and isinstance(call, str)) or name != call:
                continue
            if any(m == mod or m.startswith(f"{mod}.") or mod.startswith(f"{m}.")
                   for m in mods):
                out.add(f"{rel}:{call}")
    return sorted(out)


def _check_encoding(errors: list[str], policy: dict | None = None, py_files=None,
                    root: Path | None = None) -> None:
    """Reject text boundaries with no explicit codec in karta-owned Python.

    `policy`, `py_files` and `root` exist so the fixtures can drive the rule over
    fabricated input; left alone, all three come from the committed policy and git."""
    if policy is None:
        policy = _load_json(POLICY_PATH, errors)
        if not policy:
            return
    inventory_scope = py_files is None
    if py_files is None:
        base = ROOT if root is None else root
        tracked = _cp_tracked(errors, root=base)
        if tracked is None:
            return
        py_files = [(base / rel, rel) for rel in tracked
                    if rel.endswith(".py") and not _excluded(rel, policy)]
    loaders = policy.get("stdlib_text_loaders") or []
    found_sites: list[str] = []
    for path, rel in py_files:
        try:
            source = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            errors.append(f"{rel}: tracked Python that is not decodable as UTF-8 ({e})")
            continue
        except OSError as e:
            errors.append(f"{rel}: unreadable Python source ({e})")
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            errors.append(f"{rel}: unparseable Python ({e})")
            continue
        for line, why in _ec_python_findings(source):
            errors.append(f"{rel}:{line}: {why}")
        found_sites.extend(_ec_loader_sites(tree, rel, loaders))
    if not inventory_scope:
        return                                    # a fixture's file list is not the tree
    committed = (policy.get("stdlib_loader_call_sites") or {}).get("sites")
    if not isinstance(committed, list):
        errors.append(f"{POLICY_PATH.name}: stdlib_loader_call_sites.sites must be a list — "
                      f"it is the committed inventory of the limitation this rule names")
        return
    for site in sorted(set(found_sites) - set(committed)):
        errors.append(f"{site}: calls a stdlib loader that opens text itself, and this call "
                      f"site is not in stdlib_loader_call_sites.sites — the encoding rule "
                      f"cannot see inside it, so the gap is inventoried or it is invisible")
    for site in sorted(set(committed) - set(found_sites)):
        errors.append(f"{site}: listed in stdlib_loader_call_sites.sites but no longer in the "
                      f"tree — an inventory nobody prunes stops describing anything")



LINE_ENDING_DEFAULT = "* text=auto eol=lf"
# The format belt's extensions, lowercased. gitattributes patterns are
# case-sensitive on case-sensitive filesystems, so `LOGO.PNG` slips past
# `*.png` there — the check below therefore asserts every tracked file OF a
# belt format resolves -text however its name is spelled, which fails the
# commit until the belt (or a rename) covers it.
LINE_ENDING_BELT = frozenset({
    "png", "jpg", "jpeg", "gif", "ico", "webp", "woff", "woff2", "ttf", "otf",
    "eot", "pdf", "zip", "gz", "tar"})


def _check_line_endings(errors: list[str], root: Path | None = None) -> None:
    """Line endings are a repo rule, enforced from git's own index state.

    Two facts, both read from git rather than from a hand-kept list:

      * .gitattributes carries the default `* text=auto eol=lf`, so the
        working-tree guarantee holds whatever a contributor's core.autocrlf
        says — without it the rest of this check enforces nothing for the
        NEXT clone;
      * no tracked file's INDEX bytes carry CRLF unless a `-text` attribute
        deliberately exempts it (`git ls-files --eol` reports both the stored
        eol and the resolved attribute per file). Binary content is exempt by
        git's detection plus the format belt in .gitattributes — formats, not
        a path inventory: the path list the earlier version of this rule kept
        rotted from 12 entries to 39 while it sat on a parked branch;
      * the root .gitattributes is the WHOLE policy: a nested .gitattributes
        anywhere in the tree is a finding (an override no one reads the root
        file to discover), and so is any `eol=crlf` in the root file (it
        would flip checkouts while every index check here stays green);
      * the .karta byte-store guarantee is asserted POSITIVELY: a live
        sentinel under .karta/ must resolve text/eol/filter all unset, and
        working-tree-encoding/ident unset or unspecified — the roundtable
        gate hashes those bytes verbatim, and any of the five attributes
        would let git store different bytes than the gate approved.

    Named limits: the check reads the index and the working tree's attribute
    files, the same tree-state posture every gate in this repo takes; and a
    NUL-carrying text encoding (UTF-16) classifies as binary to git, so its
    line endings are stored verbatim rather than policed — this repo's
    encoding rule keeps tracked text UTF-8, which is what closes that door.
    """
    base = ROOT if root is None else root
    ga = base / ".gitattributes"
    try:
        lines = [ln.strip() for ln in ga.read_text(encoding="utf-8").splitlines()]
    except OSError:
        lines = []
    if LINE_ENDING_DEFAULT not in lines:
        errors.append(
            f".gitattributes: missing the exact default '{LINE_ENDING_DEFAULT}' — "
            f"without it line endings are a per-machine setting again, and every "
            f"CRLF this check would catch can enter on the next differently "
            f"configured clone")
    for ln in lines:
        # git treats '#' as a comment only at line START; an embedded '#' is a
        # legal pattern character, so truncating at it would let a pattern
        # like `issue#1.txt text eol=crlf` smuggle the override past this scan.
        if ln.startswith("#"):
            continue
        if "eol=crlf" in ln:
            errors.append(
                f".gitattributes: '{ln}' sets eol=crlf — it would flip checkouts "
                f"to CRLF while every index assertion here stays green; a "
                f"byte-preserving exemption is -text, never a CRLF conversion")
    try:
        nested = subprocess.run(
            ["git", "-C", str(base), "ls-files", "-z", "--", "*/.gitattributes",
             "*/.git/info/attributes"],
            capture_output=True, timeout=120)
        for rel in nested.stdout.decode("utf-8", "replace").split("\0"):
            if rel:
                errors.append(
                    f"{rel}: a nested attributes file can override the root "
                    f"line-ending policy where nobody reads for it — the root "
                    f".gitattributes is the whole policy in this repo")
    except (OSError, subprocess.TimeoutExpired):
        pass  # the ls-files call below reports the broken-git case once
    sentinel = next(iter(sorted((base / ".karta").rglob("*.json"))), None) \
        if (base / ".karta").is_dir() else None
    if sentinel is not None:
        try:
            rel = sentinel.relative_to(base).as_posix()
            attr = subprocess.run(
                ["git", "-C", str(base), "check-attr", "-z", "text", "eol",
                 "filter", "ident", "working-tree-encoding", "--", rel],
                capture_output=True, timeout=120)
            fields = attr.stdout.decode("utf-8", "replace").split("\0")
            resolved = {fields[i + 1]: fields[i + 2]
                        for i in range(0, len(fields) - 2, 3)}
            for name in ("text", "eol", "filter"):
                if resolved.get(name) != "unset":
                    errors.append(
                        f"{rel}: attribute '{name}' resolves to "
                        f"{resolved.get(name)!r}, not 'unset' — the roundtable "
                        f"gate hashes .karta bytes verbatim, and this attribute "
                        f"would let git store different bytes than it approved")
            for name in ("ident", "working-tree-encoding"):
                if resolved.get(name) not in ("unset", "unspecified"):
                    errors.append(
                        f"{rel}: attribute '{name}' resolves to "
                        f"{resolved.get(name)!r} — a checkout/check-in transform "
                        f"on a .karta path breaks the byte-hash gate")
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"line endings: could not resolve .karta sentinel "
                          f"attributes ({e})")
    try:
        proc = subprocess.run(["git", "-C", str(base), "ls-files", "--eol", "-z"],
                              capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"line endings: could not read index eol state ({e}) — a "
                      f"broken check, not a clean one")
        return
    if proc.returncode != 0:
        errors.append(f"line endings: `git ls-files --eol` failed "
                      f"({proc.stderr.decode('utf-8', 'replace').strip()})")
        return
    for entry in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not entry or "\t" not in entry:
            continue
        info, rel = entry.split("\t", 1)
        fields = info.split()
        index_eol = next((f[2:] for f in fields if f.startswith("i/")), "")
        attr = next((f[5:] for f in fields if f.startswith("attr/")), "")
        if index_eol in ("crlf", "mixed") and "-text" not in attr.split():
            errors.append(
                f"{rel}: stored with {index_eol.upper()} line endings and no "
                f"-text exemption — normalize it (git add --renormalize) or, "
                f"for a deliberate byte-preserving store, give it a -text line "
                f"in .gitattributes so the decision is visible")
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else ""
        if ext in LINE_ENDING_BELT and "-text" not in attr.split():
            errors.append(
                f"{rel}: a {ext} file whose resolved attributes lack -text — the "
                f"format belt did not reach it (case-variant spelling, or a "
                f"belt line removed), so text=auto's content sniff is all that "
                f"stands between this binary and a silent CR/LF rewrite")


def _check_git_output_encoding(errors: list[str], value: str | None = None) -> None:
    """The other named limitation: decoding git's output as UTF-8 is right only while
    git EMITS UTF-8. A contributor who sets i18n.logOutputEncoding to a legacy codec
    defeats a correctly encoding-explicit parent, and no amount of care at the call
    site fixes it. The rule checks the setting rather than pretending to solve it."""
    if value is None:
        try:
            proc = subprocess.run(["git", "-C", str(ROOT), "config", "--get",
                                   "i18n.logOutputEncoding"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return                                # no git here is another check's problem
        value = proc.stdout.strip() if proc.returncode == 0 else ""
    if value and value.lower().replace("-", "") not in ("utf8",):
        errors.append(f"git config i18n.logOutputEncoding is {value!r} — karta decodes git's "
                      f"output as UTF-8, so a legacy codec here mojibakes every branch, ref "
                      f"and path karta reads, with no defect at the call site to find")


# The Codex manifests whose command hooks must be launchable on both platforms.
# Both are hand-edited; plugins/karta/ is a generated projection of the first and
# is covered by sync_codex_skills.py --check, so listing it here would only
# re-report the same defect under a second path.
CODEX_HOOK_MANIFESTS = (
    ROOT / ".codex-plugin" / "hooks" / "hooks.json",
    ROOT / ".codex" / "hooks.json",
)


def _command_hooks(data: dict):
    """(event, hook) for every `type: command` hook in a hook manifest.

    Shared by the Codex Windows-twin check and the command-portability check so the
    two can never disagree about what counts as a command hook — the nested
    event -> matcher group -> hooks walk is identical for both, and duplicating it
    is how one of them would quietly stop seeing a hook the other still sees."""
    for event, groups in (data.get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue                      # shape errors belong to the manifest's own schema
        for group in groups:
            hook_list = group.get("hooks") if isinstance(group, dict) else None
            for hook in hook_list if isinstance(hook_list, list) else []:
                if isinstance(hook, dict) and hook.get("type") == "command":
                    yield event, hook


def _check_codex_hook_windows(errors: list[str], manifests=None) -> None:
    """Every Codex command hook must carry a `commandWindows` twin naming the same guard.

    Codex runs a hook's POSIX `command` through `sh` (`SHELL` or `/bin/sh`) and its
    `commandWindows` through `cmd.exe /C`. A stock Windows install has no `sh`, so a
    hook with only a POSIX command does not merely skip — it FAILS, and the turn
    reports `hook exited with code 1` while naming neither the shell nor the guard.
    That is indistinguishable from a guard that deliberately blocked.

    This check exists because the failure is silent in the direction that matters:
    the guards are fail-closed security hooks, and nothing else in the repo reads
    these manifests. Adding a hook without a Windows twin would re-break the
    platform with no signal until someone ran Codex on Windows.

    Three things are enforced per command hook:

      * a non-empty `commandWindows` exists;
      * it names the same guard script as its POSIX twin, so the two cannot drift
        onto different scripts — the failure mode where Windows silently enforces
        a different rule than POSIX;
      * it does not itself invoke `sh`, which would reintroduce the dependency the
        twin exists to remove.

    Deliberately NOT enforced: the shape of the launcher. How the command finds the
    guard differs by manifest — the bundled plugin keys off %PLUGIN_ROOT%, the
    repo-local one resolves the checkout with git — and pinning a spelling here
    would freeze an implementation detail rather than the contract.
    """
    for manifest in (manifests if manifests is not None else CODEX_HOOK_MANIFESTS):
        if not manifest.exists():
            continue                      # optional surface; _check_codex covers required files
        try:
            rel = manifest.relative_to(ROOT).as_posix()
        except ValueError:
            rel = manifest.name
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            errors.append(f"{rel}: unreadable Codex hook manifest ({e})")
            continue
        for event, hook in _command_hooks(data):
            posix = hook.get("command") or ""
            win = hook.get("commandWindows") or ""
            guards = _guard_scripts(posix)
            label = f"{rel}: {event} hook for {'/'.join(guards) or '(unknown script)'}"
            if not win.strip():
                errors.append(
                    f"{label}: missing 'commandWindows' — the POSIX command runs "
                    f"through sh, which a stock Windows install does not have, so "
                    f"this hook fails rather than runs there")
                continue
            if re.search(r"(?<![\w.-])sh\s+-[a-z]*c(?![\w-])", win):
                errors.append(
                    f"{label}: 'commandWindows' invokes sh, defeating its purpose")
            win_guards = _guard_scripts(win)
            if guards and not win_guards:
                # The drift the panel found the first version accepting: a twin
                # that is present, non-empty, sh-free — and runs NOTHING. An
                # `exit /b 0` here would disable the guard on exactly one
                # platform while every per-hook assertion above stays green.
                errors.append(
                    f"{label}: 'commandWindows' names no guard script at all — a "
                    f"no-op twin disables this guard on Windows while its POSIX "
                    f"twin still enforces")
            elif guards and win_guards != guards:
                errors.append(
                    f"{label}: 'commandWindows' runs {'/'.join(win_guards)} but its "
                    f"POSIX twin runs {'/'.join(guards)} — the two platforms would "
                    f"enforce different rules")


def _guard_scripts(command: str) -> tuple[str, ...]:
    """The guard scripts a hook command runs, as repo-relative posix paths.

    Anchored at a repo-relative root rather than matched as a loose suffix: both
    manifests interpolate a variable immediately before the path
    (`${PLUGIN_ROOT}/...`, `$r/...`, `%PLUGIN_ROOT%\\...`), and a pattern that
    allows leading path characters swallows the variable's tail into the match.
    """
    found = re.findall(r"(?<![\w.])((?:\.codex-plugin|scripts)[\\/][\w./\\-]*\.py)", command)
    return tuple(sorted({f.replace("\\", "/") for f in found}))


# ---------------------------------------------------------------------------
# Command portability — no POSIX-only shell, interpreter, or utility assumptions
# ---------------------------------------------------------------------------
# karta launched every bundled Codex guard through `sh -c`. A stock Windows
# install has no `sh`, so every guard failed at the LAUNCHER while the guards
# themselves were correct — and the turn reported only `hook exited with code 1`,
# which reads exactly like a guard that deliberately blocked. That is a class,
# not an instance: an encoding rule inspects decoders and would never have caught
# a command. This rule covers the class.
#
# What the rule reads is committed as data (.karta/portability.json), not
# written into the code: the utility list, the interpreter names, the shell
# launchers, and the exclusions with the reason each one was excluded. Widening
# the rule is then a reviewable diff to that file, and a fixture can prove the
# rule consults it rather than restating it.
#
# Reach is derived from git rather than hand-listed, so a manifest or script
# added later cannot escape by not being on a list. Exclusions are the committed
# counterweight — every one carries its reason in the same file.

# Tokens after which the NEXT token is a command name rather than an argument.
# The utility check fires only in command position: an argument that happens to
# read `test` is not an invocation of test(1), and a rule that cannot tell the
# difference is a rule that wedges the repo on its first false positive.
CP_COMMAND_POSITION = frozenset({"|", "||", "&&", ";", "exec", "xargs", "env"})

def _cp_alt(names) -> str:
    return "|".join(re.escape(str(n)) for n in names)


def _cp_command_findings(command: str, policy: dict) -> list[str]:
    """Every way one command string assumes a POSIX shell, interpreter, or tool."""
    out: list[str] = []
    shells = _cp_alt(policy.get("shell_launchers", ()))
    if shells and re.search(rf"(?<![\w./\\-])({shells})\s+-[a-zA-Z]*c(?![\w-])", command):
        out.append("launches through a POSIX shell (`sh -c` / `bash -c`), which a stock "
                   "Windows install does not have — the hook fails rather than runs there")
    interps = _cp_alt(policy.get("interpreter_names", ()))
    if interps and re.search(rf"(?<![\w./\\-])({interps})(?![\w.-])", command):
        out.append("names a bare `python` / `python3` interpreter, which on Windows is "
                   "either absent or the Store stub that opens a store page and exits")
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    suffixes = tuple(policy.get("script_suffixes", ()))
    if tokens and suffixes and tokens[0].endswith(suffixes):
        out.append(f"runs {tokens[0]!r} directly, relying on its shebang and executable "
                   f"bit — Windows honours neither")
    utils = set(policy.get("posix_only_utilities", ()))
    for i, tok in enumerate(tokens):
        if i and tokens[i - 1] not in CP_COMMAND_POSITION:
            continue                      # an argument, not a command name
        base = tok.replace("\\", "/").rsplit("/", 1)[-1]
        if base in utils:
            out.append(f"invokes the POSIX-only utility {base!r}, absent from PowerShell")
    return out


def _cp_check_manifest(path: Path, rel: str, policy: dict, errors: list[str]) -> None:
    """A hook manifest is portable when every command either runs on both platforms
    or carries a Windows twin. The twin suppresses the POSIX findings on its own
    hook: _check_codex_hook_windows already proves that twin is non-empty, names the
    same guard, and does not itself shell out — so a POSIX `command` beside a
    validated twin is one half of a two-platform pair, not an assumption. A Claude
    manifest has no twin mechanism at all, so its single command must be portable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        errors.append(f"{rel}: unreadable hook manifest ({e})")
        return
    if not isinstance(data, dict):
        return
    for event, hook in _command_hooks(data):
        command = hook.get("command") or ""
        if (hook.get("commandWindows") or "").strip() or not command.strip():
            continue
        for why in _cp_command_findings(command, policy):
            errors.append(f"{rel}: {event} hook command is not portable — {why}")


def _cp_strings(node, bindings: dict, scope: int, depth: int = 0) -> list[str]:
    """The string constants an argv element can evaluate to, one alias hop deep.

    One hop is what catches the shape this repo actually had:
    `py = sys.executable or "python3"` followed by `subprocess.run([py, ...])`.
    A rule that read only the literals written inside the call would have missed
    both live instances of its own defect class."""
    if node is None or depth > 1:
        return []
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.Name):
        return _cp_strings(bindings.get((scope, node.id)), bindings, scope, depth + 1)
    if isinstance(node, ast.BoolOp):
        return [s for v in node.values for s in _cp_strings(v, bindings, scope, depth)]
    if isinstance(node, ast.IfExp):
        return (_cp_strings(node.body, bindings, scope, depth)
                + _cp_strings(node.orelse, bindings, scope, depth))
    return []


def _cp_posix_branch(test: ast.AST) -> str | None:
    """'body' / 'orelse' when `test` is a plain platform comparison and that arm
    is the POSIX one; None for any test this reader cannot fully decide.

    Recognised: `os.name ==/!= "<x>"` and `sys.platform ==/!= "<x>"` with one
    constant. `os.name == "nt"` puts POSIX in the orelse; `os.name != "nt"` and
    `os.name == "posix"` put it in the body. Anything else — a disjunct, a
    call, a precomputed flag — returns None and the launch stays FLAGGED: a
    test the rule cannot read must never widen the exemption, or a dead
    `if os.name == "posix":` wrapper becomes a licence."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and len(test.comparators) == 1):
        return None
    left, op, right = test.left, test.ops[0], test.comparators[0]
    if isinstance(right, ast.Attribute) and isinstance(left, ast.Constant):
        left, right = right, left
    if not (isinstance(left, ast.Attribute) and left.attr in ("name", "platform")
            and isinstance(left.value, ast.Name) and left.value.id in ("os", "sys")
            and isinstance(right, ast.Constant) and isinstance(right.value, str)):
        return None
    if not isinstance(op, (ast.Eq, ast.NotEq)):
        return None
    names_windows = right.value in ("nt", "win32", "cygwin")
    equals_windows = names_windows == isinstance(op, ast.Eq)
    return "orelse" if equals_windows else "body"


def _cp_platform_guarded(node, parents) -> bool:
    """True when `node` sits in the POSIX arm of a platform branch. A POSIX
    shell chosen BY a platform branch is a decision — run_oracle's `sh -c` is
    the documented POSIX half of an os.name split whose other half runs
    cmd.exe — where the same launch with no branch around it, in the WINDOWS
    arm, or under a test this reader cannot decide is the assumption this rule
    exists to reject. Only the shell finding consults this: a bare `python3`
    or a POSIX utility stays wrong inside a platform branch too, because
    sys.executable and portable tools exist on both sides."""
    child = node
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.If):
            arm = _cp_posix_branch(cur.test)
            if arm is not None:
                in_body = any(child is s or any(child is d for d in ast.walk(s))
                              for s in cur.body)
                if arm == ("body" if in_body else "orelse"):
                    return True
        child = cur
        cur = parents.get(id(cur))
    return False


def _cp_python_findings(source: str, policy: dict) -> list[tuple[int, str]]:
    """(line, reason) for every subprocess invocation in one module that assumes POSIX."""
    tree = ast.parse(source)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def scope_of(node) -> int:
        cur = parents.get(id(node))
        while cur is not None and not isinstance(
                cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            cur = parents.get(id(cur))
        return id(cur)

    bindings: dict[tuple[int, str], ast.AST] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            bindings[(scope_of(node), node.targets[0].id)] = node.value

    imported = any(isinstance(n, ast.ImportFrom) and n.module == "subprocess"
                   for n in ast.walk(tree))
    shells = set(policy.get("shell_launchers", ()))
    interps = set(policy.get("interpreter_names", ()))
    utils = set(policy.get("posix_only_utilities", ()))
    suffixes = tuple(policy.get("script_suffixes", ()))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            if fn.attr not in CP_SUBPROCESS_CALLS:
                continue
        elif isinstance(fn, ast.Name):
            if not (imported and fn.id in CP_SUBPROCESS_CALLS):
                continue
        else:
            continue
        argv = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == "args"), None)
        if argv is None:
            continue
        scope = scope_of(node)
        if isinstance(argv, ast.Name):
            argv = bindings.get((scope, argv.id), argv)
        if isinstance(argv, (ast.List, ast.Tuple)):
            elements = list(argv.elts)
        elif isinstance(argv, ast.Constant) and isinstance(argv.value, str):
            for why in _cp_command_findings(argv.value, policy):
                found.append((node.lineno, why))
            continue
        else:
            continue                      # an argv this rule cannot read is not a claim
        if not elements:
            continue
        rest = {s for e in elements[1:] for s in _cp_strings(e, bindings, scope)}
        for head in _cp_strings(elements[0], bindings, scope):
            base = head.replace("\\", "/").rsplit("/", 1)[-1]
            if (base in shells and any(a.startswith("-") and "c" in a for a in rest)
                    and not _cp_platform_guarded(node, parents)):
                found.append((node.lineno, f"launches through the POSIX shell {base!r} with "
                                           f"-c; name a real executable instead"))
            if base in interps:
                found.append((node.lineno, f"invokes a bare {base!r} interpreter; use "
                                           f"sys.executable, which names the running Python"))
            if base in utils:
                found.append((node.lineno, f"invokes the POSIX-only utility {base!r}, "
                                           f"absent from PowerShell"))
            if suffixes and head.endswith(suffixes):
                found.append((node.lineno, f"runs {head!r} as the command itself, relying on "
                                           f"its shebang and executable bit — Windows honours "
                                           f"neither"))
    return sorted(set(found))


def _check_command_portability(errors: list[str], policy: dict | None = None,
                               manifests=None, py_files=None) -> None:
    """Reject POSIX-only command assumptions in karta-owned hook manifests and in the
    subprocess invocations inside karta-owned Python.

    `policy`, `manifests` and `py_files` exist so the fixtures can drive the rule over
    fabricated input; left alone, all three come from the committed policy and git."""
    if policy is None:
        policy = _load_json(POLICY_PATH, errors)
        if not policy:
            return
    if manifests is None or py_files is None:
        tracked = _cp_tracked(errors)
        if tracked is None:
            return
        auto_m, auto_p = [], []
        for rel in tracked:
            if _excluded(rel, policy):
                continue
            path = ROOT / rel
            if rel.endswith(".py"):
                auto_p.append((path, rel))
            elif rel.endswith(".json") and path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue              # not this rule's business; other checks parse it
                except UnicodeDecodeError as e:
                    errors.append(f"{rel}: tracked JSON that is not decodable as UTF-8 ({e})")
                    continue
                if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
                    auto_m.append((path, rel))
        manifests = auto_m if manifests is None else manifests
        py_files = auto_p if py_files is None else py_files
    for path, rel in manifests:
        _cp_check_manifest(Path(path), rel, policy, errors)
    for path, rel in py_files:
        try:
            source = Path(path).read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            errors.append(f"{rel}: unreadable Python source ({e})")
            continue
        try:
            findings = _cp_python_findings(source, policy)
        except SyntaxError as e:
            errors.append(f"{rel}: unparseable Python ({e})")
            continue
        for line, why in findings:
            errors.append(f"{rel}:{line}: subprocess call is not portable — {why}")


def check() -> list[str]:
    errors: list[str] = []
    skill_dirs = [p.parent for p in SKILLS.glob("*/SKILL.md")]
    if not skill_dirs:
        errors.append("no skills found under skills/*/SKILL.md")
    for sd in sorted(skill_dirs):
        text = (sd / "SKILL.md").read_text(encoding="utf-8")
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
        fm = _frontmatter(agent.read_text(encoding="utf-8"))
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
            data = json.loads(mp.read_text(encoding="utf-8"))
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
    _check_pi(errors)
    _check_hooks(errors)
    _check_skill_scripts(errors)
    _check_behaviour_anchor(errors)
    _check_vendored_fonts(errors)
    _check_design_reference(errors)
    _check_fact_traces(errors)
    _check_encoding(errors)
    _check_git_output_encoding(errors)
    _check_codex_hook_windows(errors)
    _check_command_portability(errors)
    _check_line_endings(errors)
    return errors


def _load_json(path: Path, errors: list[str]) -> dict:
    if not path.exists():
        errors.append(f"{path.relative_to(ROOT)}: missing")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"{path.relative_to(ROOT)}: invalid JSON ({e})")
        return {}


def _check_pi(errors: list[str]) -> None:
    """Guard the Pi package contract and its version parity."""
    package = _load_json(ROOT / "package.json", errors)
    claude = _load_json(ROOT / ".claude-plugin" / "plugin.json", errors)
    lock = _load_json(ROOT / "package-lock.json", errors)
    if not package:
        return

    package_name = package.get("name")
    if not isinstance(package_name, str) or package_name.rsplit("/", 1)[-1] != "karta":
        errors.append("package.json: name must identify the karta package")
    for field in ("version", "license"):
        if claude and package.get(field) != claude.get(field):
            errors.append(
                f"package.json: '{field}' ({package.get(field)!r}) != "
                f".claude-plugin/plugin.json ({claude.get(field)!r})")
    if package.get("private") is not True:
        errors.append("package.json: private must stay true until publication is approved")
    if package.get("type") != "module":
        errors.append("package.json: type must be 'module'")
    if "pi-package" not in package.get("keywords", []):
        errors.append("package.json: keywords must include 'pi-package'")
    if not str(package.get("packageManager", "")).startswith("npm@"):
        errors.append("package.json: packageManager must pin npm")
    expected_files = [
        "extensions/pi/", "skills/", "agents/", "hooks/scripts/",
        "!**/__pycache__/", "!**/*.pyc",
    ]
    if package.get("files") != expected_files:
        errors.append(f"package.json: files must be {expected_files!r}")

    pi = package.get("pi")
    expected_extensions = ["./extensions/pi/index.ts"]
    if not isinstance(pi, dict):
        errors.append("package.json: missing pi manifest")
    else:
        if pi.get("extensions") != expected_extensions:
            errors.append(
                f"package.json: pi.extensions must be {expected_extensions!r}")
        if "skills" in pi:
            errors.append(
                "package.json: pi.skills must stay absent; the extension trust-gates skill discovery")
    for extension in expected_extensions:
        if not (ROOT / extension).is_file():
            errors.append(f"package.json: Pi extension '{extension}' is missing")

    consumer_script = re.compile(
        r"(?:python3|uv run(?: --script)?)\s+skills/karta-[a-z-]+/scripts/")
    for skill in sorted((ROOT / "skills").glob("karta-*/SKILL.md")):
        if consumer_script.search(skill.read_text(encoding="utf-8")):
            errors.append(
                f"{skill.relative_to(ROOT)}: bundled script command resolves from the consumer cwd")

    peers = package.get("peerDependencies", {})
    for dependency in ("@earendil-works/pi-coding-agent", "typebox"):
        if peers.get(dependency) != "*":
            errors.append(f"package.json: peerDependencies.{dependency} must be '*'")
    tested_pi = package.get("devDependencies", {}).get("@earendil-works/pi-coding-agent")
    if not isinstance(tested_pi, str) or not re.fullmatch(r"\d+\.\d+\.\d+", tested_pi):
        errors.append(
            "package.json: devDependencies.@earendil-works/pi-coding-agent must pin one tested version")
    scripts = package.get("scripts", {})
    expected_smoke = "node scripts/smoke_pi_package.mjs"
    if scripts.get("smoke:pi-package") != expected_smoke:
        errors.append(
            f"package.json: scripts.smoke:pi-package must be {expected_smoke!r}")
    if not (ROOT / "scripts" / "smoke_pi_package.mjs").is_file():
        errors.append("scripts/smoke_pi_package.mjs: packed-artifact smoke gate is missing")
    lifecycle = {"preinstall", "install", "postinstall", "prepare"}
    present_lifecycle = sorted(lifecycle & set(scripts))
    if present_lifecycle:
        errors.append(
            f"package.json: install lifecycle scripts are forbidden ({', '.join(present_lifecycle)})")

    if lock:
        for field in ("name", "version"):
            if lock.get(field) != package.get(field):
                errors.append(
                    f"package-lock.json: '{field}' ({lock.get(field)!r}) != "
                    f"package.json ({package.get(field)!r})")
        locked_root = lock.get("packages", {}).get("", {})
        if locked_root.get("peerDependencies") != peers:
            errors.append("package-lock.json: root peerDependencies differ from package.json")
        if locked_root.get("devDependencies") != package.get("devDependencies"):
            errors.append("package-lock.json: root devDependencies differ from package.json")


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
    for p, (content, exec_bits) in sorted(want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from .agents/skills mirror (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical skill (run sync_codex_skills.py)")
        elif (p.stat().st_mode & 0o111) != exec_bits:
            errors.append(f"{p.relative_to(ROOT)}: executable bit differs from canonical skill (run sync_codex_skills.py)")
    for p in sorted(set(sync_codex_skills.mirror_files()) - set(want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in mirror (no canonical source)")
    for name in sorted(sync_codex_skills.mirror_skill_names() - names):
        errors.append(f".agents/skills/{name}: orphaned (no skills/{name})")
    install_want = sync_codex_skills.expected_install_projection()
    install_have = set(sync_codex_skills.install_projection_files())
    for p, (content, exec_bits) in sorted(install_want.items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing from Codex install projection (run sync_codex_skills.py)")
        elif p.read_bytes() != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from canonical Codex install projection (run sync_codex_skills.py)")
        elif (p.stat().st_mode & 0o111) != exec_bits:
            errors.append(f"{p.relative_to(ROOT)}: executable bit differs from canonical Codex install projection (run sync_codex_skills.py)")
    for p in sorted(install_have - set(install_want)):
        errors.append(f"{p.relative_to(ROOT)}: orphaned in Codex install projection (no canonical source)")
    for name in sorted(sync_codex_skills.install_projection_skill_names() - names):
        errors.append(f"plugins/karta/skills/{name}: orphaned (no skills/{name})")

    # 3b. External skill hash liveness — recompute each external skill's SKILL.md
    # content hash (computedHash = sha256 of the bytes as synced locally) against
    # skills-lock.json; a mismatch, degraded entry, or missing lock entry fails,
    # naming the skill. Shared with sync_codex_skills --check (one truth).
    errors.extend(sync_codex_skills.external_integrity_problems())

    # 4. Codex agent projections — TOML + bundled instructions match agents/*.md.
    for p, content in sorted(sync_codex_agents.projections().items()):
        if not p.exists():
            errors.append(f"{p.relative_to(ROOT)}: missing (run sync_codex_agents.py)")
        elif p.read_text(encoding="utf-8") != content:
            errors.append(f"{p.relative_to(ROOT)}: differs from agents/*.md (run sync_codex_agents.py)")
    for toml_path in sorted((ROOT / ".codex" / "agents").glob("*.toml")):
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            errors.append(f".codex/agents/{toml_path.name}: invalid TOML ({e})")
            continue
        agent_md = ROOT / "agents" / f"{toml_path.stem}.md"
        if agent_md.exists():
            expected = sync_codex_agents.sandbox_mode_for(_frontmatter(agent_md.read_text(encoding="utf-8")))
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
        elif "display_name:" not in yml.read_text(encoding="utf-8"):
            errors.append(f"{name}: agents/openai.yaml missing interface.display_name")

    # 6. doc-gardner opt-in config — if a repo commits one, it must match the shape
    # the shipped schema promises (docs/specs/2026-06-18-doc-gardner-design.md §5).
    _check_doc_gardner(errors)

    # 7. kaizen opt-in config — if a repo commits one, it must be well-formed.
    # KARTA-SME-OVERRIDE(min.4): mirrors the proven doc-gardner block above
    # pattern-for-pattern, and this repo ships no test framework by design (manual gate
    # scripts only) [ceiling: a third opt-in config copy; upgrade: factor the copies
    # into one shared, checked helper]
    kz = ROOT / ".karta" / "kaizen.json"
    if kz.exists():
        try:
            cfg = json.loads(kz.read_text(encoding="utf-8"))
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
            cfg = json.loads(rt.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            errors.append(f".karta/roundtable.json: invalid JSON ({e})")
            cfg = None
        if isinstance(cfg, dict):
            if not isinstance(cfg.get("enabled"), bool):
                errors.append(".karta/roundtable.json: 'enabled' must be a boolean")
            # KARTA-SME-OVERRIDE(house.4): this validator has no fixture harness for the
            # opt-in config blocks (see the min.4 override above); the negative case for
            # a non-boolean ledger lives in roundtable_gate.py --self-test ("a non-boolean
            # ledger key is a denial") and the ledger-gate oracle's schema probe
            # [ceiling: a fourth typed key in this block; upgrade: the schema-driven helper]
            if "ledger" in cfg and not isinstance(cfg.get("ledger"), bool):
                errors.append(".karta/roundtable.json: 'ledger' must be a boolean")
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
                if key not in ("enabled", "ledger", "tool", "providers", "min_providers", "focus", "points"):
                    errors.append(
                        f".karta/roundtable.json: unknown key '{key}' "
                        "(allowed: enabled, ledger, tool, providers, min_providers, focus, points)")

    # 9. design-pins.json opt-in config — if this repo commits one, it must be a
    # flat map from a repo-relative design path to a well-formed pin record. This
    # only gates the committed file's SHAPE at commit time, the way the three
    # blocks above gate theirs; freshness (bytes-vs-hash, recapture_after) is the
    # runtime job of check_design_pins.py, self-tested by the skill-scripts pass
    # above.
    # KARTA-SME-OVERRIDE(min.4): mirrors the proven doc-gardner/kaizen/roundtable
    # blocks above pattern-for-pattern, and this repo ships no test framework by
    # design (manual gate scripts only) [ceiling: a fifth divergent opt-in config
    # copy; upgrade: factor the shared enabled/unknown-key checks into one
    # schema-driven helper]
    _check_design_pins(errors)


# --- the Karta Watch coverage floor ----------------------------------------
# serve_status.py is the file every item of a watch binder edits, so a check and
# the expectation it guards can be deleted together in one edit with nothing to
# notice. The anchor below lives beside it but is NOT it, and no restyle item
# touches the anchor — so the deletion still fails here.

WATCH_SCRIPT = SKILLS / "karta-status" / "scripts" / "serve_status.py"
BEHAVIOUR_ANCHOR = SKILLS / "karta-status" / "scripts" / "selftest_behaviours.txt"
KW_PREFIX = "data-kw-"


def _anchored_behaviours(anchor: Path) -> list[str]:
    """One behaviour name per line; blank lines and # comments ignored."""
    return [ln.strip() for ln in anchor.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _registered_behaviours(script: Path) -> tuple[dict, str | None]:
    """The live coverage registry, read from the page's own script. (registry, error)."""
    try:
        proc = subprocess.run([sys.executable, str(script), "--list-behaviours"],
                              capture_output=True, text=True, timeout=120, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, f"could not read the coverage registry ({e})"
    if proc.returncode != 0:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-2:])
        return {}, f"--list-behaviours failed ({tail})"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {}, f"--list-behaviours emitted invalid JSON ({e})"
    if not isinstance(data, dict):
        return {}, "--list-behaviours must emit a JSON object of behaviour -> entry"
    return data, None


def _check_behaviour_anchor(errors: list[str], anchor: Path | None = None,
                            registry: dict | None = None,
                            script: Path | None = None) -> None:
    """Compare the committed anchor against the live coverage registry as a FLOOR:
    every anchored behaviour must still be registered; extra registrations pass, so
    a later item can add its own. Equality would be self-defeating — each restyle
    item introduces new behaviours and would fail against a frozen anchor.

    An absent or empty anchor is itself a failure: a floor compared against nothing
    passes vacuously, which is the exact hole the anchor exists to close."""
    anchor = anchor or BEHAVIOUR_ANCHOR
    try:
        label = str(anchor.relative_to(ROOT))
    except ValueError:
        label = anchor.name
    if not anchor.is_file():
        errors.append(f"{label}: missing — the Karta Watch coverage floor would "
                      "have nothing to compare against and would pass vacuously")
        return
    anchored = _anchored_behaviours(anchor)
    if not anchored:
        errors.append(f"{label}: empty — the coverage floor would pass vacuously; "
                      "it must name every behaviour the page's self-test must keep")
        return
    if registry is None:
        registry, failure = _registered_behaviours(script or WATCH_SCRIPT)
        if failure:
            errors.append(f"{label}: {failure}")
            return
    for name in anchored:
        if name not in registry:
            errors.append(f"{label}: anchors '{name}', which serve_status.py's "
                          "coverage registry no longer has — a behaviour lost its "
                          "check (add it back, or drop the anchor line deliberately)")
    # Entry shape: the kind rule, enforced from outside the file that declares it.
    for name, entry in sorted(registry.items()):
        entry = entry if isinstance(entry, dict) else {}
        kind = entry.get("kind")
        if kind == "rendered":
            if not str(entry.get("hook") or "").startswith(KW_PREFIX):
                errors.append(f"{label}: registry entry '{name}' is rendered but "
                              f"names no {KW_PREFIX}* hook")
        elif kind == "behaviour":
            if not entry.get("check"):
                errors.append(f"{label}: registry entry '{name}' is a behaviour but "
                              "names no check that exercises it")
        else:
            errors.append(f"{label}: registry entry '{name}' declares no kind "
                          "(expected rendered or behaviour)")


# --- the vendored typefaces -------------------------------------------------
# The page's three families are BINARY files inside the shipped plugin, and the
# two Codex mirrors are generated copies. Byte drift in a font is invisible in a
# diff and would ship a different face to Codex users than to Claude Code users,
# so the mirrors are compared byte for byte here rather than trusted. The same
# comparison covers serve_status.py, which every item of a watch binder edits.
#
# All three families are Open Font Licence: redistributing a face without its
# licence file is a licensing defect, not an untidiness, so an absent licence is
# an error and not a warning.

MIRROR_SKILL_ROOTS = (Path(".agents") / "skills",
                      Path("plugins") / "karta" / "skills")
WATCH_FONTS_REL = Path("karta-status") / "assets" / "fonts"
WATCH_SCRIPT_REL = Path("karta-status") / "scripts" / "serve_status.py"


def _mirror_drift(canonical: Path, twins: list[Path]) -> list[str]:
    """Byte-level drift of one canonical file against each generated mirror copy."""
    data = canonical.read_bytes()
    out = []
    for twin in twins:
        if not twin.is_file():
            out.append(f"{canonical.name} is missing from {twin.parent}")
        elif twin.read_bytes() != data:
            out.append(f"{canonical.name} differs from canonical in {twin.parent}")
    return out


def _check_vendored_fonts(errors: list[str], root: Path | None = None) -> None:
    """Every vendored font and licence, plus the page script itself, byte-identical
    in the canonical tree and both Codex mirrors — and one licence per family."""
    root = root or ROOT
    label = "skills/karta-status/assets/fonts"
    fonts = root / "skills" / WATCH_FONTS_REL
    mirror_fonts = [root / m / WATCH_FONTS_REL for m in MIRROR_SKILL_ROOTS]
    if not fonts.is_dir():
        errors.append(f"{label}: missing — the page declares vendored faces "
                      "with no files to serve")
        return
    manifest_path = fonts / "manifest.json"
    if not manifest_path.is_file():
        errors.append(f"{label}: manifest.json missing — the vendored faces have "
                      "no declared record to check the files against")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as e:
        errors.append(f"{label}/manifest.json: invalid JSON ({e})")
        return
    families = manifest.get("families") or {}
    if not families:
        errors.append(f"{label}/manifest.json: names no families, so no licence "
                      "can be required of it")
    for family, entry in sorted(families.items()):
        licence = (entry or {}).get("licence") or ""
        if not licence or not (fonts / licence).is_file():
            errors.append(f"{label}: family '{family}' ships no licence file "
                          f"('{licence or 'none declared'}') — an OFL face "
                          "redistributed without its licence")
    _check_font_provenance(errors, label, manifest)
    for f in sorted(p for p in fonts.iterdir() if p.is_file()):
        for problem in _mirror_drift(f, [m / f.name for m in mirror_fonts]):
            errors.append(f"{label}: {problem}")
    script = root / "skills" / WATCH_SCRIPT_REL
    if script.is_file():
        for problem in _mirror_drift(script, [root / m / WATCH_SCRIPT_REL
                                              for m in MIRROR_SKILL_ROOTS]):
            errors.append(f"skills/{WATCH_SCRIPT_REL.as_posix()}: {problem}")


# The manifest's provenance block is what a re-vendor is read back out of: the
# upstream commit, the source file and its digest, and the fontTools version the
# cut was made with. None of it is VERIFIED — no check here can confirm the bytes
# on disk came from that recipe rather than from somewhere else that renders the
# same, and the manifest says so in its own words. What is checked is the part a
# reader can be misled by: that the block is COMPLETE, and that it does not
# contradict itself. An incomplete provenance record reads as a stronger claim
# than it is, and a self-contradicting one reads as a checked claim.
#
# The version is required because the recipe is not byte-reproducible: the same
# flags on two fontTools versions give different byte counts, so "which version"
# is the difference between a record someone can act on and a record that only
# looks precise.

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _check_font_provenance(errors: list[str], label: str, manifest: dict) -> None:
    """The manifest's provenance record is complete and internally consistent.

    Complete: a fontTools version, an upstream commit, and a source file plus
    digest per face. Consistent: the commit appears in every family's source
    URL, two faces cut from the same source file record the same digest, and a
    family with a VARIABLE face carries a non-empty `axes` map and pins none.
    That last rule is this repository's policy for the faces it vendors, NOT a
    property of variable fonts — a variable font may pin one axis and keep
    another, and stays variable. Nothing here compares a pinned key against a
    declared one, so the rule is deliberately blunt rather than precise.
    That is a DECLARATION being present, never a declaration being true: no
    axis tag or range here is compared against the file, because that needs
    Brotli. Read it as "the record was filled in", not "the record is right".
    For the cut this repository ships, a leftover pin would be describing the
    flattened face this one replaced.

    A face's source_file is checked for presence and used to group the digests,
    and deliberately not matched against its family's source_files: that field
    is written for a reader ("IBMPlexMono-{Regular,Medium,SemiBold}.ttf"), and
    teaching this check to expand a brace list would be a parser bought to
    satisfy a rule nobody asked for.

    Say where the digest rule stops, because it is narrower than it looks. It
    catches a digest that DISAGREES with another face cut from the same file, so
    it has teeth only for a family shipping two or more faces from one source —
    IBM Plex Sans, today. A family shipping ONE face from its source, which is
    what the serif became, has nothing to disagree with: swap that digest for a
    different well-formed one and this passes. Closing it would mean recording
    the same digest a second time in this same file so the two copies could be
    compared, and a value typed twice by the same hand catches a typo and
    nothing else — the cross-checks in this manifest work by reading the SAME
    fact out of independent records (the enumeration in code, the bytes on
    disk, the stylesheet), which a second hand-written copy is not. So this
    is disclosed rather than closed: the upstream digest is recorded, and
    whether it is the digest the bytes actually came from is not established
    here and is not established anywhere else in this repository either."""
    sub = manifest.get("subsetting") or {}
    version = str(sub.get("fonttools_version") or "").strip()
    if not version:
        errors.append(f"{label}/manifest.json: subsetting block records no "
                      "fonttools_version — the recipe is not byte-reproducible, "
                      "so the version is the only thing that makes the recorded "
                      "recipe replayable")
    commit = str(sub.get("upstream_commit") or "").strip()
    if not commit:
        errors.append(f"{label}/manifest.json: subsetting block records no "
                      "upstream_commit")
    families = manifest.get("families") or {}
    faces = manifest.get("faces") or []
    for family, entry in sorted(families.items()):
        entry = entry or {}
        if commit and commit not in str(entry.get("source_url") or ""):
            errors.append(f"{label}/manifest.json: family '{family}' records a "
                          f"source_url that does not name the pinned upstream "
                          f"commit {commit}")
        variable = [f for f in faces
                    if f.get("family") == family and f.get("variable")]
        if variable and entry.get("pinned_axes"):
            errors.append(f"{label}/manifest.json: family '{family}' ships a "
                          "variable face while still declaring pinned_axes "
                          f"({entry['pinned_axes']}) — this repository's rule "
                          "for its own vendored faces, not a fact about "
                          "variable fonts, which may legally pin one axis and "
                          "keep another. What is pinned here is checked "
                          "nowhere against what `axes` declares, so the rule "
                          "is the blunt one: a family shipping a variable face "
                          "declares no pins at all, and a leftover pin reads "
                          "as the flattened cut this replaced")
        if variable and not entry.get("axes"):
            errors.append(f"{label}/manifest.json: family '{family}' ships a "
                          "variable face but records no axes")
    digests: dict[str, str] = {}
    for face in faces:
        name = f"{face.get('family')} {face.get('weight')}"
        source = str(face.get("source_file") or "").strip()
        digest = str(face.get("source_sha256") or "").strip()
        if not source:
            errors.append(f"{label}/manifest.json: face '{name}' records no "
                          "source_file")
        if not _SHA256_RE.match(digest):
            errors.append(f"{label}/manifest.json: face '{name}' records no "
                          "well-formed source_sha256")
            continue
        # Only dedup on a real source. A face with no source_file already
        # errored above; keying it as "" makes two such faces look like they
        # disagree about a shared source file, which is a second error about
        # a relationship that does not exist.
        if source and digests.setdefault(source, digest) != digest:
            errors.append(f"{label}/manifest.json: face '{name}' records a "
                          f"source_sha256 for '{source}' that disagrees with "
                          "another face cut from the same file")


# --- Watch design reference: self-contained, no-network, and the serving rig
# that later visual checks in the watch-fidelity binder depend on. -----------
#
# docs/designs/karta-watch-1440x900-light.html is a frozen capture of a live
# Claude Design export, committed instead of the export itself so the
# comparison never needs the network and never drifts when a font host does.
# The rule below is the guard against it quietly regaining a remote
# dependency: every http(s) reference is an error, and every local asset
# reference must resolve to a real file relative to the design file itself.
#
# docs/designs/fixtures/watch-fidelity-state is a hand-written repo root the
# serving rig points serve_status.py at (--root) so the page it renders is
# fixed by one committed binder file rather than by whatever binder happens
# to be live in this repo. Its slug is deliberately fictitious — checked
# against this repo's real karta/*/* refs so it can never collide.

DESIGN_REFERENCE_REL = Path("docs") / "designs" / "karta-watch-1440x900-light.html"
DESIGN_FIXTURE_REL = Path("docs") / "designs" / "fixtures" / "watch-fidelity-state"

# The frozen reference points its @font-face rules at the very files the page
# serves, so it renders in whatever the page renders in and agrees with the page
# about typefaces BY CONSTRUCTION — including when both are wrong. Giving it a
# pinned second copy of the fonts would only move the question, so the limit is
# not fixed here; it is DECLARED, in the file's own header, where the next
# person to run a font comparison against it will read it. Declared and then
# enforced, because a caveat only a reviewer maintains is the same reassurance
# the missing caveat already was: the header must say the file cannot witness a
# font difference, and must point at where the check that can is briefed.
DESIGN_FONT_CAVEAT_PHRASE = "cannot witness a font difference"
DESIGN_FONT_CAVEAT_POINTER = "docs/backlog/watch-optical-harness/FINDINGS.md"

# Absolute http(s) URLs, and the protocol-relative `//host/path` form that is
# just as much an external fetch while carrying no scheme to grep for. The
# leading (?<![:/\w]) keeps it off the `//` inside `https://…` (already matched
# by the first branch) and off a bare `path//x`.
_EXTERNAL_URL_RE = re.compile(r"https?://[^\s\"'()]+|(?<![:/\w])//[A-Za-z0-9-]+\.[^\s\"'()]+")
_ASSET_REF_RE = re.compile(r'(?:src|href)="([^"]+)"|url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')
_HEADER_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _design_reference_asset_paths(text: str) -> list[str]:
    """Every local asset a design capture points at (src=, href=, css url()) —
    skipping data: URIs and same-page #fragments, which resolve to nothing on
    disk by design."""
    paths: list[str] = []
    for m in _ASSET_REF_RE.finditer(text):
        ref = m.group(1) or m.group(2)
        if not ref or ref.startswith(("data:", "#")):
            continue
        paths.append(ref)
    return paths


def _check_design_self_contained(errors: list[str], design_file: Path) -> None:
    """The committed design capture opens with no network: no external host
    referenced anywhere, every local asset it points at resolves to a real
    file relative to the design file itself, and its header comment records
    the origin design, the capture date, the viewport and the theme."""
    if not design_file.is_file():
        errors.append(f"{design_file}: missing — the watch-fidelity binder's design "
                      "reference, and every visual check built on it, has nothing to "
                      "compare against")
        return
    text = design_file.read_text(encoding="utf-8")
    hosts = sorted(set(_EXTERNAL_URL_RE.findall(text)))
    if hosts:
        errors.append(f"{design_file}: references an external host ({', '.join(hosts[:3])}) "
                      "— the committed design reference must open with no network")
    for ref in _design_reference_asset_paths(text):
        if ref.startswith(("http://", "https://", "//")):
            continue  # already reported above as an external-host reference
        target = (design_file.parent / ref).resolve()
        if not target.is_file():
            errors.append(f"{design_file}: points at asset '{ref}' which does not "
                          "resolve to a file in this repo")
    header_m = _HEADER_COMMENT_RE.search(text)
    header = header_m.group(1) if header_m else ""
    if "1440" not in header or "900" not in header:
        errors.append(f"{design_file}: header comment must record the 1440x900 viewport")
    if "light" not in header.lower():
        errors.append(f"{design_file}: header comment must record the light theme")
    if not re.search(r"\b(19|20)\d{2}-\d{2}-\d{2}\b", header):
        errors.append(f"{design_file}: header comment must record the capture date")
    if "origin" not in header.lower() and "claude-design://" not in header:
        errors.append(f"{design_file}: header comment must record the origin design")
    if DESIGN_FONT_CAVEAT_PHRASE not in header:
        errors.append(f"{design_file}: header comment must record that this file "
                      f"'{DESIGN_FONT_CAVEAT_PHRASE}' — it points at the page's own "
                      "vendored faces, so it agrees with the page about typefaces "
                      "whether or not either one is right, and a fidelity reference "
                      "read as complete is how a flattened serif goes unnoticed")
    elif DESIGN_FONT_CAVEAT_POINTER not in header:
        errors.append(f"{design_file}: the font caveat in the header comment must "
                      f"point at {DESIGN_FONT_CAVEAT_POINTER}, where the check that "
                      "CAN witness a font difference is briefed — a caveat naming "
                      "nowhere to go reads as reassurance")


def _check_design_fixture(errors: list[str], fixture_root: Path,
                          ref_prober=None) -> str | None:
    """The committed fixture is a repo root holding exactly one hand-written
    .karta/binders/<slug>.json, whose slug matches no karta/<slug>/* ref
    anywhere in this repo — so the wave shape the page renders when rooted
    there is fixed by that one committed file, readable in the diff, with
    nothing needing to be served to settle it. Returns the fixture's slug on
    success, None on any check failure. `ref_prober(slug) -> bool` (True if a
    matching ref exists) is the self-test injection seam — default probes
    this repo's real refs with `git for-each-ref`."""
    binders_dir = fixture_root / ".karta" / "binders"
    if not binders_dir.is_dir():
        errors.append(f"{fixture_root}: missing .karta/binders — not a fixture repo root")
        return None
    binder_files = sorted(binders_dir.glob("*.json"))
    if len(binder_files) != 1:
        errors.append(f"{fixture_root}/.karta/binders: must hold exactly one binder "
                      f"json (found {len(binder_files)})")
        return None
    try:
        binder = json.loads(binder_files[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{binder_files[0]}: invalid JSON ({e})")
        return None
    slug = binder.get("slug")
    if not slug or not isinstance(slug, str):
        errors.append(f"{binder_files[0]}: binder has no 'slug'")
        return None
    if ref_prober is None:
        def ref_prober(s: str) -> bool:
            proc = subprocess.run(["git", "for-each-ref", f"refs/karta/{s}/"],
                                  cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
            return bool(proc.stdout.strip())
    if ref_prober(slug):
        errors.append(f"{binder_files[0]}: slug '{slug}' matches a real karta/{slug}/* "
                      "ref in this repo — the fixture must use a slug that derives as "
                      "pending forever, never a real binder's slug")
        return None
    return slug


def _check_design_serving_rig(errors: list[str], fixture_root: Path, script: Path,
                              slug: str, *, timeout: float = 10.0) -> None:
    """Prove the rig this whole binder depends on, at item one rather than the
    end of the binder: start the committed page as a subprocess rooted at the
    committed fixture on a loopback port, request it with ?theme=light, and
    confirm HTTP 200 with the fixture's one binder actually rendered in the
    body. A wrong --root, a malformed fixture, or a page that stops serving
    then fails here instead of at the last item that needs it."""
    import socket
    import time
    import urllib.error
    import urllib.request

    if not script.is_file():
        errors.append(f"{script}: missing — cannot prove the watch-fidelity serving rig")
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, str(script), "--root", str(fixture_root), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        url = f"http://127.0.0.1:{port}/?theme=light"
        body = None
        last_err: Exception | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                last_err = RuntimeError(
                    f"exited early ({proc.returncode}): {proc.stderr.read() if proc.stderr else ''}")
                break
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    if resp.status == 200:
                        body = resp.read().decode("utf-8", "replace")
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last_err = e
                time.sleep(0.2)
        if body is None:
            errors.append(f"watch-fidelity serving rig: the page never answered 200 "
                          f"at --root {fixture_root} --port {port} ({last_err})")
            return
        if slug not in body:
            errors.append("watch-fidelity serving rig: the page answered but the "
                          f"fixture's binder ('{slug}') is not visibly rendered in the response")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


# --- Watch design reference: the binder panel's ground, read from the design ---
#
# card-ground-and-tint-base puts the binder card on the surface role and the
# frame around it on the page-ground role. serve_status.py's own self-test
# proves WHICH role each container resolves to, but both of its sides descend
# from the page's _PALETTE, so it can only prove the page agrees with itself.
# The design side — the role the design's binder panel declares, and the value
# the design file itself declares for that role in each palette — is read HERE,
# from the file this validator already resolves by DESIGN_REFERENCE_REL, and
# held against what the shipped page resolves. It lives here and not in
# serve_status.py because that script's self-test is contracted to need no repo
# and ships to consumer installs that carry no docs/ at all.

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_ROOT_RULE_RE = re.compile(r"(:root[^{}]*)\{([^{}]*)\}")
_BODY_RULE_RE = re.compile(r"(?:^|[\s}])body\s*\{([^{}]*)\}")
_TOKEN_DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+)")
_ONE_TOKEN_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^<>]*)>")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
              "meta", "param", "source", "track", "wbr"}


def _decls(style: str) -> dict[str, str]:
    """`prop:value;…` as {prop: value} — an inline style or a rule body."""
    out: dict[str, str] = {}
    for decl in style.split(";"):
        prop, sep, value = decl.partition(":")
        if sep:
            out[prop.strip()] = value.strip()
    return out


def _inline_style(tag: str) -> dict[str, str]:
    m = re.search(r'style="([^"]*)"', tag)
    return _decls(m.group(1)) if m else {}


def _one_token(value: str) -> str | None:
    """The single palette token a declared value names, or None."""
    m = _ONE_TOKEN_RE.fullmatch((value or "").strip())
    return m.group(1) if m else None


def _design_palettes(text: str) -> dict[str, dict[str, str]]:
    """The design's palettes as {theme: {token: value}}, read off its :root
    rules: the light one is the bare :root or the rule naming
    data-theme="light"; the dark one names data-theme="dark"."""
    out: dict[str, dict[str, str]] = {}
    for prelude, body in _ROOT_RULE_RE.findall(text):
        selectors = [sel.strip() for sel in prelude.split(",")]
        themes = {"dark" if 'data-theme="dark"' in sel else "light"
                  for sel in selectors if sel == ":root" or "data-theme=" in sel}
        for theme in themes:
            out.setdefault(theme, {}).update(
                {k: v.strip() for k, v in _TOKEN_DECL_RE.findall(body)})
    return out


def _direct_children(text: str, start: int) -> list[str]:
    """The start tags one level inside the element whose start tag begins at
    `start` — its direct children, depth-counted so a nested box of the same
    name cannot close it early and a void or self-closing tag opens nothing."""
    first = _TAG_RE.match(text, start)
    if not first:
        return []
    out, depth = [], 0
    for m in _TAG_RE.finditer(text, first.end()):
        closing, name, rest = m.group(1), m.group(2).lower(), m.group(3)
        if closing:
            depth -= 1
            if depth < 0:
                break
            continue
        if depth == 0:
            out.append(m.group(0))
        if name not in _VOID_TAGS and not rest.rstrip().endswith("/"):
            depth += 1
    return out


def _check_design_panel_ground(errors: list[str], design_file: Path, *,
                               page_card_roles: set[str], page_frame_roles: set[str],
                               page_palette: dict[str, dict[str, str]]) -> None:
    """The binder card resolves to the role the design gives its binder panel,
    and the frame around it to the role the design paints its page in — and
    the value the page resolves each role to, in both palettes, equals the value
    the design file itself declares. The design side is read from the file, so
    this cannot pass by agreeing with itself the way a check whose two sides
    both descend from _PALETTE does.

    The design's binder panel is found structurally: inside every
    data-kw-panel section, the one direct child carrying a 1px line border and
    a ground (export 294). The section itself must declare only display,
    direction and gap — nothing between it and that panel carries a surface
    (export 282), which is the fact the page's frame-on-the-page-ground answers
    to. The page ground is the role the design's body rule paints."""
    label = design_file.as_posix()
    if not design_file.is_file():
        return  # already reported by _check_design_self_contained
    text = design_file.read_text(encoding="utf-8")
    palettes = _design_palettes(text)
    missing = [t for t in ("light", "dark") if t not in palettes]
    if missing:
        errors.append(f"{label}: declares no {' or '.join(missing)} palette (:root rule) "
                      "to read the binder panel's ground from")
        return
    style = "\n".join(_STYLE_BLOCK_RE.findall(text))
    body = _BODY_RULE_RE.search(style)
    ground_role = _one_token(_decls(body.group(1)).get("background", "")) if body else None
    if ground_role is None:
        errors.append(f"{label}: the body rule does not paint the page ground as one token")
        return
    panel_roles: set[str] = set()
    sections = [m.group(0) for m in _TAG_RE.finditer(text)
                if not m.group(1) and "data-kw-panel=" in m.group(3)]
    if not sections:
        errors.append(f"{label}: no data-kw-panel section to read the binder panel from")
        return
    for sec in sections:
        own = _inline_style(sec)
        if "background" in own or "background-color" in own:
            errors.append(f"{label}: a data-kw-panel section carries a surface of its own "
                          "(a frame) — the design puts nothing between the section and "
                          "the binder panel that carries one (export 282)")
        if set(own) - {"display", "flex-direction", "gap"}:
            errors.append(f"{label}: a data-kw-panel section declares more than display, "
                          f"direction and gap ({', '.join(sorted(set(own) - {'display', 'flex-direction', 'gap'}))})")
        panels = [c for c in _direct_children(text, text.index(sec))
                  if _inline_style(c).get("border", "").startswith("1px solid")
                  and "background" in _inline_style(c)]
        if len(panels) != 1:
            errors.append(f"{label}: a data-kw-panel section holds {len(panels)} direct "
                          "children with a 1px border and a ground; exactly one is the binder panel")
            continue
        role = _one_token(_inline_style(panels[0])["background"])
        if role is None:
            errors.append(f"{label}: the binder panel's ground is not one palette token")
            continue
        panel_roles.add(role)
    if len(panel_roles) != 1:
        errors.append(f"{label}: the binder panels resolve to {len(panel_roles)} ground roles; "
                      "expected one shared role")
        return
    card_role = panel_roles.pop()
    for role in (card_role, ground_role):
        for theme in ("light", "dark"):
            if role not in palettes[theme]:
                errors.append(f"{label}: the {theme} palette does not declare {role}")
            elif role not in page_palette:
                errors.append(f"watch panel ground: the page's palette does not declare {role}, "
                              f"which the design's {theme} palette does")
            elif palettes[theme][role].lower() != page_palette[role][theme].strip().lower():
                errors.append(f"watch panel ground: the design declares {role} as "
                              f"{palettes[theme][role]} in its {theme} palette; the page resolves "
                              f"it to {page_palette[role][theme]}")
    for theme in ("light", "dark"):
        if palettes[theme].get(card_role) == palettes[theme].get(ground_role):
            errors.append(f"{label}: {card_role} and {ground_role} resolve to the same value in "
                          f"the {theme} palette, so the binder panel cannot advance off the page")
    if page_card_roles != {card_role}:
        errors.append(f"watch panel ground: the design's binder panel resolves to {card_role}; "
                      f"the page's binder card resolves to {sorted(page_card_roles) or 'no role'}")
    if page_frame_roles != {ground_role}:
        errors.append(f"watch panel ground: the design paints its page in {ground_role}; the "
                      f"page's frame around the binder card resolves to "
                      f"{sorted(page_frame_roles) or 'no role'} — the frame the design never "
                      "modelled must sit on the page ground, not on a surface of its own")


def _watch_page_grounds() -> tuple[set[str], set[str], dict[str, dict[str, str]]]:
    """What the shipped page resolves: the binder card's and the delivery
    frame's ground roles — read by their data-kw hooks through the page's own
    stylesheet readers, never by class name — and the page's palette."""
    import importlib.util
    script = ROOT / "skills" / WATCH_SCRIPT_REL
    spec = importlib.util.spec_from_file_location("karta_watch_page", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # the page inserts its own directory on sys.path at import (to reach its
    # sibling engine); that is the page's business, not the validator's
    path_before = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = path_before
    css = mod._strip_css_comments(mod._page_css())

    def roles(hook: str) -> set[str]:
        tags = mod._tags_with(mod._APP_JS, hook)
        if len(tags) != 1:
            return set()
        return set(mod._VAR_REF_RE.findall(
            mod._resolved(mod._rules_for_tag(css, tags[0]), "background")))
    return roles("data-kw-binder"), roles("data-kw-delivery-panel"), mod._PALETTE


def _check_design_reference(errors: list[str]) -> None:
    """The watch-fidelity binder's design reference is self-contained and its
    fixture is well-formed, then — only once both hold — the serving rig
    itself is proven for real against the committed files. The three parts
    each take their target as an argument, so `_self_test()` drives every one
    of them against synthetic fixtures; this composes them over the real repo."""
    _check_design_self_contained(errors, ROOT / DESIGN_REFERENCE_REL)
    try:
        card_roles, frame_roles, palette = _watch_page_grounds()
    except Exception as e:  # a page that cannot be read is a reported failure, never a crash
        errors.append(f"skills/{WATCH_SCRIPT_REL.as_posix()}: could not read the page's "
                      f"grounds for the design comparison ({e})")
    else:
        _check_design_panel_ground(errors, ROOT / DESIGN_REFERENCE_REL,
                                   page_card_roles=card_roles, page_frame_roles=frame_roles,
                                   page_palette=palette)
    fixture_root = ROOT / DESIGN_FIXTURE_REL
    before = len(errors)
    slug = _check_design_fixture(errors, fixture_root)
    if slug is None or len(errors) > before:
        return  # fixture is malformed — nothing to serve, don't spin up the rig
    _check_design_serving_rig(errors, fixture_root, ROOT / "skills" / WATCH_SCRIPT_REL, slug)


DG_SCHEMA = ROOT / "skills" / "karta-doc-gardner" / "references" / "doc-gardner-schema.json"
_TYPE_BY_NAME = {"boolean": bool, "string": str}


def _check_doc_gardner(errors: list[str], config: Path | None = None,
                       schema: Path | None = None) -> None:
    """Gate a committed .karta/doc-gardner.json against the shipped schema
    skills/karta-doc-gardner/references/doc-gardner-schema.json — a hand-rolled
    stdlib check of the schema's semantics (required keys, per-key type,
    additionalProperties: false), no jsonschema dependency. An absent config is
    valid; a missing or unreadable schema is a reported integrity failure (the
    schema ships with the plugin), never a crash. Booleans are checked with
    `type(x) is bool` — bool subclasses int, so an isinstance-family check
    against int would let `"enabled": 1` through."""
    cfg_path = config if config is not None else ROOT / ".karta" / "doc-gardner.json"
    schema_path = schema if schema is not None else DG_SCHEMA
    if not cfg_path.exists():
        return  # opt-in: an absent config is valid
    label = ".karta/doc-gardner.json"
    try:
        sch = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(
            "skills/karta-doc-gardner/references/doc-gardner-schema.json: "
            f"missing or unreadable ({e}) — cannot gate {label}")
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{label}: invalid JSON ({e})")
        return
    if type(cfg) is not dict:
        errors.append(f"{label}: must be a JSON object")
        return
    props = sch.get("properties", {})
    for key in sch.get("required", []):
        if key not in cfg:
            errors.append(f"{label}: missing required key '{key}'")
    for key, val in cfg.items():
        if key not in props:  # additionalProperties: false
            errors.append(f"{label}: unknown key '{key}' (allowed: {', '.join(sorted(props))})")
            continue
        want = _TYPE_BY_NAME.get(props[key].get("type"))
        if want is not None and type(val) is not want:
            errors.append(f"{label}: '{key}' must be a {props[key].get('type')}")


_DESIGN_PIN_ENTRY_KEYS = {"sha256", "source", "captured_on", "recapture_triggers", "recapture_after"}


def _check_design_pins(errors: list[str], config: Path | None = None) -> None:
    """Gate a committed .karta/design-pins.json's SHAPE at commit time: a flat map
    from a repo-relative design path to a pin record carrying at least a string
    'sha256', with the rest of the record's keys (source, captured_on,
    recapture_triggers, recapture_after) optional but typed when present. An
    absent config is valid (opt-in). This checks shape only — the freshness rules
    (drift, recapture_after) live in skills/karta-validate/scripts/check_design_pins.py,
    run against this same file by its own --self-test in the skill-scripts pass."""
    cfg_path = config if config is not None else ROOT / ".karta" / "design-pins.json"
    label = ".karta/design-pins.json"
    if not cfg_path.exists():
        return  # opt-in: an absent config is valid
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        errors.append(f"{label}: invalid JSON ({e})")
        return
    if not isinstance(cfg, dict):
        errors.append(f"{label}: must be a JSON object (design path -> pin record)")
        return
    for path, entry in cfg.items():
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry for '{path}' must be an object")
            continue
        if not isinstance(entry.get("sha256"), str) or not entry.get("sha256"):
            errors.append(f"{label}: entry for '{path}' missing 'sha256'")
        for key in ("source", "captured_on", "recapture_after"):
            if key in entry and not isinstance(entry[key], str):
                errors.append(f"{label}: entry for '{path}' '{key}' must be a string")
        if "recapture_triggers" in entry:
            triggers = entry["recapture_triggers"]
            if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
                errors.append(f"{label}: entry for '{path}' 'recapture_triggers' must be "
                              "a list of strings")
        for key in entry:
            if key not in _DESIGN_PIN_ENTRY_KEYS:
                errors.append(f"{label}: entry for '{path}' unknown key '{key}' "
                              f"(allowed: {', '.join(sorted(_DESIGN_PIN_ENTRY_KEYS))})")


def _self_test() -> int:
    """Fixture-driven cases for the doc-gardner schema gate (run via --self-test)."""
    import tempfile
    real_schema = DG_SCHEMA.read_text(encoding="utf-8") if DG_SCHEMA.exists() else "{}"
    # (name, config text, schema text or None for a missing schema, expected error substrings)
    cases = [
        ("valid minimal config passes", '{"enabled": true}', real_schema, []),
        ("valid config with focus passes", '{"enabled": false, "focus": "api docs"}', real_schema, []),
        ("missing enabled fails", '{"focus": "x"}', real_schema, ["missing required key 'enabled'"]),
        ("enabled: 1 fails (bool, never int)", '{"enabled": 1}', real_schema, ["'enabled' must be a boolean"]),
        ('enabled: "true" fails', '{"enabled": "true"}', real_schema, ["'enabled' must be a boolean"]),
        ("non-string focus fails", '{"enabled": true, "focus": 3}', real_schema, ["'focus' must be a string"]),
        ("unknown key fails", '{"enabled": true, "scope": "docs"}', real_schema, ["unknown key 'scope'"]),
        ("invalid config JSON fails", '{"enabled": tru', real_schema, ["invalid JSON"]),
        ("non-object config fails", '[true]', real_schema, ["must be a JSON object"]),
        ("missing schema file is reported, not a crash", '{"enabled": true}', None, ["missing or unreadable"]),
        ("unreadable schema is reported, not a crash", '{"enabled": true}', "{not json", ["missing or unreadable"]),
    ]
    failures = 0
    total = 0  # incremented once per [PASS]/[FAIL] line printed below — never hand-summed,
    # so a case group (or a standalone check with no list of its own) added later cannot
    # silently under-report: it counts itself the moment it prints its own result line.
    with tempfile.TemporaryDirectory() as td:
        for i, (name, cfg_text, schema_text, want) in enumerate(cases):
            cfg = Path(td) / f"cfg{i}.json"
            cfg.write_text(cfg_text, encoding="utf-8")
            schema = Path(td) / f"schema{i}.json"
            if schema_text is not None:
                schema.write_text(schema_text, encoding="utf-8")
            errors: list[str] = []
            _check_doc_gardner(errors, config=cfg, schema=schema)
            ok = bool(errors) == bool(want) and all(any(w in e for e in errors) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errors!r}"))
            total += 1
            failures += 0 if ok else 1
        errors = []
        _check_doc_gardner(errors, config=Path(td) / "absent.json", schema=Path(td) / "schema0.json")
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] absent config stays valid" + ("" if ok else f" — got {errors!r}"))
        total += 1
        failures += 0 if ok else 1

        # design-pins.json opt-in config — shape gate only (freshness is
        # check_design_pins.py's own job, self-tested by the skill-scripts pass).
        dp_cases = [
            ("valid minimal entry passes",
             {"a.html": {"sha256": "a" * 64}}, []),
            ("valid entry with every optional key passes",
             {"a.html": {"sha256": "a" * 64, "source": "s", "captured_on": "2026-01-01",
                        "recapture_triggers": ["x"], "recapture_after": "2027-01-01"}}, []),
            ("entry missing sha256 fails",
             {"a.html": {"source": "s"}}, ["missing 'sha256'"]),
            ("non-string sha256 fails",
             {"a.html": {"sha256": 1}}, ["missing 'sha256'"]),
            ("non-object entry fails",
             {"a.html": "not-an-object"}, ["must be an object"]),
            ("non-string source fails",
             {"a.html": {"sha256": "a" * 64, "source": 1}}, ["'source' must be a string"]),
            ("recapture_triggers that is not a list of strings fails",
             {"a.html": {"sha256": "a" * 64, "recapture_triggers": "x"}},
             ["'recapture_triggers' must be a list of strings"]),
            ("unknown key fails",
             {"a.html": {"sha256": "a" * 64, "extra": 1}}, ["unknown key 'extra'"]),
            ("non-object config fails",
             ["not", "a", "map"], ["must be a JSON object"]),
        ]
        for i, (name, data, want) in enumerate(dp_cases):
            cfg = Path(td) / f"pins{i}.json"
            cfg.write_text(json.dumps(data), encoding="utf-8")
            errs: list[str] = []
            _check_design_pins(errs, config=cfg)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] design-pins: {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1
        errs = []
        _check_design_pins(errs, config=Path(td) / "absent-pins.json")
        ok = errs == []
        print(f"[{'PASS' if ok else 'FAIL'}] design-pins: absent config stays valid" + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1
        errs = []
        bad_json = Path(td) / "bad-pins.json"
        bad_json.write_text("{not json", encoding="utf-8")
        _check_design_pins(errs, config=bad_json)
        ok = bool(errs) and any("invalid JSON" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] design-pins: invalid JSON fails" + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # The Karta Watch coverage floor: the anchor is compared as a floor, and an
        # absent/empty anchor or a malformed registry entry is itself a failure.
        live = {"a-behaviour": {"kind": "rendered", "hook": "data-kw-a", "check": "_c_a"},
                "another": {"kind": "behaviour", "hook": None, "check": "_c_b"}}
        anc = Path(td) / "anchor.txt"
        anchor_cases = [
            ("anchor floor: every anchored behaviour registered -> no error",
             "# a comment\n\na-behaviour\nanother\n", live, []),
            ("anchor floor: an extra registration the anchor omits still passes",
             "a-behaviour\n", live, []),
            ("anchor floor: an anchored behaviour missing from the registry fails",
             "a-behaviour\ngone-behaviour\n", live, ["anchors 'gone-behaviour'"]),
            ("anchor floor: an emptied anchor fails (a floor over nothing is vacuous)",
             "# only comments left\n\n", live, ["empty"]),
            ("anchor floor: a rendered entry naming no data-kw hook fails",
             "a-behaviour\n", {"a-behaviour": {"kind": "rendered", "hook": "", "check": "_c_a"}},
             ["names no data-kw-* hook"]),
            ("anchor floor: a behaviour entry naming no check fails",
             "a-behaviour\n", {"a-behaviour": {"kind": "behaviour", "hook": None, "check": ""}},
             ["names no check"]),
            ("anchor floor: an entry with neither kind fails",
             "a-behaviour\n", {"a-behaviour": {"kind": None, "hook": None, "check": None}},
             ["declares no kind"]),
        ]
        for name, anchor_text, reg, want in anchor_cases:
            anc.write_text(anchor_text, encoding="utf-8")
            errs: list[str] = []
            _check_behaviour_anchor(errs, anchor=anc, registry=reg)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1
        errs = []
        _check_behaviour_anchor(errs, anchor=Path(td) / "absent-anchor.txt", registry=live)
        ok = bool(errs) and any("missing" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] anchor floor: a missing anchor fails"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # The fact-trace floor: the sweep is wired, it fails on an untraced fact, and it
        # reaches an archive/ subdirectory too — a LIST-shaped fact table there is swept
        # exactly like a live one (frozen history is no excuse for a broken trace).
        def _fact_binder(traced: bool) -> str:
            row = {"id": "a-fact", "claim": "x", "traced_by": ["it:0"] if traced else []}
            return json.dumps({"slug": "fx", "work_items": [{"id": "it", "oracle": {"assertions": ["a"]}}],
                               "token_manifest": {"design_fact_table": [row]}})
        bd = Path(td) / "binders"
        (bd / "archive").mkdir(parents=True)
        (bd / "ok.json").write_text(_fact_binder(True), encoding="utf-8")
        (bd / "archive" / "frozen.json").write_text(_fact_binder(False), encoding="utf-8")
        errs = []
        _check_fact_traces(errs, binders_dir=bd)
        ok = (len(errs) == 1 and "archive" in errs[0] and "frozen.json" in errs[0]
              and "fact 'a-fact' is untraced" in errs[0])
        print(f"[{'PASS' if ok else 'FAIL'}] fact traces: a traced live binder passes; an untraced archived binder fails too"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1
        (bd / "gap.json").write_text(_fact_binder(False), encoding="utf-8")
        errs = []
        _check_fact_traces(errs, binders_dir=bd)
        ok = (len(errs) == 2
              and any("gap.json" in e and "fact 'a-fact' is untraced" in e for e in errs)
              and any("archive" in e and "frozen.json" in e and "fact 'a-fact' is untraced" in e for e in errs))
        print(f"[{'PASS' if ok else 'FAIL'}] fact traces: an untraced fact in a live binder fails the floor"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # _run_self_test enforces "every gated script exposes --self-test": check all three
        # dispositions on fabricated scripts. Their paths are outside ROOT, exercising rel().
        rst = Path(td) / "rst"
        rst.mkdir()
        (rst / "good.py").write_text(
            "import argparse\n"
            "p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true')\n"
            "p.parse_args()\n", encoding="utf-8")
        (rst / "bad.py").write_text(
            "import argparse,sys\n"
            "p=argparse.ArgumentParser();p.add_argument('--self-test',action='store_true')\n"
            "a=p.parse_args()\n"
            "sys.exit(1 if a.self_test else 0)\n", encoding="utf-8")
        (rst / "missing.py").write_text(
            "import argparse\n"
            "argparse.ArgumentParser().parse_args()\n", encoding="utf-8")
        rst_cases = [
            ("_run_self_test: exposed & passing self-test -> no error", "good.py", False, None),
            ("_run_self_test: exposed & failing self-test -> named failure", "bad.py", True, "--self-test failed"),
            ("_run_self_test: absent --self-test -> distinct failure", "missing.py", True, "does not expose --self-test"),
        ]
        for name, fn, want_err, want_sub in rst_cases:
            errs: list[str] = []
            _run_self_test(rst / fn, errs)
            ok = (bool(errs) == want_err) and (want_sub is None or any(want_sub in e for e in errs))
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The vendored fonts: a synthetic repo shape with the canonical tree and
        # both Codex mirrors, then one deliberately broken copy per rule. Font
        # drift is invisible in a diff, so each rule is driven against a known-bad
        # tree rather than only against the (clean) real one.
        # The synthetic manifest is a COMPLETE one — provenance block, a
        # variable face and a static one cut from the same source — so each
        # negative control below can break exactly one rule by patching it.
        demo_commit = "0" * 40

        def _demo_manifest() -> dict:
            return {
                "subsetting": {"fonttools_version": "4.63.0",
                               "upstream_commit": demo_commit},
                "families": {"Demo": {
                    "licence": "demo-OFL.txt",
                    "source_url": f"https://example.invalid/{demo_commit}/demo",
                    "source_files": "Demo[wght].ttf",
                    "pinned_axes": None,
                    "axes": {"wght": "400..500"}}},
                "faces": [
                    {"family": "Demo", "weight": "400 500",
                     "file": "demo-400.woff2", "source_file": "Demo[wght].ttf",
                     "source_sha256": "a" * 64, "variable": True},
                    {"family": "Demo", "weight": 600, "file": "demo-600.woff2",
                     "source_file": "Demo[wght].ttf",
                     "source_sha256": "a" * 64}],
            }

        def _font_tree(base: Path, *, licence=True, mirror_bytes=b"WOFF2",
                       script_bytes=b"# page\n", manifest=True, patch=None):
            for rel in ("skills",) + tuple(m.as_posix() for m in MIRROR_SKILL_ROOTS):
                (base / rel / WATCH_FONTS_REL).mkdir(parents=True, exist_ok=True)
                (base / rel / WATCH_SCRIPT_REL).parent.mkdir(parents=True, exist_ok=True)
            canon = base / "skills" / WATCH_FONTS_REL
            (canon / "demo-400.woff2").write_bytes(b"WOFF2")
            if licence:
                (canon / "demo-OFL.txt").write_text("SIL OPEN FONT LICENSE", encoding="utf-8")
            if manifest:
                doc = _demo_manifest()
                if patch:
                    patch(doc)
                (canon / "manifest.json").write_text(json.dumps(doc), encoding="utf-8")
            (base / "skills" / WATCH_SCRIPT_REL).write_bytes(b"# page\n")
            for m in MIRROR_SKILL_ROOTS:
                mirror = base / m / WATCH_FONTS_REL
                (mirror / "demo-400.woff2").write_bytes(mirror_bytes)
                if licence:
                    (mirror / "demo-OFL.txt").write_text("SIL OPEN FONT LICENSE", encoding="utf-8")
                if manifest:
                    (mirror / "manifest.json").write_bytes(
                        (canon / "manifest.json").read_bytes())
                (base / m / WATCH_SCRIPT_REL).write_bytes(script_bytes)
            return base

        font_cases = [
            ("fonts: canonical tree mirrored byte for byte, licence present -> no error",
             {}, []),
            ("fonts: a font byte-differing in a mirror fails (drift a diff cannot show)",
             {"mirror_bytes": b"WOFF2-tampered"}, ["differs from canonical"]),
            ("fonts: a family whose licence file is absent fails",
             {"licence": False}, ["ships no licence file"]),
            ("fonts: serve_status.py differing from its mirror fails",
             {"script_bytes": b"# drifted\n"}, ["serve_status.py"]),
            ("fonts: an absent manifest fails (nothing to check the files against)",
             {"manifest": False}, ["manifest.json missing"]),
            # provenance: complete, and not contradicting itself. None of it is
            # verified against the upstream — these controls prove the record is
            # held to being whole and self-consistent, which is all it claims.
            ("fonts: a manifest with no fontTools version fails (the recipe is not "
             "byte-reproducible, so the version is the record)",
             {"patch": lambda d: d["subsetting"].pop("fonttools_version")},
             ["records no fonttools_version"]),
            ("fonts: an EMPTY fontTools version fails rather than passing as present",
             {"patch": lambda d: d["subsetting"].update(fonttools_version="  ")},
             ["records no fonttools_version"]),
            ("fonts: a manifest with no upstream commit fails",
             {"patch": lambda d: d["subsetting"].pop("upstream_commit")},
             ["records no upstream_commit"]),
            ("fonts: a family source_url that does not name the pinned commit fails",
             {"patch": lambda d: d["families"]["Demo"].update(
                 source_url="https://example.invalid/deadbeef/demo")},
             ["does not name the pinned upstream commit"]),
            ("fonts: a face with no well-formed source digest fails",
             {"patch": lambda d: d["faces"][0].update(source_sha256="nope")},
             ["no well-formed source_sha256"]),
            ("fonts: two faces cut from one source file recording different digests fails",
             {"patch": lambda d: d["faces"][1].update(source_sha256="b" * 64)},
             ["disagrees with another face"]),
            ("fonts: a variable face whose family still pins an axis fails — the pin "
             "describes the flattened cut it replaced",
             {"patch": lambda d: d["families"]["Demo"].update(
                 pinned_axes={"opsz": 18})},
             ["still declaring pinned_axes"]),
            ("fonts: a variable face whose family records no axes fails",
             {"patch": lambda d: d["families"]["Demo"].update(axes=None)},
             ["records no axes"]),
        ]
        for i, (name, kwargs, want) in enumerate(font_cases):
            errs = []
            _check_vendored_fonts(errs, root=_font_tree(Path(td) / f"fonts{i}", **kwargs))
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The watch design reference: a self-contained good capture, then one
        # deliberately violating negative control per rule — carrying an
        # external stylesheet, a dangling asset, a bare/incomplete header, or
        # a malformed fixture. Each is built fresh in this temp dir and
        # committed nowhere; the checks below are the static, fast half of
        # _check_design_reference (no subprocess) — the real serving-rig
        # proof runs only against the real repo, from `check()`.
        caveat_line = (f'  This file {DESIGN_FONT_CAVEAT_PHRASE}; see '
                       f'{DESIGN_FONT_CAVEAT_POINTER}.\n')
        good_header = ('<!--\n  Origin design : claude-design://demo/Demo.dc.html\n'
                       '  Captured      : 2026-08-17\n  Viewport      : 1440x900\n'
                       '  Theme         : light\n' + caveat_line + '-->\n')

        def _design_file(base: Path, *, header=good_header,
                         asset_tag='<img src="mascot.png">',
                         extra="", with_asset=True) -> Path:
            base.mkdir(parents=True, exist_ok=True)
            if with_asset:
                (base / "mascot.png").write_bytes(b"PNG")
            f = base / "design.html"
            f.write_text(f"<!DOCTYPE html>\n<html>\n<head>\n{header}"
                         f"<style>body{{color:red}}</style>\n</head>\n"
                         f"<body>\n{asset_tag}\n{extra}\n</body>\n</html>\n", encoding="utf-8")
            return f

        design_cases = [
            ("design: self-contained capture with a full header -> no error",
             lambda b: _design_file(b), []),
            ("design: an external stylesheet fails (the required negative control)",
             lambda b: _design_file(b, extra='<link href="https://fonts.googleapis.com/x" rel="stylesheet">'),
             ["references an external host"]),
            ("design: a dangling asset reference fails",
             lambda b: _design_file(b, asset_tag='<img src="missing.png">', with_asset=False),
             ["does not resolve to a file"]),
            ("design: a header missing the viewport fails",
             lambda b: _design_file(b, header='<!--\n  Captured: 2026-08-17\n  Theme: light\n  Origin: demo\n-->\n'),
             ["must record the 1440x900 viewport"]),
            ("design: a header missing the theme fails",
             lambda b: _design_file(b, header='<!--\n  Captured: 2026-08-17\n  Viewport: 1440x900\n  Origin: demo\n-->\n'),
             ["must record the light theme"]),
            ("design: a header missing the capture date fails",
             lambda b: _design_file(b, header='<!--\n  Viewport: 1440x900\n  Theme: light\n  Origin: demo\n-->\n'),
             ["must record the capture date"]),
            ("design: a missing design file fails",
             lambda b: b / "nope.html", ["missing"]),
            ("design: a protocol-relative //host reference fails too (no scheme to grep for)",
             lambda b: _design_file(b, extra='<link href="//fonts.googleapis.com/x" rel="stylesheet">'),
             ["references an external host"]),
            ("design: a header with no font caveat fails — the reference renders in "
             "the page's own faces and must say so",
             lambda b: _design_file(b, header=good_header.replace(caveat_line, "")),
             ["cannot witness a font difference"]),
            ("design: a font caveat pointing nowhere fails — a caveat with no brief "
             "behind it reads as reassurance",
             lambda b: _design_file(b, header=good_header.replace(
                 caveat_line, f'  This file {DESIGN_FONT_CAVEAT_PHRASE}.\n')),
             ["must point at"]),
        ]
        for i, (name, make, want) in enumerate(design_cases):
            errs: list[str] = []
            target = make(Path(td) / f"design{i}")
            _check_design_self_contained(errs, target)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The design fixture: a well-formed synthetic fixture repo root, then
        # one malformed variant per rule — no .karta/binders, zero or two
        # binder files, invalid JSON, no slug, and a slug a fake ref_prober
        # reports as already real (the collision this fixture's whole point
        # is to avoid).
        def _fixture_root(base: Path, *, binders="one", slug="fixture-demo-slug",
                          valid_json=True) -> Path:
            d = base / ".karta" / "binders"
            if binders != "absent":
                d.mkdir(parents=True, exist_ok=True)
            if binders in ("one", "two"):
                payload = json.dumps({"slug": slug}) if valid_json else "{not json"
                (d / "a.json").write_text(payload, encoding="utf-8")
            if binders == "two":
                (d / "b.json").write_text(json.dumps({"slug": slug + "-2"}), encoding="utf-8")
            if binders == "no-slug":
                (d / "a.json").write_text(json.dumps({"title": "no slug here"}), encoding="utf-8")
            return base

        fixture_cases = [
            ("fixture: one well-formed binder, unclaimed slug -> no error",
             lambda b: _fixture_root(b), None, []),
            ("fixture: no .karta/binders -> not a fixture repo root",
             lambda b: _fixture_root(b, binders="absent"), None,
             ["not a fixture repo root"]),
            ("fixture: two binder files -> exactly one required",
             lambda b: _fixture_root(b, binders="two"), None,
             ["must hold exactly one binder"]),
            ("fixture: invalid JSON -> reported, not crashed",
             lambda b: _fixture_root(b, valid_json=False), None,
             ["invalid JSON"]),
            ("fixture: binder with no slug -> reported",
             lambda b: _fixture_root(b, binders="no-slug"), None,
             ["binder has no 'slug'"]),
            ("fixture: slug the prober reports as a real ref -> collision fails",
             lambda b: _fixture_root(b), lambda s: True,
             ["matches a real"]),
        ]
        for i, (name, make, prober, want) in enumerate(fixture_cases):
            errs: list[str] = []
            root = make(Path(td) / f"fixture{i}")
            _check_design_fixture(errs, root, ref_prober=prober)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The binder panel's ground, read from a synthetic design against a
        # stand-in page side: the agreeing pair first, then one violating
        # control per rule — the page's pre-item assignment (card on the
        # ground, frame on the surface) and each half of it alone, a design
        # value the page does not resolve to, a design panel on the page
        # ground, a section carrying a surface of its own, a design with no
        # dark palette, and a body that paints no single token.
        def _ground_design(base: Path, *, panel_bg="var(--surface)",
                           section_style="display:flex;flex-direction:column;gap:22px",
                           light_surface="#FFFFFF", dark=True, body_bg="var(--bg)") -> Path:
            base.mkdir(parents=True, exist_ok=True)
            dark_rule = (':root[data-theme="dark"]{--bg:#2B0F14;--surface:#3B141B;--line:#444}'
                         if dark else "")
            f = base / "design.html"
            f.write_text(
                "<!DOCTYPE html>\n<html>\n<head>\n<style>\n"
                ':root, :root[data-theme="light"]{--bg:#F6EFEE;--surface:' + light_surface
                + ";--line:#DFCBC6}\n" + dark_rule + "\n*{margin:0}\n"
                "body{background:" + body_bg + ";color:#000}\n</style>\n</head>\n<body>\n"
                '<main><section data-kw-panel="a" style="' + section_style + '">\n'
                '<div style="background:var(--band);border-radius:16px"><p>band</p></div>\n'
                '<div style="background:' + panel_bg + ';border:1px solid var(--line);'
                'border-radius:16px"><div style="padding:4px"><br>x</div></div>\n'
                "</section></main>\n</body>\n</html>\n", encoding="utf-8")
            return f

        page_side = {"page_card_roles": {"--surface"}, "page_frame_roles": {"--bg"},
                     "page_palette": {"--bg": {"light": "#F6EFEE", "dark": "#2B0F14"},
                                      "--surface": {"light": "#FFFFFF", "dark": "#3B141B"}}}
        ground_cases = [
            ("ground: design panel on the surface, page card on it, values agree -> no error",
             lambda b: _ground_design(b), page_side, []),
            ("ground: the pre-item assignment — card on the ground, frame on the surface — fails",
             lambda b: _ground_design(b),
             dict(page_side, page_card_roles={"--bg"}, page_frame_roles={"--surface"}),
             ["binder card resolves to ['--bg']", "frame around the binder card resolves to ['--surface']"]),
            ("ground: the page card on the ground alone fails",
             lambda b: _ground_design(b), dict(page_side, page_card_roles={"--bg"}),
             ["binder card resolves to ['--bg']"]),
            ("ground: the page frame on the surface alone fails",
             lambda b: _ground_design(b), dict(page_side, page_frame_roles={"--surface"}),
             ["frame around the binder card resolves to ['--surface']"]),
            ("ground: a page card with no role (a literal) fails",
             lambda b: _ground_design(b), dict(page_side, page_card_roles=set()),
             ["binder card resolves to no role"]),
            ("ground: a design surface value the page does not resolve to fails",
             lambda b: _ground_design(b, light_surface="#FFFFFE"), page_side,
             ["design declares --surface as #FFFFFE in its light palette"]),
            ("ground: a design panel declared on the page ground fails",
             lambda b: _ground_design(b, panel_bg="var(--bg)"), page_side,
             ["binder panel resolves to --bg"]),
            ("ground: a section carrying a surface of its own (a frame) fails",
             lambda b: _ground_design(b, section_style="display:flex;flex-direction:column;"
                                                       "gap:22px;background:var(--surface)"),
             page_side, ["carries a surface of its own"]),
            ("ground: a design with no dark palette fails",
             lambda b: _ground_design(b, dark=False), page_side, ["declares no dark palette"]),
            ("ground: a body that paints no single token fails",
             lambda b: _ground_design(b, body_bg="#fff"), page_side,
             ["body rule does not paint the page ground as one token"]),
        ]
        for i, (name, make, side, want) in enumerate(ground_cases):
            errs: list[str] = []
            _check_design_panel_ground(errs, make(Path(td) / f"ground{i}"), **side)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The serving rig, driven against stand-in pages rather than only
        # against the real one. The rig check is the assertion that a wrong
        # --root, a malformed fixture, or a page that stopped serving fails
        # at item one — so it ships with the pages that make it fail: one
        # that never answers, one that answers 200 without the fixture's
        # binder in the body, and a missing script. `serve_status.py` is
        # never started here; each stand-in takes the same --root/--port.
        rig_dir = Path(td) / "rig"
        rig_dir.mkdir()

        def _stand_in(name: str, body_expr: str) -> Path:
            p = rig_dir / name
            p.write_text(
                "import argparse, http.server\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--root'); p.add_argument('--port', type=int)\n"
                "a = p.parse_args()\n"
                f"BODY = {body_expr}\n"
                "class H(http.server.BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        b = BODY.encode()\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Length', str(len(b)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(b)\n"
                "    def log_message(self, *a): pass\n"
                "http.server.HTTPServer(('127.0.0.1', a.port), H).serve_forever()\n", encoding="utf-8")
            return p

        serving = _stand_in("serving.py", "'<html>fixture-demo-slug rendered</html>'")
        blank = _stand_in("blank.py", "'<html>no binder here</html>'")
        dead = rig_dir / "dead.py"
        dead.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")

        rig_cases = [
            ("rig: a page serving 200 with the fixture's binder in the body -> no error",
             serving, []),
            ("rig: a page answering 200 WITHOUT the fixture's binder fails "
             "(the negative control for a wrong --root)",
             blank, ["is not visibly rendered"]),
            ("rig: a page that exits instead of serving fails",
             dead, ["never answered 200"]),
            ("rig: a missing page script fails",
             rig_dir / "absent.py", ["missing"]),
        ]
        for name, script, want in rig_cases:
            errs: list[str] = []
            _check_design_serving_rig(errs, rig_dir, script, "fixture-demo-slug",
                                      timeout=4.0)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1
    import tempfile as _ec_tf
    with _ec_tf.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        # -------------------------------------------------------------------
        # Encoding. This becomes a gate every commit passes through, so every
        # shape it REJECTS gets a negative fixture and every shape it must
        # TOLERATE gets a positive one — an over-blocking encoding rule wedges
        # the repository just as thoroughly as a missing one lets a defect out.
        # Fixture sources are written into the temp dir, never tracked as .py:
        # tracked Python must stay parseable and decodable, since this rule and
        # the floor both read every one of them.
        # -------------------------------------------------------------------
        ec_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

        def ec_run(source: str, policy=None) -> list[str]:
            errs: list[str] = []
            sp = Path(td) / "ec_fixture_mod.py"
            sp.write_text(source, encoding="utf-8")
            _check_encoding(errs, policy=policy or ec_policy,
                            py_files=[(sp, "fixture/mod.py")])
            return errs

        SWALLOW = ('import json\nfrom pathlib import Path\n'
                   'def load(p):\n'
                   '    try:\n'
                   '        return json.loads(Path(p).read_text(encoding="utf-8"))\n'
                   '    except %s:\n'
                   '        return {}\n')

        ec_cases = [
            # --- negative: one per shape the contract names -------------------
            ("ec: a bare read_text() is caught",
             'from pathlib import Path\nPath("x").read_text()\n', ["read_text() names no encoding"]),
            ("ec: a bare write_text() is caught",
             'from pathlib import Path\nPath("x").write_text("y")\n', ["write_text() names no encoding"]),
            ("ec: a bare text-mode open() is caught",
             'f = open("x")\n', ["open() in text mode names no encoding"]),
            ('ec: an explicit text-mode open("w") is caught',
             'f = open("x", "w")\n', ["open() in text mode names no encoding"]),
            ("ec: io.open() in text mode is caught",
             'import io\nio.open("x")\n', ["io.open() in text mode"]),
            ("ec: codecs.open() with no encoding is caught",
             'import codecs\ncodecs.open("x", "r")\n', ["codecs.open() in text mode"]),
            ("ec: a text=True subprocess capture is caught",
             'import subprocess\nsubprocess.run(["git", "status"], capture_output=True, text=True)\n',
             ["text=True names no encoding"]),
            ("ec: universal_newlines=True is the same boundary and is caught",
             'import subprocess\nsubprocess.run(["git"], universal_newlines=True)\n',
             ["universal_newlines=True names no encoding"]),
            ("ec: a logging file handler with no encoding is caught",
             'import logging\nlogging.FileHandler("app.log")\n', ["FileHandler() names no encoding"]),
            ("ec: `json.load(sys.stdin)` in a module that never reconfigures stdin is caught",
             'import json, sys\npayload = json.load(sys.stdin)\n', ["reads sys.stdin as text"]),
            ("ec: a bare sys.stdin.read() is caught by the same rule",
             'import sys\ntext = sys.stdin.read()\n', ["reads sys.stdin as text"]),
            ("ec: a bare .decode() is caught",
             'raw = b"x"\ns = raw.decode()\n', ["bare .decode()"]),
            ("ec: a bare .encode() is caught",
             's = "x"\nraw = s.encode()\n', ["bare .encode()"]),
            ("ec: a swallowed UnicodeDecodeError is caught",
             SWALLOW % "UnicodeDecodeError", ["catches UnicodeDecodeError"]),
            ("ec: the same shape catching UnicodeError is caught",
             SWALLOW % "UnicodeError", ["catches UnicodeError"]),
            ("ec: the same shape catching ValueError is caught "
             "(UnicodeDecodeError IS a ValueError)",
             SWALLOW % "ValueError", ["catches ValueError"]),
            ("ec: the same shape catching Exception is caught",
             SWALLOW % "Exception", ["catches Exception"]),
            ("ec: the same shape behind a bare except: is caught",
             ('import json\nfrom pathlib import Path\n'
              'def load(p):\n'
              '    try:\n'
              '        return json.loads(Path(p).read_text(encoding="utf-8"))\n'
              '    except:\n'
              '        return {}\n'), ["catches Exception"]),
            # --- positive: the over-blocking cases that would wedge the repo ---
            ("ec: read_bytes() is binary IO and does not fire",
             'from pathlib import Path\nraw = Path("x").read_bytes()\n', []),
            ("ec: an 'rb' open() does not fire",
             'f = open("x", "rb")\n', []),
            ("ec: a COMPUTED-mode open() does not fire — the rule never guesses "
             "at a mode it cannot read",
             'def go(mode):\n    return open("x", mode)\n', []),
            ('ec: an explicit .decode("utf-8") does not fire',
             'raw = b"x"\ns = raw.decode("utf-8")\n', []),
            ('ec: read_text(encoding="utf-8") does not fire',
             'from pathlib import Path\nPath("x").read_text(encoding="utf-8")\n', []),
            ("ec: a text=True subprocess that names its codec does not fire",
             'import subprocess\nsubprocess.run(["git"], text=True, encoding="utf-8")\n', []),
            ("ec: the repo's binary-first stdin idiom does not fire",
             ('import json, sys\n'
              'stream = getattr(sys.stdin, "buffer", sys.stdin)\n'
              'data = stream.read()\n'
              'payload = json.loads(data.decode("utf-8", "replace")\n'
              '                     if isinstance(data, bytes) else data)\n'), []),
            ("ec: a module that reconfigures stdin may then read it as text",
             ('import json, sys\n'
              'sys.stdin.reconfigure(encoding="utf-8", errors="replace")\n'
              'payload = json.load(sys.stdin)\n'), []),
            ("ec: a handler that NAMES the error is surfacing it, not swallowing it",
             ('import json\nfrom pathlib import Path\n'
              'def load(p, errors):\n'
              '    try:\n'
              '        return json.loads(Path(p).read_text(encoding="utf-8"))\n'
              '    except ValueError as e:\n'
              '        errors.append(f"bad file: {e}")\n'
              '        return {}\n'), []),
            ("ec: a handler that re-raises is not swallowing",
             ('from pathlib import Path\n'
              'def load(p):\n'
              '    try:\n'
              '        return Path(p).read_text(encoding="utf-8")\n'
              '    except ValueError:\n'
              '        raise RuntimeError("unreadable")\n'), []),
            ("ec: a TOTAL codec has no failure to hide, so the handler is not a swallow",
             ('import json\nfrom pathlib import Path\n'
              'def load(p):\n'
              '    try:\n'
              '        return json.loads(Path(p).read_bytes().decode("utf-8", "replace"))\n'
              '    except (OSError, json.JSONDecodeError):\n'
              '        return {}\n'), []),
        ]
        for name, src, want in ec_cases:
            errs = ec_run(src)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # REACH IS COMPUTED FROM GIT AT CHECK TIME. A fixture repo whose violating
        # module sits in a directory no item in any binder enumerated: if the rule
        # read a hand-list it would miss this file, and the assertion would fail.
        ec_repo = Path(td) / "ec-reach-repo"
        (ec_repo / "a" / "tree" / "nobody" / "listed").mkdir(parents=True)
        (ec_repo / "a" / "tree" / "nobody" / "listed" / "late.py").write_text(
            'from pathlib import Path\nPath("x").read_text()\n', encoding="utf-8")
        for cmd in (["init", "-q"], ["add", "-A"]):
            subprocess.run(["git", "-C", str(ec_repo), *cmd], capture_output=True, timeout=120)
        errs = []
        _check_encoding(errs, policy=ec_policy, root=ec_repo)
        ok = any("a/tree/nobody/listed/late.py" in e and "read_text" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] ec: the rule reaches a .py added in a tree no "
              f"item enumerated — reach is git ls-files at check time, not a list"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # An excluded path is excluded for BOTH rules, from ONE list. Widening the
        # committed exclusions must silence the same finding the rule just made.
        widened = dict(ec_policy)
        widened["exclusions"] = list(ec_policy["exclusions"]) + [
            {"path": "a/tree/", "reason": "fixture: proves the shared exclusion list is read"}]
        errs_ex = []
        _check_encoding(errs_ex, policy=widened, root=ec_repo)
        ok = errs_ex == []
        print(f"[{'PASS' if ok else 'FAIL'}] ec: adding a path to the committed exclusion "
              f"list silences the finding — one list, read by both rules"
              + ("" if ok else f" — got {errs_ex!r}"))
        total += 1
        failures += 0 if ok else 1

        # Every exclusion carries a reason, and an entry without one FAILS. The same
        # assertion the command-portability group makes, over the same array — there
        # is only one array, which is the point.
        bad_ex = dict(ec_policy)
        bad_ex["exclusions"] = list(ec_policy["exclusions"]) + [{"path": "unreasoned/"}]
        ok = (all(isinstance(x.get("path"), str)
                  and len(str(x.get("reason", "")).strip()) > 40
                  for x in ec_policy.get("exclusions", []))
              and not all(isinstance(x.get("path"), str)
                          and len(str(x.get("reason", "")).strip()) > 40
                          for x in bad_ex["exclusions"]))
        print(f"[{'PASS' if ok else 'FAIL'}] ec: every committed exclusion carries a reason, "
              f"and an entry without one fails")
        total += 1
        failures += 0 if ok else 1

        # The exclusion list is SHARED, not duplicated: the two rules must give the
        # same answer for every path, which they can only do by reading one array.
        ok = all(_excluded(p, ec_policy) == _excluded(p, ec_policy)
                 for p in ("benchmarks/x.py", ".karta/binders/a.json", "scripts/x.py"))
        ok = ok and _excluded("benchmarks/x.py", ec_policy) \
            and _excluded(".claude/skills/plannotator-compound/scripts/x.py", ec_policy) \
            and not _excluded("hooks/scripts/guard_pack_write.py", ec_policy)
        print(f"[{'PASS' if ok else 'FAIL'}] ec: command portability and encoding resolve "
              f"scope through the same predicate over the same committed array")
        total += 1
        failures += 0 if ok else 1

        # The refinement this port added: binary mode has nothing to decode, so
        # an except around `open(p, "rb")` + json.load(fh) — the byte-first
        # shape this sweep standardises on — is not a swallowed decode failure.
        errs = ec_run('import json\n'
                      'def load(p):\n'
                      '    try:\n'
                      '        with open(p, "rb") as fh:\n'
                      '            return json.load(fh)\n'
                      '    except (OSError, ValueError):\n'
                      '        return {}\n')
        ok = errs == []
        print(f"[{'PASS' if ok else 'FAIL'}] ec: a binary-mode open under a degrade "
              f"handler is not a boundary — bytes have nothing to mis-decode"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

    import tempfile as _cp_tf
    with _cp_tf.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        # _check_codex_hook_windows: a Codex command hook with no Windows twin fails
        # on Windows rather than running. Fixtures cover both directions plus the two
        # drift modes, because a check that cannot fail is not a check.
        def _manifest(hooks: list) -> dict:
            return {"hooks": {"Stop": [{"matcher": "*", "hooks": hooks}]}}

        POSIX = ("sh -c 'p=\"${PLUGIN_ROOT}/.codex-plugin/hooks/scripts/guard_delivery_stop.py\";"
                 " [ -f \"$p\" ] && exec python3 \"$p\"; exit 0'")
        WIN = ('if exist "%PLUGIN_ROOT%\\.codex-plugin\\hooks\\launch_hook.ps1" (powershell -File'
               ' "%PLUGIN_ROOT%\\.codex-plugin\\hooks\\launch_hook.ps1"'
               ' .codex-plugin/hooks/scripts/guard_delivery_stop.py Plugin) else (exit /b 0)')
        WIN_OTHER = WIN.replace("guard_delivery_stop.py", "guard_pack_write.py")

        hook_cases = [
            ("command hook with a matching Windows twin passes",
             _manifest([{"type": "command", "command": POSIX, "commandWindows": WIN}]), []),
            ("command hook with no Windows twin is caught",
             _manifest([{"type": "command", "command": POSIX}]), ["missing 'commandWindows'"]),
            ("empty Windows twin is caught (present but blank)",
             _manifest([{"type": "command", "command": POSIX, "commandWindows": "   "}]),
             ["missing 'commandWindows'"]),
            ("Windows twin that shells out to sh is caught",
             _manifest([{"type": "command", "command": POSIX,
                         "commandWindows": "sh -c 'exec python3 x'"}]), ["invokes sh"]),
            ("Windows twin naming a different guard is caught",
             _manifest([{"type": "command", "command": POSIX, "commandWindows": WIN_OTHER}]),
             ["enforce different rules"]),
            ("Windows twin that is a sh-free NO-OP is caught — present, non-empty, "
             "and running nothing is the quietest way to disarm one platform",
             _manifest([{"type": "command", "command": POSIX,
                         "commandWindows": "exit /b 0"}]),
             ["names no guard script"]),
            ("non-command hooks are not required to have a twin",
             _manifest([{"type": "prompt"}]), []),
        ]
        for i, (name, doc, want) in enumerate(hook_cases):
            man = Path(td) / f"codexhooks{i}.json"
            man.write_text(json.dumps(doc), encoding="utf-8")
            errors = []
            _check_codex_hook_windows(errors, manifests=[man])
            ok = bool(errors) == bool(want) and all(any(w in e for e in errors) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errors!r}"))
            total += 1
            failures += 0 if ok else 1

        errors = []
        _check_codex_hook_windows(errors, manifests=[Path(td) / "no-such-manifest.json"])
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] absent Codex hook manifest is skipped, not an error"
              + ("" if ok else f" — got {errors!r}"))
        total += 1
        failures += 0 if ok else 1

        bad = Path(td) / "codexhooks-bad.json"
        bad.write_text("{not json", encoding="utf-8")
        errors = []
        _check_codex_hook_windows(errors, manifests=[bad])
        ok = len(errors) == 1 and "unreadable" in errors[0]
        print(f"[{'PASS' if ok else 'FAIL'}] unreadable Codex hook manifest is reported, not a crash"
              + ("" if ok else f" — got {errors!r}"))
        total += 1
        failures += 0 if ok else 1

        # The shipped manifests must satisfy the check they are the reason for — this
        # is what would have caught the reported Windows failure before it shipped.
        errors = []
        _check_codex_hook_windows(errors)
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] this repo's Codex hook manifests carry Windows twins"
              + ("" if ok else f" — got {errors!r}"))
        total += 1
        failures += 0 if ok else 1

        # Command portability. The rule covers the CLASS that produced the escaped
        # defect, so every shape it rejects gets its own negative fixture and every
        # shape it must tolerate gets a positive one — a rule whose compliant shapes
        # are untested is a rule that wedges the repo the first time one appears.
        cp_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

        def cp_manifest(command: str, windows: str | None = None) -> dict:
            hook = {"type": "command", "command": command}
            if windows:
                hook["commandWindows"] = windows
            return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [hook]}]}}

        def cp_run(policy, manifest_doc=None, source=None) -> list[str]:
            errs: list[str] = []
            mans, pys = [], []
            if manifest_doc is not None:
                mp = Path(td) / "cp-fixture-hooks.json"
                mp.write_text(json.dumps(manifest_doc), encoding="utf-8")
                mans = [(mp, "fixture/hooks.json")]
            if source is not None:
                sp = Path(td) / "cp_fixture_mod.py"
                sp.write_text(source, encoding="utf-8")
                pys = [(sp, "fixture/mod.py")]
            _check_command_portability(errs, policy=policy, manifests=mans, py_files=pys)
            return errs

        GUARD = "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/guard_pack_write.py"
        TWIN = 'powershell -File "%PLUGIN_ROOT%\\launch_hook.ps1" guard_pack_write.py'

        cp_cases = [
            # --- negative: one per shape the contract names -------------------
            ("cp: manifest launching a hook through `sh -c` is caught",
             cp_manifest(f"sh -c 'exec \"{GUARD}\"'"), None, ["POSIX shell"]),
            ("cp: manifest launching a hook through `bash -c` is caught",
             cp_manifest(f"bash -c 'exec \"{GUARD}\"'"), None, ["POSIX shell"]),
            ("cp: manifest naming a bare `python3` is caught",
             cp_manifest(f'python3 "{GUARD}"'), None, ["bare `python`"]),
            ("cp: manifest naming a bare `python` is caught",
             cp_manifest(f'python "{GUARD}"'), None, ["bare `python`"]),
            ("cp: manifest relying on a shebang and the executable bit is caught",
             cp_manifest(f'"{GUARD}"'), None, ["shebang and executable bit"]),
            ("cp: manifest invoking a POSIX-only utility is caught",
             cp_manifest('grep -qF karta AGENTS.md'), None, ["POSIX-only utility 'grep'"]),
            ("cp: python launching through `sh -c` is caught", None,
             'import subprocess\nsubprocess.run(["sh", "-c", "echo hi"])\n', ["POSIX shell 'sh'"]),
            ("cp: the same launch inside an os.name platform branch is a decision, "
             "not an assumption — it does not fire", None,
             'import os, subprocess\n'
             'if os.name == "nt":\n'
             '    pass\n'
             'else:\n'
             '    subprocess.run(["sh", "-c", "echo hi"])\n', []),
            ("cp: a platform branch excuses only the shell — a bare interpreter "
             "inside one is still caught", None,
             'import os, subprocess\n'
             'if os.name != "nt":\n'
             '    subprocess.run(["python3", "x.py"])\n', ["bare 'python3'"]),
            ("cp: the exemption reads the branch DIRECTION — sh -c in the "
             "WINDOWS arm is the failure itself, not a platform decision", None,
             'import os, subprocess\n'
             'if os.name == "nt":\n'
             '    subprocess.run(["sh", "-c", "echo hi"])\n', ["POSIX shell 'sh'"]),
            ("cp: a test the reader cannot decide never widens the exemption — "
             "a dead platform-flag wrapper is not a licence", None,
             'import subprocess\n'
             'IS_POSIX = True\n'
             'if IS_POSIX:\n'
             '    subprocess.run(["sh", "-c", "echo hi"])\n', ["POSIX shell 'sh'"]),
            ("cp: python invoking a bare `python3` is caught", None,
             'import subprocess\nsubprocess.run(["python3", "x.py"])\n', ["bare 'python3'"]),
            ("cp: python running a script on its shebang is caught", None,
             'import subprocess\nsubprocess.run(["tools/thing.py", "--go"])\n',
             ["shebang and executable bit"]),
            ("cp: python invoking a POSIX-only utility is caught", None,
             'import subprocess\nsubprocess.run(["xargs", "-0", "rm"])\n',
             ["POSIX-only utility 'xargs'"]),
            ("cp: a bare interpreter reached through one alias hop is caught", None,
             'import subprocess, sys\n'
             'def go(args):\n'
             '    py = sys.executable or "python3"\n'
             '    return subprocess.run([py, "helper.py", *args])\n',
             ["bare 'python3'"]),
            # --- positive: compliant shapes must NOT fire ---------------------
            ("cp: a manifest hook with a commandWindows twin does not fire",
             cp_manifest(f"sh -c 'exec python3 \"{GUARD}\"'", TWIN), None, []),
            ("cp: `uv run --script` is the compliant Claude-side shape",
             cp_manifest(f'uv run --script "{GUARD}"'), None, []),
            ("cp: a subprocess invocation naming sys.executable does not fire", None,
             'import subprocess, sys\nsubprocess.run([sys.executable, "helper.py"])\n', []),
            ("cp: a POSIX-only name outside command position is an argument, not a call",
             None, 'import subprocess\nsubprocess.run(["git", "grep", "-n", "test"])\n', []),
        ]
        for name, doc, src, want in cp_cases:
            errs = cp_run(cp_policy, manifest_doc=doc, source=src)
            ok = bool(errs) == bool(want) and all(any(w in e for e in errs) for w in want)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" — got {errs!r}"))
            total += 1
            failures += 0 if ok else 1

        # The utility list is DATA the rule reads, not prose beside it: the same argv
        # passes under the committed list and fails once a name is added to it. Without
        # this, nothing distinguishes a list the rule consults from a list it ignores.
        widened = dict(cp_policy)
        widened["posix_only_utilities"] = sorted(set(cp_policy["posix_only_utilities"]) | {"cowsay"})
        before = cp_run(cp_policy, source='import subprocess\nsubprocess.run(["cowsay", "moo"])\n')
        after = cp_run(widened, source='import subprocess\nsubprocess.run(["cowsay", "moo"])\n')
        ok = before == [] and any("'cowsay'" in e for e in after)
        print(f"[{'PASS' if ok else 'FAIL'}] cp: adding a name to the committed utility list "
              f"makes a previously-passing argv fail"
              + ("" if ok else f" — before {before!r}, after {after!r}"))
        total += 1
        failures += 0 if ok else 1

        # The four utilities measured absent from PowerShell on the target host, plus the
        # companions that would otherwise pass by omission. Pinned because a policy file
        # that quietly lost an entry disarms the rule without failing anything.
        want_utils = {"env", "xargs", "grep", "test", "sed", "awk", "cat", "tr",
                      "cut", "mktemp", "readlink", "realpath"}
        missing = sorted(want_utils - set(cp_policy.get("posix_only_utilities", ())))
        ok = not missing
        print(f"[{'PASS' if ok else 'FAIL'}] cp: the committed utility list covers every "
              f"enumerated name" + ("" if ok else f" — missing {missing!r}"))
        total += 1
        failures += 0 if ok else 1

        # Exclusions are committed WITH their reason. A path excluded silently is a hole
        # nobody can review; a path excluded with a reason is a decision.
        ok = (_excluded(".karta/binders/archive/pack-separation.json", cp_policy)
              and _excluded("benchmarks/fixtures/shellenv.py", cp_policy)
              and not _excluded("hooks/hooks.json", cp_policy)
              and not _excluded("scripts/hooks/precommit_gate.py", cp_policy)
              and all(isinstance(x.get("path"), str) and len(str(x.get("reason", "")).strip()) > 40
                      for x in cp_policy.get("exclusions", [])))
        print(f"[{'PASS' if ok else 'FAIL'}] cp: every exclusion carries a path and a reason, "
              f"and excludes only what it names")
        total += 1
        failures += 0 if ok else 1

        # The repo must satisfy the rule it arms. This is the assertion that would have
        # caught the reported Windows failure — and the one that keeps the rule honest,
        # since a rule armed over an unrepaired tree wedges every later commit.
        errors = []
        _check_command_portability(errors)
        ok = errors == []
        print(f"[{'PASS' if ok else 'FAIL'}] cp: this repo's manifests and subprocess calls "
              f"are portable" + ("" if ok else f" — got {errors!r}"))
        total += 1
        failures += 0 if ok else 1

    # Line endings: the rule reads git's index state, so the fixtures commit
    # real bytes into a fabricated repo — a hand-built string cannot fake what
    # `git ls-files --eol` reports.
    import tempfile as _le_tf
    with _le_tf.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        le_repo = Path(td) / "le-repo"
        le_repo.mkdir()
        def _le_git(*a: str) -> None:
            subprocess.run(["git", "-C", str(le_repo), *a], capture_output=True,
                           timeout=120)
        _le_git("init", "-q")
        _le_git("config", "user.email", "t@example.com")
        _le_git("config", "user.name", "t")
        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\n").encode("utf-8"))
        (le_repo / "clean.py").write_bytes(b"print('x')\n")
        (le_repo / "keep").mkdir()
        (le_repo / "keep" / "bytes.json").write_bytes(b'{"a": 1}\r\n')
        sneak = le_repo / "sneak.txt"
        # committed as CRLF despite the default: exactly the accident a
        # differently configured clone produces — forced past normalization
        # the same way (a later attr change leaves the index as committed)
        (le_repo / ".gitattributes").write_bytes(b"sneak.txt -text\n")
        sneak.write_bytes(b"a\r\nb\r\n")
        _le_git("add", "-A")
        _le_git("-c", "commit.gpgsign=false", "commit", "-qm", "seed")
        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\n").encode("utf-8"))
        _le_git("add", ".gitattributes")
        errs: list[str] = []
        _check_line_endings(errs, root=le_repo)
        ok = (any("sneak.txt" in e and "CRLF" in e for e in errs)
              and not any("bytes.json" in e for e in errs)
              and not any("clean.py" in e for e in errs))
        print(f"[{'PASS' if ok else 'FAIL'}] le: a CRLF file with no -text exemption is "
              f"caught, an exempted byte-store and a clean file are not"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        (le_repo / ".gitattributes").write_bytes(b"keep/** -text\nsneak.txt -text\n")
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = any("missing the exact default" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] le: dropping the default line is its own "
              f"finding — without it the rule guards nothing on the next clone"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\nsneak.txt -text\n"
             "*.ps1 text eol=crlf\n").encode("utf-8"))
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = any("eol=crlf" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] le: an eol=crlf override is refused — it "
              f"flips checkouts while every index assertion stays green"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # git treats '#' as a comment at line START only, so an embedded '#'
        # in a pattern must not truncate the scan (the round-2 smuggle), while
        # a whole-line comment MENTIONING eol=crlf must not trip it.
        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\nsneak.txt -text\n"
             "# never set eol=crlf here\nissue#1.txt text eol=crlf\n").encode("utf-8"))
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = (sum("eol=crlf" in e for e in errs) == 1
              and any("issue#1.txt" in e for e in errs))
        print(f"[{'PASS' if ok else 'FAIL'}] le: an embedded-# pattern cannot smuggle "
              f"eol=crlf past the scan, and a comment mentioning it does not trip"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        # The belt invariant: a tracked file OF a belt format is never left
        # unprotected. On a case-insensitive filesystem `*.png` reaches
        # `logo.PNG` and the attr carries -text (nothing to flag); on a
        # case-sensitive one the pattern misses and the check must flag it.
        # Assert the invariant, not one platform's expression of it — and
        # prove the check ARM by removing the belt line, which must flag on
        # every platform.
        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\nsneak.txt -text\n"
             "*.png -text -eol\n").encode("utf-8"))
        (le_repo / "logo.PNG").write_bytes(b"NOT-REALLY-PNG\r\ntext header\r\n")
        _le_git("add", "-A")
        attr_probe = subprocess.run(
            ["git", "-C", str(le_repo), "check-attr", "text", "--", "logo.PNG"],
            capture_output=True, text=True, encoding="utf-8", timeout=120)
        belt_reached = attr_probe.stdout.strip().endswith("unset")
        errs = []
        _check_line_endings(errs, root=le_repo)
        flagged = any("logo.PNG" in e and "format belt" in e for e in errs)
        ok = flagged != belt_reached
        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\nsneak.txt -text\n").encode("utf-8"))
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = ok and any("logo.PNG" in e and "format belt" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] le: a belt-format file is never left "
              f"unprotected — flagged exactly when the belt misses it, and always "
              f"once the belt line is gone" + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1
        _le_git("rm", "-q", "--cached", "logo.PNG")
        (le_repo / "logo.PNG").unlink()

        (le_repo / ".gitattributes").write_bytes(
            (LINE_ENDING_DEFAULT + "\nkeep/** -text\nsneak.txt -text\n").encode("utf-8"))
        (le_repo / "sub").mkdir()
        (le_repo / "sub" / ".gitattributes").write_bytes(b"* -text\n")
        _le_git("add", "-A")
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = any("nested attributes file" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] le: a nested .gitattributes is refused — "
              f"the root file is the whole policy"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        _le_git("rm", "-q", "--cached", "sub/.gitattributes")
        (le_repo / "sub" / ".gitattributes").unlink()
        (le_repo / ".karta").mkdir()
        (le_repo / ".karta" / "record.json").write_bytes(b'{"x": 1}\r\n')
        _le_git("add", "-A")
        errs = []
        _check_line_endings(errs, root=le_repo)
        ok = any("record.json" in e and "'text'" in e for e in errs)
        print(f"[{'PASS' if ok else 'FAIL'}] le: a .karta sentinel whose text attribute "
              f"is not unset is refused — the byte-store guarantee is asserted, "
              f"never assumed" + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

        errs = []
        _check_line_endings(errs)
        ok = errs == []
        print(f"[{'PASS' if ok else 'FAIL'}] le: this repo satisfies the rule it arms"
              + ("" if ok else f" — got {errs!r}"))
        total += 1
        failures += 0 if ok else 1

    print(f"self-test: {total - failures}/{total} embedded fixture cases passed")
    return 1 if failures else 0


def _run_self_test(script: Path, errors: list[str]) -> None:
    """Run `<script> --self-test` and append a reported error on failure. Shared by the
    hooks/scripts and skills/*/scripts passes so both self-test their fixtures identically.

    The floor assumes every gated script exposes --self-test, so that invariant is enforced,
    not narrated: a script whose --help does not list --self-test is a distinct, named failure
    ('does not expose --self-test') — never mistaken for a self-test that ran and failed, and
    never a silent pass behind argparse's generic 'unrecognized arguments' exit."""
    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return p.name
    try:
        helpp = subprocess.run([sys.executable, str(script), "--help"],
                               capture_output=True, text=True, timeout=120, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{rel(script)}: could not probe --help ({e})")
        return
    if "--self-test" not in (helpp.stdout + helpp.stderr):
        errors.append(f"{rel(script)}: does not expose --self-test "
                      f"(the validator floor self-tests every gated script; add a --self-test mode)")
        return
    try:
        proc = subprocess.run([sys.executable, str(script), "--self-test"],
                              capture_output=True, text=True, timeout=120, encoding="utf-8")
    except (OSError, subprocess.TimeoutExpired) as e:
        errors.append(f"{rel(script)}: --self-test did not run ({e})")
        return
    if proc.returncode != 0:
        tail = "; ".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
        errors.append(f"{rel(script)}: --self-test failed ({tail})")


def _check_fact_traces(errors: list[str], binders_dir: Path | None = None) -> None:
    """Every design fact a binder records must be traced to an assertion
    ('<item-id>:<0-based assertion index>') or carry an untraced_reason — the rule
    scripts/check_fact_traces.py owns. It runs here so a fact table added later cannot
    go untraced without failing the floor. The sweep is .karta/binders/*.json plus its
    archive/ subdirectory (see check_fact_traces.check_path): a LIST-shaped fact table
    is checked there exactly like a live one — archived does not mean exempt — while a
    DICT-shaped, pre-convention table is reported as OUT OF SCOPE by name rather than
    checked or silently passed over. That report must be visible at THIS enforced floor,
    not only in the standalone script, so its notes are printed here rather than
    discarded. The script's own fixtures run too, the way every other gated script's do.
    `binders_dir` is the self-test seam; the default sweeps this repo."""
    if binders_dir is None:
        binders_dir = ROOT / ".karta" / "binders"
        _run_self_test(ROOT / "scripts" / "check_fact_traces.py", errors)
    if binders_dir.is_dir():
        swept, notes = check_fact_traces.check_path(binders_dir)
        errors.extend(f"fact traces: {e}" for e in swept)
        for n in notes:
            print(f"  ~ fact traces: {n}")


def _check_skill_scripts(errors: list[str]) -> None:
    """Self-test every skills/*/scripts/*.py the way _check_hooks self-tests the hook
    scripts. The hooks pass never covered the skill-shipped scripts, so their embedded
    fixtures (e.g. resolve_pack_checklist.py) went unrun at commit; every skills script
    exposes --self-test, so this pass durably covers them."""
    for script in sorted(SKILLS.glob("*/scripts/*.py")):
        _run_self_test(script, errors)


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
        _run_self_test(script, errors)

    # Codex guards are hand-maintained implementations, not copies of the Claude
    # guards above. Projection equality alone cannot prove they work.
    for script in sorted((ROOT / ".codex-plugin/hooks/scripts").glob("*.py")):
        _run_self_test(script, errors)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="run the embedded doc-gardner schema fixtures, then the repo check")
    args = ap.parse_args()
    if args.self_test and _self_test() != 0:
        print("PLUGIN INTEGRITY: FAIL")
        print("  - embedded --self-test fixtures failed")
        return 1
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
