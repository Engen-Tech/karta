@AGENTS.md

## Standing directions (Claude sessions in this repo)

- **Every karta binder is reviewed before commit — always.** Same for landing a delivery branch on main. The stakes are the framework itself: a flawed binder propagates into every consumer repo. That requirement has not changed; what performs the review has.
- **The review is the multi-lens panel, not roundtable.** `.karta/roundtable.json` carries `enabled: false`, so the roundtable tool is not called and its two enforced gates no longer fire. Run `scripts/review/binder_review_panel.js` on the drafted binder before a binder commit, and on the diff before landing a delivery branch. See [AGENTS.md](AGENTS.md) — "Review before commit" — for what that trades away.
- **Landing a delivery branch on main is the user's call — ask before you merge.** Assembling the branch and running the floor are yours; deciding it ships is not. `scripts/hooks/roundtable_gate.py` now blocks the merge, and `KARTA_SKIP_ROUNDTABLE` does not bypass it — but the gate cannot tell an agent from a human, so **never set `KARTA_LANDING_APPROVED=1` yourself**. Say the branch is assembled, say the floor result, and ask. See [AGENTS.md](AGENTS.md) — "Two human approvals, and only one of them is enforced".
- **Never record a panel result as a roundtable.** Every lens is the same model wearing a different hat, so it cannot meet the `min_providers` floor. Do not pipe it to `scripts/roundtable/run_review.py --record`, and do not file it under `.karta/roundtable/`.
