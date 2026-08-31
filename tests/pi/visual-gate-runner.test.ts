import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import {
  judgeVisualEvidence,
  type VisionPreflight,
  type VisualCurrency,
  type VisualCurrencySource,
  type VisualGateInvocation,
  type VisualGateInvoker,
} from "../../extensions/pi/visual-gate-runner.ts";
import type { VisualEvidence } from "../../extensions/pi/visual-capture-runner.ts";

// verification-runner.ts is intentionally NOT imported or modified by this item; its
// pi-visual-capture block-holds tests run untouched under `npm run check:pi`.

const APP_BYTES = Buffer.from("KARTA-APP-RENDER-PNG-\u0001\u0002\u0003", "utf8");
const DESIGN_BYTES = Buffer.from("KARTA-DESIGN-REFERENCE-PNG-\u0004\u0005\u0006", "utf8");

function makeCtx(): ExtensionContext {
  return { cwd: process.cwd() } as unknown as ExtensionContext;
}

function makeEvidence(): VisualEvidence {
  return {
    schema: "karta-visual-evidence-v1",
    binder: "demo",
    item: "item-a",
    route: "/dashboard",
    designReference: "design/dashboard",
    candidateCommit: "a".repeat(40),
    candidateTree: "b".repeat(40),
    generatedAt: "2026-01-01T00:00:00.000Z",
    captures: {
      design: { url: "http://design/", health: "OK", render_health: null, extracted_data: null },
      app: { url: "http://app/", health: "OK", render_health: null, extracted_data: null },
    },
    renderHealth: {
      design: { result: "healthy", readySelector: "main", consoleErrorCount: 0, failedRequestCount: 0 },
      app: { result: "healthy", readySelector: "main", consoleErrorCount: 0, failedRequestCount: 0 },
    },
    structuredDiff: {
      schema: "karta-structured-diff-v1",
      status: "ok",
      blockedReason: null,
      renderHealth: null,
      summary: {
        discrepancyCount: 1,
        tokenDriftCount: 0,
        missingCount: 0,
        extraCount: 0,
        byDimension: { color: 1 },
      },
      discrepancies: [{ property: "color", design: "rgb(17, 24, 39)", app: "rgb(220, 38, 38)" }],
      tokenDrift: [],
      missingElements: [],
      extraElements: [],
    },
  };
}

function baseCurrency(): VisualCurrency {
  return {
    app: { data: Buffer.from(APP_BYTES), mimeType: "image/png" },
    design: { data: Buffer.from(DESIGN_BYTES), mimeType: "image/png" },
    treeRange: { base: "b".repeat(40), target: "c".repeat(40) },
  };
}

function fixedCurrency(): VisualCurrencySource {
  return async () => baseCurrency();
}

function stubPreflight(advertises: boolean, model = "stub-vision"): VisionPreflight {
  return async () => ({
    provider: "stub",
    model,
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "builtin",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
    advertisesImageInput: advertises,
    modelInputs: advertises ? ["text", "image"] : ["text"],
  });
}

function stubRuntime(model = "stub-vision"): ChildRuntimeReport {
  return {
    provider: "stub",
    model,
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "builtin",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
    advertisesImageInput: true,
    modelInputs: ["text", "image"],
  };
}

// A verdict text a fake invoker can return, echoing the exact dispatch hashes it received.
function verdictText(inv: VisualGateInvocation, overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    schema: "karta-gate-verdict-v1",
    role: "visual-gate",
    evidenceHash: inv.dispatchHash,
    roleDefinitionHash: inv.roleDefinitionHash,
    promptHash: inv.promptHash,
    profileHash: inv.profileHash,
    verdict: "pass",
    summary: "the render matches the design",
    findings: [],
    ...overrides,
  });
}

