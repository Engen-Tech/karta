# karta backlog

Work that is known, scoped, and deferred — not yet on a branch. Each item says what it is, why it is not done, and what unblocks it, so anyone can pick it up cold.

**Current release:** `main` is at **v1.10.0**. The per-host model + effort tiering shipped in v1.9.0; the karta-validate Phase-3 comparison rework shipped in v1.10.0. Tag `validate-stable` pins v1.9.0 as the rollback point for the validate rework (`git reset --hard validate-stable`).

Status legend: **Ready** (scoped, unblocked) · **Blocked** (needs a prerequisite) · **Idea** (not yet scoped).

---

## 1. skill → shell + agent conversion — *Blocked* (scope decided 2026-07-01)

**What.** Turn each *reasoning* skill into a thin shell that dispatches a model-pinned agent, instead of holding the reasoning inline. karta already does this for the two gate agents and the doc-gardner agent (3 of 9 skills are already shells).

**Why it matters.** Agents honor a model/effort pin under both hosts (Claude Code reads `model`/`effort`; Codex reads the projected `.codex/agents/*.toml`). Skills only honor the Claude-side `model`/`effort` — **Codex skills have no model field.** So today a skill's intended tier is silently ignored under Codex. Converting the reasoning skills to shell-plus-agent is the only way per-skill tiers actually take effect on Codex.

**Scope decision: reasoning-5, phased as clean-3 first.** A roundtable deliberate (Antigravity, Codex, DeepSeek, GLM-5.2, Kimi, Qwen) + exa research on the Codex workaround converged on this. Full findings, panel verdict, and raw artifacts: [`skill-to-agent-scope/FINDINGS.md`](skill-to-agent-scope/FINDINGS.md).

