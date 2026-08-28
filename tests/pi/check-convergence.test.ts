import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { runStableTreeChecks } from "../../extensions/pi/check-convergence.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(script: string): Promise<{ repo: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-check-convergence-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "candidate\n");
  await writeFile(join(repo, "check.mjs"), script);
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Convergence"]);
  await git(repo, ["config", "user.email", "convergence@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "base"]);
  await writeFile(join(repo, "subject.txt"), "changed\n");
  return { repo, cleanup: () => rm(root, { recursive: true, force: true }) };
}

const plan = [{ id: "unit", purpose: "floor" as const, command: "node check.mjs", cwd: "." }];

test("generated tracked artifacts converge before final receipts bind", async () => {
  const state = await fixture(
    "import { existsSync, writeFileSync } from 'node:fs'; if (!existsSync('generated.txt')) writeFileSync('generated.txt', 'stable\\n');\n",
  );
  try {
    const checkpoints: string[] = [];
    const result = await runStableTreeChecks({
      worktree: state.repo,
      checks: plan,
      maxPasses: 3,
      checkpoint(point) {
        checkpoints.push(point);
      },
    });
    assert.equal(result.status, "stable");
    if (result.status !== "stable") return;
    assert.equal(result.passes, 2);
    assert.equal(result.manifest.entries.length, 1);
    assert.equal(result.manifest.entries[0].preTree, result.targetTree);
    assert.equal(result.manifest.entries[0].postTree, result.targetTree);
    assert.equal(result.manifest.entries[0].receipt.targetTree, result.targetTree);
    assert.equal(await git(state.repo, ["write-tree"]), result.targetTree);
    assert.ok(checkpoints.includes("tree-drifted"));
    assert.equal(checkpoints.at(-1), "manifest-bound");
  } finally {
    await state.cleanup();
  }
});

test("a generator that changes every run reaches the deterministic cap", async () => {
  const state = await fixture(
    "import { readFileSync, writeFileSync } from 'node:fs'; let n = 0; try { n = Number(readFileSync('generated.txt', 'utf8')); } catch {} writeFileSync('generated.txt', String(n + 1));\n",
  );
  try {
    const result = await runStableTreeChecks({
      worktree: state.repo,
      checks: plan,
      maxPasses: 2,
    });
    assert.equal(result.status, "non-converging");
    assert.equal(result.passes, 2);
  } finally {
    await state.cleanup();
  }
});

test("failed checks halt without manufacturing a manifest", async () => {
  const state = await fixture("console.error('failed'); process.exit(7);\n");
  try {
    const result = await runStableTreeChecks({ worktree: state.repo, checks: plan });
    assert.equal(result.status, "failed");
    assert.equal(result.check?.id, "unit");
    assert.equal(result.check?.result.code, 7);
    assert.equal("manifest" in result, false);
  } finally {
    await state.cleanup();
  }
});

test("check plans reject duplicate ids and unsafe cwd", async () => {
  const state = await fixture("\n");
  try {
    await assert.rejects(
      () =>
        runStableTreeChecks({
          worktree: state.repo,
          checks: [
            ...plan,
            { id: "unit", purpose: "oracle", command: "true", cwd: "../outside" },
          ],
        }),
      /malformed or contains duplicates/,
    );
  } finally {
    await state.cleanup();
  }
});

async function commitEnv(repo: string, setup: string): Promise<void> {
  await writeFile(join(repo, ".gitignore"), "dep/\n");
  await mkdir(join(repo, ".karta"), { recursive: true });
  await writeFile(join(repo, ".karta", "environment.json"), `{ "setup": ${JSON.stringify(setup)} }`);
  await git(repo, ["add", "-A"]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "env"]);
}

test("a declared environment setup runs in the worktree before checks", async () => {
  const state = await fixture(
    "import { existsSync } from 'node:fs'; process.exit(existsSync('dep/marker') ? 0 : 5);\n",
  );
  try {
    await commitEnv(state.repo, "mkdir -p dep && echo ok > dep/marker");
    const result = await runStableTreeChecks({
      worktree: state.repo,
      checks: plan,
      environmentSetupRef: "HEAD",
    });
    assert.equal(result.status, "stable");
  } finally {
    await state.cleanup();
  }
});

test("a failing environment setup halts before checks with a named result", async () => {
  const state = await fixture("process.exit(0);\n");
  try {
    await commitEnv(state.repo, "exit 3");
    const result = await runStableTreeChecks({
      worktree: state.repo,
      checks: plan,
      environmentSetupRef: "HEAD",
    });
    assert.equal(result.status, "failed");
    assert.equal(result.check?.id, "environment-setup");
    assert.equal(result.check?.result.code, 3);
  } finally {
    await state.cleanup();
  }
});

test("an environment setup that mutates a tracked file is refused", async () => {
  const state = await fixture("process.exit(0);\n");
  try {
    await commitEnv(state.repo, "echo drift >> subject.txt");
    const result = await runStableTreeChecks({
      worktree: state.repo,
      checks: plan,
      environmentSetupRef: "HEAD",
    });
    assert.equal(result.status, "failed");
    assert.equal(result.check?.id, "environment-setup");
    assert.match(result.check?.result.stderr ?? "", /mutated tracked files/);
  } finally {
    await state.cleanup();
  }
});
