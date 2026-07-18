# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Single-writer composer for the shared fixture registry.

`benchmarks/fixtures/REGISTRY.json` is the one place every bench fixture is
recorded with a sha256 pin; a vector may only consume a fixture through this
registry, so an audit can later prove nothing changed unnoticed. This script is
the *sole* writer of that file. The six fixture-writing items of the
bench-probe-buildout binder each end their contract with a "registry manifest"
clause naming the exact fixture paths they create and the `used_by` list they
intend; those clauses are transcribed verbatim into MANIFEST below (data for
this item), and this script turns them into per-file registry entries.

Three modes:

  * append (default) — for every manifested fixture file present on the tree,
    compute sha256 and APPEND an entry {id, path, sha256, used_by} to
    REGISTRY.json. Append-only: existing entries are never edited, reordered, or
    removed, and a path already registered is skipped (so re-running is a no-op).
    The write is a text splice that leaves every pre-existing byte untouched.
  * --check — re-read REGISTRY.json and verify every listed fixture path exists
    (relative to the repo root) and its sha256 matches the file on disk; exit
    nonzero on any missing path or hash mismatch.
  * --self-test — drive append, duplicate-refusal, and mismatch detection against
    temporary registry copies (never the committed file). Its report format is the
    binder-declared `self-test-report-format` shared term, rendered verbatim here:
    "[PASS]/[FAIL] lines and an N/N checks passed summary"; exit 0 only when every
    check passed, nonzero otherwise.

Zero third-party dependencies (pure stdlib), so every invocation form behaves
identically:
  python3 benchmarks/fixtures/update_registry.py            # append
  python3 benchmarks/fixtures/update_registry.py --check     # verify
  python3 benchmarks/fixtures/update_registry.py --self-test # embedded fixtures
  uv run --script benchmarks/fixtures/update_registry.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# Repo root, derived from this file's own location (no absolute paths anywhere):
# benchmarks/fixtures/update_registry.py -> parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_REL = "benchmarks/fixtures/REGISTRY.json"

# The registry manifest, transcribed verbatim from the six fixture-writing items'
# contracts in .karta/binders/bench-probe-buildout.json. Each rule resolves to one
# or more fixture files on the tree; every resolved file becomes exactly one entry
# (a directory of payloads gets one entry per file). `used_by` is carried verbatim.
#   kind "file" — a single explicit path.
#   kind "glob" — every path matching a shell glob (sorted).
#   kind "tree" — every file under a directory, recursively (sorted).
MANIFEST: list[dict] = [
    # flow-guard-enforcement-matrix: the fixture builder ...
    {"kind": "file", "path": "benchmarks/fixtures/hooked-repo/build_fixture.sh",
     "used_by": ["flow-guard-enforcement-matrix"]},
    # ... plus every stdin payload JSON, additionally listing flow-spec-contradictions
    # (the contradiction probe's A1 promise check consumes the same payloads).
    {"kind": "glob", "path": "benchmarks/fixtures/hooked-repo/payloads/*.json",
     "used_by": ["flow-guard-enforcement-matrix", "flow-spec-contradictions"]},
    # flow-spec-contradictions: every file under the schema-contradictions dir.
    {"kind": "tree", "path": "benchmarks/fixtures/schema-contradictions",
     "used_by": ["flow-spec-contradictions"]},
    # dark-status-surface-probes: the state factory and its frozen grading anchors.
    {"kind": "file", "path": "benchmarks/fixtures/stranded-states/make_state.sh",
     "used_by": ["dark-status-surface-probes"]},
    {"kind": "file", "path": "benchmarks/fixtures/stranded-states/expected.json",
     "used_by": ["dark-status-surface-probes"]},
    # sec-untrusted-input-surfaces: every hostile payload file including expected.json.
    {"kind": "tree", "path": "benchmarks/fixtures/adversarial",
     "used_by": ["sec-untrusted-input-surfaces"]},
    # field-delivery-state-audit: the seeded-violation fixture builder.
    {"kind": "file", "path": "benchmarks/flow/fixtures/delivery-state/build_fixture.sh",
     "used_by": ["field-delivery-state-audit"]},
    # perf-delivery-telemetry: every file under the synthetic transcript fixture,
    # including binder.json.
    {"kind": "tree", "path": "benchmarks/perf/fixtures/miner-transcript",
     "used_by": ["perf-delivery-telemetry"]},
]


