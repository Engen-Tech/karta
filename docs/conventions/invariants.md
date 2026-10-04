# The invariant register

One entry per invariant karta holds — founding and newly adopted, none privileged over the rest.
The register is a map with an honesty column, not a second telling: each entry states the rule in
one sentence, names where the telling lives (its **carriers**), and says plainly what fires when
the rule is violated. The prose that argues for each rule stays woven where it always was — in
README.md, AGENTS.md, the skill docs — because that is where a reader meets it in context. This
file exists so that "what are karta's invariants, and which of them are actually enforced?" is
answered by one page instead of a trawl of the whole tree.

**Provenance shorthand.** *founding* — present since the doctrine was first written. *2026-08-31
review* — adopted after a doctrine review that trawled the project's whole history (27 archived
binders, the roundtable ledgers, 658 commits, 20 backlog investigations, 137 session transcripts)
and a seven-provider roundtable (Antigravity, Codex, DeepSeek, GLM, Kimi, MiniMax, Qwen) that
deliberated and converged on each candidate.

**Reading an entry.** *Kind*: **rule-authoring** entries govern how any rule here gets written;
**operational** entries govern the plan, the run, review, landing, the tree, and the writers.
*Status* is the honesty column, and it uses four words exactly:

- **enforced** — a named check blocks the violation.
- **partial** — some violations are blocked; the named remainder is not.
- **prose** — stated, and nothing fires. The gap is real and usually has a backlog pointer.
- **prose by design** — stated, nothing will ever fire, and the entry says why.

Status describes the strongest install — Claude Code with the plugin's hooks. Codex ships bundled
twins of most of them, enforced once the user trusts the plugin's hooks; what stays doctrine there
(writer confinement among it) is stated once in README's "Enforcement below the agent" and the
parity table in docs/how-to/codex.md. Pi enforces its own subset through its extension surface and
advises where Pi cannot refuse a stop; its parity table is in docs/how-to/pi.md. So this file
doesn't restate the split 27 times.

*Level* names how a specific host carries a rule; it uses five words exactly:

- **host-enforced** — the host itself supplies the boundary, such as a read-only sandbox.
- **hook-enforced** — a deterministic hook blocks the recognized violation.
- **instruction-level** — the agent is told the rule, but no host boundary or hook proves it.
- **disabled** — the mechanism exists but is deliberately switched off.
- **untested** — the claimed host behavior has not been exercised by this repository's tests.

## How this file stays true

Nothing in a repository is immutable; what can be enforced is that nothing here mutates
*silently*. Three layers, stated in the order they exist:

1. **Binding by content, today.** Every carrier is named by file plus a short distinctive phrase
   from the woven sentence itself. Reword the sentence and the entry's phrase no longer matches —
   so a change to any invariant's prose is a two-file diff, register beside carrier, visible to
   the reviewer of the branch it must arrive on. Today that visibility is held by review alone:
   **no checker verifies this file yet**, and per invariant 2 the register says so rather than
   implying otherwise. For delivery diffs, checklist rule inv.1 in
   `.karta/sme/karta-house-invariants.md` makes the same-diff rule a judged checklist item.
2. **A checker, planned.** The intended check is small and has a precedent in this suite:
   `check_shared_copies.py` already holds prose byte-equal across locations on every commit. The
   register checker does the same at phrase grain — every carrier file exists, every quoted phrase
   is present, every named enforcement script exists — joining the commit hook's gate list so
   silent drift becomes a commit-time denial (backlog item 21). When it lands, this paragraph changes and entry 20's
   status improves; until then, both stand as written.
3. **The limit, named.** A phrase check proves presence, not meaning. Prose around an intact
   sentence can still reframe it, and no grep will notice. That last grain is what branch review
   is for — and why every change to a doctrine file arrives by branch and merge, never directly.

**Changing the register.** An entry changes only in the same commit as its carriers. Ids are never
renumbered or reused. An invariant leaves only by a recorded human decision: the entry stays,
marked retired, with the reason and date — deletion would erase the decision along with the rule.

---

## Rule-authoring

