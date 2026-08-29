import { execFile } from "node:child_process";
import { access, mkdir, readFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { KartaBuildItemResult, KartaBuildItemRunner } from "./build-runner.ts";
import type { KartaCheckPlanEntry } from "./check-convergence.ts";
import {
  validateRecoveredCompanionCommit,
  type KartaCompanionResult,
  type ProcessContext,
} from "./companion-runner.ts";
import type { DispatchLockLease, DispatchLockManager } from "./dispatch-lock.ts";
import { deriveItemGitState } from "./git-state.ts";
import type { KartaIntegrationResult, KartaIntegrationRunner } from "./integration-runner.ts";
import { KartaProcessManager } from "./process-manager.ts";
import type {
  KartaWaveFinalizationResult,
  KartaWaveRunner,
} from "./wave-runner.ts";
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
  sme: string[];
  work_items: DeliveryItem[];
}

export interface KartaDeliveryWave {
  wave: number;
  items: string[];
  builds: KartaBuildItemResult[];
  integrations: KartaIntegrationResult[];
  finalization?: KartaWaveFinalizationResult;
}

export interface KartaDeliveryResult {
  schema: "karta-delivery-v1";
  binder: string;
  status: "complete" | "blocked";
  integrationWorktree: string;
  waves: KartaDeliveryWave[];
  message: string;
  companions?: KartaCompanionResult;
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
  const sme = (value as { sme?: unknown }).sme ?? [];
  if (!Array.isArray(sme) || !sme.every((id) => typeof id === "string" && IDENTIFIER.test(id))) {
    throw new Error(`Karta binder '${binder}' has invalid sme pins`);
  }
  return { slug: binder, sme: [...new Set(sme)] as string[], work_items: items };
}

function splitNul(output: string): string[] {
  return output.split("\0").filter(Boolean).map((path) => path.replaceAll("\\", "/"));
}

