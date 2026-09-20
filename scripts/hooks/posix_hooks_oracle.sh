#!/bin/sh
# Runs inside a Linux container against the repo mounted at /work. Proves the
# POSIX half of the hook surface still works after the Windows-launch changes:
#   1. every .codex/hooks.json POSIX `command` executes under sh with a real
#      payload — the binder guard's entry must DENY (exit 2) a committed-binder
#      overwrite and ALLOW (exit 0) an ordinary write;
#   2. the Claude-side `uv run --script` commands launch and enforce the same;
#   3. every guard's own --self-test passes under Linux CPython.
# Output: TAP-ish PASS/FAIL lines; exit 1 on any FAIL.
set -u
fail=0
note() { printf '%s\n' "$*"; }
check() { # name want got
    if [ "$3" -eq "$2" ]; then note "PASS $1 (rc=$3)"; else note "FAIL $1 (want $2 got $3)"; fail=1; fi
}

git config --global --add safe.directory '*'
cd /work || exit 1

DENY='{"hook_event_name":"PreToolUse","tool_name":"Write","cwd":"/work","tool_input":{"file_path":".karta/binders/archive/baseline-burndown.json","content":"{}"}}'
ALLOW='{"hook_event_name":"PreToolUse","tool_name":"Write","cwd":"/work","tool_input":{"file_path":"src/x.py","content":"x"}}'

# 1. repo-local Codex manifest commands, verbatim, under sh
python3 - <<'PY' > /tmp/cmds.txt
import json
doc = json.load(open("/work/.codex/hooks.json", encoding="utf-8"))
for ev, groups in doc["hooks"].items():
    for g in groups:
        for h in g["hooks"]:
            if h.get("type") == "command":
                print(h["command"])
PY
n=0
while IFS= read -r cmd; do
    n=$((n + 1))
    printf '%s' "$ALLOW" | sh -c "$cmd" >/dev/null 2>&1
    check "codex-manifest-cmd-$n allow" 0 $?
done < /tmp/cmds.txt

BINDER_CMD=$(head -1 /tmp/cmds.txt)
printf '%s' "$DENY" | sh -c "$BINDER_CMD" >/dev/null 2>&1
check "codex-manifest binder deny" 2 $?

# 2. Claude-side uv shape
export CLAUDE_PLUGIN_ROOT=/work
printf '%s' "$DENY" | uv run --script "/work/hooks/scripts/guard_binder_immutability.py" >/dev/null 2>&1
check "claude uv-shape binder deny" 2 $?
printf '%s' "$ALLOW" | uv run --script "/work/hooks/scripts/guard_binder_immutability.py" >/dev/null 2>&1
check "claude uv-shape allow" 0 $?

# 3. guard + gate self-tests under Linux CPython
for f in /work/hooks/scripts/*.py /work/.codex-plugin/hooks/scripts/*.py \
         /work/scripts/hooks/precommit_gate.py /work/scripts/hooks/roundtable_gate.py \
         /work/scripts/hooks/codex_precommit_gate.py; do
    python3 "$f" --self-test >/dev/null 2>&1
    check "self-test $(basename "$f")" 0 $?
done

[ "$fail" -eq 0 ] && note "POSIX ORACLE: PASS" || note "POSIX ORACLE: FAIL"
exit "$fail"
