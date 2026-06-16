# Phase & section labels

karta's skills and reference docs are organized into numbered phases (and numbered sections). Numbers show **order**; they are fragile as cross-reference targets because inserting or reordering a phase shifts every number after it. So each phase/section also carries a **stable one-word label**, and cross-references point at the label, not the number.

## The rule

- A phase/section header keeps its **number** and gains a **label** anchor, appended after two spaces:
  - `### Phase 6 — Acceptance loop  ` `` `build:acceptance` ``
  - `## 7. Slice → work-item breakdown and S/M/L estimate  ` `` `ui:slice` ``
- A **cross-reference** uses the label, never the bare number: write `` see `build:acceptance` `` (or, for readability, `` the acceptance loop (`build:acceptance`) ``), not "see Phase 6".
- Labels are **unique within their doc** and never reused, so they stay valid across reorders and renumbering.
- **Reserved/placeholder phases** (e.g. karta-build Phase 3, Phase 8) carry no label.

## Addressing (colon-path)

Address any anchor by colon-path from its doc root:

- `root:phase` — a phase/section (e.g. `deliver:waveloop`, `ui:tokens`).
- `root:phase:substep` — a sub-step inside a phase (e.g. `dvl:invoke:auth`, the authenticated-views prerequisite inside the design-validation loop's invoke step).

The leaf is a single lowercase word naming the thing's job. Because labels are unique within a doc, the leaf alone is unambiguous; the path shows where it sits.

## Roots

|Root|Doc|
|-|-|
|`plan`|`skills/karta-plan/SKILL.md`|
|`build`|`skills/karta-build/SKILL.md`|
|`deliver`|`skills/karta-deliver/SKILL.md`|
|`verify`|`skills/karta-verify/SKILL.md`|
|`validate`|`skills/karta-validate/SKILL.md`|
|`dvl`|`skills/karta-build/references/design-validation-loop.md`|
|`ui`|`skills/karta-plan/references/ui-analysis.md`|

A reference doc gets its own root only when something points **into** it by sub-step (like `dvl` and `ui`). Most reference docs are cited by file path alone and need no labels.

## Adding a phase or section

1. Give the new header a number for its position (keep the existing numbering scheme; reserved gaps are fine).
2. Pick a unique one-word label under the doc's root and append it after two spaces.
3. When you reference it elsewhere, use the label — not the number.

## Why hybrid (number + label) rather than labels alone

Phases are a sequence; the number communicates "this runs after that" at a glance, which a bare label loses. Keeping both gives readers the order *and* gives cross-references an anchor that can't drift. (A bare number used as a cross-reference target silently goes stale when a phase is reordered or renumbered; a label doesn't.)
