import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import {
  ModelRegistry,
  ModelRuntime,
  type ExtensionContext,
} from "@earendil-works/pi-coding-agent";
import { ChildRegistry, GateProviderPreflight } from "../../extensions/pi/child-runtime.ts";
import {
  hashEvidencePayload,
  type KartaEvidenceManifest,
  type KartaEvidencePayload,
} from "../../extensions/pi/evidence.ts";
import { executeGateOnEvidence } from "../../extensions/pi/gate-runner.ts";

const exec = promisify(execFile);
const PROVIDER = "karta-gate-live";

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", args, { cwd })).stdout.trim();
}

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

async function repositoryFixture(): Promise<{
  root: string;
  repo: string;
  manifest: KartaEvidenceManifest;
}> {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-gate-live-"));
  const repo = join(root, "repo");
  await mkdir(repo);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Live Gate"]);
  await git(repo, ["config", "user.email", "live-gate@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "fixture"]);
  const tip = await git(repo, ["rev-parse", "HEAD"]);
  const tree = await git(repo, ["rev-parse", "HEAD^{tree}"]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/integration", tip]);
  await git(repo, ["update-ref", "refs/heads/karta/demo/item-item-a", tip]);
  const workItem = {
    id: "item-a",
    title: "Live gate fixture",
    summary: "Exercise a real isolated child",
    touches: ["subject.txt"],
    oracle: { type: "unit", assertions: ["subject is present"] },
  };
  const payload: KartaEvidencePayload = {
    binder: {
      slug: "demo",
      path: ".karta/binders/demo.json",
      blob: tip,
      sha256: "b".repeat(64),
      document: { slug: "demo", work_items: [workItem] },
    },
    workItem,
    git: {
      integrationRef: "refs/heads/karta/demo/integration",
      integrationTip: tip,
      itemRef: "refs/heads/karta/demo/item-item-a",
      itemTip: tip,
      mergeBase: tip,
      targetKind: "committed-tip",
      targetTree: tree,
    },
    diff: {
      format: "git-binary-patch",
      sha256: "d".repeat(64),
      bytes: 0,
      touchedPaths: ["subject.txt"],
      content: "",
    },
    checks: { manifest: { status: "not-required", targetTree: tree } },
    files: [],
    citations: [],
    packs: [],
  };
  return {
    root,
    repo,
    manifest: {
      schema: "karta-evidence-v2",
      generatedAt: new Date().toISOString(),
      repositoryRoot: repo,
      evidenceHash: hashEvidencePayload(payload),
      payload,
    },
  };
}

test("real isolated child sessions run both gate profiles through a declarative provider", async () => {
  const fixture = await repositoryFixture();
  const requests: Array<{ tools: string[]; toolResults: number }> = [];
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
      requests.push({ tools, toolResults });
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
  const runtime = await ModelRuntime.create({ allowModelNetwork: false });
  runtime.registerProvider(PROVIDER, {
    name: "Karta controlled gate provider",
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    api: "openai-completions",
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
  await runtime.setRuntimeApiKey(PROVIDER, "fixture-key", { allowNetwork: false });
  const model = runtime.getModel(PROVIDER, "fixture");
  assert.ok(model);
  const ctx = {
    cwd: fixture.repo,
    model,
    modelRegistry: new ModelRegistry(runtime),
    thinkingLevel: "minimal",
  } as unknown as ExtensionContext;
  const registry = new ChildRegistry();
  const preflight = new GateProviderPreflight();
  try {
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
    assert.equal(requests.length, 5);
    assert.deepEqual(requests.map((entry) => entry.toolResults), [0, 0, 4, 0, 4]);
    assert.deepEqual(serverErrors, []);
  } finally {
    await registry.abortAll();
    await new Promise<void>((resolveClose, rejectClose) =>
      server.close((error) => error ? rejectClose(error) : resolveClose()),
    );
    await rm(fixture.root, { recursive: true, force: true });
  }
});
