import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { guardInvocation, runKartaGuard } from "../../extensions/pi/guard-runner.ts";
import { resolvePackagePath } from "../../extensions/pi/package-paths.ts";

const exec = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<void> {
  await exec("git", args, { cwd });
}

test("guard invocation uses a package-owned script without a shell", () => {
  const invocation = guardInvocation("binderImmutability");
  assert.equal(invocation.command, "uv");
  assert.deepEqual(invocation.args.slice(0, 2), ["run", "--script"]);
  assert.equal(
    invocation.args[2],
    resolvePackagePath("hooks/scripts/guard_binder_immutability.py"),
  );
});

test("guard runner fails open when its working directory is unavailable", async () => {
  const missing = join(tmpdir(), `karta-pi-missing-${process.pid}-${Date.now()}`);
  const guard = await runKartaGuard("binderImmutability", {}, { cwd: missing });
  assert.equal(guard.code, 0);
  assert.equal(guard.failedOpen, true);
});

test("binder guard blocks committed binders and allows new drafts", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-binder-guard-"));
  await mkdir(join(root, ".karta", "binders"), { recursive: true });
  await writeFile(join(root, ".karta", "binders", "committed.json"), "{}\n");
  try {
    await git(root, ["init", "--initial-branch=main"]);
    await git(root, ["config", "user.name", "Karta Phase 2"]);
    await git(root, ["config", "user.email", "phase2@invalid.example"]);
    await git(root, ["config", "commit.gpgSign", "false"]);
    await git(root, ["add", "."]);
    await git(root, ["commit", "--no-gpg-sign", "-m", "fixture"]);
    const committed = await runKartaGuard(
      "binderImmutability",
      {
        hook_event_name: "PreToolUse",
        tool_name: "Write",
        tool_input: { file_path: ".karta/binders/committed.json", content: "{}" },
        cwd: root,
      },
      { cwd: root },
    );
    assert.equal(committed.code, 2);
    assert.match(committed.stderr, /committed binders are read-only/);

    const draft = await runKartaGuard(
      "binderImmutability",
      {
        hook_event_name: "PreToolUse",
        tool_name: "Write",
        tool_input: { file_path: ".karta/binders/draft.json", content: "{}" },
        cwd: root,
      },
      { cwd: root },
    );
    assert.equal(draft.code, 0);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("pack guard blocks invalid writes and reports invalid edits", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-pack-guard-"));
  const target = join(root, ".karta", "sme", "terraform.md");
  await mkdir(join(root, ".karta", "sme"), { recursive: true });
  try {
    const invalid = await runKartaGuard(
      "packWrite",
      {
        hook_event_name: "PreToolUse",
        tool_name: "Write",
        tool_input: { file_path: target, content: "not a pack\n" },
        cwd: root,
      },
      { cwd: root },
    );
    assert.equal(invalid.code, 2);
    assert.match(invalid.stderr, /proposed content fails/);

    const validContent =
      "---\nname: terraform\ndescription: Terraform fixture\nmatch: [\"terraform\"]\n---\n" +
      "## Review checklist\n- [ ] tf.1 — Pin provider versions.\n";
    const valid = await runKartaGuard(
      "packWrite",
      {
        hook_event_name: "PreToolUse",
        tool_name: "Write",
        tool_input: { file_path: target, content: validContent },
        cwd: root,
      },
      { cwd: root },
    );
    assert.equal(valid.code, 0, valid.stderr);

    await writeFile(target, "not a pack\n");
    const postEdit = await runKartaGuard(
      "packWrite",
      {
        hook_event_name: "PostToolUse",
        tool_name: "Edit",
        tool_input: { file_path: target },
        cwd: root,
      },
      { cwd: root },
    );
    assert.equal(postEdit.code, 2);
    assert.match(postEdit.stderr, /content now on disk fails/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
