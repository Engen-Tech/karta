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

## Finding 4 — the blind re-run, and where it diverged

The first run had one real weakness: the clone kept full history, so the shipped feature commit `d904e9f` was still reachable, and at least two workers consulted it. That is why two files came out byte-identical — the workers could see the answer.

So the run was repeated on a **history-truncated base**. Instead of cloning, the v1.16.0 snapshot was exported (`git archive 710c714`) into a brand-new repository (`git init`), giving a two-commit history with no feature objects in it at all. `git cat-file -e d904e9f` fails in that repo — the answer is physically absent. Each worker was told to build only from its work item's contract and check, with no target counts and no implementation hints. All seven items were delivered again through the same gates.

The blind result is the honest one, and it diverges more:

| file | blind vs `d904e9f` | self-test: blind / human |
|-|-|-|
| `guard_binder_immutability.py` | +28 / -20 (regex still identical) | 18 / 15 |
| `karta_next.py` | +51 / -37 | 16 / 14 |
| `validate_binder.py` | +137 / -46 | 34 / 30 |
| `serve_status.py` | +98 / -61 | 40 / 35 |
| deliver / build / plan `SKILL.md`, docs | +2 to +16 each | — |

Three things stand out:

- **No file came out byte-identical this time.** Every file diverged in wording and structure, while staying behaviorally equivalent — the same self-test coverage, the same gate verdicts. The first run's two identical files were an artifact of history access, exactly as suspected.
- **The core regex is identical even blind.** With no answer to copy, the guard's one load-bearing pattern still came out character-for-character the same: `(?:^|/)\.karta/binders/(?:archive/)?[^/]+\.json$`. It is a genuinely convergent minimal solution.
- **The blind workers wrote more tests than the human** — 18, 16, 34, 40 self-test checks against the human's 15, 14, 30, 35. With nothing to anchor to, each worker covered more edge cases of its own.

The blind run also surfaced a real defect the first run hid. The shadow-warning message — shown when a live binder reuses a delivered slug — drifted between two independent items:

- the status engine wrote `live binder '<slug>' shadows an archived (delivered) binder of the same slug`,
- the validator wrote `reuses an archived (delivered) slug — the delivered history is shadowed; pick a fresh slug`.

The two items have no dependency edge between them, and they diverged. The item that *did* have an edge — the plan-slug doctrine, which must quote the validator — copied the validator's wording exactly. So consistency held along the declared dependency and drifted between the unlinked items. Nothing caught it: each safety scan reads one item's diff in isolation, and the whole-plugin check passes because both strings are valid on their own. This is the concrete limit of per-item gating in a parallel build, and the most useful thing the exercise turned up. The house pack asks for one wording everywhere a term appears; enforcing that needs a check with cross-item scope, which karta does not yet have.

## Limitations

Read the fidelity result with these in mind:

- **The first run was not blind** — closed by the blind re-run above. Its two byte-identical files came from history access, not from independent convergence; treat the blind numbers as the honest ones.
- **Waves 2 and 3 used a single safety vote per item** and dispositioned acceptance from the green self-test rather than a separate reviewer agent, to keep the cost proportionate. Wave 1 ran both gate agents in full.
- **Kaizen was a verified no-op.** The switch was on, but delivery-mode kaizen seeds exactly the binder's pinned packs, both of which were already present at the base, and phase-one kaizen only seeds. Doc-gardner was off.
- **Nothing was merged back.** The feature already exists on `main` as `d904e9f`, so the integration branch had nowhere to go. The clone and all its refs were torn down after the run.

## What it validates

On a real, multi-part feature with a known-good answer, karta planned a sound decomposition, built every item through its own gates, and assembled them across waves with no failed checks. Blind — with the answer removed from the repo entirely — it produced a result that was behaviorally equivalent to the hand-written original: the same checks, the same coverage, one load-bearing regex identical to the character. The pipeline held end to end, and its enforcement hooks bit when they should. The blind run also found the honest edge: per-item gates keep each item correct but do not enforce wording consistency between items that share no dependency — a gap worth a cross-item check.

## Reproduce

The run used an isolated clone, not the live repo:

```
git clone --no-hardlinks <karta-repo> <scratch>/karta-dogfood
git -C <scratch>/karta-dogfood checkout -B main 710c714      # pre-feature base (v1.16.0)
# add the binder onto the base, remove origin, then deliver:
#   karta-deliver binder-eol-dogfood
```

For the blind re-run (Finding 4), seed a fresh repository from the snapshot instead of cloning, so the feature commit is absent from the object store entirely:

```
mkdir blind && git -C <karta-repo> archive 710c714 | tar -x -C blind
git -C blind init && git -C blind add -A && git -C blind commit -m "v1.16.0 snapshot"
# add the binder, commit, then deliver. Verify the answer is gone:
git -C blind cat-file -e d904e9f    # must fail — d904e9f is not in this repo
```

Compare the delivered result against `d904e9f` from your own full checkout, not from the blind repo.
