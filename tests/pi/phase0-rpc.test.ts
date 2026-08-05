import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const DYNAMIC_PROVIDER = join(ROOT, "tests", "pi", "fixtures", "dynamic-provider.ts");

interface RpcCommand {
  name: string;
  description?: string;
  source: string;
  path?: string;
}

interface ProbeReport {
  provider: string;
  model: string;
  policy: "probe" | "gate";
  exactModelResolved: boolean;
  parentAuthConfigured: boolean;
  childAuthConfigured: boolean;
  copiedProvider: "builtin" | "config" | "native";
  copiedRuntimeCredential: boolean;
}

async function rpcRequest<T>(options: {
  cwd: string;
  agentDir: string;
  args?: string[];
  env?: NodeJS.ProcessEnv;
  request: object;
  match(message: Record<string, unknown>): T | undefined;
}): Promise<T> {
  return new Promise<T>((resolveRequest, rejectRequest) => {
    const child = spawn(
      "pi",
      [
        "--mode",
        "rpc",
        "--no-session",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        ...(options.args ?? []),
      ],
      {
        cwd: options.cwd,
        env: { ...process.env, PI_CODING_AGENT_DIR: options.agentDir, ...options.env },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    let stderr = "";
    let buffered = "";
    let settling = false;
    const timer = setTimeout(() => finish(undefined, new Error(`RPC timeout: ${stderr}`)), 20_000);

    function finish(value?: T, error?: Error): void {
      if (settling) return;
      settling = true;
      clearTimeout(timer);
      child.stdin.end();
      child.kill("SIGTERM");
      const settle = () => {
        if (error) rejectRequest(error);
        else resolveRequest(value as T);
      };
      if (child.exitCode !== null) settle();
      else child.once("exit", settle);
    }

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buffered += chunk;
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line) as Record<string, unknown>;
        const value = options.match(message);
        if (value !== undefined) {
          finish(value);
          return;
        }
      }
    });
    child.once("error", (error) => finish(undefined, error));
    child.once("exit", (code) => {
      if (!settling) finish(undefined, new Error(`Pi exited ${code}: ${stderr}`));
    });
    child.stdin.write(`${JSON.stringify(options.request)}\n`);
  });
}

async function getCommands(cwd: string, agentDir: string, approved: boolean): Promise<RpcCommand[]> {
  return rpcRequest({
    cwd,
    agentDir,
    args: [approved ? "--approve" : "--no-approve", "-e", ROOT],
    request: { id: "commands", type: "get_commands" },
    match(message) {
      if (message.type !== "response" || message.id !== "commands") return undefined;
      if (!message.success) throw new Error(String(message.error ?? "get_commands failed"));
      return (message.data as { commands: RpcCommand[] }).commands;
    },
  });
}

async function runAuthProbe(options: {
  cwd: string;
  agentDir: string;
  model: string;
  apiKey?: string;
  dynamicProvider?: boolean;
  action?: "auth" | "gate-auth";
}): Promise<ProbeReport> {
  const args = ["--approve", "--model", options.model];
  if (options.apiKey) args.push("--api-key", options.apiKey);
  if (options.dynamicProvider) args.push("-e", DYNAMIC_PROVIDER);
  args.push("-e", ROOT);
  return rpcRequest({
    cwd: options.cwd,
    agentDir: options.agentDir,
    args,
    env: options.dynamicProvider ? { PHASE0_DYNAMIC_API_KEY: "phase0-fixture-key" } : undefined,
    request: {
      id: "probe",
      type: "prompt",
      message: `/karta-phase0 ${options.action ?? "auth"}`,
    },
    match(message) {
      if (message.type !== "extension_ui_request" || message.method !== "notify") return undefined;
      return JSON.parse(String(message.message)) as ProbeReport;
    },
  });
}

test("package extension trust-gates skills and loads once", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-trust-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(join(cwd, ".pi", "skills", "trust-trigger"), { recursive: true });
  await mkdir(join(cwd, ".pi", "skills", "karta-plan"), { recursive: true });
  await mkdir(agentDir, { recursive: true });
  await writeFile(
    join(cwd, ".pi", "skills", "trust-trigger", "SKILL.md"),
    "---\nname: trust-trigger\ndescription: force project trust resolution\n---\n",
  );
  await writeFile(
    join(cwd, ".pi", "skills", "karta-plan", "SKILL.md"),
    "---\nname: karta-plan\ndescription: collision fixture\n---\n",
  );
  try {
    const denied = await getCommands(cwd, agentDir, false);
    const approved = await getCommands(cwd, agentDir, true);
    assert.equal(denied.filter((command) => command.name === "karta-phase0").length, 1);
    assert.equal(approved.filter((command) => command.name === "karta-phase0").length, 1);
    assert.equal(denied.some((command) => command.name === "skill:karta-plan"), false);
    const plan = approved.find((command) => command.name === "skill:karta-plan");
    assert.ok(plan, JSON.stringify(approved, null, 2));
    assert.equal(plan.description, "collision fixture");
    assert.equal(
      approved.filter((command) => command.source === "skill" && command.name.startsWith("skill:karta-")).length,
      10,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("child runtime mirrors a dynamic provider", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-provider-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(cwd, { recursive: true });
  await mkdir(agentDir, { recursive: true });
  try {
    const report = await runAuthProbe({
      cwd,
      agentDir,
      model: "phase0-dynamic/fixture",
      dynamicProvider: true,
    });
    assert.equal(report.copiedProvider, "config");
    assert.equal(report.policy, "probe");
    assert.equal(report.exactModelResolved, true);
    assert.equal(report.parentAuthConfigured, true);
    assert.equal(report.childAuthConfigured, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("strict gate runtime resolves a declarative provider without parent fallback", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-gate-provider-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(cwd, { recursive: true });
  await mkdir(agentDir, { recursive: true });
  try {
    const report = await runAuthProbe({
      cwd,
      agentDir,
      model: "phase0-dynamic/fixture",
      dynamicProvider: true,
      action: "gate-auth",
    });
    assert.equal(report.policy, "gate");
    assert.equal(report.copiedProvider, "config");
    assert.equal(report.exactModelResolved, true);
    assert.equal(report.childAuthConfigured, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("strict gate runtime copies a CLI API-key override", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-gate-runtime-key-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(cwd, { recursive: true });
  await mkdir(agentDir, { recursive: true });
  try {
    const report = await runAuthProbe({
      cwd,
      agentDir,
      model: "openai/gpt-4o",
      apiKey: "phase0-fixture-key",
      action: "gate-auth",
    });
    assert.equal(report.policy, "gate");
    assert.equal(report.copiedProvider, "builtin");
    assert.equal(report.exactModelResolved, true);
    assert.equal(report.copiedRuntimeCredential, true);
    assert.equal(report.childAuthConfigured, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("child runtime copies a CLI API-key override", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-runtime-key-"));
  const cwd = join(root, "repo");
  const agentDir = join(root, "agent");
  await mkdir(cwd, { recursive: true });
  await mkdir(agentDir, { recursive: true });
  try {
    const report = await runAuthProbe({
      cwd,
      agentDir,
      model: "openai/gpt-4o",
      apiKey: "phase0-fixture-key",
    });
    assert.equal(report.copiedProvider, "builtin");
    assert.equal(report.policy, "probe");
    assert.equal(report.exactModelResolved, true);
    assert.equal(report.copiedRuntimeCredential, true);
    assert.equal(report.childAuthConfigured, true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
