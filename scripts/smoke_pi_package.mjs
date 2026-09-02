import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const PROVIDER = join(ROOT, "tests", "pi", "fixtures", "mode-provider.ts");

async function run(command, args, options = {}) {
  return exec(command, args, {
    maxBuffer: 20 * 1024 * 1024,
    timeout: 60_000,
    ...options,
  });
}

function rpcCommands(cwd, agentDir) {
  return new Promise((resolveCommands, rejectCommands) => {
    const child = spawn("pi", ["--mode", "rpc", "--no-session", "--approve", "--no-context-files"], {
      cwd,
      env: { ...process.env, PI_CODING_AGENT_DIR: agentDir },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let buffered = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => finish(new Error(`Pi RPC timed out: ${stderr}`)), 20_000);
    const finish = (error, commands) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.stdin.end();
      child.kill("SIGTERM");
      const settle = () => error ? rejectCommands(error) : resolveCommands(commands);
      if (child.exitCode === null) child.once("exit", settle);
      else settle();
    };
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      buffered += chunk;
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line);
        if (message.type !== "response" || message.id !== "commands") continue;
        if (!message.success) finish(new Error(String(message.error ?? "get_commands failed")));
        else finish(undefined, message.data.commands);
      }
    });
    child.once("error", (error) => finish(error));
    child.once("exit", (code) => {
      if (!settled) finish(new Error(`Pi RPC exited ${code}: ${stderr}`));
    });
    child.stdin.write(`${JSON.stringify({ id: "commands", type: "get_commands" })}\n`);
  });
}

function completionChunk(delta, finishReason) {
  return `data: ${JSON.stringify({
    id: "chatcmpl-karta-package-smoke",
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1_000),
    model: "fixture",
    choices: [{ index: 0, delta, finish_reason: finishReason }],
  })}\n\n`;
}

async function runDispatch(cwd, agentDir, baseUrl) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("pi", [
      "--mode",
      "text",
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
      "Call karta_dispatch with action describeRole and role acceptance-gate, then reply MODE_OK.",
    ], {
      cwd,
      env: {
        ...process.env,
        PI_CODING_AGENT_DIR: agentDir,
        KARTA_MODE_PROVIDER_URL: baseUrl,
        KARTA_MODE_PROVIDER_KEY: "fixture-key",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 30_000);
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
      else rejectRun(new Error(`Pi print mode exited ${code ?? signal}: ${stderr}`));
    });
  });
}

async function main() {
  const sourceManifest = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  const testedPi = sourceManifest.devDependencies["@earendil-works/pi-coding-agent"];
  const actualPi = (await run("pi", ["--version"])).stdout.trim();
  assert.equal(actualPi, testedPi, `local Pi ${actualPi} does not match tested Pi ${testedPi}`);

  const tempRoot = await mkdtemp(join(tmpdir(), "karta-pi-packed-"));
  const workspace = join(tempRoot, "packaged smoke π");
  const tarballs = join(workspace, "tarballs");
  const extracted = join(workspace, "extracted");
  const consumer = join(workspace, "consumer repo");
  const agentDir = join(workspace, "agent");
  let installed = false;
  let packageRoot = "";
  try {
    await Promise.all([
      mkdir(tarballs, { recursive: true }),
      mkdir(extracted, { recursive: true }),
      mkdir(consumer, { recursive: true }),
      mkdir(agentDir, { recursive: true }),
    ]);
    const packed = await run("npm", ["pack", "--silent", "--ignore-scripts", "--pack-destination", tarballs], { cwd: ROOT });
    const filename = packed.stdout.trim().split("\n").at(-1);
    assert.ok(filename?.endsWith(".tgz"));
    const tarball = join(tarballs, filename);
    await run("tar", ["-xzf", tarball, "-C", extracted]);
    packageRoot = join(extracted, "package");
    await symlink(join(ROOT, "node_modules"), join(packageRoot, "node_modules"), "dir");
    const packedManifest = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
    assert.equal(packedManifest.name, "@engen-tech/karta");
    assert.equal(packedManifest.version, sourceManifest.version);
    assert.equal(packedManifest.private, true);
    assert.equal(packedManifest.license, "UNLICENSED");
    await assert.rejects(() => access(join(packageRoot, "tests")));

    await run("git", ["init", "--initial-branch=main"], { cwd: consumer });
    await run("git", ["config", "user.name", "Karta Package Smoke"], { cwd: consumer });
    await run("git", ["config", "user.email", "karta-package-smoke@example.invalid"], { cwd: consumer });
    await run("git", ["config", "core.hooksPath", join(consumer, ".git", "hooks")], { cwd: consumer });
    await writeFile(join(consumer, "README.md"), "fixture\n");
    await run("git", ["add", "README.md"], { cwd: consumer });
    await run("git", ["commit", "--no-gpg-sign", "-m", "fixture"], { cwd: consumer });

    const env = { ...process.env, PI_CODING_AGENT_DIR: agentDir };
    await run("pi", ["install", packageRoot], { cwd: consumer, env });
    installed = true;
    const listed = await run("pi", ["list"], { cwd: consumer, env });
    assert.match(listed.stdout, /karta/i);
    const commands = await rpcCommands(consumer, agentDir);
    const packageSkills = commands.filter((command) => command.name.startsWith("skill:karta-"));
    assert.equal(packageSkills.length, 10);

    let requests = 0;
    const server = createServer(async (request, response) => {
      requests += 1;
      let raw = "";
      for await (const chunk of request) raw += chunk;
      const body = JSON.parse(raw);
      const toolResults = (body.messages ?? []).filter((message) => message.role === "tool").length;
      response.writeHead(200, { "content-type": "text/event-stream" });
      if (toolResults === 0) {
        response.write(completionChunk({
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
        response.write(completionChunk({ role: "assistant", content: "MODE_OK" }, "stop"));
      }
      response.end("data: [DONE]\n\n");
    });
    await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
    const address = server.address();
    assert.ok(address && typeof address === "object");
    try {
      const result = await runDispatch(consumer, agentDir, `http://127.0.0.1:${address.port}/v1`);
      assert.match(result.stdout, /MODE_OK/);
      assert.doesNotMatch(result.stderr, /extension_error|two package roots/i);
      assert.equal(requests, 2);
    } finally {
      server.closeAllConnections();
      await new Promise((resolveClose) => server.close(resolveClose));
    }

    await run("pi", ["remove", packageRoot], { cwd: consumer, env });
    installed = false;
    const afterRemove = await run("pi", ["list"], { cwd: consumer, env });
    assert.equal(afterRemove.stdout.includes(packageRoot), false);
    process.stdout.write(`${JSON.stringify({
      package: `${packedManifest.name}@${packedManifest.version}`,
      pi: actualPi,
      skills: 10,
      dispatchRequests: requests,
      installRemove: "pass",
      result: "pass",
    })}\n`);
  } finally {
    if (installed && packageRoot) {
      const env = { ...process.env, PI_CODING_AGENT_DIR: agentDir };
      await run("pi", ["remove", packageRoot], { cwd: consumer, env }).catch(() => {});
    }
    await rm(tempRoot, { recursive: true, force: true });
  }
}

await main();
