import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  runStableTreeChecks,
  type CheckConvergenceResult,
  type KartaCheckPlanEntry,
} from "./check-convergence.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import {
  buildKartaEvidence,
  verifyEvidenceFreshness,
  type KartaCheckManifest,
} from "./evidence.ts";
import { requirePackagePath } from "./package-paths.ts";
import type { KartaVerificationResult, KartaVerificationRunner } from "./verification-runner.ts";

const exec = promisify(execFile);
const MAX_GIT_OUTPUT = 4 * 1024 * 1024;

export type KartaBuildFinalizationStatus = "built" | "retry" | "blocked" | "no-change";

export interface KartaBuildFinalizationResult {
  status: KartaBuildFinalizationStatus;
  binder: string;
  item: string;
  targetTree?: string;
  commit?: string;
  checks?: KartaCheckManifest;
  checkFailure?: CheckConvergenceResult;
  verification?: KartaVerificationResult;
  message: string;
}

async function git(cwd: string, args: string[], allowFailure = false): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_GIT_OUTPUT,
    });
    return stdout.trim();
  } catch (error) {
    if (allowFailure) return "";
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed`);
  }
}

function oracleCheck(workItem: Record<string, unknown>): KartaCheckPlanEntry | undefined {
  const oracle = workItem.oracle;
  if (!oracle || typeof oracle !== "object" || Array.isArray(oracle)) return undefined;
  const command = (oracle as Record<string, unknown>).command;
  if (typeof command !== "string" || !command.trim()) return undefined;
  const cwd = (oracle as Record<string, unknown>).cwd;
  return {
    id: "oracle",
    purpose: "oracle",
    command: command.trim(),
    cwd: typeof cwd === "string" ? cwd : ".",
  };
}

async function nullObjectId(cwd: string): Promise<string> {
  const format = await git(cwd, ["rev-parse", "--show-object-format"]);
  if (format === "sha1") return "0".repeat(40);
  if (format === "sha256") return "0".repeat(64);
  throw new Error(`Karta does not support Git object format '${format}'`);
}

async function scanSecrets(cwd: string): Promise<void> {
  try {
    await exec(
      "uv",
      [
        "run",
        "--script",
        requirePackagePath("skills/karta-build/scripts/scan_secrets.py"),
      ],
      { cwd, encoding: "utf8", maxBuffer: MAX_GIT_OUTPUT },
    );
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    const stdout = (error as { stdout?: string }).stdout?.trim();
    throw new Error(stderr || stdout || "Karta secret scan failed");
  }
}

async function rejectProtectedCandidate(cwd: string): Promise<void> {
  const changed = await git(cwd, ["diff", "--cached", "--name-only", "-z"]);
  const paths = changed.split("\0").filter(Boolean);
  const protectedPath = paths.find(
    (path) => path === ".git" || path.startsWith(".git/") || path === ".karta" || path.startsWith(".karta/"),
  );
  if (protectedPath) {
    throw new Error(`Karta build candidate modifies protected orchestration state: ${protectedPath}`);
  }
}

function finalizationStatus(verification: KartaVerificationResult): KartaBuildFinalizationStatus {
  if (verification.status === "pass" || verification.status === "skipped") return "built";
  if (verification.status === "concerns") return "retry";
  return "blocked";
}

export class KartaBuildFinalizer {
  readonly #locks: DispatchLockManager;
  readonly #verification: KartaVerificationRunner;

  constructor(locks: DispatchLockManager, verification: KartaVerificationRunner) {
    this.#locks = locks;
    this.#verification = verification;
  }

  async finalizeCandidate(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    worktree: string,
    lease: DispatchLockLease,
    floorChecks: KartaCheckPlanEntry[] = [],
  ): Promise<KartaBuildFinalizationResult> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta build finalization requires the active binder lock lease");
    }
    await git(worktree, ["add", "-A"]);
    const stagedTree = await git(worktree, ["write-tree"]);
    const headTree = await git(worktree, ["rev-parse", "HEAD^{tree}"]);
    if (stagedTree === headTree) {
      return {
        status: "no-change",
        binder,
        item,
        targetTree: stagedTree,
        message: "Worker produced no candidate change; no completion ref was written.",
      };
    }
    await rejectProtectedCandidate(worktree);
    const preliminaryEvidence = await buildKartaEvidence({
      cwd: worktree,
      binder,
      item,
      target: "candidate",
    });
    const oracle = oracleCheck(preliminaryEvidence.payload.workItem);
    const oracleKey = oracle ? `${oracle.cwd}\0${oracle.command}` : undefined;
    const checkPlan: KartaCheckPlanEntry[] = floorChecks
      .filter((check) => `${check.cwd}\0${check.command}` !== oracleKey)
      .map((check) => ({ ...check, purpose: "floor" as const }));
    if (oracle) checkPlan.push(oracle);

    let checks: KartaCheckManifest | undefined;
    if (checkPlan.length > 0) {
      const convergence = await runStableTreeChecks({
        worktree,
        checks: checkPlan,
        signal: ctx.signal,
      });
      if (convergence.status !== "stable") {
        return {
          status: convergence.status === "failed" ? "retry" : "blocked",
          binder,
          item,
          targetTree: convergence.targetTree,
          checkFailure: convergence,
          message:
            convergence.status === "failed"
              ? "A final floor check failed; return the candidate to the worker."
              : `Final checks stopped as ${convergence.status}; the candidate remains staged for recovery.`,
        };
      }
      checks = convergence.manifest;
    }
    await rejectProtectedCandidate(worktree);
    await scanSecrets(worktree);
    const evidence = await buildKartaEvidence({
      cwd: worktree,
      binder,
      item,
      target: "candidate",
      checkManifest: checks,
    });
    if (checks && evidence.payload.git.targetTree !== checks.targetTree) {
      throw new Error("Karta stable check manifest does not match the final candidate tree");
    }
    await verifyEvidenceFreshness(evidence);
    const verification = await this.#verification.runWithLease(
      ctx,
      binder,
      item,
      "full",
      lease,
      { cwd: worktree, target: "candidate", checkManifest: checks },
    );
    const status = finalizationStatus(verification);
    if (status !== "built") {
      return {
        status,
        binder,
        item,
        targetTree: evidence.payload.git.targetTree,
        checks,
        verification,
        message:
          status === "retry"
            ? "Gate concerns require another bounded worker attempt."
            : "Gate evidence blocked finalization; the candidate remains staged.",
      };
    }

    const currentTree = await git(worktree, ["write-tree"]);
    if (currentTree !== evidence.payload.git.targetTree) {
      throw new Error("Karta candidate tree changed after verification");
    }
    const parent = await git(worktree, ["rev-parse", "HEAD"]);
    const expectedBranch = `karta/${binder}/item-${item}`;
    const branch = await git(worktree, ["branch", "--show-current"]);
    if (branch !== expectedBranch) {
      throw new Error(`Karta finalizer expected branch '${expectedBranch}', found '${branch}'`);
    }
    const message = `[karta:item-${item}] ${String(evidence.payload.workItem.title ?? item)}`;
    const commit = await git(worktree, [
      "commit-tree",
      evidence.payload.git.targetTree,
      "-p",
      parent,
      "-m",
      message,
    ]);
    await git(worktree, [
      "update-ref",
      `refs/heads/${expectedBranch}`,
      commit,
      parent,
    ]);
    const committedHead = await git(worktree, ["rev-parse", "HEAD"]);
    if (committedHead !== commit) {
      throw new Error("Karta item branch did not advance to the exact-tree commit");
    }
    const commitTree = await git(worktree, ["rev-parse", "HEAD^{tree}"]);
    if (commitTree !== evidence.payload.git.targetTree) {
      throw new Error("Karta committed tree does not match the verified candidate tree");
    }
    await git(worktree, [
      "update-ref",
      `refs/karta/${binder}/item-${item}/built`,
      commit,
      await nullObjectId(worktree),
    ]);
    return {
      status: "built",
      binder,
      item,
      targetTree: commitTree,
      commit,
      checks,
      verification,
      message: "Candidate committed and built ref written after exact-tree verification.",
    };
  }
}