function recordingInvoker(
  handler: (inv: VisualGateInvocation) => Promise<{ text: string; runtime: ChildRuntimeReport }>,
): { invoke: VisualGateInvoker; calls: VisualGateInvocation[] } {
  const calls: VisualGateInvocation[] = [];
  return {
    calls,
    invoke: async (inv) => {
      calls.push(inv);
      return handler(inv);
    },
  };
}

// ------------------------------------------------------------ vision preflight gate

test("the vision-capability preflight fails closed naming the model when the gate model has no image input, and proceeds when it does", async () => {
  const ctx = makeCtx();

  // Unsupported: fails closed BEFORE any dispatch, remediation names the configured model.
  const unsupported = recordingInvoker(async (inv) => ({ text: verdictText(inv), runtime: stubRuntime() }));
  const blocked = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence: makeEvidence(),
    currency: fixedCurrency(),
    preflight: stubPreflight(false, "text-only-model"),
    invoke: unsupported.invoke,
  });
  assert.equal(blocked.status, "vision-unsupported");
  if (blocked.status !== "vision-unsupported") return;
  assert.equal(blocked.model, "text-only-model");
  assert.match(blocked.remediation, /text-only-model/);
  assert.deepEqual(blocked.modelInputs, ["text"]);
  assert.equal(unsupported.calls.length, 0, "no dispatch happens before the vision preflight passes");

  // Supported: proceeds to dispatch and judges.
  const supported = recordingInvoker(async (inv) => ({ text: verdictText(inv), runtime: stubRuntime() }));
  const judged = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence: makeEvidence(),
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: supported.invoke,
  });
  assert.equal(judged.status, "judged");
  assert.equal(supported.calls.length, 1);
});

// ------------------------------------------------ dispatch envelope (after encoding)

test("the runner dispatches the exact app/design bytes and MIME as ordered images with the structured discrepancies as text grounding", async () => {
  const ctx = makeCtx();
  const evidence = makeEvidence();
  const rec = recordingInvoker(async (inv) => ({ text: verdictText(inv), runtime: stubRuntime() }));
  const result = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence,
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: rec.invoke,
    oracleAssertions: ["zero critical or major discrepancies at 1440x900"],
  });
  assert.equal(result.status, "judged");
  assert.equal(rec.calls.length, 1);

  const inv = rec.calls[0];
  // Ordered, role-tagged image attachments carrying the exact encoded bytes and MIME.
  assert.equal(inv.images.length, 2);
  assert.equal(inv.images[0].role, "app");
  assert.equal(inv.images[0].mimeType, "image/png");
  assert.equal(inv.images[0].data, APP_BYTES.toString("base64"));
  assert.equal(inv.images[1].role, "design");
  assert.equal(inv.images[1].mimeType, "image/png");
  assert.equal(inv.images[1].data, DESIGN_BYTES.toString("base64"));
  // Non-vacuous: the encoded attachments decode back to the exact screenshot bytes.
  assert.deepEqual(Buffer.from(inv.images[0].data, "base64"), APP_BYTES);
  assert.deepEqual(Buffer.from(inv.images[1].data, "base64"), DESIGN_BYTES);
  // The structured discrepancies and render-health travel as text grounding, not images.
  assert.deepEqual(inv.grounding.structuredDiff, evidence.structuredDiff);
  assert.deepEqual(inv.grounding.renderHealth, evidence.renderHealth);
  assert.deepEqual(inv.grounding.oracleAssertions, ["zero critical or major discrepancies at 1440x900"]);
  assert.equal(inv.grounding.route, "/dashboard");
});

// --------------------------------------------------------- strict verdict discipline