function sameStrings(left: string[], right: string[]): boolean {
  return JSON.stringify([...new Set(left)].sort()) === JSON.stringify([...new Set(right)].sort());
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

interface CompanionRunner {
  finishDelivery(
    ctx: ExtensionContext,
    binder: string,
    integrationWorktree: string,
    lease: DispatchLockLease,
    processContext: { manager: KartaProcessManager; owner: { id: string; binder: string; cwd: string } },
    options: { diffBase: string; sme: string[] },
  ): Promise<KartaCompanionResult>;
}

export class KartaDeliveryRunner {
  readonly #locks: DispatchLockManager;
  readonly #processes: KartaProcessManager;
  readonly #builds: KartaBuildItemRunner;
  readonly #integrations: KartaIntegrationRunner;
  readonly #workers: KartaBuildWorkerRunner;
  readonly #waves: KartaWaveRunner;
  readonly #companions: CompanionRunner;

  constructor(
    locks: DispatchLockManager,
    processes: KartaProcessManager,
    builds: KartaBuildItemRunner,
    integrations: KartaIntegrationRunner,
    workers: KartaBuildWorkerRunner,
    waves: KartaWaveRunner,
    companions: CompanionRunner,
  ) {
    this.#locks = locks;
    this.#processes = processes;
    this.#builds = builds;
    this.#integrations = integrations;
    this.#workers = workers;
    this.#waves = waves;
    this.#companions = companions;
  }

  async #recoverableCompanionCommit(
    worktree: string,
    binder: string,
    commit: string,
    parent: string,
    subject: string,
    processContext: ProcessContext,
  ): Promise<boolean> {
    const changed = splitNul(await git(worktree, [
      "diff-tree",
      "--no-commit-id",
      "--name-only",
      "-r",
      "-z",
      parent,
      commit,
    ]));
    if (subject === `docs: gardner ${binder}`) {
      return validateRecoveredCompanionCommit({
        worktree,
        binder,
        writer: "doc-gardner",
        parent,
        commit,
        processContext,
      });
    }
    if (subject.startsWith("kaizen: ")) {
      return validateRecoveredCompanionCommit({
        worktree,
        binder,
        writer: "kaizen",
        parent,
        commit,
        processContext,
      });
    }
    if (subject !== `chore(karta): archive binder ${binder} — delivered`) return false;
    const live = `.karta/binders/${binder}.json`;
    const archived = `.karta/binders/archive/${binder}.json`;
    if (!sameStrings(changed, [live, archived])) return false;
    const [oldBlob, newBlob] = await Promise.all([
      git(worktree, ["rev-parse", `${parent}:${live}`]),
      git(worktree, ["rev-parse", `${commit}:${archived}`]),
    ]);
    return oldBlob === newBlob;
  }

  async #ensureIntegrationWorktree(
    repoRoot: string,
    binder: string,
    processContext: ProcessContext,
  ): Promise<string> {
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
      const branchExists = await git(repoRoot, ["show-ref", "--verify", "--quiet", branchRef])
        .then(() => true)
        .catch(() => false);
      await git(
        repoRoot,
        branchExists
          ? ["worktree", "add", expected, `karta/${binder}/integration`]
          : ["worktree", "add", "-b", `karta/${binder}/integration`, expected, "HEAD"],
      );
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
      const baseTags = (await git(expected, [
        "for-each-ref",
        "--format=%(objectname)",
        `refs/tags/karta/${binder}/wave-*-base`,
      ])).split("\n").filter(Boolean);
      const recoverableTrees = new Set<string>();
      if (parents[0]) recoverableTrees.add(await git(expected, ["rev-parse", `${parents[0]}^{tree}`]));
      for (const tag of baseTags) {
        recoverableTrees.add(await git(expected, ["rev-parse", `${tag}^{tree}`]));
      }
      const recoverableMerge =
        parents.length === 2 &&
        recoverableTrees.has(indexTree) &&
        subject.startsWith("[karta:merge-item-");
      const recoverableCompanion =
        parents.length === 1 &&
        indexTree === await git(expected, ["rev-parse", `${parents[0]}^{tree}`]) &&
        await this.#recoverableCompanionCommit(
          expected,
          binder,
          "HEAD",
          parents[0],
          subject,
          processContext,
        );
      if (!recoverableMerge && !recoverableCompanion) {
        throw new Error("Karta integration index differs from HEAD outside a recoverable ref-first transaction");
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

  async #deliveryBase(repoRoot: string, binder: string): Promise<string> {
    // The earliest wave base is the delivery's start. Sort the wave-*-base tags by
    // wave NUMBER, not refname — lexical order puts wave-10-base before wave-2-base.
    const bases = (await git(repoRoot, [
      "for-each-ref",
      "--format=%(refname) %(objectname)",
      `refs/tags/karta/${binder}/wave-*-base`,
    ])).split("\n").filter(Boolean).map((line) => {
      const [ref, object] = line.split(" ");
      return { object, wave: Number(ref.match(/\/wave-(\d+)-base$/)?.[1] ?? Infinity) };
    }).sort((left, right) => left.wave - right.wave);
    if (bases[0]) return bases[0].object;
    // Degenerate fallback (no wave tags): the integration branch's root. Take the
    // first of a multi-root history rather than crashing the delivery.
    const roots = (await git(repoRoot, [
      "rev-list",
      "--max-parents=0",
      `refs/heads/karta/${binder}/integration`,
    ])).split("\n").filter(Boolean);
    if (roots.length === 0) throw new Error("Karta cannot derive one delivery diff base");
    return roots[0];
  }

  async #pendingWaveAnchor(
    repoRoot: string,
    binder: string,
  ): Promise<{ binder: string; wave: number; base: string; baseTag: string } | undefined> {
    const refs = (await git(repoRoot, [
      "for-each-ref",
      "--format=%(refname) %(objectname)",
      `refs/tags/karta/${binder}/wave-*-base`,
    ])).split("\n").filter(Boolean).map((line) => {
      const [ref, object] = line.split(" ");
      return {
        ref,
        object,
        wave: Number(ref.match(/\/wave-(\d+)-base$/)?.[1] ?? 0),
      };
    }).sort((left, right) => right.wave - left.wave);
    const currentTip = await git(repoRoot, ["rev-parse", `refs/heads/karta/${binder}/integration`]);
    for (const candidate of refs) {
      const terminalRefs = [
        `refs/tags/karta/${binder}/wave-${candidate.wave}`,
        `refs/tags/karta/${binder}/wave-${candidate.wave}-rolled-back`,
      ];
      const terminal = await Promise.all(terminalRefs.map((ref) =>
        git(repoRoot, ["show-ref", "--verify", "--quiet", ref])
          .then(() => true)
          .catch(() => false),
      ));
      if (!terminal.some(Boolean) && candidate.object !== currentTip) {
        return {
          binder,
          wave: candidate.wave,
          base: candidate.object,
          baseTag: candidate.ref,
        };
      }
    }
    return undefined;
  }

  async #discoverFloorChecks(
    ctx: ExtensionContext,
    repoRoot: string,
    binder: string,
    item: DeliveryItem,
    ownerId: string,
    mode: "recover-committed" | "recover-merged",
  ): Promise<KartaCheckPlanEntry[]> {
    const worktree = await this.#ensureItemWorktree(repoRoot, binder, item.id);
    const discovery = await this.#workers.run(
      ctx,
      worktree,
      `karta/${binder}/item-${item.id}`,
      binder,
      item.id,
      item,
      [],
      ownerId,
      mode,
    );
    if (discovery.outcome === "blocked") {
      throw new Error(`Karta floor discovery was blocked for item '${item.id}'`);
    }
    return discovery.checks.map((check): KartaCheckPlanEntry => ({
      ...check,
      purpose: "floor",
    }));
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
      const integrationWorktree = await this.#ensureIntegrationWorktree(
        repoRoot,
        binder,
        { manager: this.#processes, owner },
      );
      const liveBinder = join(integrationWorktree, ".karta", "binders", `${binder}.json`);
      const archivedBinder = join(integrationWorktree, ".karta", "binders", "archive", `${binder}.json`);
      const archived = !await exists(liveBinder);
      if (archived && !await exists(archivedBinder)) {
        throw new Error(`Karta binder '${binder}' is missing from live and archive paths`);
      }
      const document = parseBinder(await readFile(archived ? archivedBinder : liveBinder, "utf8"), binder);
      const waves: KartaDeliveryWave[] = [];
      const existingTags = await git(repoRoot, [
        "for-each-ref",
        "--format=%(refname)",
        `refs/tags/karta/${binder}/wave-*`,
      ]);
      let waveNumber = Math.max(
        0,
        ...existingTags.split("\n").map((ref) =>
          Number(ref.match(/\/wave-(\d+)(?:-base)?$/)?.[1] ?? 0),
        ),
      ) + 1;
      for (let pass = 1; pass <= document.work_items.length * 4 + 4; pass += 1) {
        const states = new Map(
          await Promise.all(document.work_items.map(async (item) => [
            item.id,
            await deriveItemGitState(repoRoot, binder, item.id),
          ] as const)),
        );
        // Recover a pending wave anchor BEFORE deriving a new frontier, whether or not
        // every item is done: starting a fresh wave over an unfinalized anchor lets the
        // later all-done recovery roll that anchor back and destroy the newer wave's work.
        const pending = await this.#pendingWaveAnchor(repoRoot, binder);
        if (
          document.work_items.every((item) => states.get(item.id)?.state === "done") || pending
        ) {
          if (pending) {
            const landed = new Set((await git(repoRoot, [
              "rev-list",
              "--first-parent",
              `${pending.base}..refs/heads/karta/${binder}/integration`,
            ])).split("\n").filter(Boolean));
            const integrations: KartaIntegrationResult[] = [];
            const checks: KartaCheckPlanEntry[] = [];
            for (const item of document.work_items) {
              const state = states.get(item.id);
              if (!state?.refs.done || !state.itemTip || !landed.has(state.refs.done)) continue;
              const parents = (await git(repoRoot, [
                "rev-list",
                "--parents",
                "-n",
                "1",
                state.refs.done,
              ])).split(/\s+/).slice(1);
              integrations.push({
                schema: "karta-integration-item-v1",
                binder,
                item: item.id,
                status: "integrated",
                base: parents[0],
                itemTip: state.itemTip,
                mergeCommit: state.refs.done,
                accepted: Boolean(state.refs.accepted),
                message: "Recovered pending wave integration.",
              });
              checks.push(...await this.#discoverFloorChecks(
                ctx,
                repoRoot,
                binder,
                item,
                owner.id,
                "recover-merged",
              ));
            }
            if (integrations.length === 0) {
              return {
                schema: "karta-delivery-v1",
                binder,
                status: "blocked",
                integrationWorktree,
                waves,
                message: "Pending wave tag has no recoverable first-parent item merges.",
              };
            }
            const waveResult: KartaDeliveryWave = {
              wave: pending.wave,
              items: integrations.map((integration) => integration.item),
              builds: [],
              integrations,
            };
            waves.push(waveResult);
            const finalization = await this.#waves.finish(
              ctx,
              pending,
              integrationWorktree,
              lease,
              integrations,
              checks,
              { manager: this.#processes, owner },
            );
            waveResult.finalization = finalization;
            if (finalization.status !== "passed") {
              return {
                schema: "karta-delivery-v1",
                binder,
                status: "blocked",
                integrationWorktree,
                waves,
                message: "Recovered pending wave failed validation and was rolled back.",
              };
            }
            continue;
          }
          if (archived) {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "complete",
              integrationWorktree,
              waves,
              message: "Every binder item is durably done and the binder is archived.",
            };
          }
          const companions = await this.#companions.finishDelivery(
            ctx,
            binder,
            integrationWorktree,
            lease,
            { manager: this.#processes, owner },
            { diffBase: await this.#deliveryBase(repoRoot, binder), sme: document.sme },
          );
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "complete",
            integrationWorktree,
            waves,
            message: "Every binder item is durably done, companion writers finished, and the binder is archived.",
            companions,
          };
        }
        const inconsistent = document.work_items.find(
          (item) => states.get(item.id)?.state === "inconsistent",
        );
        if (inconsistent) {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "blocked",
            integrationWorktree,
            waves,
            message: `Item '${inconsistent.id}' has contradictory Git state requiring manual recovery.`,
          };
        }
        const acceptPending = document.work_items.find((item) =>
          ["accept-merge-pending", "accept-ref-pending"].includes(states.get(item.id)?.state ?? ""),
        );
        if (acceptPending) {
          const checks = await this.#discoverFloorChecks(
            ctx,
            repoRoot,
            binder,
            acceptPending,
            owner.id,
            "recover-merged",
          );
          const recovered = await this.#integrations.recoverAccepted(
            ctx,
            binder,
            acceptPending.id,
            integrationWorktree,
            lease,
            checks,
            { manager: this.#processes, owner },
          );
          const anchor = await this.#pendingWaveAnchor(repoRoot, binder);
          if (!anchor) {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: "Accepted merge recovered, but its pending wave base tag is missing.",
            };
          }
          const waveResult: KartaDeliveryWave = {
            wave: anchor.wave,
            items: [acceptPending.id],
            builds: [],
            integrations: [recovered],
          };
          waves.push(waveResult);
          if (recovered.status !== "integrated") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: "Interrupted human-accepted merge could not be recovered.",
            };
          }
          const finalization = await this.#waves.finish(
            ctx,
            anchor,
            integrationWorktree,
            lease,
            [recovered],
            checks,
            { manager: this.#processes, owner },
          );
          waveResult.finalization = finalization;
          if (finalization.status !== "passed") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: "Recovered accepted merge failed post-wave validation and was rolled back.",
            };
          }
          continue;
        }
        const failed = document.work_items.find((item) => states.get(item.id)?.state === "failed");
        if (failed) {
          if (!ctx.hasUI) {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: `Item '${failed.id}' is failed; rerun interactively to choose fix, accept, or defer.`,
            };
          }
          const choice = await ctx.ui.select(
            `Karta item '${failed.id}' halted`,
            ["Fix and rerun", "Accept exact current findings", "Defer and stop delivery"],
          );
          if (choice === "Fix and rerun") {
            const itemTip = states.get(failed.id)?.itemTip;
            if (!itemTip) throw new Error("Karta failed item has no item tip");
            await git(repoRoot, [
              "update-ref",
              "-d",
              `refs/karta/${binder}/item-${failed.id}/failed`,
              itemTip,
            ]);
            continue;
          }
          if (choice !== "Accept exact current findings") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: `Item '${failed.id}' remains deferred with its failed ref intact.`,
            };
          }
          const checks = await this.#discoverFloorChecks(
            ctx,
            repoRoot,
            binder,
            failed,
            owner.id,
            "recover-committed",
          );
          if ((await deriveItemGitState(repoRoot, binder, failed.id)).state !== "failed") {
            throw new Error("Karta failed item changed during human-accept floor discovery");
          }
          const anchor = await this.#waves.start(binder, waveNumber, integrationWorktree, lease);
          const integrated = await this.#integrations.integrate(
            ctx,
            binder,
            failed.id,
            integrationWorktree,
            lease,
            checks,
            { manager: this.#processes, owner },
            {
              authorize: async (findings) => {
                const details = findings.length > 0
                  ? findings.map((finding) =>
                      `${finding.code}${finding.path ? ` — ${finding.path}${finding.line ? `:${finding.line}` : ""}` : ""}: ${finding.message}`,
                    ).join("\n")
                  : "Fresh acceptance and safety review now pass; record why this failed checkpoint is being accepted.";
                const confirmed = await ctx.ui.confirm(
                  `Accept exact findings for '${failed.id}'?`,
                  details,
                );
                if (!confirmed) return undefined;
                const reason = await ctx.ui.input(
                  "Human acceptance reason",
                  "Why is accepting this exact gap appropriate?",
                );
                return reason?.trim() ? { reason } : undefined;
              },
            },
          );
          const waveResult: KartaDeliveryWave = {
            wave: waveNumber,
            items: [failed.id],
            builds: [],
            integrations: [integrated],
          };
          waves.push(waveResult);
          const finalization = await this.#waves.finish(
            ctx,
            anchor,
            integrationWorktree,
            lease,
            [integrated],
            checks,
            { manager: this.#processes, owner },
          );
          waveResult.finalization = finalization;
          if (integrated.status !== "integrated" || finalization.status !== "passed") {
            return {
              schema: "karta-delivery-v1",
              binder,
              status: "blocked",
              integrationWorktree,
              waves,
              message: `Human acceptance for '${failed.id}' was cancelled, blocked, or rolled back.`,
            };
          }
          waveNumber += 1;
          continue;
        }
        const ready = document.work_items.filter((item) => {
          const state = states.get(item.id)?.state;
          return state !== "done" && item.depends_on.every((dependency) => states.get(dependency)?.state === "done");
        });
        const batch = collisionBatch(ready);
        const waveMates = batch.map((waveItem) => waveItem.id);
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
          batch.map((item) => this.#builds.runWithLease(ctx, binder, item.id, lease, owner, waveMates)),
        );
        const waveResult: KartaDeliveryWave = {
          wave: waveNumber,
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
        const anchor = await this.#waves.start(
          binder,
          waveNumber,
          integrationWorktree,
          lease,
        );
        const waveChecks: KartaCheckPlanEntry[] = builds.flatMap((build) =>
          build.worker?.checks.map((check) => ({ ...check, purpose: "floor" as const })) ?? [],
        );
        let integrationFailed = false;
        for (const [index, item] of batch.entries()) {
          let state = await deriveItemGitState(repoRoot, binder, item.id);
          if (state.state === "done") continue;
          if (state.state === "merged-unmarked") {
            // Serial recovery after the wave barrier: nothing is building concurrently,
            // so only this item's own worktree may churn.
            await this.#builds.runWithLease(ctx, binder, item.id, lease, owner, [item.id]);
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
            waveChecks.push(...checks);
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
            integrationFailed = true;
            break;
          }
        }
        const waveFinalization = await this.#waves.finish(
          ctx,
          anchor,
          integrationWorktree,
          lease,
          waveResult.integrations,
          waveChecks,
          { manager: this.#processes, owner },
        );
        waveResult.finalization = waveFinalization;
        if (integrationFailed || waveFinalization.status === "rolled-back") {
          return {
            schema: "karta-delivery-v1",
            binder,
            status: "blocked",
            integrationWorktree,
            waves,
            message: integrationFailed
              ? "A proposed-tree integration failed; the partial wave was rolled back."
              : "Post-wave validation failed; the wave was rolled back.",
          };
        }
        waveNumber += 1;
      }
      throw new Error("Karta delivery exceeded its deterministic orchestration bound");
    } finally {
      try {
        await this.#processes.stopOwner(owner);
      } finally {
        await this.#locks.release(lease);
      }
    }
  }
}
