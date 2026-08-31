import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { type TestContext } from "node:test";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import type { VisualEnvConfig } from "../../extensions/pi/environment.ts";
import {
  ENV_SERVER_GRACE_MS,
  EnvServerStartupError,
  startEnvServer,
  withEnvServer,
  type EnvServerContext,
  type StartEnvServerOptions,
} from "../../extensions/pi/env-server-runner.ts";

// Every fixture is a tiny app the runner spawns exactly as it spawns a real dev server:
// a shell command that reads the injected port from APP_PORT and serves (or crashes).
const FIXTURES: Record<string, string> = {
  // Serves the target route (/ready) with a 2xx and everything else with a 503, so a
  // poll of / would look unhealthy while the real route is healthy.
  "healthy.mjs": [
    'import http from "node:http";',
    "const port = Number(process.env.APP_PORT);",
    "const server = http.createServer((req, res) => {",
    '  if (req.url === "/ready") { res.writeHead(200); res.end("ready"); }',
    '  else { res.writeHead(503); res.end("starting"); }',
    "});",
    'server.listen(port, "127.0.0.1", () => process.stdout.write("listening\\n"));',
    'process.on("SIGTERM", () => process.exit(0));',
    "",
  ].join("\n"),
  // Exits before it ever listens.
  "crash.mjs": ['process.stderr.write("boom: fatal during startup\\n");', "process.exit(7);", ""].join("\n"),
  // Tries to bind the injected port; when it is already taken the bind fails and the
  // EADDRINUSE code lands on stderr before a non-zero exit.
  "bindfail.mjs": [
    'import http from "node:http";',
    "const port = Number(process.env.APP_PORT);",
    'const server = http.createServer((_req, res) => res.end("ok"));',
    'server.once("error", (err) => {',
    '  process.stderr.write(String(err && err.code ? err.code : err) + "\\n");',
    "  process.exit(1);",
    "});",
    'server.listen(port, "127.0.0.1", () => process.stdout.write("listening\\n"));',
    'process.on("SIGTERM", () => process.exit(0));',
    "",
  ].join("\n"),
  // 3xx-redirects the target route to an auth wall.
  "auth.mjs": [
    'import http from "node:http";',
    "const port = Number(process.env.APP_PORT);",
    "http.createServer((req, res) => {",
    '  if (req.url.startsWith("/dashboard")) { res.writeHead(302, { location: "/login?next=/dashboard" }); res.end(); }',
    '  else { res.writeHead(200); res.end("ok"); }',
    '}).listen(port, "127.0.0.1", () => process.stdout.write("listening\\n"));',
    'process.on("SIGTERM", () => process.exit(0));',
    "",
  ].join("\n"),
  // Never binds the port (a foreign responder holds it); exits before health confirms.
  "squatter-child.mjs": [
    "setTimeout(() => process.exit(0), 200);",
    'process.on("SIGTERM", () => process.exit(0));',
    "",
  ].join("\n"),
  // Binds and stays up but answers the route 503 forever, so the poll never resolves
  // healthy on its own.
  "never-healthy.mjs": [
    'import http from "node:http";',
    "const port = Number(process.env.APP_PORT);",
    'http.createServer((_req, res) => { res.writeHead(503); res.end("nope"); })',
    '  .listen(port, "127.0.0.1", () => process.stdout.write("listening\\n"));',
    'process.on("SIGTERM", () => process.exit(0));',
    "",
  ].join("\n"),
  // Healthy, but traps and ignores SIGTERM so only a SIGKILL escalation reclaims it.
  "sigterm-ignore.mjs": [
    'import http from "node:http";',
    "const port = Number(process.env.APP_PORT);",
    'http.createServer((_req, res) => { res.writeHead(200); res.end("ok"); })',
    '  .listen(port, "127.0.0.1", () => process.stdout.write("listening\\n"));',
    'process.on("SIGTERM", () => {});',
    "",
  ].join("\n"),
  // A foreign process (its own group) that holds the port passed as argv[2] but never
  // answers the target route with a 2xx, so the only health signal is the spawned
  // child's own bind failure.
  "foreign-holder.mjs": [
    'import http from "node:http";',
    "const port = Number(process.argv[2]);",
    'http.createServer((_req, res) => { res.writeHead(503); res.end("foreign"); })',
    '  .listen(port, "127.0.0.1", () => process.stdout.write("up\\n"));',
    'process.on("SIGTERM", () => {});',
    "",
  ].join("\n"),
  // A foreign responder that warms up: 503 for its first 500ms, 200 thereafter. Lets a
  // test guarantee the 2xx only becomes available after the spawned child has exited.
  "foreign-timed.mjs": [
    'import http from "node:http";',
    "const port = Number(process.argv[2]);",
    "const started = Date.now();",
    "http.createServer((_req, res) => {",
    '  if (Date.now() - started < 500) { res.writeHead(503); res.end("warming"); }',
    '  else { res.writeHead(200); res.end("squatter"); }',
    "})",
    '  .listen(port, "127.0.0.1", () => process.stdout.write("up\\n"));',
    'process.on("SIGTERM", () => {});',
    "",
  ].join("\n"),
};

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function nodeCommand(script: string): string {
  return `${shellQuote(process.execPath)} ${shellQuote(script)}`;
}