test("the runner returns a strict gate-verdict-v1 and rejects prose, wrong-hash, and unknown-key verdicts exactly as the gates do", async () => {
  const ctx = makeCtx();
  const evidence = makeEvidence();

  // Well-formed concerns verdict → judged, with findings and retry classification.
  const ok = recordingInvoker(async (inv) => ({
    text: verdictText(inv, {
      verdict: "concerns",
      summary: "primary color is off palette",
      findings: [{ severity: "major", code: "color.mismatch", message: "primary renders red, design is slate" }],
    }),
    runtime: stubRuntime(),
  }));
  const judged = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence,
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: ok.invoke,
  });
  assert.equal(judged.status, "judged");
  if (judged.status !== "judged") return;
  assert.equal(judged.schema, "karta-gate-verdict-v1");
  assert.equal(judged.verdict, "concerns");
  assert.equal(judged.retry, "retryable");
  assert.equal(judged.findings.length, 1);
  assert.equal(judged.findings[0].code, "color.mismatch");

  // Prose → rejected.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: fixedCurrency(),
      preflight: stubPreflight(true),
      invoke: recordingInvoker(async () => ({ text: "Looks faithful to me!", runtime: stubRuntime() })).invoke,
    }),
    /Malformed Karta gate verdict/,
  );

  // Wrong hash → rejected.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: fixedCurrency(),
      preflight: stubPreflight(true),
      invoke: recordingInvoker(async (inv) => ({
        text: verdictText(inv, { evidenceHash: "0".repeat(64) }),
        runtime: stubRuntime(),
      })).invoke,
    }),
    /evidenceHash does not match/,
  );

  // Unknown key → rejected.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: fixedCurrency(),
      preflight: stubPreflight(true),
      invoke: recordingInvoker(async (inv) => ({
        text: JSON.stringify({ ...JSON.parse(verdictText(inv)), sneaky: true }),
        runtime: stubRuntime(),
      })).invoke,
    }),
    /expected keys/,
  );
});

// ------------------------------------------------------------- currency binds pixels

test("the verdict is currency-bound: changing screenshot bytes, MIME, or the git tree range after dispatch each invalidates it", async () => {
  const ctx = makeCtx();
  const evidence = makeEvidence();

  // Returns the base snapshot before dispatch and a mutated snapshot on the re-verify.
  function movingSource(mutate: (c: VisualCurrency) => VisualCurrency): VisualCurrencySource {
    let call = 0;
    return async () => {
      call += 1;
      return call === 1 ? baseCurrency() : mutate(baseCurrency());
    };
  }
  const invoke = (): VisualGateInvoker =>
    recordingInvoker(async (inv) => ({ text: verdictText(inv), runtime: stubRuntime() })).invoke;

  // (a) screenshot bytes replaced.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: movingSource((c) => ({
        ...c,
        app: { data: Buffer.from("A-DIFFERENT-APP-RENDER"), mimeType: c.app.mimeType },
      })),
      preflight: stubPreflight(true),
      invoke: invoke(),
    }),
    /app screenshot bytes changed/,
  );

  // (b) screenshot MIME altered.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: movingSource((c) => ({ ...c, design: { data: c.design.data, mimeType: "image/jpeg" } })),
      preflight: stubPreflight(true),
      invoke: invoke(),
    }),
    /design screenshot MIME type changed/,
  );

  // (c) git tree range moved.
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence,
      currency: movingSource((c) => ({ ...c, treeRange: { base: c.treeRange.base, target: "f".repeat(40) } })),
      preflight: stubPreflight(true),
      invoke: invoke(),
    }),
    /git tree range moved/,
  );

  // Positive control: unchanged currency judges cleanly, so the check is not vacuously failing.
  const clean = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence,
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: invoke(),
  });
  assert.equal(clean.status, "judged");
});

// ------------------------------------------------------ fail-closed, never retried

