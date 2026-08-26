# fixture-v1 — the frozen denominator of the per-phase cost baseline

Four files, and they do not change. This directory is the fixed input of the
`perf-fixture-cost-baseline` vector
([the card](../perf-fixture-cost-baseline.md) is the spec; this README records
only what the card says must be identical every quarter).

| file | what it is |
|-|-|
| `repo.tar.gz` | the whole testbed repo, git history included, as frozen bytes |
| `settings.json` | the permission allowlist one headless baseline run is expected to need |
| `runner.sh` | procedure steps 2-4: mint a run root, extract, pin the plugin, run the timed delivery |
| `README.md` | this file — the pin mechanism and the immutability rule |

## The immutability rule

**Fixture bytes never change.** Not to fix a typo, not to add a third work item,
not to track a schema change. The number this vector produces is only a
regression signal because the input was byte-identical to last quarter's; edit
the fixture and every earlier measurement silently becomes incomparable.

When a karta release genuinely breaks the fixture — the binder schema moves and
`bench.json` no longer validates, or the delivery flow stops recognising the
repo shape — the answer is a **new directory, `fixture-v2/`, and a baseline
RESET**. Old results stay committed under their own fixture version and are
never compared across versions. Say "fixture-v2, baseline reset" in the results
file; never quietly re-cut fixture-v1.

The vector's probe enforces the weak half of this mechanically on every gate
run (all four files present, the tarball still carries `.karta/binders/bench.json`,
`runner.sh` still parses). The strong half — that the bytes are the same bytes —
is doctrine plus git history.

## What is inside `repo.tar.gz`

A deliberately tiny stdlib-Python repo that extracts to `repo/`:

```
repo/
  .gitignore  AGENTS.md  README.md
  src/__init__.py  src/greet.py
  tests/__init__.py  tests/test_greet.py
  .karta/binders/bench.json     one S unit-oracle item + one M integration-oracle item
  .git/                         one commit, pinned author/committer dates
```

The binder is `bench` — `shout-helper` (S, `unit` oracle) and `greet-cli`
(M, `integration` oracle), no UI fields, no dependencies between them, so a
delivery runs them in one wave. It validated against this repo's live
`skills/karta-plan/scripts/validate_binder.py` at freeze time, and the vector's
probe re-validates the extracted copy on every gate run, so the fixture cannot
rot silently while nobody is looking.

**One git file is deliberately absent: `.git/index`.** It is the only member
whose bytes carry the build machine's stat data, so shipping it would make the
tarball unreproducible. `runner.sh` rebuilds it with `git reset -q` right after
extracting; do the same if you extract by hand, or the tree reads as wholly
deleted-and-untracked.

## The pin mechanism — identical every quarter

`runner.sh <plugin-tag> <repeat-index>` does exactly this, and nothing about it
may drift between quarters:

1. `RUN_ROOT=$(mktemp -d /tmp/karta-bench.XXXXXX)` — never a session
   scratchpad. A scratchpad path changes every session, which scrambles the
   `~/.claude/projects` name the miner needs to find the transcripts again.
2. `tar -xzf repo.tar.gz -C "$RUN_ROOT"`, then `git -C "$RUN_ROOT/repo" reset -q`.
3. `git clone --branch <plugin-tag> --depth 1` the karta repo into
   `$RUN_ROOT/plugin`, copy this directory's `settings.json` to
   `$RUN_ROOT/repo/.claude/settings.json`, and register the clone as a
   project-scoped local marketplace from inside `$RUN_ROOT/repo`:
   `claude plugin marketplace add "$RUN_ROOT/plugin" --scope project` followed by
   `claude plugin install karta@karta --scope project --yes`. Project scope is
   the point — the pinned tag governs the run, not whatever the operator happens
   to have installed at user scope.
4. `/usr/bin/time -v` wrapping `timeout 90m claude -p '/karta:karta-deliver bench'
   --output-format stream-json --verbose`, redirected to
   `$RUN_ROOT/run<i>.jsonl`. See the `KARTA-DEFER` note on that line in
   `runner.sh`: one flag from the card's step 4 is missing and a human must
   restore it before the first real baseline run.

Override `KARTA_REPO` to pin from a clone URL rather than this checkout;
override `TIMEOUT` only to reproduce an old run that used a different cap, and
say so in the results file.

## After the run

`benchmarks/perf/mine_fixture.py` turns the session transcripts into the card's
step-6 metric set; `benchmarks/probes/perf-fixture-cost-baseline.py` is the gate
adapter over the deterministic half. The quarterly timed run itself is manual —
the probe never launches a 90-minute delivery, which is why it reports
`partial: true`.

## How `repo.tar.gz` was built

Recorded so the freeze is auditable, not so it gets rebuilt — the committed
bytes are the authority.

The repo above was assembled, then committed with author and committer both
`karta bench fixture <bench@karta.invalid>` and both dates
`2026-01-01T00:00:00+0000` on branch `main`. The archive was then written by a
throwaway stdlib script that normalises every source of nondeterminism: members
sorted by name and prefixed `repo/`, `__pycache__`/`*.pyc` and
`.git/{index,hooks,logs,info,COMMIT_EDITMSG,description}` dropped, `mtime=0`,
`uid=gid=0`, empty `uname`/`gname`, mode `0644` for files and `0755` for
directories, GNU tar format, then gzipped with `compresslevel=9, mtime=0`.
Building it twice on one machine gives identical bytes; across machines a
different zlib may recompress the loose git objects differently, which is
another reason the committed bytes — not a rebuild — are the fixture.

sha256 of the committed archive:
`2f328f31b74d89ef14f37f12941cb1d0293757867103015946432abf9f773acc`

Not yet listed in `benchmarks/fixtures/REGISTRY.json`: that file's sole writer
(`update_registry.py`) is manifest-driven and its manifest belongs to a
different binder. Register these four files the next time that manifest is
extended.
