# User pack sovereignty — your pack copies are yours

Date: 2026-07-30. Status: decided — records a direct user edict. Supersedes the halt phase of the [2026-07-21 pack-overlay redesign](2026-07-21-pack-overlay-redesign.md) on that one point; the 2026-07-21 spec file stays untouched as a historical record.

## The edict

Users may edit the pack copies in `.karta/sme/` however they please. The copies belong to the project, and user changes always win over karta's. Machinery built to deter those edits — the loud warning, the write denial, the halt planned for a future release — punished owners for using their own files, so it goes. Honest information stays.

## What changes

**The state is renamed.** A copy of a shipped built-in carrying a genuine user edit was called an `illegal shadow`. It is now a **local fork**, reported with a message containing the exact phrase `local fork: a user-edited copy of the shipped built-in`, naming the built-in it forked. The report is one neutral line stating the single consequence: the copy no longer receives upstream pack updates. Never a warning, never a deprecation, never an instruction to fix anything.

**The planned plan-time halt is cancelled.** The 2026-07-21 spec staged a future release in which planning halts on an edited copy and the overlay-wins behavior is removed. That phase is cancelled. Forked copies keep working, indefinitely, exactly as their owner wrote them.

**The write guard's fork-deny half is deleted.** `guard_pack_write.py` no longer refuses a write that would fork a built-in. karta informs, never denies — any writer may make the edit.

## What stays unchanged

- Kaizen's discipline: it never edits a user-edited copy, never weakens a rule, and stays confined to its writer surface by `guard_writer_confinement.py`.
- The validator-clean deny half of `guard_pack_write.py`: a stack pack still lands only well-formed.
- Suppression packs (`disabled: true`).
- Project packs and `extends` composition.
- Stale-cache auto-reseed: a copy with no local edit still refreshes automatically.
- The karta-repo-only managed `minimalism` shadow policy in `validate_plugin.py`.
- The seed-drift bench vocabulary: DIVERGENT and LOCAL-ADDITIVE stay exactly as the bench uses them.
- `hooks/hooks.json` wiring: the pack-write guard keeps its registration and event wiring, with one rule fewer inside.

## No migration

There is no consumer-repo migration. An existing fork is simply reported under the new name — nothing is repaired, moved, or renamed on karta's initiative, ever.
