# Pi package delivery plan

## Goal

Ship Karta as a first-class Pi package without weakening its Git-native resume model, read-only gates, or Claude Code and Codex behavior.

Karta will use one explicit Pi extension and the canonical `skills/` tree. It will not fork Pi, create another full skill mirror, or use Pi sessions as a second orchestration database.

## Reboot checkpoint

|Field|Value|
|-|-|
|Worktree|`/Users/tej/src/karta-pi`|
|Branch|`feat/pi-package`|
|Package|`@engen-tech/karta` version `2.30.0`, private and `UNLICENSED`|
|Completed|Phase 0 feasibility closure; Phases 1, 2, 3A, 3B, 3C, 3D, 3E, 4A, 4B, 4C, 4D, and 4E|
|In progress|Phase 4F — interruption behavior|
|First next action|Close Phase 4F with managed dev-server/wave-environment shutdown coverage and archive checkpoints after Phase 5 writers land|
|Do not touch|Unrelated changes in `/Users/tej/src/karta`|
|Commit state|Shutdown ordering, hook-process ownership, first-edit fault injection, fresh-process Git recovery, and competing-delivery lock tests are ready on `feat/pi-package`|

The source tree and Git refs are the durable checkpoint. Pi session history is not part of Karta recovery.

Mac, local terminal from the feature worktree:

```sh
cd /Users/tej/src/karta-pi
npm run check:pi
uv run scripts/validate_plugin.py --self-test
uv run scripts/check_shared_copies.py --self-test
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
```

All five checks pass during Phase 4F. The Pi suite currently runs 157 tests. `npm audit --omit=dev` reports zero vulnerabilities. The full development tree still carries the three known vulnerabilities inherited from Pi 0.83.0.

## Non-negotiable invariants

1. Project trust gates every Karta action. Untrusted projects expose no Karta skills, and registered Karta tools refuse execution.
2. Authoritative prompts, scripts, role definitions, and policy resolve from the installed package root. A project skill collision may alter conversational guidance but never selects a Karta worker or gate prompt.
3. Karta state remains Git-native: binders, branches, commits, tags, refs, and the existing Git-common-dir sentinels. Pi sessions hold no durable delivery state.
4. Gate children inherit no ambient extensions, skills, prompts, themes, context files, tools, parent conversation, or project instructions.
5. Parent hooks do not protect SDK children. Every child receives an explicit capability set owned by Karta.
6. Gate children are read-only by construction. They receive no Bash, write, edit, arbitrary path, arbitrary prompt, or arbitrary script capability.
7. Build worktrees isolate changes; they are not security sandboxes. Build workers are trusted, high-authority coding agents.
8. A guard may fail open only where the existing Karta hook contract explicitly requires it. Gate startup, provider compatibility, evidence integrity, dispatch locking, and verdict binding fail closed.
9. No background child may outlive its owning lifecycle record. Shutdown aborts all active children before disposal.
10. Ordinary consumer repositories gain no new setup files or mandatory configuration.
11. Claude Code and Codex projections remain generated from their documented canonical sources.
12. Git history is never rebased.

## Delivery map

|Phase|Status|Exit condition|
|-|-|-|
|0 — SDK feasibility|Complete|OAuth refresh and real multi-child shutdown are proved; unsupported provider shapes fail closed|
|1 — Package and paths|Complete|Package loading, fixed scripts, projections, install/update/rollback, and inventory checks pass|
|2 — Trust and host adapters|Complete|Trust, binder/pack guards, status, lifecycle, whiff, and dirty-delivery backstops pass|
|3A — Strict child runtime|Complete|Gate provider policy, exact model resolution, OAuth behavior, and real multi-child shutdown pass|
|3B — Dispatch foundation|Complete|Role catalog, evidence builder, cross-process lock, and capability profiles pass|
|3C — Read-only gates|Complete|Acceptance and safety gates produce hash-bound verdicts with read-only tools|
|3D — Contract alignment|Complete|Gates bind staged candidates, proposed merge trees, host-run checks, composed rules, and explicit lock ownership|
|3E — Transaction closure|Complete|Full-floor receipts, stable-tree convergence, complete citations, dependency-correct rules, and exact hook-validated commits pass|
|4 — Build and delivery|In progress|Parallel workers and serial moving-tip integration preserve Karta's existing protocol|
|5 — Narrow writers|Not started|Doc-gardner and kaizen run with confined capabilities and labeled commits|
|6 — Release|Not started|Native OS matrix, upgrades, rollback, documentation, and release checks pass|

## Decisions carried from Phase 0

- Pi loads one package extension. `resources_discover` adds canonical skills only after Pi resolves project trust.
- The project skill wins a same-name collision. Future dispatch must therefore enter through a package-owned fixed tool or command, not through the ambient skill winner.
- In-memory sessions and settings plus an explicit resource loader successfully remove ambient extensions, skills, prompts, themes, context files, and tools.
- Stored credentials, environment credentials, CLI runtime keys, and declarative dynamic provider configurations can be reproduced in a child runtime.
- A child can complete a real model turn, and aborting a child reaches its active tool signal.
- Worktrees are change-isolation boundaries, not hostile-code sandboxes.

