import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import {
  ModelRegistry,
  ModelRuntime,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { ChildRegistry, GateProviderPreflight } from "../../extensions/pi/child-runtime.ts";
import { executeGateOnEvidence } from "../../extensions/pi/gate-runner.ts";
import { createGateEvidenceFixture } from "./fixtures/gate-evidence.ts";
const PROVIDER = "karta-gate-live";

async function readRequest(request: IncomingMessage): Promise<Record<string, unknown>> {
  let body = "";
  for await (const chunk of request) body += chunk;
  return JSON.parse(body) as Record<string, unknown>;
}

function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) =>
      part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string"
        ? (part as { text: string }).text
        : "",
    )
    .join("");
}

function sendChunks(response: ServerResponse, chunks: unknown[]): void {
  response.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });
  for (const chunk of chunks) response.write(`data: ${JSON.stringify(chunk)}\n\n`);
  response.end("data: [DONE]\n\n");
}

function completionChunk(delta: Record<string, unknown>, finishReason: string | null) {
  return {
    id: "chatcmpl-karta-live",
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model: "fixture",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  };
}

function toolCalls(names: Array<{ name: string; arguments: Record<string, unknown> }>) {
  return names.map((tool, index) => ({
    index,
    id: `call_${index}`,
    type: "function",
    function: { name: tool.name, arguments: JSON.stringify(tool.arguments) },
  }));
}

function finalVerdict(systemPrompt: string): string {
  const match = systemPrompt.match(
    /\{"schema":"karta-gate-verdict-v1","role":"([^"]+)","evidenceHash":"([a-f0-9]{64})","roleDefinitionHash":"([a-f0-9]{64})","promptHash":"([a-f0-9]{64})","profileHash":"([a-f0-9]{64})"/,
  );
  if (!match) throw new Error("controlled provider could not find the gate envelope in the system prompt");
  return JSON.stringify({
    schema: "karta-gate-verdict-v1",
    role: match[1],
    evidenceHash: match[2],
    roleDefinitionHash: match[3],
    promptHash: match[4],
    profileHash: match[5],
    verdict: "pass",
    summary: "Controlled provider inspected the fixed evidence.",
    findings: [],
  });
}

