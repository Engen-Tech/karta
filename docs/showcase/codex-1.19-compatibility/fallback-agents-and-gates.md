# Codex 1.19 fallback agents and gates

Result: **PASS on fix-and-rerun.** The first attempt halted on a Codex usage-limit error and remains recorded below. A user-authorized fix-and-rerun used a fresh API-key `CODEX_HOME`; every contract scenario then produced its required verdict and filesystem boundary.

## Test environment

- Codex: `codex-cli 0.143.0`
- Model selected by Codex: `gpt-5.5`
- Karta plugin: `karta@karta-local`, version `1.19.0`, installed and enabled
- Installed plugin cache: `/private/tmp/karta-codex-119-fallback-scratch/codex-home/plugins/cache/karta-local/karta/1.19.0`
- Scratch repository: `/private/tmp/karta-codex-119-fallback-scratch/gate-pass`
- Registered Karta agents in the scratch repository: `0`
- Scratch diff before the run: `src/status.txt` changed from `pending` to `ready`

The plugin was installed into an isolated `CODEX_HOME`. `codex plugin list --json` reported:

```json
{
  "pluginId": "karta@karta-local",
  "name": "karta",
  "marketplaceName": "karta-local",
  "version": "1.19.0",
  "installed": true,
  "enabled": true
}
```

The scratch repository contained no `.codex/agents` file. Its only non-Git files had these SHA-256 hashes before the run:

```text
36b5adf745189e9c075fd15153ad1f560444644fa3a81eb9530c019c75da5355  ./.karta/binders/fallback-gate.json
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

## Failed live scenario

Exact command:

```sh
CODEX_HOME=/private/tmp/karta-codex-119-fallback-scratch/codex-home codex exec --ephemeral --sandbox read-only --color never -o /private/tmp/karta-codex-119-fallback-scratch/gate-pass.last.md '<prompt below>'
```

Exact prompt:

```text
Use the installed Karta 1.19.0 plugin skill $karta-verify in full mode. Delegation is explicitly authorized: spawn the required fresh read-only fallback subagents because this repository intentionally has no .codex/agents. Use only the bundled agent instruction files from the installed plugin. Inputs: repo root /private/tmp/karta-codex-119-fallback-scratch/gate-pass; binder .karta/binders/fallback-gate.json; work item set-ready; diff range HEAD. Do not edit any file. Return the acceptance-reviewer report and YAML envelope, the safety-auditor report and YAML envelope, the stack-pack provenance line, and the aggregate karta-verify verdict.
```

Exit status: `1`.

Relevant output, preserved exactly:

```text
OpenAI Codex v0.143.0
--------
workdir: /private/tmp/karta-codex-119-fallback-scratch/gate-pass
model: gpt-5.5
provider: openai
approval: never
sandbox: read-only
reasoning effort: none
reasoning summaries: none
session id: 019f5261-0b1a-7bf1-86d0-378a2fd0bc79
--------
user
Use the installed Karta 1.19.0 plugin skill $karta-verify in full mode. Delegation is explicitly authorized: spawn the required fresh read-only fallback subagents because this repository intentionally has no .codex/agents. Use only the bundled agent instruction files from the installed plugin. Inputs: repo root /private/tmp/karta-codex-119-fallback-scratch/gate-pass; binder .karta/binders/fallback-gate.json; work item set-ready; diff range HEAD. Do not edit any file. Return the acceptance-reviewer report and YAML envelope, the safety-auditor report and YAML envelope, the stack-pack provenance line, and the aggregate karta-verify verdict.
ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 6:28 PM.
ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 6:28 PM.
```

Codex produced no acceptance-reviewer envelope, safety-auditor envelope, stack-pack provenance line, or aggregate verdict. This is a failed scenario under the binder's evidence rule, not a pass.

## Filesystem and Git check

After the failed command, the same two files had the same hashes:

```text
36b5adf745189e9c075fd15153ad1f560444644fa3a81eb9530c019c75da5355  ./.karta/binders/fallback-gate.json
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

Git still reported only the intended fixture diff, ` M src/status.txt`. `refs/heads/main` remained at `f2f61f6366928235853f1459d3820ca1b5759167`.

## Attempt 1 stopped at the first failure

The first test drive stopped at the first failure, as required. It did not run the safety fallback, doc-gardner fallback, unknown-pack halt, or missing-checklist BLOCKED scenario. No result from that partial attempt was treated as a pass.

The user later chose Karta's Phase-4 `fix-and-rerun` path. The retry below starts from new scratch repositories and new authentication; it does not reuse the partial attempt as evidence.

## Attempt 2 environment

- Codex: `codex-cli 0.143.0`
- Model: `gpt-5.4-mini`
- Karta: `karta@karta-local` version `1.19.0`, installed and enabled
- Installed plugin cache: `/private/tmp/karta-codex-119-fallback-rerun/codex-home/plugins/cache/karta-local/karta/1.19.0`
- Scratch root: `/private/tmp/karta-codex-119-fallback-rerun`
- Authentication: API key through an isolated `CODEX_HOME`; no credential value is stored here

Each scenario used a fresh Git repository. Each repository contained zero files under `.codex/agents`.

