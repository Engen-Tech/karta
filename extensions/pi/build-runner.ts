import { execFile } from "node:child_process";
import { access, mkdir } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { KartaBuildFinalizer, type KartaBuildFinalizationResult } from "./build-finalizer.ts";
import type { KartaCheckPlanEntry } from "./check-convergence.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import { deriveItemGitState, type KartaItemRecoveryState } from "./git-state.ts";
import {
  KartaProcessManager,
  type BinderLifecycleOwner,
} from "./process-manager.ts";
import { KartaBuildWorkerRunner, type KartaWorkerResult } from "./worker-runner.ts";

const exec = promisify(execFile);
const MAX_GIT_OUTPUT = 8 * 1024 * 1024;
const MAX_ACCEPTANCE_ATTEMPTS = 2;
const MAX_SAFETY_ATTEMPTS = 3;

export type KartaBuildCheckpoint = (
  name:
    | "lock-acquired"
    | "owner-created"
    | "state-derived"
    | "worktree-ready"
    | "before-worker"
    | "first-worker-edit"
    | "worker-attested"
    | "before-finalization"
    | "finalization-returned",
) => Promise<void> | void;

export interface KartaBuildItemResult {
  schema: "karta-build-item-v1";
  binder: string;
  item: string;
  status: "built" | "failed" | "blocked" | "no-change" | "recovered";
  recoveryState: KartaItemRecoveryState;
  attempts: number;
  worktree?: string;
  commit?: string;
  message: string;
  worker?: KartaWorkerResult;
  finalization?: KartaBuildFinalizationResult;
}

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_GIT_OUTPUT,
    });
    return stdout.trim();
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during build orchestration`);
  }
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

function parseAssignment(raw: string, binder: string, item: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error(`Karta binder '${binder}' is not valid JSON at the integration tip`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Karta binder '${binder}' is not an object`);
  }
  const document = value as { slug?: unknown; work_items?: unknown };
  if (document.slug !== binder || !Array.isArray(document.work_items)) {
    throw new Error(`Karta binder '${binder}' has invalid identity or work_items`);
  }
  const matches = document.work_items.filter(
    (candidate): candidate is Record<string, unknown> =>
      Boolean(candidate) && typeof candidate === "object" && !Array.isArray(candidate) &&
      (candidate as Record<string, unknown>).id === item,
  );
  if (matches.length !== 1) {
    throw new Error(`Karta binder '${binder}' must contain item '${item}' exactly once`);
  }
  return matches[0];
}

function terminalRecovery(
  binder: string,
  item: string,
  state: Awaited<ReturnType<typeof deriveItemGitState>>,
): KartaBuildItemResult | undefined {
  if (state.state === "done" || state.state === "built") {
    return {
      schema: "karta-build-item-v1",
      binder,
      item,
      status: "recovered",
      recoveryState: state.state,
      attempts: 0,
      worktree: state.worktree,
      commit: state.state === "built" ? state.refs.built : state.itemTip,
      message: state.nextAction,
    };
  }
  if (
    state.state === "failed" ||
    state.state === "accept-merge-pending" ||
    state.state === "accept-ref-pending"
  ) {
    return {
      schema: "karta-build-item-v1",
      binder,
      item,
      status: "blocked",
      recoveryState: state.state,
      attempts: 0,
      worktree: state.worktree,
      commit: state.itemTip,
      message: state.nextAction,
    };
  }
  if (state.state === "inconsistent") {
    return {
      schema: "karta-build-item-v1",
      binder,
      item,
      status: "blocked",
      recoveryState: state.state,
      attempts: 0,
      worktree: state.worktree,
      commit: state.itemTip,
      message: state.nextAction,
    };
  }
  return undefined;
}

function retryKind(result: KartaBuildFinalizationResult): "acceptance" | "safety" | undefined {
  if (result.verification?.gates.acceptance?.verdict === "concerns") return "acceptance";
  if (result.verification?.gates.safety?.verdict === "concerns") return "safety";
  return undefined;
}