# --------------------------------------------------------------------------- #
# Core helpers
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def make_id(rel_path: str) -> str:
    """Deterministic, readable, unique id derived from a repo-relative path.

    The leading 'benchmarks/' is dropped and every run of non-alphanumeric
    characters becomes a single hyphen. Distinct paths yield distinct ids."""
    stem = rel_path
    if stem.startswith("benchmarks/"):
        stem = stem[len("benchmarks/"):]
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def resolve_manifest(root: Path, manifest: list[dict]) -> list[tuple[str, list[str]]]:
    """Resolve MANIFEST rules to a list of (repo-relative path, used_by), in
    manifest order with each rule's matches sorted. Raises on a missing explicit
    file (a manifested fixture that should exist on the assembled tree)."""
    resolved: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for rule in manifest:
        used_by = list(rule["used_by"])
        kind = rule["kind"]
        rel = rule["path"]
        matches: list[str] = []
        if kind == "file":
            if not (root / rel).is_file():
                raise FileNotFoundError(f"manifested fixture missing on tree: {rel}")
            matches = [rel]
        elif kind == "glob":
            base = rel.rsplit("/", 1)[0]
            pattern = rel[len(base) + 1:]
            matches = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in (root / base).glob(pattern) if p.is_file()
            )
            if not matches:
                raise FileNotFoundError(f"manifested glob matched no files: {rel}")
        elif kind == "tree":
            treeroot = root / rel
            if not treeroot.is_dir():
                raise FileNotFoundError(f"manifested fixture dir missing on tree: {rel}")
            matches = sorted(
                str(p.relative_to(root)).replace("\\", "/")
                for p in treeroot.rglob("*") if p.is_file()
            )
            if not matches:
                raise FileNotFoundError(f"manifested fixture dir is empty: {rel}")
        else:  # pragma: no cover - guarded by construction
            raise ValueError(f"unknown manifest rule kind: {kind!r}")
        for m in matches:
            if m in seen:
                raise ValueError(f"path claimed by two manifest rules: {m}")
            seen.add(m)
            resolved.append((m, used_by))
    return resolved


def find_fixtures_array(raw: str) -> tuple[int, int]:
    """Return (open_bracket_index, matching_close_bracket_index) of the top-level
    'fixtures' array, scanning with JSON string/escape awareness so brackets
    inside string values or nested `used_by` arrays are ignored."""
    key = raw.index('"fixtures"')
    open_idx = raw.index("[", key)
    depth = 0
    in_str = False
    esc = False
    i = open_idx
    while i < len(raw):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return open_idx, i
        i += 1
    raise ValueError("unterminated 'fixtures' array in registry")


def format_entry(entry: dict) -> str:
    """Serialize one entry as a 4-space-indented JSON object block, matching the
    registry's existing two-space-per-level layout."""
    body = json.dumps(entry, indent=2, ensure_ascii=True)
    return "\n".join("    " + line for line in body.splitlines())


def splice_entries(raw: str, new_entries: list[dict]) -> str:
    """Append `new_entries` into the 'fixtures' array of `raw`, preserving every
    pre-existing byte. Handles both an empty and a non-empty array."""
    if not new_entries:
        return raw
    open_idx, close_idx = find_fixtures_array(raw)
    blocks = ",\n".join(format_entry(e) for e in new_entries)
    if raw[open_idx + 1:close_idx].strip() == "":
        # Empty array: "fixtures": [] -> lay the entries out fresh.
        before = raw[:open_idx + 1]
        after = raw[close_idx:]
        return f"{before}\n{blocks}\n  {after}"
    # Non-empty: insert after the last existing entry, before the closing bracket's
    # indentation. rstrip only touches the trailing whitespace run before ']'.
    head = raw[:close_idx]
    tail = raw[close_idx:]
    stripped = head.rstrip()
    trailing_ws = head[len(stripped):]
    return f"{stripped},\n{blocks}{trailing_ws}{tail}"


def build_new_entries(
    root: Path, manifest: list[dict], existing: list[dict]
) -> list[dict]:
    """Compute the append set: one {id, path, sha256, used_by} entry per manifested
    fixture whose path is not already registered. Asserts id/path uniqueness."""
    existing_paths = {e["path"] for e in existing}
    existing_ids = {e["id"] for e in existing}
    out: list[dict] = []
    for rel, used_by in resolve_manifest(root, manifest):
        if rel in existing_paths:
            continue  # already registered — append-only, no duplicate
        entry_id = make_id(rel)
        if entry_id in existing_ids:
            raise ValueError(f"id collision for {rel}: {entry_id}")
        existing_ids.add(entry_id)
        existing_paths.add(rel)
        out.append({
            "id": entry_id,
            "path": rel,
            "sha256": sha256_file(root / rel),
            "used_by": list(used_by),
        })
    return out


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def do_append(root: Path, registry_path: Path, manifest: list[dict]) -> list[dict]:
    """Append manifested entries to the registry file in place. Returns the list
    of entries actually added (empty when everything was already registered)."""
    raw = registry_path.read_text(encoding="utf-8")
    data = json.loads(raw)
    added = build_new_entries(root, manifest, data.get("fixtures", []))
    if added:
        registry_path.write_text(splice_entries(raw, added), encoding="utf-8")
    return added


