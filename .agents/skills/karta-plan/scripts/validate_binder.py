# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validate a karta binder: schema + dependency-graph + opt-out checks.

Zero dependencies (pure stdlib), so every invocation form behaves identically —
nothing has to be provisioned before it runs:
  uv run --script validate_binder.py --binder <path>   # validate one binder, exit 0/1
  uv run --script validate_binder.py --self-test        # run embedded fixtures, exit 0/1
  python3 validate_binder.py --binder <path>            # also fine — no deps to install
"""
from __future__ import annotations
import argparse, json, posixpath, re, sys
from fnmatch import fnmatch
from itertools import combinations
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "binder-schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


# --- Minimal JSON-Schema checker (pure stdlib) --------------------------------
# karta owns its binder schema, so rather than depend on `jsonschema` we check
# against exactly the draft-2020-12 keywords binder-schema.json actually uses:
#   type (incl. union lists), required, properties, additionalProperties:false,
#   items, enum, const, pattern, minLength, minItems, oneOf, and local $ref.
# Keywords outside that subset are ignored — keep this in step with the schema.

def _type_ok(value, t: str) -> bool:
    if t == "object":  return isinstance(value, dict)
    if t == "array":   return isinstance(value, list)
    if t == "string":  return isinstance(value, str)
    if t == "boolean": return isinstance(value, bool)
    if t == "null":    return value is None
    if t == "integer": return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":  return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _resolve_ref(ref: str, root: dict) -> dict:
    # local refs only, e.g. "#/$defs/workItem"
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _check(value, schema: dict, root: dict, path: list, errors: list[str]) -> None:
    if "$ref" in schema:
        _check(value, _resolve_ref(schema["$ref"], root), root, path, errors)
        return

    loc = "/".join(str(p) for p in path) or "(root)"

    if "type" in schema:
        types = schema["type"]
        types = [types] if isinstance(types, str) else types
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"schema: {loc}: is not of type {' or '.join(types)}")
            return  # a wrong-typed value makes the deeper keyword checks noise

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"schema: {loc}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"schema: {loc}: {value!r} is not the allowed constant {schema['const']!r}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"schema: {loc}: string shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"schema: {loc}: {value!r} does not match pattern {schema['pattern']!r}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"schema: {loc}: array shorter than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                _check(item, schema["items"], root, path + [i], errors)

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"schema: {loc}: missing required property '{req}'")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"schema: {loc}: additional property '{key}' is not allowed")
        for key, subschema in props.items():
            if key in value:
                _check(value[key], subschema, root, path + [key], errors)

    if "oneOf" in schema:
        matched = 0
        for sub in schema["oneOf"]:
            branch: list[str] = []
            _check(value, sub, root, path, branch)
            if not branch:
                matched += 1
        if matched != 1:
            errors.append(
                f"schema: {loc}: matched {matched} of the oneOf branches (exactly 1 required)")


def _schema_errors(binder: dict) -> list[str]:
    schema = _load_schema()
    errors: list[str] = []
    _check(binder, schema, schema, [], errors)
    return sorted(errors)


def validate_binder(binder: dict) -> list[str]:
    """Return a list of human-readable errors; empty list == valid."""
    errors = _schema_errors(binder)
    if errors:
        return errors  # graph checks assume a schema-valid shape

    items = binder.get("work_items", [])
    ids = [it["id"] for it in items]
    if len(ids) != len(set(ids)):
        errors.append("graph: duplicate work-item id(s)")
    id_set = set(ids)
    for it in items:
        for dep in it.get("depends_on", []):
            if dep not in id_set:
                errors.append(f"graph: item '{it['id']}' depends_on unknown id '{dep}'")

    # cycle detection (DFS over depends_on)
    graph = {it["id"]: list(it.get("depends_on", [])) for it in items}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in graph}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue  # dangling already reported
            if color[nxt] == GRAY:
                cyc = " -> ".join(stack + [nxt])
                errors.append(f"graph: dependency cycle: {cyc}")
            elif color[nxt] == WHITE:
                visit(nxt, stack + [nxt])
        color[node] = BLACK

    for i in graph:
        if color[i] == WHITE:
            visit(i, [i])

    if not errors:
        errors.extend(_wave_collision(items))
    return errors


def _paths_overlap(a_paths: list[str], b_paths: list[str]) -> list[str]:
    """Do two `touches` lists name any common file? Beyond literal equality this
    normalizes `./` and redundant separators, expands a glob entry against concrete
    entries (fnmatch), and treats a directory-prefix of another path as an overlap.
    Glob-vs-glob is not expanded (rare; left to serialize/shared_resources)."""
    def is_glob(p: str) -> bool:
        return any(c in p for c in "*?[")

    hits: set[str] = set()
    for x in a_paths:
        nx, gx = posixpath.normpath(x.strip()), is_glob(x)
        for y in b_paths:
            ny, gy = posixpath.normpath(y.strip()), is_glob(y)
            if nx == ny:
                hits.add(nx)
            elif gx and not gy and fnmatch(ny, nx):
                hits.add(f"{x.strip()} ~ {y.strip()}")
            elif gy and not gx and fnmatch(nx, ny):
                hits.add(f"{x.strip()} ~ {y.strip()}")
            elif not gx and not gy and (ny.startswith(nx + "/") or nx.startswith(ny + "/")):
                hits.add(f"{x.strip()} ~ {y.strip()}")
    return sorted(hits)


def _wave_collision(items: list[dict]) -> list[str]:
    """Flag item pairs that can land in the SAME wave and both `touches` a file,
    without declaring serialize or a shared resource to order them. Items with a
    dependency path between them land in different waves, so they never collide."""
    deps = {it["id"]: set(it.get("depends_on", [])) for it in items}

    def reachable(start: str) -> set[str]:
        seen: set[str] = set()
        stack = list(deps.get(start, ()))
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(deps.get(n, ()))
        return seen

    trans = {i: reachable(i) for i in deps}
    by_id = {it["id"]: it for it in items}
    out: list[str] = []
    for a, b in combinations(list(deps), 2):
        if a in trans[b] or b in trans[a]:
            continue  # a dependency path sequences them into different waves
        overlap = _paths_overlap(by_id[a].get("touches", []), by_id[b].get("touches", []))
        if not overlap:
            continue
        if by_id[a].get("serialize") or by_id[b].get("serialize"):
            continue  # an explicit-serialize item never shares a build slot
        if set(by_id[a].get("shared_resources", [])) & set(by_id[b].get("shared_resources", [])):
            continue  # a co-declared shared resource serializes the whole pair, so no file is edited concurrently
        out.append(
            f"graph: items '{a}' and '{b}' can run in the same wave and both touch "
            f"{overlap}, but neither sets serialize nor shares a shared_resources entry"
        )
    return out


def opt_out_summary(binder: dict) -> list[str]:
    return [f"{it['id']}: {it['oracle']['reason']}"
            for it in binder.get("work_items", [])
            if isinstance(it.get("oracle"), dict) and it["oracle"].get("opt_out")]


def _run_self_test() -> int:
    valid = json.loads((SCHEMA_PATH.parent / "example-binder.json").read_text())
    cyclic = {
        "slug": "c", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "depends_on": ["b"], "oracle": {"type": "unit"}},
            {"id": "b", "title": "B", "depends_on": ["a"], "oracle": {"type": "unit"}},
        ],
    }
    dangling = {
        "slug": "d", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "depends_on": ["ghost"], "oracle": {"type": "unit"}}],
    }
    no_oracle = {
        "slug": "n", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A"}],
    }
    optout_no_reason = {
        "slug": "o", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "oracle": {"opt_out": True}}],
    }
    _u = {"type": "unit"}
    collide = {
        "slug": "collide", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/models.py"], "oracle": _u},
            {"id": "b", "title": "B", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_serialize = {
        "slug": "collide-ser", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/models.py"], "serialize": True, "oracle": _u},
            {"id": "b", "title": "B", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_dep = {
        "slug": "collide-dep", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/models.py"], "depends_on": ["b"], "oracle": _u},
            {"id": "b", "title": "B", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_shared = {
        "slug": "collide-shared", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["db/x.sql"], "shared_resources": ["db/schema"], "oracle": _u},
            {"id": "b", "title": "B", "touches": ["db/x.sql"], "shared_resources": ["db/schema"], "oracle": _u},
        ],
    }
    collide_glob = {
        "slug": "collide-glob", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/*.py"], "oracle": _u},
            {"id": "b", "title": "B", "touches": ["./app/models.py"], "oracle": _u},
        ],
    }
    no_collide = {
        "slug": "no-collide", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/a.py"], "oracle": _u},
            {"id": "b", "title": "B", "touches": ["app/b.py"], "oracle": _u},
        ],
    }
    collide_transitive = {
        "slug": "collide-trans", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "touches": ["app/x.py"], "depends_on": ["b"], "oracle": _u},
            {"id": "b", "title": "B", "depends_on": ["c"], "oracle": _u},
            {"id": "c", "title": "C", "touches": ["app/x.py"], "oracle": _u},
        ],
    }
    sme_valid = {
        "slug": "sme-ok", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["angular", "python-fastapi"],
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    sme_not_array = {
        "slug": "sme-bad", "motivation": "x", "scope": {"included": ["x"]},
        "sme": "angular",
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    sme_bad_id = {
        "slug": "sme-badid", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["Angular_Expert"],
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    bad_estimate = {
        "slug": "bad-est", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "estimate": "XL", "oracle": _u}],
    }
    unknown_top_key = {
        "slug": "extra", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
        "surprise": True,
    }
    empty_work_items = {
        "slug": "empty", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [],
    }
    bad_slug = {
        "slug": "Bad_Slug", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    cases = [
        ("valid example", valid, True),
        ("binder with sme packs", sme_valid, True),
        ("sme not an array", sme_not_array, False),
        ("sme id bad pattern", sme_bad_id, False),
        ("bad estimate enum", bad_estimate, False),
        ("unknown top-level property", unknown_top_key, False),
        ("empty work_items", empty_work_items, False),
        ("bad slug pattern", bad_slug, False),
        ("cyclic deps", cyclic, False),
        ("dangling dep", dangling, False),
        ("missing oracle", no_oracle, False),
        ("opt-out without reason", optout_no_reason, False),
        ("same-wave file collision", collide, False),
        ("file collision but serialized", collide_serialize, True),
        ("file overlap across a dependency edge", collide_dep, True),
        ("file overlap with shared resource", collide_shared, True),
        ("glob/normalized same-wave collision", collide_glob, False),
        ("same-wave different files", no_collide, True),
        ("file overlap across a transitive edge", collide_transitive, True),
    ]
    failures = 0
    for name, binder, should_pass in cases:
        errs = validate_binder(binder)
        passed = not errs
        ok = passed == should_pass
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: "
              f"{'valid' if passed else 'invalid (' + '; '.join(errs) + ')'}")
        if not ok:
            failures += 1
    # opt-out summary must be detected on the valid example
    summ = opt_out_summary(valid)
    ok = len(summ) == 1
    print(f"[{'PASS' if ok else 'FAIL'}] opt-out summary on example: {summ}")
    failures += 0 if ok else 1
    print(f"\n{len(cases) + 1 - failures}/{len(cases) + 1} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--binder", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    if not args.binder:
        ap.error("provide --binder <path> or --self-test")
    binder = json.loads(args.binder.read_text())
    errs = validate_binder(binder)
    if errs:
        print("INVALID:")
        for e in errs:
            print(f"  - {e}")
        return 1
    summ = opt_out_summary(binder)
    print(f"VALID. {len(binder['work_items'])} work items; {len(summ)} opted out of acceptance checks.")
    for s in summ:
        print(f"  opt-out: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