test("a dispatch that errors or times out is a typed fail-closed outcome and is never retried", async () => {
  const ctx = makeCtx();
  const evidence = makeEvidence();

  // Error path.
  const errRec = recordingInvoker(async () => {
    throw new Error("gate child crashed");
  });
  const errored = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence,
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: errRec.invoke,
  });
  assert.equal(errored.status, "dispatch-failed");
  if (errored.status !== "dispatch-failed") return;
  assert.equal(errored.reason, "error");
  assert.match(errored.message, /gate child crashed/);
  assert.equal(errRec.calls.length, 1, "the dispatch is attempted exactly once, never retried");

  // Timeout path: the invoker hangs until the internal timeout aborts it.
  const hangRec = recordingInvoker(
    (inv) =>
      new Promise<{ text: string; runtime: ChildRuntimeReport }>((_resolve, reject) => {
        inv.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      }),
  );
  const timedOut = await judgeVisualEvidence(ctx, {
    registry: new ChildRegistry(),
    evidence,
    currency: fixedCurrency(),
    preflight: stubPreflight(true),
    invoke: hangRec.invoke,
    timeoutMs: 25,
  });
  assert.equal(timedOut.status, "dispatch-failed");
  if (timedOut.status !== "dispatch-failed") return;
  assert.equal(timedOut.reason, "timeout");
  assert.equal(hangRec.calls.length, 1, "a timed-out dispatch is not retried");
});

// -------------------------------------------------- runtime drift after preflight

test("a dispatched runtime that is not the model the vision preflight approved is rejected", async () => {
  const ctx = makeCtx();
  await assert.rejects(
    judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence: makeEvidence(),
      currency: fixedCurrency(),
      preflight: stubPreflight(true, "approved-vision-model"),
      invoke: recordingInvoker(async (inv) => ({
        text: verdictText(inv),
        runtime: stubRuntime("a-different-model"),
      })).invoke,
    }),
    /runtime changed after the vision preflight/,
  );
});

// ------------------------------------------------- opt-in live end-to-end judgement

const LIVE = process.env.KARTA_LIVE_VISUAL_JUDGE === "1";
const LIVE_PROVIDER = process.env.KARTA_LIVE_VISUAL_PROVIDER ?? "amorphic";
const LIVE_MODEL = process.env.KARTA_LIVE_VISUAL_MODEL ?? "claude-opus-5";
const LIVE_IMAGE = fileURLToPath(new URL("../../docs/images/icon.png", import.meta.url));

test(
  "live: a real vision-capable gate child judges real screenshots end to end",
  { skip: !LIVE, timeout: 5 * 60_000 },
  async () => {
    const { ModelRegistry, ModelRuntime } = await import("@earendil-works/pi-coding-agent");
    const runtime = await ModelRuntime.create({ allowModelNetwork: false });
    const model = runtime.getModel(LIVE_PROVIDER, LIVE_MODEL);
    assert.ok(model, `missing live vision model ${LIVE_PROVIDER}/${LIVE_MODEL}`);
    assert.ok(model.input?.includes("image"), `${LIVE_PROVIDER}/${LIVE_MODEL} does not advertise image input`);
    const modelRegistry = new ModelRegistry(runtime);
    const ctx = {
      cwd: fileURLToPath(new URL("../..", import.meta.url)),
      model,
      modelRegistry,
      thinkingLevel: "minimal",
    } as unknown as ExtensionContext;

    const bytes = await readFile(LIVE_IMAGE);
    const currency: VisualCurrencySource = async () => ({
      app: { data: bytes, mimeType: "image/png" },
      design: { data: bytes, mimeType: "image/png" },
      treeRange: { base: "0".repeat(40), target: "1".repeat(40) },
    });

    // Real vision preflight (default) and real dispatch (default invoker).
    const outcome = await judgeVisualEvidence(ctx, {
      registry: new ChildRegistry(),
      evidence: makeEvidence(),
      currency,
      timeoutMs: 4 * 60_000,
    });
    assert.equal(outcome.status, "judged", JSON.stringify(outcome));
    if (outcome.status !== "judged") return;
    assert.equal(outcome.schema, "karta-gate-verdict-v1");
    assert.ok(["pass", "concerns", "blocked"].includes(outcome.verdict), `unexpected verdict ${outcome.verdict}`);
    assert.equal(outcome.provider, LIVE_PROVIDER);
    assert.equal(outcome.model, LIVE_MODEL);
  },
);
