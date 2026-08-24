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
// A typed, package-owned blocked reason. Its only value is a fail-closed visual
// prerequisite; callers may never supply or widen it, so free text can never spoof it.
export type KartaVerificationBlockedReason = "visual-required";

export interface KartaVerificationResult {
  schema: "karta-verification-v1";
  binder: string;
  item: string;
  requestedMode: KartaVerificationMode;
  effectiveMode: KartaVerificationMode | "skipped";
  evidenceHash: string;
  status: KartaVerificationStatus;
  reason?: string;
  // Set only for a blocked status whose cause is a typed prerequisite (visual-required),
  // never oracle opt-out prose. The free-text reason field stays reserved for opt-out.
  blockedReason?: KartaVerificationBlockedReason;
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
    // A full visual oracle has no package-run visual acceptance yet, so it never runs the
    // acceptance gate (consuming no worker-feedback attempt); the ordered derivation below
    // blocks it as visual-required after boundary safety. The mode is never downgraded — a
    // full request stays full, so callers see requestedMode and effectiveMode as "full".
    const visualAcceptancePending = itemOracle.type === "visual";
    const effectiveMode: KartaVerificationMode = requestedMode;
    const gates: KartaVerificationResult["gates"] = {};
    if (effectiveMode === "full" && !visualAcceptancePending) {
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
    // Ordered, package-owned result derivation. Callers key on the top-level
    // status/blockedReason, never on gates.safety.verdict as the overall verdict.
    // 1. A non-pass safety verdict is the overall failure and is never overwritten.
    if (gates.safety.verdict !== "pass") {
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
    // 2. Safety passed, but a full visual oracle still has no visual acceptance result:
    //    block as visual-required rather than reporting the safety pass as the verdict.
    if (effectiveMode === "full" && visualAcceptancePending) {
      return {
        schema: "karta-verification-v1",
        binder,
        item,
        requestedMode,
        effectiveMode,
        evidenceHash: evidence.evidenceHash,
        status: "blocked",
        blockedReason: "visual-required",
        gates,
      };
    }
    // 3. Otherwise the passing safety verdict is the overall verdict.
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
