# Splitting serve_status.py — investigation, and why it was shelved

**Status: shelved 2026-08-16.** Not blocked by a missing prerequisite — shelved on a
judgement that the plan cost more to make safe than the monolith costs to live with.
Everything needed to restart is here. Do not re-run the investigation; read
[`investigation.json`](investigation.json) instead.

## What was proposed

`skills/karta-status/scripts/serve_status.py` is 5,504 lines, of which 2,523 (46%) are
its own self-test suite. The proposal was six items splitting it into flat sibling
modules: a no-op step widening the hub identity digest, then the six test suites out,
then the store module, then the page module, then a prose sweep.

## Why it was shelved

Three review rounds, two independent providers each. Every round found real defects,
and **every round found defects introduced by the previous round's fixes** — 17 in
rounds 1 and 2, nine more in round 3. The binder reached 77 KB and 101 oracle
assertions for a refactor that ships no user-visible change and makes the repo's total
line count go *up*.

The cause is specific and worth stating, because it is a property of this file rather
than of the planning: **the self-test suite scans its own source.** Two read sites feed
three checks that assert forbidden strings appear nowhere in the script — a `--bind`
flag, a pidfile, a retired vocabulary word — and every one of those literals is
assembled at runtime (`"--" + "bind"`, `"QUE" + "UED"`) so the check cannot match
itself. Add a check to a system that inspects its own text and you get a second-order
effect nearly every time. Two examples that survived a full round each:

- The required companion negative cases would have **tripped the very scans they test**,
  because each companion must materialize a forbidden literal and each lives in a file
  that is a member of the scanned set.
- The manifest artifact would contain the retired vocabulary word, because the check
  policing that word renders it into its own check name — so the manifest can never
  join the scanned set.

Two round-3 findings were fatal to the plan's own safety machinery, both empirically
demonstrated rather than argued:

- **`git diff -M --find-copies-harder` cannot certify the relocation.** A reviewer
  carved a byte-perfect 2,522-line split and ran it: five plain new files, zero copies.
  Git scores a copy as `copied / max(src, dst)`, so 505 lines out of 5,504 scores 8–11%,
  far under the 50% default. The assertion the binder called "the only thing standing
  between this item and silent coverage loss" detects nothing.
- **The manifest bootstrap was logically impossible.** The baseline file does not exist
  at the binder base, so its creating commit shows every line as an addition. A complete
  manifest violated "the diff contains exactly the N new names"; an N-line manifest
  violated "the suite's set equals the manifest".

## Facts about this repo that were established and are worth keeping

These were verified against the tree — several by scratch-copy experiment — and are true
independent of the split.

| Fact | Consequence |
|-|-|
| `validate_plugin.py:447` globs `skills/*/scripts/*.py` **non-recursively** | Every flat sibling must expose `--self-test`; anything one directory deeper is exempt from the floor. Verified: moving a file into `scripts/watch/` flipped a hard FAIL to PASS with no other change. |
| `_script_digest()` hashes only `_SCRIPT_PATH`'s bytes, and a running hub compares it to retire itself on plugin update (read externally at `hooks/scripts/inject_karta_status.py:500`) | Move code to a sibling without widening the digest and a stale hub silently serves old code across an upgrade. The self-exit mtime baseline has the same shape. |
| "Newest mtime across the files" is **not** a change detector | A change to a non-newest member leaves the maximum unmoved. An ordered `(name, mtime_ns)` snapshot is required, with deletion treated as change. |
| Hashing concatenated bytes without framing is forgeable | Moving a line between two files preserves the digest. Frame each member's basename and byte length. |
| `globals()["_script_digest"] = …` at `:3476` is the file's only `globals()` assignment | In a separate module `globals()` is that module's namespace, so the "identity is a startup snapshot" check would pass vacuously. Any split must convert it to `setattr` on the imported module **plus** a positive assertion the tamper took effect. |
| A true symbol cycle exists between hub bootstrap and daemon lifecycle | `_run_hub` calls `lost_bind_race` / `_probe_hub` / `_self_exit_watch`, which call back to `_hub_port`. Carving hub/http/lifecycle apart is not a clean cut. |
| Exactly 13 names form the external ABI | `ensure_state_dir`, `_hub_port`, `get_token`, `_probe_hub`, `ENSURE_FAILURE_FILENAME`, `_SCRIPT_PATH`, `PORT_BASE`, `PORT_SPAN`, `upsert_repo`, `record_port`, `_record_ensure_failure`, `_script_digest`, `_STATE_META` — reached by `karta_next.py`, `inject_karta_status.py`, and `benchmarks/probes/dark-status-surface-probes.py`. `import *` would drop 6 of the 13. |
| Sibling imports are safe on every invocation path | `sys.path.insert(0, Path(__file__).resolve().parent)` at `:95-97` makes them cwd-independent under `uv run --script`, both projections, the hub's bare-interpreter children, and the `spec_from_file_location` probe. |
| The self-test isolates the real store only in the driver | `KARTA_WATCH_STATE_DIR` is redirected once for the whole run and checked by `no self-test touched the real per-user state dir`. Any new standalone entry point must carry its own redirect, or a pre-commit run writes the developer's `~/.local/state/karta`. |
| Six MIRROR markers exist, in three pairs | `join_archived`/`joinArchived`, `poll_decision`/`pollDecision`, `_feed_transition`/`feedTransition`. Only the first two are compared by checks; the feed pair is comment-only. |

## If someone restarts this

The narrowest version that captures most of the value: **extract only the six self-test
suites and leave every line of production code in `serve_status.py`.** That removes the
digest widening, the scan rerouting, the no-op proof, the manifest bootstrap and the
relocation proof — because the source scans police *shipped behaviour*, which never
leaves the file. It delivers the 46% reduction and stops the tests living inside their
own subject, and it costs the `watch_page.py` extraction that the redesign work would
have benefited from.

Whatever the shape, keep these: the `globals()` → `setattr` conversion with a
took-effect assertion; aliasing the running module into `sys.modules` before importing
any suite (otherwise each suite gets a second module object and the tamper patches the
wrong one); per-module store isolation; and a check-name manifest, whose enforcement
must not be satisfiable by regenerating it from the tree being built.

## Cost of shelving

`.karta/binders/watch-redesign.json` stays valid exactly as planned — its 13 items list
`serve_status.py` in `touches` and one cites a line range, all of which would have needed
replanning. Deliveries continue to serialize on the single filename, as
`watch-derivation-cost` did across seven waves.
