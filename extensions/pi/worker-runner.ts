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
  // Carried so the corrective turn can validate identity on the same terms as
  // the strict parse; without them the predicate would be the looser of the two.
  binder: string;
  item: string;
  systemPrompt: string;
  userPrompt: string;
  parentId?: string;
}

export type BuildWorkerModelInvoker = (
  invocation: BuildWorkerInvocation,
) => Promise<{ text: string; runtime: ChildRuntimeReport }>;

const MAX_WORKER_SUMMARY = 2000;
const MAX_WORKER_CHECKS = 16;

const WORKER_ENVELOPE_REPAIR_PROMPT =
  'Your previous message was not the required result. Reply now with ONLY the single JSON object envelope described in your instructions (schema "karta-worker-result-v2") — no prose, no headings, no code fence, and nothing before or after the object.';

// Every rule the strict parse enforces, in one place, returning the reason the
// envelope is unacceptable or null when it is fine. The repair turn and the
// strict parse read the SAME function deliberately: when the predicate was
// looser than the parse, a worker that finished the build and then wrote an
// over-long summary was told nothing and had its whole run discarded, which is
// the opposite of what the corrective turn exists to do.
export function workerEnvelopeViolation(
  text: string,
  expected: { binder: string; item: string; roleDefinitionHash: string; profileHash: string } | null,
): string | null {
  let value: unknown;
  try {
    value = parseWorkerEnvelopeJson(text);
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "the result must be a single JSON object";
  }
  const result = value as Record<string, unknown>;
  const required = [
    "schema",
    "role",
    "binder",
    "item",
    "roleDefinitionHash",
    "profileHash",
    "outcome",
    "summary",
    "checks",
  ];
  const missing = required.filter((key) => !(key in result));
  const extra = Object.keys(result).filter((key) => !required.includes(key));
  if (missing.length > 0) return `missing key(s): ${missing.join(", ")}`;
  if (extra.length > 0) return `unknown key(s): ${extra.join(", ")}`;
  if (result.schema !== WORKER_SCHEMA) return `"schema" must be "${WORKER_SCHEMA}"`;
  if (result.role !== "build-worker") return '"role" must be "build-worker"';
  // Identity and freshness are not repairable by rewording: say so, so the turn
  // is not spent asking a worker to invent a hash it was never given.
  if (expected) {
    if (result.binder !== expected.binder) return `"binder" must be "${expected.binder}"`;
    if (result.item !== expected.item) return `"item" must be "${expected.item}"`;
    if (result.roleDefinitionHash !== expected.roleDefinitionHash) {
      return '"roleDefinitionHash" does not match the hash given in your instructions';
    }
    if (result.profileHash !== expected.profileHash) {
      return '"profileHash" does not match the hash given in your instructions';
    }
  }
  if (!["ready", "no-change", "blocked"].includes(String(result.outcome))) {
    return '"outcome" must be one of ready, no-change, blocked';
  }
  if (typeof result.summary !== "string" || !result.summary.trim()) {
    return '"summary" must be a non-empty string';
  }
  if (result.summary.length > MAX_WORKER_SUMMARY) {
    return `"summary" is ${result.summary.length} characters; the limit is ${MAX_WORKER_SUMMARY}` +
      " — shorten it and resend the same envelope, keeping every other field byte-identical";
  }
  if (!Array.isArray(result.checks)) return '"checks" must be an array';
  if (result.checks.length > MAX_WORKER_CHECKS) {
    return `"checks" has ${result.checks.length} entries; the limit is ${MAX_WORKER_CHECKS}`;
  }
  // The proposals are part of the envelope, so they are the turn's business too.
  // Validating them only in the strict parse put a blanket "malformed check
  // proposal" one loop past the last point the worker could have fixed it.
  return checkProposalsViolation(result.checks);
}

function checkProposalsViolation(checks: unknown[]): string | null {
  const ids = new Set<string>();
  for (const [index, proposal] of checks.entries()) {
    const at = `checks[${index}]`;
    if (!proposal || typeof proposal !== "object" || Array.isArray(proposal)) {
      return `${at} must be an object`;
    }
    const check = proposal as Record<string, unknown>;
    const keys = Object.keys(check);
    const missing = ["id", "command", "cwd"].filter((key) => !keys.includes(key));
    const extra = keys.filter((key) => !["id", "command", "cwd"].includes(key));
    if (missing.length > 0) return `${at} is missing key(s): ${missing.join(", ")}`;
    if (extra.length > 0) return `${at} has unknown key(s): ${extra.join(", ")}`;
    if (typeof check.id !== "string" || !/^[a-z][a-z0-9-]{0,63}$/.test(check.id)) {
      return `${at}.id must be lowercase letters, digits and hyphens, starting with a letter`;
    }
    if (check.id === "oracle") return `${at}.id must not be "oracle"; the host owns that check`;
    if (ids.has(check.id)) return `${at}.id "${check.id}" is a duplicate`;
    if (typeof check.command !== "string" || !check.command.trim()) {
      return `${at}.command must be a non-empty string`;
    }
    if (check.command.length > 16 * 1024) return `${at}.command is longer than 16384 characters`;
    const cwd = typeof check.cwd === "string" ? check.cwd.replaceAll("\\", "/") : "";
    if (!cwd) return `${at}.cwd must be a non-empty worktree-relative path`;
    if (cwd.startsWith("/")) return `${at}.cwd must be worktree-relative, not absolute`;
    if (cwd.split("/").includes("..")) return `${at}.cwd must not traverse outside the worktree`;
    ids.add(check.id);
  }
  return null;
}

export function promptWorkerForEnvelope(
  session: Parameters<typeof promptForJsonEnvelope>[0],
  userPrompt: string,
  expected: { binder: string; item: string; roleDefinitionHash: string; profileHash: string },
): Promise<string> {
  return promptForJsonEnvelope(
    session,
    userPrompt,
    (text) => workerEnvelopeViolation(text, expected) === null,
    (text) => {
      const violation = workerEnvelopeViolation(text, expected);
      return violation === null
        ? WORKER_ENVELOPE_REPAIR_PROMPT
        : `${WORKER_ENVELOPE_REPAIR_PROMPT} The result you sent was rejected because ${violation}.`;
    },
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
    const text = await promptWorkerForEnvelope(session, invocation.userPrompt, {
      binder: invocation.binder,
      item: invocation.item,
      roleDefinitionHash: invocation.profile.role.definitionHash,
      profileHash: invocation.profile.profileHash,
    });
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
  const violation = workerEnvelopeViolation(text, {
    binder,
    item,
    roleDefinitionHash: profile.role.definitionHash,
    profileHash: profile.profileHash,
  });
  // Name the field. The blanket "malformed or stale" message covered nine
  // different rules, so a discarded build told its operator nothing about which
  // one to look at.
  if (violation !== null) {
    throw new Error(`Malformed or stale Karta worker result envelope: ${violation}`);
  }
  const result = parseWorkerEnvelopeJson(text) as Record<string, unknown>;
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
        binder,
        item,
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
