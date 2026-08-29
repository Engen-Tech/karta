import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, createWorkerChildSession, type ChildRuntimeReport } from "./child-runtime.ts";
import { parseJsonEnvelope, promptForJsonEnvelope } from "./child-envelope.ts";
import {
  attestWorkerAuthority,
  snapshotWorkerAuthority,
  type WorkerAuthorityAttestation,
  type WorkerAuthoritySnapshot,
} from "./worker-attestation.ts";
import {
  loadWorkerProjectInstructions,
  type WorkerProjectInstruction,
} from "./worker-instructions.ts";
import {
  createBuildWorkerCapabilityProfile,
  type BuildWorkerCapabilityProfile,
} from "./worker-profile.ts";

const WORKER_SCHEMA = "karta-worker-result-v2";

export type KartaWorkerOutcome = "ready" | "no-change" | "blocked";

export interface KartaWorkerCheckProposal {
  id: string;
  command: string;
  cwd: string;
}

export interface KartaWorkerResult {
  schema: typeof WORKER_SCHEMA;
  role: "build-worker";
  binder: string;
  item: string;
  roleDefinitionHash: string;
  profileHash: string;
  outcome: KartaWorkerOutcome;
  summary: string;
  checks: KartaWorkerCheckProposal[];
  runtime: ChildRuntimeReport;
  attestation: WorkerAuthorityAttestation;
}

export interface BuildWorkerInvocation {
  ctx: ExtensionContext;
  registry: ChildRegistry;
  profile: BuildWorkerCapabilityProfile;
  systemPrompt: string;
  userPrompt: string;
  parentId?: string;
}

export type BuildWorkerModelInvoker = (
  invocation: BuildWorkerInvocation,
) => Promise<{ text: string; runtime: ChildRuntimeReport }>;

const WORKER_ENVELOPE_REPAIR_PROMPT =
  'Your previous message was not the required result. Reply now with ONLY the single JSON object envelope described in your instructions (schema "karta-worker-result-v2") — no prose, no headings, no code fence, and nothing before or after the object.';

function looksLikeWorkerEnvelope(text: string): boolean {
  try {
    const value = parseWorkerEnvelopeJson(text);
    return (
      Boolean(value) &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      (value as Record<string, unknown>).schema === WORKER_SCHEMA
    );
  } catch {
    return false;
  }
}

export function promptWorkerForEnvelope(
  session: Parameters<typeof promptForJsonEnvelope>[0],
  userPrompt: string,
): Promise<string> {
  return promptForJsonEnvelope(
    session,
    userPrompt,
    looksLikeWorkerEnvelope,
    WORKER_ENVELOPE_REPAIR_PROMPT,
  );
}

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
    parentId: invocation.parentId,
  });
  const abort = () => void session.abort();
  invocation.ctx.signal?.addEventListener("abort", abort, { once: true });
  if (invocation.ctx.signal?.aborted) abort();
  try {
    const text = await promptWorkerForEnvelope(session, invocation.userPrompt);
    return { text, runtime: report };
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

function parseWorkerEnvelopeJson(text: string): unknown {
  return parseJsonEnvelope(text, "build worker");
}

function parseWorkerResult(
  text: string,
  binder: string,
  item: string,
  profile: BuildWorkerCapabilityProfile,
  runtime: ChildRuntimeReport,
): Omit<KartaWorkerResult, "attestation"> {
  const value = parseWorkerEnvelopeJson(text);
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
    "checks",
  ]);
  if (
    result.schema !== WORKER_SCHEMA ||
    result.role !== "build-worker" ||
    result.binder !== binder ||
    result.item !== item ||
    result.roleDefinitionHash !== profile.role.definitionHash ||
    result.profileHash !== profile.profileHash ||
    !["ready", "no-change", "blocked"].includes(String(result.outcome)) ||
    !Array.isArray(result.checks) ||
    result.checks.length > 16 ||
    typeof result.summary !== "string" ||
    !result.summary.trim() ||
    result.summary.length > 2000
  ) {
    throw new Error("Malformed or stale Karta worker result envelope");
  }
  const checkIds = new Set<string>();
  for (const proposal of result.checks) {
    if (!proposal || typeof proposal !== "object" || Array.isArray(proposal)) {
      throw new Error("Malformed Karta worker check proposal");
    }
    const check = proposal as Record<string, unknown>;
    exactKeys(check, ["id", "command", "cwd"]);
    const cwd = typeof check.cwd === "string" ? check.cwd.replaceAll("\\", "/") : "";
    if (
      typeof check.id !== "string" ||
      !/^[a-z][a-z0-9-]{0,63}$/.test(check.id) ||
      check.id === "oracle" ||
      checkIds.has(check.id) ||
      typeof check.command !== "string" ||
      !check.command.trim() ||
      check.command.length > 16 * 1024 ||
      !cwd ||
      cwd.startsWith("/") ||
      cwd.split("/").includes("..")
    ) {
      throw new Error("Malformed Karta worker check proposal");
    }
    checkIds.add(check.id);
  }
  return {
    ...(result as Omit<KartaWorkerResult, "runtime" | "attestation">),
    runtime,
  } as Omit<KartaWorkerResult, "attestation">;
}

