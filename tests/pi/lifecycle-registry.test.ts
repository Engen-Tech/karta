import assert from "node:assert/strict";
import test from "node:test";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";

test("lifecycle registry records authority in memory and enforces parent ownership", () => {
  const registry = new LifecycleRegistry();
  const resource = { abort() {}, dispose() {} };
  const parentId = registry.register({
    id: "delivery",
    role: "build-worker",
    cwd: "/repo",
    label: "integration owner",
    resource,
  });
  registry.register({
    id: "item-a",
    role: "build-worker",
    cwd: "/repo/worktree-a",
    parentId,
    resource,
  });
  assert.deepEqual(
    registry.snapshot().map(({ id, role, parentId: parent }) => ({ id, role, parent })),
    [
      { id: "delivery", role: "build-worker", parent: undefined },
      { id: "item-a", role: "build-worker", parent: "delivery" },
    ],
  );
  assert.throws(() => registry.forget(parentId), /still owns active children/);
  assert.throws(
    () =>
      registry.register({
        id: "orphan",
        role: "safety-gate",
        cwd: "/repo",
        parentId: "missing",
        resource,
      }),
    /parent is not active/,
  );
  assert.throws(
    () =>
      registry.register({
        id: "item-a",
        role: "acceptance-gate",
        cwd: "/repo",
        resource,
      }),
    /already active/,
  );
});

test("lifecycle shutdown aborts every child before disposing resources", async () => {
  const calls: string[] = [];
  const registry = new LifecycleRegistry();
  for (const id of ["a", "b", "c"]) {
    registry.register({
      id,
      role: "phase0-probe",
      cwd: "/repo",
      resource: {
        abort() {
          calls.push(`abort:${id}`);
          if (id === "b") throw new Error("abort fixture");
        },
        dispose() {
          calls.push(`dispose:${id}`);
          if (id === "b") throw new Error("dispose fixture");
        },
      },
    });
  }
  const firstShutdown = registry.shutdown();
  const secondShutdown = registry.shutdown();
  assert.equal(firstShutdown, secondShutdown);
  await firstShutdown;
  assert.equal(registry.size, 0);
  const firstDispose = calls.findIndex((call) => call.startsWith("dispose:"));
  assert.equal(firstDispose, 3);
  assert.deepEqual(
    calls.filter((call) => call.startsWith("abort:")).sort(),
    ["abort:a", "abort:b", "abort:c"],
  );
  assert.throws(
    () =>
      registry.register({
        role: "phase0-probe",
        cwd: "/repo",
        resource: { abort() {}, dispose() {} },
      }),
    /shutting down/,
  );
});
