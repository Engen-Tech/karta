import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import {
  KartaBuildWorkerRunner,
  promptWorkerForEnvelope,
  workerEnvelopeViolation,
} from "../../extensions/pi/worker-runner.ts";

const authoritySnapshot = {
  schema: "karta-worker-authority-snapshot-v1" as const,
  worktree: "/fixture",
  branch: "karta/demo/item-item-a",
  head: "a".repeat(40),
  index: "b".repeat(64),
  refs: "c".repeat(64),
  config: "d".repeat(64),
  hooks: "e".repeat(64),
  worktrees: "f".repeat(64),
  protectedPaths: "1".repeat(64),
  siblings: "2".repeat(64),
};

const authority = {
  async snapshot() {
    return authoritySnapshot;
  },
  attest(before: typeof authoritySnapshot, after: typeof authoritySnapshot) {
    return {
      schema: "karta-worker-authority-attestation-v1" as const,
      passed: true,
      issues: [],
      before,
      after,
    };
  },
};

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
    ], authority);
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
    assert.equal(result.attestation.passed, true);
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

test("worker runner rejects protected authority changes before parsing a valid envelope", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-worker-authority-"));
  try {
    const violatingAuthority = {
      ...authority,
      attest(before: typeof authoritySnapshot, after: typeof authoritySnapshot) {
        return {
          schema: "karta-worker-authority-attestation-v1" as const,
          passed: false,
          issues: ["worker changed protected authority surface: refs"],
          before,
          after,
        };
      },
    };
    const runner = new KartaBuildWorkerRunner(
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
          summary: "Attempted unauthorized Git mutation.",
          checks: [],
        }),
      }),
      async () => [],
      violatingAuthority,
    );
    await assert.rejects(
      () =>
        runner.run(
          { cwd: worktree } as ExtensionContext,
          worktree,
          "karta/demo/item-item-a",
          "demo",
          "item-a",
          {},
        ),
      /violated host authority.*refs/,
    );
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
      authority,
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
      authority,
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
      authority,
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

test("worker runner tolerates a fenced or prose-wrapped envelope and diagnoses an absent one", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-worker-wrap-"));
  try {
    const envelope = (invocation: { profile: { role: { definitionHash: string }; profileHash: string } }) =>
      JSON.stringify({
        schema: "karta-worker-result-v2",
        role: "build-worker",
        binder: "demo",
        item: "item-a",
        roleDefinitionHash: invocation.profile.role.definitionHash,
        profileHash: invocation.profile.profileHash,
        outcome: "ready",
        summary: "Wrapped envelope.",
        checks: [{ id: "unit", command: "npm test", cwd: "." }],
      });
    const runWrapped = (wrap: (json: string) => string) => {
      const runner = new KartaBuildWorkerRunner(
        new ChildRegistry(),
        async (invocation) => ({ runtime, text: wrap(envelope(invocation)) }),
        async () => [],
        authority,
      );
      return runner.run(
        { cwd: worktree } as ExtensionContext,
        worktree,
        "karta/demo/item-item-a",
        "demo",
        "item-a",
        {},
      );
    };

    const fenced = await runWrapped((json) => "```json\n" + json + "\n```");
    assert.equal(fenced.outcome, "ready");
    const prosed = await runWrapped((json) => `Here is my result:\n\n${json}\n\nDone.`);
    assert.equal(prosed.outcome, "ready");

    const empty = new KartaBuildWorkerRunner(
      new ChildRegistry(),
      async () => ({ runtime, text: "   " }),
      async () => [],
      authority,
    );
    await assert.rejects(
      () =>
        empty.run(
          { cwd: worktree } as ExtensionContext,
          worktree,
          "karta/demo/item-item-a",
          "demo",
          "item-a",
          {},
        ),
      /malformed JSON.*<empty>/,
    );
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

class FakePrompter {
  readonly calls: string[] = [];
  #responses: string[];
  #last = "";
  constructor(responses: string[]) {
    this.#responses = responses;
  }
  async prompt(message: string): Promise<void> {
    this.calls.push(message);
    this.#last = this.#responses.shift() ?? "";
  }
  getLastAssistantText(): string {
    return this.#last;
  }
}

const EXPECTED = {
  binder: "demo",
  item: "item-a",
  roleDefinitionHash: "a".repeat(64),
  profileHash: "b".repeat(64),
};

function envelope(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    schema: "karta-worker-result-v2",
    role: "build-worker",
    binder: EXPECTED.binder,
    item: EXPECTED.item,
    roleDefinitionHash: EXPECTED.roleDefinitionHash,
    profileHash: EXPECTED.profileHash,
    outcome: "ready",
    summary: "Closed the false-success path.",
    checks: [],
    ...overrides,
  });
}