- **Phase 1 — clean-3:** convert `karta-plan`, `karta-validate`, `karta-build`. Proves the pattern on the heaviest reasoning + the most agentic skill.
- **Phase 2 — reasoning-5 remainder:** convert `karta-deliver`, `karta-plainlanguage`. Lands the rule "every reasoning skill honors its Codex tier."
- **Reject all-9.** `karta-debt` and `karta-status` stay inline — read-only one-shots that gain nothing from a pin; a Haiku pin could *degrade* them on a stronger session model (GLM's note).

**What's blocking it.**
- **No headless build runner** to drive the A/B/C spike (same build through builder-A/B/C on a fixture binder). This blocks *execution/merge validation*, not the scope decision — scope is architectural (Codex's platform constraint), the runner is empirical tier-tuning. Build it during Phase 1.
- **Codex can't register bundled subagents, and the install-hook workaround is currently blocked upstream.** A Codex plugin install cannot ship subagents (openai/codex#28491, open). The "SessionStart install-hook" path listed previously is **not viable today**: plugin-local `hooks.json` is not loaded by the runtime (openai/codex#16430, open, updated 2026-05-16). The only working Codex path is the existing **bundled-instructions-in-skill fallback** that `karta-verify`/`karta-doc-gardner` already use (adaptive dispatch: registered TOML when present, else spawn a worker with the bundled `references/<name>.agent.md`). Re-check #16430 / #28491 before Phase 2 — if either ships, the per-skill cost drops and the install-hook option reopens.

**Unblock path.** (1) Build a minimal headless build runner. (2) Convert `karta-plan`, `karta-validate`, `karta-build` using the existing bundled-instructions fallback (no install-hook available yet). (3) Run the A/B/C spike on a fixture binder; manually validate dual-platform. (4) Re-check upstream issues; convert `karta-deliver` + `karta-plainlanguage` if the spike holds. Keep each shell byte-identical across the three projection targets (`skills/` → `.agents/skills/` → `plugins/karta/skills/`, via `sync_codex_skills.py`) and the agent projections via `sync_codex_agents.py`.

**Open judgment call.** If `plainlanguage` proves to *downgrade* Codex sessions already running sonnet+ (GLM's risk), Phase 2 can drop it to a clean-4 (plan, validate, build, deliver) without breaking the rule — the boundary is still "reasoning skills get their tier." Revisit after the Phase-1 spike.

---

## 2. karta-validate hardening — *Ready (small)*

**What.** Two tightenings inside the existing karta-validate skill. Both pre-date the v1.10.0 rework and were flagged by the review panels but left out of that PR to keep its scope clean.

- **Strict pixel thresholds.** Give the comparison concrete numeric bars (e.g. an ignore-below-N-px rule) so borderline spacing/position deltas are classified consistently instead of by feel.
- **Mock-data-vs-real-copy definition.** Write a crisp rule for telling seeded/mock content (dates, names, counts) apart from real UI copy (labels, body text, links), so the matcher ignores the former and still catches genuine copy defects. The v1.10.0 prompt already assumes this distinction — this makes it explicit and testable.

**Why deferred.** Out of scope for the v1.10.0 comparison-prompt PR; small enough to land on their own.

**Notes for whoever picks this up.** Edit only the canonical `skills/karta-validate/SKILL.md`; the `.agents/` and `plugins/karta/` mirrors regenerate via `scripts/sync_codex_skills.py`. Keep `validate_plugin.py --self-test` and both sync `--check` green. If the change is behavioral, re-run an **unbiased** multi-model panel (neutral prompt, verdict free to land anywhere) before merging — that is how the v1.10.0 over-suppression regression was caught.

---

## 3. splitting `serve_status.py` — *Shelved* (decided 2026-08-16)

**What.** Split `skills/karta-status/scripts/serve_status.py` (5,504 lines when this was investigated, 46% of it its own self-test suite; the watch-redesign delivery has since taken it past 11,800) into flat sibling modules.

**Why shelved.** Not blocked — a judgement that the plan cost more to make safe than the monolith costs to live with. Three review rounds, two independent providers each; every round found real defects, and every round found defects *introduced by the previous round's fixes*. The binder reached 77 KB and 101 oracle assertions for a refactor shipping no user-visible change. The cause is a property of the file: its self-test scans its own source, with every forbidden literal assembled at runtime so the checks cannot match themselves — so adding a check produces a second-order effect nearly every time. Two of the plan's own safety mechanisms were then disproved empirically: `git diff -M --find-copies-harder` scores a 2,522-line extraction at 8–11% similarity and detects nothing, and the check-name manifest could not be bootstrapped in the commit that creates it.

**What survives.** Full investigation, the verified facts about the file, and the narrowest restart shape: [`watch-module-split/FINDINGS.md`](watch-module-split/FINDINGS.md). Several findings are worth reading even if the split never happens — the hub identity digest goes blind on any code moved to a sibling, `max(mtime)` is not a change detector, and the `globals()` patch (now at `:5500`, and shifting as the file grows) passes vacuously from any other module.

**Cost accepted.** `watch-redesign.json` stays valid as written; deliveries keep serializing on the one filename.

---

## 4. the commit gate cannot accept a single-model review — *Ready* (filed 2026-08-17)

**What.** Teach `scripts/hooks/roundtable_gate.py` and `run_review.py` a second, clearly-labelled
record kind, so a multi-lens panel review can satisfy the plan-commit gate without ever claiming to
be a multi-model roundtable.

**Why it matters.** `.karta/roundtable.json` has one master switch, and the only record the gate
accepts is one that cleared the `min_providers` floor. With the roundtable environment unavailable
the only option is `enabled: false`, which turns off both enforced gates — so karta's own binders
are now reviewed by discipline instead of by a check, which is the skippable prose the doctrine
exists to avoid.

**The surprise that makes it cheaper.** The gate never inspects a record; it delegates to
`check_fresh()`, which compares `reviewed_hash` and nothing else. A record containing only a correct
hash already passes (verified). So the gate side accepts a non-roundtable record today — the work is
making that deliberate and labelled. The same finding is a latent hole on its own: a hand-written
hash file passes with no review behind it.

Full report, evidence, constraints and the open design decisions:
[`roundtable-panel-record/BUG.md`](roundtable-panel-record/BUG.md).

---

## 5. the right panel is a binder detail view, not a phase list — *Done* (shipped in watch-drill-in, 2026-08-22)

The main panel now shows exactly one binder — the one picked in the left rail — with no Now / Next
/ Later grouping. `panel-is-one-binder` removed `_PHASE_DEFS`'s grouping markup from the panel
(`phase__head`, `phase__binders`, `data-kw-phase` and the rest); the rail is the selector, the panel
is the detail. The other work this entry carried is resolved the same way: the binder summary,
type-scale gaps, and inverted card ground are closed, the live-page controls are booked as intended,
and the rail carries a halt badge. One item from the original list is still open — the binder head's
own progress track is still square where the design's is a pill — and five more turned up in the
same comparison run. All of it, closed and open, is tracked in
[`docs/conventions/watch-design-fidelity.md`](../conventions/watch-design-fidelity.md#findings-from-the-latest-run),
not duplicated here.

---

## 6. the design-asset rule certifies more than it enforces — *Ready* (filed 2026-08-18)

**What.** `scripts/validate_plugin.py`'s design-asset rule promises that every asset a committed
design reference points at "resolves inside this repo". It checks neither half completely.

**Two holes.** `_ASSET_REF_RE`'s attribute branch admits double quotes only, so `<img src='x.png'>`
and `<img src=x.png>` are never seen at all. And the resolution check never tests containment, so
`/etc/hostname` and a `../../../../../../etc/hostname` both pass — despite the rule's own error text
and the runbook both promising otherwise.

**Nothing is wrong today.** The one committed capture's nine refs are all double-quoted, relative,
and inside the repo. This is a rule that would not catch the next one.

**Unblock path.** Match all three attribute quotings, assert containment against the repo ROOT — not
the design directory, since the mascot and all seven font faces legitimately live under `skills/` —
and add three negative controls to `_self_test()`'s `design_cases`, one per hole.

---

## 7. the watch ensure probe scores a slow answer as a foreign occupant — *Ready* (filed 2026-08-18)

**What.** The watch end-to-end check fails roughly one run in six, and has since before the
watch-fidelity delivery.

**Why.** `hooks/scripts/inject_karta_status.py` waits on a detached ensure child whose `_probe_hub`
has a hard 500ms per-attempt cap. A loopback `/identity` that answers slower than that is scored
"foreign", the child steps past it, and the breadcrumb never clears.

**Measured, not inferred.** Sixteen interleaved paired runs: two failures in a worktree, two in a
pristine clone — identical rates, so it is not worktree-specific. `KARTA_WATCH_STATE_DIR` is a
proven no-op as a remedy: both self-tests overwrite it with their own temp dir before the first
check.

**Unblock path.** Retry the identity probe rather than treating one slow answer as a verdict.

---

## 8. the merge gates fire on `git merge-base` — *FIXED 2026-08-24*

**What.** `scripts/hooks/roundtable_gate.py:80` matches the git verb with `\bmerge\b`. A word
boundary sits between `merge` and the `-` in `merge-base`, so `git merge-base`, `git merge-tree`
and `git merge-file` — all read-only, none able to move a ref — are blocked by both the
integration-merge gate and the landing gate.

**How it surfaced.** Two consecutive read-only ancestry checks during a binder review were refused,
one by each gate, both with a message about who decides a delivery ships. Neither command lands
anything.

**Fix.** `merge(?![-\w])` in place of the trailing `\b`. Measured against the matcher: keeps all
three true positives (`--no-ff`, `--squash`, bare `git merge`), drops all three false positives.

**Why it is worth doing.** The gate's own doctrine argues the *ref* match is anchored rather than
searched because a gate that refuses commands merely quoting a merge blocks its own maintenance.
That reasoning was never applied to the *verb*. A gate that blocks harmless commands trains people
to reach for `KARTA_SKIP_ROUNDTABLE=1` by reflex, which costs the audit trail more than the blocked
command costs anyone's time. A second, smaller finding — the matcher cannot be exercised from a
shell command containing its own trigger — is documented but needs doctrine, not code.

Full writeup, reproduction and the truth table: [`landing-gate-verb-match/BUG.md`](landing-gate-verb-match/BUG.md).

---

## 9. measuring optical sizing on the watch page — *Ready, unblocked* (measured 2026-08-23; the blocker landed 2026-08-24 at main 1183eea)

**What.** Build `scripts/check_optical_sizing.py`: drive the served page in a browser and prove
Newsreader renders at the optical size each of its six sizes asks for. The `watch-font-adherence`
binder ships the axis and proves at the floor that a variation table of the recorded size is
present; it proves nothing about what the browser draws, and says so in scope.

**Why it is a backlog entry and not binder items.** It was drafted as binder items and went
thirteen review rounds without converging. Rounds 3–9 argued one claim down four times — each time
the fix was to shrink the claim, and each time I added a bigger check instead. Rounds 10–13 were
implementation decisions in a measurement harness, where rounds 11 and 12 each broke on the
previous round's fix. A prose specification of a harness has no natural end: every detail pinned
reveals another that interacts with it. The two items that did land describe artifacts whose
properties are knowable before building; a harness's are not.

**What survives, and it is a lot.** Every measurement — the weight and optical axis tables, the
detection edge bracketed to 3/64 px steps, the `fvar` 84/56/absent arithmetic, the unstable subset
byte count — plus the rendered-page facts that only appear on the real fixture: four of six serif
sizes exist at rest, one element per tuple (not twenty-seven), two elements carry no hook, the
wave numeral is a single character. And eleven design decisions, each one a place a plausible
implementation is wrong, with the evidence for each.

**The sharpest two.** No absolute pixel floor works: the page carries a one-character numeral and
a sixty-three-character sentence, and the 0.3 px floor three drafts carried fails a *correct*
build. And width cannot witness outline variation at all — `HVAR` carries the advances, so a font
with `gvar` stripped separates weight 400 from 500 by the same 4.828 px as a correct one while its
letterforms sit frozen (21 sub-pixels of ink change against 5,234). Measure ink, not width, for
any claim about the shapes.

[`watch-optical-harness/FINDINGS.md`](watch-optical-harness/FINDINGS.md).

---

## 10. the commit gate fires on `git commit-tree` and `git commit-graph` — *Ready* (filed 2026-08-24)

**What.** The exact defect entry 8 fixed, one line above it. `scripts/hooks/roundtable_gate.py`'s
`_COMMIT_RE` ends in `commit\b`, and `-` is a non-word character, so any git subcommand that merely
*starts with* `commit` reads as a commit. Verified: `is_commit_command("git commit-tree HEAD^{tree}")`
and `is_commit_command("git commit-graph write")` both return `True`. Neither writes a commit the
binder gate is meant to govern — `commit-tree` is plumbing that takes an existing tree, and
`commit-graph write` only builds a cache file.

**Fix.** The same one, one line up: `commit\b` becomes `commit(?![-\w])`, plus a self-test case per
false positive alongside the existing `check("detect commit", ...)`.

**Why it is filed rather than folded into entry 8.** Entry 8 was scoped to the merge verb and was
reviewed and fixed as that. Widening a verified one-line fix at commit time is how an unreviewed
change rides in on a reviewed one. Found by the independent review of entry 8's fix.

---

## 11. the gates read HEAD from the main checkout, not from where the merge runs — *Ready* (filed 2026-08-24)

**What.** `scripts/hooks/roundtable_gate.py` roots every git call at a fixed path — `ROOT` is derived
from the hook script's own location (`roundtable_gate.py:65`) and `git()` runs with `cwd=ROOT`
(`roundtable_gate.py:183`). So `current_branch()` (`roundtable_gate.py:257`) always reports the
*primary checkout's* HEAD, whatever directory the command being judged actually runs in. Both places
that ask "am I on the default branch?" — the landing gate (`roundtable_gate.py:307-310`) and the
deliver-merge review gate (`roundtable_gate.py:340`) — are reading a branch that may have nothing to
do with the merge in front of them.

Demonstrated on this repo while the primary checkout sat on `main` and a delivery worktree sat on
`karta/watch-drill-in/integration`: driving `current_branch()` with a git rooted at the worktree
returns the integration branch, with one rooted at `ROOT` returns `main`, and the gate takes the
latter.

**Two consequences, and the second is the serious one.**

- *False positive, observed 2026-08-24.* Consolidating `karta/watch-drill-in-remediation/integration`
  into `karta/watch-drill-in/integration`, run inside the drill-in worktree, was refused as "landing
  ... on main is the human's decision". Nothing was landing on main; HEAD in that worktree was an
  integration branch. The gate blocks integration-to-integration merges run from a worktree, which is
  the normal shape of a stacked delivery.
- *False negative, by the same mechanism inverted.* Git allows the default branch to be checked out
  in a linked worktree while the primary checkout sits on a feature branch. In that arrangement a
  real landing merge — an integration branch onto `main`, in the worktree that has `main` — sees
  `current_branch()` report the feature branch, so the condition is false and the gate stays silent.
  A gate that is meant to make one decision unmissable can be silently absent.

**Fix, and why it is partial.** Reading `cwd` off the PreToolUse payload instead of `ROOT` covers a
session whose working directory *is* the worktree, which is the common case. It does not cover the
case that produced the observed block: the command was `cd <worktree> && git merge ...`, so the
payload's cwd was the project directory and only the command text named the real location. Resolving
a leading `cd <path> &&` in the same shell segment narrows that too. Neither closes the gap, and the
entry should not pretend otherwise — a PreToolUse hook sees command text, and where a shell command
finally runs is not decidable from text. State the residual plainly in `AGENTS.md` alongside the
bypasses already named there, the same way `git cherry-pick` is named.

**Also worth doing.** `decide()` is already pure over a stubbed `git`, so the regression is cheap to
pin: drive it with a git stub reporting a worktree HEAD that differs from the primary checkout's and
assert both directions — no block for integration-to-integration, block for a real landing.

**Found by** the `watch-drill-in-remediation` delivery, which hit the false positive on three item
merges and then again on the consolidation.

---

## 12. the coverage harness's own guards have four soft spots — *Ready* (filed 2026-08-24)

**Where.** `skills/karta-status/scripts/serve_status.py`, the three aggregate checks that stand in
for per-behaviour tests across the whole `_COVERAGE_REGISTRY` (~258 checks), plus the two self-test
counters. Found by the two-provider branch review of `watch-drill-in-remediation`; both providers
raised the first one independently. None of these is a defect the branch introduced — the harness
predates it — and none is a one-line fix, which is why they are filed rather than folded into a
review commit.

**a. The vacuity guard proves nothing for a callable control.** The check is
`any(k not in ctx or ctx[k] == v for k, v in overrides.items())`. For the string and tuple artifacts
that is real — a `.replace()` whose target has gone compares equal and is caught. For a **callable**
override `==` is identity, and a freshly built lambda never equals what it replaces, so a callable
control can never be flagged vacuous. The aggregate still reports *"each negative control proves its
mutation changed the rendered bytes"*, which is untrue for that whole class. `never_failed` catches a
control that changes no behaviour, so there is no exploit today, but the stated invariant is not the
one being enforced. Compounding it: the runner treats an exception as `survived = False`, so a
callable break that merely makes the check *crash* satisfies `never_failed` without ever showing it
exercised the guarded behaviour. **Fix:** have callable overrides carry an explicit before/after
probe — assert the replacement returns something different from the original on a fixed input —
rather than leaning on `==`.

**b. A break receives the shared, un-copied context.** `overrides = mutate(ctx)` runs against the
live `ctx`; the defensive `broken = dict(ctx)` happens afterwards, and one `ctx` is reused for every
entry in the loop. A control that mutates a nested value in place rather than returning a
replacement would poison the true-render comparison for every check evaluated after it, making
results order-dependent. No current control does this. **Fix:** snapshot and restore around each
break, or hand `mutate` a copy.

**c. A duplicated registry name silently drops a check.** `_covers` writes
`_COVERAGE_REGISTRY[name] = {...}`, so registering two behaviours under one name discards the first
with no diagnostic, and none of the three aggregates asserts a count or uniqueness. The committed
behaviour anchor catches it only when the dropped name is already in the anchor — a duplicate pair
added in a single commit is invisible. **Fix:** reject a duplicate at registration.

**d. Both self-test totals are disciplined, not derived.** `scripts/check_fact_traces.py` and
`scripts/validate_plugin.py` now increment a running `total` beside each printed result line, which
is a real improvement on the hand-summed expression it replaced — but a future block that prints
`[PASS]`/`[FAIL]` and forgets its `total += 1` still under-reports silently. That is the same failure
class `selftest-count-is-real` set out to end, moved from one central sum to N local increments
rather than removed. **Fix:** collect `(name, ok)` results and derive both numbers from the list, so
the count cannot disagree with what was printed.

**Worth recording about the review itself.** A third claim — that a rendered check whose hook is
absent from every string artifact is *silently* skipped — was checked and rejected. The skip is not
silent: the aggregate compares `renamed_fails == rendered` as lists, so a skipped check shortens one
side and fails the aggregate.

---

## 13. `check_fact_traces.py` has four narrow gaps the third review pass found — *Ready* (filed 2026-08-24)

**Where they came from.** The two-provider review of `karta/watch-drill-in/integration` at tip
`66cf410` ([record](../../.karta/roundtable/branch-66cf41012c755895a6ca297f123795404b4647d0.json)).
Two of the four were raised by both providers independently. All four are confirmed by running them,
all are low severity, and none blocks the landing — they are filed rather than fixed because fixing
would have moved the tip and voided the record that pass produced.

**a. An explicit `"token_manifest": null` bypasses validation.** `check_binder` reads
`binder.get("token_manifest")` and returns clean on `None`, so a present-but-null manifest is
indistinguishable from an absent key — which contradicts the rule the same function states one line
later, that a manifest *when present* must be an object. Confirmed: `errors=[]`. Note this is a gap
in code the review itself introduced, one pass earlier. **Fix:** test key presence, then the value.

**b. The self-test is asymmetric about unexpected errors.** The predicate is
`bool(errors) == bool(want_err)`, so once a case expects an error it cannot tell one error from
five: a regression emitting the wanted error *plus* a spurious one still passes. The note side was
tightened to `bool(notes) == bool(want_note)` in the same pass; the error side was not. **Fix:**
compare counts, or require every error to match a wanted substring.

**c. The `row {i}` fallback id can alias a real one.** A row with no `id` is labelled `row 3` by
position, so a fact legitimately named `"row 3"` alongside an id-less row at index 3 reports a
spurious "recorded twice". Confirmed. The direction is a false alarm rather than a pass-while-broken,
but it is the duplicate detector reporting on itself. The mirror case matters more: two id-less rows
get *distinct* fallback ids, so a genuine duplicate among them is never flagged. **Fix:** namespace
the fallback so it cannot collide with a real id.

**d. A fact row needs no `id` at all.** The fallback means `{"traced_by": ["item:0"]}` validates,
even though ids are documented as unique stable identities. **Fix:** require a non-blank string
`id`, and use the positional label only when reporting that failure.

**Speculative, kept for whoever nests a binder.** The sweep is `*.json` plus one level of `archive/`.
A live binder in any other subdirectory, or an archived one deeper than a level, is never checked and
passes by omission. Binders are flat today, so this is not reachable.

**One recommendation declined, with the reason.** A provider asked that the archived DICT exemption
validate the full pre-convention schema — the metadata keys, not just that `facts` is a list. Turning
that down: the only pre-convention table in this repo is `watch-fidelity.json`, and pinning the
exemption to its particular metadata keys would fit the check to one file rather than to a shape.
Requiring `facts` to be a list is the line that distinguishes "pre-convention" from "malformed",
which is what the exemption is for.

**One finding checked and cleared.** The same pass flagged that the new comment on the coverage
harness claims callable controls are "covered instead by `never_failed`", and that the claim was
unverifiable from what it had been shown. It was verified against the source: a callable control that
changes no behaviour leaves the check passing, `survived` is true, and the name is appended to
`never_failed`. The comment is accurate.

---

## 14. a binder committed to main after its delivery branch was cut survives the landing twice — *Ready* (filed 2026-08-24)

**What happened.** `watch-drill-in-remediation` landed on main with its binder in **two** places at
once: `.karta/binders/watch-drill-in-remediation.json` (live) and
`.karta/binders/archive/watch-drill-in-remediation.json`, byte-identical. The delivery had archived
it correctly — `126bda6` is a real `git mv` — and the archival did not survive the merge.

**Why, exactly.** The binder was added *independently on both sides*. It went onto main as `3f93a0d`
(the standing rule lets a fully committed binder reach main directly) and onto the delivery branch as
`dda1878`, karta-deliver's own plan commit. The delivery branch had been cut from an earlier main, so
the file is **absent in the merge base**. Git then sees: base absent, ours (main) present, theirs
(branch) absent — which is an add on one side and nothing on the other, not a delete. Ours wins, and
the live copy is resurrected on top of the archived one.

`watch-drill-in.json` in the same merge renamed into `archive/` cleanly, which is the control: that
binder *was* in the merge base, so git had a file to trace and the move applied.

**Why this is not a one-off.** It follows from two rules that are each correct on their own — a
binder may be committed straight to main, and karta-deliver commits the binder as its plan commit on
the integration branch. Any delivery whose binder reaches main *after* its branch was cut hits this.
The delivery reports 5/5 and archived, the floor stays green, and the repo quietly holds a live
binder that karta-status will keep offering as work to do.

**Fix, in order of preference.**
1. Cut the integration branch from a main that already carries the binder — the plan commit then has
   a base to be a delete against. This is a sequencing rule for karta-deliver, not code.
2. Failing that, have the archive step at `deliver:archive` assert afterwards that no live binder of
   that slug remains, so the delivery fails loudly instead of the merge silently undoing it.
3. Add a validator check: a slug present in both `.karta/binders/` and `.karta/binders/archive/` is
   an error. That is the cheap net and it catches every route in, including this one.

**Found by** checking the tree after the `watch-drill-in` landing rather than trusting the merge
diffstat, which reported `create mode .karta/binders/archive/watch-drill-in-remediation.json` with no
matching delete — the tell, if anyone had read it as one.

---

## 15. `_check_font_provenance` drives its variable-face checks off `families`, not `faces` — *Ready* (filed 2026-08-24)

The variable-face rules — a non-empty `axes` map, no `pinned_axes` — are applied while iterating
`sorted(families.items())` in `scripts/validate_plugin.py`. A face whose `family` names a key that is
not in `families` is therefore never evaluated against them: a misspelt family on a variable face
skips its axis-provenance checks entirely.

**Why it is Low rather than a hole.** Nothing in the shipped manifest can reach that state today, and
a face naming an undeclared family would fail other agreement checks first. It is a defence-in-depth
gap, not a live bypass.

**Fix.** Assert the foreign key: every `face["family"]` must exist in `families`. That is a cheaper
and more general net than inverting the loop, and it catches the typo case directly.

**Found by** the antigravity panelist on the fifth and final roundtable pass over
`watch-font-adherence`, which was otherwise a unanimous merge.

---

## 16. a docstring still hardcodes a source-line pointer — *Ready* (filed 2026-08-24)

`skills/karta-status/scripts/serve_status.py` carries a `serve_status.py:110-124` pointer in the
variation-agreement docstring. This is the exact pattern removed from the WOFF2 reader's docstring in
the same delivery, for the reason that hardcoded line numbers rot silently and nothing checks them.

**Fix.** Describe the referent instead of indexing it, the way the reader's docstring now does. If a
pointer is genuinely wanted, point at a stable name rather than a line range.

**Found by** the claude opus-4-8 panelist on the final pass, which noted the inconsistency with the
change made two commits earlier.

---

## 17. `karta-status`'s own SKILL.md says the page uses system fonts — *Ready* (filed 2026-08-24)

`skills/karta-status/SKILL.md:56` describes the page as "self-contained (vendored Vue, system fonts,
no CDN, no build step)". It vendors its fonts, and has since the redesign vendored them — so this
predates `watch-font-adherence` and was not caused by it.

**Why it was not fixed in that delivery.** SKILL.md is outside karta-doc-gardner's writable surface
and the writer-confinement hook blocked the edit, correctly. Correcting it is a code-track change,
and widening a delivery's scope to sweep up unrelated drift is the thing that makes a delivery's diff
stop describing its own binder.

**Fix.** One word, plus the two generated mirrors via `uv run scripts/sync_codex_skills.py`.

---

## 18. kaizen only ever adds, so its pack now exceeds the size the validator warns at — *Ready* (filed 2026-08-24)

`validate_packs.py` warns that `.karta/sme/karta-house-skill-authoring.md` is 7299 bytes against a
3500-byte advisory ceiling, with the reason that packs are prompt text. The warning predates this
delivery and this delivery widened it.

**Why it will keep widening.** karta-kaizen's contract forbids weakening or removing a rule, so the
pack grows monotonically by construction. Every delivery that teaches it something makes the warning
worse, and no amount of care in a single run reverses it.

**What is actually being traded.** Every byte is prompt budget spent on every build that loads the
pack. A rule that earns its place at 3500 bytes may not at 10000, and the pack has no mechanism for
retiring a lesson that has stopped paying.

**Fix, and it is a human's call by design.** Either trim the pack — deciding which recorded lesson
stops being worth its budget, which kaizen may not do for itself — or give packs an explicit
retirement path so the ledger can shrink under review rather than only grow. The second is the real
fix; the first is what unblocks the warning today.


## Done (recent)

- **v1.9.0** — per-host model + effort tiering on all 3 agents + 9 skills (PR #1, merged).
- **v1.10.0** — karta-validate Phase-3 comparison-prompt rework: data-first structured diff → mandatory screenshot pass → evidence-grounded findings, role/position matching, capture-artifact filter, severity rubric (PR #2, merged).
