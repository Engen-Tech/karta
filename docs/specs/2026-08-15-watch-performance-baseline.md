# Karta Watch performance baseline

Measured 2026-08-15, before the `watch-derivation-cost` work. This records what the
status server actually costs, so the redesign and the batching work argue from numbers
rather than intuition.

## Why the measurement runs in a container

The development host is fast enough to hide the problem: a `git` fork/exec there is
cheap, so per-item git calls look free. The page is meant to run on lean hardware, so
the baseline is taken on a constrained Incus container instead.

```
incus launch images:debian/13 karta-lean -c limits.cpu=1 -c limits.memory=512MiB
incus exec karta-lean -- apt-get install -y git python3
```

1 vCPU, 512 MiB, Debian 13, Python 3.13.5, git 2.47.3. Every number below is best-of-5
on that container. The container is kept between runs so before-and-after numbers are
comparable; treat it as the reference environment for any watch performance claim.

Two repo shapes are used, and the difference between them matters:

- **Real history** — a full clone of karta itself (385 commits, 148 refs), with synthetic
  binder refs layered on real commits. Merged items point at commits on the default
  branch; unmerged items point at commits on genuine side branches. This is the honest
  measurement and the one to quote.
- **Fixture** — one-commit repos, N binders x 10 work items. Cheap to build, but it makes
  any commit-graph walk free and therefore understates the cost. Retained only to show
  how far a fixture-only benchmark misleads.

Both carry the ref shapes a real run leaves: `refs/karta/<slug>/item-<id>/done|built`,
per-item branches, and an integration branch.

## Where the time goes

| binders | items | git derive | JSON serialize | HTML render (current page) | HTML render (redesign volume) |
|-|-|-|-|-|-|
| 1 | 10 | 12.2 ms | 0.02 ms | 0.1 ms | 0.0 ms |
| 5 | 50 | 53.9 ms | 0.07 ms | 0.1 ms | 0.0 ms |
| 10 | 100 | 108.4 ms | 0.14 ms | 0.2 ms | 0.1 ms |
| 20 | 200 | 214.0 ms | 0.29 ms | 0.4 ms | 0.1 ms |

Deriving state from git is roughly **2000x** more expensive than rendering the page from
it. Python renders a 174 KB page — the redesign's markup volume — in 0.1 ms.

Two conclusions follow, and both are load-bearing for the redesign:

- **The renderer is not a bottleneck and does not need replacing.** Porting the server to
  a compiled language would optimise 0.4 ms out of a 214 ms request, in exchange for
  shipping platform binaries from a plugin that is currently one `uv run --script` file.
- **The rendering strategy barely moves server cost.** A JSON feed with client-side
  updates and a server-rendered HTML feed cost within a rounding error of each other,
  because both are dominated by derivation. That choice should be made on client-state
  and dependency grounds, not on server speed.

The redesign figure comes from a synthetic renderer emitting the new design's markup
volume, not the final renderer. The conclusion survives being wrong by 10x.

## The cost is per item, and it is the whole problem

`gather_git_facts()` (`skills/karta-status/scripts/karta_next.py`) spawns git per binder
and per work item: one `for-each-ref` and one `rev-parse` per binder, one `rev-parse` per
item, and one `merge-base --is-ancestor` per done item — `1 + 2B + I + done` processes
for every request. `current_state()` in `serve_status.py` is documented "Never cached",
so single-repo mode pays that on every poll, every 2.6 s, for every open browser tab.
Hub mode multiplies it by the number of watched repos.

A batched form needs three calls, whatever the scale: one `for-each-ref refs/karta/`,
one `for-each-ref refs/heads/karta/`, and one `for-each-ref --merged=<default>
refs/karta/`.

### Measure against real history, not a fixture repo

**This is the part a naive benchmark gets wrong.** A synthetic one-commit repo makes
`--merged` free, because there is no commit graph to walk. Run the same comparison
against karta's own history — 385 commits, 148 refs — with unmerged `done` refs sitting
on genuine side branches, and the true cost appears:

| binders | items | current ms | current git calls | batched ms | batched git calls | speedup |
|-|-|-|-|-|-|-|
| 1 | 10 | 20.9 | 19 | 8.9 | 3 | 2.3x |
| 5 | 50 | 114.3 | 95 | 10.7 | 3 | 10.6x |
| 10 | 100 | 253.8 | 190 | 13.3 | 3 | 19.1x |
| 20 | 200 | 613.2 | 380 | 21.9 | 3 | 28.0x |
| 40 | 400 | 1550.8 | 760 | 37.2 | 3 | 41.7x |

At 40 binders the current code spends **1.55 s of every 2.6 s poll** deriving state —
59.6% of a core, continuously, for one open tab. The one-commit fixture reported 314 ms
at 20 binders where real history costs 613 ms, so a fixture-only benchmark understates
the problem by roughly half.

