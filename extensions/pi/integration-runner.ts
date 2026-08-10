import { execFile } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  runStableTreeChecks,
  type KartaCheckPlanEntry,
} from "./check-convergence.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import {
  buildKartaEvidence,
  verifyEvidenceFreshness,
  type KartaCheckManifest,
} from "./evidence.ts";
import { validateMergeHooks, type HookValidationResult } from "./hook-runner.ts";
import { requirePackagePath } from "./package-paths.ts";
import {
  KartaProcessManager,
  type BinderLifecycleOwner,
} from "./process-manager.ts";
import type { KartaVerificationResult, KartaVerificationRunner } from "./verification-runner.ts";

const exec = promisify(execFile);
const MAX_OUTPUT = 8 * 1024 * 1024;

export type KartaIntegrationCheckpoint = (
  name:
    | "proposed-tree"
    | "checks-bound"
    | "gates-complete"
    | "merge-commit-created"
    | "integration-ref-updated"
    | "done-ref-updated",
) => Promise<void> | void;

export interface KartaIntegrationResult {
  schema: "karta-integration-item-v1";
  binder: string;
  item: string;
  status: "integrated" | "retry" | "blocked";
  base: string;
  itemTip: string;
  targetTree?: string;
  mergeCommit?: string;
  checks?: KartaCheckManifest;
  verification?: KartaVerificationResult;
  hookValidation?: HookValidationResult;
  message: string;
}

