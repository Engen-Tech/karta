import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { KartaBuildFinalizer } from "../../extensions/pi/build-finalizer.ts";
import {
  KartaBuildItemRunner,
  type KartaBuildCheckpoint,
} from "../../extensions/pi/build-runner.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import type { KartaBuildWorkerRunner, KartaWorkerResult } from "../../extensions/pi/worker-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return stdout.trim();
}

async function fixture(): Promise<{ repo: string; root: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-build-runner-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Test"]);
  await git(repo, ["config", "user.email", "karta@example.invalid"]);
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(
    join(repo, ".karta", "binders", "demo.json"),
    `${JSON.stringify({
      version: 1,
      slug: "demo",
      title: "Demo",
      description: "Demo",
      created: "2026-08-05",
      status: "committed",
      branch: "karta/demo/integration",
      work_items: [{
        id: "item-a",
        title: "Change subject",
        description: "Change it",
        files: ["subject.txt"],
        depends_on: [],
        oracle: { type: "unit", command: "true", assertions: ["changed"] },
      }],
    })}\n`,
  );
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  return { repo, root, cleanup: () => rm(root, { recursive: true, force: true }) };
}

function workerResult(binder: string, item: string, summary = "ready"): KartaWorkerResult {
  const snapshot = {
    schema: "karta-worker-authority-snapshot-v1" as const,
    worktree: "/fixture",
    branch: `karta/${binder}/item-${item}`,
    head: "a".repeat(40),
    index: "b".repeat(64),
    refs: "c".repeat(64),
    config: "d".repeat(64),
    hooks: "e".repeat(64),
    worktrees: "f".repeat(64),
    protectedPaths: "1".repeat(64),
    siblings: "2".repeat(64),
  };
  return {
    schema: "karta-worker-result-v2",
    role: "build-worker",
    binder,
    item,
    roleDefinitionHash: "a".repeat(64),
    profileHash: "b".repeat(64),
    outcome: "ready",
    summary,
    checks: [{ id: "unit", command: "true", cwd: "." }],
    runtime: {
      provider: "fixture",
      model: "fixture",
      policy: "worker",
      exactModelResolved: true,
      parentAuthConfigured: true,
      childAuthConfigured: true,
      copiedProvider: "builtin",
      copiedRuntimeCredential: false,
      unresolvedEnvironmentKeys: [],
    },
    attestation: {
      schema: "karta-worker-authority-attestation-v1",
      passed: true,
      issues: [],
      before: snapshot,
      after: snapshot,
    },
  };
}

function runner(
  repo: string,
  workerRun: (...args: any[]) => Promise<KartaWorkerResult>,
  finalizer: Record<string, (...args: any[]) => Promise<any>>,
  checkpoint: KartaBuildCheckpoint = () => {},
): KartaBuildItemRunner {
  const locks = new DispatchLockManager();
  return new KartaBuildItemRunner(
    locks,
    { run: workerRun } as unknown as KartaBuildWorkerRunner,
    finalizer as unknown as KartaBuildFinalizer,
    new KartaProcessManager(new LifecycleRegistry(), 10),
    checkpoint,
  );
}

test("fixed build creates the deterministic item worktree and owns finalization", async () => {
  const state = await fixture();
  try {
    let workerParent: string | undefined;
    let finalizedWorktree = "";
    const build = runner(
      state.repo,
      async (_ctx, worktree, branch, binder, item, assignment, _feedback, parentId) => {
        workerParent = parentId;
        assert.equal(branch, "karta/demo/item-item-a");
        assert.equal(assignment.title, "Change subject");
        await writeFile(join(worktree, "subject.txt"), "candidate\n");
        return workerResult(binder, item);
      },
      {
        async finalizeCandidate(_ctx, binder, item, worktree, _lease, checks, processContext) {
          finalizedWorktree = worktree;
          assert.equal(checks[0].id, "unit");
          assert.equal(processContext.owner.id, workerParent);
          return {
            status: "built",
            binder,
            item,
            commit: "c".repeat(40),
            message: "built",
          };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "built");
    assert.equal(result.attempts, 1);
    assert.equal(
      result.worktree,
      await realpath(join(dirname(state.repo), "repo-worktrees", "karta-demo-item-item-a")),
    );
    assert.equal(finalizedWorktree, result.worktree);
    assert.ok(workerParent);
    assert.equal(await git(result.worktree!, ["branch", "--show-current"]), "karta/demo/item-item-a");
  } finally {
    await state.cleanup();
  }
});

test("deterministic crash after worker attestation leaves resumable Git state", async () => {
  const state = await fixture();
  try {
    const crashed = runner(
      state.repo,
      async (_ctx, worktree, _branch, binder, item) => {
        await writeFile(join(worktree, "subject.txt"), "candidate\n");
        return workerResult(binder, item);
      },
      {},
      (name) => {
        if (name === "worker-attested") throw new Error("injected worker crash");
      },
    );
    await assert.rejects(
      () => crashed.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a"),
      /injected worker crash/,
    );
    const worktree = join(dirname(state.repo), "repo-worktrees", "karta-demo-item-item-a");
    assert.match(await git(worktree, ["status", "--short"]), /subject\.txt/);

    const resumed = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item) => workerResult(binder, item),
      {
        async finalizeCandidate(_ctx, binder, item) {
          return { status: "built", binder, item, commit: "e".repeat(40), message: "resumed" };
        },
      },
    );
    const result = await resumed.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "built");
  } finally {
    await state.cleanup();
  }
});

