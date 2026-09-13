# BUG — the roundtable floor counts a blank reply as a review

**Severity:** S1 (a review gate accepts evidence of a review that did not happen)
**Status:** fixed in `scripts/roundtable/run_review.py`
**Found by:** a roundtable panelist (codex) reviewing the Pi parity delivery, 2026-09-13
**Verified:** yes — reproduced directly against `normalize_panel` + `validate_normalized`

## What is wrong

`min_providers` is the floor that makes a roundtable record mean something: at least N distinct
providers must each have returned a real verdict before the recorder will file a panel. The gate
itself never reads what the panel concluded — a fresh record is the whole requirement — so this
floor is the only thing standing between "a review happened" and "a file exists".

It counted a blank reply.

`_nonerror_status` supplied a fallback verdict from the entry's transport status whenever that status
was not in `ERROR_STATUSES` (`error`, `timeout`, `failed`, `rate_limited`, `cancelled`). It never
looked at the response. So an entry that returned nothing still carried a truthy verdict — its
status string — and counted toward the floor.

`not_found` made it sharpest: a failure, but not in the error set, so it read as a verdict.

## Reproduction

Against the module, before the fix:

```
two empty responses, status ok     accepted=True  verdicts=['ok', 'ok']
two not_found, no response         accepted=True  verdicts=['not_found', 'not_found']
one ok-empty + one timeout         accepted=False verdicts=['ok', None]
genuine: two real verdicts         accepted=True  verdicts=['ok', 'ok']
```

Two blank replies met a floor of 2. The `one ok-empty + one timeout` row shows the floor was still
doing real work for error statuses — the hole was specifically the non-error path.

## Fix

`_nonerror_status` now requires substantive content as well as a non-error status: the entry's
`response`, falling back to its `summary`, must contain non-whitespace text. A transport success
with nothing in it is not a review.

```
two empty responses, status ok     accepted=False
two not_found, no response         accepted=False
one blank + one real               accepted=False
genuine: two real verdicts         accepted=True
```

Three regression fixtures were added to `run_review.py --self-test`, which moves from 75/76 to
78/79 — the three new checks pass. The one remaining failure (`migrated ledger has thirteen
rounds`) is **pre-existing and unrelated**: the self-test was confirmed to fail identically before
this change, by stashing it and re-running.

## Why it matters

The record filed for the Pi parity delivery was produced by a panel where four of six providers
genuinely answered, so that record is sound. But the floor could not have told the difference — and
in this repo the roundtable edict is what blocks a plan commit and a delivery merge. A floor that
accepts blank replies is a gate that can be satisfied without a review.

## What is not fixed here

The fixture failure this uncovered: `run_review.py --self-test` fails its `migrated ledger has
thirteen rounds numbered 1..13` check, and has done since the context-economy ledger grew past
thirteen rounds. It is the same staleness already corrected in `AGENTS.md` — the ledger holds
nineteen. Worth its own fix; it means this repo's own gate reports a failure that nothing acts on.
