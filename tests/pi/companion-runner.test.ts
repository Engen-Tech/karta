import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import {
  KartaCompanionRunner,
  type KartaCompanionCheckpoint,
} from "../../extensions/pi/companion-runner.ts";
import type { KartaBuildItemRunner } from "../../extensions/pi/build-runner.ts";
import { KartaDeliveryRunner } from "../../extensions/pi/delivery-runner.ts";
import { DispatchLockManager } from "../../extensions/pi/dispatch-lock.ts";
import type { KartaIntegrationRunner } from "../../extensions/pi/integration-runner.ts";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import type { KartaWaveRunner } from "../../extensions/pi/wave-runner.ts";
import type { KartaBuildWorkerRunner } from "../../extensions/pi/worker-runner.ts";
import { KartaWriterRunner, type WriterModelInvoker } from "../../extensions/pi/writer-runner.ts";

const exec = promisify(execFile);
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

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", ["-C", cwd, ...args], { encoding: "utf8" })).stdout.trim();
}

async function fixture(options: { docs?: boolean; kaizen?: boolean; sme?: string[] } = {}): Promise<{
  repo: string;
  base: string;
  cleanup(): Promise<void>;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-companion-"));
  const repo = join(root, "repo");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(join(repo, ".karta", "binders", "demo.json"), `${JSON.stringify({
    slug: "demo",
    sme: options.sme ?? [],
    work_items: [],
  })}\n`);
  if (options.docs !== undefined) {
    await writeFile(join(repo, ".karta", "doc-gardner.json"), `${JSON.stringify({ enabled: options.docs })}\n`);
  }
  if (options.kaizen !== undefined) {
    await writeFile(join(repo, ".karta", "kaizen.json"), `${JSON.stringify({ enabled: options.kaizen })}\n`);
  }
  await writeFile(join(repo, "README.md"), "# Before\n");
  await writeFile(join(repo, "source.ts"), "export const value = 1;\n");
  await git(repo, ["init", "--initial-branch=karta/demo/integration"]);
  await git(repo, ["config", "user.name", "Karta Companion"]);
  await git(repo, ["config", "user.email", "companion@example.invalid"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  const base = await git(repo, ["rev-parse", "HEAD"]);
  await writeFile(join(repo, "source.ts"), "export const value = 2;\n");
  await git(repo, ["add", "source.ts"]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "delivery"]);
  return { repo, base, cleanup: () => rm(root, { recursive: true, force: true }) };
}

async function runCompanions(
  state: { repo: string; base: string },
  invoke: WriterModelInvoker,
  sme: string[] = [],
) {
  const locks = new DispatchLockManager();
  const registry = new ChildRegistry(new LifecycleRegistry());
  const processes = new KartaProcessManager(registry.lifecycles, 10);
  const owner = processes.createBinderOwner(state.repo, "demo");
  const lease = await locks.acquire(state.repo, "demo");
  try {
    const runner = new KartaCompanionRunner(locks, new KartaWriterRunner(registry, invoke));
    return await runner.finishDelivery(
      { cwd: state.repo } as ExtensionContext,
      "demo",
      state.repo,
      lease,
      { manager: processes, owner },
      { diffBase: state.base, sme },
    );
  } finally {
    await processes.stopOwner(owner);
    await locks.release(lease);
  }
}

function envelope(invocation: Parameters<WriterModelInvoker>[0], fields: Record<string, unknown>): string {
  return JSON.stringify({
    schema: "karta-writer-result-v1",
    role: invocation.profile.writer,
    binder: "demo",
    roleDefinitionHash: invocation.profile.role.definitionHash,
    profileHash: invocation.profile.profileHash,
    ...fields,
  });
}

test("doc-gardner commits an attested exact tree before the final binder archive", async () => {
  const state = await fixture({ docs: true, kaizen: false });
  try {
    const result = await runCompanions(state, async (invocation) => {
      assert.equal(invocation.profile.writer, "doc-gardner");
      const request = JSON.parse(invocation.userPrompt);
      assert.ok(request.changedPaths.includes("source.ts"));
      const write = invocation.profile.tools.find((tool) => tool.name === "write");
      assert.ok(write);
      await write.execute(
        "docs",
        { path: "README.md", content: "# After\n" },
        undefined,
        undefined,
        { cwd: invocation.profile.worktree } as ExtensionContext,
      );
      return {
        runtime,
        text: envelope(invocation, {
          correctedCount: 1,
          filesChanged: ["README.md"],
          residual: [],
          summary: "Updated the README to match the delivered code.",
        }),
      };
    });
    assert.equal(result.docGardner.status, "committed");
    assert.equal(result.kaizen.status, "disabled");
    assert.equal(result.archive.status, "committed");
    assert.equal(await readFile(join(state.repo, "README.md"), "utf8"), "# After\n");
    await assert.rejects(() => readFile(join(state.repo, ".karta", "binders", "demo.json"), "utf8"));
    assert.match(await readFile(join(state.repo, ".karta", "binders", "archive", "demo.json"), "utf8"), /"slug":"demo"/);
    assert.equal(await git(state.repo, ["show", "-s", "--format=%s", "HEAD"]), "chore(karta): archive binder demo — delivered");
    assert.equal(await git(state.repo, ["show", "-s", "--format=%s", "HEAD^"]), "docs: gardner demo");
  } finally {
    await state.cleanup();
  }
});

test("a no-drift gardner run records the required empty exact-tree commit", async () => {
  const state = await fixture({ docs: true, kaizen: false });
  try {
    const result = await runCompanions(state, async (invocation) => ({
      runtime,
      text: envelope(invocation, {
        correctedCount: 0,
        filesChanged: [],
        residual: [],
        summary: "The documentation already matches the delivered code.",
      }),
    }));
    assert.equal(result.docGardner.status, "committed");
    assert.equal(await git(state.repo, ["show", "-s", "--format=%s", "HEAD^"]), "docs: gardner demo");
    const docCommit = await git(state.repo, ["rev-parse", "HEAD^"]);
    const docParent = await git(state.repo, ["rev-parse", `${docCommit}^`]);
    assert.equal(
      await git(state.repo, ["show", "-s", "--format=%T", docCommit]),
      await git(state.repo, ["show", "-s", "--format=%T", docParent]),
    );
  } finally {
    await state.cleanup();
  }
});

test("out-of-surface writer mutation fails closed before the integration ref moves", async () => {
  const state = await fixture({ docs: true, kaizen: false });
  const before = await git(state.repo, ["rev-parse", "HEAD"]);
  try {
    await assert.rejects(
      () => runCompanions(state, async (invocation) => {
        await writeFile(join(invocation.profile.worktree, "source.ts"), "malicious\n");
        return {
          runtime,
          text: envelope(invocation, {
            correctedCount: 0,
            filesChanged: [],
            residual: [],
            summary: "No documentation changes.",
          }),
        };
      }),
      /out-of-surface path/,
    );
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
    assert.equal(await readFile(join(state.repo, "source.ts"), "utf8"), "export const value = 2;\n");
  } finally {
    await state.cleanup();
  }
});

test("archive ref-first interruption is repaired from Git by a fresh delivery owner", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-archive-recovery-"));
  const repo = join(root, "repo");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await writeFile(join(repo, ".karta", "binders", "demo.json"), `${JSON.stringify({ slug: "demo", sme: [], work_items: [] })}\n`);
  await writeFile(join(repo, "base.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Recovery"]);
  await git(repo, ["config", "user.email", "recovery@example.invalid"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  const makeDelivery = (checkpoint: KartaCompanionCheckpoint = () => {}) => {
    const registry = new ChildRegistry(new LifecycleRegistry());
    const locks = new DispatchLockManager();
    const processes = new KartaProcessManager(registry.lifecycles, 10);
    const writer = new KartaWriterRunner(registry, async () => {
      throw new Error("disabled writers must not spawn");
    });
    const companion = new KartaCompanionRunner(locks, writer, checkpoint);
    return new KartaDeliveryRunner(
      locks,
      processes,
      {} as KartaBuildItemRunner,
      {} as KartaIntegrationRunner,
      {} as KartaBuildWorkerRunner,
      {} as KartaWaveRunner,
      companion,
    );
  };
  try {
    const interrupted = makeDelivery((name) => {
      if (name === "archive-ref-updated") throw new Error("injected archive crash");
    });
    await assert.rejects(
      () => interrupted.run({ cwd: repo } as ExtensionContext, "demo"),
      /injected archive crash/,
    );
    assert.equal(
      await git(repo, ["show", "-s", "--format=%s", "refs/heads/karta/demo/integration"]),
      "chore(karta): archive binder demo — delivered",
    );
    const recovered = await makeDelivery().run({ cwd: repo } as ExtensionContext, "demo");
    assert.equal(recovered.status, "complete");
    assert.match(recovered.message, /archived/);
    assert.match(
      await readFile(join(root, "repo-worktrees", "karta-demo-integration", ".karta", "binders", "archive", "demo.json"), "utf8"),
      /"slug":"demo"/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("kaizen cannot weaken an existing project rule", async () => {
  const state = await fixture({ docs: false, kaizen: true, sme: ["project"] });
  await mkdir(join(state.repo, ".karta", "sme"), { recursive: true });
  const pack = [
    "---",
    "name: project",
    "description: Project rules",
    "always: true",
    "---",
    "## Review checklist",
    "- [ ] proj.1 — Must enforce authentication at every external boundary.",
    "",
  ].join("\n");
  await writeFile(join(state.repo, ".karta", "sme", "project.md"), pack);
  await git(state.repo, ["add", ".karta/sme/project.md"]);
  await git(state.repo, ["commit", "--no-gpg-sign", "-m", "project pack"]);
  const before = await git(state.repo, ["rev-parse", "HEAD"]);
  try {
    await assert.rejects(
      () => runCompanions(state, async (invocation) => {
        const write = invocation.profile.tools.find((tool) => tool.name === "write");
        assert.ok(write);
        await write.execute(
          "weaken",
          { path: ".karta/sme/project.md", content: pack.replace("Must enforce", "May enforce") },
          undefined,
          undefined,
          { cwd: invocation.profile.worktree } as ExtensionContext,
        );
        return {
          runtime,
          text: envelope(invocation, {
            seeded: [],
            packsChanged: [".karta/sme/project.md"],
            candidates: [],
            erosionNotes: [],
            upstreamCandidates: [],
            proposedScaffolds: [],
            residual: [],
            summary: "Changed a project rule.",
          }),
        };
      }, ["project"]),
      /weakened or removed rule/,
    );
    assert.equal(await git(state.repo, ["rev-parse", "HEAD"]), before);
  } finally {
    await state.cleanup();
  }
});

test("kaizen may seed only an exact pinned pack with package provenance", async () => {
  const state = await fixture({ docs: false, kaizen: true, sme: ["minimalism"] });
  try {
    const result = await runCompanions(state, async (invocation) => {
      assert.equal(invocation.profile.writer, "kaizen");
      const request = JSON.parse(invocation.userPrompt);
      const source = request.packs[0] as { id: string; content: string; sha256: string };
      const lines = source.content.split("\n");
      const close = lines.indexOf("---", 1);
      lines.splice(close, 0, `seeded_from: ${source.id}`, `base_sha256: ${source.sha256}`);
      const write = invocation.profile.tools.find((tool) => tool.name === "write");
      assert.ok(write);
      await write.execute(
        "seed",
        { path: ".karta/sme/minimalism.md", content: lines.join("\n") },
        undefined,
        undefined,
        { cwd: invocation.profile.worktree } as ExtensionContext,
      );
      return {
        runtime,
        text: envelope(invocation, {
          seeded: ["minimalism"],
          packsChanged: [".karta/sme/minimalism.md"],
          candidates: [],
          erosionNotes: [],
          upstreamCandidates: [],
          proposedScaffolds: [],
          residual: [],
          summary: "Seeded the pinned minimalism pack.",
        }),
      };
    }, ["minimalism"]);
    assert.equal(result.docGardner.status, "disabled");
    assert.equal(result.kaizen.status, "committed");
    assert.match(await readFile(join(state.repo, ".karta", "sme", "minimalism.md"), "utf8"), /seeded_from: minimalism/);
    assert.equal(await git(state.repo, ["show", "-s", "--format=%s", "HEAD^"]), "kaizen: seed 1 packs into .karta/sme/");
  } finally {
    await state.cleanup();
  }
});
