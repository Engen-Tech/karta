import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";
import { readEnvironmentSetup } from "../../extensions/pi/environment.ts";

const exec = promisify(execFile);

async function repo(): Promise<{ dir: string; write(json: string): Promise<void>; cleanup(): Promise<void> }> {
  const dir = await mkdtemp(join(tmpdir(), "karta-environment-"));
  await exec("git", ["-C", dir, "init", "--initial-branch=main"]);
  return {
    dir,
    async write(json: string) {
      await mkdir(join(dir, ".karta"), { recursive: true });
      await writeFile(join(dir, ".karta", "environment.json"), json);
    },
    cleanup: () => rm(dir, { recursive: true, force: true }),
  };
}

test("readEnvironmentSetup returns the declared setup command", async () => {
  const state = await repo();
  try {
    await state.write(`{ "setup": "npm ci" }`);
    assert.equal(await readEnvironmentSetup(state.dir), "npm ci");
  } finally {
    await state.cleanup();
  }
});

test("an absent config or an absent setup key means no setup", async () => {
  const state = await repo();
  try {
    assert.equal(await readEnvironmentSetup(state.dir), undefined);
    await state.write(`{}`);
    assert.equal(await readEnvironmentSetup(state.dir), undefined);
  } finally {
    await state.cleanup();
  }
});

test("a malformed environment config fails closed rather than silently skipping", async () => {
  const state = await repo();
  try {
    await state.write(`{ "setup": "" }`);
    await assert.rejects(() => readEnvironmentSetup(state.dir), /non-empty string/);
    await state.write(`{ "setup": "npm ci", "extra": true }`);
    await assert.rejects(() => readEnvironmentSetup(state.dir), /unknown keys: extra/);
    await state.write(`not json`);
    await assert.rejects(() => readEnvironmentSetup(state.dir), /not valid JSON/);
    await state.write(`[]`);
    await assert.rejects(() => readEnvironmentSetup(state.dir), /must be a JSON object/);
  } finally {
    await state.cleanup();
  }
});
