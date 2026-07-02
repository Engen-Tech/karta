# karta backlog

Work that is known, scoped, and deferred — not yet on a branch. Each item says what it is, why it is not done, and what unblocks it, so anyone can pick it up cold.

**Current release:** `main` is at **v1.10.0**. The per-host model + effort tiering shipped in v1.9.0; the karta-validate Phase-3 comparison rework shipped in v1.10.0. Tag `validate-stable` pins v1.9.0 as the rollback point for the validate rework (`git reset --hard validate-stable`).

Status legend: **Ready** (scoped, unblocked) · **Blocked** (needs a prerequisite) · **Idea** (not yet scoped).

---

## 1. skill → shell + agent conversion — *Blocked* (scope decided 2026-07-01)

**What.** Turn each *reasoning* skill into a thin shell that dispatches a model-pinned agent, instead of holding the reasoning inline. karta already does this for the two gate agents and the doc-gardner agent (3 of 9 skills are already shells).

**Why it matters.** Agents honor a model/effort pin under both hosts (Claude Code reads `model`/`effort`; Codex reads the projected `.codex/agents/*.toml`). Skills only honor the Claude-side `model`/`effort` — **Codex skills have no model field.** So today a skill's intended tier is silently ignored under Codex. Converting the reasoning skills to shell-plus-agent is the only way per-skill tiers actually take effect on Codex.

**Scope decision: reasoning-5, phased as clean-3 first.** A roundtable deliberate (Antigravity, Codex, DeepSeek, GLM-5.2, Kimi, Qwen) + exa research on the Codex workaround converged on this. Full findings, panel verdict, and raw artifacts: [`skill-to-agent-scope/FINDINGS.md`](skill-to-agent-scope/FINDINGS.md).

- **Phase 1 — clean-3:** convert `karta-plan`, `karta-validate`, `karta-build`. Proves the pattern on the heaviest reasoning + the most agentic skill.
- **Phase 2 — reasoning-5 remainder:** convert `karta-deliver`, `karta-plainlanguage`. Lands the rule "every reasoning skill honors its Codex tier."
- **Reject all-9.** `karta-debt` and `karta-status` stay inline — read-only one-shots that gain nothing from a pin; a Haiku pin could *degrade* them on a stronger session model (GLM's note).

**What's blocking it.**
- **No headless build runner** to drive the A/B/C spike (same build through builder-A/B/C on a fixture binder). This blocks *execution/merge validation*, not the scope decision — scope is architectural (Codex's platform constraint), the runner is empirical tier-tuning. Build it during Phase 1.
- **Codex can't register bundled subagents, and the install-hook workaround is currently blocked upstream.** A Codex plugin install cannot ship subagents (openai/codex#28491, open). The "SessionStart install-hook" path listed previously is **not viable today**: plugin-local `hooks.json` is not loaded by the runtime (openai/codex#16430, open, updated 2026-05-16). The only working Codex path is the existing **bundled-instructions-in-skill fallback** that `karta-verify`/`karta-doc-gardner` already use (adaptive dispatch: registered TOML when present, else spawn a worker with the bundled `references/<name>.agent.md`). Re-check #16430 / #28491 before Phase 2 — if either ships, the per-skill cost drops and the install-hook option reopens.

**Unblock path.** (1) Build a minimal headless build runner. (2) Convert `karta-plan`, `karta-validate`, `karta-build` using the existing bundled-instructions fallback (no install-hook available yet). (3) Run the A/B/C spike on a fixture binder; manually validate dual-platform. (4) Re-check upstream issues; convert `karta-deliver` + `karta-plainlanguage` if the spike holds. Keep each shell byte-identical across the three projection targets (`skills/` → `.agents/skills/` → `plugins/karta/skills/`, via `sync_codex_skills.py`) and the agent projections via `sync_codex_agents.py`.

**Open judgment call.** If `plainlanguage` proves to *downgrade* Codex sessions already running sonnet+ (GLM's risk), Phase 2 can drop it to a clean-4 (plan, validate, build, deliver) without breaking the rule — the boundary is still "reasoning skills get their tier." Revisit after the Phase-1 spike.

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
