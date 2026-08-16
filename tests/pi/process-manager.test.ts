import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
    const closed = new Promise<void>((resolve) => child.once("close", () => resolve()));
    await manager.stopOwner(owner);
    await closed;
    assert.equal(processExists(child.pid), false);
    assert.equal(manager.size, 0);
    assert.equal(lifecycles.size, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("one binder shutdown owns dev-server and wave-environment descendants", async (context) => {
  if (process.platform === "win32") {
    context.skip("POSIX process-group assertion has a native Windows release fixture");
    return;
  }
  const root = await mkdtemp(join(tmpdir(), "karta-process-environments-"));
  try {
    const script = join(root, "environment.mjs");
    await writeFile(script, [
      'import { spawn } from "node:child_process";',
      'import { writeFileSync } from "node:fs";',
      'const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 60000)"], { stdio: "ignore" });',
      'writeFileSync(process.argv[2], String(child.pid));',
      'process.stdout.write("ready\\n");',
      'setInterval(() => {}, 60000);',
      "",
    ].join("\n"));
    const lifecycles = new LifecycleRegistry();
    const manager = new KartaProcessManager(lifecycles, 25);
    const owner = manager.createBinderOwner(root, "demo");
    const entries = ["dev-server", "wave-environment"].map((label) => {
      const pidFile = join(root, `${label}.pid`);
      const child = spawn(process.execPath, [script, pidFile], {
        cwd: root,
        detached: true,
        stdio: ["ignore", "pipe", "ignore"],
      });
      assert.ok(child.pid);
      manager.registerProcess(child.pid, { cwd: root, parentId: owner.id, label });
      return { child, pidFile };
    });
    await Promise.all(entries.map(({ child }) => new Promise<void>((resolve) => {
      child.stdout!.once("data", () => resolve());
    })));
    const descendants = await Promise.all(entries.map(async ({ pidFile }) =>
      Number(await readFile(pidFile, "utf8")),
    ));
    const closes = entries.map(({ child }) => new Promise<void>((resolve) => child.once("close", () => resolve())));
    await manager.stopOwner(owner);
    await Promise.all(closes);
    for (const descendant of descendants) assert.equal(processExists(descendant), false);
    assert.equal(manager.size, 0);
    assert.equal(lifecycles.size, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("process creation has a deterministic fault checkpoint and remains shutdown-owned", async (context) => {
  if (process.platform === "win32") {
    context.skip("POSIX process-group assertion has a native Windows release fixture");
    return;
  }
  const lifecycles = new LifecycleRegistry();
  const manager = new KartaProcessManager(lifecycles, 10, (name) => {
    if (name === "process-created") throw new Error("injected process creation crash");
  });
  const owner = manager.createBinderOwner(process.cwd(), "demo");
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 60000)"], {
    detached: true,
    stdio: "ignore",
  });
  assert.ok(child.pid);
  const pid = child.pid;
  assert.throws(
    () => manager.registerProcess(pid, { cwd: process.cwd(), parentId: owner.id, label: "dev-server" }),
    /injected process creation crash/,
  );
  const closed = new Promise<void>((resolve) => child.once("close", () => resolve()));
  await manager.stopOwner(owner);
  await closed;
  assert.equal(processExists(pid), false);
  assert.equal(manager.size, 0);
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
