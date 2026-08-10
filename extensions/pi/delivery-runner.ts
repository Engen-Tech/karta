import { execFile } from "node:child_process";
import { access, mkdir } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { KartaBuildItemResult, KartaBuildItemRunner } from "./build-runner.ts";
import type { KartaCheckPlanEntry } from "./check-convergence.ts";
import type { DispatchLockManager } from "./dispatch-lock.ts";
import { deriveItemGitState } from "./git-state.ts";
import type { KartaIntegrationResult, KartaIntegrationRunner } from "./integration-runner.ts";
import { KartaProcessManager } from "./process-manager.ts";
import { KartaBuildWorkerRunner } from "./worker-runner.ts";

const exec = promisify(execFile);
const IDENTIFIER = /^[a-z0-9][a-z0-9-]*$/;
const MAX_OUTPUT = 8 * 1024 * 1024;

interface DeliveryItem extends Record<string, unknown> {
  id: string;
  depends_on: string[];
  surfaces: string[];
}

interface DeliveryDocument {
  slug: string;
  work_items: DeliveryItem[];
}

export interface KartaDeliveryWave {
  wave: number;
  items: string[];
  builds: KartaBuildItemResult[];
  integrations: KartaIntegrationResult[];
}

export interface KartaDeliveryResult {
  schema: "karta-delivery-v1";
  binder: string;
  status: "complete" | "blocked";
  integrationWorktree: string;
  waves: KartaDeliveryWave[];
  message: string;
}

async function git(cwd: string, args: string[]): Promise<string> {
  try {
    const { stdout } = await exec("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      maxBuffer: MAX_OUTPUT,
    });
    return stdout.trim();
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    throw new Error(stderr || `git ${args[0] ?? "command"} failed during delivery`);
  }
}

async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}

