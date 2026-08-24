import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry } from "../../extensions/pi/child-runtime.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";
import type { KartaGateResult } from "../../extensions/pi/gate-runner.ts";
import { KartaVerificationRunner } from "../../extensions/pi/verification-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(oracle: Record<string, unknown> = { type: "unit" }): Promise<{
  repo: string;
  manifest: KartaEvidenceManifest;
  ctx: ExtensionContext;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-verification-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Verification"]);
  await git(repo, ["config", "user.email", "verification@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "fixture"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const workItem = { id: "item-a", title: "Fixture", summary: "Verify", oracle };
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
      targetTree: tip,
    },
    diff: {
      format: "git-binary-patch",
      sha256: "d".repeat(64),
      bytes: 0,
      touchedPaths: ["subject.txt"],
      content: "",
    },
    checks: { manifest: { status: "not-required", targetTree: tip } },
    files: [],
    citations: [],
    packs: [],
  };
  return {
    repo,
    manifest: {
      schema: "karta-evidence-v2",
      generatedAt: new Date().toISOString(),
      repositoryRoot: repo,
      evidenceHash: hashEvidencePayload(payload),
      payload,
    },
    ctx: { cwd: repo } as ExtensionContext,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

function gate(role: "acceptance-gate" | "safety-gate", verdict: "pass" | "concerns" | "blocked"): KartaGateResult {
  return {
    schema: "karta-gate-verdict-v1",
    role,
    evidenceHash: "evidence",
    roleDefinitionHash: "a".repeat(64),
    promptHash: "b".repeat(64),
    profileHash: "c".repeat(64),
    verdict,
    summary: verdict,
    findings: verdict === "concerns" ? [{ severity: "major", code: "fixture", message: "finding" }] : [],
    retry: verdict === "pass" ? "none" : verdict === "concerns" ? "retryable" : "halt",
    provider: "fixture",
    model: "fixture",
  };
}

const preflight = { async ensure() { throw new Error("fake gate executor should own preflight"); } };

test("full verification runs acceptance then safety under one lock and one evidence hash", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      async buildEvidence() {
        assert.equal(locks.size, 1);
        return manifest;
      },
      async executeGate(_ctx, role, evidence) {
        assert.equal(locks.size, 1);
        assert.equal(evidence, manifest);
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: evidence.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["acceptance-gate", "safety-gate"]);
    assert.equal(result.status, "pass");
    assert.equal(result.evidenceHash, manifest.evidenceHash);
    assert.equal(result.gates.acceptance?.evidenceHash, result.gates.safety?.evidenceHash);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("delivery-owned verification reuses an explicit lease without reacquiring or releasing it", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  try {
    const lease = await locks.acquire(ctx.cwd, "demo");
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.runWithLease(ctx, "demo", "item-a", "boundary-only", lease);
    assert.equal(result.status, "pass");
    assert.equal(locks.size, 1);
    await locks.release(lease);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("acceptance concern stops before safety and releases the lock", async () => {
  const { manifest, ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "concerns"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["acceptance-gate"]);
    assert.equal(result.status, "concerns");
    assert.equal(result.gates.safety, undefined);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("an explicit boundary-only request dispatches safety alone and returns its verdict", async () => {
  const { manifest, ctx, cleanup } = await fixture({ type: "unit" });
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "boundary-only");
    assert.deepEqual(roles, ["safety-gate"]);
    assert.equal(result.requestedMode, "boundary-only");
    assert.equal(result.effectiveMode, "boundary-only");
    assert.equal(result.status, "pass");
    assert.equal(result.blockedReason, undefined);
    assert.equal(result.gates.acceptance, undefined);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("a full visual verification blocks visual-required after safety passes without acceptance", async () => {
  const { manifest, ctx, cleanup } = await fixture({ type: "visual" });
  const locks = new DispatchLockManager();
  const roles: string[] = [];
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate(_ctx, role) {
        roles.push(role);
        return { ...gate(role, "pass"), evidenceHash: manifest.evidenceHash };
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.deepEqual(roles, ["safety-gate"]);
    assert.equal(result.status, "blocked");
    assert.equal(result.blockedReason, "visual-required");
    assert.equal(result.reason, undefined);
    assert.equal(result.requestedMode, "full");
    assert.equal(result.effectiveMode, "full");
    assert.equal(result.gates.acceptance, undefined);
    assert.equal(result.gates.safety?.verdict, "pass");
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("a full visual verification surfaces a safety failure rather than folding it into visual-required", async () => {
  for (const verdict of ["concerns", "blocked"] as const) {
    const { manifest, ctx, cleanup } = await fixture({ type: "visual" });
    const locks = new DispatchLockManager();
    const roles: string[] = [];
    try {
      const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
        buildEvidence: async () => manifest,
        async executeGate(_ctx, role) {
          roles.push(role);
          return { ...gate(role, verdict), evidenceHash: manifest.evidenceHash };
        },
      });
      const result = await runner.run(ctx, "demo", "item-a", "full");
      assert.deepEqual(roles, ["safety-gate"]);
      assert.equal(result.status, verdict);
      assert.equal(result.blockedReason, undefined);
      assert.equal(result.effectiveMode, "full");
    } finally {
      await locks.releaseAll();
      await cleanup();
    }
  }
});

test("oracle opt-out skips both gates without turning Pi into state storage", async () => {
  const { manifest, ctx, cleanup } = await fixture({ opt_out: true, reason: "external certification" });
  const locks = new DispatchLockManager();
  let gates = 0;
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      buildEvidence: async () => manifest,
      async executeGate() {
        gates += 1;
        throw new Error("must not dispatch");
      },
    });
    const result = await runner.run(ctx, "demo", "item-a", "full");
    assert.equal(result.status, "skipped");
    assert.match(result.reason ?? "", /external certification/);
    assert.equal(gates, 0);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});

test("evidence failure releases the dispatch lock", async () => {
  const { ctx, cleanup } = await fixture();
  const locks = new DispatchLockManager();
  try {
    const runner = new KartaVerificationRunner(preflight, new ChildRegistry(), locks, {
      async buildEvidence() {
        throw new Error("bad evidence");
      },
    });
    await assert.rejects(() => runner.run(ctx, "demo", "item-a", "full"), /bad evidence/);
    assert.equal(locks.size, 0);
  } finally {
    await locks.releaseAll();
    await cleanup();
  }
});
