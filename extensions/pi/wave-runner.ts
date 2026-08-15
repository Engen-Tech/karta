import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  runStableTreeChecks,
  type KartaCheckPlanEntry,
} from "./check-convergence.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import type { KartaIntegrationResult } from "./integration-runner.ts";
import { requirePackagePath } from "./package-paths.ts";
import {
  KartaProcessManager,
  type BinderLifecycleOwner,
} from "./process-manager.ts";

const exec = promisify(execFile);
const MAX_OUTPUT = 8 * 1024 * 1024;

export interface KartaWaveAnchor {
  binder: string;
  wave: number;
  base: string;
  baseTag: string;
}

export interface KartaWaveFinalizationResult {
  schema: "karta-wave-finalization-v1";
  status: "passed" | "rolled-back";
  anchor: KartaWaveAnchor;
  tip: string;
  successTag?: string;
  message: string;
}

export type KartaWaveCheckpoint = (
  name:
    | "base-tag-updated"
    | "post-wave-checks-complete"
    | "rollback-worktree-prepared"
    | "rollback-refs-committed"
    | "rollback-tag-updated"
    | "success-tag-updated",
) => Promise<void> | void;

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    });
    return stdout.trim();
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during wave finalization`);
  }
}

async function nullObjectId(cwd: string): Promise<string> {
  const format = await git(cwd, ["rev-parse", "--show-object-format"]);
  if (format === "sha1") return "0".repeat(40);
  if (format === "sha256") return "0".repeat(64);
  throw new Error(`Karta does not support Git object format '${format}'`);
}

async function updateRefsTransaction(cwd: string, commands: string[]): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const child = spawn("git", ["-C", cwd, "update-ref", "--stdin"], {
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", reject);
    child.once("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(stderr.trim() || stdout.trim() || `git update-ref transaction failed (${code})`));
    });
    child.stdin.end(`start\n${commands.join("\n")}\nprepare\ncommit\n`);
  });
}

function dedupeChecks(checks: KartaCheckPlanEntry[]): KartaCheckPlanEntry[] {
  const seen = new Set<string>();
  const result: KartaCheckPlanEntry[] = [];
  for (const check of checks) {
    const key = `${check.cwd}\0${check.command}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ ...check, purpose: "floor" });
  }
  return result;
}

async function sharedTermsPass(worktree: string, binder: string): Promise<boolean> {
  try {
    await exec(
      "uv",
      [
        "run",
        "--script",
        requirePackagePath("skills/karta-plan/scripts/check_shared_terms.py"),
        "--binder",
        `.karta/binders/${binder}.json`,
        worktree,
      ],
      { cwd: worktree, encoding: "utf8", maxBuffer: MAX_OUTPUT },
    );
    return true;
  } catch (error) {
    if (typeof (error as { code?: unknown }).code === "number") return false;
    throw error;
  }
}

export class KartaWaveRunner {
  readonly #locks: DispatchLockManager;
  readonly #checkpoint: KartaWaveCheckpoint;

  constructor(
    locks: DispatchLockManager,
    checkpoint: KartaWaveCheckpoint = () => {},
  ) {
    this.#locks = locks;
    this.#checkpoint = checkpoint;
  }

