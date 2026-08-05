import { createHash } from "node:crypto";
import { execFile, spawn } from "node:child_process";
import { mkdir, mkdtemp, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { promisify } from "node:util";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { verifyEvidenceIntegrity, type KartaEvidenceManifest } from "./evidence.ts";

const exec = promisify(execFile);
const MAX_OUTPUT_BYTES = 64 * 1024;
const MAX_COMMAND_LENGTH = 16 * 1024;
const DEFAULT_TIMEOUT = 10 * 60 * 1000;

export type OracleRunStatus =
  | "passed"
  | "failed"
  | "timed-out"
  | "aborted"
  | "not-configured"
  | "not-applicable";

export interface OracleRunResult {
  evidenceHash: string;
  commandHash?: string;
  status: OracleRunStatus;
  oracleType?: string;
  code: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  stdoutTruncated: boolean;
  stderrTruncated: boolean;
  durationMs: number;
  cached: boolean;
  reason?: string;
}

export interface OracleRunnerOptions {
  timeout?: number;
  onProcessStart?: (pid: number) => void;
}

interface BoundedOutput {
  text: string;
  truncated: boolean;
}

function appendBounded(output: BoundedOutput, chunk: Buffer | string): void {
  const currentBytes = Buffer.byteLength(output.text);
  const bytes = Buffer.from(chunk);
  if (currentBytes >= MAX_OUTPUT_BYTES) {
    output.truncated = true;
    return;
  }
  const remaining = MAX_OUTPUT_BYTES - currentBytes;
  output.text += bytes.subarray(0, remaining).toString("utf8");
  if (bytes.byteLength > remaining) output.truncated = true;
}

function hash(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function cleanEnvironment(overrides: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const environment = { ...process.env, ...overrides };
  for (const key of ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"]) {
    delete environment[key];
  }
  environment.CI = environment.CI ?? "1";
  return environment;
}

async function git(
  cwd: string,
  args: string[],
  env?: NodeJS.ProcessEnv,
  signal?: AbortSignal,
): Promise<void> {
  try {
    await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      env: { ...cleanEnvironment(), ...env },
      signal,
      timeout: 60_000,
      maxBuffer: 2 * 1024 * 1024,
    });
  } catch (error) {
    const output = `${(error as { stdout?: string }).stdout ?? ""}${(error as { stderr?: string }).stderr ?? ""}`.trim();
    throw new Error(output || `git ${args[0] ?? "command"} failed while preparing oracle snapshot`);
  }
}

function validateRelativeCwd(cwd: string): void {
  const normalized = cwd.replaceAll("\\", "/");
  if (
    !normalized ||
    isAbsolute(cwd) ||
    normalized.split("/").some((part) => part === "..")
  ) {
    throw new Error(`Oracle cwd must stay inside the item snapshot: ${cwd}`);
  }
}

function isInside(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return path === "" || (!path.startsWith("..") && !isAbsolute(path));
}

async function stopProcessTree(pid: number | undefined, force = false): Promise<void> {
  if (!pid) return;
  if (process.platform === "win32") {
    await new Promise<void>((resolveStop) => {
      const killer = spawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
        shell: false,
        stdio: "ignore",
        windowsHide: true,
      });
      killer.once("error", () => resolveStop());
      killer.once("close", () => resolveStop());
    });
    return;
  }
  try {
    process.kill(-pid, force ? "SIGKILL" : "SIGTERM");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
  }
}

