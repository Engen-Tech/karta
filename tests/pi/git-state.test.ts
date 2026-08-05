import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { deriveItemGitState } from "../../extensions/pi/git-state.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(): Promise<{ repo: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-git-state-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Git State"]);
  await git(repo, ["config", "user.email", "git-state@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  return { repo, cleanup: () => rm(root, { recursive: true, force: true }) };
}

async function commitItem(repo: string): Promise<string> {
  await writeFile(join(repo, "subject.txt"), "item\n");
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] item"]);
  return git(repo, ["rev-parse", "HEAD"]);
}

test("item recovery state advances from Git alone through the clean delivery path", async () => {
  const { repo, cleanup } = await fixture();
  try {
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "not-started");
    await git(repo, ["checkout", "-b", "karta/demo/item-item-a", "karta/demo/integration"]);
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "branch-only");

    await writeFile(join(repo, "subject.txt"), "dirty\n");
    const dirty = await deriveItemGitState(repo, "demo", "item-a");
    assert.equal(dirty.state, "worktree-dirty");
    assert.equal(dirty.dirty.unstaged, true);

    await git(repo, ["add", "."]);
    const itemTip = await git(repo, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] item"]).then(
      () => git(repo, ["rev-parse", "HEAD"]),
    );
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "committed-unmarked");

    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/built", itemTip]);
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "built");

    await git(repo, ["checkout", "karta/demo/integration"]);
    await git(repo, ["merge", "--no-ff", "--no-gpg-sign", "-m", "merge item", itemTip]);
    const merged = await deriveItemGitState(repo, "demo", "item-a");
    assert.equal(merged.state, "merged-unmarked");
    assert.match(merged.nextAction, /write done ref-last/);

    const integrationTip = await git(repo, ["rev-parse", "HEAD"]);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/done", integrationTip]);
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "done");
  } finally {
    await cleanup();
  }
});

test("failed and interrupted accept states remain distinct", async () => {
  const { repo, cleanup } = await fixture();
  try {
    await git(repo, ["checkout", "-b", "karta/demo/item-item-a", "karta/demo/integration"]);
    const itemTip = await commitItem(repo);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/failed", itemTip]);
    assert.equal((await deriveItemGitState(repo, "demo", "item-a")).state, "failed");

    await git(repo, ["checkout", "karta/demo/integration"]);
    await git(repo, ["merge", "--no-ff", "--no-gpg-sign", "-m", "pending accept", itemTip]);
    const pending = await deriveItemGitState(repo, "demo", "item-a");
    assert.equal(pending.state, "accept-merge-pending");
    assert.match(pending.nextAction, /recover or revert/);
  } finally {
    await cleanup();
  }
});

test("contradictory or orphaned refs fail closed", async () => {
  const { repo, cleanup } = await fixture();
  try {
    const base = await git(repo, ["rev-parse", "HEAD"]);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/built", base]);
    const orphaned = await deriveItemGitState(repo, "demo", "item-a");
    assert.equal(orphaned.state, "inconsistent");
    assert.match(orphaned.diagnostics.join(" "), /without an item branch/);

    await git(repo, ["update-ref", "-d", "refs/karta/demo/item-item-a/built"]);
    await git(repo, ["checkout", "-b", "karta/demo/item-item-a", "karta/demo/integration"]);
    const itemTip = await commitItem(repo);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/built", itemTip]);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/failed", itemTip]);
    const contradictory = await deriveItemGitState(repo, "demo", "item-a");
    assert.equal(contradictory.state, "inconsistent");
    assert.match(contradictory.diagnostics.join(" "), /both exist/);
  } finally {
    await cleanup();
  }
});
