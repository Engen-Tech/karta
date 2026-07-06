# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Detect a repo's stack from its manifests: dependency names + languages.

Zero dependencies (pure stdlib), so every invocation form behaves identically —
nothing has to be provisioned before it runs:
  python3 detect_stack.py <repo-root>        # print {"dependencies": [...], "languages": [...]}
  python3 detect_stack.py --self-test        # run embedded fixtures, exit 0/1
  uv run --script detect_stack.py <repo-root>  # also fine — no deps to install

Manifests read (repo root only):
  package.json (dependencies + devDependencies), pyproject.toml (project.dependencies
  + tool.poetry.dependencies), requirements*.txt, go.mod (require module paths),
  Cargo.toml ([dependencies] keys), Gemfile (gem "name"), composer.json (require keys).
Languages derive deterministically from which manifests exist: python (any python
manifest), javascript + node (package.json), go (go.mod), rust (Cargo.toml),
ruby (Gemfile), php (composer.json).

Stack-pack matching consumes this output: a pack applies when one of its match
tokens equals (case-insensitively) a detected dependency name or language.
Unparseable manifests warn on stderr and are skipped — the JSON on stdout stays valid.
"""
from __future__ import annotations
import argparse, json, re, sys, tomllib
from pathlib import Path

# Leading package-name token of a requirement spec: everything before any of
# ==, >=, <=, ~=, !=, [, ; (or any other non-name character).
_REQ_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_GEM_RE = re.compile(r"""^\s*gem\s+["']([^"']+)["']""")


def _req_name(spec: str) -> str | None:
    m = _REQ_NAME_RE.match(spec.strip())
    return m.group(0) if m else None


def detect(root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Scan the repo root's manifests. Returns ({"dependencies", "languages"}, warnings)."""
    deps: set[str] = set()
    langs: set[str] = set()
    warnings: list[str] = []

    def load_json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            warnings.append(f"{path.name}: skipped ({e})")
            return {}
        return data if isinstance(data, dict) else {}

    def load_toml(path: Path) -> dict:
        try:
            return tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            warnings.append(f"{path.name}: skipped ({e})")
            return {}

    def read_lines(path: Path) -> list[str]:
        try:
            return path.read_text().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            warnings.append(f"{path.name}: skipped ({e})")
            return []

    pj = root / "package.json"
    if pj.is_file():
        langs.update(("javascript", "node"))
        data = load_json(pj)
        for key in ("dependencies", "devDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                deps.update(section)

    pp = root / "pyproject.toml"
    if pp.is_file():
        langs.add("python")
        data = load_toml(pp)
        project_deps = (data.get("project") or {}).get("dependencies") or []
        for spec in project_deps:
            if isinstance(spec, str) and (name := _req_name(spec)):
                deps.add(name)
        poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
        if isinstance(poetry, dict):
            # tool.poetry.dependencies.python pins the interpreter, not a dependency
            deps.update(k for k in poetry if k.lower() != "python")

    for req_file in sorted(root.glob("requirements*.txt")):
        langs.add("python")
        for line in read_lines(req_file):
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue  # comments and pip options (-r, -e, --index-url, …)
            if name := _req_name(line):
                deps.add(name)

    gm = root / "go.mod"
    if gm.is_file():
        langs.add("go")
        in_block = False
        for raw in read_lines(gm):
            line = raw.split("//", 1)[0].strip()
            if not line:
                continue
            if in_block:
                if line == ")":
                    in_block = False
                else:
                    deps.add(line.split()[0])
            elif line.startswith("require"):
                rest = line[len("require"):].strip()
                if rest == "(" or not rest:
                    in_block = True
                else:
                    deps.add(rest.split()[0])

    ct = root / "Cargo.toml"
    if ct.is_file():
        langs.add("rust")
        data = load_toml(ct)
        section = data.get("dependencies")
        if isinstance(section, dict):
            deps.update(section)

    gf = root / "Gemfile"
    if gf.is_file():
        langs.add("ruby")
        for line in read_lines(gf):
            if m := _GEM_RE.match(line):
                deps.add(m.group(1))

    cj = root / "composer.json"
    if cj.is_file():
        langs.add("php")
        data = load_json(cj)
        section = data.get("require")
        if isinstance(section, dict):
            deps.update(section)

    return {"dependencies": sorted(deps), "languages": sorted(langs)}, warnings


# --- Self-test -----------------------------------------------------------------

def _run_self_test() -> int:
    import tempfile
    failures = 0

    def case(name: str, files: dict[str, str], want_deps: set[str], want_langs: set[str],
             want_warnings: int = 0) -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in files.items():
                (root / rel).write_text(content)
            result, warnings = detect(root)
            ok = (set(result["dependencies"]) == want_deps
                  and set(result["languages"]) == want_langs
                  and result["dependencies"] == sorted(result["dependencies"])
                  and result["languages"] == sorted(result["languages"])
                  and len(warnings) == want_warnings)
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {result}"
                  f"{' warnings=' + repr(warnings) if warnings else ''}")
            if not ok:
                failures += 1

    case("node app (deps + devDeps)",
         {"package.json": json.dumps({
             "dependencies": {"vue": "^3.4.0", "@vue/runtime-core": "^3.4.0"},
             "devDependencies": {"vite": "^5.0.0"}})},
         {"vue", "@vue/runtime-core", "vite"}, {"javascript", "node"})

    case("pyproject (PEP 621 + poetry, interpreter pin excluded)",
         {"pyproject.toml": (
             '[project]\nname = "x"\nversion = "0"\n'
             'dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.23", "pydantic==2.7.0"]\n'
             '[tool.poetry.dependencies]\npython = "^3.11"\nhttpx = "^0.27"\n')},
         {"fastapi", "uvicorn", "pydantic", "httpx"}, {"python"})

    case("requirements*.txt (comments, options, extras, markers)",
         {"requirements.txt": ("# pinned\nrequests==2.32.0\nflask>=3.0\ncelery[redis]~=5.3\n"
                               "-r requirements-dev.txt\ngunicorn ; python_version >= \"3.10\"\n"),
          "requirements-dev.txt": "pytest!=8.0.0\n"},
         {"requests", "flask", "celery", "gunicorn", "pytest"}, {"python"})

    case("go.mod (block + single require, // indirect)",
         {"go.mod": ("module example.com/app\n\ngo 1.22\n\n"
                     "require (\n\tgithub.com/gin-gonic/gin v1.9.1\n"
                     "\tgolang.org/x/sync v0.6.0 // indirect\n)\n\n"
                     "require gopkg.in/yaml.v3 v3.0.1\n")},
         {"github.com/gin-gonic/gin", "golang.org/x/sync", "gopkg.in/yaml.v3"}, {"go"})

    case("Cargo.toml ([dependencies] only, dev-dependencies excluded)",
         {"Cargo.toml": ('[package]\nname = "x"\nversion = "0.1.0"\n'
                         '[dependencies]\nserde = "1"\n'
                         'tokio = { version = "1", features = ["full"] }\n'
                         '[dev-dependencies]\ncriterion = "0.5"\n')},
         {"serde", "tokio"}, {"rust"})

    case("Gemfile (double and single quotes)",
         {"Gemfile": 'source "https://rubygems.org"\ngem "rails", "~> 7.1"\ngem \'puma\'\n'},
         {"rails", "puma"}, {"ruby"})

    case("composer.json (require keys, as-is)",
         {"composer.json": json.dumps({"require": {"php": ">=8.2", "laravel/framework": "^11.0"}})},
         {"php", "laravel/framework"}, {"php"})

    case("polyglot repo unions manifests",
         {"package.json": json.dumps({"dependencies": {"react": "^18"}}),
          "requirements.txt": "django==5.0\n"},
         {"react", "django"}, {"javascript", "node", "python"})

    case("empty repo", {}, set(), set())

    case("malformed manifest warns, others still scanned",
         {"package.json": "{nope",
          "Gemfile": 'gem "sinatra"\n'},
         {"sinatra"}, {"javascript", "node", "ruby"}, want_warnings=1)

    print(f"\n{'all' if not failures else str(failures) + ' FAILED of'} self-test cases"
          f"{' passed' if not failures else ''}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", type=Path, metavar="repo-root")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    if not args.root:
        ap.error("provide a repo root or --self-test")
    if not args.root.is_dir():
        print(f"error: '{args.root}' is not a directory", file=sys.stderr)
        return 1
    result, warnings = detect(args.root)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