async function runShellCommand(
  command: string,
  cwd: string,
  signal: AbortSignal | undefined,
  timeout: number,
  onProcessStart?: (pid: number) => void,
): Promise<Omit<OracleRunResult, "evidenceHash" | "commandHash" | "oracleType" | "cached">> {
  const startedAt = Date.now();
  const stdout: BoundedOutput = { text: "", truncated: false };
  const stderr: BoundedOutput = { text: "", truncated: false };
  const shell = process.platform === "win32" ? process.env.ComSpec ?? "cmd.exe" : "/bin/sh";
  const args = process.platform === "win32" ? ["/d", "/s", "/c", command] : ["-lc", command];
  return new Promise((resolveRun, rejectRun) => {
    let stopped: "timed-out" | "aborted" | undefined;
    let forceKill: NodeJS.Timeout | undefined;
    const child = spawn(shell, args, {
      cwd,
      env: cleanEnvironment({ GIT_CEILING_DIRECTORIES: resolve(cwd, "..") }),
      detached: process.platform !== "win32",
      shell: false,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const stop = (reason: "timed-out" | "aborted") => {
      if (stopped) return;
      stopped = reason;
      void stopProcessTree(child.pid).catch(() => undefined);
      forceKill = setTimeout(() => void stopProcessTree(child.pid, true).catch(() => undefined), 1_000);
      forceKill.unref?.();
    };
    const timer = setTimeout(() => stop("timed-out"), timeout);
    timer.unref?.();
    const abort = () => stop("aborted");
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
    if (child.pid) onProcessStart?.(child.pid);
    child.stdout.on("data", (chunk) => appendBounded(stdout, chunk));
    child.stderr.on("data", (chunk) => appendBounded(stderr, chunk));
    child.once("error", (error) => {
      clearTimeout(timer);
      if (forceKill) clearTimeout(forceKill);
      signal?.removeEventListener("abort", abort);
      rejectRun(error);
    });
    child.once("close", (code, closeSignal) => {
      void (async () => {
        clearTimeout(timer);
        if (forceKill) clearTimeout(forceKill);
        signal?.removeEventListener("abort", abort);
        if (stopped) await stopProcessTree(child.pid, true);
        resolveRun({
          status: stopped ?? (code === 0 ? "passed" : "failed"),
          code,
          signal: closeSignal,
          stdout: stdout.text,
          stderr: stderr.text,
          stdoutTruncated: stdout.truncated,
          stderrTruncated: stderr.truncated,
          durationMs: Date.now() - startedAt,
        });
      })().catch(rejectRun);
    });
  });
}

function oracleFrom(manifest: KartaEvidenceManifest): Record<string, unknown> {
  const oracle = manifest.payload.workItem.oracle;
  if (!oracle || typeof oracle !== "object" || Array.isArray(oracle)) {
    throw new Error("Karta evidence work item has no valid oracle");
  }
  return oracle as Record<string, unknown>;
}

export class AcceptanceOracleRunner {
  readonly #manifest: KartaEvidenceManifest;
  readonly #timeout: number;
  readonly #onProcessStart: ((pid: number) => void) | undefined;
  #completed: OracleRunResult | undefined;
  #pending: Promise<OracleRunResult> | undefined;

  constructor(manifest: KartaEvidenceManifest, options: OracleRunnerOptions = {}) {
    verifyEvidenceIntegrity(manifest);
    this.#manifest = manifest;
    this.#timeout = options.timeout ?? DEFAULT_TIMEOUT;
    this.#onProcessStart = options.onProcessStart;
    if (!Number.isInteger(this.#timeout) || this.#timeout <= 0) {
      throw new Error("Karta oracle timeout must be a positive integer");
    }
  }

  async run(signal?: AbortSignal): Promise<OracleRunResult> {
    if (this.#completed) return { ...this.#completed, cached: true };
    if (this.#pending) return { ...(await this.#pending), cached: true };
    const pending = this.#runOnce(signal);
    this.#pending = pending;
    try {
      const result = await pending;
      if (result.status !== "aborted" && result.status !== "timed-out") this.#completed = result;
      return result;
    } finally {
      this.#pending = undefined;
    }
  }

  async #runOnce(signal?: AbortSignal): Promise<OracleRunResult> {
    verifyEvidenceIntegrity(this.#manifest);
    const oracle = oracleFrom(this.#manifest);
    const oracleType = typeof oracle.type === "string" ? oracle.type : undefined;
    const base = {
      evidenceHash: this.#manifest.evidenceHash,
      oracleType,
      code: null,
      signal: null,
      stdout: "",
      stderr: "",
      stdoutTruncated: false,
      stderrTruncated: false,
      durationMs: 0,
      cached: false,
    } as const;
    if (oracle.opt_out === true) {
      return { ...base, status: "not-applicable", reason: "oracle is explicitly opted out" };
    }
    if (oracleType === "visual") {
      return { ...base, status: "not-applicable", reason: "visual acceptance belongs to karta-validate" };
    }
    if (oracle.command === undefined) {
      return { ...base, status: "not-configured", reason: "oracle has no command" };
    }
    if (typeof oracle.command !== "string" || !oracle.command.trim() || oracle.command.length > MAX_COMMAND_LENGTH) {
      throw new Error("Karta oracle command is invalid or too long");
    }
    const oracleCwd = oracle.cwd === undefined ? "." : oracle.cwd;
    if (typeof oracleCwd !== "string") throw new Error("Karta oracle cwd must be a string");
    validateRelativeCwd(oracleCwd);

    const temporaryRoot = await mkdtemp(join(tmpdir(), "karta-oracle-"));
    const snapshotRoot = join(temporaryRoot, "snapshot");
    const indexPath = join(temporaryRoot, "index");
    await mkdir(snapshotRoot);
    try {
      const indexEnvironment = { GIT_INDEX_FILE: indexPath };
      await git(
        this.#manifest.repositoryRoot,
        ["read-tree", "--reset", this.#manifest.payload.git.itemTip],
        indexEnvironment,
        signal,
      );
      await git(
        this.#manifest.repositoryRoot,
        ["checkout-index", "--all", "--force", `--prefix=${snapshotRoot}${sep}`],
        indexEnvironment,
        signal,
      );
      const snapshotReal = await realpath(snapshotRoot);
      const commandCwd = await realpath(resolve(snapshotRoot, oracleCwd));
      if (!isInside(snapshotReal, commandCwd)) {
        throw new Error(`Oracle cwd escapes the item snapshot: ${oracleCwd}`);
      }
      const result = await runShellCommand(
        oracle.command,
        commandCwd,
        signal,
        this.#timeout,
        this.#onProcessStart,
      );
      return {
        ...result,
        evidenceHash: this.#manifest.evidenceHash,
        commandHash: hash(oracle.command),
        oracleType,
        cached: false,
      };
    } finally {
      await rm(temporaryRoot, { recursive: true, force: true });
    }
  }
}

const oracleParameters = Type.Object({ action: Type.Literal("run") });

export function createAcceptanceOracleTool(
  manifest: KartaEvidenceManifest,
  options: OracleRunnerOptions = {},
): ToolDefinition<typeof oracleParameters, OracleRunResult> {
  const runner = new AcceptanceOracleRunner(manifest, options);
  return {
    name: "karta_oracle",
    label: "Karta acceptance oracle",
    description:
      "Run only the acceptance command fixed in this evidence manifest, inside a disposable Git snapshot. No command, path, ref, environment, or timeout can be supplied.",
    parameters: oracleParameters,
    async execute(_toolCallId, _params, signal) {
      try {
        const result = await runner.run(signal);
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          details: result,
          isError: false,
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const result: OracleRunResult = {
          evidenceHash: manifest.evidenceHash,
          status: "failed",
          code: null,
          signal: null,
          stdout: "",
          stderr: message,
          stdoutTruncated: false,
          stderrTruncated: false,
          durationMs: 0,
          cached: false,
          reason: "oracle runner failed closed",
        };
        return {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          details: result,
          isError: true,
        };
      }
    },
  };
}
