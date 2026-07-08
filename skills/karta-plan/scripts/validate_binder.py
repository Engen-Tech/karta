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

# `shared_terms` — an optional top-level array declaring canonical strings several
# work items must render byte-identically (the whole-binder consistency gate that
# check_shared_terms.py enforces at deliver time). Its shape lives here rather than in
# binder-schema.json because only validate_binder.py and check_shared_terms.py read the
# field; injecting it into the loaded schema at check time keeps the top-level
# additionalProperties:false from rejecting it while reusing the same JSON-schema checker
# for its shape. Cross-references (unique entry id, item ids that resolve) are checked in
# Python below, exactly as depends_on's duplicate/dangling checks are.
_SHARED_TERMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["id", "canonical", "items"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$"},
            "canonical": {"type": "string", "minLength": 1},
            "items": {"type": "array", "minItems": 2, "items": {"type": "string"}},
        },
    },
}


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
    schema.setdefault("properties", {})["shared_terms"] = _SHARED_TERMS_SCHEMA
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

    # shared_terms cross-references: entry ids unique across entries, and every listed
    # item id resolves to a real work item (dangling id -> error, mirroring depends_on).
    # Shape (kebab id, non-empty canonical, >=2 items) is already enforced by the schema.
    seen_term_ids: set[str] = set()
    for term in binder.get("shared_terms", []):
        tid = term.get("id")
        if tid in seen_term_ids:
            errors.append(f"shared_terms: duplicate entry id '{tid}'")
        seen_term_ids.add(tid)
        for ref in term.get("items", []):
            if ref not in id_set:
                errors.append(f"shared_terms: entry '{tid}' lists unknown work-item id '{ref}'")

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


def sme_warnings(binder: dict) -> list[str]:
    """Advisory (non-fatal) notes. An empty or absent `sme` means no stack packs were
    pinned — yet every binder should carry at least the always-on `minimalism` pack, so an
    empty `sme` almost always means the plan:sme matching step was skipped. Surfaced on every
    run so the omission can't pass unnoticed; never fails validation (a project may legitimately
    suppress the always-on pack), which is why it is a warning and not a schema error."""
    if binder.get("sme"):
        return []
    return ["no stack packs pinned (sme is empty) — every binder should carry at least the "
            "always-on 'minimalism' pack; confirm the plan:sme matching step ran"]


def shared_terms_warnings(binder: dict) -> list[str]:
    """Advisory (non-fatal): a shared_terms entry lists an item whose `touches` is empty, so
    the deliver-time check_shared_terms.py pass has no files to scan for that item — the
    declaration would be silently un-enforceable there. Not fatal (an item may declare its
    touched files later, or legitimately carry none), so it warns rather than errors."""
    by_id = {it["id"]: it for it in binder.get("work_items", [])}
    out: list[str] = []
    for term in binder.get("shared_terms", []):
        for ref in term.get("items", []):
            it = by_id.get(ref)
            if it is not None and not it.get("touches"):
                out.append(f"shared_terms entry '{term.get('id')}' lists item '{ref}' with empty "
                           "touches — the deliver-time check has no files to scan for it")
    return out


def cross_binder_errors(binders: list[dict],
                        archived: frozenset[str] = frozenset()) -> tuple[list[str], list[str]]:
    """Check the cross-binder `after` graph across a whole set of binders.

    Returns (errors, warnings). A dangling `after` ref (no binder with that slug) is a
    WARNING — the suggested order is recomputed over the binders that exist, so a stale edge
    surfaces but never fails the set. A cycle across `after` edges is an ERROR — a cycle has no
    valid order. A single binder, or binders with no `after`, produce nothing.

    `archived` is the slug set of delivered binders (`.karta/binders/archive/`): an `after`
    naming one is satisfied, not dangling; a live binder REUSING an archived slug draws a
    warning — the delivered history would be shadowed, so new work takes a fresh slug."""
    slugs = {b.get("slug") for b in binders}
    warnings: list[str] = []
    for s in sorted(slugs & archived):
        warnings.append(f"binder '{s}' reuses the slug of an archived (delivered) binder — "
                        "the delivered history is shadowed; plan new work under a fresh slug")
    graph: dict[str, list[str]] = {}
    for b in binders:
        slug = b.get("slug")
        resolved: list[str] = []
        for ref in b.get("after", []) or []:
            if ref in slugs:
                resolved.append(ref)
            elif ref not in archived:
                warnings.append(f"binder '{slug}' has a dangling after: '{ref}' (no such binder)")
        graph[slug] = resolved

    errors: list[str] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {s: WHITE for s in graph}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        for nxt in graph.get(node, []):
            if color.get(nxt) == GRAY:
                errors.append("after cycle: " + " -> ".join(stack + [nxt]))
            elif color.get(nxt) == WHITE:
                visit(nxt, stack + [nxt])
        color[node] = BLACK

    for s in sorted(graph):
        if color[s] == WHITE:
            visit(s, [s])
    return errors, sorted(set(warnings))


