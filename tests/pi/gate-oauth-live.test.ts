import assert from "node:assert/strict";
import { rm } from "node:fs/promises";
import test from "node:test";
import {
  ModelRegistry,
  ModelRuntime,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { ChildRegistry, GateProviderPreflight } from "../../extensions/pi/child-runtime.ts";
import { executeGateOnEvidence } from "../../extensions/pi/gate-runner.ts";
import { createGateEvidenceFixture } from "./fixtures/gate-evidence.ts";

const ENABLED = process.env.KARTA_LIVE_OAUTH === "1";
const PROVIDER = process.env.KARTA_LIVE_OAUTH_PROVIDER ?? "openai-codex";
const MODEL = process.env.KARTA_LIVE_OAUTH_MODEL ?? "gpt-5.6-sol";

test(
  "stored OAuth completes a real isolated gate tool-call and verdict roundtrip",
  { skip: !ENABLED, timeout: 5 * 60_000 },
  async () => {
    const fixture = await createGateEvidenceFixture();
    const runtime = await ModelRuntime.create({ allowModelNetwork: false });
    const model = runtime.getModel(PROVIDER, MODEL);
    assert.ok(model, `missing live OAuth model ${PROVIDER}/${MODEL}`);
    const modelRegistry = new ModelRegistry(runtime);
    const auth = modelRegistry.getProviderAuthStatus(PROVIDER);
    assert.equal(auth.configured, true);
    assert.equal(auth.source, "stored");
    const ctx = {
      cwd: fixture.repo,
      model,
      modelRegistry,
      thinkingLevel: "minimal",
    } as unknown as ExtensionContext;
    const registry = new ChildRegistry();
    const preflight = new GateProviderPreflight();
    try {
      const result = await executeGateOnEvidence(
        ctx,
        "acceptance-gate",
        fixture.manifest,
        preflight,
        registry,
      );
      assert.equal(result.verdict, "pass", JSON.stringify(result));
      assert.equal(result.provider, PROVIDER);
      assert.equal(result.model, MODEL);
      assert.equal(registry.size, 0);
      assert.equal(preflight.size, 1);
    } finally {
      await registry.abortAll();
      await rm(fixture.root, { recursive: true, force: true });
    }
  },
);
