import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  KartaBuildFinalizer,
  type KartaFinalizationCheckpoint,
} from "../../extensions/pi/build-finalizer.ts";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import type { GateModelInvoker } from "../../extensions/pi/gate-runner.ts";
import { deriveItemGitState } from "../../extensions/pi/git-state.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import {
  KartaVerificationRunner,
  type VisualAcceptanceJudge,
  type VisualCaptureRunner,
  type VisionPreflightProbe,
} from "../../extensions/pi/verification-runner.ts";
import {
  VISUAL_EVIDENCE_SCHEMA,
  type VisualCaptureOutcome,
  type VisualEvidence,
} from "../../extensions/pi/visual-capture-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

const runtime: ChildRuntimeReport = {
  provider: "fixture",
  model: "fixture",
  policy: "gate",
  exactModelResolved: true,
  parentAuthConfigured: true,
  childAuthConfigured: true,
  copiedProvider: "builtin",
  copiedRuntimeCredential: false,
  unresolvedEnvironmentKeys: [],
};

function gateInvoker(verdict: "pass" | "concerns" = "pass"): GateModelInvoker {
  return async (invocation) => {
    const evidence = invocation.profile.tools[0];
    for (const params of [
      { action: "summary" },
      { action: "workItem" },
      { action: "diff" },
    ]) {
      await evidence.execute("evidence", params, undefined, undefined, invocation.ctx);
    }
    const roleTool = invocation.profile.tools[1];
    await roleTool.execute(
      "role",
      { action: invocation.profile.role.id === "acceptance-gate" ? "summary" : "inspect" },
      undefined,
      undefined,
      invocation.ctx,
    );
    const promptHash = invocation.systemPrompt.match(/"promptHash":"([a-f0-9]{64})"/)?.[1];
    assert.ok(promptHash);
    return {
      runtime,
      text: JSON.stringify({
        schema: "karta-gate-verdict-v1",
        role: invocation.profile.role.id,
        evidenceHash: invocation.profile.evidenceHash,
        roleDefinitionHash: invocation.profile.role.definitionHash,
        promptHash,
        profileHash: invocation.profile.profileHash,
        verdict,
        summary: verdict === "pass" ? "Candidate conforms." : "Candidate needs another pass.",
        findings:
          verdict === "pass"
            ? []
            : [
                {
                  severity: "major",
                  code: "candidate-needs-fix",
                  message: "Adjust the candidate before committing.",
                  path: "subject.txt",
                  line: 1,
                },
              ],
      }),
    };
  };
}

const visionReport = (advertises: boolean): ChildRuntimeReport => ({
  ...runtime,
  advertisesImageInput: advertises,
  modelInputs: advertises ? ["text", "image"] : ["text"],
});

