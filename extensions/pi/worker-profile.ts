import { createHash } from "node:crypto";
import { existsSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import {
  createBashToolDefinition,
  createEditToolDefinition,
  createReadToolDefinition,
  createWriteToolDefinition,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { loadKartaRole, type KartaRoleDefinition } from "./role-catalog.ts";
import type { WorkerProjectInstruction } from "./worker-instructions.ts";

const WORKER_PROFILE_VERSION = "karta-build-worker-profile-v2";

type AnyToolDefinition = ToolDefinition<any, any, any>;

export interface BuildWorkerCapabilityProfile {
  version: typeof WORKER_PROFILE_VERSION;
  role: KartaRoleDefinition;
  worktree: string;
  branch: string;
  instructions: WorkerProjectInstruction[];
  tools: AnyToolDefinition[];
  toolNames: string[];
  profileHash: string;
}

function inside(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function physicalAnchor(path: string): string {
  let current = path;
  while (!existsSync(current)) {
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return realpathSync(current);
}

export function requireWorktreePath(worktree: string, requested: string): string {
  if (!requested || requested.includes("\0")) throw new Error("Karta worker path is empty or invalid");
  const root = realpathSync(worktree);
  const lexical = resolve(root, requested);
  if (!inside(root, lexical)) throw new Error(`Karta worker path escapes its worktree: ${requested}`);
  const physical = physicalAnchor(lexical);
  if (!inside(root, physical)) {
    throw new Error(`Karta worker path resolves through a symlink outside its worktree: ${requested}`);
  }
  return lexical;
}

function confineFileTool(tool: AnyToolDefinition, worktree: string): AnyToolDefinition {
  const execute = tool.execute.bind(tool);
  return {
    ...tool,
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      const path = (params as { path?: unknown }).path;
      if (typeof path !== "string") throw new Error(`Karta worker tool '${tool.name}' requires a path`);
      const resolved = requireWorktreePath(worktree, path);
      const repoPath = relative(realpathSync(worktree), resolved).split("\\").join("/");
      if (repoPath === ".git" || repoPath.startsWith(".git/")) {
        throw new Error(`Karta worker file tools cannot access Git administration paths: ${path}`);
      }
      if (
        ["write", "edit"].includes(tool.name) &&
        (repoPath === ".karta" || repoPath.startsWith(".karta/"))
      ) {
        throw new Error(`Karta worker file tools cannot mutate host-owned Karta state: ${path}`);
      }
      return execute(toolCallId, params, signal, onUpdate, ctx);
    },
  };
}

export function createBuildWorkerCapabilityProfile(
  worktree: string,
  branch: string,
  instructions: WorkerProjectInstruction[] = [],
): BuildWorkerCapabilityProfile {
  const root = realpathSync(worktree);
  const role = loadKartaRole("build-worker");
  const tools: AnyToolDefinition[] = [
    confineFileTool(createReadToolDefinition(root), root),
    confineFileTool(createWriteToolDefinition(root), root),
    confineFileTool(createEditToolDefinition(root), root),
    {
      ...createBashToolDefinition(root),
      description:
        "Run a trusted project command with the assigned worktree as its initial cwd. Bash is high authority and is not confined by the worktree; do not access or mutate paths outside it.",
    } as AnyToolDefinition,
  ];
  const toolNames = tools.map((tool) => tool.name);
  const profileHash = createHash("sha256")
    .update(
      JSON.stringify({
        version: WORKER_PROFILE_VERSION,
        roleDefinitionHash: role.definitionHash,
        worktree: root,
        branch,
        instructions: instructions.map(({ path, blob, sha256 }) => ({ path, blob, sha256 })),
        tools: toolNames,
      }),
    )
    .digest("hex");
  return {
    version: WORKER_PROFILE_VERSION,
    role,
    worktree: root,
    branch,
    instructions: instructions.map((instruction) => ({ ...instruction })),
    tools,
    toolNames,
    profileHash,
  };
}
