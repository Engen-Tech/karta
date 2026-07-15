# Codex 1.19 Plannotator test drive

Result: **PASS.** The CLI probes, review offers, scratch render, live browser annotations, annotation mapping, ambiguity return, binder validation, and no-implicit-commit check all passed. The exact work-item oracle and all four repository gates also passed.

## Environment

- Codex: `codex-cli 0.143.0`
- Codex model: `gpt-5.4-mini`
- Karta plugin: `karta@karta-local` version `1.19.0`, installed and enabled in an isolated `CODEX_HOME`
- Plannotator: `plannotator 0.23.0` during the initial probes; `plannotator 0.23.1` at completion
- Scratch root: `/private/tmp/karta-codex-119-plannotator-scratch`
- Karta item worktree: `/private/tmp/karta-codex-119-item-test-codex-plannotator`

## Plan review probes

The exact cross-shell probe was run with two controlled `PATH` values:

```sh
uv run python -c "import shutil,sys; sys.exit(0 if shutil.which('plannotator') else 1)"
```

- Hidden from `PATH`: exit `1`
- `/Users/tej/.local/bin` on `PATH`: exit `0`

The hidden-CLI Codex session used the installed `$karta-plan` skill and returned only the editable card. Its final output contained zero case-insensitive matches for `plannotator`:

```text
**Binder Card**

- `slug`: `greeting-option`
- `title`: Add a greeting option
- `summary`: Add a deterministic uppercase greeting command.
...
**Work Items**

1. `id`: `add-uppercase`
   - `title`: Add uppercase output
   - `summary`: Add a deterministic uppercase greeting option.
```

Codex session: `019f5269-0579-7fc1-95ac-c8dbba44bd48`.

The present-CLI Codex session returned exactly two review verbs with Plannotator recommended:

```text
**Recommended: plannotator** — one browser session over the whole binder reads faster than a card-by-card chat walk, and every gate still runs.

Verbs: `card` — review in chat · `plannotator` — annotate in a browser session
```

Codex session: `019f5269-929f-75e0-ae7b-719a9bc031d2`.

That session reported that its read-only sandbox could not initialize the `uv` cache. It then confirmed the same executable check with `python3`. The separate exact probe above exited `0`; this record does not hide the sandbox fallback.

Neither plan-review session changed Git. Both scratch repositories stayed at `0fcaa2cc40046bd6271520c697fdbe138ee54b6c`, with only the intentionally untracked `.karta/` draft.

## Rendered review file

The installed `$karta-plan` skill rendered this file outside `.karta/binders/`:

```text
/private/tmp/karta-codex-119-plannotator-scratch/session/greeting-option-review.md
```

Codex session: `019f526b-6077-74a1-98ea-f4f7448ec655`.

The file puts the binder frame first. It then lists the work item in dependency order with every required card field:

```text
# Binder Frame

- `slug`: `greeting-option`
- `title`: `Add a greeting option`
- `summary`: Add one command-line option to the greeting fixture.
- `motivation`: Let callers choose uppercase output.
- `scope.included`: `add --uppercase to app.py`
- `scope.excluded`: `change the default greeting`
- `design_facts.source`: `null`
- `design_facts.stack`: `Python standard library command-line fixture`
- `sme`: `minimalism`
- `env_contract.command`: `python app.py`
- `env_contract.supports_isolation`: `true`
- `env_contract.isolation_params`: `[]`
- `env_contract.cwd`: `.`
- `runtime_contract`: not present

## Work Item 1

- `id`: `add-uppercase`
- `title`: `Add uppercase output`
- `summary`: Add a deterministic uppercase greeting command.
- `depends_on`: `[]`
- `contract`:
  - `exposes`: The `--uppercase` flag uppercases the existing greeting without changing the default.
- `touches`:
  - `app.py`
- `oracle`:
  - `type`: `unit`
  - `command`: `python -m unittest`
  - `assertions`:
    - Default output is unchanged.
    - `--uppercase` returns uppercase output.
- `smart-surfaced flags`:
  - `flagged`: `true`
  - `signals`:
    - `contract mutation: adds one CLI flag`
```

The round-trip scratch repository remains at `0fcaa2cc40046bd6271520c697fdbe138ee54b6c`. The binder is still untracked, so no commit occurred before the explicit `commit` verb.

## Delivery review offer

A completed scratch delivery was prepared on `karta/delivery-fixture/integration` at `4126954f8202b6e061da971c5f96bd37392f9e3a`. Its `document-ready` done ref points to the same commit.