function capturedVisualOutcome(): VisualCaptureOutcome {
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
  const evidence: VisualEvidence = {
    schema: VISUAL_EVIDENCE_SCHEMA,
    binder: "demo",
    item: "item-a",
    route: "/view-x",
    designReference: "design/view.html",
    candidateCommit: "a".repeat(40),
    candidateTree: "b".repeat(40),
    generatedAt: new Date().toISOString(),
    captures: { design: { ...target }, app: { ...target } },
    renderHealth: { design: health, app: health },
    structuredDiff: {
      schema: "karta-structured-diff-v1",
      status: "ok" as const,
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
  return {
    status: "captured",
    evidencePath: "/tmp/karta-visual/visual-evidence.json",
    evidence,
    candidateTree: evidence.candidateTree,
  };
}

interface VisualSeams {
  visionPreflight?: VisionPreflightProbe;
  captureVisual?: VisualCaptureRunner;
  judgeVisual?: VisualAcceptanceJudge;
}

async function fixture(
  invoker = gateInvoker(),
  checkpoint: KartaFinalizationCheckpoint = () => {},
  oracle: Record<string, unknown> = {
    type: "unit",
    assertions: ["subject contains candidate"],
    command: "node check.mjs",
  },
  visual: {
    designReference?: string;
    designSource?: string;
    seams?: VisualSeams;
  } = {},
): Promise<{
  repo: string;
  locks: DispatchLockManager;
  finalizer: KartaBuildFinalizer;
  ctx: ExtensionContext;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-build-finalizer-"));
  const repo = join(root, "repo");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  const binder = {
    slug: "demo",
    title: "Finalizer fixture",
    summary: "Finalize a candidate",
    motivation: "Prove exact tree sequencing",
    scope: { included: ["subject.txt"] },
    ...(visual.designSource !== undefined
      ? { design_facts: { source: visual.designSource } }
      : {}),
    work_items: [
      {
        id: "item-a",
        title: "Change subject",
        summary: "Change the subject",
        touches: ["subject.txt"],
        ...(visual.designReference !== undefined
          ? { design_reference: visual.designReference }
          : {}),
        oracle,
      },
    ],
  };
  await writeFile(join(repo, ".karta", "binders", "demo.json"), `${JSON.stringify(binder, null, 2)}\n`);
  await writeFile(
    join(repo, "check.mjs"),
    "import { readFileSync } from 'node:fs'; if (readFileSync('subject.txt', 'utf8') !== 'candidate\\n') process.exit(9);\n",
  );
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Finalizer"]);
  await git(repo, ["config", "user.email", "finalizer@invalid.example"]);
  await git(repo, ["config", "core.hooksPath", join(repo, ".git", "hooks")]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  await git(repo, ["checkout", "-b", "karta/demo/item-item-a"]);
  const locks = new DispatchLockManager();
  const verification = new KartaVerificationRunner(
    { async ensure() { return { ...runtime, cached: false }; } },
    new ChildRegistry(),
    locks,
    { invoke: invoker, ...visual.seams },
  );
  return {
    repo,
    locks,
    finalizer: new KartaBuildFinalizer(locks, verification, checkpoint),
    ctx: { cwd: repo } as ExtensionContext,
    async cleanup() {
      await locks.releaseAll();
      await rm(root, { recursive: true, force: true });
    },
  };
}

test("finalizer scans, checks, gates, commits, then writes built ref", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const processManager = new KartaProcessManager(new LifecycleRegistry(), 10);
    const owner = processManager.createBinderOwner(state.repo, "demo");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
      [],
      { manager: processManager, owner },
    );
    assert.equal(processManager.size, 0);
    await processManager.stopOwner(owner);
    assert.equal(result.status, "built");
    assert.equal(result.checks?.entries[0].receipt.status, "passed");
    assert.equal(await git(state.repo, ["rev-parse", "HEAD^{tree}"]), result.targetTree);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/built"]),
      result.commit,
    );
    assert.match(await git(state.repo, ["log", "-1", "--format=%s"]), /^\[karta:item-item-a\]/);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("a failing environment preflight blocks the item with remediation and never runs the floor", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    // Put a failing precondition probe on the integration ref the finalizer reads from.
    await git(state.repo, ["checkout", "karta/demo/integration"]);
    await mkdir(join(state.repo, ".karta"), { recursive: true });
    await writeFile(
      join(state.repo, ".karta", "environment.json"),
      JSON.stringify({
        preflight: "exit 4",
        on_unavailable: "Bring up Docker via Incus; CI has it natively.",
      }),
    );
    await git(state.repo, ["add", "-A"]);
    await git(state.repo, ["commit", "--no-gpg-sign", "-m", "env preflight"]);
    await git(state.repo, ["checkout", "karta/demo/item-item-a"]);

    const before = await git(state.repo, ["rev-parse", "HEAD"]);
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(result.status, "blocked");
    assert.equal(result.checkFailure?.status, "precondition-unmet");
    assert.match(result.message, /precondition unmet/i);
    assert.match(result.message, /Bring up Docker via Incus/);
    // The floor never committed anything: HEAD is unchanged and neither completion
    // ref exists — the probe halted before the real command hit the wall.
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/failed"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("a full visual oracle with a lifecycle context and a genuine pass lifts the block and writes the built ref", async () => {
  let captured = 0;
  const state = await fixture(
    gateInvoker(),
    () => {},
    { type: "visual", assertions: ["subject contains candidate"], command: "node check.mjs" },
    {
      designReference: "view-x",
      designSource: "design/view.html",
      seams: {
        visionPreflight: async () => visionReport(true),
        captureVisual: async () => {
          captured += 1;
          return capturedVisualOutcome();
        },
        judgeVisual: async () => ({
          status: "judged",
          schema: "karta-gate-verdict-v1",
          role: "visual-gate",
          dispatchHash: "d".repeat(64),
          verdict: "pass",
          summary: "the render matches the design",
          findings: [],
          retry: "none",
          provider: "fixture",
          model: "fixture",
        }),
      },
    },
  );
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const processManager = new KartaProcessManager(new LifecycleRegistry(), 10);
    const owner = processManager.createBinderOwner(state.repo, "demo");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
      [],
      { manager: processManager, owner },
    );
    await processManager.stopOwner(owner);
    assert.equal(captured, 1, "the capture orchestrator ran");
    assert.equal(result.status, "built");
    assert.equal(result.verification?.status, "pass");
    assert.equal(result.verification?.blockedReason, undefined);
    assert.equal(result.verification?.gates.acceptance, undefined);
    assert.equal(result.verification?.gates.safety?.verdict, "pass");
    assert.equal(result.verification?.gates.visual?.verdict, "pass");
    assert.equal(await git(state.repo, ["rev-parse", "HEAD^{tree}"]), result.targetTree);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/built"]),
      result.commit,
    );
    assert.match(await git(state.repo, ["log", "-1", "--format=%s"]), /^\[karta:item-item-a\]/);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("a full visual oracle blocks fail-closed at an unmet precondition and writes no ref", async () => {
  // No design_facts.source and no design_reference: the vision preflight passes, then the
  // visual acceptance path resolves no view and blocks visual-no-design after safety. The
  // consumer holds by default with an actionable message and writes no completion ref — no
  // fall-through moves a ref, and capture is never attempted.
  const state = await fixture(
    gateInvoker(),
    () => {},
    { type: "visual", assertions: ["subject contains candidate"], command: "node check.mjs" },
    {
      seams: {
        visionPreflight: async () => visionReport(true),
        captureVisual: async () => {
          throw new Error("capture must not run when no view resolves");
        },
      },
    },
  );
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    const before = await git(state.repo, ["rev-parse", "HEAD"]);
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const processManager = new KartaProcessManager(new LifecycleRegistry(), 10);
    const owner = processManager.createBinderOwner(state.repo, "demo");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
      [],
      { manager: processManager, owner },
    );
    await processManager.stopOwner(owner);
    assert.equal(result.status, "blocked");
    assert.equal(result.verification?.status, "blocked");
    assert.equal(result.verification?.blockedReason, "visual-no-design");
    assert.equal(result.verification?.gates.acceptance, undefined);
    assert.equal(result.verification?.gates.safety?.verdict, "pass");
    assert.match(result.message, /design_reference|design_facts\.source/);
    assert.doesNotMatch(result.message, /until visual acceptance lands/);
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/failed"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("committed-unmarked recovery reruns checks, gates, hooks, and writes built ref-last", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const built = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(built.status, "built");
    await git(state.repo, ["update-ref", "-d", "refs/karta/demo/item-item-a/built"]);
    const recovered = await state.finalizer.recoverCommittedCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(recovered.status, "built");
    assert.equal(recovered.commit, built.commit);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/built"]),
      built.commit,
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("committed recovery scans the exact committed range rather than an empty index", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    await writeFile(
      join(state.repo, "secret.txt"),
      `token = ghp_${"a".repeat(36)}\n`,
    );
    await git(state.repo, ["add", "."]);
    await git(state.repo, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] committed"]);
    await assert.rejects(
      () => state.finalizer.recoverCommittedCandidate(
        state.ctx,
        "demo",
        "item-a",
        state.repo,
        lease,
      ),
      /SECRET SCAN: BLOCKED|github-token/,
    );
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("merged-unmarked recovery validates the landed merge before done ref-last", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const built = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(built.status, "built");
    await git(state.repo, ["checkout", "karta/demo/integration"]);
    await git(state.repo, [
      "merge",
      "--no-ff",
      "--no-gpg-sign",
      "-m",
      "merge item-a",
      "karta/demo/item-item-a",
    ]);
    const merge = await git(state.repo, ["rev-parse", "HEAD"]);
    const recovered = await state.finalizer.recoverMergedCandidate(
      { ...state.ctx, cwd: state.repo },
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(recovered.status, "built");
    assert.equal(recovered.commit, merge);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/done"]),
      merge,
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("deterministic crash after branch movement recovers from committed-unmarked", async () => {
  let inject = true;
  const state = await fixture(gateInvoker(), (name) => {
    if (inject && name === "item-branch-updated") throw new Error("injected branch crash");
  });
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    await assert.rejects(
      () => state.finalizer.finalizeCandidate(state.ctx, "demo", "item-a", state.repo, lease),
      /injected branch crash/,
    );
    const interrupted = await deriveItemGitState(state.repo, "demo", "item-a");
    assert.equal(interrupted.state, "committed-unmarked");
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
    inject = false;
    const recovered = await state.finalizer.recoverCommittedCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(recovered.status, "built");
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("hook-induced tree drift blocks the real commit and built ref", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    const before = await git(state.repo, ["rev-parse", "HEAD"]);
    const hook = join(state.repo, ".git", "hooks", "pre-commit");
    await writeFile(hook, "#!/bin/sh\nprintf 'hooked\\n' > hook.txt\ngit add hook.txt\n");
    await chmod(hook, 0o755);
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(result.status, "blocked");
    assert.equal(result.hookValidation?.status, "drifted");
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("retryable gate findings preserve the staged candidate without refs", async () => {
  const state = await fixture(gateInvoker("concerns"));
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    const before = await git(state.repo, ["rev-parse", "HEAD"]);
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const result = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(result.status, "retry");
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
    assert.notEqual(await git(state.repo, ["write-tree"]), await git(state.repo, ["rev-parse", "HEAD^{tree}"]));
    await assert.rejects(() => git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]));
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("a gate-capped candidate is committed exactly before failed ref-last", async () => {
  const state = await fixture(gateInvoker("concerns"));
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    await writeFile(join(state.repo, "subject.txt"), "candidate\n");
    const retry = await state.finalizer.finalizeCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
    );
    assert.equal(retry.status, "retry");
    const failed = await state.finalizer.recordFailedCandidate(
      state.ctx,
      "demo",
      "item-a",
      state.repo,
      lease,
      retry,
    );
    assert.equal(failed.status, "failed");
    assert.equal(await git(state.repo, ["rev-parse", "HEAD^{tree}"]), retry.targetTree);
    assert.equal(
      await git(state.repo, ["rev-parse", "refs/karta/demo/item-item-a/failed"]),
      failed.commit,
    );
    await assert.rejects(() =>
      git(state.repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("protected orchestration changes fail before checks or gates", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.repo, "demo");
  try {
    const path = join(state.repo, ".karta", "binders", "demo.json");
    await writeFile(path, `${await git(state.repo, ["show", "HEAD:.karta/binders/demo.json"])} `);
    await assert.rejects(
      () => state.finalizer.finalizeCandidate(state.ctx, "demo", "item-a", state.repo, lease),
      /protected orchestration state/,
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});
