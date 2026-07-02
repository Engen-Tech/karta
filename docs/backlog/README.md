# karta backlog

Work that is known, scoped, and deferred — not yet on a branch. Each item says what it is, why it is not done, and what unblocks it, so anyone can pick it up cold.

**Current release:** `main` is at **v1.10.0**. The per-host model + effort tiering shipped in v1.9.0; the karta-validate Phase-3 comparison rework shipped in v1.10.0. Tag `validate-stable` pins v1.9.0 as the rollback point for the validate rework (`git reset --hard validate-stable`).

Status legend: **Ready** (scoped, unblocked) · **Blocked** (needs a prerequisite) · **Idea** (not yet scoped).

---

## 1. skill → shell + agent conversion — *Blocked*

**What.** Turn each *reasoning* skill into a thin shell that dispatches a model-pinned agent, instead of holding the reasoning inline. karta already does this for one skill (doc-gardner → `karta-doc-gardner` agent).

**Why it matters.** Agents honor a model/effort pin under both hosts (Claude Code reads `model`/`effort`; Codex reads the projected `.codex/agents/*.toml`). Skills only honor the Claude-side `model`/`effort` — **Codex skills have no model field.** So today a skill's intended tier is silently ignored under Codex. Converting the reasoning skills to shell-plus-agent is the only way per-skill tiers actually take effect on Codex.

**What's blocking it.**
- **No empirical scope decision.** We do not yet know which skills are worth converting. Candidate scopes: `clean-3` (only the clearest wins), `reasoning-5` (all reasoning skills), or `all-9`. Picking needs an A/B/C spike — run the same build skill through builder-A / builder-B / builder-C on a fixture binder and compare — and karta has **no headless build runner** to drive that spike yet.
- **Codex can't register bundled subagents.** A Codex plugin install cannot ship subagents (upstream FRs open). Conversion needs a `SessionStart` install-hook (the Festival `ensure-*.sh` pattern) or project-scoped `.codex/agents/` so the pins survive an install.

**Unblock path.** (1) Build a minimal headless build runner. (2) Run the A/B/C spike on a fixture binder, decide scope. (3) Add the Codex install-hook. (4) Convert the chosen skills, keeping each shell byte-identical across the three projection targets (`skills/` → `.agents/skills/` → `plugins/karta/skills/`, via `sync_codex_skills.py`).

---

## 2. karta-validate hardening — *Ready (small)*

**What.** Two tightenings inside the existing karta-validate skill. Both pre-date the v1.10.0 rework and were flagged by the review panels but left out of that PR to keep its scope clean.

- **Strict pixel thresholds.** Give the comparison concrete numeric bars (e.g. an ignore-below-N-px rule) so borderline spacing/position deltas are classified consistently instead of by feel.
- **Mock-data-vs-real-copy definition.** Write a crisp rule for telling seeded/mock content (dates, names, counts) apart from real UI copy (labels, body text, links), so the matcher ignores the former and still catches genuine copy defects. The v1.10.0 prompt already assumes this distinction — this makes it explicit and testable.

**Why deferred.** Out of scope for the v1.10.0 comparison-prompt PR; small enough to land on their own.

**Notes for whoever picks this up.** Edit only the canonical `skills/karta-validate/SKILL.md`; the `.agents/` and `plugins/karta/` mirrors regenerate via `scripts/sync_codex_skills.py`. Keep `validate_plugin.py --self-test` and both sync `--check` green. If the change is behavioral, re-run an **unbiased** multi-model panel (neutral prompt, verdict free to land anywhere) before merging — that is how the v1.10.0 over-suppression regression was caught.

---

## Done (recent)

- **v1.9.0** — per-host model + effort tiering on all 3 agents + 9 skills (PR #1, merged).
- **v1.10.0** — karta-validate Phase-3 comparison-prompt rework: data-first structured diff → mandatory screenshot pass → evidence-grounded findings, role/position matching, capture-artifact filter, severity rubric (PR #2, merged).
