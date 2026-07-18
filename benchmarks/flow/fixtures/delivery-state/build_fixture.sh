#!/usr/bin/env bash
# Deterministically fabricate the delivery-state audit fixture: a scratch git repo
# seeding >=1 instance of EVERY reported violation class the four auditors detect
# (benchmarks/flow/lint_delivery_refs.py, audit_hygiene.py, check_markers.py,
# audit_binder_mutations.py, composed by run_all.py), PLUS the two pinned negative
# cases that must NOT flag. The auditors never touch a real repo — every detection
# run in --fixture-only / gate mode is graded against the repo built here.
#
# All GIT_*_DATE values are pinned, so two builds produce byte-identical shas. The
# recorded audit_timestamp used for STALE (>48h) classification is pinned by the
# probe (2026-07-20T00:00:00Z), never wall-clock now(): artifacts dated 2026-07-15
# read as stale; the discarded/in-flight slug dated 2026-07-19 reads as fresh.
#
# Seeded slugs (class -> auditor):
#   clean-ff            NEGATIVE 1  legit fast-forward landing, archived clean     -> 0 findings
#   discarded           NEGATIVE 2  integration never merged, binder still live    -> 0 findings
#   forged-done         FORGED-DONE-REF                              lint_delivery_refs
#   stranded-built      BUILT-WITHOUT-DONE (unpaired on archived)    lint_delivery_refs
#   wave-disorder       WAVE-TAG-DISORDER + WAVE-BASE-NON-ANCESTOR   lint_delivery_refs
#   accept-no-trailer   MISSING-KARTA-ACCEPT-TRAILER  (fixture-only) lint_delivery_refs
#   leftover-inprogress LEFTOVER-IN-PROGRESS-REF                     lint_delivery_refs
#   survivor-branch     SURVIVOR-POST-ARCHIVE                        lint_delivery_refs
#   complete-unarchived COMPLETE-UNARCHIVED                          lint_delivery_refs
#   stale-artifacts     STALE (worktree + karta stash >48h)          audit_hygiene
#   no-doctrine         MISSING-DOCTRINE-REF                         audit_hygiene
#   unmarked-commit     UNMARKED-WORK-COMMIT                         check_markers
#   dangling-marker     DANGLING-MARKER-ID                           check_markers
#   binder-mutations    RE-PLAN / ARCHIVE-MOVE / METADATA-RETROFIT / MANUAL-SURGERY
#                                                                    audit_binder_mutations
set -euo pipefail

dest="${1:?usage: build_fixture.sh <dest-dir>}"

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME="karta-bench" GIT_AUTHOR_EMAIL="bench@karta.invalid"
export GIT_COMMITTER_NAME="karta-bench" GIT_COMMITTER_EMAIL="bench@karta.invalid"

D_OLD="2026-07-15T00:00:00+00:00"   # >48h before the pinned audit_timestamp -> STALE
D_FRESH="2026-07-19T00:00:00+00:00" # <48h before -> fresh / in-flight
D_MAIN="2026-07-10T00:00:00+00:00"

git init -q -b main "$dest"
cd "$dest"
git config user.name karta-bench
git config user.email bench@karta.invalid

commit() { # commit <date> <message>   (stages everything first)
  git add -A
  GIT_AUTHOR_DATE="$1" GIT_COMMITTER_DATE="$1" git commit -q -m "$2"
}
binder() { # binder <path> <slug> <item-id...>
  local path="$1" slug="$2"; shift 2
  mkdir -p "$(dirname "$path")"
  local items="" first=1
  for it in "$@"; do
    [ $first -eq 1 ] && first=0 || items="$items, "
    items="$items{\"id\": \"$it\"}"
  done
  printf '{"slug": "%s", "work_items": [%s]}\n' "$slug" "$items" > "$path"
}

mkdir -p .karta/binders/archive
echo "root" > README.md
commit "$D_MAIN" "root"
ROOT=$(git rev-parse HEAD)