## Retrospective changes to the original sequence

Phase 0 and Phase 1 do not need to be rewritten, but their findings change later work:

1. Phase 3 is split into 3A, 3B, and 3C. Provider and shutdown feasibility must close before gate implementation.
2. Dynamic native providers are executable extension code. Copying one into a gate would violate the no-ambient-extension rule. Gates reject them unless a future audited bridge is designed explicitly.
3. A gate child must resolve the selected model in its own runtime. The Phase 0 probe's fallback to the parent model object is forbidden for gates.
4. Parent `before_provider_request` and `before_provider_headers` handlers are not inherited. Gate requests use the isolated runtime directly; unsupported provider shapes fail before dispatch rather than silently loading ambient extensions.
5. The Phase 1 collision test proved path ownership, not final dispatch ownership. Command/tool collision precedence and package-owned role selection move into Phase 3 acceptance.
6. Pi cannot veto process shutdown. Phase 4 must commit each durable checkpoint before a dispatch tool returns; `session_shutdown` is cleanup, not a transaction boundary.
7. Existing Python host guards intentionally fail open on internal errors. That posture must not leak into lock, evidence, provider, or gate-verdict code.

## Phase 0 — feasibility closure

The original spike is retained at `/Users/tej/src/karta-pi-phase-0` on `spike/pi-phase-0`. Its implementation and findings have been transferred to the feature worktree.

### Proved

- Local and pinned Git package loading from outside the checkout.
- Install, update, rollback, uninstall, duplicate-root rejection, spaces, Unicode, and a symlinked local package.
- Trust-gated discovery of all ten skills.
- Project skill collision precedence.
- Ambient-free child resources and in-memory sessions.
- Stored, environment, runtime-key, and declarative dynamic-provider authentication paths.
- Real child completion and single-child cancellation propagation.

### Closure evidence

Phase 3A closed the remaining spike items in the feature worktree: deterministic OAuth expiry and concurrent refresh, strict rejection of executable provider shapes, a real isolated stored-auth preflight, and coordinated cleanup of four real children in tool, streaming, and idle states.

## Phase 1 — package and paths

**Status: complete.**

### Implemented

- `package.json`, lockfile, TypeScript configuration, and one explicit Pi extension.
- Package-root path resolution and a typed catalog covering every bundled Karta Python script.
- `karta_script`, with fixed actions, strict schemas, direct `uv` argument vectors, timeouts, output bounds, HTTP URL checks, and project/package path containment.
- Symlink-escape and traversal rejection.
- Canonical skills that prefer fixed Pi actions and use `<skill-dir>`-relative fallbacks on Claude Code and Codex.
- Regenerated `.agents/skills/` and `plugins/karta/` projections.
- Validator checks for manifest shape, version parity, peers, lockfile, lifecycle scripts, package inventory, and consumer-relative bundled commands.
- npm inventory exclusions for generated bytecode, development projections, tests, benchmarks, and private planning material.

### Acceptance evidence

- Local install through a spaced Unicode symlink.
- Pinned Git install, update, rollback, uninstall, and duplicate-root rejection.
- Project-local skill collision precedence.
- A live Pi model successfully called `karta_script`.
- Production npm audit reports zero vulnerabilities.

### Deferred proof

The package root is ready to own dispatch and gate assets. The claim that a project skill cannot replace those roles is accepted only when Phase 3 registers and tests the authoritative role entrypoint.

## Phase 2 — trust and host adapters

**Status: complete.**

### Implemented

- A central `tool_call` guard blocks `karta_*` actions before execution when project trust is off. Each action keeps its own trust check as a second boundary.
- Pi write/edit calls are translated into Karta's package-owned binder-immutability and pack-validation hook payloads.
- Invalid proposed pack writes are denied. A successful write/edit that leaves a malformed pack becomes an errored tool result with validator findings.
- Trusted Karta repositories receive neutralized, Git-derived status context before each agent run.
- Package-owned guards run through shell-free, bounded `uv` subprocesses with Python path injection disabled.
- An in-memory lifecycle registry records role, parentage, working directory, and owned resource. It rejects duplicate or orphaned records and aborts all resources before disposal.
- `agent_settled` runs the whiff and dirty-delivery backstops in trusted Karta repositories. A changed finding queues one corrective follow-up; unchanged whiff state cannot loop.
- Pi's non-cancellable shutdown limitation is stated honestly. Git remains the resume path if the operator exits.

### Acceptance evidence

- Unit and integration tests cover trust, guard payload translation, package-owned guard execution, committed binder denial, pack validation, status injection, loop suppression, lifecycle ownership, and multi-resource cleanup.
- A live Pi model attempted to overwrite a committed binder; the tool was denied and the file stayed unchanged.

