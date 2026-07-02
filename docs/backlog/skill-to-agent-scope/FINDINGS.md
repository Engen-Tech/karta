# Skill → shell + agent scope — roundtable + exa findings

Date: 2026-07-01
Status: decision recommendation (input to backlog item #1)
Backlog item: `docs/backlog/README.md` §1 "skill → shell + agent conversion"

## TL;DR

**Target scope: reasoning-5. Ship in two phases — clean-3 first, then build + deliver + plainlanguage. Reject all-9.**

The panel (Antigravity, Codex, DeepSeek, GLM-5.2, Kimi, Qwen — Gemini was down) converged 6/6 on rejecting all-9 and on the reasoning/non-reasoning boundary. The split is 5-vs-3 as the *final* target: five models pick reasoning-5; Antigravity alone picks clean-3 as the end state. GLM's compromise — reasoning-5 as the target, clean-3 as phase 1 — is the synthesized recommendation.

## Panel verdict

| Model | Stance | Confidence |
|-|-|-|
| Antigravity | clean-3 (plan, validate, build) as final | High |
| Codex | reasoning-5 as target, clean-3 as first slice | High |
| DeepSeek (Fireworks) | reasoning-5 | High |
| GLM-5.2 (Fireworks) | reasoning-5, phased as clean-3 first | Medium-high |
| Kimi (Fireworks) | reasoning-5 | High |
| Qwen (Fireworks) | reasoning-5 (clean-3 only under pressure) | High on reject-all-9, MH on 5-vs-3 |

Gemini CLI was unavailable this session (free-tier auth deprecated — `IneligibleTierError`); Antigravity covers that lens. Claude is the synthesizer, not a participant.

## Agreement (6/6)

- **Reject all-9 outright.** Uniformity is aesthetic, not architectural. `karta-debt` and `karta-status` are read-only one-shots that gain nothing from a tier pin — and GLM notes a Haiku pin could *degrade* them on a stronger session model (added latency + a weaker model than the session would have used).
- **The reasoning/non-reasoning boundary is the right cut.** `karta-verify` and `karta-doc-gardner` are already shells; `debt`/`status` are deterministic. The 5 reasoning skills are where Codex silently loses the tier today.
- **`karta-plan` + `karta-validate` (opus/xhigh) are non-negotiable.** Heavy synthesis + vision — the pin is load-bearing, not optimization. Largest behavior gap between Claude and Codex.
- **The Codex subagent-bundling workaround is the dominant per-skill cost.** Each conversion adds: canonical `agents/<name>.md` → generated `.codex/agents/<name>.toml` → bundled `references/<name>.agent.md` → 3-way byte-identical mirror (`skills/` → `.agents/skills/` → `plugins/karta/skills/`) via `sync_codex_skills.py` + `sync_codex_agents.py`. Bounded, but only justified where the pin matters.
- **The missing headless build runner blocks *execution/merge*, not the *scope decision*.** Scope is architectural, driven by Codex's platform constraint (skills have no model field). The runner answers the empirical tier-tuning question (is sonnet/high the *right* tier?) — but tiers are already set in SKILL.md. Validate manually per phase until it exists.

## Differences

- **clean-3 vs reasoning-5 as the *target*.** Antigravity alone picks clean-3 as the end state — argues `plainlanguage` degrades gracefully and `deliver`-without-pin is tolerable. The other five say clean-3 is **under-scoped as a target**: it leaves `build`/`deliver` (long agentic, cost+quality sensitive) exposed to session-model drift, and breaks the "all reasoning skills are shell+agent" rule for a single exception.
- **`plainlanguage` inclusion.** Codex/DeepSeek/Kimi/Qwen include it for rule-coherence. GLM flags it as the weakest link — on a sonnet+ session the pin provides near-zero benefit and could even downgrade. Antigravity uses it as the reason to stop at 3.
- **Phasing.** GLM proposes **clean-3 first, then reasoning-5** — capturing reasoning-5's target with clean-3's blast-radius discipline. Codex/Kimi/Qwen agree phasing is wise but frame clean-3 as a milestone, not the destination.

## Exa grounding — the Codex install-hook workaround is currently blocked

The backlog README lists a "SessionStart install-hook (the Festival `ensure-*.sh` pattern)" as one of two unblock paths for the Codex subagent-bundling limitation. Exa research confirms this path is **not viable today**:

- **openai/codex#16430** (open, updated 2026-05-16) — "Plugin docs/examples imply plugin-local hooks, but runtime only executes global hooks.json." Plugins contribute skills / `.mcp.json` / `.app.json`, but a plugin-local `hooks.json` does *not* run. (Issue #17331 was closed as a duplicate of #16430.)
- **openai/codex#28491** (open enhancement, 2026-06-16) — "declare custom subagents inside a plugin manifest (plugin.json)." Not shipping; plugins still cannot bundle subagents.
- **openai/codex#26408** (open bug, 2026-06-04) — "Project-scoped custom subagent in `.codex/agents` is advertised but cannot be spawned." Even the registered-TOML path has an open bug report.
- **openai/codex#19705** (merged 2026-04-28) — "Discover hooks bundled with plugins" PR landed, but #16430 (filed after, still open) reports plugin hooks still don't execute in-session. Net: the merge didn't resolve the runtime-loading gap yet.

**Implication:** today the *only* working Codex path is the existing **bundled-instructions-in-skill fallback** that `karta-verify` and `karta-doc-gardner` already use (adaptive dispatch: registered TOML when present, else spawn a read-only/workspace-write worker with the bundled `references/<name>.agent.md`). There is no install-hook shortcut available yet. This:

- *raises* the per-conversion cost (no cheaper workaround than the bundled-instruction pattern), and
- *strengthens* the case against all-9 — every converted skill pays the full bundled-instruction tax, with no plan-B for the two skills that gain nothing from a pin.

Re-check #16430 and #28491 before Phase 2; if either ships, the cost calculus and the install-hook option both reopen.

## Recommendation (synthesized)

**Target scope: reasoning-5. Ship in two phases.**

1. **Phase 1 — clean-3:** convert `karta-plan`, `karta-validate`, `karta-build`. Proves the pattern on the heaviest reasoning + the most agentic skill. Manually validate dual-platform using the existing bundled-instructions fallback (no install-hook available yet — exa confirmed blocked). Build the headless runner during this phase.
2. **Phase 2 — reasoning-5 remainder:** convert `karta-deliver`, `karta-plainlanguage`. Lands the coherent rule "every reasoning skill honors its Codex tier." Re-check openai/codex#16430 / #28491 first — if plugin hooks or manifest-declared subagents ship, the per-skill cost drops and the install-hook option reopens.
3. **Reject all-9.** `debt`/`status` stay inline. A Haiku pin would add spawn latency and could degrade them on stronger sessions.
4. **Headless runner:** build it before claiming Phase 1 is validated. Do not block the scope decision on it — scope is architectural, the runner is empirical validation.

**Open judgment call:** if `plainlanguage` proves to *downgrade* Codex sessions already running sonnet+ (GLM's risk), Phase 2 can drop it to a clean-4 (plan, validate, build, deliver) without breaking the rule — the boundary is still "reasoning skills get their tier." Revisit after the Phase-1 A/B/C spike once the runner exists.

## Artifacts

- `deliberate-prompt.md` — the exact prompt sent to the panel (35 lines).
- `roundtable-deliberate-result.json` — full raw JSON-RPC response from the `roundtable-deliberate` tool (all six panelist responses, verbatim).
- `exa-codex-plugin-hooks-search.sse.txt` — raw Exa `web_search_exa` SSE response (plugin-hook loading + subagent-bundling issues).
- `exa-codex-subagent-context.sse.txt` — raw Exa `get_code_context_exa` SSE response (project-scoped `.codex/agents` + plugin-manifest subagent FRs).

## How to reproduce

The roundtable MCP server is stdio-only; the panel was driven by speaking JSON-RPC to `/home/dev/.local/share/roundtable/roundtable stdio` directly. The driver script lives at `/tmp/opencode/rt_drive.py` (not committed — it carries the Fireworks API key path wiring). To re-run:

```
uv run … # n/a — roundtable is a Go binary, invoked directly
python3 /tmp/opencode/rt_drive.py roundtable-deliberate "$(cat docs/backlog/skill-to-agent-scope/deliberate-prompt.md | python3 -c 'import sys,json; print(json.dumps({"prompt":sys.stdin.read(),"timeout":900}))')"
```

Exa was driven over HTTP (it's an HTTP MCP server). The two SSE files in this folder are the raw responses; the GitHub issue states were verified via `api.github.com/repos/openai/codex/issues/<n>`.