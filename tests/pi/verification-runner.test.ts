import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import { promisify } from "node:util";
import {
  ModelRegistry,
  ModelRuntime,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import {
  ChildRegistry,
  GateProviderPreflight,
  type ChildRuntimeReport,
} from "../../extensions/pi/child-runtime.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";
import type { KartaGateRoleId } from "../../extensions/pi/capability-profile.ts";
import type { KartaGateResult } from "../../extensions/pi/gate-runner.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import {
  KartaVerificationRunner,
  type KartaVisualLifecycleContext,
} from "../../extensions/pi/verification-runner.ts";
import {
  VISUAL_EVIDENCE_SCHEMA,
  type VisualCaptureOutcome,
  type VisualEvidence,
} from "../../extensions/pi/visual-capture-runner.ts";
import type {
  VisualGateOutcome,
  VisualGateResult,
} from "../../extensions/pi/visual-gate-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(
  oracle: Record<string, unknown> = { type: "unit" },
  extras: { designReference?: string; designSource?: string | null } = {},
): Promise<{
  repo: string;
  manifest: KartaEvidenceManifest;
  ctx: ExtensionContext;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-verification-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Verification"]);
  await git(repo, ["config", "user.email", "verification@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "fixture"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const workItem: Record<string, unknown> & { id: string } = {
    id: "item-a",
    title: "Fixture",
    summary: "Verify",
    oracle,
    ...(extras.designReference !== undefined ? { design_reference: extras.designReference } : {}),
  };
  const document = {
    slug: "demo",
    work_items: [workItem],
    ...(extras.designSource !== undefined ? { design_facts: { source: extras.designSource } } : {}),
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: tip,
      sha256: "b".repeat(64),
      document,
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: tip,
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: tip,
      mergeBase: tip,
      targetKind: "committed-tip",
      targetTree: tip,
    },
    diff: {
      format: "git-binary-patch",
      sha256: "d".repeat(64),
      bytes: 0,
      touchedPaths: ["subject.txt"],
      content: "",
    },
    checks: { manifest: { status: "not-required", targetTree: tip } },
    files: [],
    citations: [],
    packs: [],
  };
  return {
    repo,
    manifest: {
      schema: "karta-evidence-v2",
      generatedAt: new Date().toISOString(),
      repositoryRoot: repo,
      evidenceHash: hashEvidencePayload(payload),
      payload,
    },
    ctx: { cwd: repo } as ExtensionContext,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

function gate(role: KartaGateRoleId, verdict: "pass" | "concerns" | "blocked"): KartaGateResult {
  return {
    schema: "karta-gate-verdict-v1",
    role,
    evidenceHash: "evidence",
    roleDefinitionHash: "a".repeat(64),
    promptHash: "b".repeat(64),
    profileHash: "c".repeat(64),
    verdict,
    summary: verdict,
    findings: verdict === "concerns" ? [{ severity: "major", code: "fixture", message: "finding" }] : [],
    retry: verdict === "pass" ? "none" : verdict === "concerns" ? "retryable" : "halt",
    provider: "fixture",
    model: "fixture",
  };
}

const preflight = { async ensure() { throw new Error("fake gate executor should own preflight"); } };

function visionReport(advertises: boolean): ChildRuntimeReport {
  return {
    provider: "fixture",
    model: "fixture",
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "builtin",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
    advertisesImageInput: advertises,
    modelInputs: advertises ? ["text", "image"] : ["text"],
  };
}

function fakeVisualEvidence(): VisualEvidence {
  const health = {
    result: "healthy" as const,
    readySelector: null,
    consoleErrorCount: 0,
    failedRequestCount: 0,
  };
  const target = {
    url: "http://127.0.0.1:9/",
    health: "OK",
    render_health: null,
    extracted_data: null,
    screenshot: "/tmp/karta-visual/app.png",
  };
  return {
    schema: VISUAL_EVIDENCE_SCHEMA,
    binder: "demo",
    item: "item-a",
    route: "/dashboard",
    designReference: "design/view.html",
    candidateCommit: "a".repeat(40),
    candidateTree: "b".repeat(40),
    generatedAt: new Date().toISOString(),
    captures: { design: { ...target }, app: { ...target } },
    renderHealth: { design: health, app: health },
    structuredDiff: {
      schema: "karta-structured-diff-v1",
      status: "ok",
      blockedReason: null,
      renderHealth: null,
      summary: {
        discrepancyCount: 0,
        tokenDriftCount: 0,
        missingCount: 0,
        extraCount: 0,
        byDimension: {},
      },
      discrepancies: [],
      tokenDrift: [],
      missingElements: [],
      extraElements: [],
    },
  };
}

function capturedOutcome(): VisualCaptureOutcome {
  const evidence = fakeVisualEvidence();
  return {
    status: "captured",
    evidencePath: "/tmp/karta-visual/visual-evidence.json",
    evidence,
    candidateTree: evidence.candidateTree,
  };
}

function visualGate(verdict: "pass" | "concerns" | "blocked"): VisualGateResult {
  return {
    status: "judged",
    schema: "karta-gate-verdict-v1",
    role: "visual-gate",
    dispatchHash: "d".repeat(64),
    verdict,
    summary: verdict,
    findings:
      verdict === "concerns"
        ? [{ severity: "major", code: "fidelity", message: "the render drifts from the design" }]
        : [],
    retry: verdict === "pass" ? "none" : verdict === "concerns" ? "retryable" : "halt",
    provider: "fixture",
    model: "fixture",
  };
}

// A real lifecycle owner. The capture/gate seams are injected in these tests, so the owner
// is only threaded, never used to spawn a process.
function makeVisualContext(worktree: string): {
  context: KartaVisualLifecycleContext;
  cleanup(): Promise<void>;
} {
  const manager = new KartaProcessManager(new LifecycleRegistry());
  const owner = manager.createBinderOwner(worktree, "demo");
  return {
    context: { processes: { manager, owner }, worktree, treeish: "a".repeat(40) },
    cleanup: () => manager.stopOwner(owner),
  };
}

const DESIGN = { designReference: "/dashboard", designSource: "design/view.html" };

test("full verification runs acceptance then safety under one lock and one evidence hash", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      async buildEvidence() {
        assert.equal(locks.size, 1);
        return manifest;
      },
      async executeGate(_ctx, role, evidence) {
        assert.equal(locks.size, 1);
        assert.equal(evidence, manifest);
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: evidence.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["acceptance-gate", "safety-gate"]);
    assert.equal(result.status, "pass");
    assert.equal(result.evidenceHash, manifest.evidenceHash);
    assert.equal(result.gates.acceptance?.evidenceHash, result.gates.safety?.evidenceHash);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("delivery-owned verification reuses an explicit lease without reacquiring or releasing it", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  try {
    const lease = await locks.acquire(ctx.cwd, "demo");
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.runWithLease(ctx, "demo", "item-a", "boundary-only", lease);
    assert.equal(result.status, "pass");
    assert.equal(locks.size, 1);
    await locks.release(lease);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("acceptance concern stops before safety and releases the lock", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "concerns"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["acceptance-gate"]);
    assert.equal(result.status, "concerns");
    assert.equal(result.gates.safety, undefined);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("an explicit boundary-only request dispatches safety alone and returns its verdict", async () => {
  const { manifest, ctx, cleanup } = await fixture({ type: "unit" });
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "boundary-only");
    assert.deepEqual(roles, ["safety-gate"]);
    assert.equal(result.requestedMode, "boundary-only");
    assert.equal(result.effectiveMode, "boundary-only");
    assert.equal(result.status, "pass");
    assert.equal(result.blockedReason, undefined);
    assert.equal(result.gates.acceptance, undefined);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("the context-less run() path blocks a visual oracle visual-no-context after safety, with no acceptance gate", async () => {
  // karta_dispatch runVerification and the index.ts construction call run() with no
  // lifecycle owner. A visual oracle runs the boundary safety gate, then blocks
  // fail-closed as visual-no-context — moving no ref. It never dispatches acceptance.
  const { manifest, ctx, cleanup } = await fixture({ type: "visual" }, DESIGN);
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
      visionPreflight: async () => {
        throw new Error("the context-less path must not reach the vision preflight");
      },
      captureVisual: async () => {
        throw new Error("the context-less path must not attempt capture");
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["safety-gate"]);
    assert.equal(result.status, "blocked");
    assert.equal(result.blockedReason, "visual-no-context");
    assert.equal(result.reason, undefined);
    assert.equal(result.requestedMode, "full");
    assert.equal(result.effectiveMode, "full");
    assert.equal(result.gates.acceptance, undefined);
    assert.equal(result.gates.visual, undefined);
    assert.equal(result.gates.safety?.verdict, "pass");
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("a full visual verification surfaces a safety failure rather than folding it into a visual reason", async () => {
  for (const verdict of ["concerns", "blocked"] as const) {
    const { repo, manifest, ctx, cleanup } = await fixture({ type: "visual" }, DESIGN);
    const locks = new DispatchLockManager();
    const { context, cleanup: cleanupContext } = makeVisualContext(repo);
    const roles: string[] = [];
    try {
      const lease = await locks.acquire(ctx.cwd, "demo");
      const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
        buildEvidence: async () => manifest,
        async executeGate(_ctx, role) {
          roles.push(role);
          return { ...gate(role, verdict), evidenceHash: manifest.evidenceHash };
        },
        visionPreflight: async () => {
          throw new Error("a non-pass safety verdict must short-circuit before the visual path");
        },
      });
      const result = await runner.runWithLease(ctx, "demo", "item-a", "full", lease, {}, context);
      // Only safety ran; the visual path (and its preflight) is never reached.
      assert.deepEqual(roles, ["safety-gate"]);
      assert.equal(result.status, verdict);
      assert.equal(result.blockedReason, undefined);
      assert.equal(result.gates.visual, undefined);
      assert.equal(result.effectiveMode, "full");
      await locks.release(lease);
    } finally {
      await cleanupContext();
      await locks.releaseAll();
      await cleanup();
    }
  }
});

test("a genuine visual pass lifts the block: status pass, verdict recorded in gates.visual, no acceptance gate", async () => {
  const { repo, manifest, ctx, cleanup } = await fixture({ type: "visual" }, DESIGN);
  const locks = new DispatchLockManager();
  const { context, cleanup: cleanupContext } = makeVisualContext(repo);
  const roles: string[] = [];
  let captured = 0;
  try {
    const lease = await locks.acquire(ctx.cwd, "demo");
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
      visionPreflight: async () => visionReport(true),
      captureVisual: async () => {
        captured += 1;
        return capturedOutcome();
      },
      judgeVisual: async () => visualGate("pass"),
    });
    const result = await runner.runWithLease(ctx, "demo", "item-a", "full", lease, {}, context);
    assert.deepEqual(roles, ["safety-gate"]);
    assert.equal(captured, 1);
    assert.equal(result.status, "pass");
    assert.equal(result.blockedReason, undefined);
    assert.equal(result.gates.acceptance, undefined);
    assert.equal(result.gates.safety?.verdict, "pass");
    assert.equal(result.gates.visual?.verdict, "pass");
    assert.equal(result.gates.visual?.role, "visual-gate");
    await locks.release(lease);
  } finally {
    await cleanupContext();
    await locks.releaseAll();
    await cleanup();
  }
});

test("a visual concern returns concerns and records gates.visual for the acceptance-cap retry", async () => {
  const { repo, manifest, ctx, cleanup } = await fixture({ type: "visual" }, DESIGN);
  const locks = new DispatchLockManager();
  const { context, cleanup: cleanupContext } = makeVisualContext(repo);
  try {
    const lease = await locks.acquire(ctx.cwd, "demo");
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
      visionPreflight: async () => visionReport(true),
      captureVisual: async () => capturedOutcome(),
      judgeVisual: async () => visualGate("concerns"),
    });
    const result = await runner.runWithLease(ctx, "demo", "item-a", "full", lease, {}, context);
    assert.equal(result.status, "concerns");
    assert.equal(result.blockedReason, undefined);
    assert.equal(result.gates.visual?.verdict, "concerns");
    assert.equal(result.gates.visual?.findings.length, 1);
    await locks.release(lease);
  } finally {
    await cleanupContext();
    await locks.releaseAll();
    await cleanup();
  }
});

test("every unmet visual precondition fails closed with its own distinct typed reason and no lift", async () => {
  const captureThrows = async (): Promise<VisualCaptureOutcome> => {
    throw new Error("playwright-cli is not available on PATH");
  };
  const cases: Array<{
    name: string;
    reason: string;
    design: { designReference?: string; designSource?: string | null };
    vision: () => Promise<ChildRuntimeReport>;
    capture: () => Promise<VisualCaptureOutcome>;
    judge: () => Promise<VisualGateOutcome>;
    captureRuns: boolean;
    visualRecorded: boolean;
  }> = [
    {
      name: "visual-no-vision-model (no capture attempted)",
      reason: "visual-no-vision-model",
      design: DESIGN,
      vision: async () => visionReport(false),
      capture: captureThrows,
      judge: async () => visualGate("pass"),
      captureRuns: false,
      visualRecorded: false,
    },
    {
      name: "visual-no-design (design_reference none and design_facts.source null)",
      reason: "visual-no-design",
      design: { designReference: "none", designSource: null },
      vision: async () => visionReport(true),
      capture: captureThrows,
      judge: async () => visualGate("pass"),
      captureRuns: false,
      visualRecorded: false,
    },
    {
      name: "visual-no-env",
      reason: "visual-no-env",
      design: DESIGN,
      vision: async () => visionReport(true),
      capture: async () => ({ status: "no-visual-env", reason: "no visual_env at the candidate" }),
      judge: async () => visualGate("pass"),
      captureRuns: true,
      visualRecorded: false,
    },
    {
      name: "visual-capture-failed (env-server startup-crash)",
      reason: "visual-capture-failed",
      design: DESIGN,
      vision: async () => visionReport(true),
      capture: async () => ({
        status: "startup-crash",
        exitCode: 7,
        tail: "boom",
        remediation: "the dev server exited during startup",
      }),
      judge: async () => visualGate("pass"),
      captureRuns: true,
      visualRecorded: false,
    },
    {
      name: "visual-capture-failed (playwright-cli absent throws)",
      reason: "visual-capture-failed",
      design: DESIGN,
      vision: async () => visionReport(true),
      capture: captureThrows,
      judge: async () => visualGate("pass"),
      captureRuns: true,
      visualRecorded: false,
    },
    {
      name: "visual-gate-error (dispatch-failed)",
      reason: "visual-gate-error",
      design: DESIGN,
      vision: async () => visionReport(true),
      capture: async () => capturedOutcome(),
      judge: async () => ({
        status: "dispatch-failed",
        reason: "timeout",
        message: "the visual gate dispatch exceeded its time budget",
        remediation: "rerun; the host decides",
      }),
      captureRuns: true,
      visualRecorded: false,
    },
    {
      name: "visual-gate-error (unjudgeable evidence, verdict blocked)",
      reason: "visual-gate-error",
      design: DESIGN,
      vision: async () => visionReport(true),
      capture: async () => capturedOutcome(),
      judge: async () => visualGate("blocked"),
      captureRuns: true,
      visualRecorded: true,
    },
  ];
  for (const item of cases) {
    const { repo, manifest, ctx, cleanup } = await fixture({ type: "visual" }, item.design);
    const locks = new DispatchLockManager();
    const { context, cleanup: cleanupContext } = makeVisualContext(repo);
    let captureCalls = 0;
    try {
      const lease = await locks.acquire(ctx.cwd, "demo");
      const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
        buildEvidence: async () => manifest,
        async executeGate(_ctx, role) {
          return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
        },
        visionPreflight: item.vision,
        captureVisual: async () => {
          captureCalls += 1;
          return item.capture();
        },
        judgeVisual: async () => item.judge(),
      });
      const result = await runner.runWithLease(ctx, "demo", "item-a", "full", lease, {}, context);
      assert.equal(result.status, "blocked", item.name);
      assert.equal(result.blockedReason, item.reason, item.name);
      assert.equal(captureCalls, item.captureRuns ? 1 : 0, `${item.name}: capture attempts`);
      assert.equal(
        result.gates.visual !== undefined,
        item.visualRecorded,
        `${item.name}: gates.visual recorded`,
      );
      // A blocked prerequisite never records a pass or moves a ref: safety passed, but the
      // overall status is blocked with the typed reason, never pass.
      assert.equal(result.gates.safety?.verdict, "pass", item.name);
      await locks.release(lease);
    } finally {
      await cleanupContext();
      await locks.releaseAll();
      await cleanup();
    }
  }
});

