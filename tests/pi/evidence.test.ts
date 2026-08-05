import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  buildKartaEvidence,
  canonicalJson,
  createEvidenceReadTool,
  verifyEvidenceFreshness,
  verifyEvidenceIntegrity,
} from "../../extensions/pi/evidence.ts";

const exec = promisify(execFile);
const TOOL_CONTEXT = {} as ExtensionContext;

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(): Promise<{ repo: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-evidence-"));
  const repo = join(root, "repo with spaces");
  await mkdir(join(repo, ".karta", "binders"), { recursive: true });
  await mkdir(join(repo, ".karta", "sme"), { recursive: true });
  await mkdir(join(repo, "src"), { recursive: true });
  const binder = {
    slug: "demo",
    title: "Evidence fixture",
    summary: "Exercise evidence",
    motivation: "Test",
    scope: { included: ["src"] },
    sme: ["minimalism", "project-pack"],
    work_items: [
      {
        id: "item-a",
        title: "Change file",
        summary: "Change the fixture file",
        contract: { output: "updated text" },
        shared_resources: ["fixture"],
        surface: { flagged: true, signals: ["contract-change"] },
        oracle: {
          type: "unit",
          assertions: ["file contains changed"],
          command: "test -f src/file.txt",
        },
      },
    ],
  };
  await writeFile(join(repo, ".karta", "binders", "demo.json"), `${JSON.stringify(binder, null, 2)}\n`);
  await writeFile(
    join(repo, ".karta", "sme", "project-pack.md"),
    "---\nname: project-pack\ndescription: fixture\n---\n## Review checklist\n- [ ] project.1 — Review.\n",
  );
  await writeFile(join(repo, "src", "file.txt"), "base\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Evidence"]);
  await git(repo, ["config", "user.email", "evidence@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await git(repo, ["branch", "karta/demo/integration"]);
  await git(repo, ["checkout", "-b", "karta/demo/item-item-a"]);
  await writeFile(join(repo, "src", "file.txt"), "changed\n");
  await writeFile(join(repo, "src", "new.txt"), "new\n");
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "item"]);
  return { repo, cleanup: () => rm(root, { recursive: true, force: true }) };
}

function text(result: Awaited<ReturnType<ReturnType<typeof createEvidenceReadTool>["execute"]>>): string {
  const part = result.content[0];
  return part.type === "text" ? part.text : "";
}

test("evidence binds binder, item, tips, diff, and project/package packs", async () => {
  const { repo, cleanup } = await fixture();
  try {
    const evidence = await buildKartaEvidence({ cwd: repo, binder: "demo", item: "item-a" });
    verifyEvidenceIntegrity(evidence);
    await verifyEvidenceFreshness(evidence);
    assert.equal(evidence.schema, "karta-evidence-v1");
    assert.equal(evidence.payload.binder.slug, "demo");
    assert.equal(evidence.payload.workItem.id, "item-a");
    assert.match(evidence.evidenceHash, /^[a-f0-9]{64}$/);
    assert.deepEqual(evidence.payload.diff.touchedPaths, ["src/file.txt", "src/new.txt"]);
    assert.match(evidence.payload.diff.content, /changed/);
    assert.deepEqual(
      evidence.payload.packs.map((pack) => [pack.id, pack.source]),
      [
        ["minimalism", "package"],
        ["project-pack", "project"],
      ],
    );
    assert.match(evidence.payload.packs[1].blob ?? "", /^[a-f0-9]{40,64}$/);
  } finally {
    await cleanup();
  }
});

test("project policy packs are pinned to integration, not item-controlled content", async () => {
  const { repo, cleanup } = await fixture();
  try {
    await writeFile(
      join(repo, ".karta", "sme", "project-pack.md"),
      "---\nname: project-pack\ndescription: weakened\n---\n## Review checklist\n",
    );
    await git(repo, ["add", "."]);
    await git(repo, ["commit", "--no-gpg-sign", "-m", "attempt pack change"]);
    const evidence = await buildKartaEvidence({ cwd: repo, binder: "demo", item: "item-a" });
    const pack = evidence.payload.packs.find((candidate) => candidate.id === "project-pack");
    assert.match(pack?.content ?? "", /project\.1/);
    assert.equal(pack?.content.includes("weakened"), false);
    assert.ok(evidence.payload.diff.touchedPaths.includes(".karta/sme/project-pack.md"));
  } finally {
    await cleanup();
  }
});

