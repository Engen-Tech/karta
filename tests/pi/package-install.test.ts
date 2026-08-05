import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { cp, lstat, mkdir, mkdtemp, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const exec = promisify(execFile);
const ROOT = resolve(fileURLToPath(new URL("../..", import.meta.url)));

interface RpcCommand {
  name: string;
  source: string;
}

async function runPi(
  args: string[],
  options: { cwd: string; agentDir: string },
): Promise<{ stdout: string; stderr: string }> {
  try {
    return await exec("pi", args, {
      cwd: options.cwd,
      env: { ...process.env, PI_CODING_AGENT_DIR: options.agentDir },
      maxBuffer: 20 * 1024 * 1024,
      timeout: 60_000,
    });
  } catch (error) {
    throw new Error(`pi ${args.join(" ")} failed`, { cause: error });
  }
}

async function rpcCommands(cwd: string, agentDir: string): Promise<{
  commands: RpcCommand[];
  extensionErrors: string[];
}> {
  return new Promise((resolveRequest, rejectRequest) => {
    const child = execFile(
      "pi",
      ["--mode", "rpc", "--no-session", "--approve", "--no-context-files"],
      {
        cwd,
        env: { ...process.env, PI_CODING_AGENT_DIR: agentDir },
      },
    );
    let stderr = "";
    let buffered = "";
    let settling = false;
    const extensionErrors: string[] = [];
    const timer = setTimeout(() => finish(undefined, new Error(`RPC timeout: ${stderr}`)), 30_000);

    function finish(
      value?: { commands: RpcCommand[]; extensionErrors: string[] },
      error?: Error,
    ): void {
      if (settling) return;
      settling = true;
      clearTimeout(timer);
      child.stdin?.end();
      child.kill("SIGTERM");
      const settle = () => (error ? rejectRequest(error) : resolveRequest(value!));
      if (child.exitCode !== null) settle();
      else child.once("exit", settle);
    }

    child.stderr?.setEncoding("utf8");
    child.stderr?.on("data", (chunk) => {
      stderr += chunk;
    });
    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk) => {
      buffered += chunk;
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line);
        if (message.type === "extension_error") {
          extensionErrors.push(String(message.error ?? message.message ?? "extension error"));
        }
        if (message.type === "response" && message.id === "commands") {
          if (!message.success) {
            finish(undefined, new Error(String(message.error ?? "get_commands failed")));
            return;
          }
          finish({ commands: message.data.commands, extensionErrors });
          return;
        }
      }
    });
    child.once("error", (error) => finish(undefined, error));
    child.once("exit", (code) => {
      if (!settling) finish(undefined, new Error(`Pi exited ${code}: ${stderr}`));
    });
    child.stdin?.write(`${JSON.stringify({ id: "commands", type: "get_commands" })}\n`);
  });
}

async function copyGitFixture(destination: string): Promise<void> {
  await mkdir(destination, { recursive: true });
  for (const path of [
    ".gitignore",
    "package.json",
    "package-lock.json",
    "extensions",
    "skills",
    "agents",
    "hooks",
  ]) {
    await cp(join(ROOT, path), join(destination, path), { recursive: true });
  }
}

async function git(cwd: string, args: string[]): Promise<void> {
  await exec("git", args, { cwd, maxBuffer: 20 * 1024 * 1024 });
}

async function createGitFixture(root: string): Promise<{ bare: string }> {
  const repo = join(root, "source repo");
  const bare = join(root, "http", "engen", "karta.git");
  await copyGitFixture(repo);
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Phase 1"]);
  await git(repo, ["config", "user.email", "phase1@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["config", "tag.gpgSign", "false"]);
  await git(repo, ["config", "tag.forceSignAnnotated", "false"]);
  await git(repo, ["config", "core.editor", "true"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "-m", "phase1 fixture v1"]);
  await git(repo, ["tag", "--no-sign", "phase1-v1"]);

  const fixtureSkill = join(repo, "skills", "phase1-fixture");
  await mkdir(fixtureSkill, { recursive: true });
  await writeFile(
    join(fixtureSkill, "SKILL.md"),
    "---\nname: phase1-fixture\ndescription: pinned Git update fixture\n---\n",
  );
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "-m", "phase1 fixture v2"]);
  await git(repo, ["tag", "--no-sign", "phase1-v2"]);

  await mkdir(dirname(bare), { recursive: true });
  await exec("git", ["clone", "--bare", repo, bare], { maxBuffer: 20 * 1024 * 1024 });
  await exec("git", ["--git-dir", bare, "update-server-info"]);
  return { bare };
}

