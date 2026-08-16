import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, createWorkerChildSession, type ChildRuntimeReport } from "./child-runtime.ts";
import {
  attestWorkerAuthority,
  snapshotWorkerAuthority,
  type WorkerAuthorityAttestation,
  type WorkerAuthoritySnapshot,
} from "./worker-attestation.ts";
import { loadWorkerProjectInstructions } from "./worker-instructions.ts";
import {
  createWriterCapabilityProfile,
  type KartaWriterRole,
  type WriterCapabilityProfile,
} from "./writer-profile.ts";

const WRITER_SCHEMA = "karta-writer-result-v1";

export interface KartaResolvedWriterPack {
  id: string;
  path: string;
  sha256: string;
  content: string;
  canonicalSha256?: string;
  canonicalContent?: string;
}

export interface KartaOverrideEvidence {
  rule: string;
  reason: string;
  path: string;
  line: number;
  commit: string;
  date: string;
  delivery: string;
  inBlastRadius: boolean;
}

interface WriterResultBase {
  schema: typeof WRITER_SCHEMA;
  role: KartaWriterRole;
  binder: string;
  roleDefinitionHash: string;
  profileHash: string;
  summary: string;
  runtime: ChildRuntimeReport;
  attestation: WorkerAuthorityAttestation;
}

export interface KartaDocGardnerResult extends WriterResultBase {
  role: "doc-gardner";
  correctedCount: number;
  filesChanged: string[];
  residual: string[];
  focusStale?: string;
}

export interface KartaKaizenResult extends WriterResultBase {
  role: "kaizen";
  seeded: string[];
  packsChanged: string[];
  candidates: string[];
  erosionNotes: string[];
  upstreamCandidates: string[];
  proposedScaffolds: string[];
  residual: string[];
}

export type KartaWriterResult = KartaDocGardnerResult | KartaKaizenResult;
type KartaUnattestedWriterResult =
  | Omit<KartaDocGardnerResult, "attestation">
  | Omit<KartaKaizenResult, "attestation">;

export interface WriterInvocation {
  ctx: ExtensionContext;
  registry: ChildRegistry;
  profile: WriterCapabilityProfile;
  systemPrompt: string;
  userPrompt: string;
  parentId?: string;
}

export type WriterModelInvoker = (
  invocation: WriterInvocation,
) => Promise<{ text: string; runtime: ChildRuntimeReport }>;

async function invokeWriter(
  invocation: WriterInvocation,
): Promise<{ text: string; runtime: ChildRuntimeReport }> {
  const { session, report } = await createWorkerChildSession(
    invocation.ctx,
    invocation.systemPrompt,
    invocation.profile.tools,
    invocation.profile.worktree,
  );
  invocation.registry.add(session, {
    cwd: invocation.profile.worktree,
    role: invocation.profile.writer,
    label: invocation.profile.writer,
    parentId: invocation.parentId,
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

function exactKeys(value: Record<string, unknown>, required: string[], optional: string[] = []): void {
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !(key in value)) || Object.keys(value).some((key) => !allowed.has(key))) {
    throw new Error("Malformed Karta writer result: unexpected or missing keys");
  }
}

function stringArray(value: unknown, field: string, max = 256): string[] {
  if (
    !Array.isArray(value) || value.length > max ||
    !value.every((entry) => typeof entry === "string" && entry.length > 0 && entry.length <= 4_096)
  ) {
    throw new Error(`Malformed Karta writer result field '${field}'`);
  }
  return [...value];
}

function parseWriterResult(
  text: string,
  binder: string,
  profile: WriterCapabilityProfile,
  runtime: ChildRuntimeReport,
): KartaUnattestedWriterResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text.trim());
  } catch {
    throw new Error("Karta writer returned malformed JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Karta writer returned a non-object result");
  }
  const value = parsed as Record<string, unknown>;
  const common = ["schema", "role", "binder", "roleDefinitionHash", "profileHash", "summary"];
  if (
    value.schema !== WRITER_SCHEMA || value.role !== profile.writer || value.binder !== binder ||
    value.roleDefinitionHash !== profile.role.definitionHash || value.profileHash !== profile.profileHash ||
    typeof value.summary !== "string" || !value.summary.trim() || value.summary.length > 2_000
  ) {
    throw new Error("Malformed or stale Karta writer result envelope");
  }
  if (profile.writer === "doc-gardner") {
    exactKeys(value, [...common, "correctedCount", "filesChanged", "residual"], ["focusStale"]);
    if (!Number.isInteger(value.correctedCount) || Number(value.correctedCount) < 0) {
      throw new Error("Malformed Karta doc-gardner correctedCount");
    }
    if (value.focusStale !== undefined && (typeof value.focusStale !== "string" || value.focusStale.length > 2_000)) {
      throw new Error("Malformed Karta doc-gardner focusStale");
    }
    return {
      schema: WRITER_SCHEMA,
      role: "doc-gardner",
      binder,
      roleDefinitionHash: profile.role.definitionHash,
      profileHash: profile.profileHash,
      summary: value.summary,
      correctedCount: Number(value.correctedCount),
      filesChanged: stringArray(value.filesChanged, "filesChanged"),
      residual: stringArray(value.residual, "residual"),
      ...(value.focusStale === undefined ? {} : { focusStale: value.focusStale }),
      runtime,
    };
  }
  exactKeys(value, [
    ...common,
    "seeded",
    "packsChanged",
    "candidates",
    "erosionNotes",
    "upstreamCandidates",
    "proposedScaffolds",
    "residual",
  ]);
  return {
    schema: WRITER_SCHEMA,
    role: "kaizen",
    binder,
    roleDefinitionHash: profile.role.definitionHash,
    profileHash: profile.profileHash,
    summary: value.summary,
    seeded: stringArray(value.seeded, "seeded"),
    packsChanged: stringArray(value.packsChanged, "packsChanged"),
    candidates: stringArray(value.candidates, "candidates"),
    erosionNotes: stringArray(value.erosionNotes, "erosionNotes"),
    upstreamCandidates: stringArray(value.upstreamCandidates, "upstreamCandidates"),
    proposedScaffolds: stringArray(value.proposedScaffolds, "proposedScaffolds"),
    residual: stringArray(value.residual, "residual"),
    runtime,
  };
}