### INV-1 · Enforced checks over skippable prose
A rule that matters gets a check that fires; prose alone is a request, not a rule.
- founding · **prose by design** — this is the criterion the other entries' status column applies; it cannot check itself.
- Carriers: README.md "Enforcement below the agent" ("Skills still state every rule; hooks are the backstop"); .karta/sme/karta-house-skill-authoring.md ("Give every new invariant an enforcement point").

### INV-2 · Doctrine never claims more than enforcement delivers
When a sentence promises a check no code performs, the sentence is the defect: shrink the claim or build the check.
- 2026-08-31 review · **prose by design** — it governs authoring; its instances are checkable, it is not. This register's status column is its standing application.
- Carriers: README.md "Enforcement below the agent" ("never claims more than its backstop delivers"); .karta/sme/karta-house-skill-authoring.md ("never claim more than that").
- The sentence-scale case is real: a validator whose error text promises containment its code never tests (backlog item 6).
- Test: read the doctrine sentence and ask *what fires if this is violated?* If nothing, rewrite until true.

### INV-3 · Identity is proven by content, never inferred from circumstance
A check that asks "is this the thing I think it is?" compares bytes; recency, ordering, position, and response time are guesses wearing a badge.
- 2026-08-31 review · **prose by design** — the principle; its instances carry their own status (INV-14 enforced, INV-16 partial). This repo's release gate also checks the evidence bytes Git will commit and a source fingerprint, including file modes and Git-normalized content, as described in benchmarks/README.md ("Release evidence is read from the bytes Git will commit").
- Carriers: AGENTS.md, binder-freshness rule ("identity is proven by content — a hash of the exact bytes"); .karta/sme/karta-house-invariants.md (inv.3, the build-time application).
- Three costumes, one root: a hash file with no review behind it (backlog item 4); a verdict with no diff range (item 19); a slow probe answer read as a foreign occupant (item 7).
- A caveat that is INV-2 applied here: a matching hash proves what a verdict is *about*, never that the review behind it happened — provenance is a separate claim.
- Test: find every "is this current / mine / the same?" and ask whether the answer is a content address or a circumstance.

### INV-4 · An exception is the whole commit or it is a hole
An exception defined by what a commit *contains* ("has a binder in it") leaks — content rides in around the qualifying item. An exception defined by what the commit *is, entirely* ("consists of exactly a binder plus its review record") cannot. Name the edges (completing an in-progress merge is not a direct commit) instead of leaving them to be guessed.
- 2026-08-31 review · **prose by design** — governs the wording of every exception; the route exception it was distilled from is INV-17.
- Carriers: AGENTS.md "How work reaches the default branch" ("names something a commit *contains* is a hole"); .karta/sme/karta-house-invariants.md (inv.2).
- Test: does the exception describe the *entire* action it excuses, or one feature of it? One feature is a hole.

### INV-5 · Repetition triggers a decision, never a rule
A repeated pattern forces a recorded decision — promote it, mark it deliberately optional with the reason, or reject it. What is forbidden is not optional conventions; it is *undecided* ones. A constant observed in one environment is configuration; only the principle is doctrine.
- 2026-08-31 review · **prose by design** — a judgment rule; no code can make the decision, only demand that one exists.
- Carriers: AGENTS.md, kaizen dogfood policy ("repetition triggers a decision, never a rule"); .karta/sme/karta-house-invariants.md (inv.4).
- The cost of no decision: one binder wrote `depends_on: null`, twenty-six wrote `[]`, and nothing ever ruled which is right.
- Test: when a pattern repeats, ask who decides, where the decision is recorded, and which of the three outcomes it got.

## The plan

### INV-6 · The binder is the plan of record
Items, acceptance contracts, waves, and pinned packs live in the binder JSON; no other document defines the work, and a binder is read-only once committed — between waves as much as during one. A plan a wave proved wrong is repaired by a successor binder (INV-28), never by an edit.
- founding · **partial** — supported edit-tool writes to committed binders are hook-enforced after normalized, resolved target classification, including aliases and hard links across repository worktrees. Shell and plain-terminal writes remain outside that enforcement. karta-deliver reads the binder and never writes it.
- Carriers: README.md "The binder"; skills/karta-plan/SKILL.md ("The binder is read-only once committed"); skills/karta-deliver/SKILL.md ("sanctioned repair path is a successor binder").

