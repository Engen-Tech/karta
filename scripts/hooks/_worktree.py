# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Which working tree a hook's lookups belong to — the shared invocation-root resolver.

The PreToolUse hooks in scripts/hooks/ judge a git command before bash runs it,
and each one reads repository state to do it: a branch name, HEAD's copy of a
file, the index, the worktree bytes. Every one of those reads has to come from
the tree the command actually runs in. A hook roots itself at the checkout its
own script file sits in, which is the primary checkout — so a command issued in
a linked worktree gets judged on a tree that has nothing to do with it, and the
verdict is wrong in both directions at once (register INV-11, backlog item 11).

The answer lives in its own module rather than inside one hook because both
hooks have the same defect from the same cause: roundtable_gate.py resolves
through this module, and precommit_gate.py roots itself at its own script file
in exactly the same way. One resolver cannot drift into two answers, and
importing this handful of lines out of a 2600-line review hook would drag that
hook's whole surface along with it.

resolve_invocation_root(cwd) answers the one question that fixes it: which tree
contains this cwd?

  - a cwd inside `root`'s own working tree                 -> `root`, unchanged
  - a cwd inside a linked worktree of the SAME repository  -> that worktree's top
  - anything else — no repository above it, or a DIFFERENT
    repository's tree, nested inside `root`'s or not       -> None

WALK-UP CONTAINMENT, not top-level matching: a subdirectory answers with the top
level that contains it, because a hook needs somewhere to read from even when the
cwd itself is not a directory it would accept. Deciding whether that cwd is
*acceptable* is the caller's business and stays there — this module says where
state lives, never whether the invocation may proceed. The two answers differ on
purpose: a subdirectory of a worktree resolves (so its reads are right) and is
still denied by the caller (so a pathspec issued there is never guessed at).

MEMBERSHIP IS THE COMMON DIRECTORY, never "the nearest .git". Every worktree of
one repository shares a single git common directory — `rev-parse
--git-common-dir` — while a different repository, including one nested inside
this tree, has its own. Matching on "the first ancestor carrying a .git" would
hand a vendored clone or a submodule the same answer as our own worktree, and
the hook would then judge one repository's command against another's state. So
the walk stops at the first ancestor that is a working tree, and answers only if
that tree's common directory is `root`'s.

READ FROM THE POINTER FILES, no subprocess. The common directory is exactly what
git records on disk: `<top>/.git` is either the directory itself (a primary
checkout) or a one-line `gitdir: <path>` pointer (a linked worktree), and a
linked worktree's gitdir carries a `commondir` file naming the shared one. That
is the same fact `git rev-parse --git-common-dir` prints, obtained without
spawning git per lookup — a PreToolUse hook runs on every Bash call.

FAIL-CLOSED, quietly. Anything unreadable, unparseable, or outside the
repository is None. None is the conservative answer for both callers: the hook
keeps the triple it was handed and its own cwd denial still fires, so an
unreadable worktree can never be waved through as if it were the root.

  python3 _worktree.py                # print this cwd's invocation root, or 'none'
  python3 _worktree.py --self-test    # fabricated git layouts, exit 0/1

Import-safe: no side effects at import, everything below __main__ guarded.
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/hooks/ -> repo root
GITDIR_PREFIX = "gitdir:"
COMMONDIR = "commondir"


