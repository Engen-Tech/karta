# AGENTS.md — working on karta

karta is a stack-agnostic orchestration framework shipped for Claude Code, Codex CLI, and Pi. It plans a binder of work items, delivers it in parallel waves onto a per-binder integration branch, builds each item in an isolated git worktree, and gates each one against its own acceptance check. This file orients an agent editing karta itself; end-user usage lives in `README.md` and `docs/how-to/`.

## Layout — canonical vs generated

Some files are hand-edited (canonical); others are generated projections you must never hand-edit. Edit the canonical, then run the generator.

| Path | Role | Edit? |
|-|-|-|
| `skills/<name>/` | Skills — canonical, Claude-native | yes |
| `.agents/skills/<name>/` | Codex repo-local skill mirror — generated, byte-identical | no — run `sync_codex_skills.py` |
| `agents/<name>.md` | Agents — canonical (Claude registered subagents). Three read-only gates + two writers: `karta-doc-gardner` (docs) and `karta-kaizen` (stack packs) | yes |
| `.codex/agents/<name>.toml` | Codex registered subagent — generated. `sandbox_mode` is derived from the agent's `tools` (Write/Edit → workspace-write; else read-only) | no — run `sync_codex_agents.py` |
| `skills/<spawn-site>/references/<name>.agent.md` | Agent instructions bundled in the agent's sole spawn-site skill (Codex plugin-install fallback) — generated. Gates → `karta-verify`; gardner → `karta-doc-gardner`; kaizen → `karta-kaizen` (see `BUNDLE_SITE` in `sync_codex_agents.py`) | no — run `sync_codex_agents.py` |
| `plugins/karta/` | Codex marketplace install projection — generated real directory. The marketplace points here (`./plugins/karta`) because Codex CLI expects plugin entries under a child path, and real files work on Windows/macOS/Linux | no — run `sync_codex_skills.py` |
| `skills/_shared/<f>.md` | Shared reference text — canonical | yes |
| `skills/_shared/sme/<id>.md` | Built-in stack packs — stack (`match`) + rule (`always: true`); canonical, copied byte-equal into karta-plan/build/verify `references/sme/` | yes |
| `skills/_shared/sme/platform-native.md` | Shared reference data the packs link to via `see_also` — not a pack | yes |
| `skills/<name>/references/<f>.md` | Per-skill copy of a `_shared` file | no — keep byte-equal |
| `.claude-plugin/` | Claude plugin + marketplace manifests | yes |
| `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json` | Codex plugin + repo marketplace manifests | yes (keep name/version in step with `.claude-plugin/plugin.json`) |
| `.codex-plugin/hooks/` | Codex hooks manifest and guard scripts — canonical, hand-maintained twins of the Claude guards in `hooks/scripts/` (same rule, Codex's payload shape) | yes — run `sync_codex_skills.py` to refresh the `plugins/karta/` mirror after editing |
| `package.json`, `extensions/pi/`, `tests/pi/` | Pi package manifest, first-party runtime adapter, and compatibility tests — canonical | yes |

Why committed mirrors and not symlinks: Codex does not detect symlinked skills on Windows (openai/codex#8400), so `.agents/skills/` and the marketplace install projection under `plugins/karta/` are real directories kept in sync by the generator and guarded by the validator.

Externally managed cross-runtime skills are the exception to `.agents/skills/` ownership. A skill with a complete entry in `skills-lock.json` may be committed under `.agents/skills/` alongside its `.claude/skills/` and `.pi/skills/` copies. The generator preserves these locked external skills, does not compare them with `skills/`, and never ships them in `plugins/karta/`.

## After you edit

- Edited a skill (including its `references/`, `scripts/`, or `agents/openai.yaml`): run `uv run scripts/sync_codex_skills.py`.
- Edited an agent (`agents/*.md`): run `uv run scripts/sync_codex_agents.py`, then `uv run scripts/sync_codex_skills.py` (the bundled `*.agent.md` lives inside the agent's spawn-site skill, so that mirror changes too).
- Edited a `skills/_shared/*.md`: copy it into each consuming skill's `references/` (keep them byte-equal), then run the skills mirror.
- Edited `package.json`, `extensions/pi/`, or `tests/pi/`: run `npm run check:pi`.

## Before you commit

All five must be clean.

Mac/Linux/Windows, local terminal from the repository root:

```
uv run scripts/validate_plugin.py --self-test
uv run scripts/check_shared_copies.py --self-test
uv run scripts/sync_codex_agents.py --check
uv run scripts/sync_codex_skills.py --check
npm run check:pi
```

The validator also runs the two `--check` paths itself, so a green `validate_plugin.py` already implies the projections are in sync; the explicit `--check` calls are here for a faster signal while iterating.

## Before Pi package changes go remote

Any change to the Pi package, runtime, tests, or operator docs must pass the packed-artifact smoke test on the exact local tree before it is pushed, tagged, or otherwise sent to a remote.

Mac or Linux, local terminal in the Karta checkout:

```sh
npm run smoke:pi-package
```

The smoke test must use the installed `pi` executable, an isolated Pi agent directory, and the artifact produced by `npm pack`. A checkout loaded directly with `-e` does not satisfy this gate.

## How work reaches the default branch

One rule, and it has one exception. **Everything lands on the default branch through a branch and a
merge — except a fully committed binder**, which may be committed directly. A binder is the plan of
record, not a change to the framework: its JSON plus the roundtable record filed alongside it is the
whole commit, and the binder-commit gate already governs it.

Everything else goes on a branch first. Code, hooks, scripts, docs — no direct commits.

| What | Branch | Who merges it |
|-|-|-|
| A karta delivery | `karta/<slug>/integration`, assembled by karta-deliver | the human — the landing gate blocks the merge and `KARTA_LANDING_APPROVED=1` must prefix it |
| Ordinary work — a fix, a doc, a script | a plainly-named branch (`fix/…`, `docs/…`), merged `--no-ff` | whoever is doing the work |
| A binder | none needed | committed directly, with its review record |

**Do not borrow the `karta/*/integration` namespace for work that is not a delivery.** That name is
what both merge gates match on, so using it for an ordinary fix manufactures a landing-gate block
for something no one planned as a delivery, and it misdescribes the change in the audit trail.

The rule is here because it was broken. On 2026-08-24 a one-line hook fix — independently reviewed,
59/59 on its own self-test, clean on all four floor commands — was committed straight to main. The
change was right and the route was wrong. Note what that says about enforcement: the repo's gates
cover binder commits and delivery merges, and neither one looks at an ordinary commit landing on
main. Nothing would have stopped it, which is why the rule is written down rather than assumed.

## Review before commit (house-only)

karta's own binders and deliveries get a multi-perspective review before they land. This is a house rule for the karta repo building itself — consumer repos never carry it.

**Standing direction: every karta binder is reviewed before commit, always**, and the same before landing a delivery branch on main. The stakes are the framework itself: a flawed binder propagates into every consumer repo. That requirement has not changed. What performs the review has.

### What runs today

`.karta/roundtable.json` carries `enabled: true`, restored on 2026-08-23 after the multi-provider environment came back (a connectivity probe that day returned seven providers, all `ok`, against a `min_providers` floor of 2). **Both enforced review gates fire again**: a commit staging `.karta/binders/<slug>.json` and a `git merge` landing a `karta/*/integration` branch each need a fresh record of that exact content, committed alongside it. A third gate in the same hook, the landing gate, is not a review gate and never reads that switch; see "Two human approvals" below.

Two reviews now run, and they are not the same thing. Only the first is enforced.

| Review | What it is | Enforced? |
|-|-|-|
| Roundtable | several *different* models answering the same prompt, recorded under `.karta/roundtable/` | yes — the gate blocks the commit without a fresh record |
| Multi-lens panel | six adversarial lenses, all the *same* model, each finding re-verified against the repo | no — run it because it is worth running |

```
Workflow({ scriptPath: 'scripts/review/binder_review_panel.js',
           args: { binder: '<slug>', focus: '<optional extra lens>' } })
```

The panel stayed after the switch flipped because it does something the roundtable cannot: every lens opens the actual source and runs the actual commands, so a finding either cites a `file:line` or it does not survive the verify phase. An external panel reads what you paste it. Use both on anything whose numbers matter — the roundtable for independent judgement, the panel for findings that had to be proven against the tree.

**A panel result is never a roundtable record.** Every lens is the same model wearing a different hat, so it cannot meet the `min_providers` floor. Never pipe it to `scripts/roundtable/run_review.py --record`, and never file it under `.karta/roundtable/`. The two are different kinds of evidence and conflating them would make the audit trail lie. This rule did not relax when the switch came back on — it is the reason the switch matters.

### What the off period cost, kept for the record

Say it plainly, because it cuts against karta's own doctrine of enforced checks over skippable prose. While `enabled` was false nothing blocked, and the review depended on whoever was at the keyboard choosing to run it — exactly the strength the old prose-only disclosure had, and it was not enough. Binders were reviewed in that window, but by choice rather than by gate. The switch being back on is what makes "every binder is reviewed" a fact about the repo rather than a habit of its maintainer.

### Two human approvals, and only one of them is enforced

A karta delivery asks a person to decide twice. The two are not equally protected, and reading them as one thing is how a decision gets taken quietly.

| Decision | Who makes it | What holds the line |
|-|-|-|
| Accept an item that failed its own acceptance gate | the human, at a live orchestrator prompt | **Enforced in code.** The orchestrator issues the prompt itself; an accept signal appearing anywhere in worker output is non-authoritative and is ignored. The waiver's reason is the human's own words captured at the prompt — never lifted from worker text, a commit message, or a marker. The waiver suppresses only the named assertion, and a fresh floor check on the post-accept tip can still revert it. |
| Land the integration branch on the default branch | the human | **Enforced in code.** `scripts/hooks/roundtable_gate.py`'s landing gate blocks a `git merge` naming a `karta/*/integration` ref while you are on the default branch. It reads neither `.karta/roundtable.json` nor `KARTA_SKIP_ROUNDTABLE` — a downed review environment says nothing about who decides a delivery ships. Its own variable, `KARTA_LANDING_APPROVED=1`, must prefix the merge itself: `KARTA_LANDING_APPROVED=1 git merge --no-ff --no-edit karta/<slug>/integration`. |

Say the consequence plainly, since this repo's own history is the example. `watch-fidelity` reached `main` at `ff800d8` through an agent-run `git merge --ff-only`, after the agent ran the four floor commands by hand. The accept-waiver on `design-fidelity-gate` was a real human decision, made at a real prompt. The landing was not — no one was asked.

**The landing is the human's call**, and since 2026-08-19 that is a gate rather than a paragraph. An agent that runs the merge is standing in for a decision the doctrine assigns to a person. Ask first, in the same session, the way the accept prompt asks. If a merge does happen without asking, report it as what it was at the moment it happens — a run that says "merged, floor green" and omits who decided has substituted itself for the person and hidden the substitution.

#### What the landing gate does and does not do

It fires on a `git merge` naming a `karta/*/integration` ref while HEAD is the default branch, and it exits 2 unless `KARTA_LANDING_APPROVED=1` prefixes that same invocation (or sits in the environment) — the landing command is `KARTA_LANDING_APPROVED=1 git merge --no-ff --no-edit karta/<slug>/integration`. Two deliberate narrowings, both of which cost something:

- **Anchored, not searched.** The ref has to head its own shell segment, after any `VAR=value` prefix. Without this the gate blocks its own maintenance: a merge command inside a heredoc, an `echo`, or a `grep` pattern is text, and the first thing this gate did when it went live was refuse a command that merely quoted one. The cost is that an invocation buried mid-segment — behind a `do`, an `xargs` — reads as text and is not caught.
- **Approval must prefix the merge, as an exact assignment word.** Both variables are read the same way — `NAME=1` (bare, `'1'` or `"1"`) sitting in front of the git invocation, or in the environment — and a lookalike is not a grant: `X=KARTA_LANDING_APPROVED=1`, `KARTA_LANDING_APPROVED=10`, a quoted `FOO="KARTA_LANDING_APPROVED=1"`, or the text of a commit message. This one grants authority, so an accidental grant is worse than an accidental block.

And the limits worth saying out loud, because the gate is not a proof:

- **It cannot tell an agent from a human.** A PreToolUse hook sees command text. An agent that sets `KARTA_LANDING_APPROVED=1` has forged an approval it was never given. The gate makes the moment impossible to pass through *silently*; the rule against forging it lives in doctrine, in this file and in CLAUDE.md.
- **It shares the documented bypasses.** `git cherry-pick`, `git rebase`, `git reset --hard` reach the same end and are not `git merge`.
- **It fails open.** Like every hook here, an internal error exits 0 rather than wedging the repo.

### The roundtable machinery, for when it returns

Switched off, not removed — still present and still correct. The gate was deterministic: it enforced one fact, *a fresh recorded review of this exact content exists*, never the panel's verdict, so disagreeing with findings never blocked and only skipping the review did.

#### The four points, when enabled

| Point | Git event | Treatment |
|-|-|-|
| Plan (binder) | commit staging `.karta/binders/<slug>.json` | enforced edict |
| Deliver (integration branch) | `git merge` landing a `karta/*/integration` branch on the default branch | enforced edict |
| Verify (a built diff) | none | helper-available (advisory) |
| Standalone (ad hoc) | none | helper-available (advisory) |

Plan-commit and deliver-merge have a real commit to block, so those are the two that are gated. Verify and standalone have no commit or stop moment to hang an edict on, so they get the same one-command helper with no hard gate.

#### Running it

The tool per point is configured in `.karta/roundtable.json` (default `roundtable-critique`). A script cannot run roundtable — it is an MCP tool the agent calls. So the flow is three steps:

1. Run the roundtable panel on the target (the staged binder, or the integration-branch diff).
2. After each round, keep it: `... | python3 scripts/roundtable/run_review.py --round --target <slug-or-branch> --fixed "..." --refuted "..."` appends the round — every provider's verdict or the reason it gave none, what was fixed, what was refuted — to `.karta/roundtable/<key>.rounds.json`.
3. On the final round, file the record: `... | python3 scripts/roundtable/run_review.py --record --target <slug-or-branch>`. It refuses a record the ledger's last round did not review.

The gate then confirms the record with `run_review.py --check`. The `min_providers` floor keeps "multi-model" honest: a panel with fewer than `min_providers` distinct providers is not a review, and the recorder refuses to file it. `.karta/roundtable/context-economy.rounds.json` — thirteen rounds on one binder — is the worked example of what the ledger holds.

#### Rules the gate enforced

- **Records must be committed.** The recorder stages the record under `.karta/roundtable/`, and the binder-commit gate requires it to be in the same commit — read from the same source git will commit the binder from, so a record a pathspec or `--only` leaves out does not count. A record that lives only in the working tree does not satisfy the gate — `.karta/roundtable/` is the committed audit trail.
- **Binder freshness keys on the bytes git will commit.** The binder gate hashes the staged blob for a plain commit, the working-tree file for `-a` or a pathspec that names the binder, and `HEAD`'s copy for a pathspec that does not — decided by `git ls-files`, never by token matching. Review one version of the binder and stage a different one, and the gate re-arms — you must re-review what you are actually committing.
- **With `ledger: true`, the rounds must be committed too.** `.karta/roundtable.json` carries `ledger: true` here, so both gates additionally require the round ledger — `.karta/roundtable/<slug>.rounds.json` in the content being committed, `branch-<tip>.rounds.json` in `HEAD` for a merge — with a last round that reviewed exactly the bytes the record reviewed, and a record bound to that final round. A missing or stale ledger is its own named denial pointing at `run_review.py --round`; a malformed one is a denial too, never a fail-open. `KARTA_SKIP_ROUNDTABLE=1` bypasses it exactly as it bypasses the record check — one hatch, not two.
- **The gate recognises one command shape and denies the rest.** `git commit …`/`git merge …` are parsed with a whitelist of options and root-relative pathspecs, from the repository root, with a message (`-m`/`-F`, or `--amend --no-edit`; a merge needs `--no-edit` or `-m` unless `--ff-only`). A preceding or trailing segment, a substitution, an unquoted expansion, a redirection, a relocating `git -C`/`GIT_*` prefix, or an unknown option such as `-am` is denied by name. The cost is over-denial of unusual spellings; the gain is that what the gate approved is what git records.
- **The merge gate is narrow.** It fires only for a `git merge` naming a `karta/*/integration` branch while you are on the default branch. Nothing else trips it. A merge that names the tip by SHA, and `git pull`, do not match it — that limit fails open and is named here rather than assumed closed.

#### Accepted bypasses

A PreToolUse hook sees a command before it runs, so it can only match command text and read current git state — it cannot judge a post-condition like "will this make the integration tip an ancestor." So these paths are **not** gated, by design, and are the same class of deliberate escape as the hatch below: landing integration content via `git cherry-pick`, `git rebase`, or `git reset --hard`; and a `git merge --squash` followed by a separate `git commit`. The doctrine names them plainly rather than pretending the gate is airtight.

#### Escape hatch

When the roundtable environment is down, or you need a deliberate partial commit, set `KARTA_SKIP_ROUNDTABLE=1` — as a leading assignment prefix on the git command, or in the environment — and the gate allows the command. The hook also fails open on any internal error: a broken hook never wedges the repo. With the switch back on the hatch is live again and it is the only way past a review gate, so reach for it deliberately and say why in the commit — an unexplained `KARTA_SKIP_ROUNDTABLE=1` is a review that did not happen.

### When the retroactive panel rejects a hatch-committed binder

The hatch defers the review; it never waives it. So the panel can come back with blockers against a plan that is already committed, and a committed binder is read-only — `guard_binder_immutability.py` denies the edit, and that guard stays.

The sanctioned path is **withdrawal, not a history rewrite**. Commit the deletion of the binder file, then commit the corrected plan under the same slug together with its review record. The binder gate skips the deletion commit by construction (a deleted path has no staged plan to review), and both commits move forward, so the audit trail keeps the rejected plan, its rejection, and its replacement. Resetting or amending the offending commit destroys that trail and only works while it is unpushed.

Full operator guide: [docs/how-to/roundtable.md](docs/how-to/roundtable.md).

## Kaizen dogfood policy (this repo)

Kaizen is enabled here (`.karta/kaizen.json`) under a scoped policy, because this repo authors the built-in packs while also consuming karta like any other project:

- karta is an ordinary consumer of its own framework: its `.karta/sme/` carries a project pack `.karta/sme/karta-house-minimalism.md` that declares `extends: minimalism` and narrows one rule locally, exactly the way any consumer repo tailors a built-in. A change to the built-in rule itself is only ever made upstream in `skills/_shared/sme/minimalism.md`, by a human — never by drifting a repo-local copy of the pack.
- `.karta/sme/karta-house-skill-authoring.md` is this repo's own non-coding pack (reserved `karta-house-*` namespace, so it can never collide with a built-in). It is the pack kaizen is expected to actually evolve; its edits are reviewed like any `kaizen:` commit.
- Never seed built-in copies here: the repo carries zero seeded built-in copies under `.karta/sme/`, only its own `karta-house-*` project packs; deliveries pin what their binders pin.

## Three runtimes, one behavior

The gate agents are read-only on every install. On Claude Code and on Codex-with-`.codex/agents/`, they run as registered subagents (`sandbox_mode = "read-only"`). On a Codex plugin install — where plugins cannot register subagents — `karta-verify` spawns a read-only subagent using the bundled `references/*.agent.md`. Keep that adaptive dispatch intact when editing `skills/karta-verify/SKILL.md`.

Pi has no registered-agent projection. Its adapter must load the canonical package-owned gate prompts into fresh child sessions with explicit read-only tools, in-memory settings, and no ambient skills, extensions, project context, or parent conversation. A project-local skill with the same name may affect conversational guidance, but it never becomes an authoritative Karta gate agent.
