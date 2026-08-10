import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";
import {
  executeGateOnEvidence,
  parseGateVerdict,
  type GateModelInvoker,
} from "../../extensions/pi/gate-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(): Promise<{
  repo: string;
  manifest: KartaEvidenceManifest;
  ctx: ExtensionContext;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-gate-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "item\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Gate"]);
  await git(repo, ["config", "user.email", "gate@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "item"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const tree = await git(repo, ["rev-parse", "HEAD^{tree}"]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/integration", tip]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/item-item-a", tip]);
  const workItem = {
    id: "item-a",
    title: "Gate fixture",
    summary: "Review exact evidence",
    touches: ["subject.txt"],
    oracle: { type: "unit", assertions: ["subject exists"] },
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: tip,
      sha256: "b".repeat(64),
      document: { slug: "demo", work_items: [workItem] },
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: tip,
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: tip,
      mergeBase: tip,
      targetKind: "committed-tip",
      targetTree: tree,
    },
    diff: {
      format: "git-binary-patch",
      sha256: "d".repeat(64),
      bytes: 0,
      touchedPaths: ["subject.txt"],
      content: "",
    },
    checks: { manifest: { status: "not-required", targetTree: tree } },
    files: [],
    citations: [],
    packs: [],
  };
  const manifest: KartaEvidenceManifest = {
    schema: "karta-evidence-v2",
    generatedAt: new Date().toISOString(),
    repositoryRoot: repo,
    evidenceHash: hashEvidencePayload(payload),
    payload,
  };
  return {
    repo,
    manifest,
    ctx: { cwd: repo } as ExtensionContext,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

const runtime: ChildRuntimeReport = {
  provider: "fixture-provider",
  model: "fixture-model",
  policy: "gate",
  exactModelResolved: true,
  parentAuthConfigured: true,
  childAuthConfigured: true,
  copiedProvider: "builtin",
  copiedRuntimeCredential: false,
  unresolvedEnvironmentKeys: [],
};

const preflight = {
  async ensure() {
    return { ...runtime, cached: false };
  },
};

function validInvoker(
  verdict: "pass" | "concerns" | "blocked" = "pass",
  options: { invokeRoleTool?: boolean; mutate?: (invocation: Parameters<GateModelInvoker>[0]) => Promise<void> } = {},
): GateModelInvoker {
  return async (invocation) => {
    const evidenceTool = invocation.profile.tools[0];
    for (const params of [
      { action: "summary" },
      { action: "workItem" },
      { action: "diff" },
    ]) {
      await evidenceTool.execute("evidence", params, undefined, undefined, invocation.ctx);
    }
    if (invocation.profile.role.id === "safety-gate") {
      for (const id of invocation.profile.evidenceToolState.requiredPacks) {
        await evidenceTool.execute("pack", { action: "pack", id }, undefined, undefined, invocation.ctx);
      }
      for (const index of invocation.profile.evidenceToolState.requiredCitations) {
        await evidenceTool.execute(
          "citation",
          { action: "citation", index },
          undefined,
          undefined,
          invocation.ctx,
        );
      }
    }
    if (options.invokeRoleTool !== false) {
      const roleTool = invocation.profile.tools[1];
      await roleTool.execute(
        "role-tool",
        { action: invocation.profile.role.id === "acceptance-gate" ? "summary" : "inspect" },
        undefined,
        undefined,
        invocation.ctx,
      );
    }
    await options.mutate?.(invocation);
    const promptHash = invocation.systemPrompt.match(/"promptHash":"([a-f0-9]{64})"/)?.[1];
    assert.ok(promptHash);
    return {
      runtime,
      text: JSON.stringify({
        schema: "karta-gate-verdict-v1",
        role: invocation.profile.role.id,
        evidenceHash: invocation.profile.evidenceHash,
        roleDefinitionHash: invocation.profile.role.definitionHash,
        promptHash,
        profileHash: invocation.profile.profileHash,
        verdict,
        summary: verdict === "pass" ? "Evidence conforms." : "Evidence needs attention.",
        findings:
          verdict === "concerns"
            ? [
                {
                  severity: "major",
                  code: "oracle.deviation",
                  message: "The assertion is not covered.",
                  path: "subject.txt",
                  line: 1,
                  nextStep: "Add a focused test.",
                },
              ]
            : [],
      }),
    };
  };
}

test("acceptance and safety gates return strict hash-bound envelopes", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    for (const role of ["acceptance-gate", "safety-gate"] as const) {
      const result = await executeGateOnEvidence(
        ctx,
        role,
        manifest,
        preflight,
        new ChildRegistry(),
        validInvoker(),
      );
      assert.equal(result.role, role);
      assert.equal(result.evidenceHash, manifest.evidenceHash);
      assert.equal(result.verdict, "pass");
      assert.equal(result.retry, "none");
      assert.equal(result.provider, runtime.provider);
      assert.equal(result.model, runtime.model);
    }
  } finally {
    await cleanup();
  }
});