function parseBinder(raw: string, binder: string): DeliveryDocument {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new Error(`Karta binder '${binder}' is not valid JSON`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Karta binder '${binder}' is not an object`);
  }
  const document = value as { slug?: unknown; work_items?: unknown };
  if (document.slug !== binder || !Array.isArray(document.work_items)) {
    throw new Error(`Karta binder '${binder}' has invalid identity or work_items`);
  }
  const ids = new Set<string>();
  const items = document.work_items.map((candidate) => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new Error(`Karta binder '${binder}' contains a malformed work item`);
    }
    const item = candidate as Record<string, unknown>;
    if (typeof item.id !== "string" || !IDENTIFIER.test(item.id) || ids.has(item.id)) {
      throw new Error(`Karta binder '${binder}' contains a duplicate or invalid item id`);
    }
    ids.add(item.id);
    const dependencies = item.depends_on ?? [];
    if (!Array.isArray(dependencies) || !dependencies.every((id) => typeof id === "string" && IDENTIFIER.test(id))) {
      throw new Error(`Karta item '${item.id}' has invalid dependencies`);
    }
    const declared = Array.isArray(item.touches)
      ? item.touches
      : Array.isArray(item.files)
        ? item.files
        : [];
    const surfaces = declared.filter((path): path is string => typeof path === "string");
    return {
      ...item,
      id: item.id,
      depends_on: [...dependencies] as string[],
      surfaces: surfaces.length > 0 ? [...new Set(surfaces)].sort() : ["*"],
    };
  });
  for (const item of items) {
    for (const dependency of item.depends_on) {
      if (!ids.has(dependency) || dependency === item.id) {
        throw new Error(`Karta item '${item.id}' has an unknown or self dependency '${dependency}'`);
      }
    }
  }
  return { slug: binder, work_items: items };
}

function worktreeMap(porcelain: string): Map<string, string> {
  const result = new Map<string, string>();
  let path: string | undefined;
  for (const line of porcelain.split("\n")) {
    if (line.startsWith("worktree ")) path = line.slice("worktree ".length);
    if (line.startsWith("branch ") && path) result.set(line.slice("branch ".length), path);
    if (!line) path = undefined;
  }
  return result;
}

function collisionBatch(items: DeliveryItem[]): DeliveryItem[] {
  const selected: DeliveryItem[] = [];
  const occupied = new Set<string>();
  for (const item of items) {
    if (item.surfaces.includes("*")) {
      if (selected.length === 0) return [item];
      continue;
    }
    if (occupied.has("*") || item.surfaces.some((surface) => occupied.has(surface))) continue;
    selected.push(item);
    item.surfaces.forEach((surface) => occupied.add(surface));
  }
  return selected;
}

export class KartaDeliveryRunner {
  readonly #locks: DispatchLockManager;
  readonly #processes: KartaProcessManager;
  readonly #builds: KartaBuildItemRunner;
  readonly #integrations: KartaIntegrationRunner;
  readonly #workers: KartaBuildWorkerRunner;

  constructor(
    locks: DispatchLockManager,
    processes: KartaProcessManager,
    builds: KartaBuildItemRunner,
    integrations: KartaIntegrationRunner,
    workers: KartaBuildWorkerRunner,
  ) {
    this.#locks = locks;
    this.#processes = processes;
    this.#builds = builds;
    this.#integrations = integrations;
    this.#workers = workers;
  }

  async #ensureIntegrationWorktree(repoRoot: string, binder: string): Promise<string> {
    const branchRef = `refs/heads/karta/${binder}/integration`;
    const root = join(dirname(repoRoot), `${basename(repoRoot)}-worktrees`);
    const expected = resolve(root, `karta-${binder}-integration`);
    await mkdir(root, { recursive: true });
    const registered = worktreeMap(await git(repoRoot, ["worktree", "list", "--porcelain"])).get(branchRef);
    if (registered && resolve(registered) !== expected) {
      throw new Error(`Karta integration branch is checked out in a foreign worktree: ${registered}`);
    }
    if (!registered) {
      if (await exists(expected)) throw new Error(`Karta refuses to clobber integration path: ${expected}`);
      await git(repoRoot, ["worktree", "add", expected, `karta/${binder}/integration`]);
    }
    const [branch, unstaged, untracked, indexTree, headTree] = await Promise.all([
      git(expected, ["branch", "--show-current"]),
      git(expected, ["diff", "--quiet", "--no-ext-diff", "--"]).then(() => true).catch(() => false),
      git(expected, ["ls-files", "--others", "--exclude-standard", "-z"]),
      git(expected, ["write-tree"]),
      git(expected, ["rev-parse", "HEAD^{tree}"]),
    ]);
    if (branch !== `karta/${binder}/integration` || !unstaged || untracked) {
      throw new Error(
        `Karta refuses to alter a dirty or foreign integration worktree (branch=${branch}, unstagedClean=${unstaged}, untracked=${Boolean(untracked)})`,
      );
    }
    if (indexTree !== headTree) {
      const parents = (await git(expected, ["rev-list", "--parents", "-n", "1", "HEAD"]))
        .split(/\s+/).slice(1);
      const subject = await git(expected, ["show", "-s", "--format=%s", "HEAD"]);
      if (
        parents.length !== 2 ||
        indexTree !== await git(expected, ["rev-parse", `${parents[0]}^{tree}`]) ||
        !subject.startsWith("[karta:merge-item-")
      ) {
        throw new Error("Karta integration index differs from HEAD outside a recoverable ref-first merge");
      }
      await git(expected, ["read-tree", "--reset", "-u", "HEAD"]);
    }
    return expected;
  }

  async #ensureItemWorktree(repoRoot: string, binder: string, item: string): Promise<string> {
    const branchRef = `refs/heads/karta/${binder}/item-${item}`;
    const registered = worktreeMap(await git(repoRoot, ["worktree", "list", "--porcelain"])).get(branchRef);
    if (registered) return resolve(registered);
    const root = join(dirname(repoRoot), `${basename(repoRoot)}-worktrees`);
    const expected = resolve(root, `karta-${binder}-item-${item}`);
    await mkdir(root, { recursive: true });
    if (await exists(expected)) throw new Error(`Karta refuses to clobber item path: ${expected}`);
    await git(repoRoot, ["worktree", "add", expected, `karta/${binder}/item-${item}`]);
    return expected;
  }

  async run(ctx: ExtensionContext, binder: string): Promise<KartaDeliveryResult> {
    const lease = await this.#locks.acquire(ctx.cwd, binder);
    let owner;
    try {
      owner = this.#processes.createBinderOwner(ctx.cwd, binder);
    } catch (error) {
      await this.#locks.release(lease);
      throw error;
    }
    try {
      const repoRoot = await git(ctx.cwd, ["rev-parse", "--show-toplevel"]);
      const integrationRef = `refs/heads/karta/${binder}/integration`;
      const document = parseBinder(
        await git(repoRoot, ["show", `${integrationRef}:.karta/binders/${binder}.json`]),
        binder,
      );
      const integrationWorktree = await this.#ensureIntegrationWorktree(repoRoot, binder);
      const waves: KartaDeliveryWave[] = [];
      for (let wave = 1; wave <= document.work_items.length * 2 + 1; wave += 1) {
        const states = new Map(
          await Promise.all(document.work_items.map(async (item) => [
            item.id,
            await deriveItemGitState(repoRoot, binder, item.id),
          ] as const)),
        );
        if (document.work_items.every((item) => states.get(item.id)?.state === "done")) {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "complete",
            integrationWorktree,
            waves,
            message: "Every binder item is durably done on the integration branch.",
          };
        }
        const halted = document.work_items.find((item) =>
          ["failed", "accept-merge-pending", "inconsistent"].includes(states.get(item.id)?.state ?? ""),
        );
        if (halted) {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "blocked",
            integrationWorktree,
            waves,
            message: `Item '${halted.id}' requires human or manual recovery before delivery can continue.`,
          };
        }
        const ready = document.work_items.filter((item) => {
          const state = states.get(item.id)?.state;
          return state !== "done" && item.depends_on.every((dependency) => states.get(dependency)?.state === "done");
        });
        const batch = collisionBatch(ready);
        if (batch.length === 0) {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "blocked",
            integrationWorktree,
            waves,
            message: "No dependency-ready item exists; the binder graph or Git state is stuck.",
          };
        }
        const builds = await Promise.all(
          batch.map((item) => this.#builds.runWithLease(ctx, binder, item.id, lease, owner)),
        );
        const waveResult: KartaDeliveryWave = {
          wave,
          items: batch.map((item) => item.id),
          builds,
          integrations: [],
        };
        waves.push(waveResult);
        const failedBuild = builds.find((build) =>
          ["failed", "blocked", "no-change"].includes(build.status),
        );
        if (failedBuild) {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "blocked",
            integrationWorktree,
            waves,
            message: `Item '${failedBuild.item}' did not produce a mergeable built checkpoint.`,
          };
        }
        for (const [index, item] of batch.entries()) {
          let state = await deriveItemGitState(repoRoot, binder, item.id);
          if (state.state === "done") continue;
          if (state.state === "merged-unmarked") {
            await this.#builds.runWithLease(ctx, binder, item.id, lease, owner);
            state = await deriveItemGitState(repoRoot, binder, item.id);
            if (state.state === "done") continue;
          }
          if (state.state !== "built") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: `Item '${item.id}' is not in a serially mergeable built state.`,
            };
          }
          let checks: KartaCheckPlanEntry[] = builds[index].worker?.checks.map((check) => ({
            ...check,
            purpose: "floor" as const,
          })) ?? [];
          if (checks.length === 0) {
            const worktree = await this.#ensureItemWorktree(repoRoot, binder, item.id);
            const discovery = await this.#workers.run(
              ctx,
              worktree,
              `karta/${binder}/item-${item.id}`,
              binder,
              item.id,
              item,
              [],
              owner.id,
              "recover-committed",
            );
            checks = discovery.checks.map((check): KartaCheckPlanEntry => ({
              ...check,
              purpose: "floor",
            }));
          }
          const integrated = await this.#integrations.integrate(
            ctx,
            binder,
            item.id,
            integrationWorktree,
            lease,
            checks,
            { manager: this.#processes, owner },
          );
          waveResult.integrations.push(integrated);
          if (integrated.status !== "integrated") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: `Item '${item.id}' failed proposed-tree integration.`,
            };
          }
        }
      }
      throw new Error("Karta delivery exceeded its deterministic wave bound");
    } finally {
      try {
        await this.#processes.stopOwner(owner);
      } finally {
        await this.#locks.release(lease);
      }
    }
  }
}
