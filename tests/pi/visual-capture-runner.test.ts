import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test, { type TestContext } from "node:test";
import { promisify } from "node:util";
import { LifecycleRegistry } from "../../extensions/pi/lifecycle-registry.ts";
import { KartaProcessManager } from "../../extensions/pi/process-manager.ts";
import type {
  EnvServerContext,
  EnvServerOutcome,
  StartEnvServerOptions,
} from "../../extensions/pi/env-server-runner.ts";
import {
  captureVisualEvidence,
  PLAYWRIGHT_CLI_MISSING_REMEDIATION,
  PlaywrightCliUnavailableError,
  realCaptureView,
  resolvePlaywrightCli,
  VISUAL_EVIDENCE_SCHEMA,
  type CaptureArtifact,
  type DesignServerHandle,
  type VisualCaptureBoundary,
} from "../../extensions/pi/visual-capture-runner.ts";

const exec = promisify(execFile);
const FIXTURES = new URL("./fixtures/visual-captures/", import.meta.url);

async function git(cwd: string, args: string[]): Promise<string> {
  return (await exec("git", ["-C", cwd, ...args], { encoding: "utf8" })).stdout.trim();
}

interface Harness {
  manager: KartaProcessManager;
  context: EnvServerContext;
  cleanup(): Promise<void>;
}

function makeHarness(): Harness {
  const lifecycles = new LifecycleRegistry();
  const manager = new KartaProcessManager(lifecycles);
  const owner = manager.createBinderOwner(process.cwd(), "demo");
  return { manager, context: { manager, owner }, cleanup: () => manager.stopOwner(owner) };
}

const VISUAL_ENV = (command: string): string =>
  JSON.stringify(
    { visual_env: { command, port_param: "APP_PORT", startup_timeout_seconds: 20, auth: "none" } },
    null,
    2,
  );

interface Repo {
  repo: string;
  candidateCommit: string;
  candidateTree: string;
  scratch: string;
  cleanup(): Promise<void>;
}

// A git repo whose candidate commit declares a well-formed visual_env, plus a scratch
// directory that lives OUTSIDE the worktree (a sibling), where every capture artifact is
// written. `candidateTree` is the pinned commit's tree the evidence must bind to.
async function makeRepo(command = "node app.mjs"): Promise<Repo> {
  const root = await mkdtemp(join(tmpdir(), "karta-visual-capture-test-"));
  const repo = join(root, "repo");
  const scratch = join(root, "scratch");
  await exec("mkdir", ["-p", join(repo, ".karta")]);
  await exec("mkdir", ["-p", scratch]);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Visual"]);
  await git(repo, ["config", "user.email", "visual@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await writeFile(join(repo, ".karta", "environment.json"), VISUAL_ENV(command));
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "candidate"]);
  const candidateCommit = await git(repo, ["rev-parse", "HEAD"]);
  const candidateTree = await git(repo, ["rev-parse", "HEAD^{tree}"]);
  return {
    repo,
    candidateCommit,
    candidateTree,
    scratch,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}

async function fixtureArtifact(name: string): Promise<CaptureArtifact> {
  const raw = await exec("cat", [new URL(`${name}.json`, FIXTURES).pathname], { encoding: "utf8" });
  return JSON.parse(raw.stdout).capture as CaptureArtifact;
}

function fakeHealthyStart(
  url: string,
  onStart?: (options: StartEnvServerOptions) => void,
): VisualCaptureBoundary["startEnvServer"] {
  return async (options): Promise<EnvServerOutcome> => {
    onStart?.(options);
    const handle = { url, port: 4321, pid: 424242, stop: async () => {} };
    return { status: "healthy", handle, url, port: 4321, pid: 424242 };
  };
}

const fakeServeDesign = (url = "http://127.0.0.1:9/"): VisualCaptureBoundary["serveDesign"] =>
  async (): Promise<DesignServerHandle> => ({ url, stop: async () => {} });

function isPortFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.listen(port, "127.0.0.1", () => srv.close(() => resolve(true)));
  });
}

// -------------------------------------------------------------------- the real diff