### INV-7 · A binder is valid by the exact bytes being committed
Binder validation and review freshness key on the staged blob git will commit — never on a working-tree copy the commit may not match, never on token matching.
- 2026-08-31 review · **partial** — freshness is enforced by the review gate. Schema validity is helper-backed: `validate_binder.py` loads `binder-schema.json`, and delivery preflight halts on an invalid binder. The commit hook still does not validate a hand-edited binder.
- Carriers: AGENTS.md ("Binder freshness keys on the bytes git will commit").

### INV-8 · One slug, one place
A slug's binder lives under `.karta/binders/` or its archive, never both — including in the tree a prospective merge would produce, which is where the pair actually gets created.
- 2026-08-31 review · **prose** — no check exists; the motivating incident is a binder surviving its own landing twice through a 3-way merge (backlog item 14).
- Carriers: none yet — this entry is the statement of record until the check lands.

### INV-28 · A committed binder is repaired only by a successor that proves what it carries
A plan a wave proved wrong is replaced by a new binder whose top-level `supersedes` names the predecessor; carried items are copied unchanged and count as done only when predecessor refs prove it.
- **partial** — helper-backed by `deliver_preflight.py`; retiring the predecessor remains instruction-level.
- Carriers: skills/karta-deliver/SKILL.md; docs/how-to/binder-repair.md.

## The run

### INV-9 · Run state lives in git and nowhere else
Every item outcome is a ref under `refs/karta/<slug>/`, a wave tag, or a commit marker; resume reads git, not a side database, so the run's memory survives any session.
- founding, made explicit 2026-08-31 · **enforced** — by construction: the delivery has no other store to consult.
- Carriers: skills/karta-deliver/SKILL.md ("the `refs/karta/` ref namespace"); skills/karta-deliver/references/integration-branch.md.

### INV-10 · An acceptance failure is waived only by a human, at the orchestrator's own prompt
The orchestrator asks through the host's user-input facility; any accept signal in worker output is non-authoritative and ignored, the waiver's reason is the human's words captured at the prompt, and the waiver suppresses only the named gap — the post-accept floor is never waived.
- founding · **partial** — the human half is instruction-level: the deliver skill prescribes the prompt, but the orchestrator is a model following that procedure, and nothing authenticates that a person answered it; the `Karta-Accept-*` trailers are text any writer can produce. The structural half is helper-backed: `check_item_provenance.py --check-accepted` requires an `accepted` ref to sit on a first-parent `done` merge of the integration branch carrying both trailers, and flags trailer-bearing commits off that chain; delivery preflight, the status engine, and the Stop guard apply it (INV-30). It proves the accept's shape, not who decided it.
- Carriers: skills/karta-deliver/SKILL.md ("The human channel is required, not asserted" — it names itself an instruction, not a check); AGENTS.md "Two human approvals" ("Instruction-level, with a structural check after the fact").

### INV-11 · Gates read the worktree they were invoked in
A gate's branch, HEAD, index, and file lookups resolve from the invoking worktree; judging one tree while the command runs in another produces false blocks and false passes in both directions.
- 2026-08-31 review · **partial** — commit-side is rescoped (`scripts/hooks/roundtable_gate.py`, "every lookup is rescoped to that worktree"); the merge-side gates still read HEAD from the main checkout (backlog item 11).
- Carriers: the gate's own doc comments; backlog item 11 is the open half.

### INV-12 · Waves are parallel by default; the budget is configuration, not doctrine
Serialization needs a named correctness or collision reason. A concurrency cap observed under one provider's limiter is evidence about an environment — it belongs in config, and only the principle (cap concurrency, treat a dead worker as a real outcome) belongs here.
- 2026-08-31 review · **partial** — the parallel-default is carried and lived; no config surface for the budget exists yet.
- Carriers: skills/karta-deliver/SKILL.md ("parallel waves"; serial "only when running two items together would produce a wrong or broken result").

