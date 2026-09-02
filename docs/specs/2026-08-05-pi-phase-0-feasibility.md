# Pi phase 0 feasibility

## Purpose

Phase 0 tests the assumptions that could invalidate a first-class Pi package before Karta commits to the full adapter.

The SDK design is still provisional. It advances only after the remaining gates below pass.

## What the spike contains

- A root Pi package manifest with one explicit extension entry: `extensions/pi/index.ts`.
- Trust-gated discovery of the ten canonical Karta skills. The package manifest does not expose skills statically.
- A child-session factory with an in-memory session and settings, no ambient extensions, skills, prompts, themes, context files, or tools.
- Provider and runtime-credential mirroring for child sessions.
- A registry that aborts and disposes every active child during Pi shutdown.
- `/karta-phase0 status|auth|child|cancel` probes.
- Unit and RPC integration tests.

## Confirmed

|Assumption|Result|
|-|-|
|Pi can load Karta as a local package from an unrelated working directory|Pass|
|Pi can install a pinned Git ref, update it, roll it back, and uninstall it|Pass|
|The package extension loads once and rejects a second package root|Pass|
|Denied project trust hides Karta skills|Pass|
|Approved project trust exposes all ten Karta skills|Pass|
|Project-local skill collisions follow Pi precedence|Pass; the project skill wins|
|Child sessions can exclude ambient resources and context|Pass|
|Stored OpenAI Codex and Anthropic credentials resolve in a child runtime|Pass|
|Google environment credentials resolve in a child runtime|Pass|
|A CLI `--api-key` runtime override can be copied without persisting it|Pass|
|A dynamically registered declarative provider configuration can be copied|Pass|
|A strict gate runtime resolves its exact model without the parent-model fallback|Pass|
|A real isolated stored-auth gate preflight completes|Pass; `KARTA_GATE_RUNTIME_OK` returned|
|OAuth refresh crosses expiry and coalesces across active and independently created runtimes|Pass; one refresh under concurrency|
|A child can complete a real model turn|Pass; `KARTA_PHASE0_OK` returned|
|Aborting a child reaches its active tool signal|Pass|
|Four real children—two in tools, one streaming, and one idle—abort and dispose together|Pass; both tool signals aborted and zero children remained|
|Duplicate local and Git package roots can load together|No; startup fails closed|
|Production install has vulnerable runtime dependencies|No; the package has peer dependencies only|

A project-local `karta-plan` skill shadows the package skill. Karta's authoritative dispatch and gate prompts therefore cannot depend on the ambient skill winner. The extension must load those assets from its own package root.

## Unsupported by design

- Parent `before_provider_request` and `before_provider_headers` handlers are not replayed in gate children. The package runs a direct isolated preflight instead of loading ambient extensions.
- Dynamically registered native providers and provider configurations containing `streamSimple`, `refreshModels`, or extension OAuth callbacks are rejected before gate dispatch because they carry executable ambient extension code.

Native Windows and Linux behavior remains a Phase 6 release-matrix requirement, not an SDK architecture blocker.

## Current decision

Phase 0 is complete and the direct Pi SDK remains viable. Stored credentials, environment credentials, runtime API keys, declarative providers, OAuth refresh, direct isolated requests, cancellation, and multi-child cleanup work with the adapter. Unsupported executable provider shapes fail before a gate child is created.

Gate isolation wins over compatibility with ambient provider hooks. If a supported provider cannot complete the direct preflight, Karta stops rather than silently loading those hooks.

## Run the checks

From the `karta-pi` whole-feature sibling worktree on the Mac in a local terminal, run `npm run check:pi`.

In an approved Pi session loaded from this package, use `/karta-phase0 auth`, `/karta-phase0 gate-auth`, `/karta-phase0 gate-child`, `/karta-phase0 child`, `/karta-phase0 cancel`, and `/karta-phase0 multi-cancel` to exercise the live model paths. The probes never print credentials.
