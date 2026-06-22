# Multi-binder sequences — design (V1)

> **Status: settled V1 design.** Supersedes the problem brief
> [2026-06-21-multi-binder-planning-discussion.md](2026-06-21-multi-binder-planning-discussion.md),
> which framed the problem and the directions. This spec records the decisions made in the
> brainstorm and scopes V1 deliberately small.

## Goal

Let `karta-plan` emit an **ordered set of self-sufficient binders** when a job naturally
separates into stages that must land in order — plus a plain manifest recording that order
and a plain-language run-order suggestion. The human runs the binders manually, one at a
time. karta adds **no** cross-binder machinery.

## The decisions (from the brainstorm)

These are firm, and the rest of the spec follows from them:

1. **Each binder is self-sufficient and independently mergeable.** A sequence is sliced
   expand → migrate → contract (e.g. *new* adds standalone code, *edit* rewires call sites,
   *delete* removes the now-dead old code) so **every binder leaves the default branch green
   on its own**. This is the only model consistent with reviewing and merging each binder to
   main, and karta's existing floor already enforces the compile/type-check/lint half of it.
2. **No magic.** No binder-to-binder coupling field, no dependency graph, no cycle/topological
   resolution, no preflight guard, no auto-sequencer, no manifest validation.
3. **Movement between binders is fully manual.** The human runs `karta-deliver` per binder, in
   the suggested order, reviewing and merging each before *choosing* to start the next.
4. **The only enforcement is the existing floor + gates.** A binder that cannot stand on its
   own cannot clear its own gate and so cannot merge — no new machinery needed to guarantee
   the green-tree discipline.

The guiding principle for every open detail: **simple over complete.** Cut features or make
them manual before adding dependency-resolution complexity.

## Architecture

The change is almost entirely in `karta-plan`. Two new outputs and one dropped limitation;
nothing else in the pipeline changes.

### 1. `karta-plan` emits a set of binders (when warranted)

`karta-plan` splits a job into a sequence only when **either**:
- the user explicitly asks for separate, ordered binders (the trigger case: *"new first, then
  edit, then delete — separate binders"*), **or**
- the work genuinely requires ordered, separately-mergeable stages — the expand → migrate →
  contract shape is the canonical one.

Default stays **one binder.** A sequence is the exception, not the reflex; do not split work
that fits one binder. When it does split, each emitted binder is a normal binder that passes
`validate_binder.py` on its own and is mergeable on its own.

**Slug convention:** `<base>-<N>-<phase>`, e.g. `note-tags-1-new`, `note-tags-2-edit`,
`note-tags-3-delete`. The number gives readable ordering; `<phase>` is a short verb. This is a
**convention**, not a rule — the only hard requirement is that slugs are unique per repo
(already enforced, because the slug is the binder filename and names the integration branch).

**Validate-each, commit-together.** Before presenting the set, `karta-plan` validates **every**
binder in it — each must pass `validate_binder.py` on its own (the existing per-binder check; no
sequence-level validation is added). The whole set — all binders **plus** the manifest — is then
committed together on the explicit `commit` verb, exactly as a single binder is committed today.
A set that contains an invalid binder is not presented for commit, the same rule as a single
binder.

### 2. The sequence manifest

`karta-plan` writes one manifest per sequence at `.karta/sequences/<name>.json`:

```json
{
  "sequence": "note-tags-editing",
  "order": [
    "note-tags-1-new",
    "note-tags-2-edit",
    "note-tags-3-delete"
  ]
}
```

- `sequence` — a human-readable name for the set.
- `order` — the binder slugs in intended run order.

That is the whole format. It is a **durable record for the human** — "run these, in this
order." Nothing in karta reads it at delivery time, and karta does **not** validate it (an
unresolved or duplicate slug is a human-visible typo in a plain file, not a gate). No
per-binder metadata, no status, no descriptions — that edges into the deferred "what's next"
work.

### 3. The run-order suggestion

After writing the set, `karta-plan` states the order in plain language in its normal output:

> Planned 3 binders. Run them in order: **first** `note-tags-1-new`, **next**
> `note-tags-2-edit`, **then** `note-tags-3-delete`. Review and merge each before starting the
> next.

A linear first → next → then suggestion. No graph, no conditional ordering.

### 4. Drop the one-binder-per-run limitation

