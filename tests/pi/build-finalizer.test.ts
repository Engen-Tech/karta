import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { KartaBuildFinalizer } from "../../extensions/pi/build-finalizer.ts";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import type { GateModelInvoker } from "../../extensions/pi/gate-runner.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import { KartaVerificationRunner } from "../../extensions/pi/verification-runner.ts";

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

async function fixture(invoker = gateInvoker()): Promise<{
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
    work_items: [
      {
        id: "item-a",
        title: "Change subject",
        summary: "Change the subject",
        touches: ["subject.txt"],
        oracle: {
          type: "unit",
          assertions: ["subject contains candidate"],
          command: "node check.mjs",
        },
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
    { invoke: invoker },
  );
  return {
    repo,
    locks,
    finalizer: new KartaBuildFinalizer(locks, verification),
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
