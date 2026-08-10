import { createHash } from "node:crypto";
import {
  AgentSession,
  createAgentSession,
  DefaultResourceLoader,
  getAgentDir,
  ModelRuntime,
  SessionManager,
  SettingsManager,
  type ExtensionContext,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { LifecycleRegistry, type LifecycleRole } from "./lifecycle-registry.ts";

export type ChildRuntimePolicy = "probe" | "gate" | "worker";

export interface ChildRuntimeReport {
  provider: string;
  model: string;
  policy: ChildRuntimePolicy;
  exactModelResolved: boolean;
  parentAuthConfigured: boolean;
  parentAuthSource?: string;
  childAuthConfigured: boolean;
  copiedProvider: "builtin" | "config" | "native";
  copiedRuntimeCredential: boolean;
  unresolvedEnvironmentKeys: string[];
}

interface ManagedSession {
  abort(): Promise<void>;
  dispose(): void;
}

export class ChildRegistry {
  readonly #sessions = new Map<ManagedSession, string>();
  readonly lifecycles: LifecycleRegistry;

  constructor(lifecycles = new LifecycleRegistry()) {
    this.lifecycles = lifecycles;
  }

  add(
    session: ManagedSession,
    options: { cwd?: string; role?: LifecycleRole; label?: string; parentId?: string } = {},
  ): void {
    if (this.#sessions.has(session)) throw new Error("Karta child session is already registered");
    const id = this.lifecycles.register({
      role: options.role ?? "phase0-probe",
      cwd: options.cwd ?? process.cwd(),
      label: options.label,
      parentId: options.parentId,
      resource: session,
    });
    this.#sessions.set(session, id);
  }

  delete(session: ManagedSession): void {
    const id = this.#sessions.get(session);
    if (!id) return;
    this.lifecycles.forget(id);
    this.#sessions.delete(session);
  }

  get size(): number {
    return this.#sessions.size;
  }

  async abortAll(): Promise<void> {
    await this.lifecycles.shutdown();
    this.#sessions.clear();
  }
}

export async function createIsolatedResourceLoader(
  cwd: string,
  systemPrompt: string,
  agentDir = getAgentDir(),
): Promise<{ loader: DefaultResourceLoader; settings: SettingsManager }> {
  const settings = SettingsManager.inMemory();
  const loader = new DefaultResourceLoader({
    cwd,
    agentDir,
    settingsManager: settings,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPrompt,
  });
  await loader.reload();
  return { loader, settings };
}

export async function createMirroredModelRuntime(
  ctx: ExtensionContext,
  policy: ChildRuntimePolicy = "probe",
): Promise<{
  runtime: ModelRuntime;
  model: NonNullable<ExtensionContext["model"]>;
  report: ChildRuntimeReport;
}> {
  if (!ctx.model) {
    throw new Error("Pi has no active model");
  }

  const providerId = ctx.model.provider;
  const nativeProvider = ctx.modelRegistry.getRegisteredNativeProvider(providerId);
  const providerConfig = ctx.modelRegistry.getRegisteredProviderConfig(providerId);
  const isolatedPolicy = policy === "gate" || policy === "worker";
  if (isolatedPolicy && nativeProvider) {
    throw new Error(
      `Karta ${policy} children do not inherit dynamic native provider '${providerId}' because it contains ambient extension code`,
    );
  }
  if (
    isolatedPolicy &&
    providerConfig &&
    (providerConfig.streamSimple || providerConfig.refreshModels || providerConfig.oauth)
  ) {
    throw new Error(
      `Karta ${policy} children do not inherit executable provider hooks for '${providerId}' from an ambient extension`,
    );
  }

  const runtime = await ModelRuntime.create({ allowModelNetwork: false });
  let copiedProvider: ChildRuntimeReport["copiedProvider"] = "builtin";

  if (nativeProvider) {
    runtime.registerNativeProvider(nativeProvider);
    copiedProvider = "native";
  }
  if (providerConfig) {
    runtime.registerProvider(providerId, providerConfig);
    copiedProvider = "config";
  }

  const parentAuthStatus = ctx.modelRegistry.getProviderAuthStatus(providerId);
  const resolvedParentAuth = await ctx.modelRegistry.getApiKeyAndHeaders(ctx.model);
  let copiedRuntimeCredential = false;
  let unresolvedEnvironmentKeys: string[] = [];

  if (resolvedParentAuth.ok) {
    unresolvedEnvironmentKeys = Object.keys(resolvedParentAuth.env ?? {});
    if (parentAuthStatus.source === "runtime" && resolvedParentAuth.apiKey) {
      await runtime.setRuntimeApiKey(providerId, resolvedParentAuth.apiKey, { allowNetwork: false });
      copiedRuntimeCredential = true;
    }
  }

  const exactModel = runtime.getModel(providerId, ctx.model.id);
  if (isolatedPolicy && !exactModel) {
    throw new Error(
      `Karta ${policy} runtime cannot resolve exact model '${providerId}/${ctx.model.id}' without the parent runtime`,
    );
  }
  const childModel = exactModel ?? ctx.model;
  const childAuthStatus = runtime.getProviderAuthStatus(providerId);
  return {
    runtime,
    model: childModel,
    report: {
      provider: providerId,
      model: ctx.model.id,
      policy,
      exactModelResolved: exactModel !== undefined,
      parentAuthConfigured: parentAuthStatus.configured,
      parentAuthSource: parentAuthStatus.source,
      childAuthConfigured: childAuthStatus.configured,
      copiedProvider,
      copiedRuntimeCredential,
      unresolvedEnvironmentKeys,
    },
  };
}

async function createChild(
  ctx: ExtensionContext,
  systemPrompt: string,
  customTools: ToolDefinition[] = [],
  policy: ChildRuntimePolicy = "probe",
  cwd = ctx.cwd,
): Promise<{ session: AgentSession; report: ChildRuntimeReport }> {
  const { runtime, model, report } = await createMirroredModelRuntime(ctx, policy);
  const { loader, settings } = await createIsolatedResourceLoader(cwd, systemPrompt);
  const { session } = await createAgentSession({
    cwd,
    modelRuntime: runtime,
    model,
    thinkingLevel: ctx.thinkingLevel ?? "minimal",
    resourceLoader: loader,
    settingsManager: settings,
    sessionManager: SessionManager.inMemory(ctx.cwd),
    noTools: "all",
    tools: customTools.map((tool) => tool.name),
    customTools,
  });
  return { session, report };
}

export async function createGateChildSession(
  ctx: ExtensionContext,
  systemPrompt: string,
  customTools: ToolDefinition[],
  cwd: string,
): Promise<{ session: AgentSession; report: ChildRuntimeReport }> {
  if (customTools.length === 0) throw new Error("Karta gate child requires explicit tools");
  return createChild(ctx, systemPrompt, customTools, "gate", cwd);
}

export async function createWorkerChildSession(
  ctx: ExtensionContext,
  systemPrompt: string,
  customTools: ToolDefinition[],
  cwd: string,
): Promise<{ session: AgentSession; report: ChildRuntimeReport }> {
  if (customTools.length === 0) throw new Error("Karta worker child requires explicit tools");
  return createChild(ctx, systemPrompt, customTools, "worker", cwd);
}

function bindAbort(session: AgentSession, signal: AbortSignal | undefined): () => void {
  if (!signal) return () => undefined;
  const abort = () => void session.abort();
  signal.addEventListener("abort", abort, { once: true });
  return () => signal.removeEventListener("abort", abort);
}

export async function runAuthProbe(ctx: ExtensionContext): Promise<ChildRuntimeReport> {
  const { session, report } = await createChild(ctx, "Reply only when prompted.");
  session.dispose();
  return report;
}

export async function runGateAuthProbe(ctx: ExtensionContext): Promise<ChildRuntimeReport> {
  const { report } = await createMirroredModelRuntime(ctx, "gate");
  return report;
}

const GATE_PREFLIGHT_RESPONSE = "KARTA_GATE_RUNTIME_OK";

export interface GateProviderPreflightReport extends ChildRuntimeReport {
  cached: boolean;
}

export async function runGateResponseProbe(
  ctx: ExtensionContext,
  registry: ChildRegistry,
): Promise<ChildRuntimeReport> {
  const { session, report } = await createChild(
    ctx,
    `Reply with exactly ${GATE_PREFLIGHT_RESPONSE} and no other text.`,
    [],
    "gate",
  );
  registry.add(session, {
    cwd: ctx.cwd,
    role: "provider-preflight",
    label: `${report.provider}/${report.model}`,
  });
  const unbind = bindAbort(session, ctx.signal);
  try {
    await session.prompt("Run the isolated Karta gate provider preflight.");
    const response = session.getLastAssistantText()?.trim() ?? "";
    if (response !== GATE_PREFLIGHT_RESPONSE) {
      throw new Error(
        `Karta gate provider preflight returned an unexpected response for '${report.provider}/${report.model}'`,
      );
    }
    return report;
  } finally {
    unbind();
    registry.delete(session);
    session.dispose();
  }
}

export type GatePreflightProbe = (
  ctx: ExtensionContext,
  registry: ChildRegistry,
) => Promise<ChildRuntimeReport>;

export class GateProviderPreflight {
  readonly #successful = new Map<string, ChildRuntimeReport>();
  readonly #pending = new Map<string, Promise<ChildRuntimeReport>>();
  readonly #probe: GatePreflightProbe;
  #generation = 0;

  constructor(probe: GatePreflightProbe = runGateResponseProbe) {
    this.#probe = probe;
  }

  #key(ctx: ExtensionContext): string {
    if (!ctx.model) throw new Error("Pi has no active model");
    const auth = ctx.modelRegistry.getProviderAuthStatus(ctx.model.provider);
    const providerConfig = ctx.modelRegistry.getRegisteredProviderConfig?.(ctx.model.provider);
    const providerIdentity = createHash("sha256")
      .update(
        JSON.stringify(providerConfig ?? null, (_key, value) =>
          typeof value === "function" ? `[function:${value.name || "anonymous"}]` : value,
        ),
      )
      .digest("hex");
    return [
      ctx.model.provider,
      ctx.model.id,
      auth.source ?? "unconfigured",
      providerIdentity,
    ].join("\u0000");
  }

  async ensure(
    ctx: ExtensionContext,
    registry: ChildRegistry,
  ): Promise<GateProviderPreflightReport> {
    const key = this.#key(ctx);
    const successful = this.#successful.get(key);
    if (successful) return { ...successful, cached: true };
    const pending = this.#pending.get(key);
    if (pending) return { ...(await pending), cached: true };

    const generation = this.#generation;
    const probe = this.#probe(ctx, registry);
    this.#pending.set(key, probe);
    try {
      const report = await probe;
      if (generation === this.#generation) this.#successful.set(key, report);
      return { ...report, cached: false };
    } finally {
      this.#pending.delete(key);
    }
  }

  clear(): void {
    this.#generation += 1;
    this.#successful.clear();
    this.#pending.clear();
  }

  get size(): number {
    return this.#successful.size;
  }
}

export async function runResponseProbe(
  ctx: ExtensionContext,
  registry: ChildRegistry,
): Promise<ChildRuntimeReport & { response: string }> {
  const { session, report } = await createChild(
    ctx,
    "Reply with exactly KARTA_PHASE0_OK and no other text.",
  );
  registry.add(session, { cwd: ctx.cwd, label: "response probe" });
  const unbind = bindAbort(session, ctx.signal);
  try {
    await session.prompt("Run the response probe.");
    return { ...report, response: session.getLastAssistantText() ?? "" };
  } finally {
    unbind();
    registry.delete(session);
    session.dispose();
  }
}

export async function runMultiChildShutdownProbe(ctx: ExtensionContext): Promise<{
  children: number;
  toolCallsStarted: number;
  toolSignalsAborted: number;
  modelStreamsStarted: number;
  activeAfterShutdown: number;
}> {
  const registry = new ChildRegistry();
  if (ctx.signal?.aborted) throw new Error("multi-child shutdown probe was already aborted");
  const abort = () => void registry.abortAll();
  ctx.signal?.addEventListener("abort", abort, { once: true });
  const states = [
    { started: false, aborted: false, resolveStarted: undefined as (() => void) | undefined },
    { started: false, aborted: false, resolveStarted: undefined as (() => void) | undefined },
  ];
  const started = states.map(
    (state) =>
      new Promise<void>((resolve) => {
        state.resolveStarted = resolve;
      }),
  );
  const sessions: AgentSession[] = [];
  const prompts: Promise<void>[] = [];
  const unsubscribers: Array<() => void> = [];
  let modelStreamsStarted = 0;
  let resolveStreamStarted: (() => void) | undefined;
  const streamStarted = new Promise<void>((resolve) => {
    resolveStreamStarted = resolve;
  });
  let startTimer: NodeJS.Timeout | undefined;
  try {
    for (const [index, state] of states.entries()) {
      const toolName = `phase0_wait_${index + 1}`;
      const waitTool: ToolDefinition = {
        name: toolName,
        label: `Phase 0 wait ${index + 1}`,
        description: `Required multi-child shutdown probe. Call ${toolName} exactly once.`,
        parameters: Type.Object({}),
        async execute(_id, _params, signal) {
          state.started = true;
          state.resolveStarted?.();
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(resolve, 60_000);
            signal?.addEventListener(
              "abort",
              () => {
                clearTimeout(timer);
                state.aborted = true;
                reject(new Error(`${toolName} aborted`));
              },
              { once: true },
            );
          });
          return { content: [{ type: "text", text: "wait completed" }], details: undefined };
        },
      };
      const { session } = await createChild(
        ctx,
        `Call ${toolName} exactly once. Do not emit text before calling it.`,
        [waitTool],
        "gate",
      );
      sessions.push(session);
      registry.add(session, {
        cwd: ctx.cwd,
        role: "provider-preflight",
        label: `multi-child active ${index + 1}`,
      });
      prompts.push(session.prompt("Run the multi-child shutdown probe."));
    }
    const { session: streaming } = await createChild(
      ctx,
      "Write a long continuous response until interrupted. Do not call tools.",
      [],
      "gate",
    );
    sessions.push(streaming);
    registry.add(streaming, {
      cwd: ctx.cwd,
      role: "provider-preflight",
      label: "multi-child streaming",
    });
    unsubscribers.push(
      streaming.subscribe((event) => {
        if (event.type === "message_update" && modelStreamsStarted === 0) {
          modelStreamsStarted = 1;
          resolveStreamStarted?.();
        }
      }),
    );
    prompts.push(streaming.prompt("Begin the streaming shutdown probe now."));

    const { session: idle } = await createChild(ctx, "Remain idle.", [], "gate");
    sessions.push(idle);
    registry.add(idle, {
      cwd: ctx.cwd,
      role: "provider-preflight",
      label: "multi-child idle",
    });

    await Promise.race([
      Promise.all([...started, streamStarted]),
      Promise.all(prompts).then(() => {
        throw new Error("a child settled before both wait tools started");
      }),
      new Promise<never>((_, reject) => {
        startTimer = setTimeout(
          () => reject(new Error("multi-child active states did not start within 45 seconds")),
          45_000,
        );
      }),
    ]);
    await registry.abortAll();
    await Promise.allSettled(prompts);
    return {
      children: sessions.length,
      toolCallsStarted: states.filter((state) => state.started).length,
      toolSignalsAborted: states.filter((state) => state.aborted).length,
      modelStreamsStarted,
      activeAfterShutdown: registry.size,
    };
  } finally {
    ctx.signal?.removeEventListener("abort", abort);
    for (const unsubscribe of unsubscribers) unsubscribe();
    if (startTimer) clearTimeout(startTimer);
    if (registry.size > 0) await registry.abortAll();
    await Promise.allSettled(prompts);
  }
}