test("a typed visual block finalization halts without consuming a worker-feedback attempt", async () => {
  const state = await fixture();
  try {
    let workers = 0;
    let finalizations = 0;
    const build = runner(
      state.repo,
      async (_ctx, worktree, _branch, binder, item) => {
        workers += 1;
        await writeFile(join(worktree, "subject.txt"), "candidate\n");
        return workerResult(binder, item, `attempt ${workers}`);
      },
      {
        async finalizeCandidate(_ctx, binder, item) {
          finalizations += 1;
          return {
            status: "blocked",
            binder,
            item,
            verification: {
              status: "blocked",
              blockedReason: "visual-no-design",
              gates: { safety: { verdict: "pass" } },
            },
            message: "Visual acceptance needs a view; no ref moved.",
          };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "blocked");
    assert.equal(result.attempts, 1);
    assert.equal(workers, 1);
    assert.equal(finalizations, 1);
    assert.equal(result.finalization?.verification?.blockedReason, "visual-no-design");
  } finally {
    await state.cleanup();
  }
});

test("visual gate concerns retry under the acceptance cap and write failed through the host", async () => {
  const state = await fixture();
  try {
    let workers = 0;
    let failedRecords = 0;
    // A gates.visual concern is a fidelity kickback: it retries under the same bounded
    // acceptance-attempt cap as an acceptance concern, and on exhaustion the host writes
    // the failed ref exactly as a capped acceptance/safety concern does.
    const retry = {
      status: "retry",
      binder: "demo",
      item: "item-a",
      targetTree: "a".repeat(40),
      verification: {
        status: "concerns",
        gates: { safety: { verdict: "pass" }, visual: { verdict: "concerns" } },
      },
      message: "retry",
    };
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item) => {
        workers += 1;
        return workerResult(binder, item, `attempt ${workers}`);
      },
      {
        async finalizeCandidate() {
          return retry;
        },
        async recordFailedCandidate() {
          failedRecords += 1;
          return { ...retry, status: "failed", commit: "f".repeat(40), message: "failed" };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "failed");
    assert.equal(result.attempts, 2);
    assert.equal(workers, 2);
    assert.equal(failedRecords, 1);
  } finally {
    await state.cleanup();
  }
});

test("acceptance concerns cap at two attempts and write failed through the host", async () => {
  const state = await fixture();
  try {
    let workers = 0;
    let failedRecords = 0;
    const retry = {
      status: "retry",
      binder: "demo",
      item: "item-a",
      targetTree: "a".repeat(40),
      verification: {
        gates: { acceptance: { verdict: "concerns" } },
      },
      message: "retry",
    };
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item) => {
        workers += 1;
        return workerResult(binder, item, `attempt ${workers}`);
      },
      {
        async finalizeCandidate() {
          return retry;
        },
        async recordFailedCandidate() {
          failedRecords += 1;
          return { ...retry, status: "failed", commit: "f".repeat(40), message: "failed" };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "failed");
    assert.equal(result.attempts, 2);
    assert.equal(workers, 2);
    assert.equal(failedRecords, 1);
  } finally {
    await state.cleanup();
  }
});

test("committed-unmarked reuses the worker only for floor discovery and host recovery", async () => {
  const state = await fixture();
  try {
    await git(state.repo, ["branch", "karta/demo/item-item-a", "karta/demo/integration"]);
    const worktree = join(state.root, "committed-item");
    await git(state.repo, ["worktree", "add", worktree, "karta/demo/item-item-a"]);
    await writeFile(join(worktree, "subject.txt"), "candidate\n");
    await git(worktree, ["add", "."]);
    await git(worktree, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] committed"]);
    let mode: string | undefined;
    let recoveries = 0;
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item, _assignment, _feedback, _parent, workerMode) => {
        mode = workerMode;
        return workerResult(binder, item);
      },
      {
        async recoverCommittedCandidate(_ctx, binder, item) {
          recoveries += 1;
          return { status: "built", binder, item, commit: "c".repeat(40), message: "recovered" };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "recovered");
    assert.equal(mode, "recover-committed");
    assert.equal(recoveries, 1);
  } finally {
    await state.cleanup();
  }
});

test("merged-unmarked revalidates through the host without editing or redispatching implementation", async () => {
  const state = await fixture();
  try {
    await git(state.repo, ["branch", "karta/demo/item-item-a", "karta/demo/integration"]);
    const worktree = join(state.root, "merged-item");
    await git(state.repo, ["worktree", "add", worktree, "karta/demo/item-item-a"]);
    await writeFile(join(worktree, "subject.txt"), "candidate\n");
    await git(worktree, ["add", "."]);
    await git(worktree, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] built"]);
    const itemTip = await git(worktree, ["rev-parse", "HEAD"]);
    await git(worktree, ["update-ref", "refs/karta/demo/item-item-a/built", itemTip]);
    await git(state.repo, ["checkout", "karta/demo/integration"]);
    await git(state.repo, ["merge", "--no-ff", "--no-gpg-sign", "-m", "merge item", itemTip]);
    let mode: string | undefined;
    let recoveries = 0;
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item, _assignment, _feedback, _parent, workerMode) => {
        mode = workerMode;
        return workerResult(binder, item);
      },
      {
        async recoverMergedCandidate(_ctx, binder, item) {
          recoveries += 1;
          return { status: "built", binder, item, commit: "d".repeat(40), message: "recovered" };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "recovered");
    assert.equal(mode, "recover-merged");
    assert.equal(recoveries, 1);
  } finally {
    await state.cleanup();
  }
});

test("an existing built ref recovers without redispatch", async () => {
  const state = await fixture();
  try {
    await git(state.repo, ["branch", "karta/demo/item-item-a", "karta/demo/integration"]);
    const worktree = join(state.root, "manual-item");
    await git(state.repo, ["worktree", "add", worktree, "karta/demo/item-item-a"]);
    await writeFile(join(worktree, "subject.txt"), "candidate\n");
    await git(worktree, ["add", "."]);
    await git(worktree, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] built"]);
    const tip = await git(worktree, ["rev-parse", "HEAD"]);
    await git(worktree, ["update-ref", "refs/karta/demo/item-item-a/built", tip]);
    const build = runner(
      state.repo,
      async () => {
        throw new Error("worker must not run");
      },
      {},
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "recovered");
    assert.equal(result.recoveryState, "built");
    assert.equal(result.attempts, 0);
  } finally {
    await state.cleanup();
  }
});

test("a finalization retry with no gate verdict halts blocked instead of crashing", async () => {
  const state = await fixture();
  try {
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item) => workerResult(binder, item),
      {
        async finalizeCandidate(_ctx, binder, item) {
          return {
            status: "retry",
            binder,
            item,
            checkFailure: {
              status: "failed",
              passes: 0,
              targetTree: "a".repeat(40),
              check: { id: "environment-setup", result: { status: "failed" } },
            },
            message: "environment setup mutated tracked files",
          };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "blocked");
    assert.equal(result.attempts, 1);
  } finally {
    await state.cleanup();
  }
});

test("a mixed acceptance-then-safety retry sequence caps without crashing", async () => {
  const state = await fixture();
  try {
    let calls = 0;
    const sequence = ["acceptance", "safety", "safety", "safety"];
    let failedRecords = 0;
    const build = runner(
      state.repo,
      async (_ctx, _worktree, _branch, binder, item) => workerResult(binder, item, `attempt ${calls}`),
      {
        async finalizeCandidate(_ctx, binder, item) {
          const kind = sequence[calls++] ?? "safety";
          return {
            status: "retry",
            binder,
            item,
            targetTree: "a".repeat(40),
            verification: { gates: { [kind]: { verdict: "concerns" } } },
            message: "retry",
          };
        },
        async recordFailedCandidate(_ctx, binder, item) {
          failedRecords += 1;
          return { status: "failed", binder, item, commit: "f".repeat(40), message: "failed" };
        },
      },
    );
    const result = await build.run({ cwd: state.repo } as ExtensionContext, "demo", "item-a");
    assert.equal(result.status, "failed");
    assert.equal(failedRecords, 1);
    assert.equal(calls, 4);
  } finally {
    await state.cleanup();
  }
});
