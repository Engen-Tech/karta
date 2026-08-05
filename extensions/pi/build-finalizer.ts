import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { bindCheckReceipt, runBoundCheck } from "./check-runner.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import {
  buildKartaEvidence,
  verifyEvidenceFreshness,
  type KartaCheckReceipt,
} from "./evidence.ts";
import { requirePackagePath } from "./package-paths.ts";
import type { KartaVerificationResult, KartaVerificationRunner } from "./verification-runner.ts";

const exec = promisify(execFile);
const ZERO_OBJECT = "0".repeat(40);
const MAX_GIT_OUTPUT = 4 * 1024 * 1024;

export type KartaBuildFinalizationStatus = "built" | "retry" | "blocked" | "no-change";

export interface KartaBuildFinalizationResult {
  status: KartaBuildFinalizationStatus;
  binder: string;
  item: string;
  targetTree?: string;
  commit?: string;
  check?: KartaCheckReceipt;
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

function oracleCommand(workItem: Record<string, unknown>): string | undefined {
  const oracle = workItem.oracle;
  if (!oracle || typeof oracle !== "object" || Array.isArray(oracle)) return undefined;
  const command = (oracle as Record<string, unknown>).command;
  return typeof command === "string" && command.trim() ? command.trim() : undefined;
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
    (path) => path.startsWith(".karta/binders/") || path.startsWith(".karta/roundtable/"),
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
    await scanSecrets(worktree);
    const evidence = await buildKartaEvidence({
      cwd: worktree,
      binder,
      item,
      target: "candidate",
    });
    const command = oracleCommand(evidence.payload.workItem);
    let check: KartaCheckReceipt | undefined;
    if (command) {
      const completed = await runBoundCheck({
        worktree,
        command,
        signal: ctx.signal,
      });
      if (completed.status === "aborted" || completed.status === "timed-out") {
        return {
          status: "blocked",
          binder,
          item,
          targetTree: evidence.payload.git.targetTree,
          message: `Host check ${completed.status}; the candidate remains staged for recovery.`,
        };
      }
      check = bindCheckReceipt(completed, evidence.payload.git.targetTree);
    }
    await verifyEvidenceFreshness(evidence);
    const verification = await this.#verification.runWithLease(
      ctx,
      binder,
      item,
      "full",
      lease,
      { cwd: worktree, target: "candidate", checkReceipt: check },
    );
    const status = finalizationStatus(verification);
    if (status !== "built") {
      return {
        status,
        binder,
        item,
        targetTree: evidence.payload.git.targetTree,
        check,
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
    await git(worktree, [
      "commit",
      "--no-gpg-sign",
      "-m",
      `[karta:item-${item}] ${String(evidence.payload.workItem.title ?? item)}`,
    ]);
    const commit = await git(worktree, ["rev-parse", "HEAD"]);
    const commitTree = await git(worktree, ["rev-parse", "HEAD^{tree}"]);
    if (commitTree !== evidence.payload.git.targetTree) {
      throw new Error("Karta committed tree does not match the verified candidate tree");
    }
    await git(worktree, [
      "update-ref",
      `refs/karta/${binder}/item-${item}/built`,
      commit,
      ZERO_OBJECT,
    ]);
    return {
      status: "built",
      binder,
      item,
      targetTree: commitTree,
      commit,
      check,
      verification,
      message: "Candidate committed and built ref written after exact-tree verification.",
    };
  }
}
