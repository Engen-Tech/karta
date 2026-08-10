import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import { deriveItemGitState } from "../../extensions/pi/git-state.ts";
import {
  KartaIntegrationRunner,
  type KartaIntegrationCheckpoint,
} from "../../extensions/pi/integration-runner.ts";
import type { KartaVerificationRunner } from "../../extensions/pi/verification-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return stdout.trim();
}

async function fixture(checkpoint: KartaIntegrationCheckpoint = () => {}): Promise<{
  root: string;
  integration: string;
  item: string;
  locks: DispatchLockManager;
  runner: KartaIntegrationRunner;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-integration-runner-"));
  const repo = join(root, "repo");
  const integration = join(root, "integration");
  const item = join(root, "item");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(
    join(repo, ".karta", "binders", "demo.json"),
    `${JSON.stringify({
      slug: "demo",
      title: "Integration fixture",
      summary: "Integrate item",
      motivation: "Prove moving-tip merge",
      scope: { included: ["subject.txt"] },
      work_items: [{
        id: "item-a",
        title: "Change subject",
        summary: "Change subject",
        touches: ["subject.txt"],
        oracle: {
          type: "unit",
          assertions: ["subject is candidate"],
          command: "node check.mjs",
        },
      }],
    })}\n`,
  );
  await writeFile(
    join(repo, "check.mjs"),
    "import { readFileSync } from 'node:fs'; if (readFileSync('subject.txt', 'utf8') !== 'candidate\\n') process.exit(8);\n",
  );
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Integration"]);
  await git(repo, ["config", "user.email", "integration@example.invalid"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  await git(repo, ["branch", "karta/demo/item-item-a", "karta/demo/integration"]);
  await git(repo, ["worktree", "add", integration, "karta/demo/integration"]);
  await git(repo, ["worktree", "add", item, "karta/demo/item-item-a"]);
  await writeFile(join(item, "subject.txt"), "candidate\n");
  await git(item, ["add", "."]);
  await git(item, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] candidate"]);
  const itemTip = await git(item, ["rev-parse", "HEAD"]);
  await git(item, ["update-ref", "refs/karta/demo/item-item-a/built", itemTip]);
  const locks = new DispatchLockManager();
  const verification = {
    async runWithLease(_ctx: unknown, binder: string, workItem: string) {
      return {
        schema: "karta-verification-v1",
        binder,
        item: workItem,
        requestedMode: "full",
        effectiveMode: "full",
        evidenceHash: "a".repeat(64),
        status: "pass",
        gates: {},
      };
    },
  } as unknown as KartaVerificationRunner;
  return {
    root,
    integration,
    item,
    locks,
    runner: new KartaIntegrationRunner(locks, verification, checkpoint),
    async cleanup() {
      await locks.releaseAll();
      await rm(root, { recursive: true, force: true });
    },
  };
}

test("integration gates the proposed tree then creates an exact no-ff merge and done ref", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    const result = await state.runner.integrate(
      { cwd: state.integration } as ExtensionContext,
      "demo",
      "item-a",
      state.integration,
      lease,
      [{ id: "floor", purpose: "floor", command: "node check.mjs", cwd: "." }],
    );
    assert.equal(result.status, "integrated");
    const tip = await git(state.integration, ["rev-parse", "HEAD"]);
    assert.equal(result.mergeCommit, tip);
    assert.equal(await git(state.integration, ["rev-parse", "HEAD^{tree}"]), result.targetTree);
    const parents = (await git(state.integration, ["rev-list", "--parents", "-n", "1", "HEAD"]))
      .split(/\s+/).slice(1);
    assert.deepEqual(parents, [result.base, result.itemTip]);
    assert.equal(await git(state.integration, ["rev-parse", "refs/karta/demo/item-item-a/done"]), tip);
    assert.equal((await deriveItemGitState(state.integration, "demo", "item-a")).state, "done");
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("merge-hook drift blocks before the integration ref moves", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    const before = await git(state.integration, ["rev-parse", "HEAD"]);
    const common = await git(state.integration, ["rev-parse", "--git-common-dir"]);
    const hook = join(common, "hooks", "pre-commit");
    await writeFile(hook, "#!/bin/sh\nprintf 'hooked\\n' > hook.txt\ngit add hook.txt\n");
    await chmod(hook, 0o755);
    const result = await state.runner.integrate(
      { cwd: state.integration } as ExtensionContext,
      "demo",
      "item-a",
      state.integration,
      lease,
    );
    assert.equal(result.status, "blocked");
    assert.equal(result.hookValidation?.status, "drifted");
    assert.equal(await git(state.integration, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("crash after integration ref movement leaves merged-unmarked recovery state", async () => {
  const state = await fixture((name) => {
    if (name === "integration-ref-updated") throw new Error("injected merge crash");
  });
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    await assert.rejects(
      () => state.runner.integrate(
        { cwd: state.integration } as ExtensionContext,
        "demo",
        "item-a",
        state.integration,
        lease,
      ),
      /injected merge crash/,
    );
    const recovery = await deriveItemGitState(state.integration, "demo", "item-a");
    assert.equal(recovery.state, "merged-unmarked");
    await assert.rejects(() =>
      git(state.integration, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/done"]),
    );
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});
