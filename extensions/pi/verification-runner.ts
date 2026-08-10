import type { ExtensionContext } from "@earendil-works/pi-coding-agent";
import { ChildRegistry, type GateProviderPreflightReport } from "./child-runtime.ts";
import { DispatchLockManager, type DispatchLockLease } from "./dispatch-lock.ts";
import {
  buildKartaEvidence,
  type BuildEvidenceOptions,
  type KartaEvidenceManifest,
} from "./evidence.ts";
import {
  executeGateOnEvidence,
  type GateModelInvoker,
  type KartaGateResult,
} from "./gate-runner.ts";

export type KartaVerificationMode = "full" | "boundary-only";
export type KartaVerificationStatus = "pass" | "concerns" | "blocked" | "skipped";

export interface KartaVerificationResult {
  schema: "karta-verification-v1";
  binder: string;
  item: string;
  requestedMode: KartaVerificationMode;
  effectiveMode: KartaVerificationMode | "skipped";
  evidenceHash: string;
  status: KartaVerificationStatus;
  reason?: string;
  gates: {
    acceptance?: KartaGateResult;
    safety?: KartaGateResult;
  };
}

interface GatePreflight {
  ensure(ctx: ExtensionContext, registry: ChildRegistry): Promise<GateProviderPreflightReport>;
}

export type EvidenceBuilder = (options: BuildEvidenceOptions) => Promise<KartaEvidenceManifest>;

export type EvidenceGateExecutor = typeof executeGateOnEvidence;
export type VerificationEvidenceOptions = Pick<BuildEvidenceOptions, "target" | "checkManifest"> & {
  cwd?: string;
};

function oracle(manifest: KartaEvidenceManifest): Record<string, unknown> {
  const value = manifest.payload.workItem.oracle;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Karta work item has no valid oracle after binder validation");
  }
  return value as Record<string, unknown>;
}

function gateStatus(gate: KartaGateResult): KartaVerificationStatus {
  return gate.verdict;
}

export class KartaVerificationRunner {
  readonly #preflight: GatePreflight;
  readonly #children: ChildRegistry;
  readonly #locks: DispatchLockManager;
  readonly #buildEvidence: EvidenceBuilder;
  readonly #executeGate: EvidenceGateExecutor;
  readonly #invoke?: GateModelInvoker;

  constructor(
    preflight: GatePreflight,
    children: ChildRegistry,
    locks: DispatchLockManager,
    options: {
      buildEvidence?: EvidenceBuilder;
      executeGate?: EvidenceGateExecutor;
      invoke?: GateModelInvoker;
    } = {},
  ) {
    this.#preflight = preflight;
    this.#children = children;
    this.#locks = locks;
    this.#buildEvidence = options.buildEvidence ?? buildKartaEvidence;
    this.#executeGate = options.executeGate ?? executeGateOnEvidence;
    this.#invoke = options.invoke;
  }

  async run(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    requestedMode: KartaVerificationMode,
  ): Promise<KartaVerificationResult> {
    if (ctx.signal?.aborted) throw new Error("Karta verification was cancelled before dispatch");
    const lease = await this.#locks.acquire(ctx.cwd, binder);
    try {
      return await this.#runLocked(ctx, binder, item, requestedMode, {});
    } finally {
      await this.#locks.release(lease);
    }
  }

  async runWithLease(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    requestedMode: KartaVerificationMode,
    lease: DispatchLockLease,
    evidenceOptions: VerificationEvidenceOptions = {},
  ): Promise<KartaVerificationResult> {
    if (!this.#locks.owns(lease) || lease.owner.binder !== binder) {
      throw new Error(`Karta verification does not own the supplied lock for binder '${binder}'`);
    }
    if (ctx.signal?.aborted) throw new Error("Karta verification was cancelled before dispatch");
    return this.#runLocked(ctx, binder, item, requestedMode, evidenceOptions);
  }

  async #runLocked(
    ctx: ExtensionContext,
    binder: string,
    item: string,
    requestedMode: KartaVerificationMode,
    evidenceOptions: VerificationEvidenceOptions,
  ): Promise<KartaVerificationResult> {
    const { cwd = ctx.cwd, ...targetOptions } = evidenceOptions;
    const evidence = await this.#buildEvidence({
      cwd,
      binder,
      item,
      ...targetOptions,
    });
    const itemOracle = oracle(evidence);
    if (itemOracle.opt_out === true) {
      return {
        schema: "karta-verification-v1",
        binder,
        item,
        requestedMode,
        effectiveMode: "skipped",
        evidenceHash: evidence.evidenceHash,
        status: "skipped",
        reason:
          typeof itemOracle.reason === "string"
            ? `oracle opt-out: ${itemOracle.reason}`
            : "oracle opt-out",
        gates: {},
      };
    }
    const effectiveMode = itemOracle.type === "visual" ? "boundary-only" : requestedMode;
    const gates: KartaVerificationResult["gates"] = {};
    if (effectiveMode === "full") {
      gates.acceptance = await this.#executeGate(
        ctx,
        "acceptance-gate",
        evidence,
        this.#preflight,
        this.#children,
        this.#invoke,
      );
      if (gates.acceptance.verdict !== "pass") {
        return {
          schema: "karta-verification-v1",
          binder,
          item,
          requestedMode,
          effectiveMode,
          evidenceHash: evidence.evidenceHash,
          status: gateStatus(gates.acceptance),
          gates,
        };
      }
    }
    gates.safety = await this.#executeGate(
      ctx,
      "safety-gate",
      evidence,
      this.#preflight,
      this.#children,
      this.#invoke,
    );
    return {
      schema: "karta-verification-v1",
      binder,
      item,
      requestedMode,
      effectiveMode,
      evidenceHash: evidence.evidenceHash,
      status: gateStatus(gates.safety),
      gates,
    };
  }
}
