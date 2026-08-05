import assert from "node:assert/strict";
import test from "node:test";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type GateProviderPreflightReport } from "../../extensions/pi/child-runtime.ts";
import {
  createKartaDispatchTool,
  type KartaDispatchParameters,
} from "../../extensions/pi/dispatch-tool.ts";

function context(trusted = true): ExtensionContext {
  return { isProjectTrusted: () => trusted } as ExtensionContext;
}

const preflightReport: GateProviderPreflightReport = {
  provider: "fixture",
  model: "model",
  policy: "gate",
  exactModelResolved: true,
  parentAuthConfigured: true,
  childAuthConfigured: true,
  copiedProvider: "builtin",
  copiedRuntimeCredential: false,
  unresolvedEnvironmentKeys: [],
  cached: false,
};

test("dispatch role descriptions expose hashes and capabilities, never prompt text or paths", async () => {
  let preflightCalls = 0;
  const tool = createKartaDispatchTool(
    {
      async ensure() {
        preflightCalls += 1;
        return preflightReport;
      },
    },
    new ChildRegistry(),
  );
  const response = await tool.execute(
    "describe",
    { action: "describeRole", role: "acceptance-gate" },
    undefined,
    undefined,
    context(),
  );
  const body = JSON.parse(response.content[0].type === "text" ? response.content[0].text : "{}");
  assert.equal((response as { isError?: boolean }).isError, false);
  assert.equal(body.role, "acceptance-gate");
  assert.equal(body.authority, "read-only");
  assert.deepEqual(body.capabilities, ["evidence.read", "checks.read"]);
  assert.match(body.definitionHash, /^[a-f0-9]{64}$/);
  assert.equal("prompt" in body, false);
  assert.equal("sourcePath" in body, false);
  assert.equal(preflightCalls, 0);
});

test("dispatch preflight binds a fixed read-only role to isolated provider evidence", async () => {
  let calls = 0;
  const tool = createKartaDispatchTool(
    {
      async ensure() {
        calls += 1;
        return preflightReport;
      },
    },
    new ChildRegistry(),
  );
  const response = await tool.execute(
    "preflight",
    { action: "preflightGate", role: "safety-gate" },
    undefined,
    undefined,
    context(),
  );
  assert.equal((response as { isError?: boolean }).isError, false);
  assert.equal(calls, 1);
  assert.deepEqual(response.details, {
    action: "preflightGate",
    role: "safety-gate",
    definitionHash: response.details?.definitionHash,
    provider: "fixture",
    model: "model",
    cached: false,
  });
  assert.match(response.details?.definitionHash ?? "", /^[a-f0-9]{64}$/);
});

test("dispatch fails before role loading or preflight in an untrusted project", async () => {
  let calls = 0;
  const tool = createKartaDispatchTool(
    {
      async ensure() {
        calls += 1;
        return preflightReport;
      },
    },
    new ChildRegistry(),
  );
  const response = await tool.execute(
    "denied",
    { action: "preflightGate", role: "acceptance-gate" },
    undefined,
    undefined,
    context(false),
  );
  assert.equal((response as { isError?: boolean }).isError, true);
  assert.match(response.content[0].type === "text" ? response.content[0].text : "", /untrusted/);
  assert.equal(calls, 0);
});

test("dispatch runs verification from fixed Git identity without role or prompt input", async () => {
  let received: unknown;
  const tool = createKartaDispatchTool(
    { ensure: async () => preflightReport },
    new ChildRegistry(),
    {
      async run(_ctx, binder, item, mode) {
        received = { binder, item, mode };
        return { evidenceHash: "a".repeat(64), status: "pass" };
      },
    },
  );
  const response = await tool.execute(
    "verification",
    { action: "runVerification", binder: "demo", item: "item-a", mode: "full" },
    undefined,
    undefined,
    context(),
  );
  assert.equal((response as { isError?: boolean }).isError, false);
  assert.deepEqual(received, { binder: "demo", item: "item-a", mode: "full" });
  assert.equal(response.details?.role, undefined);
  assert.equal(response.details?.evidenceHash, "a".repeat(64));
});

test("dispatch schema contains no caller-controlled prompt, path, tool, or provider fields", () => {
  const tool = createKartaDispatchTool(
    { ensure: async () => preflightReport },
    new ChildRegistry(),
  );
  const serialized = JSON.stringify(tool.parameters);
  for (const forbidden of ["prompt", "path", "tool", "provider", "model"]) {
    assert.equal(serialized.includes(`\"${forbidden}\"`), false, forbidden);
  }
  const valid: KartaDispatchParameters = {
    action: "describeRole",
    role: "build-worker",
  };
  assert.equal(valid.role, "build-worker");
});
