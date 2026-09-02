# Pi package integration session — findings (2026-08-30)

A live dogfood of the Pi package (`karta_dispatch` + the `karta-status` / `karta-plan` /
`karta-deliver` / `karta-build` skills) against a real consumer repo (`parchmark`, a
Vue + FastAPI app). Two binders were taken end to end. Nothing here is a correctness bug in
the plan→build→verify core; every finding is an **operator-experience gap at the boundary
between karta's git-ref world and a messy real repo**. Recorded as backlog items 23–26.

## Session shape

1. **`backend-hygiene`** (5 items) — already fully implemented and merged to `main` before the
   run. `deliverBinder` dispatched 5 parallel workers; all 5 correctly returned `no-change`,
   citing the existing commits and refusing to fabricate diffs. Top-level result: `blocked`.
2. **`auth-service-refresh-coverage`** (1 item) — planned in-session, committed, delivered. The
   worker wrote correct tests and passed lint/format/types, but the floor check
   (`make test-backend-all`) ran the full DB-backed suite, which needs Docker; Docker was down on
   the host, so every fixture-backed test `ERROR`ed at setup and the floor "failed" →
   `retry` / `blocked`. When Docker was later started, the identical oracle passed cleanly
   (`auth_service.py` 33 stmts / 0 miss / 100%; positive control: deselect the two tests → 93.94%,
   missing `74, 79`).

## What worked (keep)

- **No-slop workers.** 6/6 refused to invent no-op changes; every `no-change` summary named the
  already-present commit and (for the migration) the head-fork it declined to create.
- **Honest-oracle methodology earned its keep.** The plan-time positive control was the thing that
  later *proved* the tests weren't vacuous (deselect → coverage drops, exact lines re-listed).
- **Structured, auditable output** (`karta-delivery-v1` / `karta-build-item-v1` /
  `karta-worker-result-v2`), per-worker authority attestation (before/after snapshots), and
  deterministic tooling (`validate_binder`, `detect_stack`, `check_shared_terms`).
- **`no-change` is already a first-class per-item status** — the granularity exists at item level.

## Finding A — a binder whose work landed outside karta reads as `not_started` forever

`backend-hygiene`'s work reached `main` through ordinary PRs, not karta's own merge machinery, so
no `refs/karta/<slug>/item-*/done` refs exist. `karta-status` therefore reports the binder
`not_started` and keeps pointing at `karta-deliver`, which re-dispatches workers that all whiff on
`no-change` — an endless loop whose only exit is archiving the binder by hand.

Root cause is by-design and total: `skills/karta-status/scripts/karta_next.py` `_binder_status`
(around line 51) derives state **only** from the `refs/karta/` namespace. There is no
reconciliation against what the default branch actually contains. Pure git-ref state is elegant, but
work performed by another hand is invisible to it, and there is no read-only affordance that says
"this binder's declared surface already exists on `main` — maybe it's done."

Evidence: five successive `no-change` builds; `karta-status` output `backend-hygiene ○ / ▶ next:
karta-deliver backend-hygiene` even though all five items were ancestors of `main`.

## Finding B — the runtime records `env_contract` / `runtime_contract` but never acts on them

The `auth-service-refresh-coverage` binder's `env_contract` spelled out the exact constraint
("Docker is reachable only through Incus… CI has it natively"). The worker's own summary even said
"full pytest needs Docker (unavailable in sandbox)". Yet the floor check ran `make test-backend-all`
on the bare host anyway, hit the missing daemon, and surfaced a wall of truncated pytest `ERROR`
output whose decisive signal (fixtures can't reach a database) was buried.

A grep across `extensions/pi/*.ts` shows the entire `preflight` apparatus concerns the **gate
provider/model** (`child-runtime.ts`, `gate-runner.ts`, `dispatch-tool.ts` `preflightGate`). Nothing
in `delivery-runner.ts`, `wave-runner.ts`, or `build-finalizer.ts` reads `env_contract`,
`runtime_contract`, `on_unavailable`, or probes Docker. So `runtime_contract.on_unavailable: halt`
is inert schema — no code consumes it. The binder documents the precondition; the runtime ignores it
and fails opaquely instead of halting cleanly with the remediation the binder already carries.

## Finding C — no-change / blocked runs leave scaffolding, and a pre-commit floor failure leaves work uncommitted

Both deliveries left worktrees + branches parked at the `main` tip with no refs (6 for
`backend-hygiene`, 2 for the auth binder); they were removed by hand. Separately, when the auth
item's floor failed **before** the worker committed, the correct changes sat unstaged in the item
worktree (branch never advanced past `main`), recoverable only via `git diff > patch`. A clean
recovery story wants the branch to carry a commit even when the floor fails, and wants a
no-change/blocked run to clean up (or clearly hand off) its own scratch worktrees.

## Finding D (minor) — `detect_stack.py` scans repo-root only

In this monorepo (`backend/` + `ui/`), a root run returned `{"dependencies": [], "languages": []}`;
pointing it at `backend/` produced the real dependency set. `plan:sme` matching depends on this, so a
root-only scan silently under-matches packs in any repo whose manifests live in sub-trees.

## Cross-cutting — top-level `blocked` conflates three very different outcomes

*Already delivered* (all `no-change`), *environment unavailable*, and *genuine failure* all surface
as top-level `status: "blocked"`; the human must read worker prose to tell "done" from "broken" from
"can't run here". The per-item `no-change` status already exists — the conflation is in the
top-level delivery rollup and the human-facing message, not the per-item payload.
