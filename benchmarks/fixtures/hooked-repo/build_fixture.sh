#!/usr/bin/env bash
# Deterministically fabricate the hooked-repo guard fixture: a scratch git repo
# every guard probe runs against (benchmarks/probes/flow-guard-enforcement-matrix.py,
# Family A). The probes never mutate the real repo — all guard invocations run with
# cwd = the fixture built here.
#
# States fabricated (each one a documented probe surface):
#   - .karta/binders/hooked.json        HEAD-committed binder (guarded write target;
#                                          pins sme ["minimalism"] so auditor-dispatch
#                                          probes exercise the checklist-evidence gate)
#   - .karta/binders/archive/delivered.json  HEAD-committed archived binder
#   - .karta/binders/staged-only.json      staged (git add) but NOT committed — the
#                                          known HEAD-only gap: writes pass
#   - plans -> .karta/binders              symlink alias — the known BINDER_RE gap:
#                                          a write via plans/hooked.json passes
#   - .karta/sme/minimalism.md             a validator-clean stack pack
#   - refs/karta/hooked/item-beta/built    standing built ref with NO done/failed ref
#                                          (git update-ref), so the delivery Stop-gate
#                                          sees a stranded built-unmerged item.
#                                          Deliberate deviation from the card's step-1
#                                          spelling refs/karta/<slug>/built/<id>: that
#                                          namespace does not exist in karta —
#                                          guard_delivery_stop.py and karta-build use
#                                          refs/karta/<slug>/item-<id>/{built,done,failed}
#                                          (recorded in benchmarks/meta/card-errata-2026-07-17.md)
#
# GIT_AUTHOR_DATE/GIT_COMMITTER_DATE are pinned, so two builds of the same fixture
# version produce byte-identical HEAD shas (the probe self-test asserts this).
#
# Companion payloads live in payloads/*.json (committed stdin payload JSONs, also
# consumed by the flow-spec-contradictions A1 promise check). Any "cwd" value of
# "__FIXTURE__" in a payload is a placeholder the consumer replaces with the
# absolute path of the fixture repo this script built.
set -euo pipefail

dest="${1:?usage: build_fixture.sh <dest-dir>}"

export GIT_AUTHOR_DATE="2026-07-17T00:00:00+00:00"
export GIT_COMMITTER_DATE="2026-07-17T00:00:00+00:00"
export GIT_AUTHOR_NAME="karta-bench" GIT_AUTHOR_EMAIL="bench@karta.invalid"
export GIT_COMMITTER_NAME="karta-bench" GIT_COMMITTER_EMAIL="bench@karta.invalid"

git init -q -b main "$dest"
cd "$dest"
mkdir -p .karta/binders/archive .karta/sme src

cat > .karta/binders/hooked.json <<'EOF'
{"slug": "hooked", "sme": ["minimalism"], "work_items": [{"id": "alpha"}, {"id": "beta"}]}
EOF

cat > .karta/binders/archive/delivered.json <<'EOF'
{"slug": "delivered", "work_items": [{"id": "a"}]}
EOF

cat > .karta/sme/minimalism.md <<'EOF'
---
name: minimalism
description: Fixture pack — validator-clean, used by the guard enforcement matrix
match: ["python"]
---
## Review checklist
- [ ] min.1 — Fixture rule line for guard probes.
EOF

printf 'print("benign")\n' > src/app.py
ln -s .karta/binders plans

git add -A
git -c user.name=karta-bench -c user.email=bench@karta.invalid \
    commit -q -m "hooked-repo fixture seed"

# staged-but-uncommitted binder: present in the index, absent from HEAD.
printf '{"slug": "staged-only", "work_items": []}\n' > .karta/binders/staged-only.json
git add .karta/binders/staged-only.json

# stranded wave state: built with no done/failed, in the REAL ref namespace.
git update-ref refs/karta/hooked/item-beta/built "$(git rev-parse HEAD)"
