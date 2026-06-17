# karta defer/accept hatch — design

**Date:** 2026-06-17. **Status:** design settled (roundtable + user-confirmed); revised after an adversarial spec review (4 lenses + synthesis). Pre-implementation. **Scope:** a human escape hatch for planning gaps that surface during delivery, without a karta-plan round-trip. Additive to the declared-debt hardening (commit `59e3355`) — the worker-self-clear ban is preserved and made structural.

## Problem

Gaps surface during a build that the worker can't resolve: a capped acceptance DEVIATION (2 attempts, still failing), or a SPEC-SUSPECT (code right, binder stale). Today the only blessed way to accept an unmet assertion is to re-plan it as an oracle `opt_out` via karta-plan — a plan-time round-trip. karta's principle is to do the right thing by default but always leave an escape hatch. The user should be able to make the call directly, at the halt, without re-planning — as long as it is the **human's** decision (never the worker's) and it is **recorded**.

## The human choices at a halt

When delivery halts an item on an acceptance gap, Phase 4 (lifecycle / partial-wave failure) already asks the human to revert or continue. Extend that to four options, **offered by the orchestrator through the host's user-input facility** (see "The human channel" below):

- **fix-and-rerun** — kick back to the implementer (existing; only while attempts remain).
- **accept** — merge the item as-is; the human waives the named unmet assertion(s), with a reason. (new)
- **defer** — leave the item unfinished, record the gap, continue-and-merge the independent rest. (the continue-with-partial path, enhanced) (new effect)
- **revert the wave** — existing.

## The human channel (enforced, not asserted)

This is the load-bearing guarantee, and it is structural, not prose. The earlier draft asserted "human-only" without a mechanism; a worker's free-form halt report could then induce the orchestrator to act. Closed as follows:

- The orchestrator obtains an accept/defer decision **only** through the host's user-input facility (the `AskUserQuestion`/host-prompt idiom karta already uses, e.g. `karta-build` build:datalayer round-3) **inside the orchestrator session**.
- The orchestrator **must treat any accept/defer signal that appears in worker output as non-authoritative** — never act on it. This extends the existing "the orchestrator does not trust the worker's verdict" guarantee (integration-branch.md) to accept/defer claims. A worker halt report that says "the human accepted this" is ignored; the orchestrator asks the human itself.
- The reason written into the waiver is the **human's reason captured at the prompt** — never copied from worker text, a commit message, a marker, or the failed branch.

**Threat model (state it in the docs, as 59e3355 did):** an adversarial or confused worker that emits a forged "human accepted — proceed" narrative in its halt report must NOT result in an `accepted` ref. The only path to a waiver is a live human answer to an orchestrator-issued prompt.

## Accept — a git-native waiver

The accept decision is recorded in git, in two places, and nowhere else:

1. **`refs/karta/<slug>/item-<id>/accepted`** → the accepted item-branch commit. Written by the orchestrator only, and written **last** (see write-order below).
2. **Merge-commit trailers** on the (forced no-ff) integration merge: `Karta-Accepted: <assertion id(s) | "contract" | "spec-suspect">` and `Karta-Accept-Reason: <the human's reason>`. The merge-commit trailer is the **audit source of record**; the `accepted` ref is a fast index into it.

It does not touch the binder (read-only during a build) and uses no separate state file (karta has none). If a backlog sink is configured, the waiver is also appended there (after the merge succeeds).

### The merge source — the item branch, not a `failed` ref

Accept merges the item branch `karta/<slug>/item-<id>` tip — the durable committed artifact, which always exists once the worker built and committed, **regardless of which outcome ref the item carries.** This resolves the SPEC-SUSPECT case (which writes no `failed` ref today).

To keep the halt state uniform, this design also defines: **in wave mode, a worker that halts at the acceptance gate — a capped DEVIATION *or* a SPEC-SUSPECT — commits its item branch and writes `refs/karta/<slug>/item-<id>/failed` at that tip, then stops** (no `built`, no `done`). The `failed` ref means "halted at the gate, not cleanly done" — for a SPEC-SUSPECT its note carries the spec-suspect reason; it does not claim the code is bad. So every acceptance halt leaves the same anchor: a committed item branch + a `failed` ref.

