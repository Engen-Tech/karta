import { createHash } from "node:crypto";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { createBoundaryInspectionTool } from "./boundary-inspector.ts";
import { createCheckEvidenceTool } from "./check-tool.ts";
import {
  canonicalJson,
  createEvidenceReadTool,
  verifyEvidenceIntegrity,
  type KartaEvidenceManifest,
} from "./evidence.ts";
import { loadKartaRole, type KartaRoleDefinition, type KartaRoleId } from "./role-catalog.ts";

export const GATE_CAPABILITY_PROFILE_VERSION = 3;

export type KartaGateRoleId = Extract<KartaRoleId, "acceptance-gate" | "safety-gate">;

type AnyToolDefinition = ToolDefinition<any, any, any>;

export interface GateRoleToolState {
  invoked: boolean;
  details?: unknown;
}

export interface GateEvidenceToolState {
  actions: Set<string>;
  packs: Set<string>;
  citations: Set<number>;
  requiredPacks: string[];
  requiredCitations: number[];
  diffReads: Array<[number, number]>;
  diffTotal: number;
}

export interface GateCapabilityProfile {
  version: typeof GATE_CAPABILITY_PROFILE_VERSION;
  role: KartaRoleDefinition;
  evidenceHash: string;
  tools: AnyToolDefinition[];
  toolNames: string[];
  profileHash: string;
  evidenceToolState: GateEvidenceToolState;
  roleToolState: GateRoleToolState;
}


function hash(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function toolIdentity(tool: AnyToolDefinition): Record<string, unknown> {
  return {
    name: tool.name,
    description: tool.description,
    parameters: tool.parameters,
  };
}

export function createGateCapabilityProfile(
  roleId: KartaGateRoleId,
  manifest: KartaEvidenceManifest,
): GateCapabilityProfile {
  verifyEvidenceIntegrity(manifest);
  const role = loadKartaRole(roleId);
  if (role.authority !== "read-only") {
    throw new Error(`Karta gate capability profile requires read-only authority: ${roleId}`);
  }
  const evidenceToolState: GateEvidenceToolState = {
    actions: new Set(),
    packs: new Set(),
    citations: new Set(),
    requiredPacks: manifest.payload.packs.map((pack) => pack.id),
    requiredCitations: manifest.payload.citations.map((citation) => citation.index),
    diffReads: [],
    diffTotal: 0,
  };
  const evidence: AnyToolDefinition = createEvidenceReadTool(manifest);
  const executeEvidenceTool = evidence.execute.bind(evidence);
  evidence.execute = async (toolCallId, params, signal, onUpdate, ctx) => {
    const action = (params as { action?: unknown }).action;
    if (typeof action === "string") evidenceToolState.actions.add(action);
    if (action === "pack" && typeof (params as { id?: unknown }).id === "string") {
      evidenceToolState.packs.add((params as { id: string }).id);
    }
    if (action === "citation" && Number.isInteger((params as { index?: unknown }).index)) {
      evidenceToolState.citations.add((params as { index: number }).index);
    }
    const result = await executeEvidenceTool(toolCallId, params, signal, onUpdate, ctx);
    if (action === "diff") {
      // Record the byte range this read covered so grounding can require the whole
      // diff, not just one page. offset defaults to 0; a null nextOffset means the
      // read reached the end (totalLength).
      const details = result.details as { totalLength?: unknown; nextOffset?: unknown } | undefined;
      const total = Number.isInteger(details?.totalLength) ? Number(details?.totalLength) : 0;
      const offset = Number.isInteger((params as { offset?: unknown }).offset)
        ? Number((params as { offset?: unknown }).offset)
        : 0;
      const end = Number.isInteger(details?.nextOffset) ? Number(details?.nextOffset) : total;
      evidenceToolState.diffTotal = total;
      evidenceToolState.diffReads.push([offset, end]);
    }
    return result;
  };
  const roleToolState: GateRoleToolState = { invoked: false };
  const roleTool: AnyToolDefinition =
    roleId === "acceptance-gate"
      ? createCheckEvidenceTool(manifest)
      : createBoundaryInspectionTool(manifest);
  const executeRoleTool = roleTool.execute.bind(roleTool);
  roleTool.execute = async (toolCallId, params, signal, onUpdate, ctx) => {
    roleToolState.invoked = true;
    const result = await executeRoleTool(toolCallId, params, signal, onUpdate, ctx);
    roleToolState.details = result.details;
    return result;
  };
  const tools: AnyToolDefinition[] = [evidence, roleTool];
  const toolNames = tools.map((tool) => tool.name);
  if (new Set(toolNames).size !== toolNames.length) {
    throw new Error(`Karta gate capability profile '${roleId}' contains duplicate tools`);
  }
  const profileHash = hash(
    canonicalJson({
      version: GATE_CAPABILITY_PROFILE_VERSION,
      role: role.id,
      roleDefinitionHash: role.definitionHash,
      evidenceHash: manifest.evidenceHash,
      capabilities: role.capabilities,
      tools: tools.map(toolIdentity),
    }),
  );
  return {
    version: GATE_CAPABILITY_PROFILE_VERSION,
    role,
    evidenceHash: manifest.evidenceHash,
    tools,
    toolNames,
    profileHash,
    evidenceToolState,
    roleToolState,
  };
}