# ---------------------------------------------------------------------------
# clean-ff  (NEGATIVE 1): fast-forward landing, archived clean, markers present.
# ---------------------------------------------------------------------------
echo a > clean-ff-a.txt
commit "$D_MAIN" "[karta:item-a] clean-ff item a"
CF_A=$(git rev-parse HEAD)
git update-ref refs/tags/karta/clean-ff/wave-1-base "$ROOT"
git update-ref refs/tags/karta/clean-ff/wave-1 "$CF_A"
echo b > clean-ff-b.txt
commit "$D_MAIN" "[karta:item-b] clean-ff item b"
CF_B=$(git rev-parse HEAD)
git update-ref refs/tags/karta/clean-ff/wave-2-base "$CF_A"
git update-ref refs/tags/karta/clean-ff/wave-2 "$CF_B"
binder .karta/binders/archive/clean-ff.json clean-ff a b
commit "$D_MAIN" "chore(karta): archive binder clean-ff — delivered"
for it in a b; do
  git update-ref "refs/karta/clean-ff/item-$it/built" "$CF_A"
  git update-ref "refs/karta/clean-ff/item-$it/done" "$CF_A"
done
git update-ref refs/karta/clean-ff/item-b/done "$CF_B"

# ---------------------------------------------------------------------------
# complete-unarchived: all items done + integration-merged, binder still LIVE.
# ---------------------------------------------------------------------------
echo cu > complete-unarchived.txt
commit "$D_MAIN" "[karta:item-only] complete-unarchived work"
CU=$(git rev-parse HEAD)
binder .karta/binders/complete-unarchived.json complete-unarchived only
commit "$D_MAIN" "[karta:item-only] register complete-unarchived binder"
CU_B=$(git rev-parse HEAD)
git update-ref refs/karta/complete-unarchived/item-only/built "$CU"
git update-ref refs/karta/complete-unarchived/item-only/done "$CU"
git update-ref refs/tags/karta/complete-unarchived/wave-1-base "$CU"
git update-ref refs/tags/karta/complete-unarchived/wave-1 "$CU_B"

# tip of main so far, for reachability of the "landed" done refs above.
MAIN_TIP=$(git rev-parse HEAD)

# ---------------------------------------------------------------------------
# forged-done: archived binder, done ref points at a DANGLING commit (never
# merged / not an ancestor of main).
# ---------------------------------------------------------------------------
binder .karta/binders/archive/forged-done.json forged-done x
commit "$D_MAIN" "chore(karta): archive binder forged-done — delivered"
# a dangling commit built off ROOT but never merged onto main
DANGLE=$(GIT_AUTHOR_DATE="$D_MAIN" GIT_COMMITTER_DATE="$D_MAIN" \
  git commit-tree "$(git rev-parse "$ROOT^{tree}")" -p "$ROOT" -m "forged side commit")
git update-ref refs/karta/forged-done/item-x/built "$DANGLE"
git update-ref refs/karta/forged-done/item-x/done "$DANGLE"

# ---------------------------------------------------------------------------
# stranded-built: archived binder, built ref but NO done/failed (unpaired).
# ---------------------------------------------------------------------------
binder .karta/binders/archive/stranded-built.json stranded-built y
commit "$D_MAIN" "chore(karta): archive binder stranded-built — delivered"
git update-ref refs/karta/stranded-built/item-y/built "$MAIN_TIP"

# ---------------------------------------------------------------------------
# wave-disorder: wave-1 is a DESCENDANT of wave-2 (reversed order) and
# wave-2-base is NOT an ancestor of wave-2.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/wave-disorder.json wave-disorder p
commit "$D_MAIN" "chore(karta): archive binder wave-disorder — delivered"
WD_LOW=$ROOT
WD_HIGH=$(git rev-parse HEAD)
git update-ref refs/tags/karta/wave-disorder/wave-1-base "$WD_HIGH"
git update-ref refs/tags/karta/wave-disorder/wave-1 "$WD_HIGH"
git update-ref refs/tags/karta/wave-disorder/wave-2-base "$WD_HIGH"
# wave-2 points LOWER than wave-1 -> disorder; wave-2-base(HIGH) not ancestor of wave-2(LOW)
git update-ref refs/tags/karta/wave-disorder/wave-2 "$WD_LOW"
git update-ref refs/karta/wave-disorder/item-p/built "$WD_HIGH"
git update-ref refs/karta/wave-disorder/item-p/done "$WD_HIGH"

