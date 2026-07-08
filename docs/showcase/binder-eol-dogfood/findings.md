# karta on karta — the binder end-of-life dogfood

**Date:** 2026-07-08. **Purpose:** run karta's own plan-and-deliver pipeline on the karta repo itself, and measure how close it comes to a feature we already shipped by hand. karta planned a binder that reconstructs the v1.17.0 **binder end-of-life** feature (merged as `d904e9f`), then built and delivered all seven work items through the real gates. Because the human-written result already exists in git, `d904e9f` is the answer key.

This is a self-host test, not a product change. Nothing here was merged into `main` beyond the binder file itself.

## Why a pre-feature clone

The feature already exists on `main`. Deliver it there and every build worker finds the code already present, so every check passes without any work being done — a no-op that proves nothing. So the run happens on an **isolated throwaway clone** in the scratchpad, with its history reset to `710c714` (the v1.16.0 merge — the commit right before the feature), the binder committed on top, and `origin` removed so `karta-deliver` bases its integration branch off that pre-feature state. Every branch, ref, and tag the run creates stays inside the clone; the real repo never sees them.

At the base, the feature is confirmed absent (no `load_archived_binders`), and the four scripts it touches carry their old self-test counts — the numbers each worker has to grow:

| script | base (v1.16.0) | target |
|-|-|-|
| `guard_binder_immutability.py` | 12/12 | 15/15 |
| `karta_next.py` | 11/11 | 14/14 |
| `validate_binder.py` | 28/28 | 30/30 |
| `serve_status.py` | 31/31 | 35/35 |

## The binder

`karta-plan` synthesized `binder-eol-dogfood` — 7 work items, no design surface, stack packs `minimalism` and `karta-house-skill-authoring` (both always-on; the repo ships no dependency manifest, so no stack-specific pack matched). The house skill-authoring pack is the right expert here: the whole feature is scripts with self-tests, skill doctrine, and three-way mirrors.

The dependency shape is three independent roots, each with one dependent:

| item | depends on | kind |
|-|-|-|
| `archive-immutability-guard` | — | hook script |
| `archive-aware-status-engine` | — | engine script |
| `archive-aware-validator` | — | validator script |
| `deliver-archival-step` | — | skill doctrine |
| `watch-page-delivered-history` | `archive-aware-status-engine` | engine script |
| `build-single-hatch-archival` | `deliver-archival-step` | skill doctrine |
| `plan-slug-freshness-doctrine` | `archive-aware-validator` | skill doctrine |

## How the run went

Delivery ran in three waves onto `karta/binder-eol-dogfood/integration`.

- **Wave 1** built `archive-immutability-guard` with the full ceremony run inline, to prove the pipeline before scaling out.
- **Waves 2 and 3** each ran three build workers in parallel. Each worker implemented its item in its own worktree branched off the moving integration tip, grew the self-test, regenerated the mirrors, and got the floor green — then stopped at a committed branch. The orchestrator ran the safety gate on each diff, then merged the items one at a time, re-validating each item's check against the tip that had just moved, and ran a post-wave check on the assembled result.

Every item cleared its gates:

| item | check result | safety scan |
|-|-|-|
| `archive-immutability-guard` | guard 15/15 | PASS |
| `archive-aware-status-engine` | karta_next 14/14 | PASS |
| `archive-aware-validator` | validate_binder 30/30 | PASS |
| `deliver-archival-step` | mirror in sync + plugin pass | PASS |
| `watch-page-delivered-history` | serve_status 35/35 | PASS |
| `build-single-hatch-archival` | mirror in sync + plugin pass | PASS |
| `plan-slug-freshness-doctrine` | mirror in sync + plugin pass | PASS |

Every self-test count matched the numbers the human commit reported. Every merge re-validated on the moving tip; every post-wave check passed; every secret scan was clean.

With all seven items done, the run was complete, so the end-of-life phase fired: `karta-deliver` archived the binder to `.karta/binders/archive/binder-eol-dogfood.json` — the feature using the very mechanic it had just built to retire its own binder.