export class KartaBuildItemRunner {
  readonly #locks: DispatchLockManager;
  readonly #workers: KartaBuildWorkerRunner;
  readonly #finalizer: KartaBuildFinalizer;
  readonly #processes: KartaProcessManager;
  readonly #checkpoint: KartaBuildCheckpoint;

  constructor(
    locks: DispatchLockManager,
    workers: KartaBuildWorkerRunner,
    finalizer: KartaBuildFinalizer,
    processes: KartaProcessManager,
    checkpoint: KartaBuildCheckpoint = () => {},
  ) {
    this.#locks = locks;
    this.#workers = workers;
    this.#finalizer = finalizer;
    this.#processes = processes;
    this.#checkpoint = checkpoint;
  }

  async run(ctx: ExtensionContext, binder: string, item: string): Promise<KartaBuildItemResult> {
    const lease = await this.#locks.acquire(ctx.cwd, binder);
    let owner: BinderLifecycleOwner;
    try {
      await this.#checkpoint("lock-acquired");
      owner = this.#processes.createBinderOwner(ctx.cwd, binder);
      await this.#checkpoint("owner-created");
    } catch (error) {
      await this.#locks.release(lease);
      throw error;
    }
    try {
      return await this.#runLocked(ctx, binder, item, lease, owner, [item]);
    } finally {
      try {
        await this.#processes.stopOwner(owner);
      } finally {
        await this.#locks.release(lease);
      }
    }
  }