### INV-13 · A gate fix ships with a negative control
A change to enforcement code lands with a test that fails on the pre-fix code for the named reason — proof the gate catches what it claims to, not just proof it still passes.
- 2026-08-31 review · **partial** — for delivery diffs it is enforced: `karta-house-skill-authoring`'s checklist rule house.4 requires the companion negative case, and the safety-auditor judges every item's diff against the pinned checklist. Ordinary branch work meets no such check.
- Carriers: .karta/sme/karta-house-skill-authoring.md (house.4, "a companion negative case").

### INV-29 · The default branch is found locally or asked for, never guessed
Delivery uses local configuration and refs, then an unambiguous local answer; anything else halts. It never contacts a remote, and subprocesses are time-bounded.
- **partial** — helper-backed by `deliver_preflight.py`; status and Stop guards use the same offline order but cannot halt discovery failures.
- Carriers: skills/karta-deliver/SKILL.md; skills/karta-build/SKILL.md.

### INV-30 · A done ref counts only when its provenance passes
A `done` ref is completion evidence only after delivery's resume rules pass it. A forged or stray ref never reads as finished.
- **partial** — preflight halts, status reports it as suspect, and the Stop guards nudge once per state. Nothing guards `git update-ref` itself.
- Carriers: skills/karta-status/SKILL.md; hooks/scripts/guard_delivery_stop.py.

### INV-34 · The secret scan reads every added line of the staged diff
The pre-commit secret scan parses pinned-format diff metadata and hunk content with explicit state, so content beginning with `++` cannot be mistaken for a header.
- **partial** — enforced by `scan_secrets.py` when karta-build runs it; binary files and secrets split across lines remain outside its line model.
- Carriers: skills/karta-build/SKILL.md.

## Review and evidence

### INV-14 · A binder commit and a delivery merge each require a fresh committed review of that exact content
The record and its round ledger ride in the same commit, bound by hash to the bytes being committed; review one version and stage another, and the gate re-arms.
- founding · **enforced** — `scripts/hooks/roundtable_gate.py` (binder gate and merge gate), recorder `scripts/roundtable/run_review.py`. The named hatch is `KARTA_SKIP_ROUNDTABLE=1`, deliberate and explained in the commit, or it is a review that did not happen.
- Carriers: AGENTS.md "Review before commit"; docs/how-to/roundtable.md.

### INV-15 · A review is several independent providers; a panel of one model is never a roundtable
The `min_providers` floor is what keeps "multi-model" honest — six hats on the same model cannot meet it, and a panel result is never filed under `.karta/roundtable/`.
- founding · **enforced** — the recorder refuses a record below the floor; the doctrine half (never file a panel) is prose backed by that refusal.
- Carriers: AGENTS.md ("A panel result is never a roundtable record").

### INV-16 · A verdict binds to the content and range it judged, and names its provenance
A gate-authorizing verdict states which bytes and which diff range it is about, bound by hash — and what the binding does not prove (that the review happened) is said, not implied.
- 2026-08-31 review · **partial** — `check_gate_report.py` requires item/range/diff-digest identity before CLI acceptance and checks passing assertion/checklist coverage. The oracle runner rejects source mutation by comparing pre/post tree hashes; the acceptance reviewer requires that unchanged-tree evidence. These checks bind claims to content, not proof that the model reviewed it or that the command meaningfully tested every assertion; runtime identity remains a separate concern.
- Carriers: AGENTS.md binder-freshness rule; skills/karta-verify/SKILL.md ("Check every returned report"); agents/karta-acceptance-reviewer.md ("tree_before_sha"); skills/karta-build/SKILL.md ("Prepare generated inputs first").

### INV-31 · Retry caps are counted in a persisted ledger, never in conversation memory
Each gate verdict is recorded against the item, gate, reviewed range, diff digest, and item-spec digest. A changed diff keeps the count; a changed item spec starts a new sequence.
- **partial** — helper-backed by `gate_attempts.py` under the Git common directory. The orchestrator must call it; no hook does, and the ledger is not tamper-proof.
- Carriers: skills/karta-verify/SKILL.md; the three reviewer agent files.

## Landing

