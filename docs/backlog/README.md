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

## 3. splitting `serve_status.py` — *Shelved* (decided 2026-08-16)

**What.** Split `skills/karta-status/scripts/serve_status.py` (5,504 lines when this was investigated, 46% of it its own self-test suite; the watch-redesign delivery has since taken it past 11,800) into flat sibling modules.

**Why shelved.** Not blocked — a judgement that the plan cost more to make safe than the monolith costs to live with. Three review rounds, two independent providers each; every round found real defects, and every round found defects *introduced by the previous round's fixes*. The binder reached 77 KB and 101 oracle assertions for a refactor shipping no user-visible change. The cause is a property of the file: its self-test scans its own source, with every forbidden literal assembled at runtime so the checks cannot match themselves — so adding a check produces a second-order effect nearly every time. Two of the plan's own safety mechanisms were then disproved empirically: `git diff -M --find-copies-harder` scores a 2,522-line extraction at 8–11% similarity and detects nothing, and the check-name manifest could not be bootstrapped in the commit that creates it.

**What survives.** Full investigation, the verified facts about the file, and the narrowest restart shape: [`watch-module-split/FINDINGS.md`](watch-module-split/FINDINGS.md). Several findings are worth reading even if the split never happens — the hub identity digest goes blind on any code moved to a sibling, `max(mtime)` is not a change detector, and the `globals()` patch (now at `:5500`, and shifting as the file grows) passes vacuously from any other module.

**Cost accepted.** `watch-redesign.json` stays valid as written; deliveries keep serializing on the one filename.

---

## 4. the commit gate cannot accept a single-model review — *Ready* (filed 2026-08-17)

**What.** Teach `scripts/hooks/roundtable_gate.py` and `run_review.py` a second, clearly-labelled
record kind, so a multi-lens panel review can satisfy the plan-commit gate without ever claiming to
be a multi-model roundtable.

**Why it matters.** `.karta/roundtable.json` has one master switch, and the only record the gate
accepts is one that cleared the `min_providers` floor. With the roundtable environment unavailable
the only option is `enabled: false`, which turns off both enforced gates — so karta's own binders
are now reviewed by discipline instead of by a check, which is the skippable prose the doctrine
exists to avoid.

**The surprise that makes it cheaper.** The gate never inspects a record; it delegates to
`check_fresh()`, which compares `reviewed_hash` and nothing else. A record containing only a correct
hash already passes (verified). So the gate side accepts a non-roundtable record today — the work is
making that deliberate and labelled. The same finding is a latent hole on its own: a hand-written
hash file passes with no review behind it.

Full report, evidence, constraints and the open design decisions:
[`roundtable-panel-record/BUG.md`](roundtable-panel-record/BUG.md).

---

## 5. the right panel is a binder detail view, not a phase list — *Done* (shipped in watch-drill-in, 2026-08-22)

The main panel now shows exactly one binder — the one picked in the left rail — with no Now / Next
/ Later grouping. `panel-is-one-binder` removed `_PHASE_DEFS`'s grouping markup from the panel
(`phase__head`, `phase__binders`, `data-kw-phase` and the rest); the rail is the selector, the panel
is the detail. The other work this entry carried is resolved the same way: the binder summary,
type-scale gaps, and inverted card ground are closed, the live-page controls are booked as intended,
and the rail carries a halt badge. One item from the original list is still open — the binder head's
own progress track is still square where the design's is a pill — and five more turned up in the
same comparison run. All of it, closed and open, is tracked in
[`docs/conventions/watch-design-fidelity.md`](../conventions/watch-design-fidelity.md#findings-from-the-latest-run),
not duplicated here.

---

## 6. the design-asset rule certifies more than it enforces — *Ready* (filed 2026-08-18)

**What.** `scripts/validate_plugin.py`'s design-asset rule promises that every asset a committed
design reference points at "resolves inside this repo". It checks neither half completely.

**Two holes.** `_ASSET_REF_RE`'s attribute branch admits double quotes only, so `<img src='x.png'>`
and `<img src=x.png>` are never seen at all. And the resolution check never tests containment, so
`/etc/hostname` and a `../../../../../../etc/hostname` both pass — despite the rule's own error text
and the runbook both promising otherwise.

**Nothing is wrong today.** The one committed capture's nine refs are all double-quoted, relative,
and inside the repo. This is a rule that would not catch the next one.

**Unblock path.** Match all three attribute quotings, assert containment against the repo ROOT — not
the design directory, since the mascot and all eight font faces legitimately live under `skills/` —
and add three negative controls to `_self_test()`'s `design_cases`, one per hole.

---

## 7. the watch ensure probe scores a slow answer as a foreign occupant — *Ready* (filed 2026-08-18)

**What.** The watch end-to-end check fails roughly one run in six, and has since before the
watch-fidelity delivery.

**Why.** `hooks/scripts/inject_karta_status.py` waits on a detached ensure child whose `_probe_hub`
has a hard 500ms per-attempt cap. A loopback `/identity` that answers slower than that is scored
"foreign", the child steps past it, and the breadcrumb never clears.

**Measured, not inferred.** Sixteen interleaved paired runs: two failures in a worktree, two in a
pristine clone — identical rates, so it is not worktree-specific. `KARTA_WATCH_STATE_DIR` is a
proven no-op as a remedy: both self-tests overwrite it with their own temp dir before the first
check.

**Unblock path.** Retry the identity probe rather than treating one slow answer as a verdict.

---

## Done (recent)

- **v1.9.0** — per-host model + effort tiering on all 3 agents + 9 skills (PR #1, merged).
- **v1.10.0** — karta-validate Phase-3 comparison-prompt rework: data-first structured diff → mandatory screenshot pass → evidence-grounded findings, role/position matching, capture-artifact filter, severity rubric (PR #2, merged).