## Finding 1 — karta reproduced the human's work closely

Comparing karta's assembled result against `d904e9f`, file by file:

| file | karta vs `d904e9f` |
|-|-|
| `validate_binder.py` | identical |
| `karta-deliver/SKILL.md` (the Phase 6c doctrine) | identical |
| `guard_binder_immutability.py` | 18 lines added, 19 removed — but the core regex is identical |
| `karta_next.py` | 20 added, 13 removed |
| `karta-plan/SKILL.md` | 10 added, 2 removed |
| `karta-build/SKILL.md` | 3 added, 2 removed |
| `hooks.md` | 2 added, 2 removed |
| `binder-reference.md` | 1 added, 1 removed |
| `serve_status.py` | 94 added, 53 removed |

Two files came out byte-for-byte identical to the human's. The heart of the guard change — the one regex that decides whether a path is a protected binder — is identical in both:

```
BINDER_RE = re.compile(r"(?:^|/)\.karta/binders/(?:archive/)?[^/]+\.json$")
```

Most other files differ by a handful of lines — docstring phrasing, which test cases were chosen, wording. The one sizable divergence is `serve_status.py`, where karta wrote a more elaborate join for the delivered rows; it still passed its 35/35 self-test. Across all files the behavior matched: the same self-test counts, the same gate verdicts.

## Finding 2 — a live gate caught a real mistake mid-run

The first attempt to dispatch the safety auditor was **blocked by karta's own `guard_auditor_dispatch.py` hook**. The hook is fail-closed: the binder pins stack packs, but the dispatch prompt did not carry their resolved review checklists, and the built-in packs live in the plugin rather than the worktree, so the auditor could not re-derive them. The dispatch was refused until the checklists were embedded. This is the enforcement working exactly as designed — one of karta's gates policing the way another gate gets called — and it fired against real work, not a fixture.

## Finding 3 — the feature validates itself

The last step, Phase 6c, archived the binder with `git mv` to `.karta/binders/archive/`. The status engine built earlier in the same run (`archive-aware-status-engine`) is precisely what reads that archive directory and treats a delivered binder as finished rather than pending. So the run ends with the feature correctly handling its own delivery record — the clearest possible end-to-end check.

## Limitations

Read the fidelity result with these in mind:

- **This was not a blind reconstruction.** The clone kept full history, so `d904e9f` stayed reachable, and at least two workers said they consulted it. That inflates the two byte-identical results. A cleaner test needs a history-truncated base that hides the answer key. This is the run's main methodological gap.
- **Waves 2 and 3 used a single safety vote per item** and dispositioned acceptance from the green self-test rather than a separate reviewer agent, to keep the cost proportionate. Wave 1 ran both gate agents in full.
- **Kaizen was a verified no-op.** The switch was on, but delivery-mode kaizen seeds exactly the binder's pinned packs, both of which were already present at the base, and phase-one kaizen only seeds. Doc-gardner was off.
- **Nothing was merged back.** The feature already exists on `main` as `d904e9f`, so the integration branch had nowhere to go. The clone and all its refs were torn down after the run.

## What it validates

On a real, multi-part feature with a known-good answer, karta planned a sound decomposition, built every item through its own gates, assembled them across three waves with no failed checks, and produced a result that matched the hand-written original — two files exactly, the rest behaviorally. The pipeline held end to end, and its enforcement hooks bit when they should. The honest asterisk is that the workers could see the answer; closing that gap is the next thing to try.

## Reproduce

The run used an isolated clone, not the live repo:

```
git clone --no-hardlinks <karta-repo> <scratch>/karta-dogfood
git -C <scratch>/karta-dogfood checkout -B main 710c714      # pre-feature base (v1.16.0)
# add the binder onto the base, remove origin, then deliver:
#   karta-deliver binder-eol-dogfood
```

For a stricter re-run, truncate the clone's history so `d904e9f` is unreachable before delivering, so the workers cannot consult the shipped feature.
