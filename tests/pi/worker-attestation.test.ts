import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import {
  attestWorkerAuthority,
  snapshotWorkerAuthority,
} from "../../extensions/pi/worker-attestation.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  const { stdout } = await exec("git", ["-C", cwd, ...args], { encoding: "utf8" });
  return stdout.trim();
}

async function fixture(): Promise<{ root: string; item: string; sibling: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-worker-attestation-"));
  const repo = join(root, "repo");
  const item = join(root, "item");
  const sibling = join(root, "sibling");
  await mkdir(repo);
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Test"]);
  await git(repo, ["config", "user.email", "karta@example.invalid"]);
  await writeFile(join(repo, "subject.txt"), "base\n");
  await mkdir(join(repo, ".karta"));
  await writeFile(join(repo, ".karta", "state.txt"), "owned\n");
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/item-item-a"]);
  await git(repo, ["branch", "sibling"]);
  await git(repo, ["worktree", "add", item, "karta/demo/item-item-a"]);
  await git(repo, ["worktree", "add", sibling, "sibling"]);
  return { root, item, sibling, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test("ordinary worker file edits preserve authority surfaces", async () => {
  const state = await fixture();
  try {
    const before = await snapshotWorkerAuthority(state.item);
    await writeFile(join(state.item, "subject.txt"), "candidate\n");
    await writeFile(join(state.item, "new.txt"), "new\n");
    const after = await snapshotWorkerAuthority(state.item);
    assert.deepEqual(attestWorkerAuthority(before, after), {
      schema: "karta-worker-authority-attestation-v1",
      passed: true,
      issues: [],
      before,
      after,
    });
  } finally {
    await state.cleanup();
  }
});

test("Git authority, hooks, protected paths, and sibling mutations are detected", async () => {
  const state = await fixture();
  try {
    const before = await snapshotWorkerAuthority(state.item);
    await git(state.item, ["update-ref", "refs/karta/demo/item-item-a/forged", "HEAD"]);
    await git(state.item, ["config", "karta.fixture", "changed"]);
    const common = await git(state.item, ["rev-parse", "--git-common-dir"]);
    const hooks = resolve(state.item, common, "hooks");
    await mkdir(hooks, { recursive: true });
    const hook = join(hooks, "pre-commit");
    await writeFile(hook, "#!/bin/sh\nexit 0\n");
    await chmod(hook, 0o755);
    await writeFile(join(state.item, ".karta", "state.txt"), "tampered\n");
    await writeFile(join(state.sibling, "subject.txt"), "sibling tampered\n");
    const attestation = attestWorkerAuthority(before, await snapshotWorkerAuthority(state.item));
    assert.equal(attestation.passed, false);
    assert.deepEqual(attestation.issues, [
      "worker changed protected authority surface: refs",
      "worker changed protected authority surface: config",
      "worker changed protected authority surface: hooks",
      "worker changed protected authority surface: protectedPaths",
      "worker changed protected authority surface: siblings",
    ]);
  } finally {
    await state.cleanup();
  }
});

test("worker staging and branch movement are detected", async () => {
  const state = await fixture();
  try {
    await writeFile(join(state.item, "candidate.txt"), "candidate\n");
    const before = await snapshotWorkerAuthority(state.item);
    await git(state.item, ["add", "candidate.txt"]);
    await git(state.item, ["commit", "--no-gpg-sign", "-m", "forbidden commit"]);
    const attestation = attestWorkerAuthority(before, await snapshotWorkerAuthority(state.item));
    assert.equal(attestation.passed, false);
    assert.match(attestation.issues.join(" "), /head/);
    assert.match(attestation.issues.join(" "), /index/);
  } finally {
    await state.cleanup();
  }
});