function makeConfig(command: string, overrides: Partial<VisualEnvConfig> = {}): VisualEnvConfig {
  return { command, portParam: "APP_PORT", startupTimeoutSeconds: 20, auth: "none", ...overrides };
}

interface Harness {
  lifecycles: LifecycleRegistry;
  manager: KartaProcessManager;
  context: EnvServerContext;
  spawnedPid(): number | undefined;
}

function makeHarness(): Harness {
  const lifecycles = new LifecycleRegistry();
  let spawnedPid: number | undefined;
  const manager = new KartaProcessManager(lifecycles, 1_000, (name, details) => {
    if (name === "process-created" && details.pid) spawnedPid = details.pid;
  });
  const owner = manager.createBinderOwner(process.cwd(), "demo");
  return { lifecycles, manager, context: { manager, owner }, spawnedPid: () => spawnedPid };
}

async function fixtures(): Promise<{ dir: string; script(name: string): string; cleanup(): Promise<void> }> {
  const dir = await mkdtemp(join(tmpdir(), "karta-env-server-"));
  await Promise.all(Object.entries(FIXTURES).map(([name, body]) => writeFile(join(dir, name), body)));
  return {
    dir,
    script: (name: string) => join(dir, name),
    cleanup: () => rm(dir, { recursive: true, force: true }),
  };
}

function processExists(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}

// A freshly SIGKILLed child lingers as a zombie until its parent (the runner) reaps it
// on the next event-loop turn, so a point-in-time processExists can still see it. Poll
// until the group is genuinely reclaimed.
async function waitGone(pid: number, timeoutMs = 3_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processExists(pid)) return true;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return !processExists(pid);
}

function reserveRealPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const address = srv.address();
      const port = address && typeof address === "object" ? address.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

function isPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.listen(port, "127.0.0.1", () => srv.close(() => resolve(true)));
  });
}

function get(url: string): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(url, { method: "GET" }, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, body }));
    });
    req.once("error", reject);
    req.end();
  });
}

function spawnForeign(script: string, port: number): Promise<{ pid: number; kill(): void }> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, String(port)], {
      detached: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    child.once("error", reject);
    child.stdout!.once("data", () => {
      resolve({
        pid: child.pid!,
        kill: () => {
          try {
            process.kill(-child.pid!, "SIGKILL");
          } catch {
            // already gone
          }
        },
      });
    });
  });
}

const skipOnWindows = (context: TestContext): boolean => {
  if (process.platform === "win32") {
    context.skip("POSIX process-group lifecycle has a native Windows fixture");
    return true;
  }
  return false;
};

test("ENV_SERVER_GRACE_MS is the explicit 5s dev-server teardown grace", () => {
  assert.equal(ENV_SERVER_GRACE_MS, 5_000);
});

