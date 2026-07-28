# Kaizen phase 2: sharpen and educate — activation

Date: 2026-07-28
Status: roundtabled (5-provider adversarial panel; 4x sound-with-amendments, 1x flawed — every convergent amendment applied, rejections recorded below)
Scope: activates the phase 2 that docs/specs/2026-07-04-kaizen-design.md pre-defined, amended by six days of field evidence (gringotts) and by the 2.25.0 pack-provenance doctrine that postdates the original spec. Doctrine + docs only — no new hooks, no validator schema change, no bench edits.

## The problem, from the field

Gringotts ran karta's full loop for six days (10 binders delivered). Three pack rules needed correcting, and all three failed the same way: **the enforced wording was an unconditional mandate whose applicability condition was missing** — the condition existed in the author's head (or even in the same pack's guidance prose) but not in the checklist line that gates review.

- `htmx.8` (the only kaizen-authored rule of the three): kaizen's checklist wording dropped the "must stay byte-identical" condition that its own Do-bullet stated. Corrected 17 minutes later by a human commit whose body reads "Human-decided loosening — deliberately not a `kaizen:` commit (kaizen never weakens)."
- `htmx.4` (seed-era): corrected ~6 days later by appending an evidence-cited exception — "Known vendored-version exception (seen 2026-07, two independent deliveries)… This narrows *when the pin is required*, not the rule itself."
- The `FileServerFS` Do-bullet (seed-era): additive cache-busting caveat ~10 days later.

Two confirmations that shape this design:

1. **Override markers are the overreach signal.** The `KARTA-SME-OVERRIDE(htmx.4)` marker landed 13 minutes before htmx.4's correction, with matching rationale. A second standing override (`htmx.3`) has produced no rule change — an unprocessed candidate. This is exactly the signal source the 2026-07-04 spec named for phase 2.
2. **Survivor style is teachable.** Every uncorrected rule either scopes to an observable trigger ("Every response whose body varies on `HX-Request`…") or is advisory with a decision procedure. Every corrected rule was an absolute missing its condition.

Honest attribution: only one of three corrected rules was kaizen-authored; two came from the human-curated seed (verified against htmx 2.0.10 while the repo vendors 2.0.4 — an environment mismatch the seed never checked). The discipline below therefore governs rule *authoring and repair* generally, not a kaizen-specific pathology.

Also from the field: the `kaizen(<pack>):` subject variants used for two corrections do not match the bench auditor's `^kaizen: ` pattern, so those commits were measured as *corrections by someone else* rather than kaizen activity. Label discipline is a measurement question, not cosmetics.

## What activates

### 1. First-draft scoping discipline (new rules)

- **The condition lives in the enforced wording.** A checklist rule must itself name the observable condition under which it applies. A condition stated only in Do/Don't/Patterns prose does not count — that is precisely how htmx.8 shipped overbroad.
- **Mandate carve-out.** A checklist mandate is reserved for rules whose violation is deterministically observable in a diff (a missing pin next to a matched branch, a forbidden call, an absent test file). A lesson without such a signature lands as advisory guidance with a decision procedure — the survivor style. Single-incident lessons default to advisory; a mandate needs a second occurrence or a crisp observable trigger.
- **Counterexample scan — evidence, never a gate.** Before landing a new rule, kaizen scans the repo for sites the drafted wording would flag and attaches the site list to the commit body. Its function is to force the condition into the wording and hand the reviewer concrete evidence. It never auto-decides placement; the panel judged an LLM grep unreliable as an autonomous gate.
- **Evidence citation in-text:** new and sharpened rules cite provenance inline — `(seen <date>, <delivery> delivery)` — the convention the field corrections already invented.

### 2. The sharpening pass (delivery mode, autonomous within bounds)

Every delivery-mode kaizen run, after the existing seed/migrate work:

- Read the blast radius for new `KARTA-SME-OVERRIDE` markers and tally standing markers repo-wide per rule id.
- **Threshold:** two or more occurrences sharing a reason across two or more distinct deliveries → sharpen (write the narrow, evidence-cited exception). One occurrence → record it as a candidate in the run envelope and commit body, nothing else. An explicit instruction in the dispatch may still sharpen from a single occurrence — that path is unchanged from phase 1.
- **Direction rule (unchanged from the 2026-07-04 spec):** sharper or clarifying — kaizen writes it. Anything that would loosen — kaizen writes an erosion note instead: rule id, override count, the builds, the reasons given, what loosening would let through. Erosion notes live in the run envelope and the `kaizen:` commit body — durable in git, never in the pack (the panel cut the in-pack section as a stale-data hazard with no reader).
- **Stale-exclusion re-check:** for every standing `Narrows <id>:` replacement rule (see below), compare against the current built-in text. When upstream has absorbed the narrowing, propose retiring the exclusion — an envelope note, not an autonomous edit.

### 3. Surface resolution (the provenance interaction — new since the 2026-07-04 spec)

2.25.0 made an in-place edit to a seeded pack copy an **illegal shadow** (warned now, halts in 3.0.0). The original phase-2 spec predates this. Resolution:

| The rule being sharpened lives in… | The sharpening lands in… |
|-|-|
| a built-in, lesson is repo-specific | the repo's **existing** project pack: `exclude_rules: [<id>]` plus a replacement rule under the project prefix whose text begins **`Narrows <id>:`** |
| a built-in, lesson is environment-generic | an **upstream candidate** note in the envelope + commit body; promotion into the built-in stays a human act in the karta repo |
| the repo's own project pack | in place |
| a seeded cache | **never** — kaizen never edits a seeded cache in place |
| karta's own dogfood repo | house project pack in place; the managed byte-identical minimalism shadow never |

- The `Narrows <id>:` text prefix is the identity thread: reviewers and tools tracking `htmx.4` grep-find its narrowed successor, and standing `KARTA-SME-OVERRIDE(htmx.4)` markers stay meaningful. No new frontmatter schema — the panel's `refine_rules`/`original_id` proposals were declined in favor of this zero-schema convention.
- **Kaizen never creates a project pack.** When sharpening needs one that doesn't exist, kaizen emits the proposed scaffold (frontmatter + the replacement rule) in its envelope and commit body; a human creates the pack. The panel was unanimous that minting a governance artifact (`id_prefix` is a namespace decision) exceeds kaizen's autonomy.

### 4. Label and evidence discipline (doctrine, measured — not gated)

- The commit subject prefix is exactly **`kaizen: `** — the form the bench auditor measures. The field's `kaizen(<pack>):` variant is documented as non-conforming (it reads as a correction *of* kaizen, not kaizen activity).
- The commit body carries the evidence: the citation, the counterexample site list (new rules), the candidate/erosion/upstream notes.
- The panel unanimously cut the proposed commit-trailer enforcement hook: the `-F` blindspot made it theater, and a trailer no tool reads is machinery minimalism forbids. Evidence discipline is doctrine, reviewable in every commit, and measurable by a future bench probe if coverage slips — recorded as a bench candidate, deliberately not built now.

## What does not change

- The never-weaken core rule stands. A `Narrows <id>:` replacement is the sanctioned, visible, evidence-cited form of an exception — landed via `exclude_rules` where the excluded id and the exclusion are both reported at plan time. Loosening without evidence, deleting rules, or promoting a pack to enforcing remain outside kaizen's reach.
- Writer confinement is untouched: `.karta/sme/` + `.karta/kaizen.json`, enforced by the existing hook.
- The bench auditor and field-health probe are not edited — they are the measuring sticks this work is graded by.
- Phase 3 (new-pack suggestions, the advisory/enforcing pack flag and its gate mechanics) stays excluded.

## Sequencing

This doctrine is a prerequisite for the gringotts overlay resolution (its three divergent packs migrate into exactly the shapes defined here), and both precede the 3.0.0 illegal-shadow halt. Phase 2 must not generate new shadows on its way in — that is what the surface-resolution table guarantees.

## Panel record

5 counting reviews (antigravity, deepseek, kimi, minimax, qwen; codex errored, glm5p2 timed out): 4x sound-with-amendments, 1x flawed. Accepted: hook cut, auto-create cut, in-pack erosion notes cut, distinct-delivery threshold, scan-as-evidence, mandate carve-out, `Narrows <id>:` continuity, stale-exclusion re-check, 3.0.0 sequencing. Rejected with reasons: rewriting code-side override markers (kaizen cannot write outside `.karta/sme/`); forbidding exclude+replace outright (repo-specific narrowings genuinely belong locally; it is the sanctioned 2.25.0 shape); a native git commit-msg hook (karta does not install git hooks into consumer repos); an upstream-candidate manifest (the bench's LOCAL-ADDITIVE drift detection already closes that loop — the htmx.8 promotion is the precedent). Empirically verified before the panel: an `## Erosion notes` section validates (moot after the cut), an exclude+replace `extends` pack validates and classifies as `project pack`, and a note-bullet inside the checklist section is loudly INVALID.

## Delivery

One binder (`kaizen-sharpening`): the doctrine edits (agent + skill, projections re-synced) and the docs refresh (`docs/how-to/kaizen.md` moves sharpening + erosion notes from "What's coming" to active; the label and surface-resolution rules documented). No hooks, no validator change, no bench edits.
