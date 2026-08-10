import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { loadWorkerProjectInstructions } from "../../extensions/pi/worker-instructions.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<void> {
  await exec("git", args, { cwd });
}

test("worker instructions are complete, ordered, and bound to committed blobs", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-worker-instructions-"));
  try {
    await mkdir(join(root, "src", "nested"), { recursive: true });
    await writeFile(join(root, "AGENTS.md"), "root rule\n");
    await writeFile(join(root, "CLAUDE.md"), "root claude rule\n");
    await writeFile(join(root, "src", "nested", "AGENTS.md"), "nested rule\n");
    await writeFile(join(root, "src", "code.ts"), "export {};\n");
    await git(root, ["init", "--initial-branch=main"]);
    await git(root, ["config", "user.name", "Karta Instructions"]);
    await git(root, ["config", "user.email", "instructions@invalid.example"]);
    await git(root, ["config", "commit.gpgSign", "false"]);
    await git(root, ["add", "."]);
    await git(root, ["commit", "--no-gpg-sign", "-m", "base"]);
    await writeFile(join(root, "AGENTS.md"), "dirty replacement\n");

    const instructions = await loadWorkerProjectInstructions(root);
    assert.deepEqual(
      instructions.map((instruction) => instruction.path),
      ["AGENTS.md", "CLAUDE.md", "src/nested/AGENTS.md"],
    );
    assert.equal(instructions[0].content, "root rule\n");
    assert.match(instructions[0].blob, /^[a-f0-9]{40,64}$/);
    assert.match(instructions[0].sha256, /^[a-f0-9]{64}$/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
