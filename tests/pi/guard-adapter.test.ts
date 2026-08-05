import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import type {
  AgentSettledEvent,
  BeforeAgentStartEvent,
  ExtensionAPI,
  ExtensionContext,
  ToolCallEvent,
  ToolResultEvent,
} from "@earendil-works/pi-coding-agent";
import { KartaGuardAdapter, type GuardExecutor } from "../../extensions/pi/guard-adapter.ts";
import type { GuardResult, KartaGuard } from "../../extensions/pi/guard-runner.ts";

function result(code = 0, stderr = "", stdout = ""): GuardResult {
  return { code, stderr, stdout, failedOpen: false };
}

function context(cwd: string, trusted = true): ExtensionContext {
  return {
    cwd,
    isProjectTrusted: () => trusted,
    sessionManager: { getSessionId: () => "session-phase2" },
  } as unknown as ExtensionContext;
}

function extension(messages: string[] = []): ExtensionAPI {
  return {
    sendUserMessage(content: string) {
      messages.push(content);
    },
  } as unknown as ExtensionAPI;
}

test("untrusted projects cannot call Karta action tools", async () => {
  let calls = 0;
  const execute: GuardExecutor = async () => {
    calls += 1;
    return result();
  };
  const adapter = new KartaGuardAdapter(extension(), execute);
  const decision = await adapter.beforeToolCall(
    {
      type: "tool_call",
      toolCallId: "one",
      toolName: "karta_script",
      input: { action: "detectStack", root: "." },
    } as ToolCallEvent,
    context(process.cwd(), false),
  );
  assert.equal(decision?.block, true);
  assert.match(decision?.reason ?? "", /untrusted/);
  assert.equal(calls, 0);
});

test("Pi write calls are translated to binder and pack guard payloads", async () => {
  const calls: Array<{ guard: KartaGuard; payload: unknown }> = [];
  const execute: GuardExecutor = async (guard, payload) => {
    calls.push({ guard, payload });
    return guard === "packWrite" ? result(2, "invalid pack") : result();
  };
  const adapter = new KartaGuardAdapter(extension(), execute);
  const decision = await adapter.beforeToolCall(
    {
      type: "tool_call",
      toolCallId: "write",
      toolName: "write",
      input: { path: ".karta/sme/bad.md", content: "bad" },
    } as ToolCallEvent,
    context("/repo"),
  );
  assert.equal(decision?.block, true);
  assert.equal(decision?.reason, "invalid pack");
  assert.deepEqual(
    calls.map((call) => call.guard),
    ["packWrite"],
  );
  assert.deepEqual(calls[0].payload, {
    hook_event_name: "PreToolUse",
    tool_name: "Write",
    tool_input: { file_path: ".karta/sme/bad.md", content: "bad" },
    cwd: "/repo",
  });
  const binder = await adapter.beforeToolCall(
    {
      type: "tool_call",
      toolCallId: "binder",
      toolName: "edit",
      input: { path: ".karta/binders/plan.json", edits: [] },
    } as ToolCallEvent,
    context("/repo"),
  );
  assert.equal(binder, undefined);
  assert.deepEqual(
    calls.map((call) => call.guard),
    ["packWrite", "binderImmutability"],
  );
  const ordinary = await adapter.beforeToolCall(
    {
      type: "tool_call",
      toolCallId: "ordinary",
      toolName: "write",
      input: { path: "src/file.ts", content: "code" },
    } as ToolCallEvent,
    context("/repo"),
  );
  assert.equal(ordinary, undefined);
  assert.equal(calls.length, 2);
});

test("post-write pack findings turn a successful Pi result into an error", async () => {
  const execute: GuardExecutor = async () => result(2, "repair the pack");
  const adapter = new KartaGuardAdapter(extension(), execute);
  const changed = await adapter.afterToolResult(
    {
      type: "tool_result",
      toolCallId: "write",
      toolName: "write",
      input: { path: ".karta/sme/bad.md", content: "bad" },
      content: [{ type: "text", text: "wrote file" }],
      isError: false,
      details: undefined,
    } as ToolResultEvent,
    context("/repo"),
  );
  assert.equal(changed?.isError, true);
  assert.match(
    changed?.content?.filter((part) => part.type === "text").at(-1)?.text ?? "",
    /repair the pack/,
  );
});

test("trusted Karta repos receive package-generated status context", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-status-adapter-"));
  await mkdir(join(root, ".karta", "binders"), { recursive: true });
  const execute: GuardExecutor = async (guard) =>
    guard === "statusInjection" ? result(0, "", "<karta-status>ready</karta-status>\n") : result();
  try {
    const adapter = new KartaGuardAdapter(extension(), execute);
    const injected = await adapter.beforeAgentStart(
      { type: "before_agent_start", systemPrompt: "base" } as BeforeAgentStartEvent,
      context(root),
    );
    assert.equal(injected?.systemPrompt, "base\n\n<karta-status>ready</karta-status>");
    const denied = await adapter.beforeAgentStart(
      { type: "before_agent_start", systemPrompt: "base" } as BeforeAgentStartEvent,
      context(root, false),
    );
    assert.equal(denied, undefined);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("settled-turn backstops nudge once per unchanged whiff state", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-settled-adapter-"));
  await mkdir(join(root, ".karta", "binders"), { recursive: true });
  const messages: string[] = [];
  let dirtyRuns = 0;
  const execute: GuardExecutor = async (guard) => {
    if (guard === "subagentWhiff") return result(2, "karta: whiff item-a");
    if (guard === "deliveryStop") {
      dirtyRuns += 1;
      return dirtyRuns === 1
        ? result(
            2,
            "karta: this session is stopping dirty. This stop is blocked once per state — an identical stop will pass, so fix it now or stop again to defer to the resume flow.",
          )
        : result();
    }
    return result();
  };
  try {
    const adapter = new KartaGuardAdapter(extension(messages), execute);
    const settled = { type: "agent_settled" } as AgentSettledEvent;
    await adapter.agentSettled(settled, context(root));
    await adapter.agentSettled(settled, context(root));
    assert.equal(messages.length, 1);
    assert.match(messages[0], /whiff item-a/);
    assert.match(messages[0], /this Pi turn settled dirty/);
    assert.match(messages[0], /Git-native resume flow/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
