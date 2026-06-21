# Fixture: date picker

**Task:** Add a date-of-birth field to the signup form that lets the user pick a date.

**Why this fixture:** the classic over-build trap — an agent may install a date-picker library and wrap it, where the platform ships `<input type="date">`. The `minimalism` pack's "no dependency where the platform covers it" checklist item should bite here.

**Run both arms:** build this task once with the binder pinning `minimalism` (and the stack pack), once with `sme: []`. Capture each `git diff` against the integration tip.
