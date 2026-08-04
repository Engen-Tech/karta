---
name: karta-house-minimalism
description: "karta's local narrowing of minimalism: min.4's one-runnable-check mandate, carved narrowly for a validator-internal config-shape branch"
always: true
extends: minimalism
id_prefix: hmin
exclude_rules: ["min.4"]
---
## Review checklist
- [ ] hmin.1 — Narrows min.4: new non-trivial logic (a branch, loop, parser, money/security path) must leave one runnable check — an `assert`-based self-check or one small test — with a SINGLE carve-out: a new branch inside a stdlib gate/validator script's own config-shape validation block (the numbered opt-in-config blocks in scripts/validate_plugin.py), where the script's own `--self-test` over the committed config plus the item oracle's malformed-config probe IS that runnable check and no separate per-branch test is owed. The carve-out covers only that validator-internal config branch; logic that carries product behavior still owes its runnable check. This rule stands on its own — it does not presuppose min.4 in the enforced checklist, it replaces it (see the 2026-07-06 stack-pack-hardening and 2026-07-18 roundtable-edict deliveries).