The batched form pays a floor of about 9 ms for the revision walk `--merged` performs,
which the fixture hides entirely — but it grows sub-linearly, 8.9 ms to 37.2 ms across a
fortyfold increase in work items, against 20.9 ms to 1550.8 ms for the current form.

For reference, the same comparison on one-commit fixture repos, which is what a benchmark
that does not clone real history would report:

| binders | items | current ms | current git calls | batched ms | batched git calls | speedup |
|-|-|-|-|-|-|-|
| 1 | 10 | 15.1 | 17 | 2.9 | 3 | 5.1x |
| 5 | 50 | 78.9 | 85 | 4.0 | 3 | 19.5x |
| 10 | 100 | 156.9 | 170 | 5.3 | 3 | 29.6x |
| 20 | 200 | 314.5 | 340 | 7.8 | 3 | 40.4x |

The call count is the invariant worth enforcing. Wall-clock depends on the machine and on
history depth, so a regression check should assert that git calls stay constant as binder
count grows, not that a timing threshold holds. The wall-clock numbers belong in a
reported benchmark run against real history, not in a gate.

## The batched form returns the same answer

Equivalence was checked directly rather than assumed, across six ref topologies —
including a repo with `done` refs pointing at commits deliberately **not** merged into
the default branch, which is the case `--merged` has to get right:

```
[PASS] empty            binders= 0 items/b= 0 done=  0 done_in_default=  0
[PASS] single           binders= 1 items/b= 1 done=  1 done_in_default=  1
[PASS] typical          binders= 5 items/b=10 done= 35 done_in_default= 20
[PASS] wide             binders=20 items/b=10 done=140 done_in_default= 80
[PASS] no-integration   binders= 3 items/b= 5 done= 12 done_in_default=  6
[PASS] with-integration binders= 3 items/b= 5 done= 12 done_in_default=  6
```

Both forms produced identical `done`, `done_in_default`, `built`, `failed`, `branch`,
and `integration_exists` facts in every case.

Equivalence was then re-confirmed on karta's real 385-commit history at 1, 5, 10, 20 and
40 binders, with unmerged `done` refs on side branches off historical commits — the case
that actually exercises `--merged`'s revision walk. Identical at every scale.

## The shipped code path, on a real checkout

The figures above compare derivation algorithms. This one runs the actual shipped
`serve_status.current_state()` against a real karta checkout inside the container —
385 commits, 148 refs, 17 archived binders — with live binders layered on real commits.
It confirms the algorithm comparison to within 0.3 ms at 20 binders.

| live binders | items | `current_state()` | `state.json` | full page render | full page |
|-|-|-|-|-|-|
| 0 | 0 | 3.4 ms | 98.5 KB | 0.5 ms | 133.4 KB |
| 1 | 10 | 24.2 ms | 100.4 KB | 0.5 ms | 135.3 KB |
| 5 | 50 | 116.4 ms | 108.0 KB | 0.6 ms | 142.8 KB |
| 10 | 100 | 248.9 ms | 117.5 KB | 0.6 ms | 152.3 KB |
| 20 | 200 | 613.5 ms | 136.4 KB | 0.8 ms | 171.2 KB |

Rendering the full page stays between 0.5 and 0.8 ms at every scale, which re-confirms
that the renderer is not worth optimising.

## The feed re-sends immutable history forever

`state.json` is **98.5 KB with zero live binders**. karta's 17 archived binders are
serialized in full on every poll, and archived binders never change — they are delivered
work. At 20 live binders roughly 72% of the payload is immutable history being re-sent,
and the same bytes are inlined into the initial page, which is why the page is 133 KB
before any live work exists.

This is a separate problem from derivation cost and wants a separate fix. The constraint
on any fix is that the Delivered phase must keep rendering every archived binder exactly
as it does today (`_append_archived`, `serve_status.py`).

## Refresh interval

The page polls every 2.6 s today. A 30 s interval has been accepted, with the refresh
model made explicit in the UI: a countdown to the next refresh, a manual refresh control,
and the ability to turn automatic refresh off entirely — which must genuinely stop the
requests rather than only hiding the countdown.

Duty cycle for one open tab, derivation only:

| live binders | at 2.6 s | at 30 s | at 30 s, batched |
|-|-|-|-|
| 5 | 4.5% of a core | 0.4% | 0.04% |
| 10 | 9.6% | 0.8% | 0.04% |
| 20 | 23.6% | 2.0% | 0.07% |
| 40 | 59.6% | 5.2% | 0.12% |

The longer interval does most of the work on background CPU. Batching still matters, for
a different reason: once a manual refresh button exists, derivation latency stops being
background cost and becomes something the user waits for. 613 ms feels sluggish; 22 ms
feels immediate.

