import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";
import {
  AcceptanceOracleRunner,
  createAcceptanceOracleTool,
} from "../../extensions/pi/oracle-runner.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function fixture(
  script: string,
  oracle: Record<string, unknown> = { type: "unit", command: "node check.mjs" },
): Promise<{ repo: string; manifest: KartaEvidenceManifest; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-oracle-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "check.mjs"), script);
  await writeFile(join(repo, "subject.txt"), "exact item content\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Oracle"]);
  await git(repo, ["config", "user.email", "oracle@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "item"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const workItem = {
    id: "item-a",
    title: "Oracle fixture",
    summary: "Run a check",
    oracle,
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: tip,
      sha256: "binder-hash",
      document: { slug: "demo", work_items: [workItem] },
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: tip,
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: tip,
      mergeBase: tip,
    },
    diff: {
      format: "git-binary-patch",
      sha256: "diff-hash",
      bytes: 0,
      touchedPaths: ["check.mjs", "subject.txt"],
      content: "",
    },
    packs: [],
  };
  return {
    repo,
    manifest: {
      schema: "karta-evidence-v1",
      generatedAt: new Date().toISOString(),
      repositoryRoot: repo,
      evidenceHash: hashEvidencePayload(payload),
      payload,
    },
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

test("oracle command runs once in a disposable exact-tip snapshot", async () => {
  const { repo, manifest, cleanup } = await fixture(`
    import { existsSync, readFileSync, writeFileSync } from "node:fs";
    if (existsSync(".git")) process.exit(9);
    if (readFileSync("subject.txt", "utf8") !== "exact item content\\n") process.exit(8);
    writeFileSync("generated.txt", "temporary");
    console.log("ORACLE_OK");
    console.log("x".repeat(70_000));
  `);
  try {
    const runner = new AcceptanceOracleRunner(manifest);
    const first = await runner.run();
    const second = await runner.run();
    assert.equal(first.status, "passed");
    assert.equal(first.code, 0);
    assert.match(first.stdout, /ORACLE_OK/);
    assert.equal(first.stdoutTruncated, true);
    assert.ok(Buffer.byteLength(first.stdout) <= 64 * 1024);
    assert.match(first.commandHash ?? "", /^[a-f0-9]{64}$/);
    assert.equal(first.cached, false);
    assert.equal(second.cached, true);
    await assert.rejects(() => readFile(join(repo, "generated.txt")), /ENOENT/);
  } finally {
    await cleanup();
  }
});

test("oracle failures are evidence, while runner setup failures fail closed", async () => {
  const failedFixture = await fixture(`
    console.error("ASSERTION_FAILED");
    process.exit(7);
  `);
  try {
    const result = await new AcceptanceOracleRunner(failedFixture.manifest).run();
    assert.equal(result.status, "failed");
    assert.equal(result.code, 7);
    assert.match(result.stderr, /ASSERTION_FAILED/);
  } finally {
    await failedFixture.cleanup();
  }

  const escapedFixture = await fixture("", {
    type: "unit",
    command: "node check.mjs",
    cwd: "../outside",
  });
  try {
    const tool = createAcceptanceOracleTool(escapedFixture.manifest);
    const result = await tool.execute(
      "oracle",
      { action: "run" },
      undefined,
      undefined,
      {} as never,
    );
    assert.equal((result as { isError?: boolean }).isError, true);
    assert.match(result.content[0].type === "text" ? result.content[0].text : "", /must stay inside/);
  } finally {
    await escapedFixture.cleanup();
  }
});

test("oracle timeout kills the spawned process group and is not cached", async () => {
  const { manifest, cleanup } = await fixture(`
    import { spawn } from "node:child_process";
    const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 60000)"], { stdio: "ignore" });
    console.log("CHILD_PID=" + child.pid);
    setInterval(() => {}, 60000);
  `);
  try {
    let shellPid = 0;
    const runner = new AcceptanceOracleRunner(manifest, {
      timeout: 1_000,
      onProcessStart(pid) {
        shellPid = pid;
      },
    });
    const result = await runner.run();
    assert.equal(result.status, "timed-out");
    assert.ok(shellPid > 0);
    assert.throws(() => process.kill(shellPid, 0), /ESRCH/);
    const childPid = Number(result.stdout.match(/CHILD_PID=(\d+)/)?.[1]);
    if (Number.isInteger(childPid)) assert.throws(() => process.kill(childPid, 0), /ESRCH/);
    const second = await runner.run();
    assert.equal(second.cached, false);
    assert.equal(second.status, "timed-out");
  } finally {
    await cleanup();
  }
});

test("oracle abort kills the spawned process group", async () => {
  const { manifest, cleanup } = await fixture(`
    import { spawn } from "node:child_process";
    const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 60000)"], { stdio: "ignore" });
    console.log("CHILD_PID=" + child.pid);
    setInterval(() => {}, 60000);
  `);
  try {
    const controller = new AbortController();
    let shellPid = 0;
    const runner = new AcceptanceOracleRunner(manifest, {
      onProcessStart(pid) {
        shellPid = pid;
        setTimeout(() => controller.abort(), 500);
      },
    });
    const result = await runner.run(controller.signal);
    assert.equal(result.status, "aborted");
    assert.ok(shellPid > 0);
    assert.throws(() => process.kill(shellPid, 0), /ESRCH/);
    const childPid = Number(result.stdout.match(/CHILD_PID=(\d+)/)?.[1]);
    if (Number.isInteger(childPid)) assert.throws(() => process.kill(childPid, 0), /ESRCH/);
  } finally {
    await cleanup();
  }
});

test("oracle tool schema accepts no command, cwd, path, environment, ref, or timeout", async () => {
  const { manifest, cleanup } = await fixture("console.log('ok')");
  try {
    const tool = createAcceptanceOracleTool(manifest);
    const schema = JSON.stringify(tool.parameters);
    for (const forbidden of ["command", "cwd", "path", "environment", "ref", "timeout"] as const) {
      assert.equal(schema.includes(`\"${forbidden}\"`), false, forbidden);
    }
  } finally {
    await cleanup();
  }
});
