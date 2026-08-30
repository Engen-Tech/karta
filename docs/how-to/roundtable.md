# Roundtable edict (house-only)

> **Switched on.** `.karta/roundtable.json` carries `enabled: true`, restored 2026-08-23 once the
> multi-provider environment came back. Both enforced *review* gates fire: a binder commit and an
> integration-branch merge each need a fresh record of that exact content, committed with it. The
> landing gate in the same hook is a different thing and never read that switch — see
> [The landing gate](#the-landing-gate). The multi-lens panel in
> `scripts/review/binder_review_panel.js` still runs alongside, because it opens the source and runs
> the commands where an external panel reads only what you paste it. **A panel result is not a
> roundtable record and must never be filed as one:** every lens is the same model wearing a
> different hat, so it cannot meet `min_providers`. See "Review before commit" in
> [AGENTS.md](../../AGENTS.md).

karta's own binders and deliveries may not land without a recorded multi-model review. This is a house rule for the karta repo building itself; consumer repos never carry it. This guide is how you run the review and file its result.

The gate is deterministic: it checks that *a fresh recorded review of this exact content exists*, never what the panel concluded. The panel's opinion varies run to run, so it never blocks — skipping the review is what blocks. You read the findings and decide what to act on.

## Where it applies

Four points, split by whether a git event exists to gate on:

| Point | Git event | Treatment |
|-|-|-|
| Plan (binder) | commit staging `.karta/binders/<slug>.json` | enforced |
| Deliver (integration branch) | `git merge` of a `karta/*/integration` branch onto the default branch | enforced |
| Verify (a built diff) | none | helper-available (advisory) |
| Standalone (ad hoc) | none | helper-available (advisory) |

Plan-commit and deliver-merge are enforced — each has a real commit to block. Verify and standalone are advisory — no commit or stop moment to hang a gate on, so the helper is available but nothing is blocked. The merge gate is narrow: it fires only for a `git merge` naming a `karta/*/integration` branch while you are on the default branch.

The same hook carries a third gate that is **not** about review at all, and is not covered by the switch below or by the escape hatch: the landing gate. See [The landing gate](#the-landing-gate).

## The config file

Everything is governed by `.karta/roundtable.json`:

```json
{
  "enabled": true,
  "ledger": true,
  "tool": "roundtable-critique",
  "providers": [],
  "min_providers": 2,
  "focus": "",
  "points": { "plan_commit": true, "deliver_merge": true }
}
```

- `enabled: true` arms both review gates; `enabled: false`, or an absent file, turns every gate off. The switch is absolute, matching the doc-gardner and kaizen opt-in pattern. It is `true` in this repo today.
- `ledger: true` makes both gates require the round ledger as well as the record: `.karta/roundtable/<key>.rounds.json` must be in the content being committed (or in `HEAD`, for a merge), its last round must have reviewed exactly the bytes the record reviewed, and the record must be bound to that final round. Absent or `false`, nothing about the gates changes — a consumer repo that has not opted in is unaffected. It is `true` in this repo today. The gate reads the switch from `HEAD`'s copy of this file, so a same-commit flip to `false` cannot turn off the check for the commit that carries it.
- `tool` is the roundtable tool to run (default `roundtable-critique`).
- `providers: []` means the panel default.
- `min_providers` (default 2) is the floor that keeps "multi-model" honest: a panel with fewer than `min_providers` distinct providers is not a review, and the recorder refuses to file it.
- `points` turns either edict off on its own.

The shape is validated by `scripts/validate_plugin.py` — a malformed switch is caught at commit time, exactly as a malformed doc-gardner or kaizen switch is.

## When a provider comes back empty

A panel entry with `status: "ok"` and an empty `response` is a review that did not happen, and it counts toward nothing — `min_providers` is met by providers that answered. The known case is Antigravity.

Since agy 1.1.3, headless `--print` mode soft-denies every tool that would need a confirmation (a `read_file` outside the workspace, any shell command), exits 0, and writes `jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied` to stderr. Neither `trustedWorkspaces` nor `permissions.allow` in `~/.gemini/antigravity-cli/settings.json` reliably reaches print mode (google-antigravity/antigravity-cli#548). Across the `context-economy` and `review-ledger` binder reviews on 2026-08-29, that was 50 of 50 Antigravity rounds returning nothing.

The fix lives in roundtable, not here: since `fix/antigravity-headless-permissions` in `roundtable-src` (17f7c53), the Antigravity backend passes `--dangerously-skip-permissions` — the trust level the Claude and Copilot backends already run at — reports a soft-deny as `status: "error"` with the notice as the response, and sends absolute paths in the `=== FILES ===` block (agy runs shell commands from its own scratch directory, so a relative path sent it hunting with `find /`). If an Antigravity entry is empty again, check that the installed `~/.local/share/roundtable/roundtable` carries that change before adding providers to compensate.

## Recording a review

roundtable is an MCP tool the agent calls, not a CLI a script can invoke. So recording is three steps: run the panel, keep each round, then file the final one.

1. Run the configured roundtable tool on the target — the staged binder, or the integration-branch diff.
2. After **every** round, append it to the target's ledger — what the panel said, what you fixed, what you refuted:

   ```
   ... | python3 scripts/roundtable/run_review.py --round --target <slug> --kind binder --fixed "..." --refuted "..."
   ```

   The ledger is `.karta/roundtable/<slug>.rounds.json` (`branch-<tip-sha>.rounds.json` for a branch). Every round is kept, including below-floor ones; a round never writes a record.
3. On the final round — the one whose bytes you are about to commit — file the record:

   ```
   # a binder
   ... | python3 scripts/roundtable/run_review.py --record --target <slug> --kind binder
   # an integration branch
   ... | python3 scripts/roundtable/run_review.py --record --target karta/<slug>/integration --kind branch
   ```

   `--record` refuses to file a record the ledger's last round did not review, and binds the record to that round (`rounds_ledger`, `final_round`).

The recorder writes the record under `.karta/roundtable/` — `<slug>.json` for a binder, `branch-<tip-sha>.json` for a branch — and stages it with `git add`, as `--round` stages the ledger. `.karta/roundtable/context-economy.rounds.json` is the worked example: thirteen rounds on one binder, each with every provider's verdict or the reason it gave none.

The gate confirms the record with `run_review.py --check`. These rules make the record trustworthy:

- **Freshness keys on the bytes git will commit.** A binder record's freshness hash is the sha256 of the binder bytes the commit records — the staged blob for a plain commit, the working-tree file for `-a` or a pathspec that names it, `HEAD`'s copy for a pathspec that does not — decided by `git ls-files`, never by matching tokens. If you review one version of the binder and then stage a different one, the hash no longer matches and the gate re-arms — you must re-review what you are actually committing. A branch record keys on the integration tip sha, so any new commit on the branch invalidates it.
- **The record must be committed.** The recorder stages the record so it lands in the same commit. The gate reads the record from the same source it reads the binder from, so a record that a pathspec or `--only` leaves out of the commit does not count, and a record that differs between that source and the working tree is denied (`record source mismatch`) rather than checked against the wrong file. `.karta/roundtable/` is the committed audit trail and must never be gitignored.
- **With `ledger: true`, the rounds ride with the record.** Two more blocked cases, each named in its own message: **no round ledger** in the content being committed (`.karta/roundtable/<slug>.rounds.json` missing from the source git will commit, or `branch-<tip>.rounds.json` missing from `HEAD` for a merge), and a **stale round ledger** whose last round reviewed different bytes. A ledger that is malformed, a record that names a different ledger, or a record whose `final_round` is not the ledger's round count (a round appended after `--record`) blocks the same way. Each message says to append the round with `run_review.py --round` and rerun `--record`.
- **The gate recognises one command shape.** It parses `git commit …` and `git merge …` with a whitelist — the options it knows, pathspecs it can resolve from the repository root, quoted values — and denies anything it cannot reproduce: a preceding or trailing command segment, a command substitution, an unquoted `$`/glob/brace/tilde, a redirection, `git -C`/`--git-dir`/`--work-tree`, a `GIT_*=` prefix or environment variable (except the inert `GIT_EDITOR=true`, `GIT_EDITOR=:`, `GIT_PAGER=cat`, `GIT_TERMINAL_PROMPT=0`), combined short flags such as `-am`, `--patch`/`--interactive`/`--pathspec-from-file`, a commit issued from a subdirectory, and a gated commit without `-m`/`-F` (or `--amend --no-edit`) — git would open an editor after the hook. The cost is over-denial of unusual spellings, never under-denial; spell the commit out and it passes.

## Reading a ledger

A ledger is the review's history; the record is its receipt. Open one next to the other and the difference is plain. `.karta/roundtable/context-economy.rounds.json` is the worked example: thirteen rounds on one binder, from the first draft to the bytes that were committed.

A ledger has a short header and a list of rounds. Each round carries the keys you will meet everywhere in it:

| Key | What it tells you |
|-|-|
| `reviewed_hash` | the sha256 of the binder bytes that round looked at; a new draft is a new hash |
| `providers` | one entry per panelist: its verdict, or the status it gave instead of one |
| `findings_fixed` | what the operator changed because of that round |
| `findings_refuted_or_deferred` | what the operator pushed back on or left for later, and why |
| `below_floor` | whether the round had fewer answering providers than `min_providers` |

Read the context-economy ledger top to bottom and a story appears. Round 1 opened with both answering providers saying revise and four fixes, one of them a real bug in an additive-only guard. Rounds 2 through 12 are the binder being tightened one review at a time: each round has a different `reviewed_hash`, and each carries between one and five fixes. Round 13 has no fixes, two merge verdicts, and a `reviewed_hash` starting `6d68c0e8`. That is the hash the record beside it, `context-economy.json`, was filed against, which is how you know the record certifies the last thing the panel saw and not an earlier draft.

Now look at the third provider. Antigravity is listed in every one of the thirteen rounds, and in every one of them it returned nothing: `verdict` is null and `status` says why. The record alone would never show you that. A record holds one panel snapshot and freshness for the floor, so a provider that came back empty once looks the same as one that came back empty thirteen times in a row. Only the ledger keeps the empty rounds, and only the ledger makes a pattern like that visible.

Three things a ledger is not:

- **Not a substitute for the record.** The record stays the authority on freshness and on the `min_providers` floor. The ledger supports it: with `ledger: true` in `.karta/roundtable.json`, the gate also requires the ledger's final round to have reviewed the same committed content the record identifies. If someone appends a round after `--record`, or the last round reviewed different bytes, the gate says so and asks for a fresh `--record`.
- **Not a verdict anyone is held to.** A round of `revise` beside a round of `merge` is the record of an argument, and `findings_refuted_or_deferred` is where the operator's side of it lives. The gate never reads the verdicts.
- **Not the record's home.** They are two files, and `--record` writes `rounds_ledger` and `final_round` into the record so the pair can be checked against each other.

One shape difference for branches: a binder's ledger is one file per slug, and its rounds accumulate as the plan changes. A branch ledger is one ledger per tip sha, `branch-<tip>.rounds.json`, because any new commit on the branch is a new tip. The many-round history is a binder's; a branch reaches a tip and is reviewed there.

## Accepted bypasses

A PreToolUse hook sees a command before it runs. It can match command text and read current git state, but it cannot judge a post-condition like "will this make the integration tip an ancestor." So these paths are **not** gated, by design — the same class of deliberate escape as the hatch below:

- `git cherry-pick`
- `git rebase`
- `git reset --hard`
- `git merge --squash` followed by a separate `git commit`
- an `env -S '...'` string whose further quoting hides the integration ref from the text reader (an `env -S`/`--split-string` or `env -a`/`--argv0` segment that does show `git merge` and an integration ref is denied outright — the hook cannot read what env will run, so it fails closed)

The doctrine lists them plainly rather than pretending the gate is airtight. If you land integration content this way, run the review yourself — the gate will not remind you.

## The landing gate

Separate from everything above, and switched on regardless of `enabled`.

karta stops at the assembled integration branch — no PR, no push, no auto-merge — so landing it is a separate act, and **who decides a delivery ships is always the human**. The gate blocks a `git merge` naming a `karta/*/integration` ref while you are on the default branch:

```
KARTA_LANDING_APPROVED=1 git merge --no-ff --no-edit karta/<slug>/integration
```

The assignment has to prefix the merge itself; the same string elsewhere in the command line does not grant it. The merge carries `--no-edit` (or `-m`) because anything but `--ff-only` can open the configured editor between the gate and the merge commit, and the review gate denies a merge without one. `KARTA_SKIP_ROUNDTABLE` does **not** bypass this — that hatch means the review environment is down, which says nothing about who decides to ship.

If you are an agent reading this: do not set it. Report that the branch is assembled and what the floor said, then ask.

Two known limits, stated rather than implied. The gate matches command text, so it cannot tell an agent's merge from a human's — the rule against forging the variable is doctrine, not enforcement. And it shares the bypasses below: `git cherry-pick`, `git rebase`, and `git reset --hard` are not a `git merge`.

## Escape hatch

When the roundtable environment is down, or you need a deliberate partial commit, set `KARTA_SKIP_ROUNDTABLE=1` as a leading assignment prefix on the git command or in the environment, and the gate allows the command:

```
KARTA_SKIP_ROUNDTABLE=1 git commit -m "..."
```

The hook also fails open on any internal error — a broken hook never wedges the repo. Both are deliberate: the edict raises the floor without becoming a wall you cannot get around when the tooling is down.
