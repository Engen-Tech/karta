import assert from "node:assert/strict";
import { readFile, readdir, realpath } from "node:fs/promises";
import { relative, sep } from "node:path";
import test from "node:test";
import { PACKAGE_ROOT, requirePackagePath, resolvePackagePath } from "../../extensions/pi/package-paths.ts";
import { KARTA_SCRIPT_PATHS, resolveKartaScript } from "../../extensions/pi/script-catalog.ts";

test("package paths reject absolute paths and traversal", () => {
  assert.throws(() => resolvePackagePath(""), /non-empty and relative/);
  assert.throws(() => resolvePackagePath("/tmp/karta"), /non-empty and relative/);
  assert.throws(() => resolvePackagePath("../outside"), /escapes its package root/);
});

test("every catalogued script exists inside the package root", async () => {
  const root = await realpath(PACKAGE_ROOT);
  const resolved = Object.keys(KARTA_SCRIPT_PATHS).map((action) =>
    resolveKartaScript(action as keyof typeof KARTA_SCRIPT_PATHS),
  );
  assert.equal(new Set(resolved).size, resolved.length);
  for (const path of resolved) {
    const canonical = await realpath(path);
    assert.equal(relative(root, canonical).startsWith(".."), false);
  }
});

test("script catalog covers every bundled Python script", async () => {
  const skillsRoot = requirePackagePath("skills");
  const entries = await readdir(skillsRoot, { recursive: true });
  const scripts = entries
    .map((entry) => entry.split(sep).join("/"))
    .filter((entry) => /^karta-[^/]+\/scripts\/[^/]+\.py$/.test(entry))
    .map((entry) => `skills/${entry}`)
    .sort();
  assert.deepEqual(scripts, Object.values(KARTA_SCRIPT_PATHS).sort());
});

test("canonical skills contain no consumer-relative bundled script commands", async () => {
  const skillsRoot = requirePackagePath("skills");
  const entries = await readdir(skillsRoot, { withFileTypes: true });
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.startsWith("karta-")) continue;
    const text = await readFile(`${skillsRoot}/${entry.name}/SKILL.md`, "utf8");
    assert.equal(
      /(?:python3|uv run(?: --script)?)\s+skills\/karta-[a-z-]+\/scripts\//.test(text),
      false,
      entry.name,
    );
  }
});