# ---------------------------------------------------------------------------
# accept-no-trailer: an accepted ref whose landing commit lacks Karta-Accept.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/accept-no-trailer.json accept-no-trailer z
commit "$D_MAIN" "chore(karta): archive binder accept-no-trailer — delivered"
ACC=$(git rev-parse HEAD)
git update-ref refs/karta/accept-no-trailer/item-z/built "$ACC"
git update-ref refs/karta/accept-no-trailer/item-z/done "$ACC"
git update-ref refs/karta/accept-no-trailer/item-z/accepted "$ACC"

# ---------------------------------------------------------------------------
# leftover-inprogress: a stray refs/karta/**/in-progress ref.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/leftover-inprogress.json leftover-inprogress w
commit "$D_MAIN" "chore(karta): archive binder leftover-inprogress — delivered"
LIP=$(git rev-parse HEAD)
git update-ref refs/karta/leftover-inprogress/item-w/built "$LIP"
git update-ref refs/karta/leftover-inprogress/item-w/done "$LIP"
git update-ref refs/karta/leftover-inprogress/item-w/in-progress "$LIP"

# ---------------------------------------------------------------------------
# survivor-branch: archived binder, but a live integration BRANCH survives.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/survivor-branch.json survivor-branch q
commit "$D_MAIN" "chore(karta): archive binder survivor-branch — delivered"
SB=$(git rev-parse HEAD)
git update-ref refs/karta/survivor-branch/item-q/built "$SB"
git update-ref refs/karta/survivor-branch/item-q/done "$SB"
git update-ref refs/heads/karta/survivor-branch/integration "$SB"

# ---------------------------------------------------------------------------
# unmarked-commit: rev-list done ^wave-base has a commit with no marker/trailer.
# ---------------------------------------------------------------------------
echo um1 > unmarked-1.txt
commit "$D_MAIN" "[karta:item-good] unmarked-commit good work"
UM_BASE_PARENT=$(git rev-parse HEAD~1)
UM_MARKED=$(git rev-parse HEAD)
echo um2 > unmarked-2.txt
commit "$D_MAIN" "just a plain refactor with no marker"
UM_DONE=$(git rev-parse HEAD)
binder .karta/binders/archive/unmarked-commit.json unmarked-commit good
commit "$D_MAIN" "chore(karta): archive binder unmarked-commit — delivered"
git update-ref refs/tags/karta/unmarked-commit/wave-1-base "$UM_BASE_PARENT"
git update-ref refs/karta/unmarked-commit/item-good/built "$UM_MARKED"
git update-ref refs/karta/unmarked-commit/item-good/done "$UM_DONE"

# ---------------------------------------------------------------------------
# dangling-marker: a work commit tags [karta:item-ghost] absent from the binder.
# ---------------------------------------------------------------------------
echo dm > dangling.txt
commit "$D_MAIN" "[karta:item-ghost] work for an id not in the binder"
DM_DONE=$(git rev-parse HEAD)
DM_BASE=$(git rev-parse HEAD~1)
binder .karta/binders/archive/dangling-marker.json dangling-marker real
commit "$D_MAIN" "chore(karta): archive binder dangling-marker — delivered"
git update-ref refs/tags/karta/dangling-marker/wave-1-base "$DM_BASE"
git update-ref refs/karta/dangling-marker/item-real/built "$DM_DONE"
git update-ref refs/karta/dangling-marker/item-real/done "$DM_DONE"

