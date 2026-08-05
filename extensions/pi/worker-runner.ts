import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, createWorkerChildSession, type ChildRuntimeReport } from "./child-runtime.ts";
import {
  createBuildWorkerCapabilityProfile,
  type BuildWorkerCapabilityProfile,
} from "./worker-profile.ts";

const WORKER_SCHEMA = "karta-worker-result-v1";

export type KartaWorkerOutcome = "ready" | "no-change" | "blocked";

export interface KartaWorkerResult {
  schema: typeof WORKER_SCHEMA;
  role: "build-worker";
  binder: string;
  item: string;
  roleDefinitionHash: string;
  profileHash: string;
  outcome: KartaWorkerOutcome;
  summary: string;
  runtime: ChildRuntimeReport;
}

export interface BuildWorkerInvocation {
  ctx: ExtensionContext;
  registry: ChildRegistry;
  profile: BuildWorkerCapabilityProfile;
  systemPrompt: string;
  userPrompt: string;
}

export type BuildWorkerModelInvoker = (
  invocation: BuildWorkerInvocation,
) => Promise<{ text: string; runtime: ChildRuntimeReport }>;

async function invokeBuildWorker(
  invocation: BuildWorkerInvocation,
): Promise<{ text: string; runtime: ChildRuntimeReport }> {
  const { session, report } = await createWorkerChildSession(
    invocation.ctx,
    invocation.systemPrompt,
    invocation.profile.tools,
    invocation.profile.worktree,
  );
  invocation.registry.add(session, {
    cwd: invocation.profile.worktree,
    role: "build-worker",
    label: invocation.profile.branch,
  });
  const abort = () => void session.abort();
  invocation.ctx.signal?.addEventListener("abort", abort, { once: true });
  try {
    await session.prompt(invocation.userPrompt);
    return { text: session.getLastAssistantText() ?? "", runtime: report };
  } finally {
    invocation.ctx.signal?.removeEventListener("abort", abort);
    invocation.registry.delete(session);
    session.dispose();
  }
}

function exactKeys(value: Record<string, unknown>, keys: string[]): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error("Malformed Karta worker result: unexpected keys");
  }
}

function parseWorkerResult(
  text: string,
  binder: string,
  item: string,
  profile: BuildWorkerCapabilityProfile,
  runtime: ChildRuntimeReport,
): KartaWorkerResult {
  let value: unknown;
  try {
    value = JSON.parse(text.trim());
  } catch {
    throw new Error("Karta build worker returned malformed JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Karta build worker returned a non-object result");
  }
  const result = value as Record<string, unknown>;
  exactKeys(result, [
    "schema",
    "role",
    "binder",
    "item",
    "roleDefinitionHash",
    "profileHash",
    "outcome",
    "summary",
  ]);
  if (
    result.schema !== WORKER_SCHEMA ||
    result.role !== "build-worker" ||
    result.binder !== binder ||
    result.item !== item ||
    result.roleDefinitionHash !== profile.role.definitionHash ||
    result.profileHash !== profile.profileHash ||
    !["ready", "no-change", "blocked"].includes(String(result.outcome)) ||
    typeof result.summary !== "string" ||
    !result.summary.trim() ||
    result.summary.length > 2000
  ) {
    throw new Error("Malformed or stale Karta worker result envelope");
  }
  return { ...(result as Omit<KartaWorkerResult, "runtime">), runtime };
}

function workerSystemPrompt(profile: BuildWorkerCapabilityProfile): string {
  return `${profile.role.prompt}

## Pi build-worker execution contract — authoritative

You are an isolated implementation worker in exactly one assigned Git worktree. You have only ${profile.toolNames.join(", ")}. Read project instructions and relevant code before editing. Bash is trusted high authority, not a sandbox: keep every command and mutation inside the assigned worktree.

The host owns staging, secret scanning, acceptance commands, gates, commits, tags, merges, and Karta refs. Do not run git add, git commit, git tag, git merge, git reset, git checkout, git switch, git worktree, or git update-ref. Do not edit .karta/binders or .karta/roundtable. Implement and self-check the requested item, but leave all candidate changes uncommitted for the host.

Return exactly one JSON object and no prose using this envelope:
{"schema":"${WORKER_SCHEMA}","role":"build-worker","binder":"<bound binder>","item":"<bound item>","roleDefinitionHash":"${profile.role.definitionHash}","profileHash":"${profile.profileHash}","outcome":"ready|no-change|blocked","summary":"plain-language result"}`;
}

export class KartaBuildWorkerRunner {
  readonly #registry: ChildRegistry;
  readonly #invoke: BuildWorkerModelInvoker;

  constructor(registry: ChildRegistry, invoke: BuildWorkerModelInvoker = invokeBuildWorker) {
    this.#registry = registry;
    this.#invoke = invoke;
  }

  async run(
    ctx: ExtensionContext,
    worktree: string,
    branch: string,
    binder: string,
    item: string,
    assignment: Record<string, unknown>,
    feedback: unknown[] = [],
  ): Promise<KartaWorkerResult> {
    const profile = createBuildWorkerCapabilityProfile(worktree, branch);
    const response = await this.#invoke({
      ctx,
      registry: this.#registry,
      profile,
      systemPrompt: workerSystemPrompt(profile),
      userPrompt: JSON.stringify({
        binder,
        item,
        assignment,
        feedback,
        instruction:
          "Implement this assignment in the current worktree. Obey the host-owned finalization boundary.",
      }),
    });
    return parseWorkerResult(response.text, binder, item, profile, response.runtime);
  }
}
