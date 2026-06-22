# Multi-binder planning — problem brief and directions

> **Status: open design discussion — not a settled spec.** This changes canonical karta
> behavior (how `karta-plan` and `karta-deliver` treat the binder as the unit of work), so
> it needs more conversation before becoming a plan. This doc records what was observed and
> the candidate directions, to anchor that conversation. Nothing here is decided.

## The trigger

A user asked karta (running under Codex CLI) to plan a refactor as **three separate binders,
run in order**: create the new code first, then edit the call sites, then delete the dead
code — "keep the scope lean: new first, then edit next, then delete. separate binders."

karta's own language invites exactly this ("when the scope spans multiple natural binders,
suggest a breakdown"). But karta can only *plan and deliver one binder per run*. With no way
to express "these three binders, in this order," the runtime improvised — Codex CLI tried to
drive all three binders **at once**, which karta does not support and never sequenced.

## Current behavior (as it stands today)

karta treats **one binder as the entire unit of work** for a run. The vocabulary suggests
multi-binder support; the mechanics deliver one.

- **`karta-plan` plans exactly one binder per run.** Stated twice, as a hard limit:
  - `skills/karta-plan/SKILL.md:24` — "When the scope spans multiple natural binders, it
    suggests a breakdown and plans one binder per run."
  - `skills/karta-plan/SKILL.md:244` — "**One binder per run in V1.** … Multi-binder
    partitioning is not supported in V1."
- **The whole pipeline reads one binder.** `karta-deliver`, `karta-build`, and `karta-verify`
  each take a single binder path; the integration branch is named per binder
  (`karta/<slug>/integration`). There is no driver that walks a set of binders.
- **The only ordering primitive is *inside* a binder.** `binder-schema.json` gives work items
  `depends_on` (intra-binder, validated for cycles/dangling refs by `validate_binder.py`).
  There is **no cross-binder ordering field** — nothing like `after: <slug>`. The schema is
  `additionalProperties: false`, so a binder cannot even carry "runs after X" today.
- **The gap is a vocabulary/mechanics mismatch.** karta says "split into multiple natural
  binders," then plans one and offers no way to sequence the rest. A user who wants an ordered
  set has no supported path — and an agent runtime fills that vacuum by running binders
  concurrently, the opposite of the intended order.

## Why it matters

The intent — "new → edit → delete, as separate binders, in that order" — is a *legitimate and
common* shape: independent units of work that must land in sequence because each depends on the
previous having merged. karta can express dependencies *within* a binder but not *between*
binders, so this intent is unrepresentable. The cost isn't just a missing feature; the
mismatch actively misleads a runtime into doing the wrong thing (parallel, unordered).

## Candidate directions (not decided)

### A. Binder sequence — an ordered set of independent binders  *(leaning recommendation)*

`karta-plan` emits an **ordered set** of independently-valid binders instead of one, e.g.
`<slug>-1-new`, `<slug>-2-edit`, `<slug>-3-delete`. Each is a normal binder (own integration
branch, own gates). Ordering is explicit — a new optional cross-binder field (working name
`after: <slug>`) — and `karta-deliver` **guards each binder on its predecessor being merged**.
The "not supported in V1" language is dropped.

- **Open sub-decision:** how the sequence runs.
  - *Deliver-each-in-order-with-review (preferred):* the human reviews and merges each binder
    before the next begins. Keeps karta's "no PR, you review and merge" boundary intact, one
    binder at a time. Slower, but every seam is a real review gate.
  - *Auto-sequencer:* a driver chains the binders, only pausing on a gate failure. Faster, but
    re-introduces "karta did a lot without me looking," which karta deliberately avoids.
- **Pro:** matches the user's mental model exactly; each binder stays small and reviewable;
  reuses the existing single-binder pipeline as the inner loop.
- **Con:** new schema field + validator change; `karta-deliver` needs a predecessor-merged
  guard; resume and doc-gardner semantics need a cross-binder answer (see open questions).

### B. One binder, internal phases

Keep a single binder but add a **phase/group** concept so new/edit/delete become ordered waves
*within* one binder.

- **Pro:** no multi-binder driver; the pipeline stays single-binder.
- **Con:** conflates unrelated scopes into one artifact; loses the independent review/merge
  boundary the user explicitly asked for ("separate binders"); a phase is really just a
  coarser `depends_on`, which the binder already has.

### C. Status quo, but honest

Keep one-binder-per-run, but stop implying more. `karta-plan` plans the first binder and hands
the user an explicit manual runbook for the rest ("then re-run karta-plan for the edit binder
once this merges").

- **Pro:** lowest effort; removes the misleading vocabulary immediately.
- **Con:** doesn't deliver the capability; the user still hand-orchestrates what karta named.

## Open questions for the conversation

These are what a future spec must settle — each meaningfully changes the design:

1. **Where does ordering live?** A new top-level binder field (`after: <slug>`), a separate
   sequence manifest (`.karta/sequences/<name>.json`), or naming convention only? A field means
   a `binder-schema.json` change *and* a `validate_binder.py` change (and a cross-binder
   dangling/cycle check the current validator can't do — it only sees one binder).
2. **Who drives the sequence?** Does `karta-deliver` gain a multi-binder mode, or does the
   human run `karta-deliver` per binder in order with karta only *guarding* (refusing to start
   binder N until N-1's integration branch is merged)?
3. **How does resume work across a sequence?** Today resume is git-native off one integration
   branch. A sequence needs to know which binder is "current" — derivable from which
   integration branches are merged, or recorded somewhere?
4. **Doc-gardner timing.** It runs at the end of each `karta-deliver`. Once per binder
   (three doc passes), or once at the end of the whole sequence?
5. **Slug and naming.** Is `<slug>-N-<phase>` a convention, a rule, or author-chosen? How is
   the integration branch named so three binders don't collide?
6. **Validation scope.** Should validation gain a "validate this whole sequence" mode (every
   binder valid *and* the `after` graph acyclic and complete), or stay per-binder?

## Affected surfaces (blast radius for a future plan)

- `skills/karta-plan/SKILL.md` — scope-limit language (`:21-25`, `:244`); the planning flow
  would emit a set, not one binder.
- `skills/karta-plan/references/binder-schema.json` + `scripts/validate_binder.py` — any
  cross-binder field, plus a possible sequence-level validation mode.
- `skills/karta-plan/references/binder-reference.md` — document the ordering field/convention.
- `skills/karta-deliver/SKILL.md` — predecessor-merged guard and/or sequence driver.
- `skills/karta-build/SKILL.md` — resume semantics if they cross binders.
- `README.md` + `docs/how-to/*` — the "one binder" framing in user-facing docs.

## Recommendation

Lean toward **A (binder sequence) with deliver-each-in-order-with-review**: it matches the
observed intent, keeps binders small and independently reviewable, and reuses the single-binder
pipeline as its inner loop — at the cost of a schema/validator change and a predecessor-merged
guard in deliver. Confirm direction, settle the open questions above, then promote this brief to
a full design spec and implementation plan.