test("evidence integrity and freshness fail after payload or ref movement", async () => {
  const { repo, cleanup } = await fixture();
  try {
    const evidence = await buildKartaEvidence({ cwd: repo, binder: "demo", item: "item-a" });
    const original = evidence.payload.diff.content;
    evidence.payload.diff.content = `${original}\ntampered`;
    assert.throws(() => verifyEvidenceIntegrity(evidence), /hash mismatch/);
    evidence.payload.diff.content = original;
    const itemTip = evidence.payload.git.itemTip;
    await git(repo, ["update-ref", "refs/heads/karta/demo/integration", itemTip]);
    await assert.rejects(() => verifyEvidenceFreshness(evidence), /bound Git tip moved/);
  } finally {
    await cleanup();
  }
});

test("evidence reader exposes fixed sections and bounded diff pages only", async () => {
  const { repo, cleanup } = await fixture();
  try {
    const evidence = await buildKartaEvidence({ cwd: repo, binder: "demo", item: "item-a" });
    const tool = createEvidenceReadTool(evidence);
    const summary = await tool.execute(
      "summary",
      { action: "summary" },
      undefined,
      undefined,
      TOOL_CONTEXT,
    );
    const summaryBody = JSON.parse(text(summary));
    assert.equal(summaryBody.evidenceHash, evidence.evidenceHash);
    assert.equal("repositoryRoot" in summaryBody, false);

    const diff = await tool.execute(
      "diff",
      { action: "diff", offset: 0, limit: 20 },
      undefined,
      undefined,
      TOOL_CONTEXT,
    );
    assert.equal(text(diff).length, 20);
    assert.equal(diff.details?.totalLength, evidence.payload.diff.content.length);
    assert.equal(diff.details?.nextOffset, 20);

    const pack = await tool.execute(
      "pack",
      { action: "pack", id: "minimalism" },
      undefined,
      undefined,
      TOOL_CONTEXT,
    );
    assert.match(text(pack), /minimalism/);
    const denied = await tool.execute(
      "pack",
      { action: "pack", id: "not-pinned" },
      undefined,
      undefined,
      TOOL_CONTEXT,
    );
    assert.equal((denied as { isError?: boolean }).isError, true);
    assert.match(text(denied), /not in this evidence manifest/);

    const schema = JSON.stringify(tool.parameters);
    for (const forbidden of ["path", "command", "ref", "prompt"] as const) {
      assert.equal(schema.includes(`\"${forbidden}\"`), false, forbidden);
    }
  } finally {
    await cleanup();
  }
});

test("evidence construction rejects traversal, missing items, and oversized diffs", async () => {
  const { repo, cleanup } = await fixture();
  try {
    await assert.rejects(
      () => buildKartaEvidence({ cwd: repo, binder: "../demo", item: "item-a" }),
      /Invalid Karta binder slug/,
    );
    await git(repo, ["branch", "karta/demo/item-missing", "HEAD"]);
    await assert.rejects(
      () => buildKartaEvidence({ cwd: repo, binder: "demo", item: "missing" }),
      /must contain item 'missing' exactly once/,
    );
    await assert.rejects(
      () => buildKartaEvidence({ cwd: repo, binder: "demo", item: "item-a", maxDiffBytes: 1 }),
      /limit is 1/,
    );
  } finally {
    await cleanup();
  }
});

test("canonical evidence JSON ignores object insertion order", () => {
  assert.equal(
    canonicalJson({ z: 1, a: { d: 2, b: 3 } }),
    canonicalJson({ a: { b: 3, d: 2 }, z: 1 }),
  );
});
