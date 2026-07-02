We are deciding the SCOPE of a "skill → shell + agent" conversion for the karta orchestration plugin (a Claude Code + Codex CLI dual-platform plugin).

CONTEXT — what karta is:
- 9 skills, each with a Claude-side model/effort pin in SKILL.md frontmatter.
- 3 agents already exist (karta-acceptance-reviewer, karta-safety-auditor, karta-doc-gardner) — each is a model-pinned subagent dispatched by a thin shell skill (karta-verify dispatches the two gates; karta-doc-gardner skill dispatches the gardner agent).
- The proposed conversion: turn each remaining reasoning skill into a thin shell that dispatches a model-pinned agent, instead of holding the reasoning inline.

WHY IT MATTERS:
- Claude Code honors a skill frontmatter model/effort pin natively.
- Codex skills have NO model field. Under Codex, a skill tier is silently ignored — the skill runs at the session model. Only registered Codex agents (.codex/agents/*.toml, with model + model_reasoning_effort) honor a pin.
- So converting a skill to shell+agent is the ONLY way its intended tier takes effect under Codex.

THE 9 SKILLS, with current Claude tier and shape:
1. karta-plan        — opus/xhigh   — synthesizes a binder of work items from a problem (deep reasoning, the heaviest)
2. karta-validate    — opus/xhigh   — visual design-fidelity gate (vision + structured diff, heavy)
3. karta-build       — sonnet/high  — implements one work item in an isolated worktree (long, agentic, runs lint/test/build + acceptance)
4. karta-deliver     — sonnet/high  — orchestrates parallel build waves onto an integration branch (long, agentic, multi-step)
5. karta-plainlanguage — sonnet/medium — rewrites prose to be clear (focused, moderate)
6. karta-verify      — haiku, ALREADY a thin shell dispatching two gate agents (no conversion needed)
7. karta-doc-gardner — haiku, ALREADY a thin shell dispatching the gardner agent (no conversion needed)
8. karta-debt        — haiku, read-only repo-wide grep+report ledger (one-shot, no reasoning)
9. karta-status      — haiku, read-only git-derivation + serve a status page (one-shot, no reasoning)

THREE CANDIDATE SCOPES:
- clean-3: convert only the 3 clearest wins (the heaviest reasoning skills where a tier pin matters most under Codex). backer argument: smallest blast radius, proves the pattern, ships value fast.
- reasoning-5: convert all 5 reasoning skills (plan, validate, build, deliver, plainlanguage). backer argument: every skill that actually reasons gets its Codex tier; the 4 haiku skills are already shells or one-shot read-only.
- all-9: convert everything including the 2 read-only haiku skills (debt, status). backer argument: uniformity — every skill is a shell, one mental model.

CONSTRAINTS (non-negotiable):
- Dual-platform: must work on Claude Code AND Codex CLI, no Claude regressions.
- Codex plugins CANNOT bundle subagents. Today the 3 existing agents work around this via bundled instructions in the spawn-site skill (adaptive dispatch). A converted skill would need the same, OR a Codex install hook, OR project-scoped .codex/agents/.
- Each conversion triples the projection surface (skills/ → .agents/skills/ → plugins/karta/skills/) kept byte-identical by sync_codex_skills.py, plus a new agents/<name>.md → .codex/agents/<name>.toml + bundled references/<name>.agent.md via sync_codex_agents.py.
- karta has no headless build runner yet, so the empirical A/B/C spike (same build through builder-A/B/C on a fixture) is not currently runnable.

THE QUESTION: Which scope — clean-3, reasoning-5, or all-9 — should karta pick, and why? Give conclusions, assumptions, alternatives, and a confidence level. Address: (a) which skills actually benefit from a Codex tier pin vs which are fine at session-model; (b) the Codex subagent-bundling workaround cost per converted skill; (c) whether the lack of a headless runner should block the scope decision or only block execution; (d) any scope you would reject outright.