## Phase 3A — strict child runtime

**Status: complete.**

### 3A-1 — provider policy and exact model resolution

**Status: complete.** Probe and gate policies diverge; the gate path rejects native providers before copying them, requires exact isolated model resolution, and reports its policy and resolution result. Declarative, built-in runtime-key, native-rejection, and missing-model cases pass.

1. Add separate probe and gate runtime policies. Probe behavior stays available for Phase 0 diagnostics; production gate behavior is strict.
2. Reject a registered native provider for a gate. Do not copy its executable provider object into the isolated runtime.
3. Copy declarative provider configuration and runtime credentials using the existing package-owned adapter.
4. Require `runtime.getModel(provider, model)` to return the selected model. Remove the parent-model fallback from the gate path.
5. Return a structured preflight report that names the provider class, auth source, copied configuration, and exact failure without exposing credentials.
6. Unit-test built-in, declarative, runtime-key, missing-model, and native-provider cases.

### 3A-2 — request/header stance

**Status: complete.** An in-memory `GateProviderPreflight` now coalesces concurrent checks, caches only successful provider/model/auth-source results, and runs a real isolated child with no ambient resources or tools. A live stored-auth `openai-codex/gpt-5.6-sol` preflight returned the exact package-owned response. Future gate dispatch must call this preflight before creating either reviewer.

1. Gate children never load parent extensions or replay unknown hooks.
2. Run a package-owned isolated preflight request before dispatching the first gate for a provider/model pair.
3. Cache only the successful compatibility result in memory for the current extension runtime. Do not persist it in Pi or the repository.
4. If direct isolated execution fails, stop with a clear unsupported-provider diagnostic.
5. Do not claim compatibility with parent request/header transformations. Document that those transformations are outside the gate runtime unless represented in declarative provider configuration.

### 3A-3 — OAuth refresh

**Status: complete.** Deterministic tests cross an OAuth expiry boundary while two active runtimes share serialized credentials, then repeat with two independently created file-backed runtimes. Both paths perform one refresh under concurrency and persist the rotated credential only in the temporary auth store.

1. Exercise an OAuth-backed child long enough to cross a token refresh boundary, or use Pi's supported deterministic OAuth fixture if available.
2. Verify the refreshed credential is available to the child without copying a token into session state, logs, tool output, or project files.
3. Run two OAuth children concurrently to expose refresh races.
4. If the direct SDK cannot refresh safely, stop Phase 3 and compare an isolated Pi subprocess with a narrowly integrated delegation runtime. Isolation wins over convenience.

### 3A-4 — real multi-child shutdown

**Status: complete.** A live strict-runtime probe starts four real children: two blocked in separate tool calls, one receiving a model stream, and one idle. Coordinated shutdown aborts both tool signals, interrupts streaming, disposes all four, and leaves zero lifecycle records. Unit failure injection proves one cleanup error cannot strand siblings.

1. Start at least three real children under one lifecycle owner.
2. Keep one child in a tool call, one in model streaming, and one idle but undisposed.
3. Trigger Pi shutdown/reload and verify every child observes abort before disposal.
4. Verify no process, worktree, lifecycle record, or session file remains.
5. Repeat cancellation while one child cleanup throws; all siblings must still be disposed.

### Phase 3A gate

- Native providers fail closed without executing their provider function.
- Gate model lookup has no parent-object fallback.
- Direct isolated provider preflight succeeds for every supported provider class.
- OAuth refresh and concurrent refresh are proved or the SDK approach is replaced.
- Three real active children shut down cleanly under failure injection.
- No gate or lifecycle state is persisted outside Git.

## Phase 3B — dispatch foundation

### 3B-1 — package-owned role catalog

**Progress:** a package-root role catalog now binds acceptance, safety, build, doc-gardner, and kaizen to fixed canonical sources, symbolic capability profiles, output schemas, and SHA-256 source/prompt/definition identities. `karta_dispatch` exposes only role description and read-only gate preflight actions; its schema accepts no prompt, path, tool, provider, or model input. Trust, collision, authority, hash, and preflight tests pass. Actual reviewer dispatch and canonical-skill routing remain in 3C and Phase 4.

1. Define fixed role IDs for acceptance gate, safety gate, build worker, doc-gardner, and kaizen.
2. Bind each role to a package-owned prompt path, capability profile, output schema, and runtime policy.
3. The caller selects only a role and Git-derived work identity. It cannot provide a prompt path, system prompt, tool object, script path, or arbitrary model fallback.
4. Hash role prompts at load time and include the hash in dispatch evidence.
5. Register a package-owned Karta command/tool entrypoint and test its precedence against same-name project skills and commands.

### 3B-2 — cross-process dispatch lock

