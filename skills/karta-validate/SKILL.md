---
name: karta-validate
description: Compare a running frontend implementation against design HTML files exported from Claude Design OR a runtime-JSX design source. Opens both the live app (at the caller-provided app URL) and the served design prototype through bundled uv-run capture scripts, captures screenshots and DOM snapshots, then reports structured discrepancies across layout, color, typography, spacing, component structure, and visual hierarchy. Validates one view per invocation — the calling pipeline loops for multiple views. Invoke when validating implementation fidelity — trigger phrases include "validate against the design", "compare implementation to design", "check design fidelity", "does this match the design", "visual QA against design HTML", or any request to diff what's running vs the design prototype files.
---

Compare a single frontend view against its design prototype exported from Claude Design OR a runtime-JSX design source. This skill is karta's **visual acceptance gate** — the gate invoked for oracle `type: visual` items. It is read-only: it reports discrepancies as kickback input for `karta-build` to self-correct and never fixes anything itself. The caller decides what to act on.

Write everything you show a person — the report, the stop messages, the summaries — in plain language. See [references/user-facing-prose.md](references/user-facing-prose.md).

See [references/verification-gate.md](references/verification-gate.md) for how the gate fits the broader build/verify loop, and [references/definition-of-done.md](references/definition-of-done.md) for the acceptance floor.

The skill uses bundled PEP 723 Python scripts to avoid Bash/WSL/POSIX assumptions:

- `scripts/serve_design.py` resolves and serves the design HTML on an OS-assigned localhost port.
- `scripts/capture_view.py` drives `playwright-cli`, captures both targets, and writes one JSON artifact.

Run both through `uv run`. Resolve the script paths relative to this `SKILL.md` directory. Do not invoke Python automation as `python script.py` in a uv-managed environment.

`playwright-cli` is an external dependency. This skill uses the installed `playwright-cli` command; it does not patch, wrap, bypass, or maintain Playwright CLI behavior. If a Playwright action cannot be performed, fail with the command, exit code, stdout, and stderr. When `playwright-cli` is not installed at all, `capture_view.py` fails with a one-time install CTA (`npm install -g @playwright/cli@latest`, then `playwright-cli install --skills` for its companion agent skill); relay that message to the user and stop — do not auto-install or hand-drive Playwright.

## Inputs

The caller's prompt must include:

- **Design HTML file path** — absolute or relative to the workspace root. Can be a directory; `serve_design.py` picks the best HTML file, preferring standalone non-print variants.
- **App base URL** (optional) — where the running app is served. Defaults to `http://localhost:3000` when omitted.
- **App route** — URL path in the running app; appended to the app base URL.
- **Design navigation instructions** — explicit steps to reach the target view in the design prototype.
- **App navigation instructions** (optional) — steps beyond loading the route, such as opening a modal or detail pane.
- **Auth / login setup** (optional) — steps, storage state, cookie, token, or test-login mechanism needed for the target app route to render.
- **Viewport** (optional) — width x height in pixels. Defaults to `1440x900`.
- **Focus areas** (optional) — comparison dimensions to emphasize.
- **Oracle assertions** (optional) — the acceptance assertions for this view as stated in the work item's oracle (e.g., "zero critical or major discrepancies at 1440x900"). When provided, the report adds an `ACCEPTANCE` verdict against these assertions. When omitted, the report describes drift only (fully back-compatible).

For alternate theme contexts, the caller provides navigation/context-switch steps for both design and app. This skill has no separate theme input.

## Workflow

### Phase 0 — Prerequisites  `validate:prereq`

All checks are hard gates. Fail with a clear report rather than prompting.

1. **`uv` is available.** The bundled scripts are run with `uv run`.
2. **Design files resolve.** Run:

   ```powershell
   uv run <skill-dir>/scripts/serve_design.py --self-test
   ```

   Then use the same script in the serve step (`validate:serve`) with the caller's design path. If it cannot resolve an HTML file, stop with: "No design HTML files found at `<path>`. Provide a Claude Design OR runtime-JSX design HTML export."

3. **App dev server is already running.** The caller owns the app server lifecycle. Use a host-native HTTP check or let `capture_view.py` fail on navigation. Do not start the app server here.
4. **`playwright-cli` is available.** `capture_view.py` checks this before capture. If it is missing, the script exits non-zero with an actionable install CTA — the two-step `npm install -g @playwright/cli@latest` then `playwright-cli install --skills`, plus the docs link. Surface that message and stop; this stays a hard gate (no prompting, no auto-install, no degraded capture).

Do not assume Bash, WSL, `/tmp`, `curl`, `grep`, `find`, `lsof`, `kill`, or POSIX background syntax.

### Phase 1 — Serve the design HTML  `validate:serve`

Start the design server as a managed background process/session with the bundled script:

```powershell
uv run <skill-dir>/scripts/serve_design.py --design-path <design-path> --metadata-out <metadata-json>
```

The script:

- resolves the HTML path
- serves from the design file's parent directory so relative `fonts/`, `assets/`, and `uploads/` paths work
- binds to `127.0.0.1` on an OS-assigned port
- writes JSON metadata containing `design_file`, `design_url`, `port`, and `metadata`
- verifies the design URL returns HTTP 200 before reporting readiness

Keep the process handle so cleanup (`validate:cleanup`) can stop it. Read `design_url` from the metadata file or the script's first JSON stdout line.

### Phase 2 — Capture worker  `validate:capture`

Use a capture subagent OR host worker for mechanical capture only. It does not compare and does not suggest fixes.

