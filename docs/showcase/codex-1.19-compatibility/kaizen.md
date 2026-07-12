# Kaizen on Codex 1.19: compatibility test certified after resume

Karta 1.19 Kaizen passed this Codex test-drive after a resumed delivery run. The final run used the installed plugin, dispatched the bundled fallback writer, seeded exactly the binder's pinned packs, and landed the required `kaizen:` commit on the supplied integration branch. A separate direct run proved that the fallback writer can edit an existing project pack without overwriting, committing, pushing, or opening a PR.

The first delivery attempt remains recorded below. It failed because the main turn wrote the packs itself and committed in a second clone. That failure was not accepted as delivery evidence. The resumed run used a fresh API-key Codex home and met the original contract without that workaround.

## Final result

- Switch absent: PASS
- Switch disabled: PASS
- Direct seed from detected stack: PASS
- Delivery seed from binder pins: PASS on resumed run
- Existing project-pack edit through fallback writer: PASS on resumed run
- Skipped current scenarios: none
- Failed current scenarios: none
- Repository floor: PASS
- Gate attempt 1: acceptance and safety findings corrected in this record

## Test envelope

- Date: July 12, 2026
- Item base after resume: `cbe20698c88ca88649cc5e441d0355ccfce26295`
- Shell CLI: `codex-cli 0.143.0`
- App CLI used for live installed-plugin runs: `codex-cli 0.144.0-alpha.4`
- Model: `gpt-5.4-mini`
- Fresh isolated `CODEX_HOME`: `/private/tmp/karta-kaizen-codex-resume-20260712/codex-home`
- Authentication mode: API key
- Installed plugin: `karta@karta-local 1.19.0`
- Installed plugin root: `/private/tmp/karta-kaizen-codex-resume-20260712/codex-home/plugins/cache/karta-local/karta/1.19.0`
- Installed `karta-kaizen/SKILL.md` SHA-256: `d5947b9682ddeed3e570790cc31ed51b412da1b9a3589e956d9a39c482337f97`
- Resumed scratch root: `/private/tmp/karta-kaizen-codex-resume-20260712`
- Preserved failed-run root: `/private/tmp/karta-kaizen-codex-evidence`

The fresh Codex home installed Karta from the rebased item worktree's local marketplace projection. Remote featured-plugin refreshes returned `401` because API-key authentication does not cover the ChatGPT plugin catalog. The local plugin still loaded: both live turns emitted `karta:karta-kaizen` skill-injection telemetry and read the installed cache above.

## Live command shape

The resumed runs used this shape:

```sh
CODEX_HOME=/private/tmp/karta-kaizen-codex-resume-20260712/codex-home \
  /Applications/ChatGPT.app/Contents/Resources/codex exec \
  --ephemeral --json --color never --ignore-rules \
  -m gpt-5.4-mini -s danger-full-access \
  -C <scratch-repo> '<exact prompt recorded with each scenario below>'
```

The disposable scratch repositories were the only writable test targets. `danger-full-access` allowed the orchestrating turn to write Git metadata for the required integration-branch commit; the earlier `workspace-write` run had blocked that commit. This setting does not provide Claude's exact two-path writer hook. The observed child-agent diffs, described below, are the evidence that these Codex fallback writers stayed within `.karta/sme/`.

## Scenario evidence

### 1. Switch absent: PASS

- Repo: `/private/tmp/karta-kaizen-codex-resume-20260712/off-absent`
- Baseline and final `HEAD`: `b2141efaad0e873fd8dfeec449306c62580287d0`
- Codex thread: `019f572b-d3dc-7343-b25c-e39cdd7e9dcb`
- Persisted final output: `/private/tmp/karta-kaizen-codex-resume-20260712/off-absent-last.txt`
- Result: `.karta/kaizen.json` was absent; no writer was dispatched; `git status --short` was empty.

Exact prompt:

```text
Use $karta:karta-kaizen from the installed Karta 1.19.0 plugin in direct mode on this repository. The switch is intentionally absent. Follow the skill exactly, do not create the switch, and report whether any writer was dispatched and whether anything changed. Return the baseline and final HEAD, git status, and whether any push or PR occurred.
```

Raw JSON event evidence, excerpted from the complete `--json` stdout:

```json
{"type":"thread.started","thread_id":"019f572b-d3dc-7343-b25c-e39cdd7e9dcb"}
{"type":"item.completed","item":{"id":"item_4","type":"command_execution","aggregated_output":"absent\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_5","type":"agent_message","text":"karta:karta-kaizen did not run because the switch file is absent ... I did not dispatch any writer ... Baseline HEAD: b2141efa... Final HEAD: b2141efa... Anything changed: no ... Any push or PR: no"}}
{"type":"turn.completed"}
```

The complete event stream contained no `collab_tool_call` item. The bounded thread began, checked the switch and Git state, reported the no-op, and ended.

### 2. Switch disabled: PASS

- Repo: `/private/tmp/karta-kaizen-codex-resume-20260712/off-disabled`
- Baseline and final `HEAD`: `c52f26d335638c004ec702b05a7e6f16544eacb8`
- Codex thread: `019f572c-097e-7042-9f88-b001dda82dd7`
- Persisted final output: `/private/tmp/karta-kaizen-codex-resume-20260712/off-disabled-last.txt`
- Result: the switch contained `{"enabled": false}`; no writer was dispatched; `git status --short` was empty.

Exact prompt:

```text
Use $karta:karta-kaizen from the installed Karta 1.19.0 plugin in direct mode on this repository. The switch is intentionally disabled. Follow the skill exactly, do not enable or edit the switch, and report whether any writer was dispatched and whether anything changed. Return the baseline and final HEAD, git status, and whether any push or PR occurred.
```

Raw JSON event evidence, excerpted from the complete `--json` stdout:

```json
{"type":"thread.started","thread_id":"019f572c-097e-7042-9f88-b001dda82dd7"}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","aggregated_output":"{\"enabled\": false}\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_7","type":"agent_message","text":"Writer dispatched: no ... Anything changed: no ... Baseline HEAD: c52f26d... Final HEAD: c52f26d... Push occurred: no ... PR occurred: no"}}
{"type":"turn.completed"}
```

The complete event stream contained no `collab_tool_call` item. The bounded thread read the disabled switch, checked `HEAD` and status twice, reported the no-op, and ended.

### 3. Direct seed from detected stack: PASS

- Repo: `/private/tmp/karta-kaizen-codex-resume-20260712/direct-seed`
- Baseline `HEAD`: `443bb83c51e0c0d4d6c74704d0272c385be149b9`
- Final `HEAD`: `443bb83c51e0c0d4d6c74704d0272c385be149b9`
- Main thread: `019f572c-4925-70e1-9d73-0bfa682b8220`
- Fallback child: `019f572c-c3d0-76b0-9aab-5432b0b09055`
- Persisted final output: `/private/tmp/karta-kaizen-codex-resume-20260712/direct-seed-last.txt`
- Detected tokens: dependency `vue`; languages `javascript` and `node`
- Resolved set: project-owned `minimalism` plus installed `vue`
- Result: the child seeded only `.karta/sme/vue.md`; the existing `minimalism` copy stayed unchanged; `.karta/sme/vue.md: OK`; no commit.

Exact prompt:

```text
Use $karta:karta-kaizen from the installed Karta 1.19.0 plugin in direct mode on this repository. Follow the installed skill exactly. Kaizen is enabled and this repo has no registered Karta agents, so dispatch the bundled fallback writer as a fresh write-capable subagent; the main turn must not edit pack files itself. Resolve the direct-mode pack set using the installed detect_stack script plus always-on packs, preserve every existing project-owned pack, seed every missing resolved pack as a full file, and validate every changed pack with the installed validator. Direct mode must leave changes uncommitted for review. Never push and never open a PR. If fallback writer dispatch does not occur, report failure. Return the writer envelope, detected tokens, resolved pack ids and sources, validator result, diff paths, commit before and after, status, and whether any push or PR occurred.
```