**Status: complete.** Locks use an atomic directory under the Git common directory, with binder, PID, host, process-start marker, owner nonce, package version, and acquisition time in owner metadata. Release verifies the nonce and renames before removal. Existing, unreadable, absent-owner, foreign, stale, and PID-reuse-shaped locks are never stolen automatically. Tests cover two-process races, independent binders, linked worktrees, Unicode paths, nonce mismatch, stale diagnostics, and slug traversal. Session shutdown releases only leases owned by the current extension runtime.

1. Resolve the repository's Git common directory and create one lock per repository and binder.
2. Acquire atomically before any child, branch, or worktree dispatch. Two Pi processes racing for the same binder must produce one owner and one clear refusal.
3. Store diagnostic metadata only: binder, PID, process start marker, owner nonce, package version, and acquisition time. The lock is coordination state, not delivery progress.
4. Release only when the owner nonce matches. Release in normal completion, cancellation, reload, and shutdown paths.
5. Treat uncertain stale ownership conservatively. Provide an explicit recovery diagnostic instead of silently stealing a lock.
6. Test process death, reboot-shaped stale metadata, PID reuse, Unicode paths, linked worktrees, and two different binders in one repository.

### 3B-3 — host-generated evidence

**Status: superseded by Phase 3D's stronger target model.** The host still validates the integration-tip binder and binds canonical evidence, but evidence may target a staged candidate tree, a committed item tip, or a proposed merge-result tree. Diffs and full touched-file views come from that exact tree. Project packs stay pinned to integration and are resolved into the composed Review checklist (`extends` minus `exclude_rules`, then local rules). Explicit `repo-rule` citations are the only unchanged files added to evidence. Moving refs, staged-tree drift, and merge-tree drift invalidate the verdict.

1. Resolve the binder from Git and validate it before dispatch.
2. Build evidence from Git object IDs, integration tip, item branch tip, diff, acceptance oracle, touched paths, relevant pack hashes, and external-contract boundaries.
3. Hash canonical evidence bytes. Pass the evidence and hash to the child; never ask the child to discover its own target from ambient context.
4. Expose only fixed read actions scoped to the evidence manifest. Reject traversal, symlink escape, missing paths, changed Git tips, and hash mismatch.
5. Revalidate the evidence hash immediately before accepting a verdict.
6. Keep large raw evidence in host memory or a package-owned temporary directory removed on completion. Do not commit it or append it to the Pi session.

### 3B-4 — capability profiles

**Status: gate profiles complete; writer profiles remain for Phases 4–5.** Acceptance receives only `karta_evidence` plus `karta_checks`; safety receives only `karta_evidence` plus `karta_boundary`. `karta_checks` never executes code: it reads an ordered host-generated check manifest whose receipts bind the stable evidence tree, preserving Karta's rule that the floor runs while acceptance inspects. Boundary inspection derives cues without choosing a verdict. Profile hashes include an explicit capability-profile version. Tests prove there is no Bash, write, edit, `karta_script`, caller command, path, ref, prompt, model, provider, environment, or timeout authority.

- Acceptance gate: evidence reads and acceptance runner only.
- Safety gate: evidence reads and boundary inspection only.
- Build worker: trusted coding tools in its assigned worktree.
- Doc-gardner: documentation surface and `.gitignore` only.
- Kaizen: `.karta/sme/` and `.karta/kaizen.json` only.

No child receives the parent `karta_script` tool wholesale.

### Phase 3B gate

- Project collisions cannot select a Karta role prompt.
- Dispatch races are serialized across processes.
- Evidence is hash-bound to exact Git tips and package prompt versions.
- Capability tests prove that gates cannot mutate through tools or path aliases.
- Lock and evidence failures stop dispatch before a model call.

## Phase 3C — read-only gates

**Status: complete.** `karta_dispatch runVerification` accepts only binder slug, item id, and full/boundary-only mode. A cross-process lock is acquired before evidence construction and held through both gates. Full verification runs acceptance then safety against one evidence hash; an acceptance concern/block stops safety, visual oracles become boundary-only, and opt-outs dispatch neither gate. Each fresh gate receives its package role prompt plus a Pi execution contract, exactly two role-owned tools, no ambient resources, and a strict isolated provider runtime. Verdicts must be one exact JSON object bound to evidence, role definition, composed prompt, and capability profile hashes. Host code rejects stale refs, runtime identity changes, skipped required tools, malformed envelopes, unsafe finding paths, and a pass over a failed oracle. Retry classification is host-owned; refs remain untouched. A controlled declarative OpenAI-compatible provider drives real isolated acceptance and safety children through tool calls and final verdicts; five model requests cover preflight, both role-tool rounds, and both final responses. The canonical `karta-verify` skill routes Pi through this fixed entry while preserving existing Claude Code and Codex resolution.

### 3C-1 — acceptance reviewer

