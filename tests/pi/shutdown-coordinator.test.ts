import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import { KartaShutdownCoordinator } from "../../extensions/pi/shutdown-coordinator.ts";

const exec = promisify(execFile);

test("shutdown aborts children before releasing locks and is idempotent", async () => {
  const events: string[] = [];
  let releaseCount = 0;
  const coordinator = new KartaShutdownCoordinator({
    guards: { shutdown() { events.push("guards"); } },
    preflight: { clear() { events.push("preflight"); } },
    children: {
      async abortAll() {
        events.push("children");
      },
    },
    locks: {
      async releaseAll() {
        releaseCount += 1;
        events.push("locks");
      },
    },
    releaseInstance() {
      events.push("instance");
    },
  });
  const first = coordinator.shutdown();
  const second = coordinator.shutdown();
  assert.equal(first, second);
  await first;
  assert.deepEqual(events, ["guards", "preflight", "children", "locks", "instance"]);
  assert.equal(releaseCount, 1);
});

test("shutdown releases only its own binder locks", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-shutdown-locks-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await exec("git", ["init", "--initial-branch=main"], { cwd: repo });
  await exec("git", ["config", "user.name", "Karta Shutdown"], { cwd: repo });
  await exec("git", ["config", "user.email", "shutdown@example.invalid"], { cwd: repo });
  await writeFile(join(repo, "base.txt"), "base\n");
  await exec("git", ["add", "."], { cwd: repo });
  await exec("git", ["commit", "--no-gpg-sign", "-m", "base"], { cwd: repo });
  const owned = new DispatchLockManager();
  const foreign = new DispatchLockManager();
  const ownedLease = await owned.acquire(repo, "owned");
  const foreignLease = await foreign.acquire(repo, "foreign");
  try {
    const coordinator = new KartaShutdownCoordinator({
      guards: { shutdown() {} },
      preflight: { clear() {} },
      children: { async abortAll() {} },
      locks: owned,
      releaseInstance() {},
    });
    await coordinator.shutdown();
    assert.equal(await owned.owns(ownedLease), false);
    assert.equal(await foreign.owns(foreignLease), true);
    const replacement = new DispatchLockManager();
    const replacementLease = await replacement.acquire(repo, "owned");
    await replacement.release(replacementLease);
  } finally {
    await foreign.release(foreignLease);
    await owned.releaseAll();
    await rm(root, { recursive: true, force: true });
  }
});

test("shutdown still releases locks and extension claim after child abort failure", async () => {
  const events: string[] = [];
  const coordinator = new KartaShutdownCoordinator({
    guards: { shutdown() {} },
    preflight: { clear() {} },
    children: {
      async abortAll() {
        events.push("children");
        throw new Error("abort failure");
      },
    },
    locks: {
      async releaseAll() {
        events.push("locks");
      },
    },
    releaseInstance() {
      events.push("instance");
    },
  });
  await assert.rejects(() => coordinator.shutdown(), /abort failure/);
  assert.deepEqual(events, ["children", "locks", "instance"]);
});
