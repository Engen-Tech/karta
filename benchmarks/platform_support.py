"""Small cross-platform launch helpers shared by benchmark probes."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def bash_argv(script: Path, *args: Path | str, syntax_only: bool = False) -> list[str]:
    """Return argv for Bash without routing through a shell command string.

    Git is already a Karta prerequisite. On Windows, use Git for Windows' Bash
    instead of the unrelated WSL app alias, and pass forward-slash absolute
    paths so backslashes are not consumed as shell escapes.
    """
    if os.name == "nt":
        git = shutil.which("git")
        candidates: list[Path] = []
        if git:
            git_path = Path(git).resolve()
            candidates.extend((
                git_path.parent.parent / "bin" / "bash.exe",
                git_path.parent.parent / "usr" / "bin" / "bash.exe",
            ))
        bash = next((candidate for candidate in candidates if candidate.is_file()), None)
        if bash is None:
            raise FileNotFoundError(
                "Git for Windows Bash was not found beside the installed git.exe")

        def path_arg(value: Path | str) -> str:
            return value.resolve().as_posix() if isinstance(value, Path) else str(value)
    else:
        bash = Path(shutil.which("bash") or "bash")

        def path_arg(value: Path | str) -> str:
            return str(value)

    return [str(bash), *(["-n"] if syntax_only else []),
            path_arg(script), *[path_arg(arg) for arg in args]]