async function startGitServer(root: string): Promise<{ server: Server; origin: string }> {
  const canonicalRoot = resolve(root);
  const server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
      const target = resolve(canonicalRoot, `.${pathname}`);
      const fromRoot = relative(canonicalRoot, target);
      if (fromRoot === ".." || fromRoot.startsWith(`..${sep}`)) {
        response.writeHead(403).end();
        return;
      }
      if (!(await lstat(target)).isFile()) {
        response.writeHead(404).end();
        return;
      }
      response.writeHead(200, { "Cache-Control": "no-cache" });
      createReadStream(target).pipe(response);
    } catch {
      response.writeHead(404).end();
    }
  });
  await new Promise<void>((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolveListen());
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Git fixture server has no TCP address");
  return { server, origin: `http://127.0.0.1:${address.port}` };
}

async function closeServer(server: Server): Promise<void> {
  server.closeAllConnections();
  await new Promise<void>((resolveClose, reject) =>
    server.close((error) => (error ? reject(error) : resolveClose())),
  );
}

function hasKarta(commands: RpcCommand[]): boolean {
  return commands.some((command) => command.name === "karta-phase0");
}

test("local package install works through a spaced Unicode symlink", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-local-install-"));
  const agentDir = join(root, "agent");
  const cwd = join(root, "unrelated repo");
  const link = join(root, "Karta π package");
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });
  try {
    try {
      await symlink(ROOT, link, "dir");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "EPERM") {
        context.skip("directory symlinks require elevated privileges on this Windows runner");
        return;
      }
      throw error;
    }
    await runPi(["install", link], { cwd, agentDir });
    const loaded = await rpcCommands(cwd, agentDir);
    assert.equal(hasKarta(loaded.commands), true);
    assert.equal(
      loaded.commands.filter((command) => command.name.startsWith("skill:karta-")).length,
      10,
    );
    await runPi(["remove", link], { cwd, agentDir });
    const listed = await runPi(["list"], { cwd, agentDir });
    assert.equal(listed.stdout.includes("Karta π package"), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("pinned Git install updates, rolls back, uninstalls, and rejects a duplicate root", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-pi-git-install-"));
  const agentDir = join(root, "agent");
  const cwd = join(root, "consumer");
  await mkdir(agentDir, { recursive: true });
  await mkdir(cwd, { recursive: true });
  const { bare } = await createGitFixture(root);
  assert.equal((await stat(bare)).isDirectory(), true);
  const { server, origin } = await startGitServer(join(root, "http"));
  const v1 = `${origin}/engen/karta.git@phase1-v1`;
  const v2 = `${origin}/engen/karta.git@phase1-v2`;
  try {
    await runPi(["install", v1], { cwd, agentDir });
    let loaded = await rpcCommands(cwd, agentDir);
    assert.equal(hasKarta(loaded.commands), true);
    assert.equal(loaded.commands.some((command) => command.name === "skill:phase1-fixture"), false);

    await runPi(["install", v2], { cwd, agentDir });
    loaded = await rpcCommands(cwd, agentDir);
    assert.equal(loaded.commands.some((command) => command.name === "skill:phase1-fixture"), true);

    await runPi(["install", v1], { cwd, agentDir });
    loaded = await rpcCommands(cwd, agentDir);
    assert.equal(loaded.commands.some((command) => command.name === "skill:phase1-fixture"), false);

    await runPi(["install", ROOT], { cwd, agentDir });
    await assert.rejects(() => rpcCommands(cwd, agentDir), /two package roots/);

    await runPi(["remove", ROOT], { cwd, agentDir });
    await runPi(["remove", v1], { cwd, agentDir });
    const listed = await runPi(["list"], { cwd, agentDir });
    assert.equal(listed.stdout.includes("karta.git"), false);
  } finally {
    await closeServer(server);
    await rm(root, { recursive: true, force: true });
  }
});
