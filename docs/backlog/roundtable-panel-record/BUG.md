# The commit gate cannot accept a single-model review

**Filed** 2026-08-17. **Status** Ready (scoped, unblocked). **Owner** upstream.

## Summary

`.karta/roundtable.json` has one master switch. When the multi-model roundtable environment is
unavailable, the only way to keep working is `enabled: false`, which turns off **both** enforced
gates — plan-commit and deliver-merge — because there is no record kind the gate will accept other
than one that passed the `min_providers` floor.

The house review is now the multi-lens panel (`scripts/review/binder_review_panel.js`). It is a
legitimate review and it finds real defects, but it is one model wearing six hats, so it can never
satisfy that floor. The result is that karta's own binders are reviewed by discipline rather than by
a check — the exact "skippable prose" the doctrine exists to avoid.

## What the code does today

**One switch turns off everything.** `scripts/hooks/roundtable_gate.py`, in `decide()`:

```python
if not isinstance(config, dict) or not config.get("enabled"):
    return 0, ""
```

Both the binder-commit branch and the integration-merge branch sit below that line, so they go
inert together. There is no per-kind configuration.

**The recorder is strict.** `record_review()` in `scripts/roundtable/run_review.py` calls
`validate_normalized(panel, floor)` and raises `ValueError` writing nothing when the panel has fewer
than `min_providers` distinct providers carrying a real verdict. Its own message names the case:

> an all-error or single-model dispatch is not a multi-model review

So the sanctioned writing path refuses a panel record by design, and correctly — it is enforcing an
honest claim about what a roundtable record means.

**The reader is not strict, and this is the surprise.** The gate never inspects a record itself. It
shells out to `run_review.py --check`, which is `check_fresh()`, whose entire test is:

```python
return isinstance(record, dict) and record.get("reviewed_hash") == current_hash
```

No panel, no provider count, no tool, no timestamp is required.

**Verified.** A record containing nothing but a correct hash satisfies the gate:

```
$ cat .karta/roundtable/probe.json
{"reviewed_hash": "005cb249cbecd5d9..."}

$ uv run run_review.py --check --target probe --kind binder ; echo $?
0
```

Two consequences. First, the fix is smaller than it looks: the gate side already accepts a
non-roundtable record, so the work is mostly about making that acceptance *deliberate and labelled*
rather than an accident. Second, this is a latent hole today independent of panels — a hand-written
hash file passes the gate with no review behind it at all, and nothing in the audit trail would
show it.

## What a fix has to be true of

1. **A record says what kind of review it is.** A discriminator on the record — multi-model
   roundtable versus single-model multi-lens panel — written by whatever files it, so nothing
   downstream has to infer it from the presence or absence of a `panel` array.
2. **A panel record can never claim to be multi-model.** It should be impossible to produce one that
   carries a provider list, and `min_providers` must not be reinterpreted to let it through — the
   floor keeps its current meaning for the kind it governs.
3. **The gate is configurable on which kinds it accepts.** A repo that requires multi-model review
   must be able to refuse panel records. That is a config decision, not a code path.
4. **`check_fresh()` validates the kind, not only the hash.** Otherwise (3) is unenforceable, and
   the hash-only hole above stays open regardless of what else changes.
5. **The existing records stay valid.** `.karta/roundtable/` already holds committed records with no
   kind field; they are multi-model roundtables and must keep passing.

## Decisions that are not mine to make

- **Where panel records live.** Alongside roundtable records under `.karta/roundtable/` with a kind
  field, or in a sibling directory. The hook contract currently derives the path from the target
  key (`<slug>.json`, `branch-<tip-sha>.json`), so a sibling directory means teaching the gate a
  second lookup; a shared directory means the audit trail mixes two kinds of evidence in one place.
- **How the config expresses it.** Extending `enabled` into a list of accepted kinds is one shape;
  a separate key beside `min_providers` is another. The first makes "off" and "roundtable only" and
  "either" one axis; the second keeps `enabled` boolean and adds a second thing to reason about.
- **Whether a panel record is enough on its own for the deliver-merge point.** The plan-commit
  point reviews a binder that is still editable. A delivery merge is the last moment before content
  reaches main, and it is reasonable to hold that one to multi-model even while plan-commit accepts
  a panel.

## Why it matters now

Three binders were committed under the disabled gate on 2026-08-17 (`a4e519a`, `e05a08f`,
`1de5a04`). The panel did find two blocking defects in `design-reference-gate` before that commit,
so the review is doing real work — but nothing required it to run, and nothing recorded that it did.
There is no audit trail for any of the three.

See "Review before commit" in [AGENTS.md](../../../AGENTS.md) for the current rule, and
[docs/how-to/roundtable.md](../../how-to/roundtable.md) for the machinery this would re-enable.
