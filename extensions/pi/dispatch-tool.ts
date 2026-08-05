import type {
  ExtensionContext,
  ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type, type Static } from "typebox";
import {
  ChildRegistry,
  type GateProviderPreflightReport,
} from "./child-runtime.ts";
import { loadKartaRole, type KartaRoleDefinition } from "./role-catalog.ts";

const roleId = Type.Union([
  Type.Literal("acceptance-gate"),
  Type.Literal("safety-gate"),
  Type.Literal("build-worker"),
  Type.Literal("doc-gardner"),
  Type.Literal("kaizen"),
]);

const gateRoleId = Type.Union([
  Type.Literal("acceptance-gate"),
  Type.Literal("safety-gate"),
]);

const dispatchParameters = Type.Union([
  Type.Object({
    action: Type.Literal("describeRole"),
    role: roleId,
  }),
  Type.Object({
    action: Type.Literal("preflightGate"),
    role: gateRoleId,
  }),
]);

export type KartaDispatchParameters = Static<typeof dispatchParameters>;

interface GatePreflight {
  ensure(ctx: ExtensionContext, registry: ChildRegistry): Promise<GateProviderPreflightReport>;
}

interface DispatchDetails {
  action: KartaDispatchParameters["action"];
  role: string;
  definitionHash?: string;
  provider?: string;
  model?: string;
  cached?: boolean;
}

function roleSummary(role: KartaRoleDefinition): Record<string, unknown> {
  return {
    role: role.id,
    authority: role.authority,
    capabilities: role.capabilities,
    outputSchema: role.outputSchema,
    sourceHash: role.sourceHash,
    promptHash: role.promptHash,
    definitionHash: role.definitionHash,
  };
}

function textResult(text: string, details: DispatchDetails, isError = false) {
  return {
    content: [{ type: "text" as const, text }],
    details,
    isError,
  };
}

export function createKartaDispatchTool(
  preflight: GatePreflight,
  children: ChildRegistry,
): ToolDefinition<typeof dispatchParameters, DispatchDetails> {
  return {
    name: "karta_dispatch",
    label: "Karta dispatch",
    description:
      "Package-owned Karta role entrypoint. Describes a fixed role or preflights a read-only gate. Callers cannot supply prompts, paths, tools, or provider hooks.",
    parameters: dispatchParameters,
    async execute(_toolCallId, params: KartaDispatchParameters, _signal, _onUpdate, ctx) {
      if (!ctx?.isProjectTrusted()) {
        return textResult(
          "Karta dispatch is disabled in untrusted projects.",
          { action: params.action, role: params.role },
          true,
        );
      }
      try {
        const role = loadKartaRole(params.role);
        if (params.action === "describeRole") {
          return textResult(
            JSON.stringify(roleSummary(role), null, 2),
            { action: params.action, role: role.id, definitionHash: role.definitionHash },
          );
        }
        if (role.authority !== "read-only") {
          throw new Error(`Karta gate preflight requires a read-only role, not '${role.id}'`);
        }
        const report = await preflight.ensure(ctx, children);
        return textResult(
          JSON.stringify({ ...roleSummary(role), preflight: report }, null, 2),
          {
            action: params.action,
            role: role.id,
            definitionHash: role.definitionHash,
            provider: report.provider,
            model: report.model,
            cached: report.cached,
          },
        );
      } catch (error) {
        return textResult(
          error instanceof Error ? error.message : String(error),
          { action: params.action, role: params.role },
          true,
        );
      }
    },
  };
}