test("oracle opt-out skips both gates without turning Pi into state storage", async () => {
  const { manifest, ctx, cleanup } = await fixture({ opt_out: true, reason: "external certification" });
  const locks = new DispatchLockManager();
  let gates = 0;
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate() {
        gates += 1;
        throw new Error("must not dispatch");
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.equal(result.status, "skipped");
    assert.match(result.reason ?? "", /external certification/);
    assert.equal(gates, 0);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("evidence failure releases the dispatch lock", async () => {
  const { ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      async buildEvidence() {
        throw new Error("bad evidence");
      },
    });
    await assert.rejects(() => runner.run(ctx, "demo", "item-a", "full"), /bad evidence/);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

// ------------------------------------------------ opt-in live end-to-end acceptance

const LIVE_ACCEPTANCE = process.env.KARTA_LIVE_VISUAL_ACCEPTANCE === "1";
const LIVE_PROVIDER = process.env.KARTA_LIVE_VISUAL_PROVIDER ?? "amorphic";
const LIVE_MODEL = process.env.KARTA_LIVE_VISUAL_MODEL ?? "claude-opus-5";

// Drive a real visual oracle to a genuine pass end to end: real evidence, the real safety
// gate, the real vision preflight, the real pi-visual-capture orchestrator (dev server +
// playwright-cli), and the real visual gate judging live screenshots against the design.
// Skipped by the default floor — it needs a vision-capable model, playwright-cli, and uv.
test(
  "live: a real visual oracle drives vision preflight, capture, and the visual gate to a genuine pass",
  { skip: !LIVE_ACCEPTANCE, timeout: 6 * 60_000 },
  async (context: TestContext) => {
    if (process.platform === "win32") {
      context.skip("POSIX process-group lifecycle has a native Windows fixture");
      return;
    }
    const runtime = await ModelRuntime.create({ allowModelNetwork: false });
    const model = runtime.getModel(LIVE_PROVIDER, LIVE_MODEL);
    assert.ok(model, `missing live vision model ${LIVE_PROVIDER}/${LIVE_MODEL}`);
    assert.ok(model.input.includes("image"), `${LIVE_PROVIDER}/${LIVE_MODEL} advertises no image input`);
    const modelRegistry = new ModelRegistry(runtime);

    const root = await mkdtemp(join(tmpdir(), "karta-live-visual-acceptance-"));
    const repo = join(root, "repo");
    const design = join(repo, "design");
    await mkdir(join(repo, ".karta", "binders"), { recursive: true });
    await mkdir(design, { recursive: true });
    const view =
      '<main><h1 id="t">Team dashboard</h1><button>New</button></main>';
    await writeFile(
      join(design, "view.standalone.html"),
      `<!doctype html><title>Design</title>${view}`,
    );
    await writeFile(
      join(repo, "app.mjs"),
      [
        'import http from "node:http";',
        "const port = Number(process.env.APP_PORT);",
        "http.createServer((_req, res) => {",
        '  res.writeHead(200, { "content-type": "text/html" });',
        `  res.end('<!doctype html><title>App</title>${view}');`,
        '}).listen(port, "127.0.0.1", () => process.stdout.write("up\\n"));',
        'process.on("SIGTERM", () => process.exit(0));',
        "",
      ].join("\n"),
    );
    await writeFile(
      join(repo, ".karta", "environment.json"),
      JSON.stringify(
        {
          visual_env: {
            command: "node app.mjs",
            port_param: "APP_PORT",
            startup_timeout_seconds: 30,
            auth: "none",
          },
        },
        null,
        2,
      ),
    );
    const binder = {
      slug: "demo",
      title: "Live visual acceptance",
      summary: "Drive a real visual pass end to end",
      motivation: "Prove the visual acceptance wiring against a real render",
      scope: { included: ["subject.txt"] },
      design_facts: { source: design },
      work_items: [
        {
          id: "item-a",
          title: "Render the dashboard",
          summary: "Render the dashboard view",
          touches: ["subject.txt"],
          design_reference: "/",
          oracle: { type: "visual", assertions: ["the dashboard render matches the design"] },
        },
      ],
    };
    await writeFile(
      join(repo, ".karta", "binders", "demo.json"),
      `${JSON.stringify(binder, null, 2)}\n`,
    );
    await writeFile(join(repo, "subject.txt"), "base\n");
    await git(repo, ["init", "--initial-branch=main"]);
    await git(repo, ["config", "user.name", "Karta Live"]);
    await git(repo, ["config", "user.email", "live@invalid.example"]);
    await git(repo, ["config", "commit.gpgSign", "false"]);
    await git(repo, ["add", "."]);
    await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
    await git(repo, ["branch", "karta/demo/integration"]);
    await git(repo, ["checkout", "-b", "karta/demo/item-item-a"]);
    await writeFile(join(repo, "subject.txt"), "candidate\n");
    await git(repo, ["commit", "--no-gpg-sign", "-am", "[karta:item-item-a] candidate"]);
    const itemTip = await git(repo, ["rev-parse", "HEAD"]);

    const ctx = {
      cwd: repo,
      model,
      modelRegistry,
      thinkingLevel: "minimal",
    } as unknown as ExtensionContext;
    const locks = new DispatchLockManager();
    const children = new ChildRegistry();
    const manager = new KartaProcessManager(new LifecycleRegistry());
    const owner = manager.createBinderOwner(repo, "demo");
    const runner = new KartaVerificationRunner(new GateProviderPreflight(), children, locks);
    const lease = await locks.acquire(repo, "demo");
    try {
      const result = await runner.runWithLease(
        ctx,
        "demo",
        "item-a",
        "full",
        lease,
        { cwd: repo, target: "committed" },
        { processes: { manager, owner }, worktree: repo, treeish: itemTip },
      );
      assert.equal(result.status, "pass", JSON.stringify(result));
      assert.equal(result.blockedReason, undefined);
      assert.equal(result.gates.acceptance, undefined);
      assert.equal(result.gates.safety?.verdict, "pass");
      assert.equal(result.gates.visual?.verdict, "pass");
    } finally {
      await locks.release(lease);
      await manager.stopOwner(owner);
      await children.abortAll();
      await rm(root, { recursive: true, force: true });
    }
  },
);
