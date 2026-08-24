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
import type { KartaGateFinding } from "./gate-runner.ts";
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
    | "done-ref-updated"
    | "failed-ref-deleted"
    | "accepted-ref-updated"
    | "accept-merge-reverted",
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
  safetyVerification?: KartaVerificationResult;
  hookValidation?: HookValidationResult;
  accepted?: boolean;
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

async function optionalCommitRef(cwd: string, ref: string): Promise<string | undefined> {
  try {
    await exec("git", ["-C", cwd, "show-ref", "--verify", "--quiet", ref], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    });
  } catch (error) {
    if ((error as { code?: number }).code === 1) return undefined;
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git show-ref failed for ${ref}`);
  }
  return git(cwd, ["rev-parse", "--verify", `${ref}^{commit}`]);
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

async function scanSecrets(
  cwd: string,
  committedRange?: { base: string; target: string },
): Promise<void> {
  const args = [
    "run",
    "--script",
    requirePackagePath("skills/karta-build/scripts/scan_secrets.py"),
  ];
  if (committedRange) args.push("--base", committedRange.base, "--target", committedRange.target);
  try {
    await exec(
      "uv",
      args,
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

  async recoverAccepted(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    integrationWorktree: string,
    lease: DispatchLockLease,
    floorChecks: KartaCheckPlanEntry[] = [],
    processContext?: { manager: KartaProcessManager; owner: BinderLifecycleOwner },
  ): Promise<KartaIntegrationResult> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta accepted recovery requires the active binder lock lease");
    }
    const integrationRef = `refs/heads/karta/${binder}/integration`;
    const itemRef = `refs/heads/karta/${binder}/item-${item}`;
    const [mergeCommit, itemTip, failed, done, accepted] = await Promise.all([
      git(integrationWorktree, ["rev-parse", integrationRef]),
      git(integrationWorktree, ["rev-parse", itemRef]),
      optionalCommitRef(integrationWorktree, `refs/karta/${binder}/item-${item}/failed`),
      optionalCommitRef(integrationWorktree, `refs/karta/${binder}/item-${item}/done`),
      optionalCommitRef(integrationWorktree, `refs/karta/${binder}/item-${item}/accepted`),
    ]);
    const parents = (await git(integrationWorktree, [
      "rev-list",
      "--parents",
      "-n",
      "1",
      mergeCommit,
    ])).split(/\s+/).slice(1);
    const message = await git(integrationWorktree, ["show", "-s", "--format=%B", mergeCommit]);
    if (
      parents.length !== 2 ||
      parents[1] !== itemTip ||
      (failed !== undefined && failed !== itemTip) ||
      (done !== undefined && done !== mergeCommit) ||
      accepted !== undefined ||
      !/^Karta-Accepted:\s*\S+/mi.test(message) ||
      !/^Karta-Accept-Reason:\s*\S+/mi.test(message)
    ) {
      throw new Error("Karta accepted recovery found contradictory merge or ref evidence");
    }
    const targetTree = await git(integrationWorktree, ["rev-parse", `${mergeCommit}^{tree}`]);
    const revertAccepted = async (reason: string): Promise<KartaIntegrationResult> => {
      await git(integrationWorktree, ["read-tree", "--reset", "-u", parents[0]]);
      await git(integrationWorktree, ["update-ref", integrationRef, parents[0], mergeCommit]);
      if (done) {
        await git(integrationWorktree, [
          "update-ref",
          "-d",
          `refs/karta/${binder}/item-${item}/done`,
          mergeCommit,
        ]);
      }
      if (!failed) {
        await git(integrationWorktree, [
          "update-ref",
          `refs/karta/${binder}/item-${item}/failed`,
          itemTip,
          await nullObjectId(integrationWorktree),
        ]);
      }
      await this.#checkpoint("accept-merge-reverted");
      return {
        schema: "karta-integration-item-v1",
        binder,
        item,
        status: "blocked",
        base: parents[0],
        itemTip,
        targetTree,
        mergeCommit,
        accepted: false,
        message: reason,
      };
    };
    const preliminaryEvidence = await buildKartaEvidence({
      cwd: integrationWorktree,
      binder,
      item,
      target: "landed",
    });
    const oracle = oracleCheck(preliminaryEvidence.payload.workItem);
    const oracleKey = oracle ? `${oracle.cwd}\0${oracle.command}` : undefined;
    const plan: KartaCheckPlanEntry[] = floorChecks
      .filter((check) => `${check.cwd}\0${check.command}` !== oracleKey)
      .map((check) => ({ ...check, purpose: "floor" as const }));
    if (oracle) plan.push(oracle);
    let checks: KartaCheckManifest | undefined;
    if (plan.length > 0) {
      const convergence = await runStableTreeChecks({
        worktree: integrationWorktree,
        checks: plan,
        signal: ctx.signal,
        onProcessStart: processContext
          ? (pid) => processContext.manager.registerProcess(pid, {
              cwd: integrationWorktree,
              parentId: processContext.owner.id,
              label: `${item} accepted recovery floor`,
              role: "host-check",
            })
          : undefined,
        onProcessExit: processContext
          ? (pid) => processContext.manager.forgetProcess(pid)
          : undefined,
      });
      if (convergence.status !== "stable" || convergence.targetTree !== targetTree) {
        return revertAccepted("Accepted recovery floor failed; merge reverted and failed ref restored.");
      }
      checks = convergence.manifest;
    }
    await scanSecrets(integrationWorktree, { base: parents[0], target: mergeCommit });
    const evidence = await buildKartaEvidence({
      cwd: integrationWorktree,
      binder,
      item,
      target: "landed",
      checkManifest: checks,
    });
    await verifyEvidenceFreshness(evidence);
    const verification = await this.#verification.runWithLease(
      ctx,
      binder,
      item,
      "full",
      lease,
      { cwd: integrationWorktree, target: "landed", checkManifest: checks },
    );
    let safetyVerification: KartaVerificationResult | undefined;
    let reviewPassed = verification.status === "pass" || verification.status === "skipped";
    if (verification.status === "concerns" && verification.gates.acceptance?.verdict === "concerns") {
      const waivedCodes = new Set(
        [...message.matchAll(/^Karta-Accepted:\s*([^@\s]+)/gmi)].map((match) => match[1]),
      );
      reviewPassed = verification.gates.acceptance.findings.every((finding) =>
        waivedCodes.has(finding.code),
      );
      if (reviewPassed) {
        safetyVerification = await this.#verification.runWithLease(
          ctx,
          binder,
          item,
          "boundary-only",
          lease,
          { cwd: integrationWorktree, target: "landed", checkManifest: checks },
        );
        reviewPassed = safetyVerification.status === "pass" || safetyVerification.status === "skipped";
      }
    }
    if (!reviewPassed) {
      return revertAccepted(
        "Accepted recovery found new or unsafe findings; merge reverted and failed ref restored.",
      );
    }
    if (!done) {
      await git(integrationWorktree, [
        "update-ref",
        `refs/karta/${binder}/item-${item}/done`,
        mergeCommit,
        await nullObjectId(integrationWorktree),
      ]);
      await this.#checkpoint("done-ref-updated");
    }
    if (failed) {
      await git(integrationWorktree, [
        "update-ref",
        "-d",
        `refs/karta/${binder}/item-${item}/failed`,
        itemTip,
      ]);
      await this.#checkpoint("failed-ref-deleted");
    }
    await git(integrationWorktree, [
      "update-ref",
      `refs/karta/${binder}/item-${item}/accepted`,
      itemTip,
      await nullObjectId(integrationWorktree),
    ]);
    await this.#checkpoint("accepted-ref-updated");
    return {
      schema: "karta-integration-item-v1",
      binder,
      item,
      status: "integrated",
      base: parents[0],
      itemTip,
      targetTree,
      mergeCommit,
      checks,
      verification,
      safetyVerification,
      accepted: true,
      message: "Accepted merge recovery completed with accepted ref-last.",
    };
  }

  async integrate(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    integrationWorktree: string,
    lease: DispatchLockLease,
    floorChecks: KartaCheckPlanEntry[] = [],
    processContext?: { manager: KartaProcessManager; owner: BinderLifecycleOwner },
    acceptance?: {
      authorize(findings: KartaGateFinding[]): Promise<{ reason: string } | undefined>;
    },
  ): Promise<KartaIntegrationResult> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta integration requires the active binder lock lease");
    }
    const integrationRef = `refs/heads/karta/${binder}/integration`;
    const itemRef = `refs/heads/karta/${binder}/item-${item}`;
    const [base, itemTip, built, failed, branch, status] = await Promise.all([
      git(integrationWorktree, ["rev-parse", integrationRef]),
      git(integrationWorktree, ["rev-parse", itemRef]),
      optionalCommitRef(integrationWorktree, `refs/karta/${binder}/item-${item}/built`),
      optionalCommitRef(integrationWorktree, `refs/karta/${binder}/item-${item}/failed`),
      git(integrationWorktree, ["branch", "--show-current"]),
      git(integrationWorktree, ["status", "--porcelain=v2", "-z", "--untracked-files=all"]),
    ]);
    if (acceptance) {
      if (failed !== itemTip || built) {
        throw new Error("Karta acceptance requires failed ref at the item tip and no built ref");
      }
    } else if (built !== itemTip) {
      throw new Error("Karta integration requires built ref at the item tip");
    }
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
      let safetyVerification: KartaVerificationResult | undefined;
      let waiver: { reason: string; findings: KartaGateFinding[] } | undefined;
      if (acceptance) {
        let findings: KartaGateFinding[] = [];
        if (verification.status === "concerns") {
          if (verification.gates.acceptance?.verdict !== "concerns") {
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
              message: "Human acceptance cannot waive a safety-gate concern.",
            };
          }
          findings = verification.gates.acceptance.findings;
          safetyVerification = await this.#verification.runWithLease(
            ctx,
            binder,
            item,
            "boundary-only",
            lease,
            { cwd: integrationWorktree, target: "merge", checkManifest: checks },
          );
          if (safetyVerification.status !== "pass" && safetyVerification.status !== "skipped") {
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
              safetyVerification,
              message: "Human acceptance cannot waive a safety-gate failure.",
            };
          }
        } else if (verification.status !== "pass" && verification.status !== "skipped") {
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
            message: "Failed item could not be freshly reviewed for human acceptance.",
          };
        }
        const authorization = await acceptance.authorize(findings);
        const reason = authorization?.reason.replace(/\s+/g, " ").trim() ?? "";
        if (!reason || reason.length > 1_000) {
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
            safetyVerification,
            message: "Human acceptance was cancelled or supplied no bounded reason.",
          };
        }
        waiver = { reason, findings };
      } else if (verification.status !== "pass" && verification.status !== "skipped") {
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
          message:
            verification.blockedReason === "visual-required"
              ? "Proposed integration blocks as visual-required until visual acceptance lands; no merge or ref was written."
              : "Proposed integration tree did not pass fresh verification.",
        };
      }
      const acceptTrailers = waiver
        ? [
            ...(waiver.findings.length > 0
              ? waiver.findings.map((finding) =>
                  `Karta-Accepted: ${finding.code}${finding.path ? `@${finding.path}${finding.line ? `:${finding.line}` : ""}` : ""}`,
                )
              : ["Karta-Accepted: fresh-review-passed"]),
            `Karta-Accept-Reason: ${waiver.reason}`,
          ]
        : [];
      const proposedMessage = [
        `[karta:merge-item-${item}] integrate item-${item}`,
        ...(acceptTrailers.length > 0 ? ["", ...acceptTrailers] : []),
      ].join("\n");
      const hookValidation = await validateMergeHooks({
        worktree: integrationWorktree,
        integrationTip: base,
        itemTip,
        candidateTree: targetTree,
        message: proposedMessage,
        signal: ctx.signal,
        onProcessStart: processContext
          ? (pid) => processContext.manager.registerProcess(pid, {
              cwd: integrationWorktree,
              parentId: processContext.owner.id,
              label: `${item} merge hook validation`,
            })
          : undefined,
        onProcessExit: processContext
          ? (pid) => processContext.manager.forgetProcess(pid)
          : undefined,
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
      if (
        waiver &&
        (!/^Karta-Accepted:\s*\S+/mi.test(message) ||
          !/^Karta-Accept-Reason:\s*\S+/mi.test(message))
      ) {
        throw new Error("Karta merge hooks removed mandatory human-accept trailers");
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
      if (waiver && plan.length > 0) {
        const postAccept = await runStableTreeChecks({
          worktree: integrationWorktree,
          checks: plan,
          signal: ctx.signal,
          onProcessStart: processContext
            ? (pid) => processContext.manager.registerProcess(pid, {
                cwd: integrationWorktree,
                parentId: processContext.owner.id,
                label: `${item} post-accept floor`,
                role: "host-check",
              })
            : undefined,
          onProcessExit: processContext
            ? (pid) => processContext.manager.forgetProcess(pid)
            : undefined,
        });
        if (postAccept.status !== "stable" || postAccept.targetTree !== targetTree) {
          await git(integrationWorktree, ["read-tree", "--reset", "-u", base]);
          try {
            await git(integrationWorktree, ["update-ref", integrationRef, base, mergeCommit]);
          } catch (error) {
            await git(integrationWorktree, ["read-tree", "--reset", "-u", mergeCommit]);
            throw error;
          }
          await this.#checkpoint("accept-merge-reverted");
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
            safetyVerification,
            hookValidation,
            accepted: false,
            message: "Post-accept floor failed; the accepted merge was reverted and failed ref preserved.",
          };
        }
      }
      await git(integrationWorktree, [
        "update-ref",
        `refs/karta/${binder}/item-${item}/done`,
        mergeCommit,
        await nullObjectId(integrationWorktree),
      ]);
      await this.#checkpoint("done-ref-updated");
      if (waiver) {
        await git(integrationWorktree, [
          "update-ref",
          "-d",
          `refs/karta/${binder}/item-${item}/failed`,
          itemTip,
        ]);
        await this.#checkpoint("failed-ref-deleted");
        await git(integrationWorktree, [
          "update-ref",
          `refs/karta/${binder}/item-${item}/accepted`,
          itemTip,
          await nullObjectId(integrationWorktree),
        ]);
        await this.#checkpoint("accepted-ref-updated");
      }
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
        safetyVerification,
        hookValidation,
        accepted: Boolean(waiver),
        message: waiver
          ? "Human-waived merge passed safety and post-accept floor; accepted ref written last."
          : "Verified no-ff merge committed and done ref written ref-last.",
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