The first spawn call had a malformed tool envelope and created no child. The retry produced these raw events:

```json
{"type":"item.started","item":{"id":"item_19","type":"collab_tool_call","tool":"spawn_agent","sender_thread_id":"019f572c-4925-70e1-9d73-0bfa682b8220","receiver_thread_ids":[],"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_19","type":"collab_tool_call","tool":"spawn_agent","receiver_thread_ids":["019f572c-c3d0-76b0-9aab-5432b0b09055"],"status":"completed"}}
{"type":"item.completed","item":{"id":"item_21","type":"collab_tool_call","tool":"wait","agents_states":{"019f572c-c3d0-76b0-9aab-5432b0b09055":{"status":"completed","message":"seeded: true\npacks_changed:\n  - vue\nresidual: none\nsummary: Seeded .karta/sme/vue.md from the installed source and validated it successfully."},"status":"completed"}}
{"type":"item.completed","item":{"id":"item_24","type":"command_execution","aggregated_output":"?? .karta/sme/vue.md\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_26","type":"command_execution","aggregated_output":".karta/sme/vue.md: OK\n","exit_code":0,"status":"completed"}}
{"type":"turn.completed"}
```

Writer envelope:

```yaml
seeded: true
packs_changed: [".karta/sme/vue.md"]
residual: []
summary: "Seeded .karta/sme/vue.md from the installed source and validated it successfully."
```

### 4. Delivery seed from binder pins: PASS on resumed run

- Supplied repo: `/private/tmp/karta-kaizen-codex-resume-20260712/delivery`
- Supplied branch: `karta/delivery-evidence/integration`
- Baseline `HEAD`: `f4ff2c8367520f6ed4c90bc89775759ccf6a69a1`
- Final `HEAD`: `3a79ec59341573238322058bd534f2fa4a2cc738`
- Main thread: `019f5721-8b93-7841-8ffe-a0c85478f005`
- Fallback child: `019f5722-10aa-70a3-8d13-98addeed9007`
- Persisted final output: `/private/tmp/karta-kaizen-codex-resume-20260712/delivery-last.txt`
- Binder pins: `minimalism`, `python`
- Commit subject: `kaizen: seed 2 packs into .karta/sme/`
- Changed paths: `.karta/sme/minimalism.md`, `.karta/sme/python.md`
- Final status: clean, one commit ahead of the local baseline remote
- Push or PR: none

Exact prompt:

```text
Use $karta:karta-kaizen from the installed Karta 1.19.0 plugin in delivery mode for binder slug delivery-evidence on the current supplied integration branch. Follow the installed skill exactly. This repository has no registered Karta agents, so dispatch the bundled fallback writer as a fresh write-capable subagent; the main turn must not write pack files itself. Read exactly the binder sme list, validate every changed pack with the installed validator, and land changes as a labeled kaizen: commit on this exact current branch. Do not clone, copy to, or commit in another repository. Never push and never open a PR. If fallback writer dispatch does not occur or the labeled commit cannot land here, report failure. Return the writer envelope, validator result, commit id, subject, changed paths, branch, and whether any push or PR occurred.
```

The installed skill emitted a `spawn_agent` event with the bundled writer instructions, the supplied repo root, and exactly the two pinned source paths. The first dispatch attempt carried an unsupported `priority` service tier and did not create a child. The retry created the child above. That child seeded the two files, returned its envelope, and the main turn validated both files before committing them on the supplied branch.

Raw JSON event evidence:

```json
{"type":"item.started","item":{"id":"item_21","type":"collab_tool_call","tool":"spawn_agent","sender_thread_id":"019f5721-8b93-7841-8ffe-a0c85478f005","receiver_thread_ids":[],"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_21","type":"collab_tool_call","tool":"spawn_agent","receiver_thread_ids":["019f5722-10aa-70a3-8d13-98addeed9007"],"status":"completed"}}
{"type":"item.completed","item":{"id":"item_23","type":"collab_tool_call","tool":"wait","agents_states":{"019f5722-10aa-70a3-8d13-98addeed9007":{"status":"completed","message":"seeded: true\npacks_changed:\n  - minimalism\n  - python\nresidual: none\nsummary: Seeded .karta/sme/minimalism.md and .karta/sme/python.md from the pinned references. The pack validator passed on both files."},"status":"completed"}}
{"type":"item.completed","item":{"id":"item_26","type":"command_execution","aggregated_output":".karta/sme/minimalism.md: OK\n.karta/sme/python.md: OK\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_28","type":"command_execution","aggregated_output":"[karta/delivery-evidence/integration 3a79ec5] kaizen: seed 2 packs into .karta/sme/\n 2 files changed, 46 insertions(+)\n","exit_code":0,"status":"completed"}}
{"type":"turn.completed"}
```

Writer envelope, normalized to the skill's field names:

```yaml
seeded: ["minimalism", "python"]
packs_changed:
  - ".karta/sme/minimalism.md"
  - ".karta/sme/python.md"
residual: []
summary: "Seeded the two pinned packs. Both files passed the pack validator."
```

Validator output:

```text
.karta/sme/minimalism.md: OK
.karta/sme/python.md: OK
```

Independent checks confirmed that both committed pack files are byte-identical to their installed sources, the commit's parent is the supplied baseline, and no `.codex/agents` directory exists in the scratch repo.

### 5. Existing project-pack edit: PASS on resumed run

- Repo: `/private/tmp/karta-kaizen-codex-resume-20260712/existing-pack`
- Branch: `main`
- Baseline and final `HEAD`: `c0140c338040e76d3938e7b1a7eaeec65d34e559`
- Main thread: `019f5722-d86d-7593-b99d-1bdd616c15a0`
- Fallback child: `019f5723-1aa6-7092-b64a-f865fdb85367`
- Persisted final output: `/private/tmp/karta-kaizen-codex-resume-20260712/existing-pack-last.txt`
- Detected tokens: dependencies `[]`; languages `[]`
- Resolved set: project-owned always-on `minimalism` only
- Changed path: `.karta/sme/minimalism.md`
- Commit: none, as required in direct mode
- Push or PR: none

Exact prompt:

```text
Use $karta:karta-kaizen from the installed Karta 1.19.0 plugin in direct mode on this repository. Follow the installed skill exactly. Kaizen is enabled and this repo has no registered Karta agents, so dispatch the bundled fallback writer as a fresh write-capable subagent; the main turn must not edit pack files itself. Resolve the direct-mode pack set using installed detect_stack plus always-on packs, preserve the existing project-owned minimalism pack, and give the fallback writer this concrete phase-one instruction: add exactly one advisory Patterns bullet to the existing .karta/sme/minimalism.md saying Name scratch fixtures for the behavior they prove, not the tool that created them. Make no other content change. Validate every changed pack with the installed validator. Direct mode must leave the change uncommitted for review. Never push and never open a PR. If fallback writer dispatch does not occur, report failure. Return the writer envelope, detected tokens, resolved pack ids and sources, validator result, diff paths, commit before and after, status, and whether any push or PR occurred.
```

The installed skill dispatched the concrete phase-one instruction to add one advisory Patterns bullet: `Name scratch fixtures for the behavior they prove, not the tool that created them.` The first spawn attempt used an incompatible full-history agent envelope and created no child. The retry created the child above. The child changed exactly that one line in the existing project pack. The main turn left the edit uncommitted and validated the result:

Raw JSON event evidence:

```json
{"type":"item.started","item":{"id":"item_15","type":"collab_tool_call","tool":"spawn_agent","sender_thread_id":"019f5722-d86d-7593-b99d-1bdd616c15a0","receiver_thread_ids":[],"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_15","type":"collab_tool_call","tool":"spawn_agent","receiver_thread_ids":["019f5723-1aa6-7092-b64a-f865fdb85367"],"status":"completed"}}
{"type":"item.completed","item":{"id":"item_20","type":"collab_tool_call","tool":"wait","agents_states":{"019f5723-1aa6-7092-b64a-f865fdb85367":{"status":"completed","message":"packs_changed: minimalism\nresidual: none\nsummary: Added one advisory Patterns bullet to .karta/sme/minimalism.md."},"status":"completed"}}
{"type":"item.completed","item":{"id":"item_22","type":"command_execution","aggregated_output":"c0140c3\n M .karta/sme/minimalism.md\n... + Name scratch fixtures for the behavior they prove, not the tool that created them.\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_23","type":"command_execution","aggregated_output":".karta/sme/minimalism.md: OK\n","exit_code":0,"status":"completed"}}
{"type":"turn.completed"}
```

```text
.karta/sme/minimalism.md: OK
```

The existing project copy won over the installed built-in. Independent checks confirmed that `HEAD` did not move, `.karta/kaizen.json` and `README.md` did not change, no `.codex/agents` directory exists, and `.karta/sme/minimalism.md` is the only modified path.

## Preserved first delivery failure

The July 11 installed-plugin delivery attempt did not meet the contract and remains a failed historical attempt.

- Supplied repo: `/private/tmp/karta-kaizen-codex-evidence/cli-delivery`
- Supplied branch: `karta/delivery-evidence/integration`
- Supplied baseline and final `HEAD`: `f4ff2c8367520f6ed4c90bc89775759ccf6a69a1`
- Main thread: `019f526e-37f3-7143-8ffa-df006acb2a9f`
- Supplied repo final status: untracked `.karta/sme/minimalism.md` and `.karta/sme/python.md`; no delivery commit

That turn resolved the correct binder pins but never dispatched the fallback writer. The main turn wrote both files itself. Its `workspace-write` sandbox then blocked the required Git commit, so it cloned the repo to `/private/tmp/karta-kaizen-codex-evidence/cli-delivery-commit` and committed `5f55131dd3a3fcc017d3770cfc9b84e909ae83bf` there.

That second-repository commit is not counted as delivery. The resumed run above supersedes it by dispatching the child and committing on the original supplied integration branch.

## Codex confinement is weaker than Claude's hook

The installed Codex plugin manifest has no `hooks` key, the installed cache contains no Karta hook files, and the scratch repositories had no registered Karta agents. The fallback dispatch therefore used the bundled `karta-kaizen.agent.md` instructions with a fresh normal worker.

Those instructions restrict the writer to `.karta/sme/` and `.karta/kaizen.json`, and both resumed child diffs obeyed that rule. Codex did not enforce the exact two-path boundary with a hook. Its sandbox governs the broader workspace. Claude's `guard_writer_confinement.py` hook enforces the two-path rule before a recognized Kaizen writer edit; that hook did not run here.

This certification proves the installed Codex fallback behavior and observed file boundaries. It does not claim hook parity.

## Final item gates

The corrected report passed the repository floor:

```text
codex-cli 0.143.0
PLUGIN INTEGRITY: PASS
SHARED COPIES: IN SYNC
CODEX AGENTS: IN SYNC
CODEX SKILLS PROJECTIONS: IN SYNC
```

Gate attempt 1 found two evidence-record defects. They were corrected without changing Karta or the scratch-repository outcomes:

- Acceptance returned `DEVIATION` because the report omitted exact prompts and raw execution events. The correction reran the first three scenarios in the fresh Codex home, recorded every exact prompt, added persisted final-output paths, and embedded the dispatch, wait, validation, commit, and terminal event evidence for all five scenarios.
- Safety returned `VIOLATION` because the report called later gate results “pending.” The correction removed that unresolved placeholder and records the finding here as closed review history.

Neither finding was waived or converted to declared debt.