const validEnvelope = envelope();

test("a worker ending on prose gets exactly one envelope-repair turn", async () => {
  const proseFirst = new FakePrompter([
    "The implementation is complete.\n\n## Summary\nI closed the false-success path.",
    validEnvelope,
  ]);
  const recovered = await promptWorkerForEnvelope(proseFirst, "USER PROMPT", EXPECTED);
  assert.equal(recovered, validEnvelope);
  assert.equal(proseFirst.calls.length, 2);
  assert.equal(proseFirst.calls[0], "USER PROMPT");
  assert.match(proseFirst.calls[1], /ONLY the single JSON object/);
});

test("a worker that returns the envelope first is not re-prompted", async () => {
  const cleanFirst = new FakePrompter([validEnvelope, "should not be used"]);
  const result = await promptWorkerForEnvelope(cleanFirst, "USER PROMPT", EXPECTED);
  assert.equal(result, validEnvelope);
  assert.deepEqual(cleanFirst.calls, ["USER PROMPT"]);
});

test("a worker that stays prose-only after repair returns the last text for the diagnostic", async () => {
  const proseBoth = new FakePrompter(["first prose", "still prose, no object"]);
  const result = await promptWorkerForEnvelope(proseBoth, "USER PROMPT", EXPECTED);
  assert.equal(result, "still prose, no object");
  assert.equal(proseBoth.calls.length, 2);
});

test("an over-long summary is repaired, not discarded, and the turn says why", async () => {
  // The burn this covers: a worker finished the build, wrote a 2000+ character
  // summary, and the run was thrown away. The old predicate checked only
  // `schema`, so the corrective turn never fired for it.
  const long = envelope({ summary: "x".repeat(2500) });
  const prompter = new FakePrompter([long, validEnvelope]);
  const result = await promptWorkerForEnvelope(prompter, "USER PROMPT", EXPECTED);
  assert.equal(result, validEnvelope);
  assert.equal(prompter.calls.length, 2);
  assert.match(prompter.calls[1], /"summary" is 2500 characters; the limit is 2000/);
  assert.match(prompter.calls[1], /keeping every other field byte-identical/);
});

test("every rule the strict parse enforces also fires the repair turn", () => {
  const cases: [string, string, RegExp][] = [
    ["too many checks", envelope({ checks: new Array(17).fill({ id: "a", command: "b", cwd: "." }) }), /"checks" has 17 entries/],
    ["empty summary", envelope({ summary: "   " }), /"summary" must be a non-empty string/],
    ["bad outcome", envelope({ outcome: "done" }), /"outcome" must be one of/],
    ["unknown key", envelope({ notes: "extra" }), /unknown key\(s\): notes/],
    ["wrong item", envelope({ item: "item-b" }), /"item" must be "item-a"/],
    ["stale profile hash", envelope({ profileHash: "c".repeat(64) }), /"profileHash" does not match/],
    ["not an object", "[]", /must be a single JSON object/],
  ];
  for (const [name, text, expected] of cases) {
    const violation = workerEnvelopeViolation(text, EXPECTED);
    assert.ok(violation, `${name} should be a violation`);
    assert.match(violation, expected, name);
  }
  assert.equal(workerEnvelopeViolation(validEnvelope, EXPECTED), null);
});
