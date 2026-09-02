import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  createBuildWorkerCapabilityProfile,
  requireWorktreePath,
} from "../../extensions/pi/worker-profile.ts";

test("build worker profile exposes only explicit worktree tools", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-worker-profile-"));
  try {
    const profile = createBuildWorkerCapabilityProfile(root, "karta/demo/item-item-a");
    assert.deepEqual(profile.toolNames, ["read", "write", "edit", "bash"]);
    assert.equal(profile.role.authority, "worktree-write");
    assert.equal(profile.role.id, "build-worker");
    assert.match(profile.profileHash, /^[a-f0-9]{64}$/);
    assert.match(profile.tools[3].description, /high authority and is not confined/);
    assert.equal(profile.toolNames.includes("karta_dispatch"), false);
    assert.equal(profile.toolNames.includes("karta_script"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("worker file tools allow in-root writes and reject traversal", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-worker-write-"));
  await mkdir(join(root, "src"));
  await mkdir(join(root, ".git"));
  await mkdir(join(root, ".karta"));
  try {
    const profile = createBuildWorkerCapabilityProfile(root, "karta/demo/item-item-a");
    const write = profile.tools.find((tool) => tool.name === "write");
    assert.ok(write);
    await write.execute(
      "write",
      { path: "src/result.txt", content: "inside\n" },
      undefined,
      undefined,
      { cwd: root } as ExtensionContext,
    );
    assert.equal(await readFile(join(root, "src", "result.txt"), "utf8"), "inside\n");
    await assert.rejects(
      () =>
        write.execute(
          "escape",
          { path: "../outside.txt", content: "no\n" },
          undefined,
          undefined,
          { cwd: root } as ExtensionContext,
        ),
      /escapes its worktree/,
    );
    await assert.rejects(
      () =>
        write.execute(
          "git-admin",
          { path: ".git/config", content: "no\n" },
          undefined,
          undefined,
          { cwd: root } as ExtensionContext,
        ),
      /Git administration paths/,
    );
    await assert.rejects(
      () =>
        write.execute(
          "karta-state",
          { path: ".karta/state.json", content: "no\n" },
          undefined,
          undefined,
          { cwd: root } as ExtensionContext,
        ),
      /host-owned Karta state/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("first file mutation exposes one deterministic checkpoint before editing", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-worker-first-edit-"));
  try {
    let checkpoints = 0;
    const profile = createBuildWorkerCapabilityProfile(
      root,
      "karta/demo/item-item-a",
      [],
      () => {
        checkpoints += 1;
      },
    );
    const write = profile.tools.find((tool) => tool.name === "write");
    assert.ok(write);
    await write.execute(
      "first",
      { path: "first.txt", content: "first\n" },
      undefined,
      undefined,
      { cwd: root } as ExtensionContext,
    );
    await write.execute(
      "second",
      { path: "second.txt", content: "second\n" },
      undefined,
      undefined,
      { cwd: root } as ExtensionContext,
    );
    assert.equal(checkpoints, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("worker file paths cannot escape through an existing symlink", async () => {
  const parent = await mkdtemp(join(tmpdir(), "karta-worker-symlink-"));
  const root = join(parent, "worktree");
  const outside = join(parent, "outside");
  await mkdir(root);
  await mkdir(outside);
  await writeFile(join(outside, "secret.txt"), "outside\n");
  await symlink(outside, join(root, "link"));
  try {
    assert.throws(() => requireWorktreePath(root, "link/secret.txt"), /symlink outside/);
    assert.throws(() => requireWorktreePath(root, join(outside, "secret.txt")), /escapes/);
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});