Remove the "not supported in V1" language so karta stops telling the user multi-binder is
impossible while its own vocabulary invites it:
- `skills/karta-plan/SKILL.md:21-25` — the "Scope limits (V1)" line that says it "plans one
  binder per run."
- `skills/karta-plan/SKILL.md:244` — "**One binder per run in V1.** … Multi-binder
  partitioning is not supported in V1."

Replace with language describing sequence emission, the manifest, and the run-order suggestion.

### Everything else is unchanged

`karta-deliver`, `karta-build`, `karta-verify`, `karta-validate`, `karta-doc-gardner`, resume,
and `validate_binder.py` are **untouched**. Each binder is an ordinary binder; the pipeline
sees one binder at a time, exactly as today. This is stated explicitly so the implementation
does not "improve" these surfaces.

## Data flow — the human's workflow

```
karta-plan  ──▶  N self-sufficient binders  +  .karta/sequences/<name>.json  +  order suggestion
                      │
   you ──▶ karta-deliver <binder-1>  ─▶ build/gate ─▶ integration branch ─▶ YOU review + merge to main
   you ──▶ karta-deliver <binder-2>  ─▶ build/gate ─▶ integration branch ─▶ YOU review + merge to main
   you ──▶ karta-deliver <binder-3>  ─▶ build/gate ─▶ integration branch ─▶ YOU review + merge to main
```

The human is the sequencer. karta supplies the parts and the suggested order; the human decides
when each stage runs.

## Error handling and edge cases

Because there is no cross-binder state, the hard cases stay simple:

- **A binder halts at its gate mid-sequence.** Since every earlier binder was self-sufficient
  and already merged green, the default branch is still green. The human fixes or re-plans the
  halted binder and continues. There is no partial-sequence state to unwind.
- **Re-planning one binder.** Binders are independent — re-planning one does not invalidate the
  others (there is no `after` reference to break). If a slug changes, the human updates the
  plain manifest; nothing else depends on it.
- **Single-item binder in a sequence.** It runs through `karta-build`'s single-item path and
  self-merges, like any single-item binder. There is no guard for it to bypass, because there
  is no guard.
- **Manifest drift.** Since nothing reads the manifest, drift is cosmetic; the human is the
  source of truth for order. Acceptable for V1 by design.
- **The human runs binders out of order.** Allowed. If they start *edit* before *new* is
  merged, *edit*'s own floor/gates fail (it references code that is not there yet), surfacing
  the problem the normal way. karta does not pre-empt this — the floor catches it.

## Testing

The change is mostly `karta-plan` prose plus a manifest artifact, so testing is correspondingly
light:

- **Each emitted binder validates standalone.** Every binder in a sequence must pass
  `uv run --script skills/karta-plan/scripts/validate_binder.py --binder <each>` independently —
  the existing validator, unchanged, is the check.
- **Manifest is well-formed.** A worked example sequence (the `note-tags` case) lives as a
  reference: three binders + their `.karta/sequences/note-tags-editing.json`, each binder
  validating on its own. This doubles as documentation of the format.
- **No new validator code.** V1 adds no schema and no validation pass, so there are no new
  self-test cases in `validate_binder.py`.

## Affected files

- `skills/karta-plan/SKILL.md` — sequence emission, slug convention, manifest write, run-order
  suggestion; drop the one-binder-per-run language (`:21-25`, `:244`).
- `skills/karta-plan/references/sequence-manifest.md` *(new)* — documents the manifest format
  and the run-order suggestion.
- `skills/karta-plan/references/binder-reference.md` — a short note that a binder may be one of
  an ordered set recorded in a sequence manifest (binders themselves are unchanged).
- `README.md` and `docs/how-to/*` — soften any "one binder" framing.
- Generated mirrors (`.agents/`, `plugins/karta/`) regenerate from the canonical edits via
  `sync_codex_skills.py`; the four pre-commit checks must stay green.

No binder-schema change. No `validate_binder.py` change.

## Out of scope — deliberately deferred

- **Cross-binder dependency machinery** — `after` fields, dependency graphs, cycle/topo
  resolution, a preflight guard, an auto-sequencer, manifest validation. All rejected for V1
  to avoid accidental dependency-resolution complexity.
- **"What's next" visibility, at binder and work-item level** — the real fix for ongoing
  orientation (which binder, which work item to do next). It is its own design session,
  intentionally kept out of this spec.