test("real diffCapture over fake captures emits one hash-bound karta-visual-evidence-v1 artifact — identical is clean, a positive control shows the exact discrepancy by content", async () => {
  const repo = await makeRepo();
  const harness = makeHarness();
  try {
    // Identical capture: the REAL structured diff finds nothing.
    const clean = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
        serveDesign: fakeServeDesign(),
        captureView: async () => fixtureArtifact("diff-identical"),
        // diffCapture defaults to the REAL structured diff (uv, no browser).
      },
    });
    assert.equal(clean.status, "captured");
    if (clean.status !== "captured") return;
    assert.equal(clean.evidence.schema, VISUAL_EVIDENCE_SCHEMA);
    assert.equal(clean.evidence.structuredDiff.schema, "karta-structured-diff-v1");
    assert.equal(clean.evidence.structuredDiff.summary.discrepancyCount, 0);
    assert.equal(clean.evidence.renderHealth.design.result, "healthy");
    assert.equal(clean.evidence.renderHealth.app.result, "healthy");
    // Bound to the candidate tree hash, and written OUTSIDE the worktree.
    assert.equal(clean.candidateTree, repo.candidateTree);
    assert.equal(clean.evidence.candidateTree, repo.candidateTree);
    assert.equal(dirname(clean.evidencePath), repo.scratch);
    assert.equal(await git(repo.repo, ["status", "--porcelain"]), "");

    // Positive control: a known injected computed-style discrepancy appears by content.
    const control = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
        serveDesign: fakeServeDesign(),
        captureView: async () => fixtureArtifact("diff-computed-style"),
      },
    });
    assert.equal(control.status, "captured");
    if (control.status !== "captured") return;
    const discrepancies = control.evidence.structuredDiff.discrepancies;
    assert.equal(control.evidence.structuredDiff.summary.discrepancyCount, 2);
    const color = discrepancies.find((d) => d.property === "color");
    assert.ok(color, "expected a color discrepancy by content");
    assert.equal(color?.design, "rgb(17, 24, 39)");
    assert.equal(color?.app, "rgb(220, 38, 38)");
    const fontSize = discrepancies.find((d) => d.property === "fontSize");
    assert.ok(fontSize, "expected a fontSize discrepancy by content");
    assert.equal(fontSize?.design, "30px");
    assert.equal(fontSize?.app, "24px");
  } finally {
    await harness.cleanup();
    await repo.cleanup();
  }
});

// -------------------------------------------------------- playwright-cli host gate

test("playwright-cli absent on PATH fails closed with the install remediation, present resolves; the floor's fake path needs no browser", async () => {
  assert.throws(
    () => resolvePlaywrightCli({ path: "" }),
    (error: unknown) =>
      error instanceof PlaywrightCliUnavailableError &&
      error.message === PLAYWRIGHT_CLI_MISSING_REMEDIATION &&
      /npm install -g @playwright\/cli/.test(error.message),
  );
  assert.throws(() => resolvePlaywrightCli({ lookup: () => undefined }), PlaywrightCliUnavailableError);

  const dir = await mkdtemp(join(tmpdir(), "karta-playwright-bin-"));
  try {
    const bin = join(dir, "playwright-cli");
    await writeFile(bin, "#!/bin/sh\nexit 0\n");
    await chmod(bin, 0o755);
    assert.equal(resolvePlaywrightCli({ path: dir }), bin);
    assert.equal(
      resolvePlaywrightCli({ lookup: () => "/somewhere/playwright-cli" }),
      "/somewhere/playwright-cli",
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }

  // The real captureView hard-gates before any capture: when playwright-cli is absent
  // from the host PATH it fails closed with the install remediation rather than opening a
  // browser. The fake-boundary tests above never touch this gate, so the floor needs no
  // browser (only uv, for the real diff).
  const playwrightPresent = (() => {
    try {
      resolvePlaywrightCli();
      return true;
    } catch {
      return false;
    }
  })();
  if (!playwrightPresent) {
    const scratch = await mkdtemp(join(tmpdir(), "karta-capture-scratch-"));
    try {
      await assert.rejects(
        realCaptureView({
          designUrl: "http://127.0.0.1:9/",
          appUrl: "http://127.0.0.1:9/",
          scratchDir: scratch,
        }),
        PlaywrightCliUnavailableError,
      );
    } finally {
      await rm(scratch, { recursive: true, force: true });
    }
  }
});

// ------------------------------------------------------------- render-health gate

test("an unhealthy or shell render fails the capture closed rather than diffing a blank page", async () => {
  const repo = await makeRepo();
  const harness = makeHarness();
  let diffCalled = false;
  try {
    const blocked = await fixtureArtifact("diff-identical");
    blocked.app.render_health = { ...blocked.app.render_health!, result: "blocked" };
    const outcome = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
        serveDesign: fakeServeDesign(),
        captureView: async () => blocked,
        diffCapture: async () => {
          diffCalled = true;
          throw new Error("diff must not run on a blocked render");
        },
      },
    });
    assert.equal(outcome.status, "render-unhealthy");
    if (outcome.status !== "render-unhealthy") return;
    assert.equal(outcome.target, "app");
    assert.equal(diffCalled, false);
  } finally {
    await harness.cleanup();
    await repo.cleanup();
  }
});