def _run_self_test() -> int:
    valid = json.loads((SCHEMA_PATH.parent / "example-binder.json").read_text())
    cyclic = {
        "slug": "c", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "depends_on": ["b"], "oracle": {"type": "unit"}},
            {"id": "b", "title": "B", "summary": "s", "depends_on": ["a"], "oracle": {"type": "unit"}},
        ],
    }
    dangling = {
        "slug": "d", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "depends_on": ["ghost"], "oracle": {"type": "unit"}}],
    }
    no_oracle = {
        "slug": "n", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s"}],
    }
    optout_no_reason = {
        "slug": "o", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": {"opt_out": True}}],
    }
    _u = {"type": "unit"}
    collide = {
        "slug": "collide", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/models.py"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_serialize = {
        "slug": "collide-ser", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/models.py"], "serialize": True, "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_dep = {
        "slug": "collide-dep", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/models.py"], "depends_on": ["b"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["app/models.py"], "oracle": _u},
        ],
    }
    collide_shared = {
        "slug": "collide-shared", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["db/x.sql"], "shared_resources": ["db/schema"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["db/x.sql"], "shared_resources": ["db/schema"], "oracle": _u},
        ],
    }
    collide_glob = {
        "slug": "collide-glob", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/*.py"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["./app/models.py"], "oracle": _u},
        ],
    }
    no_collide = {
        "slug": "no-collide", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/a.py"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "touches": ["app/b.py"], "oracle": _u},
        ],
    }
    collide_transitive = {
        "slug": "collide-trans", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [
            {"id": "a", "title": "A", "summary": "s", "touches": ["app/x.py"], "depends_on": ["b"], "oracle": _u},
            {"id": "b", "title": "B", "summary": "s", "depends_on": ["c"], "oracle": _u},
            {"id": "c", "title": "C", "summary": "s", "touches": ["app/x.py"], "oracle": _u},
        ],
    }
    sme_valid = {
        "slug": "sme-ok", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["angular", "python-fastapi"],
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    sme_not_array = {
        "slug": "sme-bad", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "sme": "angular",
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    sme_bad_id = {
        "slug": "sme-badid", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "sme": ["Angular_Expert"],
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    bad_estimate = {
        "slug": "bad-est", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "estimate": "XL", "oracle": _u}],
    }
    unknown_top_key = {
        "slug": "extra", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
        "surprise": True,
    }
    empty_work_items = {
        "slug": "empty", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [],
    }
    bad_slug = {
        "slug": "Bad_Slug", "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    missing_item_summary = {
        "slug": "no-item-summary", "title": "T", "summary": "S", "motivation": "x",
        "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "oracle": _u}],
    }
    missing_binder_summary = {
        "slug": "no-binder-summary", "title": "T", "motivation": "x",
        "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    missing_binder_title = {
        "slug": "no-binder-title", "summary": "S", "motivation": "x",
        "scope": {"included": ["x"]},
        "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}],
    }
    # shared_terms: two items touching distinct files (so no wave collision) plus a term.
    _st_items = [
        {"id": "a", "title": "A", "summary": "s", "touches": ["app/a.py"], "oracle": _u},
        {"id": "b", "title": "B", "summary": "s", "touches": ["app/b.py"], "oracle": _u},
    ]
    def _st_binder(slug, terms, items=None):
        return {
            "slug": slug, "title": "T", "summary": "S", "motivation": "x",
            "scope": {"included": ["x"]},
            "work_items": items if items is not None else [dict(i) for i in _st_items],
            "shared_terms": terms,
        }
    shared_terms_ok = _st_binder(
        "st-ok", [{"id": "shadow-warning", "canonical": "reuses an archived slug", "items": ["a", "b"]}])
    shared_terms_dangling = _st_binder(
        "st-dangling", [{"id": "t", "canonical": "c", "items": ["a", "ghost"]}])
    shared_terms_dup_id = _st_binder(
        "st-dup",
        [{"id": "t", "canonical": "c", "items": ["a", "b"]},
         {"id": "t", "canonical": "d", "items": ["a", "b"]}])
    shared_terms_empty_canonical = _st_binder(
        "st-empty-canon", [{"id": "t", "canonical": "", "items": ["a", "b"]}])
    shared_terms_single_item = _st_binder(
        "st-single", [{"id": "t", "canonical": "c", "items": ["a"]}])
    cases = [
        ("valid example", valid, True),
        ("well-formed shared_terms", shared_terms_ok, True),
        ("shared_terms dangling item id", shared_terms_dangling, False),
        ("shared_terms duplicate entry id", shared_terms_dup_id, False),
        ("shared_terms empty canonical", shared_terms_empty_canonical, False),
        ("shared_terms single-item entry", shared_terms_single_item, False),
        ("binder with sme packs", sme_valid, True),
        ("sme not an array", sme_not_array, False),
        ("sme id bad pattern", sme_bad_id, False),
        ("bad estimate enum", bad_estimate, False),
        ("unknown top-level property", unknown_top_key, False),
        ("empty work_items", empty_work_items, False),
        ("bad slug pattern", bad_slug, False),
        ("work item missing summary", missing_item_summary, False),
        ("binder missing summary", missing_binder_summary, False),
        ("binder missing title", missing_binder_title, False),
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
    # sme advisory: warns only when no stack packs are pinned
    ok = len(sme_warnings(cyclic)) == 1 and len(sme_warnings(sme_valid)) == 0
    print(f"[{'PASS' if ok else 'FAIL'}] sme warning fires only on empty sme")
    failures += 0 if ok else 1
    # shared_terms advisory: warns for a listed item with empty touches; silent otherwise.
    # The entry is otherwise well-formed, so the binder still validates (warning != error).
    st_empty_touches = _st_binder(
        "st-warn", [{"id": "t", "canonical": "c", "items": ["a", "b"]}],
        items=[{"id": "a", "title": "A", "summary": "s", "touches": ["app/a.py"], "oracle": _u},
               {"id": "b", "title": "B", "summary": "s", "oracle": _u}])
    ok = (not validate_binder(st_empty_touches)
          and len(shared_terms_warnings(st_empty_touches)) == 1
          and len(shared_terms_warnings(shared_terms_ok)) == 0)
    print(f"[{'PASS' if ok else 'FAIL'}] shared_terms warns on a listed item with empty touches")
    failures += 0 if ok else 1

    # cross-binder `after` graph (resolution + acyclicity)
    cb_new   = {"slug": "s-new",   "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}
    cb_edit  = {"slug": "s-edit",  "after": ["s-new"], "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}
    cb_del   = {"slug": "s-del",   "after": ["s-edit"], "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}
    cb_dangle = {"slug": "s-x",    "after": ["ghost"], "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                 "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}
    cb_cyc_a = {"slug": "ca", "after": ["cb"], "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}
    cb_cyc_b = {"slug": "cb", "after": ["ca"], "title": "T", "summary": "S", "motivation": "x", "scope": {"included": ["x"]},
                "work_items": [{"id": "a", "title": "A", "summary": "s", "oracle": _u}]}

    cb_cases = [
        ("clean after-chain", [cb_new, cb_edit, cb_del], [], 0, frozenset()),   # no errors, no warnings
        ("dangling after-ref", [cb_dangle], [], 1, frozenset()),                # 1 warning, no error
        ("after cycle", [cb_cyc_a, cb_cyc_b], "cycle", 0, frozenset()),         # error present
        ("lone binder unchanged", [cb_new], [], 0, frozenset()),                # nothing flagged
        ("after -> archived slug is satisfied", [cb_dangle], [], 0,             # delivered predecessor
         frozenset({"ghost"})),
        ("live slug reusing an archived one warns", [cb_new], [], 1,            # shadowed history
         frozenset({"s-new"})),
    ]
    for name, binders, want_err, want_warn, archived in cb_cases:
        errs, warns = cross_binder_errors(binders, archived)
        if want_err == "cycle":
            ok = any("cycle" in e for e in errs)
        else:
            ok = (errs == []) and (len(warns) == want_warn)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: errors={errs} warnings={warns}")
        failures += 0 if ok else 1

    print(f"\n{len(cases) + 3 + len(cb_cases) - failures}/{len(cases) + 3 + len(cb_cases)} checks passed")
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
    if not args.binder.is_file():
        archived_twin = args.binder.resolve().parent / "archive" / args.binder.name
        if archived_twin.is_file():
            print(f"INVALID: binder not found at {args.binder} — it was already delivered. "
                  f"karta-deliver's end-of-life step archived it to {archived_twin}; "
                  "plan new work as a new binder with a fresh slug.")
        else:
            print(f"INVALID: binder file not found: {args.binder}")
        return 1
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
    for w in sme_warnings(binder):
        print(f"  warning: {w}")
    for w in shared_terms_warnings(binder):
        print(f"  warning: {w}")
    # cross-binder `after` graph, when the binder is one of a set on disk — including
    # delivered (archived) slugs, so an `after` naming one reads satisfied and a slug
    # reuse draws its warning even for a lone live binder.
    if args.binder:
        siblings = []
        for p in sorted(args.binder.resolve().parent.glob("*.json")):
            try:
                doc = json.loads(p.read_text())
                if isinstance(doc, dict) and "slug" in doc:
                    siblings.append(doc)
            except (OSError, json.JSONDecodeError):
                continue
        archived = set()
        archive_dir = args.binder.resolve().parent / "archive"
        if archive_dir.is_dir():
            for p in sorted(archive_dir.glob("*.json")):
                try:
                    doc = json.loads(p.read_text())
                    if isinstance(doc, dict) and isinstance(doc.get("slug"), str):
                        archived.add(doc["slug"])
                except (OSError, json.JSONDecodeError):
                    continue
        if len(siblings) > 1 or archived:
            cb_errs, cb_warns = cross_binder_errors(siblings, frozenset(archived))
            for w in cb_warns:
                print(f"  warning: {w}")
            if cb_errs:
                print("INVALID (cross-binder):")
                for e in cb_errs:
                    print(f"  - {e}")
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
