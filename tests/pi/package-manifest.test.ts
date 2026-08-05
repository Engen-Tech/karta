import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));

test("npm package inventory contains runtime assets and excludes development projections", async () => {
  const manifest = JSON.parse(await readFile(`${ROOT}/package.json`, "utf8"));
  assert.deepEqual(manifest.files, [
    "extensions/pi/",
    "skills/",
    "agents/",
    "hooks/scripts/",
    "!**/__pycache__/",
    "!**/*.pyc",
  ]);
  const { stdout } = await exec("npm", ["pack", "--dry-run", "--json", "--ignore-scripts"], {
    cwd: ROOT,
    maxBuffer: 20 * 1024 * 1024,
  });
  const report = JSON.parse(stdout)[0];
  const files: string[] = report.files.map((entry: { path: string }) => entry.path);
  for (const required of [
    "extensions/pi/index.ts",
    "extensions/pi/guard-adapter.ts",
    "extensions/pi/guard-runner.ts",
    "extensions/pi/lifecycle-registry.ts",
    "skills/karta-plan/SKILL.md",
    "agents/karta-acceptance-reviewer.md",
    "hooks/scripts/guard_binder_immutability.py",
    "hooks/scripts/guard_delivery_stop.py",
    "hooks/scripts/guard_subagent_whiff.py",
  ]) {
    assert.ok(files.includes(required), required);
  }
  for (const forbidden of ["benchmarks/", "plugins/", ".agents/", ".codex/", "tests/", "docs/"]) {
    assert.equal(files.some((path) => path.startsWith(forbidden)), false, forbidden);
  }
  assert.equal(files.some((path) => path.includes("__pycache__") || path.endsWith(".pyc")), false);
  assert.ok(report.unpackedSize < 5 * 1024 * 1024, `package is ${report.unpackedSize} bytes`);
});