## Acceptance and safety fallbacks

The gate fixture changed only `src/status.txt` from `pending` to `ready`. Its oracle command passed before dispatch:

```sh
test "$(cat src/status.txt)" = ready
```

The repository had these file hashes before the gate:

```text
c5b7d53b072f8369bfff3fb808034a0d99f752475d0a30ab113221fb76263b5a  ./.karta/binders/fallback-gate.json
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

Exact installed-skill command:

```sh
CODEX_HOME=/private/tmp/karta-codex-119-fallback-rerun/codex-home codex exec --ephemeral --sandbox read-only --model gpt-5.4-mini --color never -o /private/tmp/karta-codex-119-fallback-rerun/gate-pass.output.md '<prompt below>'
```

Exact prompt:

```text
Use the installed Karta 1.19.0 plugin skill $karta-verify in full mode exactly. This scratch repository intentionally has no .codex/agents. Delegation is explicitly authorized: resolve and spawn the required fresh read-only fallback subagents using only the bundled acceptance-reviewer and safety-auditor agent files inside the installed karta-verify skill. Inputs: repo root /private/tmp/karta-codex-119-fallback-rerun/gate-pass; binder .karta/binders/fallback-gate.json; work item set-ready; diff range HEAD. The binder pins minimalism: resolve its built-in Review checklist and pass the normalized checklist items to the safety fallback. Do not edit any file or ref. Return the full acceptance report and YAML envelope, then the full safety report and YAML envelope including the Stack-pack check provenance line, then the aggregate karta-verify verdict.
```

Umbrella Codex session: `019f547f-5272-7021-90c5-3233c359b35f`.

The umbrella run returned:

```text
## Karta Acceptance + Contract: Set status ready

**Verdict:** CONFORMANT

**Assertion disposition:**
- assertion 1 — `The actual diff changes src/status.txt from pending to ready and does not change another file.` — inspection-verifiable — CONFORMS

**Contract conformance:**
- n/a (no contract)

verdict: pass
routing_hints:
  next: karta-safety-auditor
  kickback_to: null
top_blockers: []

## Karta Boundary Scan: Set status ready

**Verdict:** PASS

Stack-pack check: ran — pinned: [minimalism]; resolved: [minimalism]; items judged: 4

verdict: pass
routing_hints:
  next: null
  kickback_to: null
top_blockers: []

Aggregate karta-verify verdict: `pass`
```

Two fresh external read-only host workers separately loaded the installed bundled agent instructions and confirmed the same result:

- Acceptance session `019f5480-55a1-7a23-aa93-1cb4d94888f7`: `CONFORMANT`, `verdict: pass`, no blockers.
- Safety session `019f5480-ba6a-7df1-9c11-d23c4ae13bfe`: `PASS`, `Stack-pack check: ran — pinned: [minimalism]; resolved: [minimalism]; items judged: 4`, no blockers.

After all three read-only sessions, the file hashes were unchanged, Git still reported only ` M src/status.txt`, and `refs/heads/main` still pointed to `fc41c1579939b39e916463bdf1cee9ee069c1e8c`.

The read-only Codex sandbox printed macOS developer-tool cache warnings because it could not create `/tmp/xcrun_db-*`. Every `git diff` still completed and returned the expected one-file diff; the warnings did not change the tree or verdicts.

## Doc-gardner fallback

The doc-gardner fixture committed a code change from `pending` to `ready` while leaving this stale sentence in `README.md`: `The current status is pending.` Before dispatch, the hashes were:

```text
802d1a819e83bf78a8ddfd6e8c96ec0b06dd2dccdf1ffe4cc6cc13044a657b42  ./README.md
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

Exact command:

```sh
CODEX_HOME=/private/tmp/karta-codex-119-fallback-rerun/codex-home codex exec --ephemeral --sandbox workspace-write --model gpt-5.4-mini --color never -o /private/tmp/karta-codex-119-fallback-rerun/doc-gardner.output.md '<prompt below>'
```

Exact prompt:

```text
Use the installed Karta 1.19.0 plugin skill $karta-doc-gardner in ad-hoc mode. This scratch repository intentionally has no .codex/agents, and this fresh external session is the write-capable fallback host worker. Read the installed skill, then use its bundled references/karta-doc-gardner.agent.md as your complete writer instructions. Inputs: repo root /private/tmp/karta-codex-119-fallback-rerun/doc-gardner; diff range HEAD^..HEAD; focus: keep the status sentence accurate. Correct documentation drift in place. Edit only the declared documentation surface; do not touch code, tests, binders, .karta, git refs, or commits. Return the required corrected_count/files_changed/residual/summary envelope only after verifying the result.
```

Codex session: `019f5481-92d7-74e2-8725-e66ab2744a81`.

Returned envelope:

```yaml
corrected_count: 1
files_changed: ["README.md"]
residual: []
summary: "Updated the status sentence in README.md to match the landed change in src/status.txt."
```

