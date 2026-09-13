# TODOS — the Pi parity work, handed off

Written 2026-09-13 at the end of the session that produced `docs/backlog/pi-parity-gaps/FINDINGS.md`. Read
that file first: it is the gap analysis this list descends from, with the evidence for every claim
below.

**This document is written to be picked up cold.** Every item names the file, the evidence, the
concrete step, and what "done" means. Where a fact was verified in the originating session, it says
so; where it was not, it says that too. Nothing here should be taken on trust without re-reading the
named file, because line numbers drift.

## 0. Do this first — an unmerged branch is sitting in roundtable-src

`docs/bug-0002-rewrite` at `5621998` is committed and unmerged. It closes BUG-0002 and BUG-0007 by
deletion, adds a scope rule to `docs/bugs/README.md` ("a defect in a host, provider, or proxy is not
filed here"), documents provider-dependent tool naming in `INSTALL.md` §6, and fixes the skills'
availability check to match case-insensitively on a trailing name.

Verify and land it before anything else, or it rots:

```sh
cd /mnt/agent-storage/vader/src/roundtable-src
git log --oneline -1 docs/bug-0002-rewrite
timeout 700 go test ./... -count=1
timeout 600 npm run check:pi
git switch main && git merge --no-ff --no-edit docs/bug-0002-rewrite
```

**Trap:** `git switch main` is not optional. In the originating session a merge was run while still
on the feature branch, which merged it into itself and printed `Already up to date` — a silent
success that was nearly reported as a landed merge. Confirm with `git log --oneline -1 main` after,
and never trust the merge output alone.

---

## A. karta finding 1 — three of eight guards ship but are never wired on Pi

**Severity: the highest on this list.** It is the only item with a security flavour: two of the three
unwired guards are the fail-closed dispatch inspectors, and the third is writer confinement.

### Verified state (re-confirm before working)

Shipped in `hooks/scripts/` — eight guards:

```
guard_auditor_dispatch.py     guard_binder_immutability.py   guard_delivery_stop.py
guard_gate_dispatch.py        guard_pack_write.py            guard_subagent_whiff.py
guard_writer_confinement.py   inject_karta_status.py
```

Wired on Pi in `extensions/pi/guard-runner.ts` — five, via `KARTA_GUARD_PATHS`:

```
binderImmutability   packWrite   deliveryStop   subagentWhiff   statusInjection
```

**Unwired: `guard_writer_confinement.py`, `guard_gate_dispatch.py`, `guard_auditor_dispatch.py`.**
They ship inside the deliverable and do nothing on Pi. Nothing fails; they are simply never called.
On Claude Code all eight run. On Codex, seven run — writer confinement is doctrine there by design.

### Why each matters, and what is *not* simply portable

Do not assume the fix is "add three entries to `KARTA_GUARD_PATHS`". Read
`extensions/pi/guard-adapter.ts` first: it maps Pi events to guards, and it only has four hook
points — `tool_call`, `tool_result`, `before_agent_start`, `agent_settled`. Map each guard to a
point before writing code.

- **`guard_writer_confinement.py`** restricts a confined writer (kaizen, doc-gardner) to its own
  surface. On Pi this is *partly* covered already, by construction rather than by the guard: see
  `extensions/pi/writer-profile.ts` (`isWriterWritablePath`, `confineWriterTool`). What is missing is
  the guard for the **main session** — a human or model editing `.karta/sme/` by hand is unconfined
  on Pi. Decide whether the fix is to wire the guard or to narrow the documented claim.
- **`guard_gate_dispatch.py`** and **`guard_auditor_dispatch.py`** are described in `AGENTS.md` and
  `docs/how-to/codex.md` as fail-closed dispatch inspectors: a recognized gate dispatch must carry a
  real, sized diff, and a recognized safety-auditor dispatch must carry its binder and pinned-pack
  checklists. On Pi this is *replaced, not missing*: `extensions/pi/gate-runner.ts` throws when
  pinned packs or repo-rule citations went unread, and when the evidence hash does not match. So the
  question is not "add two guards" but **"is the after-the-fact runtime check weaker than the
  before-the-spawn inspection, and is that acceptable?"** It is weaker on cost: by the time
  `gate-runner` throws, two reviewer contexts have already run. Decide, and record the decision.

**Evidence for all of the above:** `FINDINGS.md` rows 1, 6, and 18; the enforcement table in
`docs/how-to/pi.md` under "What karta enforces on Pi", which explicitly says the three guards are
unwired.

### Acceptance criteria

Whatever route is taken, three things must hold:

1. No guard script ships in `hooks/scripts/` without either being reachable on Pi or being named as
   deliberately unwired, with the reason, in `docs/how-to/pi.md`.
2. A test asserts the relationship between `hooks/scripts/*.py` and `KARTA_GUARD_PATHS`, so a ninth
   guard cannot be added and silently ignored, and a wired guard cannot name a file that is gone.
   *(This is the same class of fix as the manifest test that hardcoded a sample instead of deriving
   from the catalog — see `tests/pi/package-manifest.test.ts`, which asserts 3 of 5 guards rather
   than deriving the set.)*
3. `npm run check:pi` green.
4. **Note the gap in verification:** `guard-adapter.ts` guards fail open — a spawn error, a timeout,
   or a missing `uv` resolves to `code: 0` with no user-visible signal. If a wired guard can silently
   not run, "wired" is not the same as "enforcing". Consider whether that needs its own item.

---

## B. roundtable-src — eight rows are registered but their reports are not written

In `/mnt/agent-storage/vader/src/roundtable-src/docs/bugs/README.md`, the register lists eight
entries whose `Report` column reads `registered`. They have an id, a title, a severity, and a
status — no file. Read `docs/bugs/TEMPLATE.md` for the fields and `docs/bugs/README.md` for the
lifecycle (a fixed report is *deleted*, with `git log` as the record).

| Id | Title | Severity |
|-|-|-|
| BUG-0003 | A documented `ROUNDTABLE_BIN` in the Pi config file is ignored when resolving the binary | S2 |
| BUG-0004 | The bridge abandons an open MCP connection when two calls use different working directories | S3 |
| BUG-0005 | One failed tool call tears down the shared connection for its concurrent siblings | S3 |
| BUG-0006 | A missing bundled binary silently falls back to whatever `roundtable` is on `PATH` | S3 |
| BUG-0008 | `INSTALL.md` has no Pi verification or troubleshooting section | S3 |
| BUG-0009 | HTTP providers' default-panel wording contradicts `INSTALL.md` | S4 |
| BUG-0010 | `describeFailure` misclassifies some provider failures | S4 |
| BUG-0011 | The bridge has no guard against use after `close()` | S4 |

**All eight came from a roundtable panel, not from a human reproducing them.** The register's own
first rule is "reproduce before filing, or say you plainly did not". So each one needs reproduction
*before* a report file is written; a report that reads as reproduced when it is not is worse than an
empty row. Where a finding is confirmed, write the report from `TEMPLATE.md`; where it is refuted,
delete the row and say so in the commit.

Two of the eight are cheap and self-contained, and are the ones I would start with:

- **BUG-0003** is the smallest and breaks a documented route. `extensions/pi/bridge.ts` resolves the
  command from `ROUNDTABLE_BIN` → config → bundled `.pi-bin/roundtable` → bare `roundtable` on
  `PATH`. The config-file route passes the value to the child but does not use it to select the
  binary. Read `resolveRoundtableCommand` and trace where the config's `env` block is applied.
- **BUG-0006** is the same function, and the pair should be fixed together: a silent `PATH` fallback
  when the bundled binary is missing means an operator can end up running an older or unrelated
  `roundtable` with no warning. Older HTTP-era builds exist in the wild.

Also unresolved on the `bridge.ts` list: BUG-0004 and BUG-0005 are both connection-lifecycle defects
in the same code, and BUG-0011 is a missing post-`close()` guard in it. Reading `bridge.ts` once
should let you reproduce all three.

**Not roundtable's, and not to be filed there:** anything in karta's `run_review.py` (already fixed,
see Group C) or karta's Pi docs. The register now carries a scope rule saying so.

---

## C. six deferred findings on karta's Pi runtime

Deferred at the roundtable record for `fix/pi-parity/integration`; the reasoning is in
`.karta/roundtable/branch-579767e9f98724c239fbd9157c66ee3ad2da23b1.rounds.json` and the register in
`docs/backlog/pi-parity-gaps/FINDINGS.md`. None is fixed.

1. **The commit gate is bypassable.** `commitBinder` genuinely cannot have its verb supplied through
   `karta_dispatch`, and headless correctly blocks — but an agent with shell access can run
   `git commit` itself. The skill's "do not commit by hand" is prose, not enforcement. Unlike the
   accept-waiver, there is **no evidence ref recording what the human approved**, and the code
   comment claiming it works "exactly as with the accept waiver" overstates it. Either narrow the
   claim or add the evidence record. *(The accept flow writes
   `refs/karta/<slug>/item-<id>/accepted`; this writes nothing.)*
2. **Slug freshness is unenforced.** `validate_binder.py` only *warns* about a binder shadowing an
   archived slug, and the runner checks the exit code alone — so a shadowing binder commits. A later
   delivery can then misread old refs and silently skip work.
3. **`collisionBatch` ignores `serialize` and `shared_resources` — the most consequential of the
   six.** `extensions/pi/delivery-runner.ts` batches on `touches` overlap only, so items the binder
   explicitly forbids running together are built concurrently. `skills/karta-deliver/SKILL.md` defines
   four parallelism gates; Pi implements two. This is register finding 11 and predates this work.
4. **`mapWithConcurrencyLimit` uses `Promise.all`**, which rejects at the first worker failure and
   leaves the remainder running un-awaited — after the lease has been released. `allSettled`, then
   throw.
5. **`plan-runner.ts` uses raw `ctx.cwd`** instead of `git rev-parse --show-toplevel`, so a dispatch
   from a subdirectory mis-resolves the binder path. Fails safe but should match
   `delivery-runner.ts`.
6. **A set commits under a subject naming only its first slug.**

---

## D. Traps this session paid for — read before reproducing anything

These cost real time in the originating session. Each one produced a wrong conclusion at least once.

- **`pi --provider X` alone does not switch provider.** A re-test that passed `--provider
  ollama-cloud` without `--model` stayed on the default provider and appeared to *disprove* a correct
  finding. Pass both, and confirm by asking the session to state its own provider and model — or
  better, read `--mode json`'s `turn_end` record rather than trusting the model's self-report, which
  was wrong once.
- **`pi --tools <names>` is an ALLOWLIST.** Probing for tools with `--tools read` disables the tools
  being probed for and returns a convincing `NONE`.
- **A session listing both `read` and `mcp_Read` is not duplicate registration.** The plain names are
  the prose tool list in Pi's system prompt; the prefixed ones are the callable schema.
- **A roundtable record's key is the branch tip sha.** Committing the record on the branch it
  describes moves the tip and orphans its own key, so `--check` goes stale. The record belongs in the
  **landing commit**, where HEAD is the default branch and the recorded tip stays put. This is
  encoded in `scripts/roundtable/run_review.py --self-test`.
- **A worktree has no `node_modules`.** `npm run check:pi` needs
  `ln -sfn <main-checkout>/node_modules node_modules` first — and git does *not* ignore a symlink
  where it ignores a directory, so remove it before committing or it shows as untracked.
- **Do not borrow the `karta/*/integration` namespace** for ordinary work. Both merge gates match on
  it, so using it manufactures a landing-gate block and misdescribes the change. The consequence,
  stated plainly: `fix/pi-parity/integration` was landed **ungated**, because the landing gate never
  fired for a ref in that namespace.
- **Never set `KARTA_LANDING_APPROVED=1`** to get a merge through. It is the human's decision, and
  setting it when the gate is not even asking is forging an approval.

## E. The gate set — run all of it, in both repos

karta (from the worktree root):

```sh
uv run scripts/validate_plugin.py --self-test
uv run scripts/check_shared_copies.py --self-test
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
npm run check:pi
python3 scripts/roundtable/run_review.py --self-test
```

roundtable-src:

```sh
timeout 700 go test ./... -count=1
timeout 600 npm run check:pi
python3 scripts/sync_plugin.py        # after any SKILL.md edit; then verify the projections
for f in skills/roundtable/SKILL.md plugins/roundtable/skills/roundtable/SKILL.md release/SKILL.md; do
  cmp -s SKILL.md "$f" || echo "DIFFERS: $f"
done
```

**Editing a skill means editing the canonical file and regenerating.** `roundtable-src/SKILL.md` is
canonical; `skills/roundtable/SKILL.md`, `plugins/roundtable/skills/roundtable/SKILL.md` and
`release/SKILL.md` are generated by `scripts/sync_plugin.py` and guarded byte-for-byte by
`internal/roundtable/plugin_sync_test.go`. Editing a projection is silently overwritten.

**Also note:** no repo-local git hook is installed in these checkouts, so *nothing* enforces the gate
set at commit time. Every green result above is one someone ran on purpose. AGENTS.md concedes this —
"a commit made outside a hooked session meets no floor at all".

## F. State at handoff

| | |
|-|-|
| karta `main` | `e841f45` — the six-item parity delivery landed, plus its roundtable record |
| karta worktree | `/mnt/agent-storage/vader/src/karta-worktrees/karta-pi-parity-fixes`, on `fix/pi-parity/integration` |
| roundtable-src `main` | `66fdc67` — the install fix landed |
| roundtable-src unmerged | `docs/bug-0002-rewrite` @ `5621998` (item 0 above) |
| roundtable Pi package | installed, and it exposes six tools — but from **GitHub main**, not this checkout, so nothing landed locally is in effect in a live Pi session until it is pushed and the package updated |

`docs/backlog/pi-parity-gaps/FINDINGS.md` still carries its own register (rows 1, 6, 7, 9–20 open);
this file overlaps it deliberately and points at it for evidence rather than restating it.