// ------------------------------------------------ serveDesign teardown everywhere

test("serveDesign is torn down on every exit path (completion, error, abort), leaving no server process bound", async () => {
  for (const path of ["completion", "error", "abort"] as const) {
    const repo = await makeRepo();
    const harness = makeHarness();
    const server = http.createServer((_req, res) => {
      res.writeHead(200);
      res.end("design");
    });
    const port = await new Promise<number>((resolve) =>
      server.listen(0, "127.0.0.1", () => resolve((server.address() as net.AddressInfo).port)),
    );
    let stopped = false;
    const serveDesign: VisualCaptureBoundary["serveDesign"] = async () => ({
      url: `http://127.0.0.1:${port}/`,
      stop: async () => {
        if (stopped) return;
        stopped = true;
        await new Promise<void>((resolve) => server.close(() => resolve()));
      },
    });
    const controller = new AbortController();
    const captureView: VisualCaptureBoundary["captureView"] =
      path === "completion"
        ? async () => fixtureArtifact("diff-identical")
        : path === "error"
          ? async () => {
              throw new Error("capture failed mid-flight");
            }
          : async ({ signal }) =>
              new Promise<CaptureArtifact>((_resolve, reject) => {
                signal?.addEventListener("abort", () => reject(new Error("capture aborted")), {
                  once: true,
                });
                queueMicrotask(() => controller.abort());
              });
    const run = captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      signal: controller.signal,
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
        serveDesign,
        captureView,
      },
    });
    try {
      if (path === "completion") {
        const outcome = await run;
        assert.equal(outcome.status, "captured");
      } else {
        await assert.rejects(run);
      }
      assert.equal(stopped, true, `${path}: serveDesign was torn down`);
      assert.equal(await isPortFree(port), true, `${path}: design server port freed`);
    } finally {
      if (!stopped) await new Promise<void>((resolve) => server.close(() => resolve()));
      await harness.cleanup();
      await repo.cleanup();
    }
  }
});

// ------------------------------------------------------- candidate-OID read proof

test("capture reads visual_env from the candidate commit OID, not the working-tree or integration-ref copy", async () => {
  const repo = await makeRepo("node candidate-app.mjs");
  const harness = makeHarness();
  try {
    // An integration-ref copy with a different command.
    await git(repo.repo, ["checkout", "-b", "karta/demo/integration"]);
    await writeFile(join(repo.repo, ".karta", "environment.json"), VISUAL_ENV("node integration-app.mjs"));
    await git(repo.repo, ["commit", "--no-gpg-sign", "-am", "integration copy"]);
    await git(repo.repo, ["checkout", "main"]);
    // A working-tree copy with a third command (uncommitted; the worktree is now dirty).
    await writeFile(join(repo.repo, ".karta", "environment.json"), VISUAL_ENV("node working-tree-app.mjs"));

    let usedCommand: string | undefined;
    const outcome = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/", (o) => {
          usedCommand = o.config.command;
        }),
        serveDesign: fakeServeDesign(),
        captureView: async () => fixtureArtifact("diff-identical"),
      },
    });
    assert.equal(usedCommand, "node candidate-app.mjs");
    assert.equal(outcome.status, "captured");
  } finally {
    await harness.cleanup();
    await repo.cleanup();
  }
});

