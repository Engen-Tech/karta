# Declared Debt

## Marker syntax

karta uses inline debt markers to declare deferrals during build. A marker takes the form:

```
KARTA-DEFER(<id>): <what is deferred> — <why> — follow-up: <what happens next>
```

Place the marker as a comment in the source at the exact site of the deferral.

## What a deferral records

Each marker states three things:

- **What is deferred** — the specific work skipped right now (a test, an edge case, a stubbed dependency, a TODO implementation).
- **Why** — the reason it is deferred now (out of scope for this item, blocks on another item, known unknowns at build time).
- **Follow-up** — the concrete next step (a linked work item, a ticket reference, a named owner, or the condition that unblocks it).

The marker is a snapshot: it states what *is* deferred and why, not a history of decisions.

## Surfacing

Deferrals do not silently disappear. karta collects every `KARTA-DEFER` marker in the built result and surfaces them at the end of the wave — in the build summary and in the item's done record. A deferred item is never presented as fully complete without the deferral list. The binder's review output includes a consolidated debt register across all items.