## What the redesign needs from git

Nothing new. A separate gap analysis against the redesign found that every git-derived
value the new page shows is already gathered by `gather_git_facts()` or is pure string
formatting over it. The redesign's unmet needs are binder fields that `_enrich()`
currently drops — `contract`, `touches`, `estimate`, `serialize`/`shared_resources`, the
full `oracle.assertions` array, the opt-out `reason`, and the binder-level `sme` — all
pass-through additions that cost no git calls.

---

# After the work

Measured 2026-08-16, after the `watch-derivation-cost` binder landed. Same reference
environment as everything above — the `karta-lean` container, 1 vCPU / 512 MiB, Debian 13,
Python 3.13.5, git 2.47.3, best of five — against a real karta checkout at `/root/karta-rw`
(385 commits, 17 archived binders) with live binders layered on real commits. None of the
before figures above were touched; everything below is new.

## Derivation, batched, cache out of the picture

`gather_git_facts()` now answers every binder and every item with three whole-namespace ref
queries instead of a loop. These figures are the shipped `current_state()` with the state
cache not yet in play, so they compare like for like against "The shipped code path, on a
real checkout" above.

| live binders | items | before | batched, no cache | speedup |
|-|-|-|-|-|
| 0 | 0 | 3.4 ms | 6.4 ms | 0.5x |
| 1 | 10 | 24.2 ms | 12.4 ms | 2.0x |
| 5 | 50 | 116.4 ms | 14.5 ms | 8.0x |
| 10 | 100 | 248.9 ms | 17.6 ms | 14.1x |
| 20 | 200 | 613.5 ms | 21.7 ms | 28.3x |

**The empty case got slower, deliberately.** 3.4 ms to 6.4 ms with zero live binders: the
batched form always issues its three ref queries, where the old per-item loop issued none
when there was nothing to loop over. Short-circuiting to win those 3 ms would break the one
property worth enforcing — that git calls stay constant as binder count grows — so it was
not done.

## The cache has a cold path and a warm path, and they are not the same number

`current_state()` is cached now, keyed on a cheap fingerprint of the refs and binder files.
A timing loop that calls it five times in a row primes the cache on the first call and then
measures four cache hits, which reports only the warm path and overstates the win. So the
two paths are measured and reported separately:

| live binders | items | cold — a change detected | warm — nothing changed | key computation alone |
|-|-|-|-|-|
| 0 | 0 | 14.1 ms | 2.89 ms | 2.81 ms |
| 1 | 10 | 23.3 ms | 3.48 ms | 3.37 ms |
| 5 | 50 | 31.5 ms | 6.58 ms | 6.43 ms |
| 10 | 100 | 38.6 ms | 8.83 ms | 8.56 ms |
| 20 | 200 | 52.5 ms | 14.45 ms | 14.63 ms |

Two findings that belong here rather than being left out:

- **The warm hit is 97 to 101% key computation.** The cache lookup itself is free; computing
  the fingerprint is the whole remaining cost. Any further work on the steady-state poll has
  to attack the key, not the lookup.
- **A request that does find a change costs about 2.4x the uncached batched form** at 20
  binders — 52.5 ms against 21.7 ms — because the cold path pays the key twice, sampling it
  before and after the derivation, plus the derivation itself. Steady state is the common
  case, so the trade is the right one, but the cache is not free and this spec does not
  claim it is.

## The committed benchmark

`benchmarks/perf/derivation_bench.py` re-proves the win on any machine. It times the shipped
batched derivation against its own duplicate of the pre-batch per-item walker, at 1 / 5 / 10
/ 20 / 40 binders x 10 work items, best of five, and prints a table plus a machine-readable
JSON block.

The run recorded in `benchmarks/perf/results/2026-08-16-derivation-bench.json`:

| binders | items | reference ms | reference git calls | batched ms | batched git calls | speedup |
|-|-|-|-|-|-|-|
| 1 | 10 | 38.8 | 20 | 10.2 | 5 | 3.8x |
| 5 | 50 | 190.2 | 88 | 12.2 | 5 | 15.6x |
| 10 | 100 | 372.3 | 173 | 13.8 | 5 | 26.9x |
| 20 | 200 | 729.7 | 342 | 15.5 | 5 | 47.0x |
| 40 | 400 | 1522.1 | 682 | 42.1 | 5 | 36.2x |

The harness builds its own synthetic binder topology, so neither its milliseconds nor its
reference call counts are the same measurement as the hand-run before-table further up. The
mix of merged and unmerged done refs differs, and the per-item walker spends one
`merge-base` per done ref, so a different mix means a different call count — 20 / 88 / 173 /
342 / 682 here against 19 / 95 / 190 / 380 / 760 there. The commit count differs for a
duller reason: the harness reports `rev-list --count --all`, which is 400 across every ref
in that checkout, where the 385 quoted above counts the default branch. The shape is what
reproduces: linear growth on the left, near flat on the right.