test("concerns are host-classified as retryable and require findings", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    const result = await executeGateOnEvidence(
      ctx,
      "safety-gate",
      manifest,
      preflight,
      new ChildRegistry(),
      validInvoker("concerns"),
    );
    assert.equal(result.retry, "retryable");
    assert.equal(result.findings[0].code, "oracle.deviation");
  } finally {
    await cleanup();
  }
});

test("gate fails closed when the required role tool is skipped", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "acceptance-gate",
          manifest,
          preflight,
          new ChildRegistry(),
          validInvoker("pass", { invokeRoleTool: false }),
        ),
      /did not invoke its required role tool/,
    );
  } finally {
    await cleanup();
  }
});

test("acceptance cannot pass without a bound check manifest", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    manifest.payload.workItem.oracle = { type: "unit", command: "npm test" };
    manifest.payload.checks.manifest = {
      status: "missing",
      targetTree: manifest.payload.git.targetTree,
    };
    manifest.evidenceHash = hashEvidencePayload(manifest.payload);
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "acceptance-gate",
          manifest,
          preflight,
          new ChildRegistry(),
          validInvoker("pass"),
        ),
      /cannot pass without a passing bound check manifest/,
    );
  } finally {
    await cleanup();
  }
});

test("safety cannot pass with unresolved repo-rule citation evidence", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    manifest.payload.citations = [
      {
        index: 0,
        path: "AGENTS.md",
        locator: "Security",
        state: "missing",
        sourceTree: manifest.payload.git.targetTree,
        binary: false,
      },
    ];
    manifest.evidenceHash = hashEvidencePayload(manifest.payload);
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "safety-gate",
          manifest,
          preflight,
          new ChildRegistry(),
          validInvoker("pass"),
        ),
      /citation evidence is incomplete/,
    );
  } finally {
    await cleanup();
  }
});

test("moving a bound ref during review invalidates the verdict", async () => {
  const { repo, manifest, ctx, cleanup } = await fixture();
  try {
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "safety-gate",
          manifest,
          preflight,
          new ChildRegistry(),
          validInvoker("pass", {
            async mutate() {
              await writeFile(join(repo, "later.txt"), "moved\n");
              await git(repo, ["add", "."]);
              await git(repo, ["commit", "--no-gpg-sign", "-m", "move integration"]);
              const moved = await git(repo, ["rev-parse", "HEAD"]);
              await git(repo, ["update-ref", "refs/heads/karta/demo/integration", moved]);
            },
          }),
        ),
      /bound Git tip moved/,
    );
  } finally {
    await cleanup();
  }
});

test("verdict parser rejects prose, wrong hashes, unknown keys, and unsafe paths", () => {
  const expected = {
    role: "acceptance-gate" as const,
    evidenceHash: "a".repeat(64),
    roleDefinitionHash: "b".repeat(64),
    promptHash: "c".repeat(64),
    profileHash: "d".repeat(64),
  };
  const valid = {
    schema: "karta-gate-verdict-v1",
    role: expected.role,
    evidenceHash: expected.evidenceHash,
    roleDefinitionHash: expected.roleDefinitionHash,
    promptHash: expected.promptHash,
    profileHash: expected.profileHash,
    verdict: "pass",
    summary: "Conforms.",
    findings: [],
  };
  assert.equal(parseGateVerdict(JSON.stringify(valid), expected).verdict, "pass");
  assert.throws(() => parseGateVerdict(`result: ${JSON.stringify(valid)}`, expected), /exactly one JSON/);
  assert.throws(
    () => parseGateVerdict(JSON.stringify({ ...valid, evidenceHash: "e".repeat(64) }), expected),
    /does not match/,
  );
  assert.throws(
    () => parseGateVerdict(JSON.stringify({ ...valid, extra: true }), expected),
    /expected keys/,
  );
  assert.throws(
    () =>
      parseGateVerdict(
        JSON.stringify({
          ...valid,
          verdict: "concerns",
          findings: [
            {
              severity: "major",
              code: "path.escape",
              message: "Unsafe path.",
              path: "../outside",
            },
          ],
        }),
        expected,
      ),
    /invalid path/,
  );
});

test("runtime identity must still match the provider preflight", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  try {
    const invoke = validInvoker();
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "safety-gate",
          manifest,
          preflight,
          new ChildRegistry(),
          async (invocation) => {
            const result = await invoke(invocation);
            return { ...result, runtime: { ...runtime, model: "switched-model" } };
          },
        ),
      /runtime changed after provider preflight/,
    );
  } finally {
    await cleanup();
  }
});

test("provider preflight failure prevents gate model invocation", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  let invoked = false;
  try {
    await assert.rejects(
      () =>
        executeGateOnEvidence(
          ctx,
          "acceptance-gate",
          manifest,
          { async ensure() { throw new Error("unsupported provider"); } },
          new ChildRegistry(),
          async () => {
            invoked = true;
            throw new Error("must not run");
          },
        ),
      /unsupported provider/,
    );
    assert.equal(invoked, false);
  } finally {
    await cleanup();
  }
});
