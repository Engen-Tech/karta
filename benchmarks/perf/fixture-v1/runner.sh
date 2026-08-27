#!/bin/sh
# One timed fixture-v1 baseline run — procedure steps 2-4 of
# benchmarks/perf/perf-fixture-cost-baseline.md, and nothing else.
#
#   sh benchmarks/perf/fixture-v1/runner.sh <plugin-tag> <repeat-index>
#
# <plugin-tag>    a karta release tag, e.g. v2.31.0 — the plugin version under test
# <repeat-index>  1, 2, 3 — names the transcript file run<i>.jsonl
#
# Environment knobs (all optional):
#   KARTA_REPO   karta checkout or clone URL to pin from (default: this file's repo)
#   RUN_ROOT     reuse an existing run root instead of minting one
#   TIMEOUT      wall-clock cap on the delivery (default 90m, the card's figure)
#
# Prints the run root and the transcript path on success. Everything the run
# produces lives under the run root; nothing is written back into the karta
# checkout, and the fixture bytes are never modified.
#
# POSIX sh: no bashisms, no arrays, no [[ ]]. Parses under `sh -n`, which the
# vector's probe asserts on every gate run.

set -eu

PLUGIN_TAG="${1:-}"
REPEAT_INDEX="${2:-}"
if [ -z "$PLUGIN_TAG" ] || [ -z "$REPEAT_INDEX" ]; then
    echo "usage: sh runner.sh <plugin-tag> <repeat-index>" >&2
    exit 2
fi

FIXTURE_DIR=$(cd "$(dirname "$0")" && pwd)
KARTA_REPO="${KARTA_REPO:-$(cd "$FIXTURE_DIR/../../.." && pwd)}"
TIMEOUT="${TIMEOUT:-90m}"

# --- step 2: a run root outside any session scratchpad, and the frozen repo ---
# Never a session scratchpad: its path changes every session, which scrambles the
# ~/.claude/projects encoding the miner needs to find the transcripts again.
RUN_ROOT="${RUN_ROOT:-$(mktemp -d /tmp/karta-bench.XXXXXX)}"
tar -xzf "$FIXTURE_DIR/repo.tar.gz" -C "$RUN_ROOT"
REPO="$RUN_ROOT/repo"

# The tarball ships no .git/index — it is the one git file whose bytes carry the
# build machine's stat data, so leaving it out is what makes the fixture
# byte-reproducible. `git reset` rebuilds it from HEAD; without this the tree
# reads as wholly deleted-and-untracked.
git -C "$REPO" reset -q

# --- step 3: pin the plugin, by the mechanism the README fixes for every quarter
git clone --quiet --no-tags --branch "$PLUGIN_TAG" --depth 1 \
    "$KARTA_REPO" "$RUN_ROOT/plugin"

mkdir -p "$REPO/.claude"
cp "$FIXTURE_DIR/settings.json" "$REPO/.claude/settings.json"
( cd "$REPO" && claude plugin marketplace add "$RUN_ROOT/plugin" --scope project )
( cd "$REPO" && claude plugin install karta@karta --scope project --yes )

# --- step 4: the timed headless delivery, transcript in stream-json -----------
# The permission mode is the card's, verbatim. Step 5 classifies any human
# prompt as a stall rather than a datapoint, so a run that can stop to ask
# measures nothing. It is safe here because every run happens inside a throwaway
# mktemp -d holding an extracted fixture and a pinned plugin — settings.json
# beside this file records what the delivery is actually expected to need.
TRANSCRIPT="$RUN_ROOT/run$REPEAT_INDEX.jsonl"
TIMING="$RUN_ROOT/run$REPEAT_INDEX.time"
set +e
( cd "$REPO" && /usr/bin/time -v -o "$TIMING" \
    timeout "$TIMEOUT" claude -p '/karta:karta-deliver bench' \
        --output-format stream-json --verbose \
        --permission-mode bypassPermissions ) > "$TRANSCRIPT"
STATUS=$?
set -e

echo "run_root=$RUN_ROOT"
echo "transcript=$TRANSCRIPT"
echo "timing=$TIMING"
echo "exit_status=$STATUS"
# A nonzero status is data, not a script failure: the classifier in step 5 of the
# card decides COMPLETE vs INCOMPLETE, and INCOMPLETE runs are committed with
# their reason rather than thrown away.
exit 0
