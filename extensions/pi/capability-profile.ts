import { createHash } from "node:crypto";
import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { createBoundaryInspectionTool } from "./boundary-inspector.ts";
import {
  canonicalJson,
  createEvidenceReadTool,
  verifyEvidenceIntegrity,
  type KartaEvidenceManifest,
} from "./evidence.ts";
import { createAcceptanceOracleTool, type OracleRunnerOptions } from "./oracle-runner.ts";
import { loadKartaRole, type KartaRoleDefinition, type KartaRoleId } from "./role-catalog.ts";

export type KartaGateRoleId = Extract<KartaRoleId, "acceptance-gate" | "safety-gate">;

type AnyToolDefinition = ToolDefinition<any, any, any>;

export interface GateRoleToolState {
  invoked: boolean;
  details?: unknown;
}

export interface GateCapabilityProfile {
  role: KartaRoleDefinition;
  evidenceHash: string;
  tools: AnyToolDefinition[];
  toolNames: string[];
  profileHash: string;
  roleToolState: GateRoleToolState;
}

export interface GateCapabilityOptions {
  oracle?: OracleRunnerOptions;
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
  options: GateCapabilityOptions = {},
): GateCapabilityProfile {
  verifyEvidenceIntegrity(manifest);
  const role = loadKartaRole(roleId);
  if (role.authority !== "read-only") {
    throw new Error(`Karta gate capability profile requires read-only authority: ${roleId}`);
  }
  const evidence = createEvidenceReadTool(manifest);
  const roleToolState: GateRoleToolState = { invoked: false };
  const roleTool: AnyToolDefinition =
    roleId === "acceptance-gate"
      ? createAcceptanceOracleTool(manifest, options.oracle)
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
      role: role.id,
      roleDefinitionHash: role.definitionHash,
      evidenceHash: manifest.evidenceHash,
      capabilities: role.capabilities,
      tools: tools.map(toolIdentity),
    }),
  );
  return {
    role,
    evidenceHash: manifest.evidenceHash,
    tools,
    toolNames,
    profileHash,
    roleToolState,
  };
}
