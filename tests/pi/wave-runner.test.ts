import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import type { KartaIntegrationResult } from "../../extensions/pi/integration-runner.ts";
import { KartaWaveRunner } from "../../extensions/pi/wave-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return stdout.trim();
}

async function fixture(sharedTerms = false): Promise<{
  root: string;
  integration: string;
  locks: DispatchLockManager;
  runner: KartaWaveRunner;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-wave-runner-"));
  const repo = join(root, "repo");
  const integration = join(root, "integration");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(
    join(repo, ".karta", "binders", "demo.json"),
    `${JSON.stringify({
      slug: "demo",
      work_items: [{ id: "item-a", touches: ["subject.txt"] }],
      ...(sharedTerms
        ? { shared_terms: [{ id: "term", canonical: "required canonical", items: ["item-a"] }] }
        : {}),
    })}\n`,
  );
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Wave"]);
  await git(repo, ["config", "user.email", "wave@example.invalid"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  await git(repo, ["worktree", "add", integration, "karta/demo/integration"]);
  const locks = new DispatchLockManager();
  return {
    root,
    integration,
    locks,
    runner: new KartaWaveRunner(locks),
    async cleanup() {
      await locks.releaseAll();
      await rm(root, { recursive: true, force: true });
    },
  };
}

async function landItem(integration: string): Promise<KartaIntegrationResult> {
  const base = await git(integration, ["rev-parse", "HEAD"]);
  await writeFile(join(integration, "subject.txt"), "candidate\n");
  await git(integration, ["add", "."]);
  const tree = await git(integration, ["write-tree"]);
  await git(integration, ["read-tree", "--reset", "-u", base]);
  const itemTip = await git(integration, [
    "commit-tree",
    tree,
    "-p",
    base,
    "-m",
    "[karta:item-item-a] candidate",
  ]);
  await git(integration, ["update-ref", "refs/heads/karta/demo/item-item-a", itemTip]);
  await git(integration, ["update-ref", "refs/karta/demo/item-item-a/built", itemTip]);
  const mergeCommit = await git(integration, [
    "commit-tree",
    tree,
    "-p",
    base,
    "-p",
    itemTip,
    "-m",
    "[karta:merge-item-item-a] integrate",
  ]);
  await git(integration, ["update-ref", "refs/heads/karta/demo/integration", mergeCommit, base]);
  await git(integration, ["read-tree", "--reset", "-u", mergeCommit]);
  await git(integration, ["update-ref", "refs/karta/demo/item-item-a/done", mergeCommit]);
  return {
    schema: "karta-integration-item-v1",
    binder: "demo",
    item: "item-a",
    status: "integrated",
    base,
    itemTip,
    targetTree: tree,
    mergeCommit,
    message: "integrated",
  };
}

test("post-wave checks write a success tag only after the assembled tip passes", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    const anchor = await state.runner.start("demo", 1, state.integration, lease);
    const integration = await landItem(state.integration);
    const result = await state.runner.finish(
      { cwd: state.integration } as ExtensionContext,
      anchor,
      state.integration,
      lease,
      [integration],
      [{ id: "floor", purpose: "floor", command: "true", cwd: "." }],
    );
    assert.equal(result.status, "passed");
    assert.equal(await git(state.integration, ["rev-parse", result.successTag!]), integration.mergeCommit);
    assert.equal(await git(state.integration, ["rev-parse", anchor.baseTag]), anchor.base);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("shared-term drift rolls back even when the post-wave floor passes", async () => {
  const state = await fixture(true);
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    const anchor = await state.runner.start("demo", 1, state.integration, lease);
    const integration = await landItem(state.integration);
    const result = await state.runner.finish(
      { cwd: state.integration } as ExtensionContext,
      anchor,
      state.integration,
      lease,
      [integration],
      [{ id: "floor", purpose: "floor", command: "true", cwd: "." }],
    );
    assert.equal(result.status, "rolled-back");
    assert.equal(await git(state.integration, ["rev-parse", "HEAD"]), anchor.base);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});

test("failed post-wave floor atomically restores integration and removes done and built", async () => {
  const state = await fixture();
  const lease = await state.locks.acquire(state.integration, "demo");
  try {
    const anchor = await state.runner.start("demo", 1, state.integration, lease);
    const integration = await landItem(state.integration);
    const result = await state.runner.finish(
      { cwd: state.integration } as ExtensionContext,
      anchor,
      state.integration,
      lease,
      [integration],
      [{ id: "floor", purpose: "floor", command: "false", cwd: "." }],
    );
    assert.equal(result.status, "rolled-back");
    assert.equal(await git(state.integration, ["rev-parse", "HEAD"]), anchor.base);
    assert.equal(await git(state.integration, ["write-tree"]), await git(state.integration, ["rev-parse", `${anchor.base}^{tree}`]));
    await assert.rejects(() => git(state.integration, ["rev-parse", "refs/karta/demo/item-item-a/done"]));
    await assert.rejects(() => git(state.integration, ["rev-parse", "refs/karta/demo/item-item-a/built"]));
    await assert.rejects(() => git(state.integration, ["rev-parse", "refs/tags/karta/demo/wave-1"]));
    assert.equal(await git(state.integration, ["rev-parse", anchor.baseTag]), anchor.base);
  } finally {
    await state.locks.release(lease);
    await state.cleanup();
  }
});
