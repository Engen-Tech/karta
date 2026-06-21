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

## What it corrects

The gardner is liberal and current-state focused. It fixes three kinds of drift:

- **Broken pointers** — a doc names a path, file, symbol, command, or flag that no longer exists; it is corrected to the current name, or the dangling reference is removed.
- **Stale descriptions** — prose that describes changed code (signature, behavior, location, config keys) and no longer matches; it is rewritten to the current behavior.
- **Future-tense-now-landed** — "will add", "planned", "coming soon" for something that now exists; rewritten to present tense.

It leaves accurate prose, design rationale, and dated archival docs (`docs/specs/YYYY-MM-DD-*`, `docs/design-docs/YYYY-MM-DD-*`) alone. It edits **only** prose docs — never code, tests, the binder, or refs.

## Plain language on user-facing copy

The gardner writes everything a person reads to one standard — karta's bundled `karta-plainlanguage` skill. It applies this in two ways, split by what it is allowed to edit:

- **Docs** — the prose it corrects is written plainly: bottom line first, plain words, one name per thing.
- **Client-side code** — user-facing strings in the changed UI files (button labels, headings, empty/error/toast/validation messages, placeholder text) are checked against the same standard. The gardner never edits code, so it does not rewrite them; instead it returns a **suggested replacement** — the file, the current string, and the plain rewrite — for you to apply. In a delivery these suggestions ride along in the `docs: gardner <slug>` commit body and the run report.

This pass is bounded to the delivery's blast radius, and for code it is suggestions only — it never edits code and never halts the delivery.

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
