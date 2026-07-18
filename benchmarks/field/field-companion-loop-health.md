---
id: field-companion-loop-health
family: field
method: deterministic-probe
cadence: every-release
cost: S
probe: benchmarks/probes/field-companion-loop-health.py
probe_status: implemented
results: benchmarks/field/results/
provenance: lens=field; merged_from=kaizen-loop-health, companion-loop-coverage
---

# Companion loop coverage and health (doc-gardner + kaizen)

**Question.** Do the opt-in companion loops fire on every eligible delivery and produce additive edits that stick — gardner coverage per delivery, kaizen edits surviving without human counter-correction, and neither loop's config going stale?

## Procedure

1. Runner: `benchmarks/field/audit_companions.py --repo <path> [--head <sha>]`, stdlib-only python3, one JSON row per repo committed as `benchmarks/field/results/YYYY-MM-DD-companions.json`. Every row records the analyzed HEAD sha; all history reads walk first-parent of the default branch only (never `--all`, never live branches — integration branches get pruned).
2. Enable epochs: parse `git log -p --follow --first-parent -- .karta/doc-gardner.json .karta/kaizen.json` for 'enabled' value transitions; active epoch = latest transition to true (`--diff-filter=A` alone misses disable/re-enable toggles).
3. Delivery denominator: commits adding a file under `.karta/binders/archive/`; emit slug + committer date as a list, not just a count.
4. Gardner coverage: for each archived slug dated inside a gardner-enabled epoch, match subject `^docs: gardner <slug>$` anywhere in first-parent history; for each whiff, auto-classify timing-race iff the oldest commit on the delivery merge's second-parent lineage predates the enable commit, else true whiff; emit slug + both timestamps.
5. Kaizen additive floor: for each `^kaizen: ` commit, per touched `.karta/sme/*.md`, extract active/tombstone ids before and after by importing the parser from `skills/karta-kaizen/scripts/validate_packs.py` (grammar `- [ ] <prefix>.<n> — text`, tombstone `- ~~<id>~~ retired: <reason>`) — do not re-implement it; FAIL if an active id vanishes without a tombstone, WARN if a surviving id's rule text shrinks >20% chars.
6. Correction incidents: non-kaizen commits touching a kaizen-touched pack file 0-14 days AFTER the kaizen commit (window starts at 0 — gringotts 08bc02a landed 17 minutes after dd80e01 and must count; the draft's 48h floor missed its own field evidence). Right-censor: kaizen commits younger than 14d at run time are flagged window-open and excluded from the closed denominator, so the number is independent of run date. Emit each incident as (kaizen sha, correcting sha, delta-minutes, subject) — an incident list, not a rate, at these Ns.
7. Spawn utility: substantive kaizen commits (seed commit excluded) and delivery count in the kaizen epoch, raw counts.
8. Config staleness: deliveries since the last commit touching each config, plus the current focus string printed beside the last 5 delivery slugs — surface-only, no semantic match scoring, no mtime.
9. All fractions reported as n/N with underlying lists, never bare percentages.

## Metric and comparability

Gardner coverage as n/N with per-slug list (timing-race whiffs auto-attributed) + kaizen additive-floor violations (target 0) + correction-incident list (kaizen sha, correcting sha, delta-minutes; 0-14d window, right-censored) + kaizen substantive edits per delivery (raw counts) + config staleness in deliveries-since-touch — all n/N with underlying lists, trended per-delivery to feed the kaizen-phase-2 build decision. Honest caveat carried in every row: deliveries carry no plugin-version stamp, so this is loop health over calendar time, not a controlled version A/B.

The metric stays comparable across releases because every fraction ships as n/N with its underlying list, denominators come from a deterministic commit class (archive-adding commits on first-parent history), and the 14-day correction window is right-censored so the number does not depend on when the audit runs. Timing-race whiffs are auto-attributed rather than judged, and the no-version-stamp caveat is carried in every row so trends are read as calendar-time loop health, never as a controlled version A/B.

## Inputs

- .karta/doc-gardner.json + .karta/kaizen.json history in parchmark and gringotts
- 'docs: gardner' and 'kaizen:' commit streams (contractual grammars: skills/karta-doc-gardner/SKILL.md:51, skills/karta-kaizen/SKILL.md:60)
- .karta/sme/ pack edit history + the parser in skills/karta-kaizen/scripts/validate_packs.py
- field evidence: parchmark #127/#128 timing-race whiffs + stale focus string; gringotts dd80e01 -> 08bc02a 17-minute human rescope

## Seed observation (v2.21.0, 2026-07-17)

parchmark: gardner 8/10 with both whiffs auto-attributed to the enable-timing race; config 5 deliveries stale (focus string still says 'aligned with note tag editing behavior' after five backend-only deliveries); kaizen 0 edits over its post-enable deliveries — spawn utility 0, a near-pure tax. gringotts: gardner 3/3 (upload-reliability correctly excluded as pre-enable); kaizen 1 edit, additive-floor clean, corrected by a human in 17 minutes — incident (dd80e01, 08bc02a, 17 min), the first hard datapoint for whether kaizen phase 1 lacks scoping judgment.