1. Load `agents/karta-acceptance-reviewer.md` from the package root.
2. Start a fresh in-memory child under the acceptance capability profile.
3. Evaluate the work item's behavioral oracle, external contracts, and boundary crossings against the evidence manifest.
4. Return structured JSON containing evidence hash, verdict, findings, and retry classification.
5. Reject malformed output, wrong hash, missing fields, or an unsupported provider.

### 3C-2 — safety auditor

1. Load `agents/karta-safety-auditor.md` from the package root.
2. Use a separate fresh child and safety capability profile.
3. Inspect the exact diff and boundary evidence without Bash or project context.
4. Return the same hash-bound structured envelope.

### 3C-3 — verdict handling

1. Gate disagreement does not mutate work directly.
2. A retryable finding returns to the build worker within the existing bounded retry policy.
3. Retry exhaustion halts and reports to the human.
4. Only host code updates refs after both required verdicts bind to the current evidence hash.
5. A moving integration tip invalidates the evidence and forces revalidation.

### Phase 3C gate

- Both gates run with no ambient resources or mutation tools.
- Host-generated evidence and returned verdict hashes match.
- Prompt collision, path escape, stale-tip, malformed-output, and provider-failure tests all fail closed.
- Gate retries preserve existing Karta semantics.

## Phase 3D — align gates with the delivery contract

**Status: complete.** The first gate implementation proved isolation and provider behavior, then exposed four integration mistakes before worker code made them expensive: workers verify before their final commit, floor commands already run outside acceptance, project packs must be composed rather than handed over raw, and delivery-owned verification cannot reacquire its own binder lock. Phase 3D corrects all four. Tests cover staged and merge-result trees, exact-tree commits and merges, bound check receipts, composed rules, touched-file and citation evidence, changed provider configs, and explicit lease reuse.

### 3D-1 — exact candidate targets

1. Build pre-commit evidence from a fully staged Git tree. Refuse unstaged and untracked changes.
2. Keep committed-tip evidence for recovered `built` branches.
3. Build moving-tip evidence from `git merge-tree --write-tree`; gate the proposed merge tree, not a two-tip diff that falsely removes integration-only changes.
4. Before commit or merge, require the resulting tree ID to equal the gated tree ID.

### 3D-2 — check receipts, files, rules, and citations

1. Final floor/oracle commands run under Phase 4 host code in the assigned worktree or proposed-merge worktree.
2. Bind command hash, cwd, target tree, exit status, bounded output, and duration into `karta-check-receipt-v1`.
3. `karta_checks` only reads the receipt. A required missing receipt blocks; a failed receipt cannot pass.
4. Add bounded full content for touched files, addressed only by manifest index.
5. Resolve each pinned pack to normalized composed checklist items with source hashes.
6. Parse `repo-rule:` citations only from changed override markers and add those exact immutable files by manifest index. Missing or omitted citations block safety review.

### 3D-3 — lock and provider ownership

1. Public standalone verification acquires and releases a binder lock.
2. Delivery passes its existing owned lease to internal verification. Internal calls never reacquire, release, or expose the nonce.
3. Provider preflight cache keys include declarative provider configuration identity, so a same-name provider change forces a new live probe.

### Phase 3D gate

- A legacy-order fixture stages and secret-scans a candidate, gates its tree, and commits that exact tree only after a pass.
- A moving integration fixture gates a proposed merge tree and proves integration-only files survive.
- Raw packs, missing check receipts, changed staged trees, bad citations, and nested lock acquisition all fail closed.
- No final commit, merge, or ref may claim a verdict over a different tree.

## Phase 3E — transaction and evidence closure

The early Phase 4 finalizer exposed gaps that must be closed before `buildItem` can become authoritative. **Progress:** evidence v2 now carries an ordered check-manifest v1; worker-result/profile v2 supplies bounded floor proposals and committed instruction provenance; stable-tree checks converge generated artifacts, rerun the final list, and bind equal pre/post/target trees; composed packs carry dependency hashes; and exact candidate commits use `commit-tree` plus expected-old branch/ref updates with repository-format null IDs. Repository hooks now run in a disposable finalization worktree: message-only refinement is retained, while hook failure, tree drift, or worktree residue blocks the real branch. Final commits still use `commit-tree` and expected-old ref updates. Recovery now distinguishes missing refs from Git errors, validates item markers, first-parent merge reachability, accepted trailers/ref pairings, SHA-1/SHA-256 object formats, and returns preserve-first actions for dirty or contradictory state.

A Roundtable critique/convergence was run through the installed Pi MCP adapter. Provider degradation prevented a genuine multi-provider quorum—Antigravity completed while Copilot exited empty, Claude OAuth was expired, and Codex attempts timed out or rejected an unavailable model—so the review is evidence, not claimed consensus. Its converged recommendations agree with the deterministic code review below: host ownership, full-floor receipts, exact-tree commits, strict recovery, and managed process trees move in front of worker orchestration.

### 3E-1 — complete floor evidence

