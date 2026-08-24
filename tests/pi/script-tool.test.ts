import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
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

const VISUAL_CAPTURES = fileURLToPath(new URL("fixtures/visual-captures/", import.meta.url));
const RENDER_HEALTH_KEYS = [
  "consoleErrorCount",
  "consoleErrors",
  "failedRequestCount",
  "failedRequests",
  "readySelector",
  "result",
  "schema",
  "styledElementCount",
  "visibleLeafElements",
  "visibleTextChars",
];

async function loadCapture(name: string): Promise<Record<string, any>> {
  return JSON.parse(await readFile(join(VISUAL_CAPTURES, name), "utf8"));
}

test("render health conforms to karta-render-health-v1 and locks the shell boundary", async () => {
  const cases: Array<[string, number, string]> = [
    ["shell-threshold-minus-one.json", 19, "blocked"],
    ["shell-threshold.json", 20, "healthy"],
    ["shell-threshold-plus-one.json", 21, "healthy"],
  ];
  for (const [file, chars, expected] of cases) {
    const artifact = await loadCapture(file);
    const rh = artifact.app.render_health;
    // Closed evidence-key set and the literal schema.
    assert.deepEqual(Object.keys(rh).sort(), RENDER_HEALTH_KEYS);
    assert.equal(rh.schema, "karta-render-health-v1");
    assert.ok(["healthy", "degraded", "blocked"].includes(rh.result));
    // The exact shell boundary at 20 visible-text characters.
    assert.equal(rh.visibleTextChars, chars);
    assert.equal(rh.visibleLeafElements, 0);
    assert.equal(rh.styledElementCount, 0);
    assert.equal(rh.result, expected);
    // Raw CLI details preserved alongside the structured verdict.
    assert.equal(typeof artifact.app.console_errors, "string");
    assert.equal(typeof artifact.app.requests, "string");
    assert.ok(artifact.app.screenshot);
    // Both targets carry an independent render_health record.
    assert.equal(artifact.design.render_health.schema, "karta-render-health-v1");
  }
});

test("named-session evidence never cross-contaminates between design and app", async () => {
  const designFails = await loadCapture("design-fails-app-clean.json");
  const dfDesign = designFails.design.render_health;
  const dfApp = designFails.app.render_health;
  assert.equal(dfDesign.result, "degraded");
  assert.ok(dfDesign.consoleErrorCount + dfDesign.failedRequestCount > 0);
  // The clean app target carries none of the design session's evidence.
  assert.equal(dfApp.result, "healthy");
  assert.equal(dfApp.consoleErrorCount, 0);
  assert.equal(dfApp.failedRequestCount, 0);
  assert.deepEqual(dfApp.consoleErrors, []);
  assert.deepEqual(dfApp.failedRequests, []);

  const appFails = await loadCapture("design-clean-app-fails.json");
  const afApp = appFails.app.render_health;
  const afDesign = appFails.design.render_health;
  assert.equal(afApp.result, "degraded");
  assert.ok(afApp.consoleErrorCount + afApp.failedRequestCount > 0);
  // The clean design target carries none of the app session's evidence.
  assert.equal(afDesign.result, "healthy");
  assert.equal(afDesign.consoleErrorCount, 0);
  assert.equal(afDesign.failedRequestCount, 0);
  assert.deepEqual(afDesign.consoleErrors, []);
  assert.deepEqual(afDesign.failedRequests, []);
});

test("comparable extracted elements carry identity, context, styles, and a bounding box", async () => {
  const artifact = await loadCapture("design-fails-app-clean.json");
  const data = artifact.design.extracted_data;
  const groups = [data.headings, data.buttons, data.landmarks];
  let total = 0;
  for (const group of groups) {
    assert.ok(Array.isArray(group));
    for (const el of group) {
      assert.equal(typeof el.identity, "string");
      assert.ok(el.identity.length > 0);
      assert.ok(["heading", "button", "landmark"].includes(el.category));
      assert.equal(typeof el.role, "string");
      assert.equal(typeof el.text, "string");
      assert.ok("parentIdentity" in el);
      assert.equal(typeof el.siblingOrder, "number");
      assert.equal(typeof el.styles.fontSize, "string");
      assert.equal(typeof el.styles.color, "string");
      for (const k of ["x", "y", "width", "height"]) {
        assert.equal(typeof el.box[k], "number");
      }
      total++;
    }
  }
  assert.ok(total >= 3);
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