test("a declared visual_env health-polls the target route to a 2xx and names the assigned URL", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  let reserved = 0;
  const reservePort = async (): Promise<number> => {
    reserved = await reserveRealPort();
    return reserved;
  };
  try {
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("healthy.mjs"))),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      reservePort,
      pollIntervalMs: 50,
    });
    assert.equal(outcome.status, "healthy");
    if (outcome.status !== "healthy") return;
    assert.equal(outcome.handle.url, `http://127.0.0.1:${reserved}/ready`);
    assert.equal(outcome.handle.port, reserved);
    assert.equal(outcome.url, `http://127.0.0.1:${reserved}/ready`);
    // The route answers differently from / — health keys on the real route, not the root.
    assert.equal((await get(`http://127.0.0.1:${reserved}/`)).status, 503);
    assert.equal((await get(outcome.handle.url)).status, 200);
    assert.equal(harness.manager.size, 1);
    await outcome.handle.stop();
    assert.equal(harness.manager.size, 0);
    assert.equal(await waitGone(outcome.handle.pid), true);
    assert.equal(await isPortFree(reserved), true);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("a child that crashes before health returns a typed startup-crash without burning the timeout", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  try {
    const startedAt = Date.now();
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("crash.mjs")), { startupTimeoutSeconds: 20 }),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      pollIntervalMs: 50,
    });
    const elapsed = Date.now() - startedAt;
    assert.equal(outcome.status, "startup-crash");
    if (outcome.status !== "startup-crash") return;
    assert.equal(outcome.exitCode, 7);
    assert.match(outcome.stderr, /boom/);
    assert.match(outcome.tail, /boom/);
    assert.match(outcome.remediation, /exited during startup/);
    // Aborted immediately on exit, nowhere near the 20s startup timeout.
    assert.ok(elapsed < 5_000, `crash detection took ${elapsed}ms`);
    assert.equal(harness.manager.size, 0);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("an occupied reserved port surfaces as the child's bind failure — a startup-crash carrying EADDRINUSE, never a timeout", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const port = await reserveRealPort();
  const foreign = await spawnForeign(fx.script("foreign-holder.mjs"), port);
  try {
    const startedAt = Date.now();
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("bindfail.mjs")), { startupTimeoutSeconds: 20 }),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      reservePort: async () => port,
      pollIntervalMs: 50,
    });
    const elapsed = Date.now() - startedAt;
    assert.equal(outcome.status, "startup-crash");
    if (outcome.status !== "startup-crash") return;
    assert.match(outcome.tail, /EADDRINUSE/);
    assert.ok(elapsed < 5_000, `bind failure surfaced in ${elapsed}ms — must not burn the timeout`);
    assert.equal(harness.manager.size, 0);
  } finally {
    foreign.kill();
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("a port held by a foreign process this run did not start is never killed", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const port = await reserveRealPort();
  const foreign = await spawnForeign(fx.script("foreign-holder.mjs"), port);
  try {
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("bindfail.mjs")), { startupTimeoutSeconds: 20 }),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      reservePort: async () => port,
      pollIntervalMs: 50,
    });
    assert.equal(outcome.status, "startup-crash");
    // The lifecycle bailed on its own dead child; the foreign holder of the port survives.
    assert.equal(processExists(foreign.pid), true);
    assert.equal(harness.manager.size, 0);
  } finally {
    foreign.kill();
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("a target route that 3xx-redirects to /login is classified auth-required", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  try {
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("auth.mjs"))),
      worktree: fx.dir,
      route: "/dashboard",
      context: harness.context,
      pollIntervalMs: 50,
    });
    assert.equal(outcome.status, "auth-required");
    if (outcome.status !== "auth-required") return;
    assert.match(outcome.location, /\/login/);
    assert.match(outcome.remediation, /auth wall/);
    assert.equal(harness.manager.size, 0);
    assert.equal(await waitGone(harness.spawnedPid()!), true);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("a 2xx from a foreign responder after the child exits is not reported healthy", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const port = await reserveRealPort();
  // The foreign responder only answers 2xx after 500ms; the spawned child exits at 200ms.
  const foreign = await spawnForeign(fx.script("foreign-timed.mjs"), port);
  try {
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("squatter-child.mjs")), { startupTimeoutSeconds: 20 }),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      reservePort: async () => port,
      pollIntervalMs: 50,
    });
    // The child is gone, so the squatter's later 2xx is caught by the exit watcher.
    assert.equal(outcome.status, "startup-crash");
    assert.equal(processExists(foreign.pid), true);
    assert.equal(harness.manager.size, 0);
  } finally {
    foreign.kill();
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("teardown frees the port and reaps the group when ctx.signal aborts during health-poll", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const controller = new AbortController();
  let boundPort = 0;
  try {
    const timer = setTimeout(() => controller.abort(), 300);
    timer.unref?.();
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("never-healthy.mjs")), { startupTimeoutSeconds: 20 }),
      worktree: fx.dir,
      route: "/ready",
      context: harness.context,
      signal: controller.signal,
      reservePort: async () => {
        boundPort = await reserveRealPort();
        return boundPort;
      },
      pollIntervalMs: 50,
    });
    assert.equal(outcome.status, "aborted");
    assert.equal(harness.manager.size, 0);
    assert.equal(await waitGone(harness.spawnedPid()!), true);
    assert.equal(await isPortFree(boundPort), true);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("a child that ignores SIGTERM is SIGKILLed after the grace, leaving no survivor", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  let reserved = 0;
  try {
    const outcome = await startEnvServer({
      config: makeConfig(nodeCommand(fx.script("sigterm-ignore.mjs"))),
      worktree: fx.dir,
      route: "/",
      context: harness.context,
      reservePort: async () => {
        reserved = await reserveRealPort();
        return reserved;
      },
      graceMs: 300,
      pollIntervalMs: 50,
    });
    assert.equal(outcome.status, "healthy");
    if (outcome.status !== "healthy") return;
    const pid = outcome.handle.pid;
    assert.equal(processExists(pid), true);
    const stopStarted = Date.now();
    await outcome.handle.stop();
    const graceUsed = Date.now() - stopStarted;
    // SIGTERM was ignored, so the process only died once the grace elapsed and SIGKILL ran.
    assert.ok(graceUsed >= 250, `teardown returned in ${graceUsed}ms — grace not honored`);
    assert.equal(await waitGone(pid), true);
    assert.equal(harness.manager.size, 0);
    assert.equal(await isPortFree(reserved), true);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("withEnvServer hands an owned handle to the callback and tears it down afterward", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const options: StartEnvServerOptions = {
    config: makeConfig(nodeCommand(fx.script("healthy.mjs"))),
    worktree: fx.dir,
    route: "/ready",
    context: harness.context,
    pollIntervalMs: 50,
  };
  try {
    let seenPid = 0;
    const body = await withEnvServer(options, async (handle) => {
      seenPid = handle.pid;
      assert.equal(harness.manager.size, 1);
      assert.equal((await get(handle.url)).status, 200);
      return "done";
    });
    assert.equal(body, "done");
    // Completion is an exit path: the group is reaped after the callback returns.
    assert.equal(harness.manager.size, 0);
    assert.equal(await waitGone(seenPid), true);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});

test("withEnvServer throws a typed startup error carrying the crash outcome when the server never comes up", async (context) => {
  if (skipOnWindows(context)) return;
  const fx = await fixtures();
  const harness = makeHarness();
  const options: StartEnvServerOptions = {
    config: makeConfig(nodeCommand(fx.script("crash.mjs")), { startupTimeoutSeconds: 20 }),
    worktree: fx.dir,
    route: "/ready",
    context: harness.context,
    pollIntervalMs: 50,
  };
  try {
    let called = false;
    await assert.rejects(
      withEnvServer(options, async () => {
        called = true;
      }),
      (error: unknown) => {
        assert.ok(error instanceof EnvServerStartupError);
        assert.equal(error.outcome.status, "startup-crash");
        return true;
      },
    );
    assert.equal(called, false);
    assert.equal(harness.manager.size, 0);
  } finally {
    await harness.manager.stopOwner(harness.context.owner);
    await fx.cleanup();
  }
});
