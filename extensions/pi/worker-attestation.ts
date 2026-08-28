import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { lstat, readFile, readdir, readlink, realpath } from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";
import { promisify } from "node:util";

const exec = promisify(execFile);
const MAX_OUTPUT = 16 * 1024 * 1024;
const MAX_HOOK_FILES = 256;
const MAX_HOOK_BYTES = 2 * 1024 * 1024;

export interface WorkerAuthoritySnapshot {
  schema: "karta-worker-authority-snapshot-v1";
  worktree: string;
  branch: string;
  head: string;
  index: string;
  refs: string;
  config: string;
  hooks: string;
  worktrees: string;
  protectedPaths: string;
  siblings: string;
}

export interface WorkerAuthorityAttestation {
  schema: "karta-worker-authority-attestation-v1";
  passed: boolean;
  issues: string[];
  before: WorkerAuthoritySnapshot;
  after: WorkerAuthoritySnapshot;
}

function hash(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    });
    return stdout;
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during worker attestation`);
  }
}

async function gitOptional(cwd: string, args: string[]): Promise<string> {
  try {
    return await git(cwd, args);
  } catch (error) {
    const result = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    }).catch((failure) => failure as { code?: number; stderr?: string });
    if ((result as { code?: number }).code === 1) return "";
    throw error;
  }
}

async function worktreeConfigIdentity(worktree: string): Promise<string> {
  const enabled = (await gitOptional(worktree, [
    "config",
    "--local",
    "--bool",
    "--get",
    "extensions.worktreeConfig",
  ])).trim();
  if (!enabled || enabled === "false") return "worktree-config-disabled";
  if (enabled !== "true") throw new Error("Git returned an invalid extensions.worktreeConfig value");
  return git(worktree, ["config", "--worktree", "--null", "--list", "--show-origin"]);
}

async function hooksRoot(worktree: string): Promise<string> {
  const configured = (await gitOptional(worktree, ["config", "--path", "--get", "core.hooksPath"])).trim();
  if (configured) return isAbsolute(configured) ? configured : resolve(worktree, configured);
  const common = (await git(worktree, ["rev-parse", "--git-common-dir"])).trim();
  return resolve(worktree, common, "hooks");
}

async function hashHookTree(root: string): Promise<string> {
  const records: string[] = [];
  let files = 0;
  let bytes = 0;
  async function visit(directory: string): Promise<void> {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const path = join(directory, entry.name);
      const repoPath = relative(root, path).replaceAll("\\", "/");
      const stat = await lstat(path);
      if (entry.isDirectory()) {
        records.push(`d\0${repoPath}\0${stat.mode.toString(8)}`);
        await visit(path);
      } else if (entry.isSymbolicLink()) {
        records.push(`l\0${repoPath}\0${await readlink(path)}`);
      } else if (entry.isFile()) {
        files += 1;
        bytes += stat.size;
        if (files > MAX_HOOK_FILES || bytes > MAX_HOOK_BYTES) {
          throw new Error("Karta hook identity exceeds the attestation bound");
        }
        records.push(`f\0${repoPath}\0${stat.mode.toString(8)}\0${hash(await readFile(path))}`);
      } else {
        records.push(`o\0${repoPath}\0${stat.mode.toString(8)}`);
      }
    }
  }
  await visit(root);
  return hash(records.join("\n"));
}

function worktreePaths(porcelain: string): string[] {
  return porcelain
    .split("\n")
    .filter((line) => line.startsWith("worktree "))
    .map((line) => line.slice("worktree ".length));
}

async function siblingIdentity(
  worktree: string,
  registry: string,
  binder?: string,
  waveMates?: readonly string[],
): Promise<string> {
  const current = await realpath(worktree);
  // Only the item worktrees dispatched together in THIS wave legitimately churn
  // while this worker builds, so exactly those are exempted from the volatile
  // status hash (HEAD and branch are still kept). Every other sibling — foreign
  // worktrees, other/prior-wave item worktrees, stale leftovers — keeps full
  // status checking, so a worker reaching outside its concurrent batch is caught.
  const waveMateBranches = binder && waveMates && waveMates.length > 0
    ? new Set(waveMates.map((id) => `karta/${binder}/item-${id}`))
    : undefined;
  const records: string[] = [];
  for (const path of worktreePaths(registry).sort()) {
    let physical: string;
    try {
      physical = await realpath(path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        records.push(`${path}\0missing`);
        continue;
      }
      throw error;
    }
    if (physical === current) continue;
    const [head, branch, status] = await Promise.all([
      git(path, ["rev-parse", "HEAD"]),
      git(path, ["branch", "--show-current"]),
      git(path, ["status", "--porcelain=v2", "-z", "--untracked-files=all"]),
    ]);
    // A concurrent wave-mate of the active binder legitimately churns its own
    // working tree while it builds, and the worker contract forbids it from moving
    // HEAD or the branch (the host commits only after the wave barrier). Its
    // volatile status is therefore excluded so a sibling building in parallel is
    // not mistaken for a protected-surface violation, while HEAD/branch tampering
    // stays caught. Every foreign worktree keeps full status checking.
    const isWaveMate = waveMateBranches?.has(branch.trim()) ?? false;
    records.push(
      isWaveMate
        ? `${physical}\0${head.trim()}\0${branch.trim()}\0wave-mate`
        : `${physical}\0${head.trim()}\0${branch.trim()}\0${hash(status)}`,
    );
  }
  return hash(records.join("\n"));
}

export async function snapshotWorkerAuthority(
  worktree: string,
  binder?: string,
  waveMates?: readonly string[],
): Promise<WorkerAuthoritySnapshot> {
  const physical = await realpath(worktree);
  const [branch, head, index, refs, localConfig, worktreeConfig, registry, protectedPaths, hooks] =
    await Promise.all([
      git(physical, ["branch", "--show-current"]),
      git(physical, ["rev-parse", "HEAD"]),
      git(physical, ["ls-files", "--stage", "-v", "-z"]),
      git(physical, [
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(objecttype)",
        "refs/heads/karta",
        "refs/karta",
        "refs/tags",
      ]),
      git(physical, ["config", "--local", "--null", "--list", "--show-origin"]),
      worktreeConfigIdentity(physical),
      git(physical, ["worktree", "list", "--porcelain"]),
      git(physical, [
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--",
        ".karta",
      ]),
      hooksRoot(physical).then(hashHookTree),
    ]);
  return {
    schema: "karta-worker-authority-snapshot-v1",
    worktree: physical,
    branch: branch.trim(),
    head: head.trim(),
    index: hash(index),
    refs: hash(refs),
    config: hash(`${localConfig}\0${worktreeConfig}`),
    hooks,
    worktrees: hash(registry),
    protectedPaths: hash(protectedPaths),
    siblings: await siblingIdentity(physical, registry, binder, waveMates),
  };
}

export function attestWorkerAuthority(
  before: WorkerAuthoritySnapshot,
  after: WorkerAuthoritySnapshot,
): WorkerAuthorityAttestation {
  const issues: string[] = [];
  const fields: Array<keyof WorkerAuthoritySnapshot> = [
    "worktree",
    "branch",
    "head",
    "index",
    "refs",
    "config",
    "hooks",
    "worktrees",
    "protectedPaths",
    "siblings",
  ];
  for (const field of fields) {
    if (before[field] !== after[field]) issues.push(`worker changed protected authority surface: ${field}`);
  }
  return {
    schema: "karta-worker-authority-attestation-v1",
    passed: issues.length === 0,
    issues,
    before,
    after,
  };
}
