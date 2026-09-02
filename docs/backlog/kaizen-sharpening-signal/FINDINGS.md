# Why kaizen tried to rewrite `hmin.1`, and what the guard actually checks

Filed 2026-09-02, after the `invariant-foundations` delivery. Every claim below was
checked against the tree at `karta/invariant-foundations/integration` (`3b44ddc`);
the commands are inline so a reader can re-run them.

**Summary.** Kaizen was not misbehaving in the way the error message implied. It was
triggered by a signal it was told to act on, and its edit was refused by a guard that
does not test the thing it claims to test. The guard rejects any *rewording* of a rule
— including strict tightenings and typo fixes — and **admits any weakening phrased as
an appended exception**. Separately, the evidence feed that drove the trigger is about
three-quarters documentation noise, counts one marker up to three times, and attributes
every marker in the repo to a single delivery.

The delivery was not harmed: nothing landed, the pack on the branch still reads
`Must enforce`, and the integration branch is clean.

---

## Finding 1 — the trigger was legitimate

`.karta/sme/karta-house-minimalism.md` declares `exclude_rules: ["min.4"]` and defines
`hmin.1`, whose text begins `Narrows min.4:`. It is this repo's replacement for the
built-in rule `min.4`.

`scripts/validate_plugin.py` carries three standing `KARTA-SME-OVERRIDE(min.4)` markers,
introduced by three separate commits:

```
$ git log -1 --format='%h %s' -L 326,326:scripts/validate_plugin.py
df1fa6a feat(scripts): pack validator, deterministic stack detector, checker hardening
$ git log -1 --format='%h %s' -L 349,349:scripts/validate_plugin.py
54633f6 [karta:item-config-and-schema] add roundtable house switch …
$ git log -1 --format='%h %s' -L 396,396:scripts/validate_plugin.py
6ebb782 [karta:item-design-pin-check] catch a frozen design copy …
```

Two of the three share a reason almost verbatim — *"mirrors the proven doc-gardner
block above"* and *"mirrors the proven doc-gardner/kaizen/roundtable …"*.

`skills/karta-kaizen/SKILL.md` tells the agent to tally standing markers repo-wide per
rule id, sets the threshold at "two or more occurrences sharing a reason across two or
more distinct deliveries", and says a rule in the repo's own project pack "is edited in
place". Kaizen followed that instruction.

**The judgment it got wrong** is the direction rule in the same paragraph: "a
would-loosen change becomes an erosion note, never an edit". Every one of those override
reasons argues for *widening* `hmin.1`'s carve-out, and widening a carve-out loosens the
rule. Kaizen should have emitted an erosion note. That is a real fault, and it is a
judgment fault — not a mechanical one.

## Finding 2 — the guard does not test weakening

`assertMonotonicProjectPack` (`extensions/pi/companion-runner.ts`) compares an existing
rule's old and new text with `replacement.includes(text)` — plain substring containment.
Probed directly against the shipped function:

| Edit to an existing rule | Guard verdict | Right answer |
|-|-|-|
| Tighten by rewording ("All new logic, trivial or not…") | rejected as *weakened or removed* | should pass |
| Tighten by appending ("… No exceptions apply.") | passed | correct |
| **Weaken by appending ("… This does not apply to validator-internal branches.")** | **passed** | **should be rejected** |
| Weaken by rewording ("should usually leave…") | rejected | correct verdict, wrong reason |
| Typo fix (trailing `!`) | rejected as *weakened or removed* | should pass |

Two consequences.

**The advertised invariant is not delivered.** INV-23 says kaizen never weakens a rule.
The guard permits weakening whenever it is phrased as an appended exception — which is
the most natural phrasing for widening a carve-out, and precisely the edit kaizen was
drafting. Had it appended *"…except for a branch that mirrors a proven adjacent block in
the same validator"* instead of rewording, **the weakening would have landed silently**.
The guard fired in this incident because kaizen reworded, not because it weakened. This
is INV-2: doctrine claiming more than enforcement delivers.

**The rule is append-only, and nothing says so.** Neither `agents/karta-kaizen.md` nor
the Pi writer execution contract in `extensions/pi/writer-runner.ts` states that an
edited rule's new text must contain the old text verbatim. Meanwhile the skill invites
in-place sharpening, which normally means rewording. A writer told to sharpen and
silently held to append-only will fail the guard on its first honest attempt — this is
the same family as the six child-boundary defects fixed on 2026-09-01: a constraint
enforced at a boundary that the party who must satisfy it was never told.

