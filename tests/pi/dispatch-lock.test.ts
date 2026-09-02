import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { hostname, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import {
  DispatchLockError,
  DispatchLockManager,
  acquireDispatchLock,
  inspectDispatchLock,
  resolveDispatchLockPath,
} from "../../extensions/pi/dispatch-lock.ts";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const CONTENDER = join(ROOT, "tests", "pi", "fixtures", "lock-contender.ts");

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

async function repository(root: string): Promise<string> {
  const repo = join(root, "repo π");
  await mkdir(repo, { recursive: true });
  await git(repo, ["init", "--initial-branch=main"]);
  return repo;
}

async function contender(repo: string, binder: string): Promise<{ code: number | null; output: string }> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(
      process.execPath,
      ["--experimental-strip-types", CONTENDER, repo, binder, "500"],
      { cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] },
    );
    let output = "";
    let error = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      output += chunk;
    });
    child.stderr.on("data", (chunk) => {
      error += chunk;
    });
    child.once("error", rejectRun);
    child.once("exit", (code) => resolveRun({ code, output: output || error }));
  });
}

test("dispatch lock is exclusive, nonce-owned, and reusable after release", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-"));
  const repo = await repository(root);
  try {
    const lease = await acquireDispatchLock(repo, "binder-one");
    const inspection = await inspectDispatchLock(lease.lockPath);
    assert.equal(inspection.readable, true);
    assert.equal(inspection.owner?.binder, "binder-one");
    assert.equal(inspection.owner?.pid, process.pid);
    assert.equal(inspection.ownerAppearsActive, true);
    await assert.rejects(
      () => acquireDispatchLock(repo, "binder-one"),
      (error: unknown) =>
        error instanceof DispatchLockError && /already locked/.test(error.message),
    );
    const ownerPath = join(lease.lockPath, "owner.json");
    const owner = JSON.parse(await readFile(ownerPath, "utf8"));
    await writeFile(ownerPath, JSON.stringify({ ...owner, nonce: "replacement-owner" }));
    await assert.rejects(() => lease.release(), /ownership changed/);
    await writeFile(ownerPath, JSON.stringify(owner));
    await lease.release();
    await lease.release();
    const replacement = await acquireDispatchLock(repo, "binder-one");
    assert.notEqual(replacement.owner.nonce, lease.owner.nonce);
    await Promise.all([replacement.release(), replacement.release()]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("different binders may hold independent locks in one repository", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-binders-"));
  const repo = await repository(root);
  const manager = new DispatchLockManager();
  try {
    await manager.acquire(repo, "binder-a");
    await manager.acquire(repo, "binder-b");
    assert.equal(manager.size, 2);
    await manager.releaseAll();
    assert.equal(manager.size, 0);
  } finally {
    await manager.releaseAll();
    await rm(root, { recursive: true, force: true });
  }
});

test("linked worktrees resolve the same per-binder lock", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-worktree-"));
  const repo = await repository(root);
  const linked = join(root, "linked worktree");
  await writeFile(join(repo, "README.md"), "fixture\n");
  await git(repo, ["config", "user.name", "Karta Phase 3"]);
  await git(repo, ["config", "user.email", "phase3@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "fixture"]);
  await git(repo, ["worktree", "add", "-b", "linked", linked]);
  try {
    assert.equal(
      await resolveDispatchLockPath(repo, "shared-binder"),
      await resolveDispatchLockPath(linked, "shared-binder"),
    );
    const lease = await acquireDispatchLock(repo, "shared-binder");
    await assert.rejects(() => acquireDispatchLock(linked, "shared-binder"), DispatchLockError);
    await lease.release();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("an absent owner is reported as stale but never stolen", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-stale-"));
  const repo = await repository(root);
  try {
    const lockPath = await resolveDispatchLockPath(repo, "stale-binder");
    await mkdir(lockPath, { recursive: true });
    await writeFile(
      join(lockPath, "owner.json"),
      JSON.stringify({
        schema: 1,
        binder: "stale-binder",
        pid: 2_147_483_647,
        hostname: hostname(),
        processStartedAt: "2000-01-01T00:00:00.000Z",
        nonce: "stale-fixture",
        packageVersion: "0.0.0",
        acquiredAt: "2000-01-01T00:00:00.000Z",
      }),
    );
    await assert.rejects(
      () => acquireDispatchLock(repo, "stale-binder"),
      (error: unknown) =>
        error instanceof DispatchLockError &&
        error.inspection.ownerAppearsActive === false &&
        /remove the stale lock explicitly/.test(error.message),
    );
    assert.equal(JSON.parse(await readFile(join(lockPath, "owner.json"), "utf8")).nonce, "stale-fixture");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("PID-reuse-shaped ownership remains locked instead of being guessed stale", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-pid-reuse-"));
  const repo = await repository(root);
  try {
    const lockPath = await resolveDispatchLockPath(repo, "pid-reuse-binder");
    await mkdir(lockPath, { recursive: true });
    await writeFile(
      join(lockPath, "owner.json"),
      JSON.stringify({
        schema: 1,
        binder: "pid-reuse-binder",
        pid: process.pid,
        hostname: hostname(),
        processStartedAt: "2000-01-01T00:00:00.000Z",
        nonce: "pid-reuse-fixture",
        packageVersion: "0.0.0",
        acquiredAt: "2000-01-01T00:00:00.000Z",
      }),
    );
    await assert.rejects(
      () => acquireDispatchLock(repo, "pid-reuse-binder"),
      (error: unknown) =>
        error instanceof DispatchLockError && error.inspection.ownerAppearsActive === true,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("two processes racing for one binder produce exactly one owner", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-race-"));
  const repo = await repository(root);
  try {
    const results = await Promise.all([
      contender(repo, "race-binder"),
      contender(repo, "race-binder"),
    ]);
    assert.deepEqual(
      results.map((result) => result.code).sort(),
      [0, 3],
      JSON.stringify(results),
    );
    assert.equal(results.filter((result) => result.output.startsWith("ACQUIRED")).length, 1);
    assert.equal(results.filter((result) => result.output.startsWith("LOCKED:")).length, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("binder slugs cannot escape the Git-common-dir lock namespace", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-lock-slug-"));
  const repo = await repository(root);
  try {
    await assert.rejects(() => resolveDispatchLockPath(repo, "../escape"), /Invalid Karta binder/);
    await assert.rejects(() => resolveDispatchLockPath(repo, "Bad_Slug"), /Invalid Karta binder/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