### INV-17 · Nothing reaches the default branch by direct commit, except a commit that is entirely a binder plus its review record
Everything else arrives by branch and merge. The exception is whole-commit by INV-4: a binder staged beside a code change is not a binder commit.
- founding (route), exception wording tightened 2026-08-31 · **prose** — no gate looks at an ordinary commit landing on main; the 2026-08-24 direct commit proved it, and the adopted route gate (exclusive staging, a named MERGE_HEAD edge) has not been built.
- Carriers: AGENTS.md "How work reaches the default branch" ("The word *whole* is the exception's entire strength").

### INV-18 · Landing a delivery on the default branch is a human decision
The landing gate blocks a `git merge` naming a `karta/*/integration` ref on the default branch unless `KARTA_LANDING_APPROVED=1` prefixes it as an exact assignment word — and an agent never sets that variable: the gate cannot tell an agent from a human, so that half is doctrine, stated wherever agents read.
- founding · **partial** — the block is hook-enforced on Claude Code for a `git merge` whose ref heads its shell segment (`scripts/hooks/roundtable_gate.py`, landing gate, wired in `.claude/settings.json`; it ignores the roundtable switch). On Codex it is instruction-level: this repo's `.codex/hooks.json` does not run the landing gate. `git cherry-pick`, `git rebase`, `git reset --hard`, a merge naming the tip by SHA, and `git pull` are not matched anywhere. The who-may-approve half is prose by design, since a PreToolUse hook sees command text, not hands.
- Carriers: AGENTS.md "Two human approvals" ("Approval must prefix the merge, as an exact assignment word").

### INV-32 · A version bump carries green release evidence that covers the release inventory
A commit that changes the plugin version must include a full-gate result read from the bytes Git will commit, bound to a fingerprint of the source being committed, green (`fail = 0`, `error = 0`), and covering `benchmarks/gate/release-required.json` read from the committed content: every required vector present and PASS, partial only with a recorded `partial_reason`. The result's structure is re-validated; its summary is not trusted.
- **partial** — hook-enforced for the `git commit` shapes `scripts/hooks/precommit_gate.py` parses (its release block), on Claude Code and on Codex through `codex_precommit_gate.py`. `KARTA_SKIP_GATE=1` bypasses it, a commit outside a hooked session meets nothing, and there is no CI backstop. A green gate covers the 9 required vectors only, 4 of them partial by recorded decision; the 15 not-required vectors (10 without a probe, 5 that need consumer repositories) and live prompt-injection trajectories are not covered. `bench-spec.json`'s `per_release` list is the design target, not what blocks a release. The fingerprint proves content identity, not who ran the checks.
- Carriers: benchmarks/README.md ("Release evidence is read from the bytes Git will commit"; "The release hook does not trust the summary"); docs/how-to/hooks.md "The commit gate and its escape hatch".

## The tree

### INV-19 · Canonical is hand-edited; projections are generated, byte-equal, and never touched
Edit `skills/`, `agents/`, `skills/_shared/`; the Codex mirrors, Copilot reviewer profiles and plugin entrypoint, and the marketplace projection are regenerated, and a drifted copy fails the floor.
- founding · **enforced** — `check_shared_copies.py`, `sync_codex_skills.py --check`, `sync_codex_agents.py --check`, `validate_plugin.py`, all run by the commit hook. Hook-enforced for the `git commit` shapes `scripts/hooks/precommit_gate.py` parses: on Claude Code through `.claude/settings.json`, on Codex through `.codex/hooks.json` and `scripts/hooks/codex_precommit_gate.py`, each once the user approves or trusts the hook. `KARTA_SKIP_GATE=1` skips it per command; a commit outside a hooked session meets nothing, and there is no CI backstop.
- Carriers: AGENTS.md "Layout — canonical vs generated".

