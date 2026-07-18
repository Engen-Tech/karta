---
id: parity-mirror-sync-integrity
family: parity
method: deterministic-probe
cadence: every-release
cost: S
probe: benchmarks/probes/parity-mirror-sync-integrity.py
probe_status: implemented
results: benchmarks/parity/results/
provenance: "lens: parity; merged_from: mirror-sync-integrity-probes"
---

# Mirror-sync and skills-lock integrity probes

**Question.** Does the mirror-sync machinery preserve what it claims to protect — file modes across projections, externally-installed skill content under a degraded lockfile, and the integrity the skills-lock computedHash implies?

## Procedure

1. Runner: `benchmarks/probes/sync_integrity.sh` (bash, `set -euo pipefail`; python3 only — `scripts/sync_codex_skills.py` has zero deps, no uv needed). `REPO=/mnt/agent-storage/vader/src/karta`.
2. Each probe gets its OWN fresh copy: `S=$(mktemp -d)/pN; rsync -a --exclude .git "$REPO/" "$S/"`.
3. SAFETY INVARIANT: every script invocation is `python3 "$S/scripts/..."` — ROOT resolves from `__file__` (`sync_codex_skills.py:31`), so a cwd-relative invocation of the checkout's copy would run P2 destructively against the real repo.
4. Fixture derivation (P2/P3): `NAME=$(python3 -c 'import sys; sys.path.insert(0,sys.argv[1]+"/scripts"); import sync_codex_skills as m; ext=sorted(m.external_mirror_skill_names()); print(ext[0] if ext else "")' "$S")`; if empty, synthesize: `mkdir -p "$S/.agents/skills/bench-external-probe"`; write `# bench fixture` to its SKILL.md; inject a complete lock entry `{source:'bench/fixture', sourceType:'github', skillPath:'f/SKILL.md', computedHash:64 zeros}` into `"$S/skills-lock.json"`; `NAME=bench-external-probe` (derived targets + synthesized fixture keep the denominator alive if plannotator is uninstalled or renamed).
5. P1 exec-bit-projection: `T=$(cd "$S" && find skills -path '*/scripts/*.py' ! -path '*__pycache__*' | sort | head -1)`; `[ -n "$T" ] || exit 2` (probe-error, not fail); `chmod +x "$S/$T"`; capture `python3 $S/scripts/sync_codex_skills.py --check` output as evidence only (today: IN SYNC = mode-blind); run write mode; PASS iff BOTH `"$S/.agents/skills/${T#skills/}"` AND `"$S/plugins/karta/skills/${T#skills/}"` have owner-exec (python3 `os.stat` mode & 0o100) — single assertion, `--check` drift-reporting is evidence not verdict.
6. P2 lock-degradation-survival: `python3 -c` deletion of `computedHash` from the `$NAME` entry in `"$S/skills-lock.json"`; run write mode capturing exit+output (NOTE: nonzero exit is NOT a guard — today it is a post-deletion FileNotFoundError crash at `sync_codex_skills.py:227` because the 'have' set (`:201`) is stale after the rmtree at `:224`); PASS iff `test -d "$S/.agents/skills/$NAME"`.
7. P3 external-hash-liveness: `printf x >> "$S/.agents/skills/$NAME/SKILL.md"`; PASS iff (`--check` exits nonzero AND stdout contains `$NAME`) OR (`python3 $S/scripts/validate_plugin.py` exits nonzero AND output contains `$NAME`); today both silent (validated: grep count 0, exit 0).
8. Output: `benchmarks/results/<date>-sync-probes.json` = `{probes:[{id:'P1-exec-bit-projection'|'P2-lock-degradation-survival'|'P3-external-hash-liveness', pass:bool, evidence:{cmd outputs, stat modes, exit codes}}], passed:N, total:3}`.
9. Probe IDs are permanent; a previously-passing ID may never regress release-over-release; new probes append under NEW IDs and are reported per-probe, never folded into the /3 headline retroactively. At 3/3 the vector stays live as a regression tripwire (still S).

## Metric and comparability

Probes passed out of 3 (P1-exec-bit-projection, P2-lock-degradation-survival, P3-external-hash-liveness), each a binary finding with a JSON evidence card; probe IDs permanent; a previously-passing ID may never regress; new probes append under new IDs, never folded into the /3 retroactively.

Comparability holds because the denominator is pinned per probe ID, not per headline: permanent IDs mean a release-over-release diff always compares the same assertion, the no-regression rule makes any flip from pass to fail a gate event, and new probes extend the report under new IDs instead of retroactively rewriting the /3. Each verdict carries a JSON evidence card (command outputs, stat modes, exit codes), so a changed verdict is auditable against the recorded evidence rather than trusted prose, and the synthesized fixture keeps the P2/P3 denominator alive even if the live external skill disappears.

## Inputs

- scripts/sync_codex_skills.py (:31 ROOT, :64-77, :120 mirror_files, :127 mirror_skill_names, :143-152, :201 have-set, :223-246 orphan rmtree)
- scripts/validate_plugin.py (:304)
- skills-lock.json (3 live plannotator entries today)
- .agents/skills/ (13 mirrored dirs)
- plugins/karta/ projection

## Seed observation (v2.21.0, 2026-07-17)

0/3 today, all three verified empirically in scratch rsyncs: P1 projections stay 664 after chmod +x canonical + write mode (sync compares read_bytes only, write mode recreates via write_bytes/0644); P2 a degraded lock entry (any of the 4 required string fields missing) reclassifies plannotator-compound as a karta-managed orphan and rmtrees it — a live third-party skill — then crashes unlinking stale paths (a FOURTH, previously unknown defect the probe dry-run itself surfaced: stale 'have' set FileNotFoundError at sync_codex_skills.py:227); P3 a tampered external SKILL.md passes sync --check (exit 0) and validate_plugin.py (0 mentions) because mirror_files():120 excludes external paths and nothing anywhere recomputes computedHash — the hash protects names, not content. P2's trigger (hand-edit or lock format migration) is a routine event. P1 is latent today (zero +x files exist under skills/) so it primarily guards the first release that ships an executable skill script.
