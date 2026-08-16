import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  createWriterCapabilityProfile,
  isWriterWritablePath,
} from "../../extensions/pi/writer-profile.ts";

test("writer profiles expose no Bash and enforce distinct host surfaces", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-writer-profile-"));
  await mkdir(join(root, "docs"));
  await mkdir(join(root, ".karta", "sme"), { recursive: true });
  try {
    const docs = createWriterCapabilityProfile(root, "doc-gardner");
    const kaizen = createWriterCapabilityProfile(root, "kaizen");
    assert.deepEqual(docs.toolNames, ["read", "karta_writer_inventory", "karta_writer_search", "write", "edit"]);
    assert.deepEqual(kaizen.toolNames, ["read", "karta_writer_inventory", "karta_writer_search", "write", "edit"]);
    assert.equal(isWriterWritablePath("doc-gardner", "README.md"), true);
    assert.equal(isWriterWritablePath("doc-gardner", "src/code.ts"), false);
    assert.equal(isWriterWritablePath("kaizen", ".karta/sme/python.md"), true);
    assert.equal(isWriterWritablePath("kaizen", ".karta/binders/demo.json"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("writer aliases and in-repo symlinks cannot escape a declared surface", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-writer-alias-"));
  await mkdir(join(root, "docs"));
  await mkdir(join(root, "src"));
  await writeFile(join(root, "src", "code.ts"), "export {};\n");
  await symlink(join(root, "src"), join(root, "docs", "source"));
  try {
    const profile = createWriterCapabilityProfile(root, "doc-gardner");
    const write = profile.tools.find((tool) => tool.name === "write");
    assert.ok(write);
    await assert.rejects(
      () => write.execute(
        "alias",
        { path: "docs/source/code.ts", content: "changed\n" },
        undefined,
        undefined,
        { cwd: root } as ExtensionContext,
      ),
      /outside its declared surface/,
    );
    await assert.rejects(
      () => write.execute(
        "traversal",
        { path: "../README.md", content: "changed\n" },
        undefined,
        undefined,
        { cwd: root } as ExtensionContext,
      ),
      /escapes its worktree/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
