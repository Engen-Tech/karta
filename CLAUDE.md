@AGENTS.md

## Standing directions (Claude sessions in this repo)

- **Every karta binder is roundtabled before commit — always.** Run the multi-model review panel on the drafted binder and file the record with `scripts/roundtable/run_review.py --record` before any binder commit; same for landing a delivery branch on main. The stakes are the framework itself — a flawed binder propagates into every consumer repo. The `KARTA_SKIP_ROUNDTABLE=1` hatch exists for a down review environment only, never for skipping review; if used, run the panel retroactively as soon as the environment is back.
