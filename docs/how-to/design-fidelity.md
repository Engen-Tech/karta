# Design fidelity: waiving a visual check, and refreshing a frozen design

Two karta rules stop a run when a design claim is not backed by a look at the design. This page is what you do when either one fires.

| You saw | Go to |
|-|-|
| `... names a design view ('rail') but has no visual oracle and no visual_check_waiver` | [When your binder is rejected](#when-your-binder-is-rejected) |
| `design capture does not match its pin in .karta/design-pins.json` | [When the comparison stops on a pin](#when-the-comparison-stops-on-a-pin) |
| Neither — you are pinning a capture for the first time | [Pin a capture](#pin-a-capture) |

The rules in one line each:

- A work item whose `design_reference` names a real view must either carry a `visual` oracle or a recorded `visual_check_waiver` naming the item that opens the design for it. Enforced by `skills/karta-plan/scripts/validate_binder.py`.
- A design capture committed in your repository must still match the fingerprint recorded for it in `.karta/design-pins.json`. Enforced by `skills/karta-validate/scripts/check_design_pins.py`.

Both scripts live inside the karta plugin. The paths above resolve from the karta repository root; in a consumer repo, resolve them from your installed plugin directory. You rarely run either by hand — karta runs the first at `karta-deliver` preflight and at `karta-build` Gate 1, and the second in `karta-validate`'s prerequisites phase.

For the field definitions themselves, read the `design_reference` row in any skill's [binder reference](../../skills/karta-plan/references/binder-reference.md), and the `visual_check_waiver` object in [binder-schema.json](../../skills/karta-plan/references/binder-schema.json), which is where its shape is defined. This page does not restate the field tables. It also does not teach visual comparison — [karta-validate](../../skills/karta-validate/SKILL.md) documents that.

## When your binder is rejected

The validator prints one line per offending item and exits 1:

```
INVALID:
  - design: item 'binder-map-rail' names a design view ('rail') but has no visual oracle and no visual_check_waiver
  - design: item 'next-action-hero-band' names a design view ('hero-band') but has no visual oracle and no visual_check_waiver
```

Reproduce it yourself with:

```sh
uv run skills/karta-plan/scripts/validate_binder.py --binder .karta/binders/<slug>.json
```

The item is claiming that a design view is what it renders, and nothing in the plan will ever open that view. You have three ways out, and all three are honest:

1. **Give the item a `visual` oracle.** It renders the view, so it compares against the view. Costs a browser run per item.
2. **Record a `visual_check_waiver`** naming another item in the same binder whose visual gate covers this one. Costs one browser run for the group.
3. **Set `design_reference` to `"none"`.** The item stops claiming the view. Free, and see the warning at the end of this section.

### Write a waiver

The waiver is a sibling field on the work item, next to `oracle` — not inside it. Both fields are required and both must be non-empty:

```json
{
  "id": "binder-map-rail",
  "design_reference": "rail",
  "oracle": {
    "type": "unit",
    "command": "uv run skills/karta-status/scripts/serve_status.py --self-test",
    "assertions": ["the rail renders one row per binder, ordered by phase"]
  },
  "visual_check_waiver": {
    "reason": "the rail's markup is checked here by self-test; the rendered comparison against the design happens once for the whole page at the closing fidelity gate",
    "covered_by": "design-fidelity-gate"
  }
}
```

`covered_by` must satisfy six conditions, each of which the validator reports as its own error:

1. It names a real work item **in the same binder**.
2. That item's oracle `type` is `visual`.
3. That item's own `design_reference` names a real view — not `"none"`, not absent. A covering item whose `design_reference` is `"none"` is one `karta-build` skips the visual gate for, so it opens no browser and covers nothing.
4. That item depends on the waived item, directly or through the dependency chain. A gate cannot cover work it does not run after.
5. That item's oracle carries a non-empty `assertions` list. A covering check has to say what it checks.
6. That item lists the waived item's `id` in its own `covers`. A waiver alone is one item volunteering another; the gate has to accept.

Separately from the six, every id in any item's `covers` must name a real work item in the binder. A stale or mistyped id is not a way past the rule — a waiver is always resolved against the item it actually points at — but it would sit in the plan reading as coverage nobody agreed to, so it is reported.

Condition 6 is why the two `design_reference` values are never compared with each other. One closing gate legitimately covers several differently-named views — a `binder-panel` gate over items naming `rail`, `header` and `typography` is the normal shape — so requiring the strings to match would reject valid plans. What makes coverage real is not a matching view name but a gate that named the items it accepts:

```json
{
  "id": "design-fidelity-gate",
  "design_reference": "binder-panel",
  "depends_on": ["binder-map-rail", "next-action-hero-band"],
  "covers": ["binder-map-rail", "next-action-hero-band"],
  "oracle": {
    "type": "visual",
    "assertions": ["the assembled page matches the design at 1440x900"]
  }
}
```

A binder with no `visual` item anywhere cannot waive anything. Add a check or drop the claim.

Two shapes of waiver are rejected as doing no work: one on an item whose `design_reference` is absent or `"none"`, and one on an item that already carries a `visual` oracle. Neither can produce an unchecked design claim, and an inert waiver would tell a reader a comparison was deferred when none was.

Every waiver prints. `validate_binder.py` states the waiver count on its `VALID` line, then one line per waiver with its reason and its `covered_by`, then one line per covering item saying how many waivers it absorbs. Read that summary before you approve the plan: a gate covering seven views reads as covering seven views.

### If the binder is already committed

A committed binder is immutable — `hooks/scripts/guard_binder_immutability.py` refuses the edit. If you upgrade karta and a binder that has not started is now rejected, you have two resolutions:

- **Add a visual item and re-plan** as a fresh binder with a new slug, so the waivers have something real to point at.
- **Set the offending items' `design_reference` to `"none"`** in the re-plan, and write down in the binder what the dropped claim cost — which views nobody will now compare.

The mid-delivery case is sharper. The rule fires at `karta-build` Gate 1, not only at `karta-deliver` preflight, so a run whose plugin updates between waves meets the rejection on a binder that is immutable while the run is live. Editing the live binder is not one of your options. The two answers are:

- **Finish the run on the plugin version it started under.** Pin the plugin, complete the remaining waves, land the delivery.
- **Abandon the run and re-plan.** Discard the integration branch and plan a fresh binder that complies.

### The cheap way out, named

Setting `design_reference` to `"none"` is sanctioned and stays sanctioned. It also costs one token, needs no reason, and is recorded nowhere — so a repository can reach compliance by making the field always say `"none"`. There is one detection, and it is a warning rather than a stop: when a binder names a design source in `design_facts` but carries no `visual` oracle on any item, the validator prints

```
  warning: binder names a design source in design_facts but no work item carries a visual oracle — confirm this project is legitimately all-backend work, not a design claim with nothing behind it
```

It prints on every run. A project that is genuinely all backend work under a design-bearing repository is fine; read the line and decide.

## When the comparison stops on a pin

`karta-validate` hashes the design capture before it serves it and compares that hash against `.karta/design-pins.json`. A mismatch is a hard stop:

```
docs/designs/karta-watch-1440x900-light.html: design capture does not match its pin in .karta/design-pins.json
  pinned sha256=4479d7406cfa80835dd72d052bb1aabedb40fef5440a2466ae378036315d20ae
  actual sha256=48afb97311237c980e1082cf663e95e767c2abd1973198140587d4d795c0722f
```

Run it yourself against any design path:

```sh
uv run skills/karta-validate/scripts/check_design_pins.py --design-path <path-to-design-html-or-dir>
```

The check reads. It never rewrites a capture, never rewrites the pin file, and never deletes anything.

Someone changed the capture's bytes. Decide which happened:

- **The capture was edited in place.** Restore it from git — `git checkout -- <path>` — and take the change upstream instead. Editing the frozen copy makes the copy the design.
- **The capture was deliberately re-taken from upstream.** Update the pin: copy the `actual sha256=` value the failure printed into the entry's `sha256`, refresh `captured_on`, and commit both the capture and the pin together.

### The other failures you can see

There are seven outcomes and six of them stop you by default. Two of those six stop you because the check verified nothing, not because it found something wrong, and that pair is what `--allow-unpinned` is for:

| Outcome | Result |
|-|-|
| Bytes match the pin | Pass, printing the capture date, the upstream address, and the recapture triggers |
| Bytes differ from the pin | **Fail** — the drift message above, with both hashes |
| Design is inside the repo, a pin file exists, this design has no entry | **Fail** — `... has no pin in .karta/design-pins.json (sha256=...)`. This repository pins its captures and this one escaped; add the entry |
| The entry's `recapture_after` date has passed | **Fail** — `... pin has expired: recapture_after 2026-12-01 has passed — recapture the design before trusting this comparison.` |
| No pin file at all | **Fail**, naming the design it did not verify and printing its `sha256`. With `--allow-unpinned`: pass with that as a notice |
| Design resolved from outside the repository | **Fail**, saying it cannot be pinned. With `--allow-unpinned`: pass with that as a notice |
| Malformed pin file — not a JSON object, or an entry missing `sha256` | **Fail** as malformed, never as a matching capture |

### `--allow-unpinned`

No pin file at all, and a design resolved from outside the repository, are the two outcomes where the check compared the capture against nothing. They exit non-zero, because a zero exit is read as "this capture was checked" by anything gating on it, and for those two that is false.

```sh
uv run skills/karta-validate/scripts/check_design_pins.py --design-path <path> --allow-unpinned
```

The flag turns exactly those two back into a notice and a pass. Nothing else moves: a drifted capture, an expired pin, a missing entry and a malformed pin file all still fail with the flag set. `karta-validate` passes it in its own prerequisites step, because pinning is opt-in and a repository that never pinned anything should not be stopped by a check it never asked for. Drop it when you have pinned your captures and want the unverifiable cases to stop you too — and write it at the call site, where a reader can see the choice, rather than leaving it to an exit code that never meant it.

## Pin a capture

Add one entry per committed design file, keyed by its repository-relative path:

```json
{
  "docs/designs/karta-watch-1440x900-light.html": {
    "sha256": "4479d7406cfa80835dd72d052bb1aabedb40fef5440a2466ae378036315d20ae",
    "source": "claude-design://59832c82-69dc-4841-9c45-c4a8a86730a3/Karta Watch.dc.html",
    "captured_on": "2026-08-17",
    "recapture_triggers": [
      "the living Claude Design export changes",
      "the vendored fonts or mascot under skills/karta-status/assets/ change"
    ],
    "recapture_after": "2027-02-17"
  }
}
```

| Key | Required | What it is for |
|-|-|-|
| `sha256` | yes | The fingerprint of the committed bytes. Get it by running the check against an unpinned design — the drift failure and the no-pin-file outcome both print the hash they computed |
| `source` | no | The upstream address the capture came from, printed on every pass so the person about to trust the comparison knows where a recapture is aimed |
| `captured_on` | no | The date the capture was taken, printed on every pass |
| `recapture_triggers` | no | The events you decided call for taking the capture again. Printed on every pass; nothing enforces them |
| `recapture_after` | no | An ISO date past which the capture must be taken again. Once that date passes, the check fails naming the date. Leave the key out — or set it to `null` — and the capture never expires. Any other non-date value (`0`, `""`, `false`) fails as a malformed date rather than passing as no deadline: those are a deadline written down wrong, not a way of saying there isn't one |

`recapture_after` is opt-in inside an opt-in. Omit it and nothing changes; add it and you have given this capture a stated shelf life that stops the comparison rather than being disclosed and ignored.

There is no write mode. Every outcome where the check computed a hash prints that hash, so writing the first pin and restoring a drifted one are both a copy and paste from the check's own output.

In the karta repository itself, `scripts/validate_plugin.py` checks the pin file's shape, and it is one of the commands run before a commit there — so a malformed pin file is caught at that floor rather than on someone's next `karta-validate` run. That script is not part of what a consumer installs. In your own repository, the malformed-pin-file outcome above is what reports it.

## Recapture: the step nothing will prompt you for

**No check compares a frozen capture against the living design upstream.** The pin check proves two things and says so: the capture still matches its recorded fingerprint, and the capture has not outlived the `recapture_after` date its author set. Neither of those looks upstream. The design lives behind agent-invoked tools and karta's gate scripts are stdlib with no network, so a pass never claims the capture still resembles the design it was taken from.

Your comparison is only as current as your last recapture. Recapture is a step a person or an agent performs:

1. Read the `source` and `recapture_triggers` the check printed on its last pass. Has any trigger happened?
2. Export the design again from that upstream address, into the same repository path.
3. Run the check. It will fail with the drift message and print the new `actual sha256=`.
4. Copy that hash into the entry's `sha256`, set `captured_on` to today, and revisit `recapture_after` and `recapture_triggers` while you are in the file.
5. Commit the new capture and the updated pin in the same change.

If step 3 passes instead of failing, the export is byte-identical to what you already had and there was nothing to recapture.

## Related

- [Binder field reference](../../skills/karta-plan/references/binder-reference.md) — the `design_reference` row and the rest of the work-item fields
- [Binder schema](../../skills/karta-plan/references/binder-schema.json) — the `visual_check_waiver` object's required fields and their descriptions
- [karta-validate](../../skills/karta-validate/SKILL.md) — the visual comparison itself, and where the pin check sits in its prerequisites phase
- [Design reference gate — decision record](../specs/2026-08-17-design-reference-gate.md) — why the rule is an error, why coverage stays inside one binder, and what was ruled out
