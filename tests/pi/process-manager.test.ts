import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

test("binder owner shutdown terminates and forgets its managed process tree", async (context) => {
  if (process.platform === "win32") {
    context.skip("POSIX process-group assertion has a native Windows release fixture");
    return;
  }
  const root = await mkdtemp(join(tmpdir(), "karta-process-manager-"));
  try {
    const script = join(root, "wait.mjs");
    await writeFile(script, "setInterval(() => {}, 60000);\n");
    const child = spawn(process.execPath, [script], {
      cwd: root,
      detached: true,
      stdio: "ignore",
    });
    assert.ok(child.pid);
    const lifecycles = new LifecycleRegistry();
    const manager = new KartaProcessManager(lifecycles, 50);
    const owner = manager.createBinderOwner(root, "demo");
    manager.registerProcess(child.pid, {
      cwd: root,
      parentId: owner.id,
      label: "fixture",
      role: "host-check",
    });
    assert.equal(manager.size, 1);
    assert.deepEqual(
      lifecycles
        .snapshot()
        .map(({ role, parentId }) => ({ role, parentId }))
        .sort((left, right) => left.role.localeCompare(right.role)),
      [
        { role: "delivery", parentId: undefined },
        { role: "host-check", parentId: owner.id },
      ],
    );
    await manager.stopOwner(owner);
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(processExists(child.pid), false);
    assert.equal(manager.size, 0);
    assert.equal(lifecycles.size, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("normally exited processes can be forgotten without stopping their owner", async () => {
  const lifecycles = new LifecycleRegistry();
  const manager = new KartaProcessManager(lifecycles, 10);
  const owner = manager.createBinderOwner(process.cwd(), "demo");
  const child = spawn(process.execPath, ["-e", "process.exit(0)"], {
    detached: process.platform !== "win32",
    stdio: "ignore",
  });
  assert.ok(child.pid);
  manager.registerProcess(child.pid, {
    cwd: process.cwd(),
    parentId: owner.id,
    label: "short",
  });
  await new Promise<void>((resolve) => child.once("close", () => resolve()));
  manager.forgetProcess(child.pid);
  assert.equal(manager.size, 0);
  assert.equal(lifecycles.snapshot().length, 1);
  await manager.stopOwner(owner);
});