  async runWithLease(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    lease: DispatchLockLease,
    owner: BinderLifecycleOwner,
    waveMates: readonly string[] = [item],
  ): Promise<KartaBuildItemResult> {
    if (!(await this.#locks.owns(lease)) || owner.binder !== binder) {
      throw new Error("Karta build item requires its delivery-owned binder lease and lifecycle");
    }
    return this.#runLocked(ctx, binder, item, lease, owner, waveMates);
  }

  async #runLocked(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    lease: DispatchLockLease,
    owner: BinderLifecycleOwner,
    waveMates: readonly string[],
  ): Promise<KartaBuildItemResult> {
      let state = await deriveItemGitState(ctx.cwd, binder, item);
      await this.#checkpoint("state-derived");
      const terminal = terminalRecovery(binder, item, state);
      if (terminal) return terminal;

      const repoRoot = await git(ctx.cwd, ["rev-parse", "--show-toplevel"]);
      const integrationRef = `refs/heads/karta/${binder}/integration`;
      const assignment = parseAssignment(
        await git(repoRoot, ["show", `${integrationRef}:.karta/binders/${binder}.json`]),
        binder,
        item,
      );
      const branch = `karta/${binder}/item-${item}`;
      let worktree = state.worktree;
      if (!worktree) {
        const worktreeRoot = join(dirname(repoRoot), `${basename(repoRoot)}-worktrees`);
        worktree = join(worktreeRoot, branch.replaceAll("/", "-"));
        await mkdir(worktreeRoot, { recursive: true });
        if (await pathExists(worktree)) {
          throw new Error(`Karta refuses to clobber existing worktree path: ${worktree}`);
        }
        if (state.state === "not-started") {
          await git(repoRoot, ["worktree", "add", worktree, "-b", branch, integrationRef]);
        } else {
          await git(repoRoot, ["worktree", "add", worktree, branch]);
        }
        state = await deriveItemGitState(repoRoot, binder, item);
        if (state.worktree !== worktree) {
          throw new Error("Karta could not prove ownership of the item worktree it created");
        }
      }
      await this.#checkpoint("worktree-ready");

      let feedback: unknown[] = [];
      let attempts = 0;
      let acceptanceAttempts = 0;
      let safetyAttempts = 0;
      while (attempts < MAX_SAFETY_ATTEMPTS) {
        attempts += 1;
        const beforeWorker = await deriveItemGitState(worktree, binder, item);
        const recoverCommitted = beforeWorker.state === "committed-unmarked";
        const recoverMerged = beforeWorker.state === "merged-unmarked";
        await this.#checkpoint("before-worker");
        const worker = await this.#workers.run(
          ctx,
          worktree,
          branch,
          binder,
          item,
          assignment,
          feedback,
          owner.id,
          recoverCommitted ? "recover-committed" : recoverMerged ? "recover-merged" : "implement",
          () => this.#checkpoint("first-worker-edit"),
          waveMates,
        );
        await this.#checkpoint("worker-attested");
        if (worker.outcome === "blocked") {
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: "blocked",
            recoveryState: state.state,
            attempts,
            worktree,
            message: worker.summary,
            worker,
          };
        }
        const checks: KartaCheckPlanEntry[] = worker.checks.map((check) => ({
          ...check,
          purpose: "floor",
        }));
        const afterWorker = await deriveItemGitState(worktree, binder, item);
        if (recoverMerged && afterWorker.state !== "merged-unmarked") {
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: "blocked",
            recoveryState: state.state,
            attempts,
            worktree,
            message: "Worker changed the item worktree during landed-merge recovery.",
            worker,
          };
        }
        const recoveringExactCommit = afterWorker.state === "committed-unmarked";
        await this.#checkpoint("before-finalization");
        const finalization = recoverMerged
          ? await this.#finalizer.recoverMergedCandidate(
              ctx,
              binder,
              item,
              worktree,
              lease,
              checks,
              { manager: this.#processes, owner },
            )
          : recoveringExactCommit
            ? await this.#finalizer.recoverCommittedCandidate(
              ctx,
              binder,
              item,
              worktree,
              lease,
              checks,
              { manager: this.#processes, owner },
            )
          : await this.#finalizer.finalizeCandidate(
              ctx,
              binder,
              item,
              worktree,
              lease,
              checks,
              { manager: this.#processes, owner },
            );
        await this.#checkpoint("finalization-returned");
        if (finalization.status === "built" || finalization.status === "no-change") {
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: recoverMerged || recoveringExactCommit ? "recovered" : finalization.status,
            recoveryState: state.state,
            attempts,
            worktree,
            commit: finalization.commit,
            message: finalization.message,
            worker,
            finalization,
          };
        }
        if (recoverMerged) {
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: "blocked",
            recoveryState: state.state,
            attempts,
            worktree,
            commit: finalization.commit,
            message: finalization.message,
            worker,
            finalization,
          };
        }
        if (finalization.status !== "retry") {
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: finalization.status === "failed" ? "failed" : "blocked",
            recoveryState: state.state,
            attempts,
            worktree,
            commit: finalization.commit,
            message: finalization.message,
            worker,
            finalization,
          };
        }
        const kind = retryKind(finalization);
        if (!kind) throw new Error("Karta retry result has no retryable gate verdict");
        if (kind === "acceptance") acceptanceAttempts += 1;
        else safetyAttempts += 1;
        const capped =
          acceptanceAttempts >= MAX_ACCEPTANCE_ATTEMPTS || safetyAttempts >= MAX_SAFETY_ATTEMPTS;
        if (capped) {
          const failed = recoveringExactCommit
            ? await this.#finalizer.recordRecoveredFailedCandidate(
                binder,
                item,
                worktree,
                lease,
                finalization,
              )
            : await this.#finalizer.recordFailedCandidate(
                ctx,
                binder,
                item,
                worktree,
                lease,
                finalization,
                { manager: this.#processes, owner },
              );
          return {
            schema: "karta-build-item-v1",
            binder,
            item,
            status: failed.status === "failed" ? "failed" : "blocked",
            recoveryState: state.state,
            attempts,
            worktree,
            commit: failed.commit,
            message: failed.message,
            worker,
            finalization: failed,
          };
        }
        feedback = [finalization.verification];
      }
      throw new Error("Karta build retry accounting reached an impossible state");
  }
}
