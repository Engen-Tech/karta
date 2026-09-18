#!/bin/bash
# Runs inside the container started by hatch_bash_oracle.py. Every case file is
# run by real bash; a fake git records, for each commit that actually executes,
# whether KARTA_SKIP_GATE reached its environment. Output: <id> TAB <log>.
set -u
unset KARTA_SKIP_GATE

cat > /usr/local/bin/git <<'SHIM'
#!/bin/sh
# Find the subcommand the way git does: skip global options (and the value of
# the ones that take one), then log only if it is `commit`.
while [ $# -gt 0 ]; do
  case "$1" in
    -C|-c|--git-dir|--work-tree|--namespace) shift 2 ;;
    -*) shift ;;
    commit)
      # `env -i` wipes GITLOG along with everything else, so fall back to the
      # path the runner parked on disk — or that commit would go unrecorded.
      log="${GITLOG:-$(cat /tmp/current_gitlog 2>/dev/null)}"
      printf 'COMMIT=%s\n' "${KARTA_SKIP_GATE-unset}" >> "$log"; exit 0 ;;
    *) exit 0 ;;
  esac
done
exit 0
SHIM
chmod +x /usr/local/bin/git
ln -sf /usr/local/bin/git /usr/bin/git

# Unknown programs (make, uv, run.py, notgit…) succeed silently, so a chain is
# not cut short by `command not found` before the commit that matters.
printf 'command_not_found_handle() { return 0; }\n' > /tmp/bashenv
export BASH_ENV=/tmp/bashenv

for f in /work/cases/*.sh; do
  id=$(basename "$f" .sh)
  d=$(mktemp -d)
  cp /usr/local/bin/git "$d/git"
  mkdir -p "$d/bin" && cp /usr/local/bin/git "$d/bin/git"
  : > "$d/f.py"; : > "$d/f"; echo msg > "$d/msg.txt"; echo m > "$d/m.txt"
  export GITLOG="$d/log"; : > "$GITLOG"; printf '%s' "$GITLOG" > /tmp/current_gitlog
  ( cd "$d" && timeout 10 bash "$f" </dev/null >/dev/null 2>&1 )
  sleep 0.05
  printf '%s\t%s\n' "$id" "$(tr '\n' ' ' < "$GITLOG")"
  rm -rf "$d"
done
