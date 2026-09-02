import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import type { ExtensionCommandContext } from "@earendil-works/pi-coding-agent";
import {
  ChildRegistry,
  GateProviderPreflight,
  createIsolatedResourceLoader,
  createMirroredModelRuntime,
  type ChildRuntimeReport,
} from "../../extensions/pi/child-runtime.ts";
import { claimExtensionInstance } from "../../extensions/pi/extension-instance.ts";

const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));

test("Pi manifest loads one explicit extension and no static skills", async () => {
  const manifest = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  assert.deepEqual(manifest.pi.extensions, ["./extensions/pi/index.ts"]);
  assert.equal(manifest.pi.skills, undefined);
  assert.equal(manifest.version, "2.35.0");
});

test("isolated child loader ignores ambient resources and context", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-loader-"));
  const agentDir = join(root, "agent");
  const cwd = join(root, "repo");
  await mkdir(join(cwd, ".agents", "skills", "ambient"), { recursive: true });
  await mkdir(agentDir, { recursive: true });
  await writeFile(join(cwd, "AGENTS.md"), "ambient context");
  await writeFile(
    join(cwd, ".agents", "skills", "ambient", "SKILL.md"),
    "---\nname: ambient\ndescription: must not load\n---\n",
  );
  try {
    const { loader } = await createIsolatedResourceLoader(cwd, "probe", agentDir);
    assert.deepEqual(loader.getExtensions().extensions, []);
    assert.deepEqual(loader.getSkills().skills, []);
    assert.deepEqual(loader.getPrompts().prompts, []);
    assert.deepEqual(loader.getAgentsFiles().agentsFiles, []);
    assert.equal(loader.getSystemPrompt(), "probe");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("isolated gate and worker runtimes reject ambient native providers", async () => {
  const ctx = {
    model: { provider: "native-fixture", id: "model" },
    modelRegistry: {
      getRegisteredNativeProvider: () => ({ id: "native-fixture" }),
      getRegisteredProviderConfig: () => undefined,
    },
  } as unknown as ExtensionCommandContext;
  for (const policy of ["gate", "worker"] as const) {
    await assert.rejects(
      () => createMirroredModelRuntime(ctx, policy),
      /do not inherit dynamic native provider/,
    );
  }
});

test("isolated gate and worker runtimes reject executable provider hooks", async () => {
  const ctx = {
    model: { provider: "oauth-fixture", id: "model" },
    modelRegistry: {
      getRegisteredNativeProvider: () => undefined,
      getRegisteredProviderConfig: () => ({ oauth: { name: "fixture" } }),
    },
  } as unknown as ExtensionCommandContext;
  for (const policy of ["gate", "worker"] as const) {
    await assert.rejects(
      () => createMirroredModelRuntime(ctx, policy),
      /do not inherit executable provider hooks/,
    );
  }
});

test("strict gate runtime rejects a model absent from its isolated runtime", async () => {
  const ctx = {
    model: { provider: "missing-fixture", id: "model" },
    modelRegistry: {
      getRegisteredNativeProvider: () => undefined,
      getRegisteredProviderConfig: () => undefined,
      getProviderAuthStatus: () => ({ configured: false }),
      getApiKeyAndHeaders: async () => ({ ok: false }),
    },
  } as unknown as ExtensionCommandContext;
  await assert.rejects(
    () => createMirroredModelRuntime(ctx, "gate"),
    /cannot resolve exact model/,
  );
});

test("gate provider preflight coalesces concurrent checks and caches only success", async () => {
  let calls = 0;
  let resolveProbe: ((report: ChildRuntimeReport) => void) | undefined;
  const report: ChildRuntimeReport = {
    provider: "fixture",
    model: "model",
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "builtin",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
  };
  const preflight = new GateProviderPreflight(
    () =>
      new Promise((resolve) => {
        calls += 1;
        resolveProbe = resolve;
      }),
  );
  const ctx = {
    model: { provider: "fixture", id: "model" },
    modelRegistry: { getProviderAuthStatus: () => ({ source: "stored" }) },
  } as unknown as ExtensionCommandContext;
  const registry = new ChildRegistry();
  const first = preflight.ensure(ctx, registry);
  const second = preflight.ensure(ctx, registry);
  assert.equal(calls, 1);
  resolveProbe?.(report);
  assert.equal((await first).cached, false);
  assert.equal((await second).cached, true);
  assert.equal((await preflight.ensure(ctx, registry)).cached, true);
  assert.equal(preflight.size, 1);
  preflight.clear();
  assert.equal(preflight.size, 0);
});

test("gate provider preflight cache is invalidated by declarative provider changes", async () => {
  let calls = 0;
  let baseUrl = "https://first.invalid/v1";
  const report: ChildRuntimeReport = {
    provider: "fixture",
    model: "model",
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "config",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
  };
  const preflight = new GateProviderPreflight(async () => {
    calls += 1;
    return report;
  });
  const ctx = {
    model: { provider: "fixture", id: "model" },
    modelRegistry: {
      getProviderAuthStatus: () => ({ source: "stored" }),
      getRegisteredProviderConfig: () => ({ baseUrl }),
    },
  } as unknown as ExtensionCommandContext;
  const registry = new ChildRegistry();
  await preflight.ensure(ctx, registry);
  assert.equal((await preflight.ensure(ctx, registry)).cached, true);
  baseUrl = "https://second.invalid/v1";
  assert.equal((await preflight.ensure(ctx, registry)).cached, false);
  assert.equal(calls, 2);
});

test("gate provider preflight does not cache a failed request", async () => {
  let calls = 0;
  const report: ChildRuntimeReport = {
    provider: "fixture",
    model: "model",
    policy: "gate",
    exactModelResolved: true,
    parentAuthConfigured: true,
    childAuthConfigured: true,
    copiedProvider: "builtin",
    copiedRuntimeCredential: false,
    unresolvedEnvironmentKeys: [],
  };
  const preflight = new GateProviderPreflight(async () => {
    calls += 1;
    if (calls === 1) throw new Error("preflight fixture");
    return report;
  });
  const ctx = {
    model: { provider: "fixture", id: "model" },
    modelRegistry: { getProviderAuthStatus: () => ({ source: "stored" }) },
  } as unknown as ExtensionCommandContext;
  const registry = new ChildRegistry();
  await assert.rejects(() => preflight.ensure(ctx, registry), /preflight fixture/);
  assert.equal((await preflight.ensure(ctx, registry)).cached, false);
  assert.equal(calls, 2);
});

test("extension instance fails closed on duplicate package roots", () => {
  const release = claimExtensionInstance("/package/a");
  assert.throws(() => claimExtensionInstance("/package/a"), /loaded twice/);
  assert.throws(() => claimExtensionInstance("/package/b"), /two package roots/);
  release();
  const releaseOther = claimExtensionInstance("/package/b");
  releaseOther();
});

test("child registry aborts and disposes every tracked child", async () => {
  const calls: string[] = [];
  const registry = new ChildRegistry();
  for (const name of ["a", "b"]) {
    registry.add({
      async abort() {
        calls.push(`abort:${name}`);
      },
      dispose() {
        calls.push(`dispose:${name}`);
      },
    });
  }
  await registry.abortAll();
  assert.equal(registry.size, 0);
  assert.deepEqual(calls.sort(), ["abort:a", "abort:b", "dispose:a", "dispose:b"]);
});
