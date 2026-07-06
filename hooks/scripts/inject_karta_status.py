#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SessionStart hook: inject a short karta status summary as session context.

Zero dependencies (pure stdlib). The harness invokes this on SessionStart with the
hook payload JSON on stdin; whatever this script prints to stdout becomes context
the model sees. When `<cwd>/.karta/binders/*.json` exists it emits at most one
short line per binder (slug, item count, pinned packs), preferring the
karta-status derivation script (`skills/karta-status/scripts/karta_next.py
--json`, resolved via CLAUDE_PLUGIN_ROOT with a fallback to this script's own
plugin root) for live status and the next action, and degrading to a static
binder summary when it is not invocable. Output stays under 10 lines; silence
when there are no binders. Always exits 0 — a status hint must never surface as
a session error.

  inject_karta_status.py              # hook mode: payload on stdin, exit 0
  inject_karta_status.py --self-test  # run embedded fixtures, exit 0/1
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

MAX_LINES = 10
STATUS_REL = Path("skills") / "karta-status" / "scripts" / "karta_next.py"


def _status_script() -> Path | None:
    roots: list[Path] = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        roots.append(Path(env))
    roots.append(Path(__file__).resolve().parent.parent.parent)  # <plugin root>/hooks/scripts/..
    for root in roots:
        cand = root / STATUS_REL
        if cand.is_file():
            return cand
    return None


def load_binders(binders_dir: Path) -> list[dict]:
    out: list[dict] = []
    if not binders_dir.is_dir():
        return out
    for p in sorted(binders_dir.glob("*.json")):
        try:
            doc = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("slug"), str):
            out.append(doc)
    return out


def derive_state(cwd: str) -> dict | None:
    """Run the karta-status engine headless; None when it is not invocable."""
    script = _status_script()
    if script is None:
        return None
    try:
        proc = subprocess.run([sys.executable, str(script), "--json"],
                              capture_output=True, text=True, cwd=cwd, timeout=15)
        if proc.returncode != 0:
            return None
        state = json.loads(proc.stdout)
        return state if isinstance(state, dict) else None
    except Exception:  # noqa: BLE001
        return None


def summarize(binders: list[dict], state: dict | None) -> list[str]:
    """At most one short line per binder, MAX_LINES total; empty when no binders."""
    if not binders:
        return []
    lines = [f"karta: {len(binders)} binder(s) in .karta/binders"]
    by_slug: dict = {}
    if state:
        by_slug = {b.get("slug"): b for b in state.get("binders", []) if isinstance(b, dict)}
    room = MAX_LINES - 1 - (1 if state else 0)  # header + optional next-action line
    shown = binders if len(binders) <= room else binders[:room - 1]
    for b in shown:
        slug = b["slug"]
        count = len(b.get("work_items") or [])
        packs = ", ".join(s for s in (b.get("sme") or []) if isinstance(s, str)) or "none"
        st = by_slug.get(slug)
        if st:
            items = st.get("items") or {}
            lines.append(f"  {slug} — {st.get('status', '?')}, "
                         f"{items.get('done', 0)}/{items.get('total', count)} items done, "
                         f"packs: {packs}")
        else:
            lines.append(f"  {slug} — {count} item(s), packs: {packs}")
    if len(binders) > len(shown):
        lines.append(f"  … and {len(binders) - len(shown)} more binder(s)")
    if state:
        na = state.get("next_action") or {}
        nxt = na.get("command") or na.get("human")
        if nxt:
            lines.append(f"  next: {nxt}")
    return lines[:MAX_LINES]


def _binder_fixture(slug: str, packs: list[str], items: int = 1) -> dict:
    return {"slug": slug, "motivation": "x", "scope": {"included": ["x"]}, "sme": packs,
            "work_items": [{"id": f"i{n}", "title": "T", "summary": "s",
                            "oracle": {"type": "unit"}} for n in range(items)]}


def _run_self_test() -> int:
    import tempfile
    checks: list[tuple[str, bool]] = []

    # silence when there are no binders
    checks.append(("no binders -> no lines", summarize([], None) == []))

    two = [_binder_fixture("s-a", ["minimalism"], 2), _binder_fixture("s-b", [], 1)]
    fake_state = {"binders": [
        {"slug": "s-a", "status": "in_flight", "items": {"total": 2, "done": 1}},
        {"slug": "s-b", "status": "not_started", "items": {"total": 1, "done": 0}}],
        "next_action": {"command": "karta-deliver s-a", "human": "resume s-a (1/2 done)"}}
    lines = summarize(two, fake_state)
    checks.append(("derived summary names slug + status", any("s-a — in_flight" in ln for ln in lines)))
    checks.append(("derived summary carries pinned packs", any("packs: minimalism" in ln for ln in lines)))
    checks.append(("derived summary ends on the next action",
                   lines[-1] == "  next: karta-deliver s-a"))

    static = summarize(two, None)
    checks.append(("static fallback: one line per binder + header", len(static) == 3))
    checks.append(("static fallback names item counts", any("s-a — 2 item(s)" in ln for ln in static)))

    many = [_binder_fixture(f"s-{n:02d}", [], 1) for n in range(12)]
    capped = summarize(many, fake_state)
    checks.append(("12 binders stay within the line budget", len(capped) <= MAX_LINES))
    checks.append(("overflow is summarized", any("more binder(s)" in ln for ln in capped)))

    with tempfile.TemporaryDirectory() as td:
        binders_dir = Path(td) / ".karta" / "binders"
        binders_dir.mkdir(parents=True)
        (binders_dir / "s-a.json").write_text(json.dumps(_binder_fixture("s-a", ["minimalism"])))
        (binders_dir / "broken.json").write_text("{ not json")
        (binders_dir / "not-a-binder.json").write_text(json.dumps(["array"]))
        loaded = load_binders(binders_dir)
        checks.append(("loader keeps binders, skips junk",
                       [b["slug"] for b in loaded] == ["s-a"]))
        # a non-dict JSON in the dir crashes the engine — the hook must degrade, not raise
        checks.append(("engine crash degrades to the static summary", derive_state(td) is None))
        static_e2e = summarize(loaded, derive_state(td))
        checks.append(("degraded summary still emits", any("s-a" in ln for ln in static_e2e)))

    with tempfile.TemporaryDirectory() as td:
        binders_dir = Path(td) / ".karta" / "binders"
        binders_dir.mkdir(parents=True)
        (binders_dir / "s-a.json").write_text(json.dumps(_binder_fixture("s-a", ["minimalism"])))
        # end-to-end: the real karta-status engine runs headless against the fixture dir
        state = derive_state(td)
        checks.append(("karta_next.py runs headless", isinstance(state, dict)))
        e2e = summarize(load_binders(binders_dir), state)
        checks.append(("end-to-end summary emits and stays capped",
                       0 < len(e2e) <= MAX_LINES and "s-a" in e2e[1]))

    failures = 0
    for name, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        failures += 0 if ok else 1
    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _run_self_test()
    try:
        payload = json.load(sys.stdin)
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
        binders = load_binders(Path(cwd) / ".karta" / "binders")
        if binders:
            for line in summarize(binders, derive_state(cwd)):
                print(line)
    except Exception:  # noqa: BLE001
        pass  # fail open and silent: a status hint must never surface as a session error
    return 0


if __name__ == "__main__":
    sys.exit(main())
