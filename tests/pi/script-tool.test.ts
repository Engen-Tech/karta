import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { resolvePackagePath } from "../../extensions/pi/package-paths.ts";
import {
  buildScriptInvocation,
  createKartaScriptTool,
  type KartaScriptParameters,
} from "../../extensions/pi/script-tool.ts";

async function fixture(): Promise<{ cwd: string; cleanup(): Promise<void> }> {
  const root = await mkdtemp(join(tmpdir(), "karta π path "));
  const cwd = join(root, "repo with spaces");
  await mkdir(join(cwd, ".karta", "binders"), { recursive: true });
  await mkdir(join(cwd, ".karta", "sme"), { recursive: true });
  await writeFile(join(cwd, ".karta", "binders", "work.json"), "{}");
  await writeFile(join(cwd, ".karta", "sme", "project.md"), "---\nid: project\n---\n");
  return { cwd, cleanup: () => rm(root, { recursive: true, force: true }) };
}

test("fixed actions build argv without a shell or consumer-relative script", async () => {
  const { cwd, cleanup } = await fixture();
  try {
    const invocation = buildScriptInvocation(
      { action: "validateBinder", binder: ".karta/binders/work.json" },
      cwd,
    );
    assert.equal(invocation.script, resolvePackagePath("skills/karta-plan/scripts/validate_binder.py"));
    assert.deepEqual(invocation.args, ["--binder", join(cwd, ".karta", "binders", "work.json")]);
    assert.equal(invocation.cwd, cwd);
  } finally {
    await cleanup();
  }
});

test("script arguments reject project traversal and symlink escape", async (context) => {
  const { cwd, cleanup } = await fixture();
  const outside = await mkdtemp(join(tmpdir(), "karta-outside-"));
  await writeFile(join(outside, "pack.md"), "outside");
  try {
    assert.throws(
      () => buildScriptInvocation({ action: "validateBinder", binder: "../../outside.json" }, cwd),
      /outside the project/,
    );
    try {
      await symlink(join(outside, "pack.md"), join(cwd, ".karta", "sme", "escape.md"));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EPERM") {
        context.skip("symlinks require elevated privileges on this Windows runner");
        return;
      }
      throw error;
    }
    assert.throws(
      () =>
        buildScriptInvocation(
          { action: "validatePacks", packs: [".karta/sme/escape.md"] },
          cwd,
        ),
      /resolves outside/,
    );
  } finally {
    await cleanup();
    await rm(outside, { recursive: true, force: true });
  }
});

test("pack actions may read package-owned packs", async () => {
  const { cwd, cleanup } = await fixture();
  try {
    const pack = resolvePackagePath("skills/karta-plan/references/sme/minimalism.md");
    const invocation = buildScriptInvocation({ action: "resolvePackChecklist", pack }, cwd);
    assert.deepEqual(invocation.args, [pack]);
  } finally {
    await cleanup();
  }
});

test("capture action accepts only HTTP URLs and project output paths", async () => {
  const { cwd, cleanup } = await fixture();
  try {
    assert.throws(
      () =>
        buildScriptInvocation(
          { action: "captureView", designUrl: "file:///tmp/design", appUrl: "https://app.test" },
          cwd,
        ),
      /HTTP or HTTPS/,
    );
    const invocation = buildScriptInvocation(
      {
        action: "captureView",
        designUrl: "http://127.0.0.1:8000",
        appUrl: "https://app.test/view",
        out: "artifacts/result.json",
      },
      cwd,
    );
    assert.equal(invocation.timeout, 300_000);
    assert.ok(invocation.args.includes(join(cwd, "artifacts", "result.json")));
  } finally {
    await cleanup();
  }
});

test("registered tool invokes uv directly and refuses untrusted projects", async () => {
  const { cwd, cleanup } = await fixture();
  const calls: Array<{ command: string; args: string[] }> = [];
  const extension = {
    async exec(command: string, args: string[]) {
      calls.push({ command, args });
      return { stdout: '{"dependencies":[],"languages":[]}', stderr: "", code: 0, killed: false };
    },
  } as unknown as ExtensionAPI;
  const tool = createKartaScriptTool(extension);
  const params: KartaScriptParameters = { action: "detectStack", root: "." };
  try {
    const trusted = await tool.execute(
      "probe",
      params,
      undefined,
      undefined,
      { cwd, isProjectTrusted: () => true } as ExtensionContext,
    );
    assert.equal((trusted as { isError?: boolean }).isError, false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, "uv");
    assert.deepEqual(calls[0].args.slice(0, 2), ["run", "--script"]);
    assert.equal(calls[0].args[2], resolvePackagePath("skills/karta-plan/scripts/detect_stack.py"));

    const denied = await tool.execute(
      "probe",
      params,
      undefined,
      undefined,
      { cwd, isProjectTrusted: () => false } as ExtensionContext,
    );
    assert.equal((denied as { isError?: boolean }).isError, true);
    assert.equal(calls.length, 1);
  } finally {
    await cleanup();
  }
});
