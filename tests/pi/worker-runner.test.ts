import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import { KartaBuildWorkerRunner } from "../../extensions/pi/worker-runner.ts";

const runtime: ChildRuntimeReport = {
  provider: "fixture",
  model: "fixture",
  policy: "worker",
  exactModelResolved: true,
  parentAuthConfigured: true,
  childAuthConfigured: true,
  copiedProvider: "builtin",
  copiedRuntimeCredential: false,
  unresolvedEnvironmentKeys: [],
};

test("worker runner binds package prompt, assignment, profile, and result hashes", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-worker-runner-"));
  try {
    const runner = new KartaBuildWorkerRunner(new ChildRegistry(), async (invocation) => {
      assert.match(invocation.systemPrompt, /host owns staging, secret scanning/i);
      assert.match(invocation.systemPrompt, /Do not run git add/);
      assert.match(invocation.systemPrompt, /AGENTS\.md \(blob a{40}/);
      assert.match(invocation.systemPrompt, /Use the repository convention/);
      assert.deepEqual(invocation.profile.toolNames, ["read", "write", "edit", "bash"]);
      const request = JSON.parse(invocation.userPrompt);
      assert.equal(request.assignment.title, "Change subject");
      return {
        runtime,
        text: JSON.stringify({
          schema: "karta-worker-result-v2",
          role: "build-worker",
          binder: "demo",
          item: "item-a",
          roleDefinitionHash: invocation.profile.role.definitionHash,
          profileHash: invocation.profile.profileHash,
          outcome: "ready",
          summary: "Implemented and self-checked the assignment.",
          checks: [{ id: "unit", command: "npm test", cwd: "." }],
        }),
      };
    }, async () => [
      {
        path: "AGENTS.md",
        blob: "a".repeat(40),
        sha256: "b".repeat(64),
        content: "Use the repository convention.\n",
      },
    ]);
    const result = await runner.run(
      { cwd: worktree } as ExtensionContext,
      worktree,
      "karta/demo/item-item-a",
      "demo",
      "item-a",
      { title: "Change subject" },
    );
    assert.equal(result.outcome, "ready");
    assert.deepEqual(result.checks, [{ id: "unit", command: "npm test", cwd: "." }]);
    assert.equal(result.runtime.policy, "worker");
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

test("worker runner rejects prose and stale envelopes", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-worker-result-"));
  try {
    const prose = new KartaBuildWorkerRunner(
      new ChildRegistry(),
      async () => ({ runtime, text: "done" }),
      async () => [],
    );
    await assert.rejects(
      () =>
        prose.run(
          { cwd: worktree } as ExtensionContext,
          worktree,
          "karta/demo/item-item-a",
          "demo",
          "item-a",
          {},
        ),
      /malformed JSON/,
    );
    const stale = new KartaBuildWorkerRunner(
      new ChildRegistry(),
      async (invocation) => ({
        runtime,
        text: JSON.stringify({
          schema: "karta-worker-result-v2",
          role: "build-worker",
          binder: "other",
          item: "item-a",
          roleDefinitionHash: invocation.profile.role.definitionHash,
          profileHash: invocation.profile.profileHash,
          outcome: "ready",
          summary: "Wrong binder.",
          checks: [],
        }),
      }),
      async () => [],
    );
    await assert.rejects(
      () =>
        stale.run(
          { cwd: worktree } as ExtensionContext,
          worktree,
          "karta/demo/item-item-a",
          "demo",
          "item-a",
          {},
        ),
      /stale Karta worker result/,
    );
    const unsafeCheck = new KartaBuildWorkerRunner(
      new ChildRegistry(),
      async (invocation) => ({
        runtime,
        text: JSON.stringify({
          schema: "karta-worker-result-v2",
          role: "build-worker",
          binder: "demo",
          item: "item-a",
          roleDefinitionHash: invocation.profile.role.definitionHash,
          profileHash: invocation.profile.profileHash,
          outcome: "ready",
          summary: "Unsafe check proposal.",
          checks: [{ id: "oracle", command: "npm test", cwd: "../outside" }],
        }),
      }),
      async () => [],
    );
    await assert.rejects(
      () =>
        unsafeCheck.run(
          { cwd: worktree } as ExtensionContext,
          worktree,
          "karta/demo/item-item-a",
          "demo",
          "item-a",
          {},
        ),
      /worker check proposal/,
    );
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});