With Plannotator hidden, the installed `$karta-deliver` skill ran the exact probe, printed exit `1`, and returned no review offer:

```text
The integration branch is `karta/delivery-fixture/integration`. Review that branch and merge it yourself.
```

Codex session: `019f526a-9a0b-7942-a825-4a5643f61853`.

With Plannotator present, the exact probe exited `0` and the skill returned:

```text
Integration branch handoff: `karta/delivery-fixture/integration` on base `main`. No PR is open; this branch is the assembled result to review and merge.

Review surface: `plannotator` is available. I can open the integration diff in a `plannotator-review` session if you want.
```

Codex session: `019f526a-dc2c-7322-8fec-c7cdbd25414c`.

Both checks left the integration branch, archived binder, and done ref unchanged.

## Human annotation round-trip

The completed Plannotator review returned this structured CLI payload:

```text
1. line 5 on “Add one command-line option to the greeting fixture.” → “Add a deterministic uppercase greeting command.”
2. line 18 on “Work Item 1” → “Make it stronger.”
3. line 22 on “ Add a deterministic uppercase greeting option.” → “Add a deterministic uppercase greeting command.”
```

The payload is preserved byte-for-byte at `/private/tmp/karta-codex-119-plannotator-scratch/session/plannotator-annotations.txt` (SHA-256 `dd86358f413a37b993a311f9f801dafea5a3387c5f6d94498fa0588b32b9496f`).

The exact line 5 request mapped to the binder's `summary`. The exact line 22 request mapped to `work_items[0].summary`. Both fields and the rendered review now read `Add a deterministic uppercase greeting command.`

The line 18 note, `Make it stronger.`, names no field or replacement wording. Karta returned it to chat as unresolved feedback and left the `Work Item 1` heading unchanged. It did not guess a resolution.

The updated scratch binder passed `validate_binder.py`: `VALID. 1 work items; 0 opted out of acceptance checks.` A separate content check confirmed that both mapped summaries match the rendered review and that the ambiguous heading remains unchanged.

## Persisted execution artifacts

The test's external conformance artifacts remain readable under the scratch root:

- hidden-CLI plan output: `absent.output.md` — SHA-256 `69a79caedd2c418ef60f33d69ab714b936836c5edbebe7c2810cb49fa4cc0fc7`
- present-CLI plan output: `present.output.md` — SHA-256 `a489f636e26324b162acc85e72f71c467cb7eb0a060432b94a9d94f8d1f1487c`
- render output and required-field inventory: `render.output.md` — SHA-256 `6edff92ec0c2c82d4052efecea0b7a85894dfbfa7de8e6764f0af897c7af42c1`
- rendered review after mapping: `session/greeting-option-review.md` — SHA-256 `cf1369c9781bdfee99833c87e4c1720fd57a9c53a4a465a89fd379dced63f894`
- validated scratch binder after mapping: `roundtrip-repo/.karta/binders/greeting-option.json` — SHA-256 `0411903151f94829da2dc74bc2743a7ff6b2c401a120fc4d2da7c2dc3e7fdb3e`
- hidden-CLI delivery output: `delivery-absent.output.md` — SHA-256 `a541a0072ade1babd2d51e9c36b988d3510141ac7d2182946d8a8eb405bab717`
- present-CLI delivery output: `delivery-present.output.md` — SHA-256 `2850dc62a83a0eb4a0b9b660e122a97aef7ec7db2a50928a6d9ace689f7c0b27`

Together, these artifacts cover the absent/present capability probes, complete render inventory, human annotation payload, mapped binder and review, validator result, unchanged scratch Git state, and absent/present delivery offer.

## No implicit commit

Before and after annotation ingestion, the scratch repository's HEAD was `0fcaa2cc40046bd6271520c697fdbe138ee54b6c`, with one commit. Git still reported only the intentionally untracked `.karta/` draft. Plannotator did not create a commit, and Karta did not commit the scratch binder without an explicit `commit` verb.

## Final checks

The exact work-item oracle ran with `plannotator 0.23.1`. All four repository gates passed:

- `PLUGIN INTEGRITY: PASS`
- `SHARED COPIES: IN SYNC`
- `CODEX AGENTS: IN SYNC`
- `CODEX SKILLS PROJECTIONS: IN SYNC`

No scenario was skipped or failed. The intentionally ambiguous wording request was handled by returning it to chat without guessing, as the contract requires.