function attestWriterAuthority(
  writer: KartaWriterRole,
  before: WorkerAuthoritySnapshot,
  after: WorkerAuthoritySnapshot,
): WorkerAuthorityAttestation {
  const base = attestWorkerAuthority(before, after);
  const allowed = writer === "kaizen" ? new Set(["protectedPaths"]) : new Set<string>();
  const issues = base.issues.filter((issue) => ![...allowed].some((field) => issue.endsWith(`: ${field}`)));
  return { ...base, passed: issues.length === 0, issues };
}

function writerSystemPrompt(profile: WriterCapabilityProfile, instructions: string): string {
  const output = profile.writer === "doc-gardner"
    ? `{"schema":"${WRITER_SCHEMA}","role":"doc-gardner","binder":"<bound binder>","roleDefinitionHash":"${profile.role.definitionHash}","profileHash":"${profile.profileHash}","correctedCount":0,"filesChanged":[],"residual":[],"summary":"plain-language outcome","focusStale":"optional advisory"}`
    : `{"schema":"${WRITER_SCHEMA}","role":"kaizen","binder":"<bound binder>","roleDefinitionHash":"${profile.role.definitionHash}","profileHash":"${profile.profileHash}","seeded":[],"packsChanged":[],"candidates":[],"erosionNotes":[],"upstreamCandidates":[],"proposedScaffolds":[],"residual":[],"summary":"plain-language outcome"}`;
  return `${profile.role.prompt}

## Committed project instructions

${instructions || "No committed AGENTS.md or CLAUDE.md files were present at this integration tip."}

## Pi writer execution contract — authoritative

You are an isolated ${profile.writer} writer in a disposable worktree. You have only ${profile.toolNames.join(", ")}; there is no shell or Git tool. The host supplied the delivery blast-radius paths because this isolated profile cannot run git diff. Package pack contents supplied in the request are authoritative installed-package inputs.

Edit only your declared surface. Never stage, commit, create refs, or modify Git administration. The host independently attests every changed path, validates applicable files, scans the exact candidate, reproduces hooks, and alone creates or moves commits.

Return exactly one JSON object and no prose using this envelope:
${output}`;
}

export class KartaWriterRunner {
  readonly #registry: ChildRegistry;
  readonly #invoke: WriterModelInvoker;

  constructor(registry: ChildRegistry, invoke: WriterModelInvoker = invokeWriter) {
    this.#registry = registry;
    this.#invoke = invoke;
  }

  async run(
    ctx: ExtensionContext,
    writer: KartaWriterRole,
    worktree: string,
    binder: string,
    input: {
      diffRange: string;
      changedPaths: string[];
      focus?: string;
      packs?: KartaResolvedWriterPack[];
      migrationPacks?: KartaResolvedWriterPack[];
      overrideEvidence?: KartaOverrideEvidence[];
    },
    parentId?: string,
  ): Promise<KartaWriterResult> {
    const profile = createWriterCapabilityProfile(worktree, writer);
    const projectInstructions = await loadWorkerProjectInstructions(worktree);
    const instructions = projectInstructions.map((instruction) =>
      `### ${instruction.path} (blob ${instruction.blob}, sha256 ${instruction.sha256})\n\n${instruction.content}`,
    ).join("\n\n");
    const before = await snapshotWorkerAuthority(worktree);
    let response: { text: string; runtime: ChildRuntimeReport } | undefined;
    let invocationError: unknown;
    try {
      response = await this.#invoke({
        ctx,
        registry: this.#registry,
        profile,
        parentId,
        systemPrompt: writerSystemPrompt(profile, instructions),
        userPrompt: JSON.stringify({ binder, writer, ...input }),
      });
    } catch (error) {
      invocationError = error;
    }
    const after = await snapshotWorkerAuthority(worktree);
    const attestation = attestWriterAuthority(writer, before, after);
    if (!attestation.passed) {
      throw new Error(`Karta writer violated host authority: ${attestation.issues.join("; ")}`);
    }
    if (invocationError) throw invocationError;
    if (!response) throw new Error("Karta writer returned no response");
    return {
      ...parseWriterResult(response.text, binder, profile, response.runtime),
      attestation,
    } as KartaWriterResult;
  }
}