def _gitdir_of(top: Path) -> Path | None:
    """The git directory the `.git` entry at `top` names — the directory itself
    for a primary checkout, or the path a `gitdir:` pointer file names for a
    linked worktree (resolved against `top` when it is written relative). None
    when `top` carries no readable `.git` entry, so it is not a worktree top."""
    dot = top / ".git"
    try:
        if dot.is_dir():
            return dot
        if not dot.is_file():
            return None
        text = dot.read_text(errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(GITDIR_PREFIX):
            named = Path(line[len(GITDIR_PREFIX):].strip())
            return named if named.is_absolute() else (top / named)
    return None


def _common_dir(top: Path) -> str | None:
    """The realpath of the git common directory shared by every worktree of the
    repository `top` belongs to — `rev-parse --git-common-dir`, read from the
    pointer files. None when `top` is not the top level of a working tree. This
    is the identity two trees are compared on: same common directory, same
    repository."""
    gitdir = _gitdir_of(top)
    if gitdir is None:
        return None
    try:
        marker = gitdir / COMMONDIR
        if marker.is_file():
            named = marker.read_text(errors="replace").strip()
            if named:
                p = Path(named)
                gitdir = p if p.is_absolute() else (gitdir / p)
    except OSError:
        return None
    return os.path.realpath(str(gitdir))


def resolve_invocation_root(cwd: str, root: Path | None = None) -> Path | None:
    """The top level of the working tree that contains `cwd`, when that tree
    belongs to the same repository as `root` (default: this checkout) — else
    None. `root` itself is returned unchanged, not a normalised copy, so a
    caller can compare it by identity and skip rescoping when nothing moved."""
    base = ROOT if root is None else (root if isinstance(root, Path) else Path(root))
    if not isinstance(cwd, str) or not cwd:
        return None
    base_common = _common_dir(base)
    if base_common is None:
        return None  # the caller's own root is not a working tree: nothing to belong to
    base_real = Path(os.path.realpath(str(base)))
    try:
        here = Path(os.path.realpath(cwd))
    except (OSError, ValueError):
        return None
    for candidate in (here, *here.parents):
        common = _common_dir(candidate)
        if common is None:
            continue  # not a worktree top; keep walking up
        if common != base_common:
            return None  # a different repository owns this cwd — never ours to read
        return base if candidate == base_real else candidate
    return None


def _run_self_test() -> int:
    """Fabricated git layouts in a temporary directory — the exact on-disk shapes
    git writes, never a real `git worktree add`, so the suite touches no
    repository's worktree registry (this one's least of all)."""
    import tempfile
    failures = total = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures, total
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{': ' + detail if detail and not ok else ''}")
        failures += 0 if ok else 1
        total += 1

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(os.path.realpath(tmpdir))
        # a primary checkout with one linked worktree, spelled the way git spells it
        main = tmp / "main"
        admin = main / ".git" / "worktrees" / "wt"
        admin.mkdir(parents=True)
        (admin / COMMONDIR).write_text("../..\n")
        (admin / "gitdir").write_text(f"{tmp / 'wt' / '.git'}\n")
        (main / "scripts" / "hooks").mkdir(parents=True)
        wt = tmp / "wt"
        (wt / "docs" / "deep").mkdir(parents=True)
        (wt / ".git").write_text(f"{GITDIR_PREFIX} {admin}\n")
        # a second linked worktree whose pointer is written relative to itself
        admin2 = main / ".git" / "worktrees" / "rel"
        admin2.mkdir(parents=True)
        (admin2 / COMMONDIR).write_text("../..\n")
        rel = tmp / "rel"
        (rel / "sub").mkdir(parents=True)
        (rel / ".git").write_text(f"{GITDIR_PREFIX} ../main/.git/worktrees/rel\n")
        # a different repository, standing alone and nested inside the primary tree
        other = tmp / "other"
        (other / ".git").mkdir(parents=True)
        (other / "src").mkdir()
        nested = main / "vendor" / "clone"
        (nested / ".git").mkdir(parents=True)
        (nested / "lib").mkdir()
        # a directory with no repository above it at all
        outside = tmp / "outside"
        outside.mkdir()

        def r(path) -> Path | None:
            return resolve_invocation_root(str(path), root=main)

        check("root-cwd-returns-root: the primary checkout's own top level resolves to the root it "
              "was given, returned unchanged so a caller can skip rescoping",
              r(main) is main, str(r(main)))
        check("worktree-top-returns-worktree: a linked worktree's top level resolves to itself, not "
              "to the primary checkout that owns its git directory",
              r(wt) == wt, str(r(wt)))
        check("subdir-returns-containing-top: a subdirectory answers with the top level that "
              "contains it, on both sides — under the primary checkout and under a linked worktree",
              r(main / "scripts" / "hooks") is main and r(wt / "docs" / "deep") == wt,
              f"{r(main / 'scripts' / 'hooks')} {r(wt / 'docs' / 'deep')}")
        check("outside-returns-none: a directory with no working tree above it resolves to nothing",
              r(outside) is None, str(r(outside)))
        # the negative control for the membership rule itself: a nearest-.git walk-up
        # would answer `other` and `nested` here, and both answers would let one
        # repository's command be judged against another's state
        check("other-repo-returns-none: a different repository is never ours to read — neither one "
              "standing alone nor one nested inside the primary tree, both of which a nearest-.git "
              "walk-up would have claimed", r(other / "src") is None and r(nested / "lib") is None,
              f"{r(other / 'src')} {r(nested / 'lib')}")

        check("a gitdir pointer written relative to its own worktree resolves like an absolute one",
              r(rel) == rel and r(rel / "sub") == rel, f"{r(rel)} {r(rel / 'sub')}")
        check("membership is the common directory: the linked worktree and the primary checkout "
              "share one, the nested clone does not",
              _common_dir(wt) == _common_dir(main) != _common_dir(nested),
              f"{_common_dir(wt)} {_common_dir(main)} {_common_dir(nested)}")
        (tmp / "junk").mkdir()
        (tmp / "junk" / ".git").write_text("not a pointer file\n")
        check("an unreadable or unparseable .git entry is nothing, never the root",
              r(tmp / "junk") is None, str(r(tmp / "junk")))
        check("an empty or non-string cwd resolves to nothing rather than raising",
              r("") is None and resolve_invocation_root(None, root=main) is None)  # type: ignore[arg-type]
        check("a root that is not a working tree resolves nothing at all — including its own path",
              resolve_invocation_root(str(outside), root=outside) is None
              and resolve_invocation_root(str(main), root=outside) is None)

    # the same reading against a real git layout, whichever kind this checkout is
    check("this repository's own root and a subdirectory of it both resolve to ROOT",
          resolve_invocation_root(str(ROOT)) is ROOT
          and resolve_invocation_root(str(ROOT / "scripts" / "hooks")) is ROOT,
          f"{resolve_invocation_root(str(ROOT))} {resolve_invocation_root(str(ROOT / 'scripts' / 'hooks'))}")

    print(f"\n{total - failures}/{total} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="resolve this cwd to the working tree that contains it")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    resolved = resolve_invocation_root(os.getcwd())
    print(resolved if resolved is not None else "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
