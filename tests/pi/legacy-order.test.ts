import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import { bindCheckReceipt, runBoundCheck } from "../../extensions/pi/check-runner.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import { buildKartaEvidence } from "../../extensions/pi/evidence.ts";
import type { GateModelInvoker } from "../../extensions/pi/gate-runner.ts";
import { KartaVerificationRunner } from "../../extensions/pi/verification-runner.ts";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

const runtime: ChildRuntimeReport = {
  provider: "fixture",
  model: "fixture",
  policy: "gate",
  exactModelResolved: true,
  parentAuthConfigured: true,
  childAuthConfigured: true,
  copiedProvider: "builtin",
  copiedRuntimeCredential: false,
  unresolvedEnvironmentKeys: [],
};

const invoke: GateModelInvoker = async (invocation) => {
  const evidenceTool = invocation.profile.tools[0];
  for (const params of [
    { action: "summary" },
    { action: "workItem" },
    { action: "diff" },
  ]) {
    await evidenceTool.execute("evidence", params, undefined, undefined, invocation.ctx);
  }
  const roleTool = invocation.profile.tools[1];
  await roleTool.execute(
    "role-tool",
    { action: invocation.profile.role.id === "acceptance-gate" ? "summary" : "inspect" },
    undefined,
    undefined,
    invocation.ctx,
  );
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
      verdict: "pass",
      summary: "The staged candidate and bound check conform.",
      findings: [],
    }),
  };
};

test("legacy build order gates a scanned staged tree and commits that exact tree", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-legacy-order-"));
  const repo = join(root, "repo");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  const binder = {
    slug: "demo",
    title: "Legacy order",
    summary: "Bind a pre-commit candidate",
    motivation: "Prove tree identity",
    scope: { included: ["subject.txt"] },
    work_items: [
      {
        id: "item-a",
        title: "Change subject",
        summary: "Change the subject",
        touches: ["subject.txt"],
        oracle: {
          type: "unit",
          assertions: ["subject contains candidate"],
          command: "node check.mjs",
        },
      },
    ],
  };
  await writeFile(join(repo, ".karta", "binders", "demo.json"), `${JSON.stringify(binder, null, 2)}\n`);
  await writeFile(
    join(repo, "check.mjs"),
    "import { readFileSync } from 'node:fs'; if (readFileSync('subject.txt', 'utf8') !== 'candidate\\n') process.exit(7);\n",
  );
  await writeFile(join(repo, "subject.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Legacy Order"]);
  await git(repo, ["config", "user.email", "legacy-order@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  await git(repo, ["checkout", "-b", "karta/demo/item-item-a"]);

  const locks = new DispatchLockManager();
  const lease = await locks.acquire(repo, "demo");
  try {
    await writeFile(join(repo, "subject.txt"), "candidate\n");
    const check = await runBoundCheck({ worktree: repo, command: "node check.mjs" });
    assert.equal(check.status, "passed");
    await git(repo, ["add", "."]);
    await exec(
      "uv",
      ["run", "--script", join(ROOT, "skills", "karta-build", "scripts", "scan_secrets.py")],
      { cwd: repo },
    );
    const candidate = await buildKartaEvidence({
      cwd: repo,
      binder: "demo",
      item: "item-a",
      target: "candidate",
    });
    const receipt = bindCheckReceipt(check, candidate.payload.git.targetTree);
    const ctx = { cwd: repo } as ExtensionContext;
    const runner = new KartaVerificationRunner(
      { async ensure() { return { ...runtime, cached: false }; } },
      new ChildRegistry(),
      locks,
      { invoke },
    );
    const verification = await runner.runWithLease(
      ctx,
      "demo",
      "item-a",
      "full",
      lease,
      { cwd: repo, target: "candidate", checkReceipt: receipt },
    );
    assert.equal(verification.status, "pass");
    assert.equal(locks.size, 1);
    await assert.rejects(
      () => git(repo, ["rev-parse", "--verify", "refs/karta/demo/item-item-a/built"]),
    );

    await git(repo, ["commit", "--no-gpg-sign", "-m", "[karta:item-item-a] change subject"]);
    const committedTree = await git(repo, ["rev-parse", "HEAD^{tree}"]);
    assert.equal(committedTree, candidate.payload.git.targetTree);
    const tip = await git(repo, ["rev-parse", "HEAD"]);
    await git(repo, ["update-ref", "refs/karta/demo/item-item-a/built", tip]);
    assert.equal(
      await git(repo, ["rev-parse", "refs/karta/demo/item-item-a/built"]),
      tip,
    );
  } finally {
    await locks.release(lease);
    await locks.releaseAll();
    await rm(root, { recursive: true, force: true });
  }
});