async function git(cwd: string, args: string[], allowFailure = false): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    });
    return stdout.trim();
  } catch (error) {
    if (allowFailure) return "";
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during integration`);
  }
}

async function nullObjectId(cwd: string): Promise<string> {
  const format = await git(cwd, ["rev-parse", "--show-object-format"]);
  if (format === "sha1") return "0".repeat(40);
  if (format === "sha256") return "0".repeat(64);
  throw new Error(`Karta does not support Git object format '${format}'`);
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

async function scanSecrets(cwd: string): Promise<void> {
  try {
    await exec(
      "uv",
      ["run", "--script", requirePackagePath("skills/karta-build/scripts/scan_secrets.py")],
      { cwd, encoding: "utf8", maxBuffer: MAX_OUTPUT },
    );
  } catch (error) {
    const output = `${(error as { stdout?: string }).stdout ?? ""}${(error as { stderr?: string }).stderr ?? ""}`.trim();
    throw new Error(output || "Karta integration secret scan failed");
  }
}

export class KartaIntegrationRunner {
  readonly #locks: DispatchLockManager;
  readonly #verification: KartaVerificationRunner;
  readonly #checkpoint: KartaIntegrationCheckpoint;

  constructor(
    locks: DispatchLockManager,
    verification: KartaVerificationRunner,
    checkpoint: KartaIntegrationCheckpoint = () => {},
  ) {
    this.#locks = locks;
    this.#verification = verification;
    this.#checkpoint = checkpoint;
  }

  async integrate(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    integrationWorktree: string,
    lease: DispatchLockLease,
    floorChecks: KartaCheckPlanEntry[] = [],
    processContext?: { manager: KartaProcessManager; owner: BinderLifecycleOwner },
  ): Promise<KartaIntegrationResult> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta integration requires the active binder lock lease");
    }
    const integrationRef = `refs/heads/karta/${binder}/integration`;
    const itemRef = `refs/heads/karta/${binder}/item-${item}`;
    const [base, itemTip, built, branch, status] = await Promise.all([
      git(integrationWorktree, ["rev-parse", integrationRef]),
      git(integrationWorktree, ["rev-parse", itemRef]),
      git(integrationWorktree, ["rev-parse", `refs/karta/${binder}/item-${item}/built`]),
      git(integrationWorktree, ["branch", "--show-current"]),
      git(integrationWorktree, ["status", "--porcelain=v2", "-z", "--untracked-files=all"]),
    ]);
    if (built !== itemTip) throw new Error("Karta integration requires built ref at the item tip");
    if (branch !== `karta/${binder}/integration` || status) {
      throw new Error("Karta integration worktree is not the clean owned integration branch");
    }
    const preliminaryEvidence = await buildKartaEvidence({
      cwd: integrationWorktree,
      binder,
      item,
      target: "merge",
    });
    const targetTree = preliminaryEvidence.payload.git.targetTree;
    await this.#checkpoint("proposed-tree");
    const oracle = oracleCheck(preliminaryEvidence.payload.workItem);
    const oracleKey = oracle ? `${oracle.cwd}\0${oracle.command}` : undefined;
    const plan: KartaCheckPlanEntry[] = floorChecks
      .filter((check) => `${check.cwd}\0${check.command}` !== oracleKey)
      .map((check) => ({ ...check, purpose: "floor" as const }));
    if (oracle) plan.push(oracle);

    const root = await mkdtemp(join(tmpdir(), "karta-proposed-integration-"));
    const proposedWorktree = join(root, "worktree");
    let registered = false;
    try {
      await git(integrationWorktree, [
        "worktree",
        "add",
        "--detach",
        "--no-checkout",
        proposedWorktree,
        base,
      ]);
      registered = true;
      await git(proposedWorktree, ["read-tree", "--reset", "-u", targetTree]);
      let checks: KartaCheckManifest | undefined;
      if (plan.length > 0) {
        const convergence = await runStableTreeChecks({
          worktree: proposedWorktree,
          checks: plan,
          signal: ctx.signal,
          onProcessStart: processContext
            ? (pid) => processContext.manager.registerProcess(pid, {
                cwd: proposedWorktree,
                parentId: processContext.owner.id,
                label: `${item} proposed integration check`,
                role: "host-check",
              })
            : undefined,
          onProcessExit: processContext
            ? (pid) => processContext.manager.forgetProcess(pid)
            : undefined,
        });
        if (convergence.status !== "stable" || convergence.targetTree !== targetTree) {
          return {
            schema: "karta-integration-item-v1",
            binder,
            item,
            status: convergence.status === "failed" ? "retry" : "blocked",
            base,
            itemTip,
            targetTree: convergence.targetTree,
            message: "Proposed integration checks failed or changed the exact merge tree.",
          };
        }
        checks = convergence.manifest;
      }
      await this.#checkpoint("checks-bound");
      const protectedPaths = (await git(proposedWorktree, [
        "diff",
        "--cached",
        "--name-only",
        "-z",
      ])).split("\0").filter(Boolean).filter(
        (path) => path === ".karta" || path.startsWith(".karta/") || path === ".git" || path.startsWith(".git/"),
      );
      if (protectedPaths.length > 0) {
        throw new Error(`Karta integration modifies protected state: ${protectedPaths[0]}`);
      }
      await scanSecrets(proposedWorktree);
      const evidence = await buildKartaEvidence({
        cwd: integrationWorktree,
        binder,
        item,
        target: "merge",
        checkManifest: checks,
      });
      await verifyEvidenceFreshness(evidence);
      const verification = await this.#verification.runWithLease(
        ctx,
        binder,
        item,
        "full",
        lease,
        { cwd: integrationWorktree, target: "merge", checkManifest: checks },
      );
      await this.#checkpoint("gates-complete");
      if (verification.status !== "pass" && verification.status !== "skipped") {
        return {
          schema: "karta-integration-item-v1",
          binder,
          item,
          status: verification.status === "concerns" ? "retry" : "blocked",
          base,
          itemTip,
          targetTree,
          checks,
          verification,
          message: "Proposed integration tree did not pass fresh verification.",
        };
      }
      const proposedMessage = `[karta:merge-item-${item}] integrate item-${item}`;
      const hookValidation = await validateMergeHooks({
        worktree: integrationWorktree,
        integrationTip: base,
        itemTip,
        candidateTree: targetTree,
        message: proposedMessage,
        signal: ctx.signal,
      });
      if (hookValidation.status !== "passed") {
        return {
          schema: "karta-integration-item-v1",
          binder,
          item,
          status: "blocked",
          base,
          itemTip,
          targetTree,
          checks,
          verification,
          hookValidation,
          message: "Repository merge hooks failed or changed the verified merge tree.",
        };
      }
      const message = hookValidation.message ?? proposedMessage;
      if (!message.split("\n", 1)[0].includes(`[karta:merge-item-${item}]`)) {
        throw new Error("Karta merge hooks removed the mandatory merge marker");
      }
      const mergeCommit = await git(integrationWorktree, [
        "commit-tree",
        targetTree,
        "-p",
        base,
        "-p",
        itemTip,
        "-m",
        message,
      ]);
      await this.#checkpoint("merge-commit-created");
      await git(integrationWorktree, ["update-ref", integrationRef, mergeCommit, base]);
      await this.#checkpoint("integration-ref-updated");
      await git(integrationWorktree, ["read-tree", "--reset", "-u", mergeCommit]);
      const actualTree = await git(integrationWorktree, ["rev-parse", "HEAD^{tree}"]);
      const parents = (await git(integrationWorktree, ["rev-list", "--parents", "-n", "1", "HEAD"]))
        .split(/\s+/).slice(1);
      if (
        actualTree !== targetTree ||
        parents.length !== 2 ||
        parents[0] !== base ||
        parents[1] !== itemTip
      ) {
        throw new Error("Karta integration commit does not preserve the verified merge transaction");
      }
      await git(integrationWorktree, [
        "update-ref",
        `refs/karta/${binder}/item-${item}/done`,
        mergeCommit,
        await nullObjectId(integrationWorktree),
      ]);
      await this.#checkpoint("done-ref-updated");
      return {
        schema: "karta-integration-item-v1",
        binder,
        item,
        status: "integrated",
        base,
        itemTip,
        targetTree,
        mergeCommit,
        checks,
        verification,
        hookValidation,
        message: "Verified no-ff merge committed and done ref written ref-last.",
      };
    } finally {
      if (registered) {
        await git(integrationWorktree, ["worktree", "remove", "--force", proposedWorktree], true)
          .catch(() => undefined);
      }
      await rm(root, { recursive: true, force: true });
    }
  }
}
