---
name: karta-house-invariants
description: "The invariant register applied at build time: rules for diffs that touch doctrine, gates, validators, or checks — see docs/conventions/invariants.md"
always: true
---
## Do
- Before writing or amending any gate, validator, or doctrine sentence, open `docs/conventions/invariants.md` and find the entries the change touches; the rule-authoring entries (INV-1 through INV-5) govern the wording itself.
- When a diff changes an invariant's reality — builds a check for a prose entry, widens or narrows one, moves or rewords a carrier — update the register entry (status, carriers, or both) in the same diff.

## Don't
- Don't restate a rule `karta-house-skill-authoring` already carries — enforcement points and claim honesty live in its Do list, negative controls in its house.4. This pack covers what that one does not.

## Review checklist
- [ ] inv.1 — A diff that rewords, moves, or deletes a sentence the register quotes as a carrier updates that register entry in the same diff. A carrier changed with its entry left standing is drift, whichever of the two is right.
- [ ] inv.2 — An exception added or amended anywhere in this diff (a doctrine sentence, a gate condition, a validator carve-out) describes the ENTIRE action it excuses, never one feature of it, and names its edges instead of leaving them to be guessed (register INV-4; the worked example is the binder route exception).
- [ ] inv.3 — A check introduced or changed by this diff that answers "is this current / mine / the same?" derives the answer from content — exact bytes, a hash of them, git plumbing over the staged blob — never from timing, ordering, position, or a label (register INV-3).
- [ ] inv.4 — A diff that promotes a repeated pattern into a rule, a required field, or a default carries the recorded decision — promote, deliberately-optional with the reason, or reject — in the diff or its commit message; repetition alone is never the justification (register INV-5).