**The message misreports.** `weakened or removed rule '<id>'` is emitted for a condition
that is actually *"the new text is not a superstring of the old"*. A reviewer reading
that message will look for a weakening that may not exist.

## Finding 3 — the evidence feed is mostly noise

`#overrideEvidence` collects markers with one repo-wide `git grep`, capped at 500.

```
$ git grep -h -I -E "KARTA-SME-OVERRIDE\([^)]+\):" HEAD -- | wc -l
60
$ git grep -h -I -E "KARTA-SME-OVERRIDE\(<[^)]*>\):|KARTA-SME-OVERRIDE\(<pack>" HEAD -- | wc -l
45
```

**45 of 60 records are documentation placeholders** — `<rule-id>` and
`<pack>: <free-text rule paraphrase>` from `README.md`, `agents/*.md`, `.codex/agents/*.toml`,
`benchmarks/*.json`, and an archived binder. They are prose describing the marker grammar,
fed to kaizen as though they were overrides. More of the remainder come from
`docs/how-to/stack-packs.md` and two `tests/pi/*.test.ts` fixtures.

Genuine overrides in production code amount to roughly six.

## Finding 4 — one marker counts three times

`vue.6` and `hvue.4` each show three occurrences. All three are the same marker, in the
canonical skill and its two generated projections:

```
skills/karta-status/scripts/serve_status.py
.agents/skills/karta-status/scripts/serve_status.py
plugins/karta/skills/karta-status/scripts/serve_status.py
```

INV-19 requires those mirrors to be byte-equal, so **every override marker in a skill
script is automatically tripled**. A single one-off override in a skill script clears the
"two or more occurrences" threshold on its own.

## Finding 5 — delivery attribution is systematically wrong

The threshold's "across two or more distinct deliveries" clause depends on the `delivery`
field the host computes. It builds each delivery's commit set with:

```ts
commits: new Set((await git(worktree, ["rev-list", "--first-parent", branch.tip])).split("\n"))
```

`rev-list --first-parent <item-tip>` returns everything reachable from that tip — the
whole history the item branched from — not what the delivery introduced. Item branches
are also short-lived, so only recent deliveries have any:

```
$ git for-each-ref --format='%(refname)' 'refs/karta' | sed -E 's#refs/karta/([^/]+)/.*#\1#' | sort -u | wc -l
20                      # deliveries that have run
$ git for-each-ref --format='%(refname)' 'refs/heads/karta/*/item-*' | wc -l
3                       # all belonging to invariant-foundations
```

Every one of the three `min.4` marker commits is an ancestor of all three surviving item
branches, so all of them — and every other marker in the repo — is credited to
`invariant-foundations`, the only delivery with branches left. Where several deliveries
have surviving branches, `deliveries[0]` after a `.sort()` awards the marker to whichever
delivery name sorts first alphabetically.

So the field is not merely lossy. It is wrong in a fixed direction, and the threshold
clause written on top of it cannot be evaluated as specified.

There is also a cost note: one `git blame` runs per record, 60 here, on every enabled
kaizen run.

---

## What to change

Ordered by severity, not by effort.

1. **Make the guard test direction, not shape.** Appending an exception clause is the
   weakening path that currently passes; catching it is what INV-23 claims. Until then,
   INV-23's register entry should say what the guard actually enforces.
2. **Tell the writer the constraint.** Whatever the guard becomes, state it in
   `agents/karta-kaizen.md` and in the Pi writer contract, so a writer can comply on the
   first attempt.
3. **Fix the message.** Say which condition failed — not-a-superstring is not the same
   claim as weakened-or-removed.
4. **Filter the evidence feed.** Exclude placeholder markers, generated mirrors
   (count the canonical path only), documentation, and test fixtures. Roughly 90% of what
   kaizen currently receives is not an override.
5. **Fix or retire the delivery attribution.** Use the delivery base to item-tip range,
   or drop the "distinct deliveries" clause from the threshold rather than leaving a rule
   defined on a field that cannot support it.

## What is not wrong

The delivery. Every item built, gated, merged and archived; the pack on the branch is
unweakened; the refusal cost nothing but the run, and that crash is fixed separately.
