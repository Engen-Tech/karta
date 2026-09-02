import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const PROVIDER = join(ROOT, "tests", "pi", "fixtures", "mode-provider.ts");
const TUI_DRIVER = join(ROOT, "tests", "pi", "fixtures", "tui-driver.py");
const PI_CLI = join(dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"))), "cli.js");

function chunk(delta: Record<string, unknown>, finishReason: string | null): string {
  return `data: ${JSON.stringify({
    id: "chatcmpl-karta-mode",
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1_000),
    model: "fixture",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`;
}

async function runMode(
  mode: "text" | "json",
  cwd: string,
  agentDir: string,
  baseUrl: string,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(process.execPath, [
      PI_CLI,
      "--mode",
      mode,
      "--print",
      "--no-session",
      "--approve",
      "--no-context-files",
      "--no-skills",
      "--offline",
      "--model",
      "karta-mode/fixture",
      "-e",
      PROVIDER,
      "-e",
      ROOT,
      "Call karta_dispatch with action describeRole and role acceptance-gate, then reply MODE_OK.",
    ], {
      cwd,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: agentDir,
        KARTA_MODE_PROVIDER_URL: baseUrl,
        KARTA_MODE_PROVIDER_KEY: "fixture-key",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 15_000);
    child.stdin.end();
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", (error) => {
      clearTimeout(timer);
      rejectRun(error);
    });
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      if (code === 0) resolveRun({ stdout, stderr });
      else rejectRun(new Error(`pi ${mode} exited ${code ?? signal}: ${stderr}`));
    });
  });
}

async function runRpc(
  cwd: string,
  agentDir: string,
  baseUrl: string,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(process.execPath, [
      PI_CLI,
      "--mode",
      "rpc",
      "--no-session",
      "--approve",
      "--no-context-files",
      "--no-skills",
      "--offline",
      "--model",
      "karta-mode/fixture",
      "-e",
      PROVIDER,
      "-e",
      ROOT,
    ], {
      cwd,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: agentDir,
        KARTA_MODE_PROVIDER_URL: baseUrl,
        KARTA_MODE_PROVIDER_KEY: "fixture-key",
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let buffered = "";
    let settled = false;
    const timer = setTimeout(() => finish(new Error(`Pi RPC timeout: ${stderr}`)), 30_000);
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.stdin.end();
      child.kill("SIGTERM");
      const settle = () => error ? rejectRun(error) : resolveRun({ stdout, stderr });
      if (child.exitCode === null) child.once("exit", settle);
      else settle();
    };
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      buffered += chunk;
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line) as { type?: string };
        if (message.type === "agent_end") finish();
      }
    });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", finish);
    child.once("exit", (code) => {
      if (!settled) finish(new Error(`Pi RPC exited ${code}: ${stderr}`));
    });
    child.stdin.write(`${JSON.stringify({
      id: "dispatch",
      type: "prompt",
      message: "Call karta_dispatch with action describeRole and role acceptance-gate, then reply MODE_OK.",
    })}\n`);
  });
}

async function runPosixTui(
  cwd: string,
  agentDir: string,
  baseUrl: string,
  doneFile: string,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("uv", [
      "run",
      "--script",
      TUI_DRIVER,
      process.execPath,
      PI_CLI,
      "--tui-mode",
      "regular",
      "--no-session",
      "--approve",
      "--no-context-files",
      "--no-skills",
      "--offline",
      "--model",
      "karta-mode/fixture",
      "-e",
      PROVIDER,
      "-e",
      ROOT,
      "Call karta_dispatch with action describeRole and role acceptance-gate, then reply MODE_OK.",
    ], {
      cwd,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: agentDir,
        KARTA_MODE_PROVIDER_URL: baseUrl,
        KARTA_MODE_PROVIDER_KEY: "fixture-key",
        KARTA_TUI_DONE_FILE: doneFile,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 35_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", (error) => {
      clearTimeout(timer);
      rejectRun(error);
    });
    child.once("close", (code, signal) => {
      clearTimeout(timer);
      if (code === 0) resolveRun({ stdout, stderr });
      else rejectRun(new Error(`pi TUI exited ${code ?? signal}: ${stderr}\n${stdout.slice(-4_000)}`));
    });
  });
}

test("Pi text, JSON, RPC, and POSIX TUI modes execute the fixed package dispatch tool", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-modes-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(cwd);
  await mkdir(agentDir);
  const tuiDone = join(root, "tui.done");
  let requests = 0;
  const server = createServer(async (request, response) => {
    requests += 1;
    let raw = "";
    for await (const part of request) raw += part;
    const body = JSON.parse(raw) as { messages?: Array<{ role?: string }> };
    const toolResults = body.messages?.filter((message) => message.role === "tool").length ?? 0;
    response.writeHead(200, { "content-type": "text/event-stream" });
    if (toolResults === 0) {
      response.write(chunk({
        role: "assistant",
        tool_calls: [{
          index: 0,
          id: "call_dispatch",
          type: "function",
          function: {
            name: "karta_dispatch",
            arguments: JSON.stringify({ action: "describeRole", role: "acceptance-gate" }),
          },
        }],
      }, "tool_calls"));
    } else {
      response.write(chunk({ role: "assistant", content: "MODE_OK" }, "stop"));
    }
    response.end("data: [DONE]\n\n");
    if (toolResults > 0 && requests >= 8) await writeFile(tuiDone, "done\n");
  });
  await new Promise<void>((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}/v1`;
  try {
    let text;
    try {
      text = await runMode("text", cwd, agentDir, baseUrl);
    } catch (error) {
      const failure = error as { stdout?: string; stderr?: string; message?: string };
      throw new Error(`text mode failed after ${requests} requests: ${failure.message ?? error}\n${failure.stdout ?? ""}\n${failure.stderr ?? ""}`);
    }
    assert.match(text.stdout, /MODE_OK/);
    assert.doesNotMatch(text.stderr, /extension_error|two package roots/i);
    const json = await runMode("json", cwd, agentDir, baseUrl);
    assert.match(json.stdout, /MODE_OK/);
    assert.doesNotMatch(json.stderr, /extension_error|two package roots/i);
    const rpc = await runRpc(cwd, agentDir, baseUrl);
    assert.match(rpc.stdout, /tool_execution_end/);
    assert.doesNotMatch(rpc.stderr, /extension_error|two package roots/i);
    if (process.platform !== "win32") {
      const tui = await runPosixTui(cwd, agentDir, baseUrl, tuiDone);
      assert.ok(tui.stdout.length > 0);
      assert.doesNotMatch(tui.stderr, /extension_error|two package roots/i);
    }
    assert.equal(requests, process.platform === "win32" ? 6 : 8);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
    await rm(root, { recursive: true, force: true });
  }
});