For simple route-only or text-click navigation, run:

```powershell
uv run <skill-dir>/scripts/capture_view.py `
  --design-url <design-url> `
  --app-url <app-base-url><app-route> `
  --viewport <WxH> `
  --out <capture-json>
```

Optional repeated flags:

- `--design-click-text "<label>"` for simple design navigation
- `--app-click-text "<label>"` for simple app navigation

If the required navigation cannot be represented by the supported capture script inputs, stop and report the unsupported navigation requirement to the caller. Do not hand-drive Playwright outside the script and do not hand-produce a replacement artifact.

The capture artifact contains:

- design/app screenshot paths
- design/app DOM snapshot paths with bounding boxes
- extracted token/heading/button/landmark CSS data
- console errors and request summaries
- `APP_HEALTH`
- `compare_ready`

**Auth-aware gate.** If the app route renders a login/auth screen instead of the target view, the artifact must set:

```text
APP_HEALTH: DEGRADED_AUTH
compare_ready: false
```

Do not compare a login screen against the target design. Return the blocked-auth report in the comparison worker (`validate:compare`) and ask the caller/build skill for authenticated session setup.

### Phase 3 — Comparison worker  `validate:compare`

Use a separate comparison subagent OR fresh host-worker pass with only the capture JSON and any caller focus areas. It has not seen the app code, design files, or pipeline state.

If `compare_ready` is `false` because `APP_HEALTH` is `DEGRADED_AUTH`, return:

```text
STATUS: blocked_auth

SUMMARY: The app route showed an authentication screen, not the target view, so design fidelity was not checked. Set up an authenticated session and re-run.

APP_HEALTH: DEGRADED_AUTH

DISCREPANCIES:
Not evaluated.

TOKEN_DRIFT:
Not evaluated.

MISSING_ELEMENTS:
Not evaluated.

EXTRA_ELEMENTS:
Not evaluated.
```

Otherwise compare across:

- layout and structure
- colors and theming
- typography
- spacing
- component fidelity
- visual hierarchy
- interactive elements
- content and copy, ignoring mock-data value differences

Use bounding boxes for quantitative layout/spacing comparisons. Flag position differences over about 20px and gap differences over about 8px as candidates, then use visual judgment for severity.

Expected report format:

```text
STATUS: <match | partial | mismatch | blocked_auth>

SUMMARY: <2-3 sentence overall assessment>

APP_HEALTH: <OK | DEGRADED | DEGRADED_AUTH>

DISCREPANCIES:
For each issue found:
- DIMENSION: <layout | colors | typography | spacing | components | hierarchy | interactive | content>
- SEVERITY: <critical | major | minor | cosmetic>
- ELEMENT: <specific element or area>
- DESIGN: <what the design shows, citing values where possible>
- APP: <what the app shows, citing values where possible>
- NOTES: <context>

TOKEN_DRIFT:
For each significant CSS custom-property difference:
- TOKEN: <name> | DESIGN: <value> | APP: <value or "not defined">

MISSING_ELEMENTS:
Bulleted list, or "None".

EXTRA_ELEMENTS:
Bulleted list, or "None".

ACCEPTANCE: <pass | fail>   ← include ONLY when oracle assertions were provided; omit entirely otherwise
```

`ACCEPTANCE: pass` means every provided oracle assertion holds (e.g., zero critical and zero major discrepancies). `ACCEPTANCE: fail` means at least one assertion does not hold. When no assertions are provided, omit the line entirely — the schema is unchanged for callers that do not pass assertions.

Do not add `RECOMMENDATIONS`, `FIXES`, code suggestions, or implementation instructions to the schema. If a worker suggests fixes, strip them before returning the report.

### Phase 4 — Cleanup  `validate:cleanup`

Always stop only the design server process started in the serve step (`validate:serve`). Close the `playwright-cli` named session defensively if the capture worker failed before cleanup. Remove temporary capture artifacts only with host-native filesystem operations and only for paths created by this run.

The final output is the structured report from the comparison worker (`validate:compare`). Do not modify app files, design files, or ticket files.

## Gotchas

- **Design render delay.** Runtime-JSX prototypes can be blank until React/Babel finishes. `capture_view.py` waits for `#root > *` or `body > *` before screenshotting.
- **Design navigation is client-side.** The prototype usually uses `useState`; reach views by interactions, not by changing the design URL.
- **HTTP server directory matters.** Serve from the design file's parent directory so relative assets resolve.
- **Standalone HTML is preferred.** The server script chooses standalone non-print HTML before other HTML files.
- **Auth redirects are not visual mismatches.** `DEGRADED_AUTH` blocks comparison and routes the problem back to the caller for session setup.
- **Validation is read-only.** This skill reports only. It never fixes, re-runs after fixes, or changes files.
- **Playwright is external.** Do not repair or bypass `playwright-cli` internals. Fail clearly when a Playwright action fails.
- **This is the visual acceptance gate.** karta routes `oracle.type: visual` items here. Other oracle types (unit, integration, e2e, smoke) go to `karta-acceptance-reviewer`, not here.
- **The report is kickback input, not a fix.** A `STATUS: mismatch` or `ACCEPTANCE: fail` result feeds back to `karta-build` for self-correction within the gate's retry cap. This skill never applies those corrections.
- **The oracle sets the bar.** When oracle assertions are provided, `ACCEPTANCE` is determined by them — not by the skill's own judgment of severity. A single critical discrepancy can produce `ACCEPTANCE: fail` even if the visual diff looks minor. When no assertions are given, no verdict is emitted; the report is descriptive only.
