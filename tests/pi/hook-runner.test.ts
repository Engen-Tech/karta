import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { access, chmod, mkdir, mkdtemp, readFile, rm, watch, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import {
  validateCandidateHooks,
  validateMergeHooks,
} from "../../extensions/pi/hook-runner.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(): Promise<{
  repo: string;
  parent: string;
  tree: string;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-hook-runner-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Hooks"]);
  await git(repo, ["config", "user.email", "hooks@invalid.example"]);
  await git(repo, ["config", "core.hooksPath", join(repo, ".git", "hooks")]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  const parent = await git(repo, ["rev-parse", "HEAD"]);
  await writeFile(join(repo, "subject.txt"), "candidate\n");
  await git(repo, ["add", "."]);
  const tree = await git(repo, ["write-tree"]);
  return { repo, parent, tree, cleanup: () => rm(root, { recursive: true, force: true }) };
}

async function hook(repo: string, name: string, source: string): Promise<void> {
  const path = join(repo, ".git", "hooks", name);
  await writeFile(path, `#!/bin/sh\nset -eu\n${source}\n`);
  await chmod(path, 0o755);
}

test("commit-msg hooks may refine the message without changing the candidate tree", async () => {
  const state = await fixture();
  try {
    await hook(state.repo, "commit-msg", "printf '\\nReviewed-By: hook\\n' >> \"$1\"");
    const result = await validateCandidateHooks({
      worktree: state.repo,
      candidateTree: state.tree,
      parent: state.parent,
      message: "[karta:item-item-a] candidate",
    });
    assert.equal(result.status, "passed");
    assert.equal(result.hookTree, state.tree);
    assert.match(result.message ?? "", /Reviewed-By: hook/);
  } finally {
    await state.cleanup();
  }
});

test("merge hooks reproduce the exact two-parent candidate and may refine its message", async () => {
  const state = await fixture();
  try {
    const item = await git(state.repo, [
      "commit-tree",
      state.tree,
      "-p",
      state.parent,
      "-m",
      "[karta:item-item-a] item",
    ]);
    await hook(state.repo, "commit-msg", "printf '\\nReviewed-By: merge-hook\\n' >> \"$1\"");
    const result = await validateMergeHooks({
      worktree: state.repo,
      integrationTip: state.parent,
      itemTip: item,
      candidateTree: state.tree,
      message: "[karta:merge-item-item-a] merge",
    });
    assert.equal(result.status, "passed");
    assert.equal(result.hookTree, state.tree);
    assert.match(result.message ?? "", /Reviewed-By: merge-hook/);
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), state.parent);
  } finally {
    await state.cleanup();
  }
});

test("binder shutdown kills a running merge hook and its descendant", { timeout: 60_000 }, async (context) => {
  if (process.platform === "win32") {
    context.skip("native Windows process-tree coverage runs in the release matrix");
    return;
  }
  const state = await fixture();
  try {
    const item = await git(state.repo, [
      "commit-tree",
      state.tree,
      "-p",
      state.parent,
      "-m",
      "[karta:item-item-a] item",
    ]);
    const ready = join(state.repo, "hook-ready");
    const descendantFile = join(state.repo, "hook-descendant");
    await hook(
      state.repo,
      "pre-commit",
      `tail -f /dev/null &\nprintf '%s' "$!" > '${descendantFile}'\nprintf ready > '${ready}'\nwait`,
    );
    const lifecycles = new LifecycleRegistry();
    const manager = new KartaProcessManager(lifecycles, 25);
    const owner = manager.createBinderOwner(state.repo, "demo");
    const watchAbort = new AbortController();
    const watcher = watch(state.repo, { signal: watchAbort.signal });
    const validation = validateMergeHooks({
      worktree: state.repo,
      integrationTip: state.parent,
      itemTip: item,
      candidateTree: state.tree,
      message: "[karta:merge-item-item-a] merge",
      onProcessStart(pid) {
        manager.registerProcess(pid, {
          cwd: state.repo,
          parentId: owner.id,
          label: "merge hooks",
        });
      },
      onProcessExit(pid) {
        manager.forgetProcess(pid);
      },
    });
    try {
      await access(ready);
    } catch {
      for await (const event of watcher) {
        if (event.filename === "hook-ready") break;
      }
    } finally {
      watchAbort.abort();
    }
    const descendant = Number(await readFile(descendantFile, "utf8"));
    assert.ok(Number.isInteger(descendant) && descendant > 0);
    await manager.stopOwner(owner);
    const stopped = await validation;
    assert.equal(stopped.status, "failed");
    assert.throws(() => process.kill(descendant, 0), (error: NodeJS.ErrnoException) => error.code === "ESRCH");
    assert.equal(manager.size, 0);
    assert.equal(lifecycles.size, 0);
  } finally {
    await state.cleanup();
  }
});

test("hooks that mutate the candidate are rejected in the disposable worktree", async () => {
  const state = await fixture();
  try {
    await hook(
      state.repo,
      "pre-commit",
      "printf 'hook mutation\\n' > hook.txt\ngit add hook.txt",
    );
    const result = await validateCandidateHooks({
      worktree: state.repo,
      candidateTree: state.tree,
      parent: state.parent,
      message: "[karta:item-item-a] candidate",
    });
    assert.equal(result.status, "drifted");
    assert.notEqual(result.hookTree, state.tree);
    await assert.rejects(() => git(state.repo, ["rev-parse", "--verify", "HEAD:hook.txt"]));
  } finally {
    await state.cleanup();
  }
});

test("failing hooks block without moving the real worktree branch", async () => {
  const state = await fixture();
  try {
    await hook(state.repo, "pre-commit", "echo blocked >&2\nexit 7");
    const result = await validateCandidateHooks({
      worktree: state.repo,
      candidateTree: state.tree,
      parent: state.parent,
      message: "[karta:item-item-a] candidate",
    });
    assert.equal(result.status, "failed");
    assert.match(result.stderr, /blocked/);
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), state.parent);
  } finally {
    await state.cleanup();
  }
});
