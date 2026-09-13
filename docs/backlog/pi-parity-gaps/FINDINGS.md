# Pi parity gap analysis — karta 2.36.0

Subject: `extensions/pi/` (45 files, 13,798 LOC) measured against the canonical skill, agent, and
hook surfaces it is supposed to project. Baseline commit `2f799e5`, working tree clean.

Method: six read-only subagents, one per axis, each required to cite `path:line` for every claim
and to report `ABSENT — no reference found` rather than infer a capability from a filename or from
doctrine prose. Every headline finding below was then re-verified by hand against the tree; claims
that were not re-verified are marked *(unverified)*. Two subtask counts disagreed and were
reconciled by direct measurement — see [Reconciled counts](#reconciled-counts).

The baseline for comparison is the parity table karta already publishes for Codex
(`docs/how-to/codex.md:54-66`). Pi has no equivalent, and that absence is itself the finding the
rest of this document fills.

## Verdict

Pi is **not** behind Claude and Codex uniformly. It is genuinely ahead on orchestration — the
accept-waiver, wave rollback, dispatch locking, and worker attestation are enforced in code with
no canonical equivalent. It is behind in three specific and load-bearing places:

1. **Three of eight canonical guards are shipped but not wired**, so writer confinement and both
   fail-closed dispatch inspectors do not exist on Pi.
2. **The delivery Stop gate is advisory on Pi, not a gate.** Claude and Codex block the stop;
   Pi queues a chat follow-up.
3. **The whole plan phase was agent doctrine on Pi**, with no dispatch action behind it.
   *(Addressed on this branch — see [Status](#status).)*

A Pi user reading `docs/how-to/pi.md` currently learns none of this.

## 1. Enforcement parity — the table Pi is missing

Rule rows are the set `docs/how-to/codex.md:54-66` already grades, plus rows Pi adds.

| Rule | Claude Code | Codex | Pi | Pi mechanism | Gap |
|-|-|-|-|-|-|
| Committed binders are read-only | Enforced (hook) | Enforced (hook) | Partial | `guard-adapter.ts:97-116` runs `binderImmutability` on `tool_call` | `hookToolInput` returns `undefined` for anything but `write`/`edit` (`guard-adapter.ts:30-38`), so a `bash` redirect or `rm` on a binder is never inspected |
| Pack edits must validate | Enforced (hook) | Enforced (hook) | Enforced | Pre `guard-adapter.ts:117-123`, post `:128-152` | Pre-check is `write`-only; an `edit` to a pack is caught only after it lands |
| Safety-auditor dispatch is complete | Enforced, fail-closed (hook) | Enforced, fail-closed (hook) | Enforced differently | `gate-runner.ts:461-475` throws when pinned packs or repo-rule citations went unread | `guard_auditor_dispatch.py` is absent from `KARTA_GUARD_PATHS` (`guard-runner.ts:5-11`); Pi checks the reviewer's *reading* after the fact, not the *dispatch payload* before the contexts spin up |
| Gate dispatches carry a real, sized diff | Enforced, fail-closed (hook) | Enforced, fail-closed (hook) | Enforced differently | `gate-runner.ts:448-458`, full-diff coverage `:331-341`, `:360-372` | No `Diff-size` contract — the host derives the diff, so a caller-supplied range is never validated |
| Confined writers stay inside their surfaces | Enforced (hook, incl. static `Bash` parsing) | Doctrine | Partial, narrower | `writer-profile.ts:63-67`, `:194-219` | `guard_writer_confinement.py` absent from Pi entirely. Confinement applies only to dispatched writer children, only to path-bearing tools — never the main session |
| You see your binders at session start | Informational (hook) | Enforced (hook) | Enforced (informational) | `guard-adapter.ts:154-170`, appends to `systemPrompt` | Gated on `.karta/binders` existing and project trust (`:158-160`) |
| A delivery may not end dirty | **Enforced (Stop hook, blocks)** | **Enforced (Stop hook, blocks)** | **Advisory only** | `guard-adapter.ts:196-207` then `sendUserMessage(..., { deliverAs: "followUp" })` `:209-213` | Pi's `agent_settled` has no deny channel. A session can end with built-but-unmerged items and nothing stops it |
| A build item that produced nothing is named | Enforced (SubagentStop) | Enforced (hook) | Advisory only | `guard-adapter.ts:182-194`, same follow-up path | Synthetic `agent_type: "karta-pi"` (`:186`) is not a real subagent identity |
| Karta Watch hub auto-revives | SessionStart hook + embedded ensure | SessionStart hook + embedded ensure | **FULL** | `beforeAgentStart` runs the same `inject_karta_status.py` (`guard-adapter.ts:155-170`; path `guard-runner.ts:9`), which fires `--ensure` at `hooks/scripts/inject_karta_status.py:195-220` | None — this row reaches parity |
| karta never pushes | — | Enforced (execpolicy) | **ABSENT** | — | No `git push` interception anywhere in `extensions/pi/` |
| Commits pass the gate suite | Enforced (project settings) | Enforced (`.codex/hooks.json`) | **ABSENT** | — | `scripts/` is not in the `files` allowlist and no Pi code references the gate scripts |
| *(Pi-only)* Untrusted projects cannot run karta tools | — | — | Enforced | `guard-adapter.ts:89-91` blocks any `karta_*` tool when `!ctx.isProjectTrusted()` | — |
| *(Pi-only)* Merge and candidate trees pass repo hooks | — | — | Enforced | `hook-runner.ts:11-17`, consumed by `integration-runner.ts:18`, `build-finalizer.ts:18`, `companion-runner.ts:9` | Runs the *project's* hooks, not karta's guards |

### Guard wiring, measured

`hooks/scripts/` holds **8** guard scripts. `KARTA_GUARD_PATHS` (`guard-runner.ts:4-10`) wires
**5**:

```
binderImmutability, packWrite, deliveryStop, subagentWhiff, statusInjection
```

Shipped but unreachable on Pi: `guard_writer_confinement.py`, `guard_gate_dispatch.py`,
`guard_auditor_dispatch.py`. Verified by reading the map directly and by grepping
`auditorDispatch|gateDispatch|writerConfinement` across `extensions/pi/`.

Two further properties of the adapter worth knowing:

- **Guard failures always fail open.** A spawn error, timeout, or abort resolves `code: 0`
  (`guard-runner.ts:57-62`, `:80-84`), and `#runGuard` swallows every throw into `failedOpen: true`
  (`guard-adapter.ts:75-83`). If `uv` is missing on the machine, every Pi guard silently
  disappears with no user-visible signal.
- **The dispatch inspectors were replaced, not ported.** They check a different thing: Codex
  denies a malformed dispatch *before* reviewer contexts start; Pi lets the gate run and throws
  afterward if evidence went unread. Stronger on coverage, weaker on cost — two reviewer contexts
  have already burned by then.

## 2. Roles and agents

All five canonical agents map to Pi roles, and all six roles' `source` paths and `expectedName`
values match the real frontmatter — no runtime throw. Mapping:

| Canonical source | Pi role | Authority | Confinement |
|-|-|-|-|
| `agents/karta-acceptance-reviewer.md` | `acceptance-gate` | read-only | evidence + check tool only |
| `agents/karta-safety-auditor.md` | `safety-gate` | read-only | evidence + boundary inspector only |
| `agents/karta-design-reviewer.md` | `visual-gate` | read-only | evidence tool only |
| `agents/karta-doc-gardner.md` | `doc-gardner` | surface-write | `docs/`, root `*.md`, `.gitignore` (`writer-profile.ts:58-66`, `:64`, `:63`) |
| `agents/karta-kaizen.md` | `kaizen` | surface-write | `.karta/kaizen.json`, `.karta/sme/` (`writer-profile.ts:58-61`) |
| `skills/karta-build/SKILL.md` | `build-worker` | worktree-write | path-confined + `.git`/`.karta` denied, **Bash unconfined** (`worker-profile.ts:104-109`) |

Real gaps:

- **Model and effort pins are deliberately not projected.** `agents/*.md` carry `model: opus`,
  `effort: xhigh`, `codex_model: gpt-5.6-sol` (`agents/karta-acceptance-reviewer.md:2-8`). Codex
  consumes them (`scripts/sync_codex_agents.py:94-103`) and `role-catalog.ts` ignores them
  entirely — `parsePromptSource` reads only `name` and the body. That is the design, not an
  oversight: every child inherits the session's model (`child-runtime.ts:165`, `:200`), so the
  model the user selected runs the gates too. Pi does not override the user's own model choice,
  and a reviewer's role is not to second-guess it. One detail is worth confirming rather than
  assuming: the thinking level inherits the parent and falls back to `"minimal"` when the session
  sets none (`child-runtime.ts:201`; `thinkingLevel` is optional in `ExtensionContext`), so a
  session with no level forces children to minimal. Whether that fallback is intended is
  unconfirmed.
- **`authority` and `capabilities` are declarative.** They are asserted
  (`capability-profile.ts:64-66`) but never translated into a tool allowlist; the real enforcement
  is hand-built `tools` arrays fed to `noTools:"all"` (`child-runtime.ts:205-207`). The two can
  drift silently.
- **No sandbox equivalent to Codex's `sandbox_mode = "read-only"`.** Codex derives it mechanically
  from the agent's `tools` (`sync_codex_agents.py:49-54`). Pi's read-only means only "we handed it
  two read tools" — there is no OS or process boundary. A future tool added to a gate profile is
  unsandboxed by default.
- **The `Skill` tool is absent from writer children.** `child-runtime.ts:93` sets `noSkills: true`,
  so `karta-doc-gardner`'s plainlanguage invocation (`agents/karta-doc-gardner.md:74`) cannot fire
  on Pi.
- **No drift guard binds `agents/*.md` to `role-catalog.ts`.** Codex hard-fails on an unmapped
  agent (`sync_codex_agents.py:38-44`); Pi's only check is a hand-written id list in
  `tests/pi/role-catalog.test.ts:14-19`. A sixth agent lands with no Pi role and nothing fails.
- **No Codex-style bundled fallback.** Codex bundles each agent body into its spawn-site skill's
  `references/` so the gate still runs without subagent registration (`sync_codex_agents.py:36-44`).
  Pi has no degraded path.
- **Ambient exclusion is genuinely enforced**, matching `AGENTS.md:225`:
  `noExtensions/noSkills/noPromptTemplates/noContextFiles` plus `SettingsManager.inMemory()`
  (`child-runtime.ts:87-96`), fresh `SessionManager.inMemory` (`:204`), and native/executable
  providers rejected for gate and worker children (`:124-138`).

## 3. Skills

Pi's tool surface is exactly two tools (`index.ts:60-62`: `karta_script`, `karta_dispatch`) plus
one command (`:70`: `/karta-phase0`). There is no `Task`, `Skill`, `AskUserQuestion`, or
`TodoWrite`. Any skill that says "spawn a subagent" therefore has no generic mechanism on Pi.

| Skill | Pi status | Consequence |
|-|-|-|
| `karta-build` | Enforced | `buildItem` action (`dispatch-tool.ts:45-49`); worktree, gates and refs host-owned |
| `karta-verify` | Enforced | `runVerification` (`dispatch-tool.ts:50-55`) + read-only gate roles |
| `karta-deliver` | Enforced, partial | Real runtime; the four-way halt offers three choices |
| `karta-status` | Doctrine | `statusControl`/`serveStatus` reachable, but only when the model calls the tool |
| `karta-validate` | Doctrine | Scripts exposed; no capture or comparison role, no user-facing command |
| `karta-plan` | **Enforced** (was doctrine) | `planSurvey` + `commitBinder`; see [Status](#status) |
| `karta-kaizen` | Enforced in delivery, absent standalone | Reachable only inside `deliverBinder` via `companion-runner` (`delivery-runner.ts:569`); `Mode: direct` is unreachable |
| `karta-doc-gardner` | Enforced in delivery, absent ad-hoc | Same delivery-only reachability; `Mode: ad-hoc` unreachable |
| `karta-debt` | Portable | No harness dependency on any harness — prose plus grep. Not a Pi regression |
| `karta-plainlanguage` | Portable | Prose only. No plainlanguage guard exists on *any* harness — `hooks/hooks.json` ships 8 guards, none prose |

The plan phase was the sharpest skill gap. `karta-plan` opens with an "Explore subagent" survey
(`skills/karta-plan/SKILL.md:77-79`), a synthesis subagent (`:170-174`), a plannotator browser
session (`:303-317`), and a commit gate on the explicit `commit` verb (`:319`). Pi had no
`planBinder` action (`dispatch-tool.ts:28-56`), no plan role (`role-catalog.ts:35-73`), and no
commit-verb check. The validator ran — `evidence.ts:243` invokes `validate_binder.py` — so shape
was checked; authoring, review, and the commit gate were not. `skills/karta-plan/SKILL.md:174`
sanctions an inline degraded path with a "visible degradation" note, but nothing verifies the note
is printed. This branch closes the enforceable half: `planSurvey` supplies the package-owned repo
facts, and `commitBinder` validates the binder, runs the shared-term check, presents the escapes
and shape line on a host card, and commits only on the human's `commit` verb — a set committing
in one commit. The grilling rounds, the shape decision, and synthesis judgment stay with the
model, which is what the skill itself requires.

## 4. Orchestration runtime

Pi enforces more than the canonical flow requires, and less than the docs imply. Graded:

**Enforced, at or above canonical:** integration worktree (`delivery-runner.ts:290-328`); per-item
worktree off the tip, refusing to clobber (`:329-339`); serial FIFO merge with the orchestrator as
sole tip writer (`:837-889`, `integration-runner.ts:579`); merge-time oracle re-run on the moved
tip (`integration-runner.ts:229-234`); shared-terms drift failing close-wave via
`wave-runner.ts:96-113`; acceptance cap 2 and safety cap 3 (`build-runner.ts:18-19`); binder
archive via hook-verified `git mv` (`companion-runner.ts:786-830`); companion opt-in
(`delivery-runner.ts:571-577`).

**The strongest Pi win — the accept waiver is structurally real.** The reason string can only
arrive from `ctx.ui.input("Human acceptance reason", ...)` (`delivery-runner.ts:750`), reached
through an `authorize` callback, and the worker is explicitly denied git and `.karta` mutation
(`worker-runner.ts:302`). Worker text cannot reach it. Verified at
`delivery-runner.ts:742-756`.

**Wave rollback exceeds doctrine.** `wave-runner.ts:129-247` tags `wave-<N>-base`, runs stable-tree
checks, then atomically deletes done/built/accepted refs and restores failed ones via
`update-ref --stdin`. Canonical doctrine says reverting "stays a doctrine decision made with the
human" (`skills/karta-deliver/SKILL.md:113`); Pi does it automatically.

**Absent:**

- **Resume-or-clear prompt.** Doctrine: "never silently resume" (`skills/karta-deliver/SKILL.md:47`).
  Pi derives the wave number from existing tags and continues (`delivery-runner.ts:456-468`).
  Grepping `resume|Resume|Clear` across `delivery-runner.ts` returns **zero hits**. Pi violates the
  rule silently.
- **Backlog sink.** Grepping `backlog` across `extensions/pi/*.ts` returns zero hits; gap records
  from accept/defer are lost.
- **Wave env binding.** `env_contract` / `isolation_params` absent (zero hits); only `visual_env`
  exists (`environment.ts:26-50`), so non-visual stateful-env oracles get no host-managed env.
- **`command_sha256` drift halt and `--allow-drift`.** Absent; only manifest binding at
  `evidence.ts:658-669`.
- **Parallelism gates beyond dependencies.** `skills/karta-deliver/SKILL.md:87-91` defines four
  gates including `serialize`, `shared_resources`, and `undecidable:stateful-env`. Pi implements
  only `depends_on` plus `touches`/`files` overlap (`delivery-runner.ts:108-119`, `:154-167`);
  grepping `serialize|shared_resources|parallelism` returns zero hits.
- **`SPEC-SUSPECT`.** Zero hits; verdicts are only `pass|concerns|blocked|skipped`
  (`verification-runner.ts:35`), so stale-binder adjudication collapses into generic `blocked`.
- **Roundtable and landing gates.** `scripts/hooks/roundtable_gate.py` and
  `scripts/roundtable/run_review.py` are unreachable and unshipped.

**Two numeric problems:**

- **Concurrency is unbounded.** `Promise.all(batch.map(...))` at `delivery-runner.ts:804` with no
  cap — a 12-item disjoint binder spawns 12 child agents at once. This contradicts this
  environment's own fan-out discipline, and it is a rate-limit hazard, not just a resource one.
- **Termination is bounded arbitrarily.** `pass <= work_items.length * 4 + 4`
  (`delivery-runner.ts:468`), then a throw at `:925`. That is an implementation constant, not a
  documented retry policy.

## 5. User-facing surfaces

| Surface | Pi status | Consequence |
|-|-|-|
| Plan review card + `commit` verb | **Absent** | Nothing stops a Pi session committing an unreviewed binder |
| Plannotator browser session | **Absent** | Zero `plannotator` references in `extensions/pi/` or `tests/pi/`; Pi never offers it unless the model runs the probe from skill prose itself |
| Karta Watch page | Full, model-initiated | `statusControl` with `ensure`/`optIn`/`optOut`/`printState` (`script-tool.ts:207-226`) |
| Watch hub auto-revive | **Full** | Parity with Claude and Codex, including `--ensure` |
| Visual oracle acceptance | **Full** | `verification-runner.ts:319-380`, 6 typed blocks `:41-54`; `visual-gate-runner.ts`, `visual-capture-runner.ts` |
| Check environment | **Full** | `environment.ts:50` covers exactly `preflight`/`setup`/`visual_env`; claims in `docs/how-to/pi.md:121-160` hold |
| Resume and lock recovery | **Full** | `dispatch-lock.ts`, shutdown ordering via `session_shutdown` (`index.ts:133-135`) |
| Commands | Partial | One command, `/karta-phase0`, a debug probe. No `/karta-plan`, `/karta-status`, `/karta-debt` |

`docs/how-to/pi.md:102-118` (visual oracles) is the one doc section that fully holds up against
code. Its "concern retries under the acceptance cap" claim was not traced to a line — treat as
*(unverified)*.

## 6. Packaging and distribution

`files` allowlist (`package.json:18-25`):

```
extensions/pi/, skills/, agents/, hooks/scripts/, !**/__pycache__/, !**/*.pyc
```

**No runtime-resolved path is unshipped.** Every `requirePackagePath` target falls under `skills/`,
`agents/`, `hooks/scripts/`, or `package.json`. Verified.

Note that `scripts/` is not merely omitted — it is an asserted prohibition: `tests/pi/package-manifest.test.ts:47`
requires that no packed path start with `scripts/` (alongside `tests/`, `docs/`, `.codex/`,
`.agents/`, `plugins/`, `benchmarks/`).

The real problems are around that allowlist:

- **The allowlist governs a channel nobody uses.** The documented Pi installs are a git tag
  (`docs/how-to/pi.md:29`) and a local path (`:38`). Pi clones git sources whole and adds local
  paths without copying. `files` only applies to an npm tarball, which `private: true`
  (`package.json:5`) forbids publishing. The one artifact the smoke test validates is the one users
  never install — and real git installs carry `scripts/`, `tests/`, and `hooks/hooks.json`,
  masking any future allowlist regression.
- **The smoke test asserts far less than the docs imply.** It checks name/version/private/license,
  `tests/` absent (`scripts/smoke_pi_package.mjs:154`), skill count == 10 (`:171`), and one
  `describeRole acceptance-gate` round trip (`:190`). That incidentally proves one of five role sources
  ships. It proves nothing about the other four, any of the 19 script paths, any of the 5 guard
  scripts, or the SME reference tree. `docs/how-to/pi.md:262` says it "verifies all ten skills" —
  it verifies they load, and the dispatch tool has no plan/validate/status/debt action at all.
- **`tests/pi/package-manifest.test.ts` hardcodes a sample instead of deriving from the catalogs.**
  It asserts 3 of 5 runtime guards (`:41-43`); `guard_pack_write.py` and `inject_karta_status.py`
  are in `KARTA_GUARD_PATHS` but unasserted, and zero of the 19 script paths are asserted. The
  obvious invariant — iterate `KARTA_SCRIPT_PATHS` ∪ `KARTA_GUARD_PATHS` ∪ `ROLE_CATALOG.source`
  and require each in `npm pack --json` output — is missing. `tests/pi/package-paths.test.ts:16-20`
  validates the checkout, not the artifact, so it passes even if `files` dropped a directory.
- **Version parity is only half-checked.** `scripts/validate_plugin.py:126-130` compares versions
  against `.claude-plugin/plugin.json` only. Nothing ties `package.json` to
  `.codex-plugin/plugin.json`, and `.agents/plugins/marketplace.json` carries no version field at
  all. All currently read 2.36.0.
- **No Pi marketplace story.** Claude gets `.claude-plugin/marketplace.json`; Codex gets
  `.agents/plugins/marketplace.json` plus the `plugins/karta/` projection. Pi's discovery is a raw
  URL a human types. No marketplace JSON references `extensions/pi/`.

## 7. Reverse gaps — Pi enforces what canonical doctrine does not

Worth preserving in any refactor, and worth naming in the docs as Pi's advantage:

- **Binder-scoped filesystem lease.** `dispatch-lock.ts:45-66`, `:144-160` makes two concurrent
  deliveries mechanically impossible. Canonical doctrine has no lock.
- **Worker authority attestation.** Before/after snapshots of git dirs, hooks, protected paths and
  sibling worktrees; a violating worker throws (`worker-runner.ts:366-398`,
  `worker-attestation.ts:178-225`).
- **Check convergence.** Checks must converge on an unchanged tree over ≤3 passes
  (`check-convergence.ts:13`, `:143`), and wave finish compares the target tree to the committed
  tree (`wave-runner.ts:186-188`).
- **Hash-bound evidence manifests.** A gate cannot pass without a passing bound check manifest, and
  must block when it is missing (`gate-runner.ts:481-484`).
- **Process ownership with kill-group teardown** (`process-manager.ts:88-180`,
  `lifecycle-registry.ts:44-85`, `shutdown-coordinator.ts:17-23`).
- **Crash-resumable checkpoints as a first-class type** across wave, integration and archive paths
  (`wave-runner.ts:29-38`, `integration-runner.ts:38-45`, `companion-runner.ts:41-45`).
- **A deterministic boundary inspector in code** rather than a reviewing agent
  (`boundary-inspector.ts:5-8`, `:82-143`).

## Reconciled counts

Two subtask reports disagreed on the script catalog; both were partly wrong, and direct measurement
settles it.

`script-catalog.ts` defines **19** actions.

- **12** are exposed through the `karta_script` tool's parameter union (`script-tool.ts:13-77`).
- **7** have zero references outside the catalog file: `checkDesignPins`, `checkGateReport`,
  `checkItemProvenance`, `deliverPreflight`, `itemContext`, `mergeItem`, `runOracle`. One subtask
  said six and listed `checkDesignPins` separately; the accurate number is seven.
- **The catalog is not the single source of truth it appears to be.** Ten call sites in
  `extensions/pi/*.ts` invoke scripts by literal path instead of through the catalog — for example
  `wave-runner.ts:103`, `evidence.ts:243`, and three in `integration-runner.ts` /
  `build-finalizer.ts`. Reference-counting catalog keys therefore understates real runtime use:
  `checkSharedTerms` looks dead by key but is live via a literal path at `wave-runner.ts:103`.

## Ranked gap register

| # | Gap | Severity | Evidence |
|-|-|-|-|
| 1 | Three of eight guards shipped but unwired — writer confinement and both fail-closed dispatch inspectors absent | High | `guard-runner.ts:4-10` vs `hooks/scripts/` (8 scripts) |
| 2 | Delivery Stop gate is advisory, not blocking | High | `guard-adapter.ts:196-213` |
| 3 | Plan phase had no dispatch action and no commit-verb gate — **fixed on this branch** | ~~High~~ Closed | `extensions/pi/plan-runner.ts`, `dispatch-tool.ts` |
| 4 | Silent resume contradicts "never silently resume" | High | `delivery-runner.ts:456-468`; zero `resume` hits |
| 5 | Unbounded concurrency in wave dispatch | High | `delivery-runner.ts:804` |
| 6 | Binder-immutability guards have a `bash`-shaped hole | Medium | `guard-adapter.ts:30-38` |
| 7 | Role model/effort pins not projected — deliberate (the session model runs everything), but undocumented in `pi.md` | Low | `child-runtime.ts:165`, `:200`; `role-catalog.ts` parses only `name` and body |
| 8 | No Pi enforcement-parity table in `docs/how-to/pi.md` | Medium | `docs/how-to/pi.md:264-279` is platform-only |
| 9 | Plan plannotator surface and commit guard absent | Medium | zero `plannotator` hits in `extensions/pi/` |
| 10 | Backlog sink and `env_contract` binding absent | Medium | zero `backlog`, `env_contract` hits |
| 11 | Four parallelism gates reduced to two | Medium | `skills/karta-deliver/SKILL.md:87-91` vs `delivery-runner.ts:108-119` |
| 12 | Roundtable and landing gates unreachable and unshipped | Medium | `package.json:18-25`; zero references |
| 13 | Catalog drift: 7 dead keys, 10 literal-path bypasses | Medium | measured above |
| 14 | Smoke test asserts one of five role sources and zero script or guard paths | Medium | `smoke_pi_package.mjs:154`, `:171`, `:190` |
| 15 | `karta-kaizen` and `karta-doc-gardner` standalone modes unreachable | Medium | `delivery-runner.ts:569`; no dispatch action |
| 16 | Guards fail open on any spawn error — a missing `uv` removes all Pi guards silently | Medium | `guard-runner.ts:57-62`, `:80-84`; `guard-adapter.ts:75-83` |
| 17 | No drift guard binds `agents/*.md` to `role-catalog.ts` | Low | `tests/pi/role-catalog.test.ts:14-19` |
| 18 | `SPEC-SUSPECT` verdict missing | Low | `verification-runner.ts:35` |
| 19 | Version parity unchecked for Codex and Pi manifests | Low | `validate_plugin.py:126-130` |
| 20 | Termination bound is an undocumented constant | Low | `delivery-runner.ts:468`, `:925` |

## Suggested next actions

The highest-value change is not a fix but a document: add the Enforced/Doctrine table from section
1 to `docs/how-to/pi.md`, replacing reliance on the platform-only support matrix. It costs nothing
and removes the largest current misrepresentation.

Then, in order: wire the three unwired guards (or delete them and delete the claim), give
`agent_settled` a real deny channel or downgrade the documented claim, and add a `planBinder`
action or state plainly in `pi.md` that planning is doctrine-only.

Two mechanical guards would prevent recurrence: derive the smoke-test and
`package-manifest.test.ts` assertions from `KARTA_SCRIPT_PATHS` ∪ `KARTA_GUARD_PATHS` ∪
`ROLE_CATALOG.source` instead of hardcoded samples, and add a check that every entry in
`KARTA_GUARD_PATHS` has a corresponding `hooks/scripts/` file and vice versa.

## Status

Uncommitted working-tree document. Repo doctrine (`AGENTS.md`, "How work reaches the default
branch") requires anything that is not a fully committed binder to land via a branch and a merge,
so this file must not be committed directly to `main`.

**Corrections after review.** Finding 7 was downgraded from Medium to Low and reframed. It
originally read "model and effort pinning are lost … an `opus`/`xhigh` gate can run on a cheap
parent at minimal thinking." That treated the user's deliberate model choice as a defect. Pi
inherits the session model by design (`child-runtime.ts:165`, `:200`) so that one capable model
runs every role; the reviewer's job does not include overriding which model the user picked. The
pins are still not projected and still undocumented in `pi.md` — that part stands.

**Resolved on branch `fix/pi-parity-gaps`.** Finding 3 is closed. `extensions/pi/plan-runner.ts`
adds two `karta_dispatch` actions — `planSurvey` (package-owned stack and pack facts) and
`commitBinder` (validate → shared-term check → host review card → commit only on the human's
`commit` verb, with a set committing in one commit, and a refusal when anything but the binder and
its review record is staged). `skills/karta-plan/SKILL.md` gained a Pi route naming both. Covered
by `tests/pi/plan-runner.test.ts` (11 cases). The remaining plan gaps in section 3 — the plannotator
probe and the synthesis role — are unchanged: the probe is skill-driven on every harness, so it is
parity rather than a gap, and synthesis judgment is deliberately not delegated.