# ---------------------------------------------------------------------------
# binder-mutations: birth -> metadata-retrofit -> re-plan -> manual-surgery ->
# archive-move, so every mutation class has a post-birth commit.
# ---------------------------------------------------------------------------
binder .karta/binders/binder-mutations.json binder-mutations m1
commit "$D_MAIN" "chore(karta): plan binder-mutations binder"
# METADATA-RETROFIT: add a top-level key (shared_terms) additively.
python3 - <<'PY'
import json
p = ".karta/binders/binder-mutations.json"
d = json.load(open(p))
d["shared_terms"] = []
json.dump(d, open(p, "w"))
open(p, "a").write("\n")
PY
commit "$D_MAIN" "chore(karta): retrofit shared_terms onto binder-mutations"
# RE-PLAN: add a work item.
python3 - <<'PY'
import json
p = ".karta/binders/binder-mutations.json"
d = json.load(open(p))
d["work_items"].append({"id": "m2"})
json.dump(d, open(p, "w")); open(p, "a").write("\n")
PY
commit "$D_MAIN" "re-plan binder-mutations: add item m2"
# MANUAL-SURGERY: change an existing scalar field in place (title of an item).
python3 - <<'PY'
import json
p = ".karta/binders/binder-mutations.json"
d = json.load(open(p))
d["work_items"][0]["id"] = "m1"          # unchanged id
d["work_items"][0]["title"] = "hand-edited title"
json.dump(d, open(p, "w")); open(p, "a").write("\n")
PY
commit "$D_MAIN" "tweak binder-mutations item title"
# ARCHIVE-MOVE: git-mv to archive/, content identical.
git mv .karta/binders/binder-mutations.json .karta/binders/archive/binder-mutations.json
commit "$D_MAIN" "chore(karta): archive binder binder-mutations — delivered"
BM=$(git rev-parse HEAD)
git update-ref refs/karta/binder-mutations/item-m1/built "$BM"
git update-ref refs/karta/binder-mutations/item-m1/done "$BM"
git update-ref refs/karta/binder-mutations/item-m2/built "$BM"
git update-ref refs/karta/binder-mutations/item-m2/done "$BM"

# ---------------------------------------------------------------------------
# no-doctrine: archived binder with NO refs/karta/** and NO wave tags at all.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/no-doctrine.json no-doctrine n1
commit "$D_MAIN" "chore(karta): archive binder no-doctrine — delivered"

# ---------------------------------------------------------------------------
# discarded  (NEGATIVE 2): integration never merged, binder still LIVE, fresh.
# ---------------------------------------------------------------------------
binder .karta/binders/discarded.json discarded d1
commit "$D_FRESH" "chore(karta): plan discarded binder"
DISC_MAIN=$(git rev-parse HEAD)
# an integration branch off main with a done ref, NEVER merged back.
DISC_INT=$(GIT_AUTHOR_DATE="$D_FRESH" GIT_COMMITTER_DATE="$D_FRESH" \
  git commit-tree "$(git rev-parse HEAD^{tree})" -p "$DISC_MAIN" -m "[karta:item-d1] discarded work on integration")
git update-ref refs/heads/karta/discarded/integration "$DISC_INT"
git update-ref refs/karta/discarded/item-d1/built "$DISC_INT"
git update-ref refs/karta/discarded/item-d1/done "$DISC_INT"

# ---------------------------------------------------------------------------
# stale-artifacts: delivered slug with a STALE (>48h) worktree and a >48h
# karta-message stash.  Built last so the worktree/stash sit on the final tree.
# ---------------------------------------------------------------------------
binder .karta/binders/archive/stale-artifacts.json stale-artifacts s1
commit "$D_OLD" "chore(karta): archive binder stale-artifacts — delivered"
SA=$(git rev-parse HEAD)
git update-ref refs/karta/stale-artifacts/item-s1/built "$SA"
git update-ref refs/karta/stale-artifacts/item-s1/done "$SA"
git worktree add -q -b karta/stale-artifacts/item-s1 \
  .worktrees/stale-artifacts "$SA" >/dev/null 2>&1
# a karta-message stash dated >48h before the audit timestamp
echo "dirty" >> README.md
GIT_AUTHOR_DATE="$D_OLD" GIT_COMMITTER_DATE="$D_OLD" \
  git stash push -q -m "karta: stale-artifacts wave-3 worker scratch (pre-resume)"

git checkout -q main
