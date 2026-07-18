#!/usr/bin/env bash
# Fixture factory for dark-status-surface-probes: fabricate one broken, forged,
# or waived delivery state in a throwaway git repo, with plain git only.
#
#   make_state.sh <case-id> [dest-dir]     # prints the fabricated repo path
#
# Case ids: S1-S8 (stranded), F1-F4 (forgeries), P1-P2 (provenance).
# S7/S8 are synthesized from recorded git-command shapes (parchmark
# backend-hygiene and gringotts leftovers replicas) — NEVER copied from live
# consumer repos at run time. Author/committer identity and dates are pinned so
# every fabrication of a case yields byte-stable shas. All fabrication is meant
# to be confined to mktemp scratch dirs (the default when dest-dir is omitted);
# the grading probe fabricates every case fresh per invocation and never reuses
# a repo across two guard_delivery_stop.py invocations (its once-per-
# (session,state) sentinel in the git common dir would silence the second Stop).
set -euo pipefail

CASE="${1:?usage: make_state.sh <case-id> [dest-dir]}"
DEST="${2:-}"
if [ -z "$DEST" ]; then
  DEST="$(mktemp -d -t "karta-bench-${CASE}.XXXXXX")"
fi
REPO="$DEST"
mkdir -p "$REPO"

# Pinned identity + dates: byte-stable shas across fabrications.
export GIT_AUTHOR_NAME='karta-bench' GIT_AUTHOR_EMAIL='bench@karta.invalid'
export GIT_COMMITTER_NAME='karta-bench' GIT_COMMITTER_EMAIL='bench@karta.invalid'
export GIT_AUTHOR_DATE='2026-07-17T00:00:00+00:00'
export GIT_COMMITTER_DATE='2026-07-17T00:00:00+00:00'
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export TZ=UTC

g() { git -C "$REPO" -c commit.gpgsign=false -c tag.gpgsign=false "$@"; }

seed_repo() {
  git init -q -b main "$REPO"
  printf 'bench fixture seed\n' > "$REPO/README.md"
  g add README.md
  g commit -qm 'seed'
}

write_binder() { # $1 = raw file body (normally the binder JSON)
  mkdir -p "$REPO/.karta/binders"
  printf '%s\n' "$1" > "$REPO/.karta/binders/wip.json"
  g add .karta/binders/wip.json
  g commit -qm 'binder wip'
}

BINDER_A='{"slug":"wip","work_items":[{"id":"a"}]}'
BINDER_AB='{"slug":"wip","work_items":[{"id":"a"},{"id":"b"}]}'

item_branch() { # $1 = item id; branch off main with one commit; echoes the tip sha
  g checkout -q -b "karta/wip/item-$1" main
  printf '%s work\n' "$1" > "$REPO/$1.txt"
  g add "$1.txt"
  g commit -qm "[karta:item-$1] $1 work"
  g rev-parse HEAD
}

ref() { g update-ref "refs/karta/wip/item-$1/$2" "$3"; }

merge_item() { # $1 = item id, $2 = merge message; leaves HEAD on integration
  g checkout -q -b karta/wip/integration main
  g merge -q --no-ff -m "$2" "karta/wip/item-$1"
}

archive_binder_on_integration() { # HEAD must be the integration branch
  mkdir -p "$REPO/.karta/binders/archive"
  g mv .karta/binders/wip.json .karta/binders/archive/wip.json
  g commit -qm 'chore(karta): archive binder wip — delivered'
}

accepted_done() { # shared shape for S3 / P1 / P2: a human-waived accepted-done item + a ready sibling
  seed_repo
  write_binder "$BINDER_AB"
  local a_tip
  a_tip="$(item_branch a)"
  merge_item a 'merge item-a

Karta-Accepted: item-a
Karta-Accept-Reason: bench fixture waiver — named unmet assertion waived by human'
  ref a done "$(g rev-parse HEAD)"
  ref a accepted "$a_tip"
  g checkout -q main
}

delivered_with_leftovers() { # shared shape for S7 / S8: delivered + archived + merged to main, refs/branches left standing
  seed_repo
  write_binder "$BINDER_A"
  local a_tip
  a_tip="$(item_branch a)"
  merge_item a 'merge item-a'
  ref a built "$a_tip"
  ref a done "$(g rev-parse HEAD)"
  archive_binder_on_integration
  g checkout -q main
  g merge -q --no-ff -m 'deliver wip' karta/wip/integration
}

case "$CASE" in
  S1) # built-unmerged mid-wave: a merged+done, b built with no done/failed
    seed_repo
    write_binder "$BINDER_AB"
    a_tip="$(item_branch a)"
    merge_item a 'merge item-a'
    ref a built "$a_tip"
    ref a done "$(g rev-parse HEAD)"
    b_tip="$(item_branch b)"
    ref b built "$b_tip"
    g checkout -q main
    ;;
  S2) # all-done-unmerged awaiting the human merge: archived on integration, integration not merged to main
    seed_repo
    write_binder "$BINDER_A"
    a_tip="$(item_branch a)"
    merge_item a 'merge item-a'
    ref a built "$a_tip"
    ref a done "$(g rev-parse HEAD)"
    archive_binder_on_integration
    g checkout -q main
    ;;
  S3|P1|P2) # accepted-done item (proper trailers) + a ready sibling
    accepted_done
    ;;
  S4) # crashed-build branch with no refs
    seed_repo
    write_binder "$BINDER_A"
    g branch karta/wip/integration
    item_branch a > /dev/null
    g checkout -q main
    ;;
  S5) # corrupt/non-dict binder JSON (valid JSON, not an object)
    seed_repo
    write_binder '["corrupt","non-dict","binder"]'
    ;;
  S6) # binder in HEAD but deleted from the working tree; a stranded built ref
    seed_repo
    write_binder "$BINDER_A"
    ref a built "$(g rev-parse HEAD)"
    rm "$REPO/.karta/binders/wip.json"
    ;;
  S7) # post-merge leftovers (parchmark backend-hygiene replica, synthesized)
    delivered_with_leftovers
    ;;
  S8) # done-item worktree still mounted (gringotts replica, synthesized)
    delivered_with_leftovers
    g worktree add -q "$REPO/.wt-item-a" karta/wip/item-a
    ;;
  F1) # forged done ref not first-parent-reachable from integration (silences the Stop-gate)
    seed_repo
    write_binder "$BINDER_AB"
    g branch karta/wip/integration
    a_tip="$(item_branch a)"
    ref a built "$a_tip"
    ref a done "$a_tip"
    g checkout -q main
    ;;
  F2) # accepted ref whose target merge lacks the Karta-Accept trailers
    seed_repo
    write_binder "$BINDER_AB"
    a_tip="$(item_branch a)"
    merge_item a 'merge item-a'
    ref a done "$(g rev-parse HEAD)"
    ref a accepted "$a_tip"
    g checkout -q main
    ;;
  F3) # built (and done) refs forged onto a commit with no item branch at all
    seed_repo
    write_binder "$BINDER_AB"
    g branch karta/wip/integration
    h="$(g rev-parse HEAD)"
    ref a built "$h"
    ref a done "$h"
    ;;
  F4) # done ref for an item absent from the binder
    seed_repo
    write_binder "$BINDER_AB"
    g update-ref refs/karta/wip/item-ghost/done "$(g rev-parse HEAD)"
    ;;
  *)
    echo "make_state.sh: unknown case id '$CASE' (want S1-S8, F1-F4, P1-P2)" >&2
    exit 64
    ;;
esac

printf '%s\n' "$REPO"
