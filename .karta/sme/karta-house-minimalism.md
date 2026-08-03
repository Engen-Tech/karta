---
name: karta-house-minimalism
description: "karta's local narrowing of minimalism: min.4's one-runnable-check mandate, carved narrowly for a validator-internal config-shape branch"
always: true
extends: minimalism
id_prefix: hmin
exclude_rules: ["min.4"]
---
## Review checklist
- [ ] hmin.1 — Narrows min.4: the one-runnable-check mandate holds for all non-trivial new logic EXCEPT a new branch added inside a stdlib gate/validator script's own config-shape validation block (the numbered opt-in-config blocks in scripts/validate_plugin.py); this repo ships no test framework by design — its checks are manual gate scripts — so the runnable check for that carve-out is the script's own `--self-test` over the committed config plus the karta item oracle's malformed-config probe, never a separate per-branch test; this narrows only what satisfies the runnable check for a validator-internal config branch, never the mandate for logic that carries product behavior (see the 2026-07-06 stack-pack-hardening and 2026-07-18 roundtable-edict deliveries).