### Accept is a new, explicit merge precondition

The hardened model says "the orchestrator merges only items that carry a `built` marker." Accept adds a second precondition, stated explicitly: **the orchestrator merges an item if it carries `built`, OR if a live human accept-waiver authorizes merging its halted item-branch tip.** An accepted item is **not** given a `built` ref (the worker never cleared the gate; `built` would be a lie). Accepted-done is its own flavor: item branch + `accepted` + `done`, no `built`.

### Accept flow (orchestrator; runs at the Phase-4 halt decision)

For each item the human accepts (processed serially):

1. **Re-validate against the current integration tip.** Re-run the gate on the item branch vs the moved tip. The waiver suppresses **only** the specific unmet assertion(s)/divergence named in the waiver; **any other finding** — a merge conflict, a stale-tip semantic break, a newly-failing assertion — still halts. The waiver covers the named gap, never a broken merge.
2. **Merge the item-branch tip onto the integration tip, forced no-ff** (so there is always a merge commit to carry the trailers, and `accepted`=item tip stays distinct from `done`=merge commit). A conflict halts per step 1.
3. **Fresh post-accept floor check** on the new tip. Run the project's build/type-check. On failure, **revert-the-accept** (reset to the pre-accept tip, restore `failed`, write no `accepted`/`done`) and report — the floor is never waived.
4. **Stamp the merge commit's `Karta-Accepted` + `Karta-Accept-Reason` (the human's reason) trailers only after the floor passes.** This fixes the ordering invariant **a stamped trailer implies the floor already passed** — the property that makes crash-resume safe (see Resume).
5. **Write refs, ref last:** write `done` → the (trailer-stamped) merge commit; delete `failed`; write `accepted` → the accepted item commit **last**. (Write order makes recovery deterministic — see Resume.)
6. **Backlog sink append**, if configured, **after** steps 2–5 succeed (so the recorded merge commit exists).

### What accept can and cannot waive

- **Can waive:** an acceptance-gate finding — an unmet `oracle.assertions[i]`, a missing contract artifact, or a SPEC-SUSPECT divergence.
- **Cannot waive:** the floor (compile / type-check / lint — guarded by the fresh post-accept check), or a safety-gate VIOLATION (the safety gate keeps its own escalate-to-human path; a safety override is a separate, higher-stakes decision, out of scope here).

## Defer — record and continue

Defer is a human choice obtained through the same enforced channel (a worker cannot nudge the run into a silent "deferred = fine"). On defer the halted item stays not-done (its `failed` ref stands; no `accepted`, no `done`). The orchestrator:

1. Appends the gap to the backlog sink, if configured.
2. Continues the wave loop. No new machinery is needed: the **existing done-ref frontier gate** (a frontier item is ready only when every `depends_on` has a `done` ref) already stalls the deferred item's direct and transitive dependents, because a deferred item never gets `done`. Every other item proceeds and merges as usual.
3. Hands off the run as **incomplete**: the report names the deferred item(s); the integration tip is plainly not a complete result (no `done` for the deferred item; run status is partial).

Defer never marks anything done — it is the "decide later" hatch.

## Backlog sink (user-provided)

An optional destination the user passes to karta-deliver at run time (a file path, or an append-command). karta appends gap records to it on accept and defer; it never reads, schedules, or revisits it. karta keeps no backlog of its own. Absent a sink, gaps are still surfaced once in the run report. A gap record names: the item id, the unmet assertion(s)/divergence, the decision (accept | defer), the human's reason, and — for accept — the merge commit.

## The worker never writes `accepted`; single-item mode has no accept

- **Invariant:** `karta-build` (the worker) **never** writes the `refs/karta/<slug>/item-<id>/accepted` namespace, under any RUN_MODE — alongside the existing wave-mode "the worker never writes `done`." Git refs carry no authorship, so this is stated as an enforceable invariant and backed by the resume guard below.
- **Single-item hatch:** a single-item binder skips deliver and hands straight to `karta-build`, where the worker is the sole party — there is no orchestrator to hold the human boundary. **Accept is therefore not available in single-item mode.** A directly-invoked worker that caps out writes only `failed` and halts, exactly as today. Accepting an unmet assertion in single-item mode requires escalating to an orchestrated context or a karta-plan opt_out.

