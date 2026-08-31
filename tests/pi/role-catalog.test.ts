import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { PACKAGE_ROOT } from "../../extensions/pi/package-paths.ts";
import { listKartaRoles, loadKartaRole } from "../../extensions/pi/role-catalog.ts";

function hash(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

test("role catalog binds every authority profile to a package-owned prompt", () => {
  const roles = listKartaRoles();
  assert.deepEqual(
    roles.map((role) => role.id),
    ["acceptance-gate", "safety-gate", "visual-gate", "build-worker", "doc-gardner", "kaizen"],
  );
  assert.equal(new Set(roles.map((role) => role.definitionHash)).size, roles.length);
  for (const role of roles) {
    assert.ok(role.sourcePath.startsWith(`${PACKAGE_ROOT}/`), role.sourcePath);
    assert.equal(role.prompt.startsWith("---"), false);
    assert.equal(role.promptHash, hash(role.prompt));
    assert.match(role.sourceHash, /^[a-f0-9]{64}$/);
    assert.match(role.definitionHash, /^[a-f0-9]{64}$/);
  }
});

test("read-only gate roles have no mutation or command capability", () => {
  for (const id of ["acceptance-gate", "safety-gate", "visual-gate"] as const) {
    const role = loadKartaRole(id);
    assert.equal(role.authority, "read-only");
    assert.equal(
      role.capabilities.some((capability) =>
        ["worktree.write", "command.run", "docs.write", "packs.write"].includes(capability),
      ),
      false,
    );
    assert.equal(role.outputSchema, "gate-verdict-v1");
  }
});

test("project-local role-shaped files cannot replace package role sources", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-role-collision-"));
  const collision = join(root, "agents", "karta-acceptance-reviewer.md");
  await mkdir(join(root, "agents"), { recursive: true });
  await writeFile(
    collision,
    "---\nname: karta-acceptance-reviewer\n---\nPROJECT COLLISION\n",
  );
  try {
    const role = loadKartaRole("acceptance-gate");
    assert.notEqual(role.sourcePath, collision);
    assert.equal(role.prompt.includes("PROJECT COLLISION"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("unknown role ids fail before reading any caller-selected path", () => {
  assert.throws(() => loadKartaRole("../../project/prompt"), /Unknown Karta role/);
});
