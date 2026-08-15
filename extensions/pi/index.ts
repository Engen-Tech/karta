import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { KartaBuildFinalizer } from "./build-finalizer.ts";
import { KartaBuildItemRunner } from "./build-runner.ts";
import {
  ChildRegistry,
  GateProviderPreflight,
  runAuthProbe,
  runCancellationProbe,
  runGateAuthProbe,
  runMultiChildShutdownProbe,
  runResponseProbe,
} from "./child-runtime.ts";
import { KartaDeliveryRunner } from "./delivery-runner.ts";
import { DispatchLockManager } from "./dispatch-lock.ts";
import { createKartaDispatchTool } from "./dispatch-tool.ts";
import { claimExtensionInstance } from "./extension-instance.ts";
import { registerGuardAdapters } from "./guard-adapter.ts";
import { KartaIntegrationRunner } from "./integration-runner.ts";
import { PACKAGE_ROOT, requirePackagePath } from "./package-paths.ts";
import { KartaProcessManager } from "./process-manager.ts";
import { createKartaScriptTool } from "./script-tool.ts";
import { KartaShutdownCoordinator } from "./shutdown-coordinator.ts";
import { KartaVerificationRunner } from "./verification-runner.ts";
import { KartaWaveRunner } from "./wave-runner.ts";
import { KartaBuildWorkerRunner } from "./worker-runner.ts";

export default function kartaPi(extension: ExtensionAPI): void {
  const releaseInstance = claimExtensionInstance(PACKAGE_ROOT);
  const children = new ChildRegistry();
  const dispatchLocks = new DispatchLockManager();
  const processes = new KartaProcessManager(children.lifecycles);
  const gatePreflight = new GateProviderPreflight();
  const verification = new KartaVerificationRunner(gatePreflight, children, dispatchLocks);
  const workers = new KartaBuildWorkerRunner(children);
  const finalizer = new KartaBuildFinalizer(dispatchLocks, verification);
  const buildItems = new KartaBuildItemRunner(dispatchLocks, workers, finalizer, processes);
  const integrations = new KartaIntegrationRunner(dispatchLocks, verification);
  const waves = new KartaWaveRunner(dispatchLocks);
  const deliveries = new KartaDeliveryRunner(
    dispatchLocks,
    processes,
    buildItems,
    integrations,
    workers,
    waves,
  );
  const guards = registerGuardAdapters(extension);
  const shutdown = new KartaShutdownCoordinator({
    children,
    locks: dispatchLocks,
    guards,
    preflight: gatePreflight,
    releaseInstance,
  });
  extension.registerTool(createKartaScriptTool(extension));
  extension.registerTool(
    createKartaDispatchTool(gatePreflight, children, verification, buildItems, deliveries),
  );

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
              activeProcesses: processes.size,
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
    await shutdown.shutdown();
  });
}