test("shutdown aborts an active isolated provider stream", async () => {
  const fixture = await createGateEvidenceFixture();
  let reportStarted!: () => void;
  let reportClosed!: () => void;
  const started = new Promise<void>((resolve) => { reportStarted = resolve; });
  const closed = new Promise<void>((resolve) => { reportClosed = resolve; });
  const server = createServer((request, response) => {
    response.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    response.write(`data: ${JSON.stringify(completionChunk({ role: "assistant", content: "KARTA" }, null))}\n\n`);
    reportStarted();
    request.once("close", reportClosed);
  });
  await new Promise<void>((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const provider = `${PROVIDER}-shutdown`;
  const runtime = await ModelRuntime.create({ allowModelNetwork: false });
  runtime.registerProvider(provider, {
    name: "Karta shutdown provider",
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    api: "openai-completions",
    models: [{
      id: "fixture",
      name: "Karta gate fixture",
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32_768,
      maxTokens: 2_048,
    }],
  });
  await runtime.setRuntimeApiKey(provider, "fixture-key");
  const model = runtime.getModel(provider, "fixture");
  assert.ok(model);
  const ctx = {
    cwd: fixture.repo,
    model,
    modelRegistry: new ModelRegistry(runtime),
    thinkingLevel: "minimal",
  } as unknown as ExtensionContext;
  const registry = new ChildRegistry();
  const preflight = new GateProviderPreflight();
  const pending = preflight.ensure(ctx, registry);
  const rejected = assert.rejects(() => pending);
  try {
    await started;
    assert.equal(registry.size, 1);
    await registry.abortAll();
    await rejected;
    await closed;
    assert.equal(registry.size, 0);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
    await rm(fixture.root, { recursive: true, force: true });
  }
});

test("stored, environment, runtime-key, and declarative provider classes complete both gate tool roundtrips", async () => {
  const fixture = await createGateEvidenceFixture();
  const requests: Array<{
    tools: string[];
    toolResults: number;
    authorization?: string;
    declarativeHeader?: string;
  }> = [];
  const serverErrors: string[] = [];
  const server = createServer(async (request, response) => {
    try {
      const body = await readRequest(request);
      const messages = Array.isArray(body.messages) ? body.messages as Array<Record<string, unknown>> : [];
      const tools = Array.isArray(body.tools)
        ? body.tools.map((tool) => {
            const fn = (tool as { function?: { name?: string } }).function;
            return fn?.name ?? "";
          })
        : [];
      const toolResults = messages.filter((message) => message.role === "tool").length;
      requests.push({
        tools,
        toolResults,
        authorization: request.headers.authorization,
        declarativeHeader: request.headers["x-karta-declarative"] as string | undefined,
      });
      const systemPrompt = messages
        .filter((message) => message.role === "system")
        .map((message) => messageText(message.content))
        .join("\n");

      if (systemPrompt.includes("KARTA_GATE_RUNTIME_OK")) {
        sendChunks(response, [
          completionChunk({ role: "assistant", content: "KARTA_GATE_RUNTIME_OK" }, "stop"),
        ]);
        return;
      }
      if (toolResults === 0 && tools.includes("karta_checks")) {
        sendChunks(response, [
          completionChunk(
            {
              role: "assistant",
              tool_calls: toolCalls([
                { name: "karta_evidence", arguments: { action: "summary" } },
                { name: "karta_evidence", arguments: { action: "workItem" } },
                { name: "karta_evidence", arguments: { action: "diff" } },
                { name: "karta_checks", arguments: { action: "summary" } },
              ]),
            },
            "tool_calls",
          ),
        ]);
        return;
      }
      if (toolResults === 0 && tools.includes("karta_boundary")) {
        sendChunks(response, [
          completionChunk(
            {
              role: "assistant",
              tool_calls: toolCalls([
                { name: "karta_evidence", arguments: { action: "summary" } },
                { name: "karta_evidence", arguments: { action: "workItem" } },
                { name: "karta_evidence", arguments: { action: "diff" } },
                { name: "karta_boundary", arguments: { action: "inspect" } },
              ]),
            },
            "tool_calls",
          ),
        ]);
        return;
      }
      if (toolResults > 0) {
        sendChunks(response, [
          completionChunk({ role: "assistant", content: finalVerdict(systemPrompt) }, "stop"),
        ]);
        return;
      }
      throw new Error("controlled provider received an unexpected request");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      serverErrors.push(message);
      response.writeHead(500, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message } }));
    }
  });
  await new Promise<void>((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const agentDir = join(fixture.root, "agent");
  await mkdir(agentDir, { recursive: true });
  const storedProvider = `${PROVIDER}-stored`;
  await writeFile(join(agentDir, "auth.json"), JSON.stringify({
    [storedProvider]: { type: "api_key", key: "fixture-key" },
  }));
  const priorAgentDir = process.env.PI_CODING_AGENT_DIR;
  const priorFixtureKey = process.env.KARTA_GATE_LIVE_KEY;
  process.env.PI_CODING_AGENT_DIR = agentDir;
  process.env.KARTA_GATE_LIVE_KEY = "fixture-key";
  const registries: ChildRegistry[] = [];
  try {
    for (const authClass of ["stored", "environment", "runtime-key", "declarative"] as const) {
      const provider = `${PROVIDER}-${authClass}`;
      const runtime = await ModelRuntime.create({ allowModelNetwork: false });
      runtime.registerProvider(provider, {
        name: `Karta ${authClass} gate provider`,
        baseUrl: `http://127.0.0.1:${address.port}/v1`,
        api: "openai-completions",
        ...(authClass === "environment" || authClass === "declarative"
          ? { apiKey: "$KARTA_GATE_LIVE_KEY" }
          : {}),
        ...(authClass === "declarative" ? { headers: { "x-karta-declarative": "copied" } } : {}),
        models: [
          {
            id: "fixture",
            name: "Karta gate fixture",
            reasoning: false,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 32_768,
            maxTokens: 2_048,
          },
        ],
      });
      if (authClass === "runtime-key") {
        await runtime.setRuntimeApiKey(provider, "fixture-key");
      }
      const model = runtime.getModel(provider, "fixture");
      assert.ok(model);
      const modelRegistry = new ModelRegistry(runtime);
      const expectedSource = authClass === "runtime-key"
        ? "runtime"
        : authClass === "stored"
          ? "stored"
          : "environment";
      assert.equal(modelRegistry.getProviderAuthStatus(provider).source, expectedSource);
      const ctx = {
        cwd: fixture.repo,
        model,
        modelRegistry,
        thinkingLevel: "minimal",
      } as unknown as ExtensionContext;
      const registry = new ChildRegistry();
      registries.push(registry);
      const preflight = new GateProviderPreflight();
      const acceptance = await executeGateOnEvidence(
        ctx,
        "acceptance-gate",
        fixture.manifest,
        preflight,
        registry,
      );
      const safety = await executeGateOnEvidence(
        ctx,
        "safety-gate",
        fixture.manifest,
        preflight,
        registry,
      );
      assert.equal(acceptance.verdict, "pass");
      assert.equal(safety.verdict, "pass");
      assert.equal(acceptance.evidenceHash, safety.evidenceHash);
      assert.equal(registry.size, 0);
      assert.equal(preflight.size, 1);
    }
    assert.equal(requests.length, 20);
    assert.deepEqual(
      requests.map((entry) => entry.toolResults),
      Array.from({ length: 4 }, () => [0, 0, 4, 0, 4]).flat(),
    );
    assert.equal(requests.every((entry) => entry.authorization === "Bearer fixture-key"), true);
    assert.equal(requests.filter((entry) => entry.declarativeHeader === "copied").length, 5);
    assert.deepEqual(serverErrors, []);
  } finally {
    if (priorAgentDir === undefined) delete process.env.PI_CODING_AGENT_DIR;
    else process.env.PI_CODING_AGENT_DIR = priorAgentDir;
    if (priorFixtureKey === undefined) delete process.env.KARTA_GATE_LIVE_KEY;
    else process.env.KARTA_GATE_LIVE_KEY = priorFixtureKey;
    await Promise.all(registries.map((registry) => registry.abortAll()));
    await new Promise<void>((resolveClose, rejectClose) =>
      server.close((error) => error ? rejectClose(error) : resolveClose()),
    );
    await rm(fixture.root, { recursive: true, force: true });
  }
});