  async start(
    binder: string,
    wave: number,
    integrationWorktree: string,
    lease: DispatchLockLease,
  ): Promise<KartaWaveAnchor> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta wave start requires the active binder lock lease");
    }
    const base = await git(integrationWorktree, ["rev-parse", "HEAD"]);
    const baseTag = `refs/tags/karta/${binder}/wave-${wave}-base`;
    await git(integrationWorktree, [
      "update-ref",
      baseTag,
      base,
      await nullObjectId(integrationWorktree),
    ]);
    await this.#checkpoint("base-tag-updated");
    return { binder, wave, base, baseTag };
  }

  async finish(
    ctx: ExtensionContext,
    anchor: KartaWaveAnchor,
    integrationWorktree: string,
    lease: DispatchLockLease,
    integrations: KartaIntegrationResult[],
    checks: KartaCheckPlanEntry[],
    processContext?: { manager: KartaProcessManager; owner: BinderLifecycleOwner },
  ): Promise<KartaWaveFinalizationResult> {
    if (!(await this.#locks.owns(lease))) {
      throw new Error("Karta wave finish requires the active binder lock lease");
    }
    const tip = await git(integrationWorktree, ["rev-parse", "HEAD"]);
    let failure = integrations.some((integration) => integration.status !== "integrated");
    const plan = dedupeChecks(checks);
    if (!failure && plan.length > 0) {
      const convergence = await runStableTreeChecks({
        worktree: integrationWorktree,
        checks: plan,
        signal: ctx.signal,
        onProcessStart: processContext
          ? (pid) => processContext.manager.registerProcess(pid, {
              cwd: integrationWorktree,
              parentId: processContext.owner.id,
              label: `wave-${anchor.wave} post-wave check`,
              role: "host-check",
            })
          : undefined,
        onProcessExit: processContext
          ? (pid) => processContext.manager.forgetProcess(pid)
          : undefined,
      });
      const committedTree = await git(integrationWorktree, ["rev-parse", "HEAD^{tree}"]);
      failure = convergence.status !== "stable" || convergence.targetTree !== committedTree;
    }
    if (!failure) failure = !(await sharedTermsPass(integrationWorktree, anchor.binder));
    await this.#checkpoint("post-wave-checks-complete");
    if (!failure) {
      const successTag = `refs/tags/karta/${anchor.binder}/wave-${anchor.wave}`;
      await git(integrationWorktree, [
        "update-ref",
        successTag,
        tip,
        await nullObjectId(integrationWorktree),
      ]);
      await this.#checkpoint("success-tag-updated");
      return {
        schema: "karta-wave-finalization-v1",
        status: "passed",
        anchor,
        tip,
        successTag,
        message: "Post-wave floor and shared-term checks passed; success tag written.",
      };
    }

    await git(integrationWorktree, ["read-tree", "--reset", "-u", anchor.base]);
    await this.#checkpoint("rollback-worktree-prepared");
    const commands = [
      `update refs/heads/karta/${anchor.binder}/integration ${anchor.base} ${tip}`,
    ];
    for (const integration of integrations.filter((value) => value.status === "integrated")) {
      if (!integration.mergeCommit) throw new Error("Karta rollback lacks an integrated merge commit");
      commands.push(
        `delete refs/karta/${anchor.binder}/item-${integration.item}/done ${integration.mergeCommit}`,
      );
      if (integration.accepted) {
        commands.push(
          `delete refs/karta/${anchor.binder}/item-${integration.item}/accepted ${integration.itemTip}`,
          `create refs/karta/${anchor.binder}/item-${integration.item}/failed ${integration.itemTip}`,
        );
      } else {
        commands.push(
          `delete refs/karta/${anchor.binder}/item-${integration.item}/built ${integration.itemTip}`,
        );
      }
    }
    try {
      await updateRefsTransaction(integrationWorktree, commands);
    } catch (error) {
      await git(integrationWorktree, ["read-tree", "--reset", "-u", tip]);
      throw error;
    }
    await this.#checkpoint("rollback-refs-committed");
    const rollbackTag = `refs/tags/karta/${anchor.binder}/wave-${anchor.wave}-rolled-back`;
    await git(integrationWorktree, [
      "update-ref",
      rollbackTag,
      anchor.base,
      await nullObjectId(integrationWorktree),
    ]);
    await this.#checkpoint("rollback-tag-updated");
    const branchTip = await git(integrationWorktree, ["rev-parse", "HEAD"]);
    const indexTree = await git(integrationWorktree, ["write-tree"]);
    const baseTree = await git(integrationWorktree, ["rev-parse", `${anchor.base}^{tree}`]);
    if (branchTip !== anchor.base || indexTree !== baseTree) {
      throw new Error("Karta wave rollback did not restore the exact base state");
    }
    return {
      schema: "karta-wave-finalization-v1",
      status: "rolled-back",
      anchor,
      tip: anchor.base,
      message: "Post-wave validation failed; integration and item completion refs were rolled back exactly.",
    };
  }
}
