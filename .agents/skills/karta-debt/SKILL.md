---
name: karta-debt
description: >-
  Harvest every KARTA-DEFER and KARTA-SME-OVERRIDE marker in the repo into a one-shot, read-only ledger so deliberate shortcuts and overrides don't rot into "later means never". Groups by file, flags markers with no upgrade trigger. Writes nothing, tracks nothing. Trigger phrases: "karta debt", "list the shortcuts", "what did we defer", "harvest the overrides".
---

karta-debt collects karta's inline deferral and override markers into one ledger so a deferral can't quietly become permanent. It is **read-only**: it reads and reports, and changes nothing — consistent with karta's no-backlog rule (the ledger is a report handed to you once, not a list karta persists, schedules, or revisits).

## Two marker families

- `KARTA-DEFER(<id>): <what> — <why> — follow-up: <trigger>` — an inline build-time deferral (see karta-build's references/declared-debt.md).
- `KARTA-SME-OVERRIDE(<pack>: <rule>): <rationale> [ceiling: <limit>; upgrade: <trigger>]` — a deliberate deviation from an SME pack's Review checklist (see karta-build, `build:merge`).

## Scan

Grep the repo for both markers, skipping VCS / build / vendor output:

`grep -rnE 'KARTA-DEFER|KARTA-SME-OVERRIDE' . --exclude-dir={.git,node_modules,dist,build,.venv}`

Each hit is one ledger row. The marker prefix keeps prose that merely mentions the convention out of the ledger.

## Output

One row per marker, grouped by file:

`<file>:<line>  <family>  <what>.  trigger: <upgrade / follow-up, or "none">.`

Pull the trigger straight from the marker — `follow-up:` for a `KARTA-DEFER`, `upgrade:` for an override. Flag any marker with no trigger as `no-trigger` — those silently rot. A `KARTA-SME-OVERRIDE` whose rationale states it is a permanent exception is expected; tag it `permanent`, not `no-trigger`.

End with `<N> markers, <M> no-trigger`. Nothing found: `No karta debt markers. Clean ledger.`

## Boundaries

Reads and reports only — changes nothing, persists nothing, schedules nothing. One-shot. If you want it saved, that is your copy to keep; karta does not write or track a ledger file. This skill never edits code, tests, or the binder. Write the report in plain language (the karta-plainlanguage standard).
