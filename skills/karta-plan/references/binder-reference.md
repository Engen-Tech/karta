# Binder Field Guide

The binder is karta's spine — the single JSON artifact that drives planning, build, and integration from start to finish. Every karta skill reads it; none of them write to it during a build run. The shape is karta's own. `validate_binder.py` gates every binder before a run: it checks schema validity, detects dependency cycles, flags dangling `depends_on` references, and prints an opt-out summary.

## Binder-level fields

| Field | Type | Required | Meaning |
|-|-|-|-|
| `slug` | string (kebab-case) | yes | Names the integration branch (`karta/<slug>/integration`) and all wave tags |
| `motivation` | string | yes | One-sentence reason this binder exists |
| `scope.included` | string[] | yes | Areas of the codebase in scope |
| `scope.excluded` | string[] | no | Things explicitly left out (prevents scope creep) |
| `design_facts.source` | string \| null | no | Path to the source design or prototype, or null |
| `design_facts.stack` | string | no | Resolved tech stack, recorded once at plan time |
| `token_manifest` | object \| null | no | Shared design-token map; present only when a token system exists |
| `env_contract.command` | string | yes | The project's own test/dev env command |
| `env_contract.supports_isolation` | boolean | yes | Whether the command accepts injectable isolation params |
| `env_contract.isolation_params` | string[] | no | Params that make runs isolated, e.g. `PORT`, `COMPOSE_PROJECT_NAME` |
| `work_items` | WorkItem[] | yes | Ordered list of work items (at least one) |

## Per-work-item fields

| Field | Type | Required | Meaning |
|-|-|-|-|
| `id` | string (kebab-case) | yes | Unique identifier; referenced by `depends_on` in other items |
| `title` | string | yes | Short human label for the work item |
| `estimate` | `S` \| `M` \| `L` | no | Size estimate |
| `depends_on` | string[] | no | IDs of items that must land before this one starts |
| `design_reference` | string | no | View or route ID from the design source, or the literal `none` |
| `component_map` | array | no | **UI-relevant, optional — present only when the stack has that surface** |
| `icon_map` | array | no | **UI-relevant, optional — present only when the stack has that surface** |
| `token_changes` | array | no | **UI-relevant, optional — present only when the stack has that surface** |
| `contract` | object \| string \| null | no | The open-shape interface this item exposes or consumes |
| `serialize` | boolean | no | When true, this item runs alone — no parallel build mates (default: false) |
| `shared_resources` | string[] | no | Resources that cannot be accessed concurrently, e.g. `db/migrations` |
| `surface.flagged` | boolean | no | Whether karta has flagged this item for human review |
| `surface.signals` | string[] | no | Human-readable reasons for the flag |
| `oracle` | Oracle | yes | How karta verifies the item is done |

## The oracle

Every work item carries an oracle — the verification contract karta uses before it considers the item complete.

The schema allows two shapes:

**A check oracle** names a test type and the command karta runs:

```json
{
  "type": "smoke",
  "assertions": ["route renders without error"],
  "command": "npm run lint && npm test"
}
```

`type` is one of `unit`, `integration`, `e2e`, `smoke`, or `visual`. `assertions` and `command` are optional but recommended. The floor for a non-opted-out item is compile / type-check / lint — a change that cannot clear that bar is surfaced for human review rather than auto-merged.

**An opt-out oracle** records a deliberate decision to skip karta's verification:

```json
{
  "opt_out": true,
  "reason": "migration verified by the team's existing migration test suite in CI"
}
```

Opt-outs are explicit and recorded, never silent. The `reason` field is required. After a run, karta reports every opted-out item and its reason so nothing slips through unnoticed. For the full rules on the floor and opt-out policy, see `definition-of-done.md`.

## On disk and resume

The default location is `.karta/binders/<slug>.json`. When karta-plan runs, it checks for an existing binder there first, then asks, then falls back to that default.

The binder is committed only at run boundaries — never mid-step. During a build run it is read-only: work items cannot modify the plan that governs them. This prevents a build step from corrupting its own governance.

Resume is git-native. karta tracks progress through commit markers, wave tags, and a `refs/karta/` ref namespace. The tag and ref scheme is documented in `integration-branch.md`.

## Example walk

The `example-binder.json` file has three work items from a notifications redesign:

- **`shell`** — sets up the routing and mount point. No dependencies, smoke oracle with an explicit lint + test command. Builds first.
- **`list-view`** — the visible notifications list. Depends on `shell` (won't start until shell lands). Uses a visual oracle that checks pixel fidelity against the design at 1440×900.
- **`schema-migration`** — adds a `read_at` column. No UI surface. Marked `serialize: true` so it runs alone, not in parallel with other items. Lists `db/migrations` as a shared resource. Uses an opt-out oracle with a recorded reason — the team's CI migration suite already covers this; karta reports the opt-out rather than re-running it.