function workerSystemPrompt(profile: BuildWorkerCapabilityProfile): string {
  const projectInstructions = profile.instructions.length === 0
    ? "No committed AGENTS.md or CLAUDE.md files were present at the assigned item tip."
    : profile.instructions
        .map(
          (instruction) =>
            `### ${instruction.path} (blob ${instruction.blob}, sha256 ${instruction.sha256})\n\n${instruction.content}`,
        )
        .join("\n\n");
  return `${profile.role.prompt}

## Committed project instructions

These instructions are explicitly loaded from the assigned item tip. Follow the applicable directory-scoped instruction for every path you touch unless it conflicts with the authoritative Pi execution contract below.

${projectInstructions}

## Pi build-worker execution contract — authoritative

You are an isolated implementation worker in exactly one assigned Git worktree. You have only ${profile.toolNames.join(", ")}. Read project instructions and relevant code before editing. Bash is trusted high authority, not a sandbox: keep every command and mutation inside the assigned worktree.

The host owns staging, secret scanning, acceptance commands, gates, commits, tags, merges, and Karta refs. Do not run git add, git commit, git tag, git merge, git reset, git checkout, git switch, git worktree, or git update-ref. Do not edit .karta/binders or .karta/roundtable. Implement and self-check the requested item, but leave all candidate changes uncommitted for the host.

Return exactly one JSON object and no prose using this envelope:
{"schema":"${WORKER_SCHEMA}","role":"build-worker","binder":"<bound binder>","item":"<bound item>","roleDefinitionHash":"${profile.role.definitionHash}","profileHash":"${profile.profileHash}","outcome":"ready|no-change|blocked","summary":"plain-language result","checks":[{"id":"stable-floor-id","command":"exact command to rerun","cwd":"repo-relative cwd"}]}

List every final lint, test, type-check, build, or other project-floor command you used and the host must rerun. Do not include the binder oracle; the host adds it. This list is an untrusted proposal, never proof that a command passed.`;
}

export type WorkerInstructionLoader = (
  worktree: string,
) => Promise<WorkerProjectInstruction[]>;

interface WorkerAuthorityInspector {
  snapshot(
    worktree: string,
    binder?: string,
    waveMates?: readonly string[],
  ): Promise<WorkerAuthoritySnapshot>;
  attest(before: WorkerAuthoritySnapshot, after: WorkerAuthoritySnapshot): WorkerAuthorityAttestation;
}

const defaultAuthorityInspector: WorkerAuthorityInspector = {
  snapshot: snapshotWorkerAuthority,
  attest: attestWorkerAuthority,
};

export class KartaBuildWorkerRunner {
  readonly #registry: ChildRegistry;
  readonly #invoke: BuildWorkerModelInvoker;
  readonly #loadInstructions: WorkerInstructionLoader;
  readonly #authority: WorkerAuthorityInspector;

  constructor(
    registry: ChildRegistry,
    invoke: BuildWorkerModelInvoker = invokeBuildWorker,
    loadInstructions: WorkerInstructionLoader = loadWorkerProjectInstructions,
    authority: WorkerAuthorityInspector = defaultAuthorityInspector,
  ) {
    this.#registry = registry;
    this.#invoke = invoke;
    this.#loadInstructions = loadInstructions;
    this.#authority = authority;
  }

  async run(
    ctx: ExtensionContext,
    worktree: string,
    branch: string,
    binder: string,
    item: string,
    assignment: Record<string, unknown>,
    feedback: unknown[] = [],
    parentId?: string,
    mode: "implement" | "recover-committed" | "recover-merged" = "implement",
    onFirstMutation?: () => Promise<void> | void,
    waveMates: readonly string[] = [],
  ): Promise<KartaWorkerResult> {
    const instructions = await this.#loadInstructions(worktree);
    const profile = createBuildWorkerCapabilityProfile(
      worktree,
      branch,
      instructions,
      onFirstMutation,
    );
    const before = await this.#authority.snapshot(worktree, binder, waveMates);
    let response: { text: string; runtime: ChildRuntimeReport } | undefined;
    let invocationError: unknown;
    try {
      response = await this.#invoke({
        ctx,
        registry: this.#registry,
        profile,
        parentId,
        systemPrompt: workerSystemPrompt(profile),
        userPrompt: JSON.stringify({
        binder,
        item,
        assignment,
        feedback,
        mode,
        instruction:
          mode === "recover-committed"
            ? "The item tip was committed before its completion ref. Do not edit files. Inspect and self-check the committed implementation, then return the complete floor-command proposal for host revalidation."
            : mode === "recover-merged"
              ? "The item was merged before its done ref was written. Do not edit files. Inspect the project and return the complete floor-command proposal for host revalidation of the landed merge."
              : "Implement this assignment in the current worktree. Obey the host-owned finalization boundary.",
        }),
      });
    } catch (error) {
      invocationError = error;
    }
    const after = await this.#authority.snapshot(worktree, binder, waveMates);
    const attestation = this.#authority.attest(before, after);
    if (!attestation.passed) {
      throw new Error(`Karta worker violated host authority: ${attestation.issues.join("; ")}`);
    }
    if (invocationError) throw invocationError;
    if (!response) throw new Error("Karta build worker returned no response");
    return {
      ...parseWorkerResult(response.text, binder, item, profile, response.runtime),
      attestation,
    };
  }
}
