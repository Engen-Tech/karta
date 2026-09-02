import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type ChildRuntimeReport } from "../../extensions/pi/child-runtime.ts";
import {
  assertMonotonicProjectPack,
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
import {
  KartaWriterRunner,
  writerEnvelopeViolation,
  type WriterModelInvoker,
} from "../../extensions/pi/writer-runner.ts";

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
    const result = await runCompanions(state, async (invocation) => {
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
    });
    // The safety property is unchanged and is the one that matters: the mutation
    // never reaches the ref. What changed is that a refused optional companion is
    // recorded rather than thrown, so it cannot destroy a finished delivery.
    assert.equal(result.docGardner.status, "rejected");
    assert.match(result.docGardner.reason ?? "", /out-of-surface path/);
    // The refused writer contributed no commit. The delivery went on to finish,
    // so the only thing after `before` is the binder archive.
    assert.deepEqual(
      (await git(state.repo, ["log", "--format=%s", `${before}..HEAD`])).split("\n").filter(Boolean),
      ["chore(karta): archive binder demo — delivered"],
    );
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

test("kaizen cannot rewrite an existing project rule, in either direction", async () => {
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
    const result = await runCompanions(state, async (invocation) => {
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
      }, ["project"]);
    // kaizen is still refused — INV-23 is untouched — but the refusal is a
    // recorded outcome, and the pack on the branch is the unweakened one.
    assert.equal(result.kaizen.status, "rejected");
    assert.match(result.kaizen.reason ?? "", /rewrote rule 'proj\.1'.*immutable/s);
    assert.deepEqual(
      (await git(state.repo, ["log", "--format=%s", `${before}..HEAD`])).split("\n").filter(Boolean),
      ["chore(karta): archive binder demo — delivered"],
    );
    assert.match(await readFile(join(state.repo, ".karta", "sme", "project.md"), "utf8"), /Must enforce/);
    // The delivery still finished: the binder is archived despite the refusal.
    assert.equal(result.archive.status, "committed");
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

test("a writer result the strict parse would reject gets the corrective turn, named", () => {
  // Fourth instance of one defect: the writer predicate recognised only
  // `schema`, so the summary cap, the per-writer key set and the string-array
  // bounds were all enforced after the single corrective turn had passed.
  const profile = {
    writer: "doc-gardner" as const,
    role: { definitionHash: "a".repeat(64) },
    profileHash: "b".repeat(64),
  } as unknown as Parameters<typeof writerEnvelopeViolation>[1] extends null ? never
    : NonNullable<Parameters<typeof writerEnvelopeViolation>[1]>["profile"];
  const runtime = {} as NonNullable<Parameters<typeof writerEnvelopeViolation>[1]>["runtime"];
  const expected = { binder: "demo", profile, runtime };

  const result = (overrides: Record<string, unknown> = {}) =>
    JSON.stringify({
      schema: "karta-writer-result-v1",
      role: "doc-gardner",
      binder: "demo",
      roleDefinitionHash: "a".repeat(64),
      profileHash: "b".repeat(64),
      summary: "Corrected two drifted paragraphs.",
      correctedCount: 2,
      filesChanged: ["README.md"],
      residual: [],
      ...overrides,
    });

  assert.equal(writerEnvelopeViolation(result(), expected), null);
  assert.match(
    writerEnvelopeViolation(result({ summary: "x".repeat(2500) }), expected) ?? "",
    /"summary" is 2500 characters; the limit is 2000/,
  );
  assert.match(
    writerEnvelopeViolation(result({ profileHash: "c".repeat(64) }), expected) ?? "",
    /"profileHash" does not match/,
  );
  assert.match(
    writerEnvelopeViolation(result({ notes: "extra" }), expected) ?? "",
    /unexpected or missing keys/,
  );
  assert.match(
    writerEnvelopeViolation(result({ residual: [1] }), expected) ?? "",
    /field 'residual'/,
  );
  // With no dispatch expectations only the shape is judged, so a caller without
  // them cannot manufacture a rejection.
  assert.equal(writerEnvelopeViolation(result({ profileHash: "c".repeat(64) }), null), null);
  assert.match(writerEnvelopeViolation("[]", null) ?? "", /single JSON object/);
});

test("the pack guard is byte-exact, so an appended exception cannot weaken a rule", () => {
  // The hole this closes: the guard used to test substring containment, so any
  // weakening phrased as an appended clause passed — the natural way to widen a
  // carve-out. It also called a strict tightening, and a typo fix, "weakened".
  // No mechanical test separates a tightening from a loosening, so the guard
  // stopped claiming to and now enforces one thing it can actually prove.
  const head = ["---", "name: p", "description: d", "always: true", "---", "## Review checklist"].join("\n");
  const pack = (rule: string) => `${head}\n- [ ] p.1 — ${rule}\n`;
  const base = "New non-trivial logic must leave one runnable check.";
  for (
    const [name, edit] of [
      ["weaken by appending an exception", `${base} This does not apply to validator branches.`],
      ["tighten by appending", `${base} No exceptions apply.`],
      ["tighten by rewording", "All new logic, trivial or not, must leave one runnable check."],
      ["weaken by rewording", "New non-trivial logic should usually leave one runnable check."],
      ["typo fix", `${base}!`],
    ] as const
  ) {
    assert.throws(
      () => assertMonotonicProjectPack("p.md", pack(base), pack(edit)),
      /rewrote rule 'p\.1'.*immutable/s,
      name,
    );
  }
  // Adding a rule beside the old one is the sanctioned path and still passes.
  assert.doesNotThrow(() =>
    assertMonotonicProjectPack("p.md", pack(base), `${pack(base)}- [ ] p.2 — A narrower companion rule.\n`)
  );
  // Dropping a rule is named as a removal, not as a rewrite.
  assert.throws(
    () => assertMonotonicProjectPack("p.md", pack(base), `${head}\n`),
    /removed rule 'p\.1'.*never dropped/s,
  );
});

test("the override feed drops placeholders, prose and mirror duplicates", async () => {
  const state = await fixture({ docs: false, kaizen: true, sme: [] });
  try {
    // One real override, copied into both generated mirrors exactly as INV-19
    // requires; plus the documentation that explains the marker grammar. On this
    // repo that documentation was 45 of 60 records, and the mirrors turned one
    // marker into three occurrences — enough to clear the sharpening threshold
    // by itself.
    const marker = "# KARTA-SME-OVERRIDE(min.4): no test framework in this script\n";
    for (
      const path of [
        "skills/karta-status/scripts/serve_status.py",
        ".agents/skills/karta-status/scripts/serve_status.py",
        "plugins/karta/skills/karta-status/scripts/serve_status.py",
      ]
    ) {
      await mkdir(join(state.repo, dirname(path)), { recursive: true });
      await writeFile(join(state.repo, path), marker);
    }
    await writeFile(join(state.repo, "README.md"), "Use `KARTA-SME-OVERRIDE(<rule-id>): <why>` to declare.\n");
    await mkdir(join(state.repo, "docs", "how-to"), { recursive: true });
    await writeFile(
      join(state.repo, "docs", "how-to", "stack-packs.md"),
      "For example `KARTA-SME-OVERRIDE(min.1): mirrors the block above`.\n",
    );
    await git(state.repo, ["add", "--all"]);
    await git(state.repo, ["commit", "--no-gpg-sign", "-m", "markers"]);

    let seen: { path: string; rule: string; delivery: string; inBlastRadius: boolean }[] = [];
    await runCompanions(state, async (invocation) => {
      seen = JSON.parse(invocation.userPrompt).overrideEvidence ?? [];
      return {
        runtime,
        text: envelope(invocation, {
          seeded: [],
          packsChanged: [],
          candidates: [],
          erosionNotes: [],
          upstreamCandidates: [],
          proposedScaffolds: [],
          residual: [],
          summary: "Nothing to sharpen.",
        }),
      };
    });

    // The placeholder and the doc example are gone; the mirrors collapse to one.
    assert.deepEqual(seen.map((entry) => `${entry.rule} ${entry.path}`), [
      "min.4 skills/karta-status/scripts/serve_status.py",
    ]);
    // No `done` ref introduced that commit, so attribution falls back to the
    // blast radius — the marker's file did change in this delivery's range. What
    // it must never do again is name whichever delivery happens to still have
    // item branches, which credited every marker in the repo to one delivery.
    assert.equal(seen[0].delivery, "demo");
    assert.equal(seen[0].inBlastRadius, true);
  } finally {
    await state.cleanup();
  }
});