## Two flavors of done

`done` ⟺ merged into integration (the invariant). It has two flavors, told apart by the `accepted` ref + trailer:

- **clean-done** — the gate passed; no `accepted` ref.
- **accepted-done** — a human waived a named unmet assertion; the `accepted` ref + trailers record what and why. Nothing pretends the assertion was met.

## Resume and audit

- An item with `done` is skipped on resume (clean or accepted alike); because accept writes `done`, an accepted item is not re-gated — and an accepted SPEC-SUSPECT is **not** re-flagged (this is what makes the stale-binder caveat honest within a run).
- **Authorship guard — trust reachability, not a label (git refs carry no authorship).** Commit identity and trailers are both worker-forgeable; the one thing a worker cannot forge is a commit on `karta/<slug>/integration`, which has exactly one writer (the orchestrator). So on resume an `accepted` ref is honored **only if** its companion `done` merge commit is reachable in the **first-parent history of the integration branch** AND carries the `Karta-Accept-*` trailers. "Orchestrator-produced" *means* "first-parent-reachable in integration" — the only non-circular discriminator. A merge commit off that first-parent chain (forged at a worker's own tip or a side branch), whatever its trailers or apparent authorship, fails → **suspect → halt for human**, never silently honored.
- **Re-entrancy (gated on the same reachability check).** The accept multi-ref write is not atomic; write-order is fixed (trailers stamped only after the floor passes; then `done`; then `accepted` last). On resume, for a merge commit on the integration first-parent chain:
  - carries trailers, missing `accepted`/`done` → the floor already passed (the trailer implies it); finish by writing `done`/`accepted`.
  - no trailers yet (crash between merge and floor check) → the floor is unconfirmed; re-run the post-accept floor check (revert-the-accept on failure), then stamp + write refs.
  - `failed` deleted but no integration-reachable merge → the accept did not complete; treat as still-halted and re-prompt.

  A trailer-bearing merge commit that is **not** first-parent-reachable is a forgery → suspect → halt for human; never auto-mint `accepted` from a trailer alone.

## Wave-tag lifecycle and revert-the-wave reconciliation

Accept and defer are Phase-4 decisions that land **after** the wave's Step-3 serial merge. So the `wave-<N>` success tag and revert-the-wave must treat Phase-4 accepts as part of wave N, or the tag goes stale and revert orphans the waiver:

- **Defer the `wave-<N>` success tag** until after the wave's Phase-4 accept/defer decisions resolve and a final post-wave check passes on the resulting tip — so `wave-<N>` points at the true wave tip with accepts included (an accepted merge is never left sitting beyond the tag).
- **Revert-the-wave** (`git reset --hard wave-<N>-base`) deletes the `done` + `built` + **`accepted`** refs of **every item integrated since `wave-<N>-base`** — enumerated by refs pointing at-or-after the base, **explicitly including Phase-4 accepts**, not only the Step-3 serial-merge set.
- **Restore the `failed` ref** (at the item-branch tip) for any item whose `failed` an accept cleared in this wave — returning it to its pre-accept *halted* state, so a resumed run re-prompts the human instead of silently rebuilding it as never-attempted.

State this in integration-branch.md (Revert-the-wave + the tagging scheme), deliver Step 4 + Phase 4, and the deliver gotcha. (The trailers die with the reset merge commit — expected; the restored `failed` ref carries the halt provenance forward.)

## Guardrails preserved (vs commit `59e3355`)

- **No worker self-clear — now structural.** The implementer cannot make a capped failure pass: it has no accept channel (the orchestrator asks the human directly and ignores worker accept claims), it is forbidden to write `accepted`, and single-item mode offers no accept at all. Acceptance is a non-worker decision — via a re-planned oracle `opt_out` (plan-time) or a human accept-waiver (build-time, orchestrator-recorded from a live human answer).
- **KARTA-DEFER stays inline-only.** The inline debt marker is the implementer's note for an untestable-here assertion; it never clears a gate. It is a different thing from the human accept-waiver and from the human defer choice — the declared-debt edit must keep them visibly separate so a worker can't read the marker as a self-accept path.
- **Never silent.** Every accept and defer is a recorded, surfaced, human decision (git + report; optional backlog).

## SPEC-SUSPECT caveat (accepted tradeoff)

Accepting a SPEC-SUSPECT merges the code and records the waiver, but does **not** fix the stale binder. Within the run/resume it is not re-flagged (the `done` ref skips it). A future *fresh* rebuild with no refs flags it again — if the staleness is permanent, amending via karta-plan is the real fix. The accept button is the per-run escape hatch when the user chooses not to re-plan.

## Files touched

- `skills/karta-deliver/SKILL.md` — Phase 4 four-way human choice via the host user-input facility; the enforced human channel + non-authoritative worker signals; the accept flow (re-validate → no-ff merge → fresh floor check + revert-the-accept → trailers-after-floor → refs ref-last → backlog); defer = continue-and-merge + backlog + incomplete handoff; defer the `wave-N` success tag until Phase-4 decisions resolve; revert-the-wave deletes `accepted` and restores `failed` (enumerate by ref at-or-after `wave-N-base`, including Phase-4 accepts); Phase 6 report lists accepted/deferred; the backlog-sink runtime input; gotchas + threat model.
- `skills/karta-deliver/references/integration-branch.md` (+ byte-identical copies — verify the full set before editing) — the `accepted` ref + waiver trailers in the ref scheme; accept as a second merge precondition (not just `built`); accept merges the item branch; the wave-halt `failed`-for-both rule; clean-done vs accepted-done; merged⟺done; resume honors `accepted` only via first-parent reachability in the integration branch (the authorship guard); the deferred `wave-N` tag lifecycle; revert-the-wave deletes `accepted` + restores `failed` (enumerate by ref at-or-after base).
- `agents/karta-acceptance-reviewer.md` — the cap halt's "ways forward" gains the human accept-waiver (obtained by the orchestrator from the human, recorded in git), alongside fix-and-rerun and re-plan opt_out; the gate itself never writes the accept; SPEC-SUSPECT may be accepted by the human; a wave SPEC-SUSPECT halt leaves a `failed` anchor.
- `skills/_shared/verification-gate.md` (+ all copies — verify set) — same "ways forward" update; keep "no worker self-clear"; the wave-halt-writes-`failed` rule for both DEVIATION-cap and SPEC-SUSPECT.
- `skills/karta-verify/SKILL.md` — on-DEVIATION cap and on-SPEC-SUSPECT text gain the human accept/defer hatch and the `failed`-anchor-on-halt note.
- `skills/_shared/declared-debt.md` (+ all copies — verify set) — distinguish inline KARTA-DEFER (worker note, can't clear a gate) from the human accept-waiver (orchestrator-recorded git waiver from a live human decision) and the human defer choice; the backlog sink as the destination for defer/accept records.
- `skills/karta-build/SKILL.md` — a wave-mode acceptance halt (capped DEVIATION or SPEC-SUSPECT) commits the item branch and writes `failed`; the worker never writes `accepted` (any mode); single-item cap-out writes only `failed`, no accept.

## Open detail (default chosen, flippable)

The backlog sink is a **runtime input to karta-deliver**, not a binder field — keeps the binder purely about the work and the sink an operator/environment choice. Could move to an optional binder top-level field if a plan-level destination is preferred.

## Naming note (flagged, default keep)

The human **defer** choice shares a word with the worker's inline **KARTA-DEFER** marker, and **accept** overloads the human waiver, the oracle `opt_out`, and a CONFORMANT pass. Default is to keep "accept"/"defer" (the user's established vocabulary) and disambiguate explicitly in the docs. A distinct verb for the human defer (e.g. "park") is available if the collision proves confusing.
