# Automatic doc-gardner

The doc-gardner keeps a repo's prose in lockstep with its code, automatically. When it is on, every `karta-deliver` run ends by rewriting any drifted docs to match the just-delivered code and committing the fix. There is no advisory report, no human waive, and no halt — it corrects and the delivery proceeds. It is **all or nothing**: opted in, drift is fixed automatically; opted out, it never runs.

## Turn it on

Add `.karta/doc-gardner.json` to your repo:

```json
{ "enabled": true }
```

That single switch is the only setup. Optionally bias the gardner's attention with a freeform note (it is **not** a list of docs and never limits what gets swept):

```json
{ "enabled": true, "focus": "keep the public API reference and the architecture overview honest to the code" }
```

Remove the file, or set `"enabled": false`, to turn it off. The file shape is gated by `skills/karta-doc-gardner/references/doc-gardner-schema.json`.

Once the switch is on, the doc-gardner phase runs on **every** delivery and cannot be silently skipped — that is the "once opted in, always required" contract.

## The doctrine — minimalism and derivability

The gardner holds docs to one goal: **capture the whole state of the app as it stands now** — the whole, not partial, with no gaps. Docs store only facts, and decisions recorded against those facts (rationale, accepted trade-offs, deliberate deferrals, and the designs that informed them). Anything that can be derived from the code or from other docs is derived, not documented. Derivability is not extrapolation: no projection, no prediction, no speculation — every statement grounded in the current tree. The gardner examines every change it makes against these tests and culls anything extraneous, including its own new prose.

## What it corrects

The gardner is current-state focused. It fixes five kinds of drift:

- **Broken pointers** — a doc names a path, file, symbol, command, or flag that no longer exists; it is corrected to the current name, or the dangling reference is removed.
- **Stale descriptions** — prose that describes changed code (signature, behavior, location, config keys) and no longer matches; it is rewritten to the current behavior.
- **Future-tense-now-landed** — "will add", "planned", "coming soon" for something that now exists; rewritten to present tense.
- **Speculative or derivable content** — prose that projects or predicts, or that restates what the code or another doc already answers; the span is culled. Recorded decisions and design documents stay — designs are informers for decision-making, and deciding to defer work is decision capture, not speculation.
- **Coverage gaps** — current state in the change's blast radius that no doc captures (a behavior, command, flag, config key, or contract with no prose anywhere); closed with the smallest grounded prose in the most specific existing doc.

It leaves doctrine-passing prose (grounded, non-derivable facts and recorded decisions) alone, never culls design documents (specs, design docs, plans — they record what a decision was weighed against, though their broken pointers and landed-now promises still get fixed), and leaves dated archival docs (`docs/specs/YYYY-MM-DD-*`, `docs/design-docs/YYYY-MM-DD-*`) entirely untouched. It edits **only** prose docs — never code, tests, the binder, or refs (the lone exception is adding `superpowers/` to `.gitignore`; see below).

## Salvaging a superpowers folder

If a `superpowers/` folder is present (top-level `superpowers/` or `docs/superpowers/`), the gardner salvages it rather than letting its keepers vanish when the folder is ignored. It relocates every keeper — recorded facts, decisions, and design documents — into the right `docs/` home for the repo's hierarchy, leaves pure scratch (brainstorm logs, working notes) where it is, and then ensures `.gitignore` ignores `superpowers/`. That `.gitignore` line is the single non-prose file the gardner ever writes. Where there is no such folder, this step does nothing.

## Plain language

The prose the gardner writes follows one standard — karta's bundled `karta-plainlanguage` skill: bottom line first, plain words, one name per thing. It applies this to the doc prose it corrects (README, `docs/`, `AGENTS.md`, `ARCHITECTURE`, and the like) — **prose artifacts only**. It never touches code, HTML, or templates, here or anywhere.

## Scope is recomputed live (new files are never missed)

Nothing about *what* to garden is stored — the switch is the only static element. Every run, the gardner re-globs the live doc surface (`README*`, `docs/**`, `AGENTS.md`, `CLAUDE.md`, `ARCHITECTURE*`, other top-level markdown) and re-derives the change blast radius from `git`. So:

- a doc added in an earlier delivery is in the set automatically the next run;
- new or changed code is in the blast radius automatically;
- a doc that rots with no related code change is caught by the run's repo-wide pointer pass.

There is no cached analysis that can go stale.

## Where the corrections land, and how to review them

In a delivery, the corrections are committed to the integration branch as one labeled commit: `docs: gardner <slug>`. karta never pushes and never commits to a protected branch — delivery ends at the integration branch you review and merge yourself. So the `docs: gardner` commit is your review surface:

- Inspect it: `git show` the `docs: gardner <slug>` commit to see exactly what changed.
- Revert it like any commit if a correction is wrong: `git revert <sha>` (or drop it before you merge the integration branch).

There is no inline waive because there is nothing to wait for — the corrections are already a reviewable commit.

## Run it on demand

You can also invoke the `karta-doc-gardner` skill directly for a one-off correction pass, independent of a delivery (for example "garden the docs"). A direct run corrects the working tree and hands the edits back for you to review and commit; it does not require the opt-in switch (the switch only governs the automatic delivery path).

## Accepted risk

An LLM rewriting docs automatically can mis-correct. That is the deliberate trade for zero-babysitting upkeep. The guardrails that do not reintroduce a human gate: corrections are scoped strictly to detected drift, the gardner re-verifies its own output before committing, and everything lands as a single labeled commit on a branch you review before merging.

For the canonical agent and the generate-and-guard workflow, see [AGENTS.md](../../AGENTS.md).
