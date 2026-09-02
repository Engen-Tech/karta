import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../../extensions/pi/evidence.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

export async function createGateEvidenceFixture(): Promise<{
  root: string;
  repo: string;
  manifest: KartaEvidenceManifest;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-gate-live-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "before\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Live Gate"]);
  await git(repo, ["config", "user.email", "live-gate@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  const base = await git(repo, ["rev-parse", "HEAD"]);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["add", "subject.txt"]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "item"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const tree = await git(repo, ["rev-parse", "HEAD^{tree}"]);
  const diff = await git(repo, ["diff", "--binary", "--no-ext-diff", base, tip]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/integration", base]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/item-item-a", tip]);
  const workItem = {
    id: "item-a",
    title: "Live gate fixture",
    summary: "Exercise a real isolated child",
    touches: ["subject.txt"],
    oracle: { type: "unit", assertions: ["subject is present"] },
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
      integrationTip: base,
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: tip,
      mergeBase: base,
      targetKind: "committed-tip",
      targetTree: tree,
    },
    diff: {
      format: "git-binary-patch",
      sha256: createHash("sha256").update(diff).digest("hex"),
      bytes: Buffer.byteLength(diff),
      touchedPaths: ["subject.txt"],
      content: diff,
    },
    checks: { manifest: { status: "not-required", targetTree: tree } },
    files: [],
    citations: [],
    packs: [],
  };
  return {
    root,
    repo,
    manifest: {
      schema: "karta-evidence-v2",
      generatedAt: new Date().toISOString(),
      repositoryRoot: repo,
      evidenceHash: hashEvidencePayload(payload),
      payload,
    },
  };
}
