import { execFile } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);
const MAX_HOOK_OUTPUT = 2 * 1024 * 1024;

export interface HookValidationResult {
  status: "passed" | "failed" | "drifted";
  candidateTree: string;
  hookTree?: string;
  message?: string;
  stdout: string;
  stderr: string;
}

async function git(
  cwd: string,
  args: string[],
  options: { signal?: AbortSignal; allowFailure?: boolean } = {},
): Promise<{ stdout: string; stderr: string; code: number }> {
  try {
    const { stdout, stderr } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_HOOK_OUTPUT,
      signal: options.signal,
    });
    return { stdout, stderr, code: 0 };
  } catch (error) {
    const value = error as { stdout?: string; stderr?: string; code?: number | string };
    if (options.allowFailure && typeof value.code === "number") {
      return { stdout: value.stdout ?? "", stderr: value.stderr ?? "", code: value.code };
    }
    const stderr = value.stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during hook validation`);
  }
}

export async function validateCandidateHooks(options: {
  worktree: string;
  candidateTree: string;
  parent: string;
  message: string;
  signal?: AbortSignal;
}): Promise<HookValidationResult> {
  if (!/^[a-f0-9]{40,64}$/.test(options.candidateTree) || !/^[a-f0-9]{40,64}$/.test(options.parent)) {
    throw new Error("Karta hook validation requires valid candidate and parent object ids");
  }
  const root = await mkdtemp(join(tmpdir(), "karta-hook-validation-"));
  const disposable = join(root, "worktree");
  let registered = false;
  try {
    await git(options.worktree, [
      "worktree",
      "add",
      "--detach",
      "--no-checkout",
      disposable,
      options.parent,
    ]);
    registered = true;
    await git(disposable, ["read-tree", "--reset", "-u", options.candidateTree]);
    const committed = await git(
      disposable,
      ["commit", "--no-gpg-sign", "-m", options.message],
      { signal: options.signal, allowFailure: true },
    );
    if (committed.code !== 0) {
      return {
        status: "failed",
        candidateTree: options.candidateTree,
        stdout: committed.stdout,
        stderr: committed.stderr,
      };
    }
    const hookTree = (await git(disposable, ["rev-parse", "HEAD^{tree}"])).stdout.trim();
    const message = (await git(disposable, ["log", "-1", "--format=%B"])).stdout.trimEnd();
    const dirty = (await git(disposable, ["status", "--porcelain=v1", "-z"])).stdout;
    if (hookTree !== options.candidateTree || dirty.length > 0) {
      return {
        status: "drifted",
        candidateTree: options.candidateTree,
        hookTree,
        message,
        stdout: committed.stdout,
        stderr: committed.stderr,
      };
    }
    return {
      status: "passed",
      candidateTree: options.candidateTree,
      hookTree,
      message,
      stdout: committed.stdout,
      stderr: committed.stderr,
    };
  } finally {
    if (registered) {
      await git(options.worktree, ["worktree", "remove", "--force", disposable], {
        allowFailure: true,
      }).catch(() => undefined);
    }
    await rm(root, { recursive: true, force: true });
  }
}
