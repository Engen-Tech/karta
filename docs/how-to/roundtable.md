# Roundtable edict (house-only)

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

## The config file

Everything is governed by `.karta/roundtable.json`:

```json
{
  "enabled": true,
  "tool": "roundtable-critique",
  "providers": [],
  "min_providers": 2,
  "focus": "",
  "points": { "plan_commit": true, "deliver_merge": true }
}
```

- `enabled: false`, or an absent file, turns every gate off. The switch is absolute, matching the doc-gardner and kaizen opt-in pattern.
- `tool` is the roundtable tool to run (default `roundtable-critique`).
- `providers: []` means the panel default.
- `min_providers` (default 2) is the floor that keeps "multi-model" honest: a panel with fewer than `min_providers` distinct providers is not a review, and the recorder refuses to file it.
- `points` turns either edict off on its own.

The shape is validated by `scripts/validate_plugin.py` — a malformed switch is caught at commit time, exactly as a malformed doc-gardner or kaizen switch is.

## Recording a review

roundtable is an MCP tool the agent calls, not a CLI a script can invoke. So recording is two steps: run the panel, then file the result.

1. Run the configured roundtable tool on the target — the staged binder, or the integration-branch diff.
2. Pipe the panel result to the recorder:

   ```
   # a binder
   ... | python3 scripts/roundtable/run_review.py --record --target <slug> --kind binder
   # an integration branch
   ... | python3 scripts/roundtable/run_review.py --record --target karta/<slug>/integration --kind branch
   ```

The recorder writes the record under `.karta/roundtable/` — `<slug>.json` for a binder, `branch-<tip-sha>.json` for a branch — and stages it with `git add`.

The gate confirms the record with `run_review.py --check`. Two rules make the record trustworthy:

- **Staged-blob freshness.** A binder record's freshness hash is the sha256 of the *staged* binder bytes (`git show :<path>`), not the working-tree file. If you review one version of the binder and then stage a different one, the hash no longer matches and the gate re-arms — you must re-review what you are actually committing. A branch record keys on the integration tip sha, so any new commit on the branch invalidates it.
- **The record must be committed.** The recorder stages the record so it lands in the same commit. The gate requires it to be staged, or already in `HEAD`; a record that lives only in the working tree does not count. `.karta/roundtable/` is the committed audit trail and must never be gitignored.

## Accepted bypasses

A PreToolUse hook sees a command before it runs. It can match command text and read current git state, but it cannot judge a post-condition like "will this make the integration tip an ancestor." So these paths are **not** gated, by design — the same class of deliberate escape as the hatch below:

- `git cherry-pick`
- `git rebase`
- `git reset --hard`
- `git merge --squash` followed by a separate `git commit`

The doctrine lists them plainly rather than pretending the gate is airtight. If you land integration content this way, run the review yourself — the gate will not remind you.

## Escape hatch

When the roundtable environment is down, or you need a deliberate partial commit, set `KARTA_SKIP_ROUNDTABLE=1` in the command text or the environment, and the gate allows the command:

```
KARTA_SKIP_ROUNDTABLE=1 git commit -m "..."
```

The hook also fails open on any internal error — a broken hook never wedges the repo. Both are deliberate: the edict raises the floor without becoming a wall you cannot get around when the tooling is down.