1. Replace the single check receipt with an ordered `karta-check-manifest-v1` containing unique receipts for every final project floor command plus the binder oracle. This changes the enclosing payload to `karta-evidence-v2` and the gate capability profile to v3; mixed versions fail closed.
2. `karta-worker-result-v2` may propose the stack-appropriate floor command/cwd list; that proposal grants no execution or completion authority. Worker profile v2 binds the result to committed project-instruction blob identities. Host code validates bounds and containment, canonicalizes and deduplicates it, adds the immutable binder oracle, and executes the final list itself.
3. Bind each command hash, repo-relative cwd, bounded environment policy, exit/timeout result, output, duration, and final target tree. Acceptance reads the manifest and cannot pass a missing, failed, reordered, duplicate, or stale required check.
4. For visual or explicitly opted-out oracles, retain their existing boundary-only semantics while still recording every applicable project floor command.

### 3E-2 — stable-tree convergence

1. Run the complete host check list, stage permitted outputs, and compute the candidate tree.
2. If a check generated a tracked candidate change, repeat the complete ordered list against the new worktree state. Cap convergence deterministically; never sleep and guess.
3. Once the tree stabilizes, rerun the complete list one final time, require zero tracked/untracked drift, secret-scan the staged diff, and bind all receipts to that exact tree.
4. Gate only the stable tree. A non-converging generator, cancelled process, failed check, or post-check mutation blocks before commit or refs.

### 3E-3 — complete policy evidence

1. Discover `KARTA-SME-OVERRIDE` repo-rule citations from bounded full touched blobs, not only diff hunks, so an unchanged marker in a touched file remains visible.
2. Missing, binary, oversized, ambiguous, or out-of-tree cited artifacts block safety review.
3. Remove the composed-pack cache until its key covers the entire resolved `extends` dependency graph and exclusions. Correctness beats shaving seconds from tests.
4. Pin normalized rule provenance and every dependency hash in evidence.

### Phase 3E gate

- Multi-command, reordered, duplicate, failed, missing, and stale-tree receipt fixtures fail closed.
- A generated tracked artifact either converges and is rechecked on the final tree or reaches the deterministic cap without a commit.
- An unchanged override marker outside the diff hunk still supplies its exact repository-rule citation.
- Changing a base pack changes the resolved evidence hash even when the leaf pack is byte-identical.

## Phase 4 — authoritative build and delivery

### 4A — authority and recovery

**Status: complete.** Git recovery is fail-closed and non-destructive; instruction blobs and isolated provider policy are bound before worker dispatch; host/model authority is explicit.

**Existing foundations:** the Git classifier derives coarse item states; the candidate finalizer demonstrates stage/scan/check/gate/commit/ref-last ordering; the isolated worker has fixed role/profile-bound tools and output. Phase 4 now hardens rather than discards them.

1. The Pi host is the sole owner of staging, scans, final checks, gates, commits, tags, merges, and Karta refs. Model workers edit and self-check only. Apply the same boundary to Phase 5 writers.
2. Upgrade recovery to validate first-parent integration reachability, mandatory item markers, accepted trailer/ref pairing, built/failed/done exclusivity, expected tree identities, and archive transitions.
3. Distinguish a genuinely absent ref from every Git execution, permission, corruption, and object error. Operational failures fail closed.
4. Derive repository object format and null object IDs; support SHA-1 and SHA-256 without hard-coded 40-character assumptions.
5. Load relevant project instructions explicitly, record their blob identities and provenance, and append them below package-owned policy only for writer workers—never gates.
6. Reject native providers and executable ambient provider hooks for workers as well as gates. Declarative provider/model/auth reproduction remains allowed.

### 4B — mutation and process ownership

**Status: complete.** Binder lifecycle owners and child process records share the extension lifecycle registry. Host checks register detached process groups under their binder owner, normal exit forgets them, owner stop performs bounded TERM/KILL, and session shutdown reaches the same resources. Worker sessions are registered beneath their binder owner. Every worker is now bracketed by pre/post authority snapshots covering branch, HEAD, index flags and entries, Karta heads/refs and tags, repository/worktree config, hook identity, worktree registration, host-owned `.karta` paths, and sibling worktree state. Unexpected mutation blocks before result parsing or finalization.

1. File tools deny `.git`, binders, roundtable records, SME packs, and every other host-owned surface not granted by the role. These are guardrails, never a Bash sandbox claim.
2. Snapshot and attest branch, HEAD, index baseline, Karta refs/tags, repository/worktree config, hooks identity, worktree registry, protected paths, and sibling worktrees around every worker. Unexpected authority use blocks finalization.
3. Register a binder lifecycle owner, each worker beneath it, and every spawned process beneath the responsible worker or host operation.
4. Use one Karta-owned process-tree abstraction for host checks, worker Bash, dev servers, visual-validation services, and wave environments. Shutdown performs bounded graceful termination then force-kill and leaves no descendants.

### 4C — exact-tree finalization