export async function runCancellationProbe(
  ctx: ExtensionContext,
  registry: ChildRegistry,
): Promise<ChildRuntimeReport & { toolStarted: boolean; toolSignalAborted: boolean }> {
  let toolStarted = false;
  let toolSignalAborted = false;
  let resolveStarted: (() => void) | undefined;
  const started = new Promise<void>((resolve) => {
    resolveStarted = resolve;
  });
  const waitTool: ToolDefinition = {
    name: "phase0_wait",
    label: "Phase 0 wait",
    description: "Required Phase 0 cancellation probe. Call exactly once.",
    parameters: Type.Object({}),
    async execute(_id, _params, signal) {
      toolStarted = true;
      resolveStarted?.();
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, 60_000);
        signal?.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            toolSignalAborted = true;
            reject(new Error("phase0 wait aborted"));
          },
          { once: true },
        );
      });
      return { content: [{ type: "text", text: "wait completed" }], details: undefined };
    },
  };
  const { session, report } = await createChild(
    ctx,
    "Call phase0_wait exactly once. Do not emit text before calling it.",
    [waitTool],
  );
  registry.add(session, { cwd: ctx.cwd, label: "cancellation probe" });
  const prompt = session.prompt("Run the cancellation probe.");
  let startTimer: NodeJS.Timeout | undefined;
  try {
    await Promise.race([
      started,
      prompt.then(() => {
        throw new Error("child settled without calling phase0_wait");
      }),
      new Promise<never>((_, reject) => {
        startTimer = setTimeout(
          () => reject(new Error("model did not call phase0_wait within 30 seconds")),
          30_000,
        );
      }),
    ]);
    await session.abort();
    await prompt.catch(() => undefined);
    return { ...report, toolStarted, toolSignalAborted };
  } finally {
    if (startTimer) clearTimeout(startTimer);
    await session.abort().catch(() => undefined);
    await prompt.catch(() => undefined);
    registry.delete(session);
    session.dispose();
  }
}
