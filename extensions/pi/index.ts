import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  ChildRegistry,
  GateProviderPreflight,
  runAuthProbe,
  runCancellationProbe,
  runGateAuthProbe,
  runMultiChildShutdownProbe,
  runResponseProbe,
} from "./child-runtime.ts";
import { DispatchLockManager } from "./dispatch-lock.ts";
import { createKartaDispatchTool } from "./dispatch-tool.ts";
import { claimExtensionInstance } from "./extension-instance.ts";
import { registerGuardAdapters } from "./guard-adapter.ts";
import { PACKAGE_ROOT, requirePackagePath } from "./package-paths.ts";
import { createKartaScriptTool } from "./script-tool.ts";

export default function kartaPi(extension: ExtensionAPI): void {
  const releaseInstance = claimExtensionInstance(PACKAGE_ROOT);
  const children = new ChildRegistry();
  const dispatchLocks = new DispatchLockManager();
  const gatePreflight = new GateProviderPreflight();
  const guards = registerGuardAdapters(extension);
  extension.registerTool(createKartaScriptTool(extension));
  extension.registerTool(createKartaDispatchTool(gatePreflight, children));

  extension.on("resources_discover", (_event, ctx) => {
    if (!ctx.isProjectTrusted()) return {};
    return { skillPaths: [requirePackagePath("skills")] };
  });

  extension.registerCommand("karta-phase0", {
    description:
      "Run Karta's Pi feasibility probes: status, auth, gate-auth, gate-child, multi-cancel, child, or cancel",
    handler: async (args, ctx) => {
      const action = args.trim() || "status";
      if (action === "status") {
        ctx.ui.notify(
          JSON.stringify(
            {
              trusted: ctx.isProjectTrusted(),
              packageRoot: PACKAGE_ROOT,
              model: ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : null,
              activeChildren: children.size,
              lifecycles: children.lifecycles.snapshot(),
              gatePreflights: gatePreflight.size,
              dispatchLocks: dispatchLocks.size,
            },
            null,
            2,
          ),
          "info",
        );
        return;
      }
      if (!ctx.isProjectTrusted()) {
        ctx.ui.notify("Karta Phase 0 probes are disabled in an untrusted project.", "error");
        return;
      }
      try {
        let result: unknown;
        switch (action) {
          case "auth":
            result = await runAuthProbe(ctx);
            break;
          case "gate-auth":
            result = await runGateAuthProbe(ctx);
            break;
          case "gate-child":
            result = await gatePreflight.ensure(ctx, children);
            break;
          case "multi-cancel":
            result = await runMultiChildShutdownProbe(ctx);
            break;
          case "child":
            result = await runResponseProbe(ctx, children);
            break;
          case "cancel":
            result = await runCancellationProbe(ctx, children);
            break;
          default:
            ctx.ui.notify(
              "Usage: /karta-phase0 [status|auth|gate-auth|gate-child|multi-cancel|child|cancel]",
              "warning",
            );
            return;
        }
        ctx.ui.notify(JSON.stringify(result, null, 2), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });

  extension.on("session_shutdown", async () => {
    guards.shutdown();
    gatePreflight.clear();
    try {
      await children.abortAll();
    } finally {
      await dispatchLocks.releaseAll();
      releaseInstance();
    }
  });
}
