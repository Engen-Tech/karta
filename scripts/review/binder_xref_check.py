#!/usr/bin/env python3
"""Cross-reference checker for a karta binder's internal prose consistency.

Not a karta feature — a local instrument, written after three hand-patch rounds on
watch-column-fidelity introduced five defects of exactly these shapes. Each check
below exists because a real review finding matched it.
"""
import json, re, sys, pathlib

P = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                 else '/mnt/agent-storage/vader/src/karta/.karta/binders/watch-column-fidelity.json')
b = json.loads(P.read_text(encoding='utf-8'))
items = {i['id']: i for i in b['work_items']}
fails, checked = [], 0


def check(name, ok, detail=""):
    global checked
    checked += 1
    if not ok:
        fails.append(f"[FAIL] {name}: {detail}")


def blob(o):
    return json.dumps(o, ensure_ascii=False)


# 1. shared_terms: every listed item must actually be able to render the phrase
#    (it must declare at least one file it shares with another listed item).
for t in b.get('shared_terms', []):
    listed = t['items']
    check(f"shared_term:{t['id']}:items-exist",
          all(i in items for i in listed),
          f"unknown item(s) {[i for i in listed if i not in items]}")
    for i in listed:
        if i in items:
            check(f"shared_term:{t['id']}:{i}:has-touches",
                  bool(items[i].get('touches')), "listed item declares no touches")

# 2. No prose anywhere may state a count of a shared term's items that disagrees
#    with the list. This is the "all four" vs five-item defect, twice.
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10}
for t in b.get('shared_terms', []):
    n = len(t['items'])
    body = blob(b)
    for word, val in WORDNUM.items():
        for m in re.finditer(rf"clears? (?:the term for )?all {word}\b", body):
            check(f"shared_term:{t['id']}:count-prose", val == n,
                  f"prose says 'all {word}' but the term lists {n} items")

# 3. Every item whose contract says it writes the harness must declare it.
HARNESS = "skills/karta-status/scripts/measure_page.py"
for iid, item in items.items():
    writes = "--check group into" in blob(item) or "group is this item's to write" in blob(item)
    if writes:
        check(f"{iid}:declares-harness", HARNESS in (item.get('touches') or []),
              "contract says it writes the harness; touches does not declare it")
        check(f"{iid}:harness-mirrors",
              sum(1 for p in (item.get('touches') or []) if p.endswith("measure_page.py")) == 3,
              "harness declared without both Codex mirrors")

# 4. env_contract must not give two different port instructions for the harness.
ip = " ".join((b.get('env_contract') or {}).get('isolation_params') or [])
same_port = re.search(r"harness[^.]*same port", ip) or re.search(r"must be given the same port", ip)
own_port = "its own free port" in ip or "--port 0" in ip
check("env:port-instruction-single", not (same_port and own_port),
      "isolation_params tells the harness both to reuse the env port and to use its own")

# 5. A width named in a run matrix may not also be named as unshown.
mat = re.search(r"the run is taken at ([0-9,\s]+(?:and\s*)?[0-9]+)", blob(b))
if mat:
    widths = set(re.findall(r"\d{3,4}", mat.group(1)))
    for m in re.finditer(r"any width (?:above|below) (\d{3,4})", blob(b)):
        bound = int(m.group(1))
        over = sorted(w for w in widths if int(w) > bound)
        check("matrix:unshown-consistent", not over,
              f"matrix measures {over} but prose calls any width above {bound} unshown")

# 6. An item may not claim a reading is uncovered by other items when another
#    item's assertions pin the same number.
for iid, item in items.items():
    for n, a in enumerate(item['oracle'].get('assertions', [])):
        if re.search(r"no (?:other|earlier) (?:item|group)'?s? group (?:covers|pins)", a) or \
           re.search(r"pins what no earlier group pins", a):
            claimed = set(re.findall(r"\b(\d{3,4})\b", a))
            for oid, other in items.items():
                if oid == iid:
                    continue
                oblob = " ".join(other['oracle'].get('assertions', []))
                clash = sorted(w for w in claimed if re.search(rf"\b{w}\b", oblob))
                check(f"{iid}:{n}:novelty-claim", not clash,
                      f"claims {clash} uncovered, but {oid} names the same width(s)")

# 7. Fact traces must resolve (the repo's own checker proves reference, not range).
for f in b.get('token_manifest', {}).get('design_fact_table', []):
    for tr in (f.get('traced_by') or []):
        iid, _, idx = tr.partition(':')
        check(f"fact:{f['id']}:{tr}",
              iid in items and idx.isdigit()
              and int(idx) < len(items[iid]['oracle'].get('assertions', [])),
              "trace does not resolve")

# 8. depends_on must name real items and form no cycle.
for iid, item in items.items():
    for d in item.get('depends_on', []):
        check(f"{iid}:dep:{d}", d in items, "depends on an item that does not exist")

print(f"{checked} cross-reference checks run")
for f in fails:
    print(f)
print("XREF: PASS" if not fails else f"XREF: {len(fails)} FAILURE(S)")
sys.exit(1 if fails else 0)