**Five git calls per request, not three.** Three are the ref queries; the other two are
default-branch resolution, which the harness counts because it counts at the whole-request
boundary rather than inside one helper. The number itself is not the invariant and is not
asserted anywhere — what is enforced, by `karta_next.py --self-test`, is that git calls stay
constant as binder count grows, meaning identical at every binder count whatever that
identical figure turns out to be.

The JSON block carries the spread across runs as well as the best time, because best-of-five
alone lets a noisy machine look clean. This run makes the point: the reference walker at 40
binders spread 1228 ms across its five samples on a contended single core, while every
batched sample at that scale landed within 4 ms of the others.

### Running it

On a normal machine, against the current checkout:

```
uv run --script benchmarks/perf/derivation_bench.py
```

On the reference container, which is how the table above was produced — the harness and the
shipped `karta_next.py` staged into `/root/karta-bench`, measuring the real checkout at
`/root/karta-rw`:

```
incus exec karta-lean -- python3 /root/karta-bench/benchmarks/perf/derivation_bench.py \
  --repo /root/karta-rw --out /root/derivation-bench.json
```

This container invocation is documented, not gated. A consumer machine has no Incus, and
wall-clock depends on the machine and on history depth, so the enforced check stays the
call-count one.

### It never writes to the repository it measures

The harness creates git refs, so it creates them somewhere else. It resolves the real git
directory with `git rev-parse --git-common-dir` — a linked worktree's `.git` is a file
pointing elsewhere, and copying that file would yield no refs and no objects — copies that
directory to a temporary location, and builds every synthetic ref in the copy. Every git
invocation against the copy points `core.hooksPath` at an empty directory, because
`.git/hooks/` is copied along with everything else and a `reference-transaction` hook fires
on ref creation, so an un-neutralised hook could write straight back into the source. Every
invocation also runs with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and `GIT_COMMON_DIR`
cleared out of the inherited environment, since any of them would redirect resolution at a
directory the harness never intended to copy. The temporary copy is removed on exit,
including after a failure. And every function that creates or deletes a ref refuses to run
unless `GIT_DIR` points inside a copy the harness made, so the confinement holds even if a
later caller forgets where it is standing.

Two refusals rather than a wrong number:

- **A repository with one commit.** With one commit there is no graph to walk, so
  `for-each-ref --merged` is free and the measurement understates the true cost by roughly
  half — the fixture trap documented above. The harness names the reason and stops.
- **A repository with a non-empty `objects/info/alternates`.** A copy leaves the alternate
  object store behind, so it walks a partial object graph and reports numbers lower than the
  truth. Same treatment: name it, stop.

`--self-test` covers all of this, including that a source repository's full ref listing is
byte-identical after a run and after a run interrupted partway through.

# What was kept, and what was reverted

Added 2026-08-16, after the delivery's multi-model review panel. **The derived-state cache
measured in "The cache has a cold path and a warm path" was removed. The batching and the
ETag stayed.** Every measurement above is left exactly as it was recorded — they are true
readings of the code as it stood when the stopwatch ran, and the numbers are what argued the
revert.

Why the cache went:

- **The warm hit it buys is about 7 ms, and 97 to 101% of that hit is computing the key.**
  The table above says so in its own third column. The cache was paying almost its whole
  steady-state cost to answer "has anything changed".
- **A miss costs about 2.4x the uncached batched form** — 52.5 ms against 21.7 ms at 20
  binders. Break-even is roughly 4.4 hits per miss, and this page exists to watch a delivery
  *in flight*, which is exactly the regime where refs move on most polls.
- **In hub mode it never hit at all.** The hub derives each repo in a child process
  (`--print-state`) that exits before anything module-level could be reused, so the cache
  was dead weight on the mode most likely to be left running.
- **The key was a hand-maintained proxy for the derivation's inputs**, and no test can prove
  such a list complete — an input nobody thought of is served stale and silently.

What stands unchanged:

- **The batching.** `gather_git_facts()` still answers every binder and item with three
  whole-namespace ref queries. The "Derivation, batched, cache out of the picture" table is
  the shipped path — it was measured with the cache out of the way, so it needs no re-run —
  and so is every figure under "The committed benchmark".
- **The ETag and the conditional poll.** An unchanged poll still costs a 304 with no body.
  A 304 is safe by construction here: the tag is taken over the bytes just derived, not over
  a proxy for them. The tag is now the sha256 of exactly the body served, which is the one
  correction the panel's ETag findings called for.

So the shipped steady-state poll is the "batched, no cache" column — 21.7 ms at 20 binders,
against 613.5 ms before the work — with the download skipped whenever nothing moved.