**Status: complete.** Clean candidates and gate-capped failed candidates both preserve the reviewed tree through disposable hook validation, `commit-tree`, expected-old branch movement, tree equality assertions, and completion-ref-last ordering.

1. Consume Phase 3E's stable-tree receipt manifest under the existing binder lease.
2. Define one hook policy: run applicable repository checks before gating, then create the reviewed commit without allowing commit hooks to mutate the index. Assert `commit^{tree}` equals the gated tree before any completion ref.
3. Preserve the repository's commit-subject convention while always carrying the mandatory Karta item marker.
4. A gate concern returns bounded findings to the worker. At the acceptance/safety cap, commit only the already scanned stable candidate and write `failed` ref-last; no-change writes no completion ref.
5. Every ref update uses expected-old-object checks and lands only after the durable commit it indexes.

### 4D — fixed `buildItem`

**Status: complete.** `karta_dispatch buildItem` accepts only binder/item identity, takes the binder lease, derives recovery state, creates or resumes the deterministic item worktree, binds the worker session to the binder lifecycle owner, attests worker authority, validates host-owned floor proposals, finalizes under the same lease, applies acceptance-two/safety-three retry caps, and writes `built` or `failed` ref-last. Existing `built`, `done`, and failed/human-decision states do not redispatch. A committed item with no completion ref is rechecked against its exact committed tree, committed-range secret scan, current hooks, floor, and gates before `built` or `failed` moves. A landed two-parent item merge with no `done` is rechecked in a disposable integration worktree against landed-merge evidence before `done` moves. Fast-forward or malformed merge shapes fail closed. Deterministic checkpoints now cover lock/owner/worktree/worker/finalization boundaries plus staging, checks, gates, commit creation, branch movement, and completion refs; injected crashes prove Git-native resume.

1. Add the package-owned fixed entry with binder/item identity only; callers cannot select prompts, tools, paths, commands, providers, or models.
2. Acquire or receive the binder lease, validate the binder, derive the Git frontier, and create or resume the existing item worktree without clobbering it.
3. Dispatch the isolated worker, attest its return, run exact-tree finalization, and apply Karta's existing acceptance and safety retry caps.
4. Recover committed-without-marker, built, failed, no-change, and interrupted finalization states from Git alone.
5. Write every durable branch/commit/ref checkpoint before returning success.

### 4E — waves and serial integration

**Status: complete.** `karta_dispatch deliverBinder` now accepts only binder identity, holds one binder lease and lifecycle owner, derives dependency-ready frontiers from Git, parallelizes non-colliding items, serializes declared collision surfaces, and integrates built items FIFO. Each integration is checked on a disposable materialization of `git merge-tree --write-tree`, secret-scanned, gated against merge-tree evidence, reproduced through merge hooks, and written as an exact two-parent `commit-tree` merge followed by `done` ref-last. Ref-first integration crashes classify as `merged-unmarked`; deterministic integration-worktree repair handles the exact clean parent-index shape. Every wave now writes an expected-old base tag before integration, reruns its deduplicated floor plus the package-owned shared-term check on the assembled tip, and writes a success tag only after both pass. Failure prepares the owned worktree, atomically restores integration while deleting that wave's `done`/`built` refs, and leaves the base tag as the audit/recovery anchor. Failed items now route only through the live host UI: fix-and-rerun deletes the expected failed ref, defer preserves it and stops, and accept displays the fresh exact findings, requires confirmation plus a bounded human reason, independently reruns the non-waivable safety gate, stamps finding-specific trailers, reruns the post-accept floor, then writes `done`, deletes `failed`, and writes `accepted` last. Crashes before the last ref recover from the merge trailers and rerun committed-range secret scan, full floor, accepted-finding matching, and safety. Wave rollback removes `accepted` and restores `failed` instead of manufacturing `built`. Binder archival intentionally remains after Phase 5's enabled companion writers, so it can be the final host-owned integration commit.

1. Add the fixed binder delivery entry. Hold one binder lease while dependency-ready workers execute in parallel waves; serialize declared collision surfaces.
2. Keep the integration tip single-writer and process built items FIFO.
3. Derive and materialize each proposed merge-result tree from the current integration tip and item tip. Conflicts stop before refs move.
4. Run Phase 3E's full floor and both gates on that exact proposed tree, then create a real no-ff merge and assert tree equality.
5. Update `done`/`accepted`/`failed` refs and wave tags in the existing order with expected-old-object checks. Preserve first-parent and trailer doctrine.
6. On post-wave failure, restore branch and all affected refs exactly as the existing revert protocol requires. Archive only after every item is durably done.

### 4F — interruption behavior

