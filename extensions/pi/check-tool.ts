import type { ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import {
  verifyEvidenceIntegrity,
  type KartaCheckEvidence,
  type KartaEvidenceManifest,
} from "./evidence.ts";

export interface CheckToolDetails extends KartaCheckEvidence {
  evidenceHash: string;
}

const checkParameters = Type.Object({ action: Type.Literal("summary") });

export function createCheckEvidenceTool(
  manifest: KartaEvidenceManifest,
): ToolDefinition<typeof checkParameters, CheckToolDetails> {
  verifyEvidenceIntegrity(manifest);
  return {
    name: "karta_checks",
    label: "Karta check evidence",
    description:
      "Read the host-generated check receipt bound to this evidence tree. It cannot execute or select commands, paths, environments, refs, or timeouts.",
    parameters: checkParameters,
    async execute() {
      try {
        verifyEvidenceIntegrity(manifest);
        const details: CheckToolDetails = {
          evidenceHash: manifest.evidenceHash,
          ...manifest.payload.checks.oracle,
        };
        return {
          content: [{ type: "text", text: JSON.stringify(details, null, 2) }],
          details,
          isError: false,
        };
      } catch (error) {
        const details: CheckToolDetails = {
          evidenceHash: manifest.evidenceHash,
          status: "missing",
          targetTree: manifest.payload.git.targetTree,
        };
        return {
          content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
          details,
          isError: true,
        };
      }
    },
  };
}
