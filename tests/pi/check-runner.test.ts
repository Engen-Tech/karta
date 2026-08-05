import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { bindCheckReceipt, runBoundCheck } from "../../extensions/pi/check-runner.ts";

test("host check runner executes in the assigned worktree and binds a later tree", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-check-runner-"));
  try {
    await writeFile(
      join(worktree, "check.mjs"),
      "import { writeFileSync } from 'node:fs'; writeFileSync('generated.txt', 'owned'); console.log('PASS');\n",
    );
    const result = await runBoundCheck({ worktree, command: "node check.mjs" });
    assert.equal(result.status, "passed");
    assert.equal(result.code, 0);
    assert.match(result.stdout, /PASS/);
    assert.equal(await readFile(join(worktree, "generated.txt"), "utf8"), "owned");
    const receipt = bindCheckReceipt(result, "a".repeat(40));
    assert.equal(receipt.targetTree, "a".repeat(40));
    assert.equal(receipt.status, "passed");
    assert.match(receipt.commandHash, /^[a-f0-9]{64}$/);
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

test("failed checks produce bindable failed receipts with bounded output", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-check-failure-"));
  try {
    await writeFile(
      join(worktree, "check.mjs"),
      "process.stderr.write('x'.repeat(70000), () => process.exit(7));\n",
    );
    const result = await runBoundCheck({ worktree, command: "node check.mjs" });
    assert.equal(result.status, "failed");
    assert.equal(result.code, 7);
    assert.equal(result.stderrTruncated, true);
    assert.ok(Buffer.byteLength(result.stderr) <= 64 * 1024);
    assert.equal(bindCheckReceipt(result, "b".repeat(40)).status, "failed");
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

test("aborted checks kill their process group and cannot become receipts", async () => {
  const worktree = await mkdtemp(join(tmpdir(), "karta-check-abort-"));
  const controller = new AbortController();
  let shellPid = 0;
  try {
    await writeFile(join(worktree, "wait.mjs"), "setInterval(() => {}, 60000);\n");
    const result = await runBoundCheck({
      worktree,
      command: "node wait.mjs",
      signal: controller.signal,
      onProcessStart(pid) {
        shellPid = pid;
        setTimeout(() => controller.abort(), 100);
      },
    });
    assert.equal(result.status, "aborted");
    assert.ok(shellPid > 0);
    assert.throws(() => process.kill(shellPid, 0), /ESRCH/);
    assert.throws(() => bindCheckReceipt(result, "c".repeat(40)), /cannot bind/);
  } finally {
    await rm(worktree, { recursive: true, force: true });
  }
});

test("check cwd cannot traverse outside the worktree", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-check-cwd-"));
  const worktree = join(root, "worktree");
  await mkdir(worktree);
  try {
    await assert.rejects(
      () => runBoundCheck({ worktree, command: "echo no", cwd: "../outside" }),
      /must stay inside/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
