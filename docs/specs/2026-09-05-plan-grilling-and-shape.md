# karta-plan — the grilling interview, and deciding one binder or a set

Date: 2026-09-05. Status: decided; landed from branch `feat/plan-grilling`. The operating text is `skills/karta-plan/SKILL.md`, Phase 2 (`plan:grill` and its sub-step `plan:shape`).

## What changed

Two things in karta-plan. It now interviews the user in rounds — using the `grilling` skill verbatim — before synthesis drafts anything. And it decides whether the work is one binder or an ordered set once, early, by two named tests, instead of at emit time by feel.

## Decision 1 — the interview is the grilling skill, verbatim, kept as a reference file

The method is mattpocock/skills `skills/productivity/grilling/SKILL.md` at commit `85f83d3` (2026-08-20): a design tree of decisions, a frontier of the ones askable now, numbered rounds with a recommended answer under each question, facts found by the agent and decisions made by the user, done when the frontier is empty and the user confirms.

It is copied byte for byte — frontmatter included — to `skills/karta-plan/references/grilling.md`. A whole-file copy was chosen over an inlined excerpt because a whole file is checkable: `cmp` against upstream proves "verbatim", where an inlined block only claims it. The `agents/openai.yaml` beside it upstream is Codex packaging for a standalone skill and was not copied; karta-plan carries its own.

The copy's `name: grilling` frontmatter is inert in this tree. `validate_plugin.py` parses frontmatter only from `skills/*/SKILL.md`; `check_shared_copies.py` compares only files that also exist under `skills/_shared/`; the Pi package loads skills by their `SKILL.md`. The floor was run on this branch to confirm it.

To change the method, change it upstream and re-copy. Phase 2 of the skill says so and records the commit it was taken from.

## Decision 2 — facts are never asked; decisions are never assumed

Before, the skill promised "a minimal set of questions" and "batch all unknowns into one question". Facts and decisions sat in one bucket, and the bucket was minimised — which is how a decision gets assumed.

Now the survey (`plan:survey`) answers everything the repo can answer, and the grilling rounds put every remaining decision to the user, with a recommendation under each so a cheap decision costs a word. "Minimal" still governs facts — the repo is asked, never the user — and no longer governs decisions. A fact that lives in neither the repo nor the intent (an env command nothing pins) is asked inside a round, as a fact the survey could not settle, not as a questionnaire of its own.

The interview ends on the user's words. A round that produced no new questions is not confirmation, and neither is silence.

Where the answers live: in the binder — a `scope` line, a `contract` term, or an `oracle` assertion. The synthesis review checks that every settled decision has one of those homes. This is the answer to the gap the 2026-07-17 seed map recorded ("answers live only in conversation context"): the answers now reach the plan of record. The transcript itself is still not stored, deliberately — the binder is the plan of record, not a chat log.

## Decision 3 — the shape is decided once, by two tests

Before, the split lived in the emit phase as an aside, after synthesis had already drafted one binder: split when the user asks, or when the work "genuinely needs ordered, separately-mergeable stages". Nothing said what made a need genuine, and nothing said when the question was asked.

Now `plan:shape` is a decision every plan answers exactly once, as the tree's last question — it depends on the scope boundary, so it comes in the round after the boundary settles — with the agent's recommendation attached and one line recorded on the review card and in the report. The default is one binder. A set is recommended only when one of two tests holds, and the test is named:

1. **The user asked for separate binders.** Their words, recorded as their decision.
2. **An event outside the branch must happen between two stages.** A migration has run against real data, a soak or deprecation window has elapsed, another repo has shipped against the new contract, a flag has flipped, a consumer has cut over. The event is named. If the only thing between the stages is "the earlier code exists", that is `depends_on` inside one binder.

Five things are named as *not* reasons: size (waves and the cost phase handle scale; a smaller slice is a smaller binder, not a set), build order (`depends_on`), different stacks or areas (`touches`, `serialize`, `shared_resources`), a stage that *could* merge alone (every wave could), and reviewability (that is test 1 — ask, and let the user say it).

Why test 2 is "an event outside the branch": it is the one thing a single binder cannot express. Order is `depends_on`; collisions are `touches` and `shared_resources`; scale is waves. Only a merge that something external must follow needs a second binder, because only then must the tree be green *and observed* between two pieces of the work.

## What is enforced, and what is not

Enforced, unchanged: `validate_binder.py` validates each binder on its own; across a set it resolves every `after` slug (a dangling one is a warning) and rejects a cycle in the `after` graph (an error).

Not enforced: that the interview happened; that the shape line's reason is true; that every settled decision reached the binder. No hook can see a chat. What a reviewer reads is the residue — the card, the report, and the binder's scope and contract lines — and the skill says so in as many words rather than implying a gate.

## Affected files

- `skills/karta-plan/SKILL.md` — Phase 2 inserted; the later phases renumbered 3–7 with their ids unchanged (`plan:synthesize`, `plan:surface`, `plan:cost`, `plan:emit`, `plan:report`). Inside the hash-pinned matching-rule span, two phase-number pointers became ids, so a future renumber never touches the span again.
- `skills/karta-plan/references/grilling.md` — new, verbatim.
- `benchmarks/sme-static/match_pins.py` — `RULE_SHA256` re-pinned; the span's text changed by those two pointers, the rule did not.
- `README.md` — the karta-plan row.
- `.agents/skills/karta-plan/`, `plugins/karta/skills/karta-plan/` — regenerated mirrors.