**Progress:** deterministic checkpoints now cover lock/owner creation, first file edit, worker attestation, convergence/manifest binding, gates, commit and merge creation, branch movement, accepted refs, wave tags, and atomic rollback. Fresh Node processes classify injected commit/build/merge/done states from Git alone. Shutdown is one idempotent coordinator: guards and preflight close first, children and registered process groups abort next, only owned locks release afterward, and the extension claim releases last even on abort failure. Hook-bearing Git processes run in owned detached groups; a hook-synchronized test proves binder shutdown kills the hook and its descendant. Competing delivery owners cannot enter one binder lease. Managed dev-server/wave-environment shutdown fixtures remain; archive checkpoints follow the Phase 5 writer-before-archive sequence.

1. Deterministic injection points cover owner/worker/process creation, first edit, attestation, every convergence pass, receipt binding, gate completion, commit creation, branch update, merge, each ref/tag update, archive move, and archive commit.
2. Tests synchronize on hooks, never timing sleeps.
3. Shutdown aborts children and owned process trees, then releases only owned locks. It never invents progress.
4. A fresh Pi process resumes every injected state from Git with no session data.

### Phase 4 gate

- Forged refs/trailers, protected-path mutations, Git failures, and SHA-256 fixtures fail closed.
- Forced shutdown during worker, check, dev-server, and merge preparation leaves no child or process descendant.
- Parallel waves preserve dependencies while integration remains serial and moving-tip safe.
- Two Pi processes cannot deliver the same binder concurrently.
- Every crash fixture resumes from Git alone.
- Existing Claude Code and Codex behavior remains unchanged.

## Phase 5 — narrow writers

### 5A — doc-gardner

1. Respect `.karta/doc-gardner.json`; absent or disabled means no run.
2. Use the package-owned prompt and a capability-limited writer child.
3. Permit only README, `docs/`, `AGENTS.md`, `ARCHITECTURE*`, top-level Markdown, and `.gitignore` according to the existing confinement contract.
4. The child returns edits only. Host code attests the surface, runs applicable checks, creates the exact-tree commit on integration, and returns its ID.
5. Any out-of-surface mutation fails closed and discards the disposable writer worktree before integration moves.

### 5B — kaizen

1. Respect `.karta/kaizen.json`; absent or disabled means no run.
2. Preserve Karta's seed, inheritance, override, and never-weaken rules.
3. Permit only `.karta/sme/` and `.karta/kaizen.json`.
4. Run package validation before and after edits through host-owned checks.
5. Host code commits only an attested exact-tree `kaizen:` change for human review; the child never commits or writes refs.

### Phase 5 gate

- Writer path aliases, symlinks, Bash indirection, and Git path options cannot escape the declared surfaces.
- Disabled writers create no child, worktree, or commit.
- Writer commits preserve existing Claude Code and Codex semantics.

## Phase 6 — release

### 6A — compatibility matrix

Run native tests on:

- current macOS;
- Linux;
- Windows, including directory-symlink privilege handling;
- paths with spaces, Unicode, symlinks, and linked worktrees;
- stored, environment, runtime-key, declarative custom, and OAuth provider classes, each through a complete gate tool-call and verdict roundtrip rather than an auth-only probe;
- staged-tree and merge-tree plumbing under each native Git implementation, including SHA-1 and SHA-256 repositories;
- shutdown during host checks, worker commands, dev servers, merge preparation, and provider streams;
- TUI, print, JSON, and RPC modes where relevant.

Container or compatibility-layer results may supplement native runs but do not replace them.

### 6B — package lifecycle

1. Test local install and pinned Git tag/commit install outside the checkout.
2. Test update, rollback, uninstall, duplicate local/Git sources, and cache cleanup.
3. Verify the npm tarball inventory and production audit.
4. Keep `private: true` and `UNLICENSED` until publication is explicitly approved.
5. Pin the tested Pi development version while leaving Pi package peers at `*` as required by Pi packages.

### 6C — documentation

1. Add `docs/how-to/pi.md` with install, trust, commands, provider support, resume, lock recovery, and uninstall instructions.
2. Update README runtime coverage without implying worktrees are sandboxes.
3. Document unsupported native providers and request/header-transform behavior plainly.
4. Document that process quit cannot be blocked and Git is the recovery source.
5. Add a release checklist and support matrix.

### Phase 6 gate

- Native OS and package lifecycle tests pass.
- All repository validators and Pi tests pass.
- Production audit reports zero vulnerabilities.
- Documentation matches the shipped commands and limitations.
- Publication remains blocked until licensing and ownership approval are explicit.

## Final completion criteria

Karta's Pi package is complete only when:

- an approved project can plan, build, verify, deliver, resume, run doc-gardner, and run kaizen through package-owned fixed entrypoints;
- an untrusted project cannot execute Karta actions;
- project collisions cannot replace authoritative role prompts;
- gates are genuinely read-only and hash-bound to exact Git evidence;
- workers preserve the existing wave, serial integration, retry, rollback, marker, and ref-last protocols;
- a fresh process resumes entirely from Git;
- no active child survives shutdown;
- Claude Code and Codex projections and validators remain clean; and
- the native release matrix passes.