// --------------------------------------------------------- candidate tree re-verify

test("the capture's out-of-worktree output never trips the tree re-verify; a mid-capture tracked-file mutation fails closed", async () => {
  // (a) A normal capture writes only outside the worktree, so the tree re-verify passes
  //     and no tracked file changed.
  {
    const repo = await makeRepo();
    const harness = makeHarness();
    try {
      const outcome = await captureVisualEvidence({
        binder: "demo",
        item: "item-a",
        worktree: repo.repo,
        candidateCommit: repo.candidateCommit,
        route: "/",
        designPath: join(repo.scratch, "design"),
        context: harness.context,
        scratchDir: repo.scratch,
        boundary: {
          startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
          serveDesign: fakeServeDesign(),
          captureView: async () => fixtureArtifact("diff-identical"),
        },
      });
      assert.equal(outcome.status, "captured");
      assert.equal(await git(repo.repo, ["status", "--porcelain"]), "");
    } finally {
      await harness.cleanup();
      await repo.cleanup();
    }
  }

  // (b) A tracked file mutated mid-capture moves the tree hash, so capture fails closed.
  {
    const repo = await makeRepo();
    const harness = makeHarness();
    try {
      const outcome = await captureVisualEvidence({
        binder: "demo",
        item: "item-a",
        worktree: repo.repo,
        candidateCommit: repo.candidateCommit,
        route: "/",
        designPath: join(repo.scratch, "design"),
        context: harness.context,
        scratchDir: repo.scratch,
        boundary: {
          startEnvServer: fakeHealthyStart("http://127.0.0.1:9/"),
          serveDesign: fakeServeDesign(),
          captureView: async () => {
            await writeFile(join(repo.repo, "subject.txt"), "mutated during capture\n");
            return fixtureArtifact("diff-identical");
          },
        },
      });
      assert.equal(outcome.status, "tree-mutated");
      if (outcome.status !== "tree-mutated") return;
      assert.equal(outcome.boundTree, repo.candidateTree);
      assert.notEqual(outcome.after, outcome.before);
    } finally {
      await harness.cleanup();
      await repo.cleanup();
    }
  }
});

// ------------------------------------------------------------------------- opt-out

test("an absent visual_env at the candidate commit is opt-out: no capture, no evidence", async () => {
  const root = await mkdtemp(join(tmpdir(), "karta-visual-noenv-"));
  const repo = join(root, "repo");
  await exec("mkdir", ["-p", repo]);
  await writeFile(join(repo, "subject.txt"), "fixture\n");
  await git(repo, ["init", "--initial-branch=main"]);
  await git(repo, ["config", "user.name", "Karta Visual"]);
  await git(repo, ["config", "user.email", "visual@invalid.example"]);
  await git(repo, ["config", "commit.gpgSign", "false"]);
  await git(repo, ["add", "."]);
  await git(repo, ["commit", "--no-gpg-sign", "-m", "no env"]);
  const candidateCommit = await git(repo, ["rev-parse", "HEAD"]);
  const harness = makeHarness();
  let started = false;
  try {
    const outcome = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo,
      candidateCommit,
      route: "/",
      designPath: join(root, "design"),
      context: harness.context,
      scratchDir: join(root, "scratch"),
      boundary: {
        startEnvServer: fakeHealthyStart("http://127.0.0.1:9/", () => {
          started = true;
        }),
        serveDesign: fakeServeDesign(),
        captureView: async () => fixtureArtifact("diff-identical"),
      },
    });
    assert.equal(outcome.status, "no-visual-env");
    assert.equal(started, false);
  } finally {
    await harness.cleanup();
    await rm(root, { recursive: true, force: true });
  }
});

