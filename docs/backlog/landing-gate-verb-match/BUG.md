# The merge gates fire on `git merge-base`, `merge-tree` and `merge-file`

**Filed** 2026-08-23. **Status** Ready (scoped, unblocked, one-line fix). **Owner** upstream.

## Summary

`scripts/hooks/roundtable_gate.py` matches the git verb with `\bmerge\b`. A word boundary sits
between `merge` and the `-` in `merge-base`, so every git command whose subcommand merely *starts
with* `merge` is treated as a merge. Three read-only plumbing commands are caught:

- `git merge-base` — the ordinary way to find a fork point
- `git merge-tree` — a read-only three-way merge preview
- `git merge-file` — operates on three files, touches no ref

None of them move a branch. None of them can land a delivery. All three are blocked today, by both
the integration-merge gate and the landing gate.

## How it was found

While reviewing a binder, two consecutive read-only commands were refused:

1. An ancestry check (`merge-base` naming an integration ref while on the default branch) hit the
   **landing gate** and was told "landing … is the human's decision, not an agent's."
2. The same command, retried to find a fork point, hit the **integration-merge gate** and was told
   to record a roundtable review before it lands.

Both messages are about landing a delivery. Neither command lands anything. The workaround was to
compute the fork point in Python by walking two `git rev-list` outputs — slower, and it obscures
what the command is doing.

## The defect

`scripts/hooks/roundtable_gate.py:80`:

```python
_MERGE_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+merge\b")
```

`\b` is a word boundary, and `-` is a non-word character, so `merge\b` matches the `merge` inside
`merge-base`. The intent is the *whole subcommand token* `merge`.

## The fix

Replace the trailing `\b` with a negative lookahead that also rejects a following `-`:

```python
_MERGE_RE = re.compile(r"\bgit(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+merge(?![-\w])")
```

Measured against the matcher, this keeps every true positive and drops all three false positives:

| command | today | fixed |
|-|-|-|
| `git merge --no-ff <ref>` | blocked | blocked |
| `git merge --squash <ref>` | blocked | blocked |
| bare `git merge` | blocked | blocked |
| `git merge-base …` | blocked | allowed |
| `git merge-tree …` | blocked | allowed |
| `git merge-file …` | blocked | allowed |

## Why it matters more than the inconvenience

The gate's own doctrine (AGENTS.md, "What the landing gate does and does not do") argues the ref
match is *anchored rather than searched* because "the first thing this gate did when it went live
was refuse a command that merely quoted one." That reasoning was applied to the **ref** and not to
the **verb**. This is the same class of defect one field over.

A gate that blocks harmless commands trains the people it governs to reach for
`KARTA_SKIP_ROUNDTABLE=1` out of habit. The hatch is meant to be deliberate and explained. Every
false positive makes the explained case harder to tell from the reflexive one, which costs the
audit trail more than the blocked command costs anyone's time.

## Second, smaller finding — testing the matcher trips the matcher

A `python3 -c` snippet containing the literal string `git merge --no-ff karta/x/integration`, as
test data for the regex, is blocked by the gate. The anchoring narrowing handles a merge command
quoted mid-segment, but a test case that is *shaped like* a merge invocation at the head of its own
segment inside a script literal still reads as one.

This is inherent to matching command text and is not worth a code change on its own. It is worth a
line in the doctrine: **the gate cannot be exercised from a shell command that contains its own
trigger** — build the fixture strings by concatenation, or run the test from a file the hook does
not read.

## Verification

Both findings were reproduced against `scripts/hooks/roundtable_gate.py` at `076b063`. The fix is a
change to one regex plus a self-test case per false positive in the module's own `--self-test`
block, alongside the existing `check("detect merge", …)` case near `:390`.