### INV-20 · The validator floor states what it covers and what it does not
The floor claim in doctrine matches the gate list the hook actually runs — including that the hook runs a gate the manual checklist does not (the pack validator) and skips one the checklist carries (`npm run check:pi`), and that commits outside a hooked session meet no floor at all.
- 2026-08-31 review · **prose** — held by this register and review; the planned register checker (above, backlog item 21) is its intended backstop.
- Carriers: AGENTS.md "Before you commit" ("All five must be clean", with the hook's own gate list stated beside it).

### INV-21 · Hooks fail open; a review denial is named, never silent
An internal error in a hook exits 0 — a broken hook never wedges the repo — while a review-gate denial always says which rule and which fix. Auditor dispatch, gate dispatch, and writer confinement are the named exceptions once they recognize their shape: they fail closed.
- founding · **enforced** — by each hook's own error handling. A launcher the host cannot start is outside this rule and may fail open.
- Carriers: AGENTS.md ("It fails open"; "A missing or stale ledger is its own named denial").

## Writers and packs

### INV-22 · Writers are confined; gates are read-only
karta-doc-gardner may write only prose docs, karta-kaizen only `.karta/sme/` and its config, and gate agents must remain read-only. The enforcement below is narrower than that obligation.
- founding · **partial** — the boundary varies by host:
  - Writers on Claude Code are **hook-enforced** for recognized file and shell operations; the guard resolves target paths and symlink parents but cannot prevent a concurrent link swap or detect every interpreter-side write.
  - Writers on Codex are **instruction-level** because its tool-call hooks do not identify the acting subagent.
  - Gate reviewers on Claude Code are **hook-enforced** by edit denial and a fail-closed read-only shell allowlist.
  - Registered gate reviewers on Codex are **host-enforced** by `sandbox_mode = "read-only"`; plugin fallback reviewers are **instruction-level** unless the whole session is read-only.
  - Gate reviewers on Pi are **host-enforced** through fresh child sessions with read-only tools.
  - Gate reviewer behavior on Copilot CLI is **instruction-level** and **untested** in this repository.
- Carriers: README.md "Enforcement below the agent"; AGENTS.md "Three runtimes, one behavior".

### INV-23 · Kaizen never weakens a rule and never promotes a pack to enforcing
It adds, clarifies, or narrows-with-an-exception; loosening what blocks a build is the human's decision, and every kaizen edit lands as a commit a human reviews.
- founding · **prose** — carried as the writer's own instructions plus commit review; nothing mechanical diffs a kaizen edit for weakening.
- Carriers: agents/karta-kaizen.md ("never weaken, loosen, or remove a rule").

### INV-24 · A user's pack edit is never overwritten; unknown provenance is the user's
A local fork — including a pack whose `base_sha256` cannot be verified — is left in place and reported, never replaced; a name clash at seed time defers to the project's copy.
- 2026-08-31 review, already carried · **prose** — the writer's instructions state it; only review checks it.
- Carriers: agents/karta-kaizen.md ("You never destroy a user's edit").

### INV-25 · A design pin is retired only by a human
Pinned design references outlive the delivery that created them; an agent may propose retirement and never perform it.
- 2026-08-31 review · **prose** — the pin machinery exists (`skills/karta-validate/scripts/check_design_pins.py`); the retirement rule has no check.
- Carriers: skills/karta-validate/SKILL.md and the design-pins scripts carry the machinery; the retirement rule's home is this entry until placed.

## Surfaces

### INV-26 · The landing ask is complete before it asks
The handoff to the human says what was assembled, what the floor found, and how to verify it in this stack's own terms — then asks; it never merges, and never buries the decision in narrative.
- founding (the ask), 2026-08-31 (its four-part shape) · **partial** — the never-merge half is enforced by the landing gate (INV-18); the shape of the ask is skill doctrine.
- Carriers: skills/karta-deliver/SKILL.md ("Review this branch and merge it yourself"); AGENTS.md "Two human approvals".

### INV-27 · A decision surface speaks plain language and preserves exact commands
Anything that asks a human to decide — a halt, a waiver prompt, a landing ask — reads in plain language, and any command it hands over is verbatim, never paraphrased into something that no longer runs.
- 2026-08-31 review · **prose** — plain language is built into the skills; the exact-commands half is stated here and checked by review.
- Carriers: README.md "Plain language, built in".

### INV-33 · A local server answers only loopback hosts and serves only its chosen root
Karta Watch pins the `Host` header and requires a session key; the design preview resolves every request and refuses targets outside its document root.
- **enforced** — by `serve_status.py` and `serve_design.py`. Concurrent filesystem changes after containment checks remain outside the preview server's reach.
- Carriers: docs/how-to/karta-watch.md; skills/karta-validate/SKILL.md.