After dispatch, `README.md` said `The current status is ready.` and its hash changed to `9613ed87167e1262da0b20923bdc4dc6545650713dc05f7ee85a7b06c47233b6`. `src/status.txt` kept its original hash, Git reported only ` M README.md`, `HEAD` stayed at `4eb8200ceeffe246dfefc281b0d2a918bc33407a`, and the only ref stayed `refs/heads/main` at that commit. The writer touched no code, tests, binders, `.karta`, commits, or refs.

## Unknown stack-pack id

The negative fixture pinned `missing-pack-119`. Exact command:

```sh
CODEX_HOME=/private/tmp/karta-codex-119-fallback-rerun/codex-home codex exec --ephemeral --sandbox read-only --model gpt-5.4-mini --color never -o /private/tmp/karta-codex-119-fallback-rerun/unknown-pack.output.md '<prompt below>'
```

Exact prompt:

```text
Use the installed Karta 1.19.0 plugin skill $karta-verify in boundary-only mode exactly. This scratch repository intentionally has no .codex/agents. Delegation is authorized if the installed skill reaches a fallback dispatch. Inputs: repo root /private/tmp/karta-codex-119-fallback-rerun/unknown-pack; binder .karta/binders/unknown-pack.json; work item set-ready; diff range HEAD. Resolve every pinned stack pack using the project-overlay-over-installed-built-in rule before any safety dispatch. Do not edit any file or ref. Return the actual boundary-only report or halt, including every unresolved pack id and the aggregate verdict.
```

Codex session: `019f5482-4ab9-76c3-812f-dca01ecefaaf`.

Returned outcome:

```text
blocked

Boundary-only verification halted during SME resolution, before any safety-auditor dispatch.

- Unresolved pack id: `missing-pack-119`
- Resolution result under the project-overlay-over-installed-built-in rule: no project overlay and no built-in pack found
- Aggregate verdict: `blocked`
```

The pre/post hashes stayed identical:

```text
e192698f353eb5941a90c594d5ff9a26c6d1feeb72eea5e33dccb00bc990df9d  ./.karta/binders/unknown-pack.json
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

Git still reported only ` M src/status.txt`; `refs/heads/main` stayed at `88a0c3ab6e1b8114737f3f7daa98391ce168a33c`.

## Missing stack-pack checklist

The final negative fixture pinned `minimalism`, but its fresh safety fallback was intentionally dispatched without any normalized checklist input. Exact command:

```sh
CODEX_HOME=/private/tmp/karta-codex-119-fallback-rerun/codex-home codex exec --ephemeral --sandbox read-only --model gpt-5.4-mini --color never -o /private/tmp/karta-codex-119-fallback-rerun/missing-checklist.output.md '<prompt below>'
```

Exact prompt:

```text
Use the installed Karta 1.19.0 plugin skill $karta-verify to execute its no-registered-agent safety fallback as a fresh read-only host worker. Your complete safety-agent instructions are the installed file /private/tmp/karta-codex-119-fallback-rerun/codex-home/plugins/cache/karta-local/karta/1.19.0/skills/karta-verify/references/karta-safety-auditor.agent.md; read and follow them exactly. Inputs: worktree /private/tmp/karta-codex-119-fallback-rerun/missing-checklist; binder .karta/binders/missing-checklist.json; work item set-ready; diff range HEAD. Negative-test condition: the dispatcher supplies no stack-pack Review checklists at all. Do not resolve or add them yourself; judge the dispatch exactly as received. Do not edit any file or ref. Return the required full report, mandatory Stack-pack check provenance line, and YAML envelope only.
```

Codex session: `019f5482-d808-7a32-b3c7-4f20949d9bc1`.

Returned report and envelope:

```text
## Karta Boundary Scan: Missing checklist fixture

**Verdict:** BLOCKED

Stack-pack check: blocked — pinned: [minimalism]; resolved: []; items judged: 0

verdict: blocked
summary: "The diff itself is small and limited to the declared fixture status file, but the safety gate cannot complete because the binder pins a stack-pack review checklist and none were dispatched."
routing_hints:
  next: null
  kickback_to: null
  reason: "Pinned stack-pack input is missing, so the fallback must fail closed."
top_blockers:
  - "stack-pack check blocked: pinned minimalism but no checklists dispatched"
```

The pre/post hashes stayed identical:

```text
c0ae39e8dcdf05acdfdf5ba83387e95205fa3c1fbb424031f5ed72161d851c4b  ./.karta/binders/missing-checklist.json
ed1a545bb85e55816bbf9566b028b2a0bc456b88f49f6f266c0401048824194b  ./src/status.txt
```

Git still reported only ` M src/status.txt`; `refs/heads/main` stayed at `3f8d17c064ebe3cad9e836d19c0df9c35b13fdd3`.

## Retry verdict

The fix-and-rerun passed every required scenario:

- Both read-only gate fallbacks judged the binder and actual diff in fresh external sessions, returned their required verdicts, and left the repository byte-for-byte unchanged.
- The write-capable doc-gardner fallback changed only `README.md` and returned a clean envelope.
- The unknown stack-pack id halted with `missing-pack-119` named.
- The missing-checklist dispatch returned `BLOCKED`, named `minimalism`, and reported `items judged: 0` rather than skipping the check.

No scenario in attempt 2 failed or was skipped.