def do_check(root: Path, registry_path: Path) -> list[str]:
    """Verify every registered fixture exists and its sha256 matches. Returns a
    list of human-readable problems (empty == all good)."""
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for entry in data.get("fixtures", []):
        rel = entry.get("path", "<no-path>")
        f = root / rel
        if not f.is_file():
            problems.append(f"MISSING  {rel} (id {entry.get('id')})")
            continue
        actual = sha256_file(f)
        if actual != entry.get("sha256"):
            problems.append(
                f"MISMATCH {rel}: registry {entry.get('sha256')} != disk {actual}")
    return problems


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
def _run_self_test() -> int:
    import tempfile

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        reg = root / REGISTRY_REL
        reg.parent.mkdir(parents=True, exist_ok=True)

        # A pre-existing registry entry with a raw non-ASCII em dash in a value,
        # so byte-preservation of existing content is genuinely exercised.
        preexisting = (
            '{\n'
            '  "$comment": "seed \\u2014 keep me",\n'
            '  "fixtures": [\n'
            '    {\n'
            '      "id": "seed-fixture",\n'
            '      "path": "benchmarks/seed.txt",\n'
            '      "sha256": "PLACEHOLDER",\n'
            '      "source": "vendored — raw dash",\n'
            '      "used_by": [\n'
            '        "some-vector"\n'
            '      ]\n'
            '    }\n'
            '  ]\n'
            '}\n'
        )
        # Lay down the seed fixture and pin its real sha into the pre-existing entry.
        (root / "benchmarks").mkdir(parents=True, exist_ok=True)
        (root / "benchmarks" / "seed.txt").write_text("seed contents\n")
        preexisting = preexisting.replace(
            "PLACEHOLDER", sha256_file(root / "benchmarks" / "seed.txt"))
        reg.write_text(preexisting, encoding="utf-8")

        # A tiny synthetic manifest over temp fixture files: one explicit file, a
        # glob of two, and a tree of two (one nested) — exercising every rule kind.
        fx = root / "benchmarks" / "fixtures" / "demo"
        (fx / "payloads").mkdir(parents=True, exist_ok=True)
        (fx / "build.sh").write_text("#!/bin/sh\necho hi\n")
        (fx / "payloads" / "a.json").write_text('{"a":1}\n')
        (fx / "payloads" / "b.json").write_text('{"b":2}\n')
        tree = root / "benchmarks" / "fixtures" / "tree"
        (tree / "sub").mkdir(parents=True, exist_ok=True)
        (tree / "top.json").write_text('{"t":0}\n')
        (tree / "sub" / "deep.json").write_text('{"d":9}\n')
        manifest = [
            {"kind": "file", "path": "benchmarks/fixtures/demo/build.sh",
             "used_by": ["vec-one"]},
            {"kind": "glob", "path": "benchmarks/fixtures/demo/payloads/*.json",
             "used_by": ["vec-one", "vec-two"]},
            {"kind": "tree", "path": "benchmarks/fixtures/tree",
             "used_by": ["vec-three"]},
        ]

        # 1) append adds one entry per manifested file, with correct sha + used_by.
        added = do_append(root, reg, manifest)
        expected_paths = [
            "benchmarks/fixtures/demo/build.sh",
            "benchmarks/fixtures/demo/payloads/a.json",
            "benchmarks/fixtures/demo/payloads/b.json",
            "benchmarks/fixtures/tree/sub/deep.json",
            "benchmarks/fixtures/tree/top.json",
        ]
        got_paths = [e["path"] for e in added]
        check("append: one entry per manifested file", got_paths == expected_paths,
              f"{got_paths}")
        data = json.loads(reg.read_text())
        by_path = {e["path"]: e for e in data["fixtures"]}
        sha_ok = all(
            by_path[p]["sha256"] == sha256_file(root / p) for p in expected_paths)
        check("append: sha256 matches file on disk", sha_ok)
        check("append: used_by carried verbatim",
              by_path["benchmarks/fixtures/demo/payloads/a.json"]["used_by"]
              == ["vec-one", "vec-two"]
              and by_path["benchmarks/fixtures/demo/build.sh"]["used_by"] == ["vec-one"])
        check("append: entry keys are exactly id/path/sha256/used_by",
              all(list(by_path[p].keys()) == ["id", "path", "sha256", "used_by"]
                  for p in expected_paths))

        # 2) pre-existing entry preserved byte-for-byte (its whole block still present).
        after_raw = reg.read_text(encoding="utf-8")
        seed_block = (
            '    {\n'
            '      "id": "seed-fixture",\n'
            '      "path": "benchmarks/seed.txt",\n'
            f'      "sha256": "{sha256_file(root / "benchmarks" / "seed.txt")}",\n'
            '      "source": "vendored — raw dash",\n'
            '      "used_by": [\n'
            '        "some-vector"\n'
            '      ]\n'
            '    }'
        )
        check("append: pre-existing entry bytes preserved", seed_block in after_raw)
        check("append: raw em dash not re-escaped", "— raw dash" in after_raw)
        check("append: result parses as valid JSON",
              len(json.loads(after_raw)["fixtures"]) == 6)

        # 3) duplicate-refusal — re-running append adds nothing, no duplicate paths.
        added2 = do_append(root, reg, manifest)
        data2 = json.loads(reg.read_text())
        paths2 = [e["path"] for e in data2["fixtures"]]
        check("duplicate refusal: second append adds nothing", added2 == [])
        check("duplicate refusal: no duplicate paths",
              len(paths2) == len(set(paths2)))

        # 4) --check passes on the intact registry.
        check("check: clean registry reports no problems",
              do_check(root, reg) == [])

        # 5) mismatch detection — tamper a file, check must flag it.
        (fx / "payloads" / "a.json").write_text('{"a":999}\n')
        probs = do_check(root, reg)
        check("check: hash mismatch detected",
              any("MISMATCH" in p and "a.json" in p for p in probs))
        (fx / "payloads" / "a.json").write_text('{"a":1}\n')  # restore

        # 6) missing-path detection.
        (fx / "build.sh").unlink()
        probs_missing = do_check(root, reg)
        check("check: missing path detected",
              any("MISSING" in p and "build.sh" in p for p in probs_missing))

        # 7) empty-array append lays entries out cleanly.
        reg2 = root / "empty-registry.json"
        reg2.write_text('{\n  "$comment": "e",\n  "fixtures": []\n}\n')
        do_append(root, reg2, [
            {"kind": "file", "path": "benchmarks/fixtures/tree/top.json",
             "used_by": ["vec-three"]}])
        empty_data = json.loads(reg2.read_text())
        check("append: empty array populated cleanly",
              len(empty_data["fixtures"]) == 1
              and empty_data["fixtures"][0]["path"] == "benchmarks/fixtures/tree/top.json")

        # 8) make_id is injective on the real manifest's resolved paths.
        real_ids = [make_id(p) for p, _ in resolve_manifest(REPO_ROOT, MANIFEST)]
        check("make_id: unique across the real manifest",
              len(real_ids) == len(set(real_ids)), f"{len(real_ids)} paths")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, detail in results:
        tag = "[PASS]" if ok else "[FAIL]"
        extra = f"  ({detail})" if detail and not ok else ""
        print(f"{tag} {name}{extra}")
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compose and verify the shared fixture registry "
                    "(benchmarks/fixtures/REGISTRY.json).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="verify every registered fixture path and sha256")
    mode.add_argument("--self-test", action="store_true",
                      help="run embedded self-tests on temp copies")
    args = parser.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    registry_path = REPO_ROOT / REGISTRY_REL

    if args.check:
        problems = do_check(REPO_ROOT, registry_path)
        if problems:
            print("REGISTRY CHECK: FAIL")
            for p in problems:
                print(f"  {p}")
            return 1
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        print(f"REGISTRY CHECK: OK ({len(data.get('fixtures', []))} fixtures verified)")
        return 0

    # Default: append manifested entries.
    added = do_append(REPO_ROOT, registry_path, MANIFEST)
    if added:
        print(f"REGISTRY APPEND: added {len(added)} entr"
              f"{'y' if len(added) == 1 else 'ies'}")
        for e in added:
            print(f"  + {e['path']}  {e['sha256'][:12]}…  used_by={e['used_by']}")
    else:
        print("REGISTRY APPEND: nothing to add (all manifested fixtures already registered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