// -------------------------------------------------------------- env-server failure

test("a startup-crash from the env-server lifecycle is surfaced as a typed fail-closed outcome, and diff never runs", async () => {
  const repo = await makeRepo();
  const harness = makeHarness();
  let diffCalled = false;
  try {
    const outcome = await captureVisualEvidence({
      binder: "demo",
      item: "item-a",
      worktree: repo.repo,
      candidateCommit: repo.candidateCommit,
      route: "/",
      designPath: join(repo.scratch, "design"),
      context: harness.context,
      scratchDir: repo.scratch,
      boundary: {
        startEnvServer: async () => ({
          status: "startup-crash",
          exitCode: 7,
          signal: null,
          stdout: "",
          stderr: "boom",
          tail: "boom",
          remediation: "the dev server exited during startup",
        }),
        serveDesign: fakeServeDesign(),
        captureView: async () => fixtureArtifact("diff-identical"),
        diffCapture: async () => {
          diffCalled = true;
          throw new Error("diff must not run");
        },
      },
    });
    assert.equal(outcome.status, "startup-crash");
    if (outcome.status !== "startup-crash") return;
    assert.equal(outcome.exitCode, 7);
    assert.match(outcome.tail, /boom/);
    assert.equal(diffCalled, false);
  } finally {
    await harness.cleanup();
    await repo.cleanup();
  }
});

// -------------------------------------------------- opt-in live end-to-end capture

const LIVE = process.env.KARTA_LIVE_VISUAL_CAPTURE === "1";

const skipOnWindows = (context: TestContext): boolean => {
  if (process.platform === "win32") {
    context.skip("POSIX process-group lifecycle has a native Windows fixture");
    return true;
  }
  return false;
};

test(
  "live: real captureView, serveDesign, and diffCapture drive playwright-cli end to end",
  { skip: !LIVE, timeout: 5 * 60_000 },
  async (context) => {
    if (skipOnWindows(context)) return;
    const repo = await makeRepo("node app.mjs");
    const harness = makeHarness();
    try {
      // A single-port app that reads APP_PORT and serves the target route.
      await writeFile(
        join(repo.repo, "app.mjs"),
        [
          'import http from "node:http";',
          "const port = Number(process.env.APP_PORT);",
          "http.createServer((_req, res) => {",
          '  res.writeHead(200, { "content-type": "text/html" });',
          '  res.end("<!doctype html><title>App</title><main><h1 id=\\"t\\">Team dashboard</h1><button>New</button></main>");',
          '}).listen(port, "127.0.0.1", () => process.stdout.write("up\\n"));',
          'process.on("SIGTERM", () => process.exit(0));',
          "",
        ].join("\n"),
      );
      const designDir = join(repo.scratch, "design");
      await exec("mkdir", ["-p", designDir]);
      await writeFile(
        join(designDir, "view.standalone.html"),
        '<!doctype html><title>Design</title><main><h1 id="t">Team dashboard</h1><button>New</button></main>',
      );
      // The command must be committed to the candidate tree, so add app.mjs and re-pin.
      await git(repo.repo, ["add", "app.mjs"]);
      await git(repo.repo, ["commit", "--no-gpg-sign", "-m", "app"]);
      const candidateCommit = await git(repo.repo, ["rev-parse", "HEAD"]);

      const outcome = await captureVisualEvidence({
        binder: "demo",
        item: "item-a",
        worktree: repo.repo,
        candidateCommit,
        route: "/",
        designPath: designDir,
        context: harness.context,
        scratchDir: repo.scratch,
      });
      assert.equal(outcome.status, "captured", JSON.stringify(outcome));
      if (outcome.status !== "captured") return;
      assert.equal(outcome.evidence.schema, VISUAL_EVIDENCE_SCHEMA);
      assert.ok(outcome.evidence.renderHealth.design.result);
      assert.ok(outcome.evidence.renderHealth.app.result);
      assert.equal(outcome.evidence.structuredDiff.schema, "karta-structured-diff-v1");
    } finally {
      await harness.cleanup();
      await repo.cleanup();
    }
  },
);
